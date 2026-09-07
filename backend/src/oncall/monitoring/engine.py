from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.application.dtos import SnapshotDTO
from oncall.application.incident_service import IncidentService, incident_fingerprint
from oncall.application.project_service import ProjectService
from oncall.domain.enums import RuleState
from oncall.infrastructure.db.models import (
    AlertEvent,
    Conversation,
    Incident,
    MetricSample,
    MonitoringBaseline,
    MonitoringRule,
    MonitoringRuleState,
    MonitoringRun,
)
from oncall.integrations.base import CollectResult
from oncall.integrations.prometheus import PrometheusIntegration
from oncall.integrations.server_exporters import ServerExportersIntegration
from oncall.integrations.service import ServiceIntegration
from oncall.jobs.queue import JobQueue
from oncall.monitoring.detector import Detector, RuleConfig, RuleRuntimeState, compare
from oncall.monitoring.signals import SUPPORTED_SIGNALS


def baseline_z_score(value: float, mean: float, std: float, operator: str) -> float:
    """Measure deviation only in the rule's unhealthy direction."""
    delta = value - mean
    if operator in ('>', '>='):
        directional = max(0.0, delta)
    elif operator in ('<', '<='):
        directional = max(0.0, -delta)
    else:
        directional = abs(delta)
    if std <= max(1e-9, abs(mean) * 1e-6):
        return float('inf') if directional > max(1e-6, abs(mean) * 1e-6) else 0.0
    return directional / std


class MonitoringEngine:
    """Collect and evaluate the one supported remote-Python project shape."""

    def __init__(self, session: AsyncSession):
        from oncall.bootstrap.config import get_settings

        self.session = session
        self.detector = Detector()
        self.settings = get_settings()

    async def _previous_run(self, project_id: UUID) -> MonitoringRun | None:
        return await self.session.scalar(
            select(MonitoringRun)
            .where(MonitoringRun.project_id == project_id, MonitoringRun.status == 'completed')
            .order_by(MonitoringRun.finished_at.desc())
            .limit(1)
        )

    async def collect(self, project_id: UUID, *, persist_state: bool = True, config=None) -> SnapshotDTO:
        cfg = config or await ProjectService(self.session).runtime_config(project_id)
        if cfg.server is None:
            raise ValueError('remote Python monitoring requires a configured server')

        integrations = [
            ServerExportersIntegration(cfg.server),
            ServiceIntegration(cfg.service_endpoints),
            PrometheusIntegration(self.session, project_id, cfg.metrics_sources, persist_state=persist_state),
        ]

        async def collect_one(integration):
            try:
                return await asyncio.wait_for(integration.collect(), timeout=8)
            except Exception as exc:  # noqa: BLE001
                return CollectResult(name=integration.name, ok=False, error=str(exc))

        results = list(await asyncio.gather(*(collect_one(x) for x in integrations)))
        signals: dict[str, float] = {}
        resource_signals: dict[str, dict[str, float]] = {}
        resources: dict = {}
        collector_status: dict = {}
        for result in results:
            signals.update(result.signals)
            resources[result.name] = result.resources
            collector_status[result.name] = {'ok': result.ok, 'error': result.error}
            for resource_key, values in result.resource_signals.items():
                resource_signals.setdefault(str(resource_key), {}).update(values)

        previous = await self._previous_run(project_id)
        previous_snapshot = (previous.snapshot or {}) if previous else {}

        # Node Exporter exposes cumulative counters. Convert them to rates using
        # the previous completed snapshot, including after worker restarts.
        current_counters = ((resources.get('server') or {}).get('node') or {}).get('counters') or {}
        previous_counters = ((previous_snapshot.get('resources', {}).get('server') or {}).get('node') or {}).get('counters') or {}
        dt = float(current_counters.get('ts', 0) or 0) - float(previous_counters.get('ts', 0) or 0)
        if dt > 0:
            cpu_total_delta = float(current_counters.get('cpu_total_seconds', 0) or 0) - float(previous_counters.get('cpu_total_seconds', 0) or 0)
            cpu_idle_delta = float(current_counters.get('cpu_idle_seconds', 0) or 0) - float(previous_counters.get('cpu_idle_seconds', 0) or 0)
            if cpu_total_delta > 0:
                signals['host.cpu.percent'] = max(0.0, min(100.0, (1.0 - cpu_idle_delta / cpu_total_delta) * 100.0))
            for metric, counter in (
                ('host.disk.read_bytes_per_sec', 'disk_read_bytes'),
                ('host.disk.write_bytes_per_sec', 'disk_write_bytes'),
                ('host.net.rx_bytes_per_sec', 'net_rx_bytes'),
                ('host.net.tx_bytes_per_sec', 'net_tx_bytes'),
            ):
                current = current_counters.get(counter)
                prior = previous_counters.get(counter)
                if current is not None and prior is not None:
                    signals[metric] = max(0.0, (float(current) - float(prior)) / dt)

        # Health failures are consecutive across worker restarts and include
        # both transport errors and unexpected HTTP status codes.
        endpoints = (resources.get('service') or {}).get('endpoints') or []
        if 'service.consecutive_failures' in signals:
            healthy = all(bool(row.get('ok')) for row in endpoints) if endpoints else True
            prior_failures = float(previous_snapshot.get('signals', {}).get('service.consecutive_failures', 0) or 0)
            signals['service.consecutive_failures'] = 0.0 if healthy else prior_failures + 1.0

        signals = {key: value for key, value in signals.items() if key in SUPPORTED_SIGNALS}
        resource_signals = {
            resource: {key: value for key, value in values.items() if key in SUPPORTED_SIGNALS}
            for resource, values in resource_signals.items()
        }
        return SnapshotDTO(
            project_id=project_id,
            observed_at=datetime.now().astimezone(),
            signals=signals,
            resource_signals=resource_signals,
            resources=resources,
            collector_status=collector_status,
        )

    async def run_project(self, project_id: UUID) -> SnapshotDTO:
        run = MonitoringRun(project_id=project_id)
        self.session.add(run)
        await self.session.flush()
        snapshot = await self.collect(project_id)
        run.snapshot = snapshot.model_dump(mode='json')
        run.collector_status = snapshot.collector_status
        run.status = 'completed'
        run.finished_at = datetime.now().astimezone()
        for metric_key, value in snapshot.signals.items():
            if isinstance(value, (int, float, bool)):
                self.session.add(MetricSample(project_id=project_id, metric_key=metric_key, resource_key='default', ts=snapshot.observed_at, value=float(value)))
        for resource_key, values in snapshot.resource_signals.items():
            for metric_key, value in values.items():
                if isinstance(value, (int, float, bool)):
                    self.session.add(MetricSample(project_id=project_id, metric_key=metric_key, resource_key=str(resource_key), ts=snapshot.observed_at, value=float(value)))
        await self.session.commit()
        await self.evaluate_rules(project_id, snapshot)
        return snapshot

    def _condition_holds(self, condition: dict, snapshot: SnapshotDTO) -> bool:
        metric_key = str(condition.get('metric_key', ''))
        resource_key = str(condition.get('resource_key', 'default') or 'default')
        source = snapshot.signals if resource_key == 'default' else snapshot.resource_signals.get(resource_key, {})
        value = source.get(metric_key)
        if not isinstance(value, (int, float, bool)):
            return False
        return compare(float(value), str(condition.get('operator', '>')), float(condition.get('threshold', 0)))

    def _composite_active(self, conditions: dict, snapshot: SnapshotDTO) -> bool:
        group = conditions.get('all') or []
        return bool(group) and all(self._condition_holds(condition, snapshot) for condition in group)

    def _composite_severity(self, conditions: dict, snapshot: SnapshotDTO, base: str) -> str:
        escalate_at = conditions.get('escalate_at')
        if escalate_at and self._condition_holds(escalate_at, snapshot):
            return str(escalate_at.get('escalate_severity', 'critical') or 'critical')
        return base

    async def _baseline_check(self, project_id: UUID, rule: MonitoringRule, value: float, *, currently_firing: bool = False) -> tuple[bool, dict]:
        row = await self.session.scalar(
            select(MonitoringBaseline)
            .where(
                MonitoringBaseline.project_id == project_id,
                MonitoringBaseline.metric_key == rule.metric_key,
                MonitoringBaseline.resource_key == rule.resource_key,
            )
            .limit(1)
        )
        samples = [float(x) for x in (row.samples if row else []) if isinstance(x, (int, float)) and math.isfinite(float(x))]
        details = {'mode': rule.detection_mode, 'sample_count': len(samples), 'min_samples': rule.baseline_min_samples}
        if len(samples) < rule.baseline_min_samples:
            return False, details
        mean = sum(samples) / len(samples)
        variance = sum((x - mean) ** 2 for x in samples) / max(1, len(samples) - 1)
        std = math.sqrt(variance)
        z_score = baseline_z_score(value, mean, std, rule.operator)
        limit = rule.baseline_recovery_z_score if currently_firing else rule.baseline_z_score
        details.update({'mean': mean, 'stddev': std, 'z_score': z_score, 'active_z_score': limit, 'trigger_z_score': rule.baseline_z_score, 'recovery_z_score': rule.baseline_recovery_z_score})
        return z_score >= limit, details

    async def _record_baseline(self, project_id: UUID, rule: MonitoringRule, value: float) -> None:
        row = await self.session.scalar(
            select(MonitoringBaseline)
            .where(
                MonitoringBaseline.project_id == project_id,
                MonitoringBaseline.metric_key == rule.metric_key,
                MonitoringBaseline.resource_key == rule.resource_key,
            )
            .limit(1)
        )
        samples = [float(x) for x in (row.samples if row else []) if isinstance(x, (int, float)) and math.isfinite(float(x))]
        samples = (samples + [value])[-rule.baseline_window:]
        if row is None:
            self.session.add(MonitoringBaseline(project_id=project_id, metric_key=rule.metric_key, resource_key=rule.resource_key, samples=samples))
        else:
            row.samples = samples

    async def evaluate_rules(self, project_id: UUID, snapshot: SnapshotDTO) -> None:
        rules = list((await self.session.scalars(select(MonitoringRule).where(MonitoringRule.project_id == project_id, MonitoringRule.enabled.is_(True)))).all())
        incident_service = IncidentService(self.session)
        for rule in rules:
            state_row = await self.session.get(MonitoringRuleState, rule.id)
            currently_firing = bool(state_row and state_row.state == 'firing')
            threshold_active = False
            baseline_active = False
            baseline_details = None
            raw = None
            if rule.conditions:
                effective_metric = rule.metric_key
                effective_resource = 'default'
                active = self._composite_active(rule.conditions, snapshot)
                effective_severity = self._composite_severity(rule.conditions, snapshot, rule.severity)
                value = 1.0 if active else 0.0
                observed = snapshot.signals.get(rule.metric_key)
                reported_value = float(observed) if isinstance(observed, (int, float, bool)) else value
                detector_config = RuleConfig('>', 0.5, rule.trigger_for, 0.5, rule.recovery_for)
            else:
                effective_metric = rule.metric_key
                effective_resource = rule.resource_key
                effective_severity = rule.severity
                source = snapshot.signals if rule.resource_key == 'default' else snapshot.resource_signals.get(rule.resource_key, {})
                raw = source.get(rule.metric_key)
                if not isinstance(raw, (int, float, bool)):
                    continue
                value = float(raw)
                reported_value = value
                threshold_active = compare(value, rule.operator, rule.trigger_threshold)
                if rule.detection_mode == 'threshold':
                    detector_config = RuleConfig(rule.operator, rule.trigger_threshold, rule.trigger_for, rule.recovery_threshold, rule.recovery_for)
                else:
                    baseline_active, baseline_details = await self._baseline_check(project_id, rule, value, currently_firing=currently_firing)
                    active = baseline_active or (threshold_active if rule.detection_mode == 'hybrid' else False)
                    value = 1.0 if active else 0.0
                    detector_config = RuleConfig('>', 0.5, rule.trigger_for, 0.5, rule.recovery_for)

            if state_row is None:
                state_row = MonitoringRuleState(rule_id=rule.id)
                self.session.add(state_row)
                await self.session.flush()
            runtime = RuleRuntimeState(
                state=RuleState(state_row.state),
                abnormal_hits=state_row.abnormal_hits,
                recovery_hits=state_row.recovery_hits,
                last_value=state_row.last_value,
            )
            transition = self.detector.evaluate(detector_config, runtime, value)
            state_row.state = runtime.state.value
            state_row.abnormal_hits = runtime.abnormal_hits
            state_row.recovery_hits = runtime.recovery_hits
            state_row.last_value = value
            if rule.conditions is None and rule.detection_mode != 'threshold' and not threshold_active and not baseline_active and transition.after.value != 'firing':
                await self._record_baseline(project_id, rule, float(raw))
            if transition.changed:
                self.session.add(AlertEvent(
                    rule_id=rule.id,
                    project_id=project_id,
                    resource_key=effective_resource,
                    state_from=transition.before.value,
                    state_to=transition.after.value,
                    payload={
                        'metric_key': effective_metric,
                        'value': reported_value,
                        'detector_value': value,
                        'raw_value': reported_value,
                        'threshold': (rule.conditions and rule.conditions.get('all')) or rule.trigger_threshold,
                        'detection_mode': rule.detection_mode,
                        'baseline': baseline_details,
                        'composite': bool(rule.conditions),
                        'observed_at': snapshot.observed_at.isoformat(),
                    },
                ))
            await self.session.commit()
            if transition.became_firing:
                await incident_service.on_firing(project_id, rule.id, effective_resource, effective_metric, effective_severity, reported_value)
            elif transition.became_recovered:
                fingerprint = incident_fingerprint(project_id, rule.id, effective_resource, effective_metric)
                incident = await self.session.scalar(select(Incident).where(Incident.project_id == project_id, Incident.fingerprint == fingerprint, Incident.status != 'resolved').order_by(Incident.first_seen.desc()).limit(1))
                if incident:
                    await incident_service.resolve(incident.id)
            elif runtime.state.value == 'firing':
                fingerprint = incident_fingerprint(project_id, rule.id, effective_resource, effective_metric)
                incident = await self.session.scalar(select(Incident).where(Incident.project_id == project_id, Incident.fingerprint == fingerprint, Incident.status.in_(['open', 'investigating', 'diagnosed'])).order_by(Incident.first_seen.desc()).limit(1))
                if not incident:
                    incident = await incident_service.on_firing(project_id, rule.id, effective_resource, effective_metric, effective_severity, reported_value)
                else:
                    await incident_service.touch_firing(incident, effective_severity, reported_value)
                now = datetime.now().astimezone()
                if incident and incident.last_investigated_at and now - incident.last_investigated_at >= timedelta(seconds=self.settings.incident_stale_reinvestigate_seconds):
                    conversation = await self.session.scalar(select(Conversation).where(Conversation.incident_id == incident.id).order_by(Conversation.created_at.asc()).limit(1))
                    if conversation:
                        bucket = int(now.timestamp()) // self.settings.incident_stale_reinvestigate_seconds
                        await JobQueue(self.session).enqueue('incident_investigate', {'incident_id': str(incident.id), 'conversation_id': str(conversation.id)}, idempotency_key=f'incident_investigate:{incident.id}:stale:{bucket}', priority=30)
