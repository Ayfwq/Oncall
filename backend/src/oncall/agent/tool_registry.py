from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.agent.tool_contracts import ALLOWED_TOOLS, validate_tool_args
from oncall.application.project_service import ProjectService
from oncall.domain.schemas import ToolResult
from oncall.infrastructure.db.models import (
    AgentRun,
    Incident,
    IncidentEvidence,
    IncidentSignal,
    MetricSample,
    MonitoringRule,
    MonitoringRun,
    RetrievalTrace,
    ToolRun,
)
from oncall.integrations.observability import RemoteObservabilityIntegration
from oncall.integrations.service import ServiceIntegration
from oncall.monitoring.signals import SUPPORTED_SIGNALS
from oncall.rag.retrieval import KnowledgeRetriever
from oncall.security.redact import redact_text


@dataclass(frozen=True)
class ToolExecutionContext:
    project_id: UUID | None
    incident_id: UUID | None
    agent_run_id: UUID


class ToolRegistry:
    def __init__(self, session: AsyncSession):
        self.session = session
        self._retriever: KnowledgeRetriever | None = None

    async def execute(
        self, name: str, args: dict, ctx: ToolExecutionContext, timeout: float = 15
    ) -> ToolResult:
        if name not in ALLOWED_TOOLS:
            return ToolResult(ok=False, summary="工具未授权", error_code="TOOL_NOT_ALLOWED")
        if name != "search_knowledge" and not ctx.project_id:
            return ToolResult(
                ok=False, summary="该工具需要绑定 Project", error_code="PROJECT_REQUIRED"
            )
        start = time.perf_counter()
        params_hash = hashlib.sha256(
            json.dumps(args, sort_keys=True, default=str).encode()
        ).hexdigest()
        args_ok, args_error = validate_tool_args(name, args)
        if not args_ok:
            result = ToolResult(
                ok=False, summary=f"工具参数无效: {args_error}", error_code="INVALID_TOOL_ARGS"
            )
            status = "error"
        else:
            try:
                result = await asyncio.wait_for(self._dispatch(name, args, ctx), timeout=timeout)
                status = "ok" if result.ok else "error"
            except TimeoutError:
                result = ToolResult(ok=False, summary="工具执行超时", error_code="TIMEOUT")
                status = "timeout"
            except (
                Exception
            ) as exc:  # every failure is returned and audited, never leaked to the graph
                result = ToolResult(
                    ok=False,
                    summary="工具执行失败",
                    error_code="TOOL_ERROR",
                    data={"error": redact_text(str(exc))},
                )
                status = "error"
        result.summary = redact_text(result.summary)
        latency = (time.perf_counter() - start) * 1000
        result_size = (
            len(result.data)
            if isinstance(result.data, list | dict)
            else (0 if result.data is None else 1)
        )
        # Audit rows are best effort. A conversation/AgentRun can be deleted
        # while a durable worker is still collecting evidence; that should not
        # turn an otherwise valid read-only tool result into a failed Agent run.
        try:
            run_exists = await self.session.scalar(
                select(AgentRun.id).where(AgentRun.id == ctx.agent_run_id)
            )
            if run_exists:
                self.session.add(
                    ToolRun(
                        agent_run_id=ctx.agent_run_id,
                        tool_name=name,
                        params_hash=params_hash,
                        status=status,
                        summary=result.summary,
                        latency_ms=latency,
                        result_size=result_size,
                        truncated=result.truncated,
                        error_code=result.error_code,
                    )
                )
                if name == "search_knowledge":
                    refs = []
                    if isinstance(result.data, list):
                        refs = [
                            {
                                "chunk_id": str(x.get("id", "")),
                                "document_id": str(x.get("document_id", "")),
                                "version_id": str(x.get("version_id", "")),
                                "title": x.get("title"),
                                "page_range": x.get("page_range"),
                                "score": x.get("rerank_score", x.get("rrf_score")),
                            }
                            for x in result.data[:20]
                        ]
                    self.session.add(
                        RetrievalTrace(
                            agent_run_id=ctx.agent_run_id,
                            project_id=ctx.project_id,
                            query=str(args.get("query", "")),
                            hit_count=len(refs),
                            refs=refs,
                            latency_ms=latency,
                            status="ok" if result.ok else "error",
                            error_code=result.error_code,
                        )
                    )
                await self.session.commit()
        except (IntegrityError, PendingRollbackError):
            # The parent AgentRun may have been removed between the existence
            # check and commit. Roll back only the audit transaction and keep
            # returning the tool evidence to the graph.
            await self.session.rollback()
        return result

    async def _incident_context(self, ctx: ToolExecutionContext) -> ToolResult:
        if not ctx.incident_id:
            return ToolResult(
                ok=False, summary="当前会话没有绑定告警事件", error_code="INCIDENT_REQUIRED"
            )
        incident = await self.session.get(Incident, ctx.incident_id)
        if not incident or incident.project_id != ctx.project_id:
            return ToolResult(
                ok=False, summary="告警事件不存在或不属于当前项目", error_code="INCIDENT_NOT_FOUND"
            )
        signals = list(
            (
                await self.session.scalars(
                    select(IncidentSignal)
                    .where(IncidentSignal.incident_id == incident.id)
                    .order_by(IncidentSignal.first_seen.asc())
                )
            ).all()
        )
        rule_ids = {x.rule_id for x in signals}
        rules = (
            {
                x.id: x
                for x in (
                    await self.session.scalars(
                        select(MonitoringRule).where(MonitoringRule.id.in_(rule_ids))
                    )
                ).all()
            }
            if rule_ids
            else {}
        )
        evidence = list(
            (
                await self.session.scalars(
                    select(IncidentEvidence)
                    .where(IncidentEvidence.incident_id == incident.id)
                    .order_by(IncidentEvidence.observed_at.desc())
                    .limit(20)
                )
            ).all()
        )
        data = {
            "incident": {
                "id": str(incident.id),
                "status": incident.status,
                "severity": incident.severity,
                "anomaly_type": incident.anomaly_type,
                "resource_key": incident.resource_key,
                "summary": incident.summary,
                "first_seen": incident.first_seen.isoformat(),
                "last_seen": incident.last_seen.isoformat(),
            },
            "signals": [
                {
                    "metric": item.anomaly_type,
                    "resource_key": item.resource_key,
                    "state": item.state,
                    "severity": item.severity,
                    "last_value": item.last_value,
                    "first_seen": item.first_seen.isoformat(),
                    "last_seen": item.last_seen.isoformat(),
                    "rule": (
                        {
                            "operator": rules[item.rule_id].operator,
                            "trigger_threshold": rules[item.rule_id].trigger_threshold,
                            "trigger_for": rules[item.rule_id].trigger_for,
                            "recovery_threshold": rules[item.rule_id].recovery_threshold,
                            "recovery_for": rules[item.rule_id].recovery_for,
                            "detection_mode": rules[item.rule_id].detection_mode,
                            "conditions": rules[item.rule_id].conditions,
                        }
                        if item.rule_id in rules
                        else None
                    ),
                }
                for item in signals
            ],
            "evidence": [
                {
                    "type": x.type,
                    "source": x.source,
                    "observed_at": x.observed_at.isoformat(),
                    "summary": x.summary,
                }
                for x in evidence
            ],
        }
        return ToolResult(
            ok=True,
            summary=f"事件包含 {len(signals)} 个触发信号和 {len(evidence)} 条已有证据",
            data=data,
        )

    @staticmethod
    def _metric_matches_groups(metric: str, groups: set[str]) -> bool:
        if not groups:
            return True
        if "gpu" in groups and metric.startswith("host.gpu."):
            return True
        prefixes = {
            "service": "service.",
            "application": "app.",
            "process": "process.",
            "logs": "log.",
            "database": "db.",
        }
        if "host" in groups and metric.startswith("host.") and not metric.startswith("host.gpu."):
            return True
        return any(
            group in groups and metric.startswith(prefix) for group, prefix in prefixes.items()
        )

    async def _current_metrics(self, args: dict, ctx: ToolExecutionContext) -> ToolResult:
        requested = set(args.get("metrics") or [])
        invalid = sorted(requested - SUPPORTED_SIGNALS)
        if invalid:
            return ToolResult(
                ok=False, summary=f"未知指标: {', '.join(invalid)}", error_code="INVALID_METRIC"
            )
        snapshot = None
        snapshot_source = "fresh_collection"
        snapshot_age_seconds = 0.0
        if not args.get("fresh", False):
            run = await self.session.scalar(
                select(MonitoringRun)
                .where(
                    MonitoringRun.project_id == ctx.project_id, MonitoringRun.status == "completed"
                )
                .order_by(MonitoringRun.finished_at.desc())
                .limit(1)
            )
            snapshot = dict(run.snapshot or {}) if run else None
            if snapshot is not None:
                snapshot_source = "latest_completed_run"
                if run.finished_at:
                    snapshot_age_seconds = max(
                        0.0, (datetime.now().astimezone() - run.finished_at).total_seconds()
                    )
        if snapshot is None:
            from oncall.monitoring.engine import MonitoringEngine

            snapshot = (
                await MonitoringEngine(self.session).collect(ctx.project_id, persist_state=False)
            ).model_dump(mode="json")
        groups = set(args.get("groups") or [])

        def wanted(key: str) -> bool:
            return (not requested or key in requested) and self._metric_matches_groups(key, groups)

        snapshot["signals"] = {
            key: value for key, value in (snapshot.get("signals") or {}).items() if wanted(key)
        }
        snapshot["resource_signals"] = {
            resource: {key: value for key, value in values.items() if wanted(key)}
            for resource, values in (snapshot.get("resource_signals") or {}).items()
        }
        snapshot["resource_signals"] = {
            key: value for key, value in snapshot["resource_signals"].items() if value
        }
        snapshot.pop(
            "resources", None
        )  # detailed logs/runtime/database data belongs to specialized tools
        snapshot["snapshot_source"] = snapshot_source
        snapshot["snapshot_age_seconds"] = round(snapshot_age_seconds, 3)
        return ToolResult(
            ok=True,
            summary=f"返回 {len(snapshot['signals'])} 个项目指标和 {sum(len(x) for x in snapshot['resource_signals'].values())} 个资源指标",
            data=snapshot,
        )

    async def _metric_history(self, args: dict, ctx: ToolExecutionContext) -> ToolResult:
        metrics = list(args["metrics"])
        invalid = sorted(set(metrics) - SUPPORTED_SIGNALS)
        if invalid:
            return ToolResult(
                ok=False, summary=f"未知指标: {', '.join(invalid)}", error_code="INVALID_METRIC"
            )
        minutes = int(args.get("minutes", 30))
        since = datetime.now().astimezone() - timedelta(minutes=minutes)
        stmt = select(MetricSample).where(
            MetricSample.project_id == ctx.project_id,
            MetricSample.metric_key.in_(metrics),
            MetricSample.ts >= since,
        )
        if args.get("resource_key"):
            stmt = stmt.where(MetricSample.resource_key == str(args["resource_key"]))
        rows = list(
            (await self.session.scalars(stmt.order_by(MetricSample.ts.asc()).limit(5001))).all()
        )
        truncated = len(rows) > 5000
        rows = rows[:5000]
        grouped: dict[tuple[str, str], list[MetricSample]] = defaultdict(list)
        for row in rows:
            grouped[(row.metric_key, row.resource_key)].append(row)
        series = []
        include_samples = bool(args.get("include_samples", False))
        for (metric, resource), items in grouped.items():
            values = [float(x.value) for x in items]
            delta = values[-1] - values[0]
            tolerance = max(abs(values[0]) * 0.02, 1e-9)
            entry = {
                "metric": metric,
                "resource_key": resource,
                "count": len(values),
                "current": values[-1],
                "min": min(values),
                "max": max(values),
                "avg": sum(values) / len(values),
                "delta": delta,
                "trend": "up"
                if delta > tolerance
                else ("down" if delta < -tolerance else "stable"),
                "first_ts": items[0].ts.isoformat(),
                "last_ts": items[-1].ts.isoformat(),
            }
            if include_samples:
                entry["samples"] = [
                    {"ts": x.ts.isoformat(), "value": x.value} for x in items[-500:]
                ]
                if len(items) > 500:
                    truncated = True
            series.append(entry)
        return ToolResult(
            ok=True,
            summary=f"过去 {minutes} 分钟返回 {len(series)} 条指标序列、{len(rows)} 个采样",
            data=series,
            truncated=truncated,
        )

    async def _dispatch(self, name: str, args: dict, ctx: ToolExecutionContext) -> ToolResult:
        if name == "search_knowledge":
            if self._retriever is None:
                self._retriever = KnowledgeRetriever()
            return await self._retriever.search(
                str(args.get("query", "")), ctx.project_id, top_k=int(args.get("top_k", 5))
            )
        if name == "query_incident_context":
            return await self._incident_context(ctx)
        if name == "query_current_metrics":
            return await self._current_metrics(args, ctx)
        if name == "query_metric_history":
            return await self._metric_history(args, ctx)
        cfg = await ProjectService(self.session).runtime_config(ctx.project_id)
        if name == "query_service_health":
            return await ServiceIntegration(cfg.service_endpoints).query(
                args.get("endpoint"), bool(args.get("include_body", False))
            )
        observability = RemoteObservabilityIntegration(
            cfg.server, cfg.log_sources, cfg.database_profiles
        )
        if name == "search_logs":
            return await observability.search_logs(
                str(args.get("query", "")),
                args.get("level"),
                int(args.get("since_minutes", 30)),
                int(args.get("limit", 200)),
                args.get("services"),
                bool(args.get("group_by_signature", True)),
            )
        if name == "query_database_health":
            return await observability.query_database(
                args.get("checks"), int(args.get("slow_query_limit", 10))
            )
        if name == "query_runtime_resources":
            return await observability.query_runtime_resources(
                args.get("services"), args.get("checks"), int(args.get("top_n", 5))
            )
        raise KeyError(name)
