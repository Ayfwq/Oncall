from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.infrastructure.db.models import (
    AlertmanagerAlert,
    BackgroundJob,
    Conversation,
    Incident,
    IncidentEvidence,
    Notification,
    Project,
)
from oncall.jobs.queue import JobQueue

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
SEVERITY_LABEL = {"info": "提示", "warning": "警告", "critical": "严重"}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _value(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _fingerprint(project_id: UUID, fingerprint: str) -> str:
    return hashlib.sha256(f"{project_id}|{fingerprint}".encode()).hexdigest()


def _group_key(payload: dict, labels: dict) -> str:
    explicit = str(payload.get("groupKey") or "").strip()
    if explicit:
        return explicit
    stable = {
        key: labels.get(key)
        for key in ("project_id", "alertname", "category")
        if labels.get(key) is not None
    }
    return repr(sorted(stable.items()))


def _notification_text(project_name: str, alert: dict, incident_id: UUID) -> str:
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}
    severity = str(labels.get("severity") or "warning").lower()
    label = SEVERITY_LABEL.get(severity, severity)
    summary = annotations.get("summary") or labels.get("alertname") or "Prometheus 告警"
    description = annotations.get("description") or "Prometheus 检测到指标异常。"
    return "\n".join(
        [
            f"🚨 **{summary}**",
            "",
            f"**级别**：{label}",
            f"**项目**：{project_name}",
            f"**说明**：{description}",
            f"**告警名**：`{labels.get('alertname', 'unknown')}`",
            f"**当前值**：{alert.get('value', '暂无')}",
            f"**事件 ID**：`{incident_id}`",
            "",
            "Prometheus 已完成异常判断，接下来由大模型结合指标、日志和数据库信息进行诊断。",
        ]
    )


class IncidentService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def on_alertmanager_webhook(self, payload: dict) -> list[Incident]:
        """Consume Alertmanager events. No local metric/rule evaluation happens here."""
        grouped: dict[UUID, list[dict]] = {}
        now = datetime.now().astimezone()
        fallback_status = str(payload.get("status") or "firing").lower()
        for alert in payload.get("alerts") or []:
            labels = dict(alert.get("labels") or {})
            annotations = dict(alert.get("annotations") or {})
            try:
                project_id = UUID(str(labels.get("project_id")))
            except (TypeError, ValueError):
                continue
            project = await self.session.get(Project, project_id)
            if not project:
                continue
            status = str(alert.get("status") or fallback_status).lower()
            severity = str(labels.get("severity") or "warning").lower()
            if severity not in SEVERITY_RANK:
                severity = "warning"
            alert_fp = str(
                alert.get("fingerprint")
                or hashlib.sha256(repr(sorted(labels.items())).encode()).hexdigest()
            )
            stored = await self.session.scalar(
                select(AlertmanagerAlert).where(AlertmanagerAlert.fingerprint == alert_fp)
            )
            if stored is None:
                stored = AlertmanagerAlert(
                    fingerprint=alert_fp,
                    project_id=project_id,
                    status=status,
                    alertname=str(labels.get("alertname") or "PrometheusAlert"),
                    severity=severity,
                    labels=labels,
                    annotations=annotations,
                    value=_value(alert.get("value")),
                    starts_at=_parse_time(alert.get("startsAt")),
                    ends_at=_parse_time(alert.get("endsAt")),
                )
                self.session.add(stored)
            else:
                stored.status = status
                stored.severity = severity
                stored.labels = labels
                stored.annotations = annotations
                stored.value = _value(alert.get("value"))
                stored.ends_at = _parse_time(alert.get("endsAt"))
                stored.received_at = now
            grouped.setdefault(project_id, []).append(
                {
                    "raw": alert,
                    "labels": labels,
                    "annotations": annotations,
                    "status": status,
                    "severity": severity,
                    "fingerprint": alert_fp,
                    "project": project,
                }
            )

        incidents: list[Incident] = []
        for project_id, entries in grouped.items():
            project = entries[0]["project"]
            active = [item for item in entries if item["status"] != "resolved"]
            representative = active[0] if active else entries[0]
            labels = representative["labels"]
            annotations = representative["annotations"]
            incident_fp = _fingerprint(project_id, _group_key(payload, labels))
            incident = await self.session.scalar(
                select(Incident)
                .where(
                    Incident.project_id == project_id,
                    Incident.fingerprint == incident_fp,
                    Incident.status.in_(["open", "investigating", "diagnosed"]),
                )
                .order_by(Incident.last_seen.desc())
                .limit(1)
            )
            if not active:
                if incident:
                    await self._resolve_without_commit(incident, "alertmanager_resolved")
                    incidents.append(incident)
                continue
            severity = max(
                (item["severity"] for item in active), key=lambda value: SEVERITY_RANK[value]
            )
            is_new = incident is None
            if incident is None:
                summary = str(
                    annotations.get("summary") or labels.get("alertname") or "Prometheus 告警"
                )
                if len(active) > 1:
                    summary += f"（已合并 {len(active)} 个实例）"
                incident = Incident(
                    project_id=project_id,
                    fingerprint=incident_fp,
                    status="open",
                    severity=severity,
                    anomaly_type=str(labels.get("alertname") or "PrometheusAlert"),
                    resource_key=str(
                        labels.get("instance")
                        or labels.get("service")
                        or labels.get("category")
                        or "default"
                    )
                    if len(active) == 1
                    else f"group:{labels.get('category', 'default')}",
                    summary=summary,
                    first_seen=now,
                    last_seen=now,
                )
                self.session.add(incident)
                await self.session.flush()
            else:
                incident.last_seen = now
                if SEVERITY_RANK[severity] > SEVERITY_RANK.get(incident.severity, 0):
                    incident.severity = severity
            if is_new:
                self.session.add(
                    IncidentEvidence(
                        incident_id=incident.id,
                        type="alertmanager",
                        source=f"alertmanager:{_group_key(payload, labels)}",
                        summary=str(
                            annotations.get("summary")
                            or labels.get("alertname")
                            or "Prometheus 告警"
                        ),
                        data={
                            "status": "firing",
                            "group_key": _group_key(payload, labels),
                            "alerts": [
                                {
                                    "fingerprint": item["fingerprint"],
                                    "labels": item["labels"],
                                    "annotations": item["annotations"],
                                    "value": _value(item["raw"].get("value")),
                                }
                                for item in active
                            ],
                        },
                    )
                )
                conversation = await self.session.scalar(
                    select(Conversation).where(Conversation.incident_id == incident.id).limit(1)
                )
                if conversation is None:
                    conversation = Conversation(
                        user_id=project.user_id,
                        title=f"🚨 {incident.anomaly_type}",
                        project_id=project_id,
                        incident_id=incident.id,
                        type="incident",
                    )
                    self.session.add(conversation)
                    await self.session.flush()
                self.session.add(
                    Notification(
                        incident_id=incident.id,
                        channel="feishu",
                        target="default",
                        payload={
                            "kind": "triggered",
                            "incident_id": str(incident.id),
                            "severity": severity,
                            "text": _notification_text(
                                project.name, representative["raw"], incident.id
                            )
                            + (f"\n**合并实例数**：{len(active)}" if len(active) > 1 else ""),
                        },
                        dedupe_key=f"incident:{incident.id}:triggered",
                    )
                )
                await JobQueue(self.session).enqueue(
                    "incident_investigate",
                    {"incident_id": str(incident.id), "conversation_id": str(conversation.id)},
                    idempotency_key=f"incident_investigate:{incident.id}:initial",
                    priority=20,
                    commit=False,
                )
            incidents.append(incident)
        await self.session.commit()
        return incidents

    async def _resolve_without_commit(self, incident: Incident, reason: str) -> None:
        if incident.status == "resolved":
            return
        incident.status = "resolved"
        incident.resolved_at = datetime.now().astimezone()
        incident.last_seen = incident.resolved_at
        self.session.add(
            IncidentEvidence(
                incident_id=incident.id,
                type="alertmanager_recovered",
                source="alertmanager",
                summary="Prometheus 告警已恢复",
                data={"reason": reason},
            )
        )
        self.session.add(
            Notification(
                incident_id=incident.id,
                channel="feishu",
                target="default",
                payload={
                    "kind": "resolved",
                    "incident_id": str(incident.id),
                    "severity": "info",
                    "summary": reason,
                    "text": f"✅ **告警已恢复**\n\n{incident.anomaly_type}\n恢复原因：{reason}",
                },
                dedupe_key=f"incident:{incident.id}:resolved",
            )
        )

    async def resolve(self, incident_id: UUID, reason: str = "manual_resolve") -> Incident | None:
        incident = await self.session.get(Incident, incident_id)
        if not incident:
            return None
        await self._resolve_without_commit(incident, reason)
        await self.session.commit()
        return incident

    async def delete(self, incident_id: UUID) -> bool:
        incident = await self.session.get(Incident, incident_id)
        if not incident:
            return False
        await self.session.execute(
            delete(BackgroundJob).where(
                BackgroundJob.payload["incident_id"].astext == str(incident_id)
            )
        )
        await self.session.execute(
            delete(Conversation).where(Conversation.incident_id == incident_id)
        )
        await self.session.delete(incident)
        await self.session.commit()
        return True

    async def add_evidence(
        self,
        incident_id: UUID,
        type_: str,
        source: str,
        summary: str,
        data: dict | None = None,
        raw_ref: str | None = None,
    ) -> IncidentEvidence:
        evidence = IncidentEvidence(
            incident_id=incident_id,
            type=type_,
            source=source,
            summary=summary,
            data=data or {},
            raw_ref=raw_ref,
        )
        self.session.add(evidence)
        await self.session.commit()
        await self.session.refresh(evidence)
        return evidence
