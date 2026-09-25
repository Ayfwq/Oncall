from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.bootstrap.config import get_settings
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


def _escalation_text(project_name: str, incident: Incident, stage: str) -> str:
    final = stage == "final_5h"
    title = "最后升级提醒" if final else "告警仍未恢复"
    action = "这是本轮告警的最后一次自动推送，请立即处理。" if final else "系统将在后续升级时间点再次提醒。"
    return "\n".join(
        [
            f"🚨 **{title}**",
            "",
            f"**级别**：{SEVERITY_LABEL.get(incident.severity, incident.severity)}",
            f"**项目**：{project_name}",
            f"**问题**：{incident.summary or incident.anomaly_type}",
            f"**事件 ID**：`{incident.id}`",
            f"**本轮发生次数**：{incident.occurrence_count}",
            "",
            "告警仍处于未恢复状态。",
            action,
        ]
    )


class IncidentService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def on_alertmanager_webhook(self, payload: dict) -> list[Incident]:
        """Consume Alertmanager events. No local metric/rule evaluation happens here."""
        grouped: dict[UUID, list[dict]] = {}
        now = datetime.now().astimezone()
        settings = get_settings()
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
                    Incident.resolved_at.is_(None),
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
            is_reopened = False
            if incident is None and settings.incident_reopen_cooldown_seconds > 0:
                reopen_cutoff = now - timedelta(
                    seconds=settings.incident_reopen_cooldown_seconds
                )
                incident = await self.session.scalar(
                    select(Incident)
                    .where(
                        Incident.project_id == project_id,
                        Incident.fingerprint == incident_fp,
                        Incident.resolved_at.is_not(None),
                        Incident.resolved_at >= reopen_cutoff,
                    )
                    .order_by(Incident.resolved_at.desc())
                    .limit(1)
                )
                is_reopened = incident is not None
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
                    occurrence_count=1,
                    escalation_generation=1,
                )
                self.session.add(incident)
                await self.session.flush()
            elif is_reopened:
                incident.status = "open"
                incident.resolved_at = None
                incident.last_seen = now
                incident.occurrence_count += 1
                incident.escalation_generation += 1
            else:
                incident.last_seen = now
                if SEVERITY_RANK[severity] > SEVERITY_RANK.get(incident.severity, 0):
                    incident.severity = severity
            if is_new or is_reopened:
                self.session.add(
                    IncidentEvidence(
                        incident_id=incident.id,
                        type="alertmanager_reopened" if is_reopened else "alertmanager",
                        source=f"alertmanager:{_group_key(payload, labels)}",
                        summary=str(
                            annotations.get("summary")
                            or labels.get("alertname")
                            or "Prometheus 告警"
                        ),
                        data={
                            "status": "firing",
                            "occurrence_count": incident.occurrence_count,
                            "escalation_generation": incident.escalation_generation,
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
                generation = incident.escalation_generation
                if is_reopened:
                    self.session.add(
                        Notification(
                            incident_id=incident.id,
                            channel="feishu",
                            target="default",
                            payload={
                                "kind": "reopened",
                                "incident_id": str(incident.id),
                                "conversation_id": str(conversation.id),
                                "severity": severity,
                                "text": (
                                    f"🔁 **告警再次触发**\n\n{incident.summary}\n"
                                    f"这是同一事件第 {incident.occurrence_count} 次发生，"
                                    "继续沿用原告警和对话。"
                                ),
                            },
                            dedupe_key=f"incident:{incident.id}:reopened:{generation}",
                        )
                    )
                else:
                    self.session.add(
                        Notification(
                            incident_id=incident.id,
                            channel="feishu",
                            target="default",
                            payload={
                                "kind": "triggered",
                                "incident_id": str(incident.id),
                                "conversation_id": str(conversation.id),
                                "severity": severity,
                                "text": _notification_text(
                                    project.name, representative["raw"], incident.id
                                )
                                + (f"\n**合并实例数**：{len(active)}" if len(active) > 1 else ""),
                            },
                            dedupe_key=f"incident:{incident.id}:triggered:{generation}",
                        )
                    )
                await self._schedule_escalations(
                    incident, conversation, project.name, generation
                )
                await JobQueue(self.session).enqueue(
                    "incident_investigate",
                    {"incident_id": str(incident.id), "conversation_id": str(conversation.id)},
                    idempotency_key=f"incident_investigate:{incident.id}:cycle:{generation}",
                    priority=20,
                    commit=False,
                )
            incidents.append(incident)
        await self.session.commit()
        return incidents

    async def _schedule_escalations(
        self, incident: Incident, conversation: Conversation, project_name: str, generation: int
    ) -> None:
        settings = get_settings()
        plan = (
            (settings.incident_reminder_first_seconds, "reminder_2h"),
            (settings.incident_reminder_final_seconds, "final_5h"),
        )
        for delay_seconds, stage in plan:
            self.session.add(
                Notification(
                    incident_id=incident.id,
                    channel="feishu",
                    target="default",
                    available_at=datetime.now().astimezone()
                    + timedelta(seconds=delay_seconds),
                    payload={
                        "kind": "escalated",
                        "stage": stage,
                        "incident_id": str(incident.id),
                        "conversation_id": str(conversation.id),
                        "severity": incident.severity,
                        "text": _escalation_text(project_name, incident, stage),
                    },
                    dedupe_key=f"incident:{incident.id}:escalated:{generation}:{stage}",
                )
            )

    async def _cancel_pending_escalations(self, incident_id: UUID) -> None:
        await self.session.execute(
            update(Notification)
            .where(
                Notification.incident_id == incident_id,
                Notification.channel == "feishu",
                Notification.status.in_(["pending", "sending"]),
                Notification.payload["kind"].astext == "escalated",
            )
            .values(status="suppressed", last_error="incident resolved before escalation")
        )

    async def _resolve_without_commit(self, incident: Incident, reason: str) -> None:
        if incident.status == "resolved" and incident.resolved_at is not None:
            return
        if incident.resolved_at is not None:
            incident.status = "resolved"
            await self._cancel_pending_escalations(incident.id)
            return
        incident.status = "resolved"
        incident.resolved_at = datetime.now().astimezone()
        incident.last_seen = incident.resolved_at
        await self._cancel_pending_escalations(incident.id)
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

    async def _delete_conversation_threads(self, incident_ids: list[UUID], checkpointer) -> list[UUID]:
        conversation_ids = list(
            (
                await self.session.scalars(
                    select(Conversation.id).where(Conversation.incident_id.in_(incident_ids))
                )
            ).all()
        )
        if checkpointer is not None:
            for conversation_id in conversation_ids:
                await checkpointer.adelete_thread(str(conversation_id))
        return conversation_ids

    async def delete(self, incident_id: UUID, checkpointer=None) -> bool:
        incident = await self.session.get(Incident, incident_id)
        if not incident:
            return False
        conversation_ids = await self._delete_conversation_threads([incident_id], checkpointer)
        await self.session.execute(
            delete(BackgroundJob).where(
                or_(
                    BackgroundJob.payload["incident_id"].astext == str(incident_id),
                    BackgroundJob.payload["conversation_id"].astext.in_(
                        [str(item) for item in conversation_ids]
                    ),
                )
            )
        )
        await self.session.execute(
            delete(Conversation).where(Conversation.incident_id == incident_id)
        )
        await self.session.delete(incident)
        await self.session.commit()
        return True

    async def delete_many(self, incident_ids: list[UUID], checkpointer=None) -> int:
        """Delete a validated batch of incidents in one transaction."""
        ids = list(dict.fromkeys(incident_ids))
        if not ids:
            return 0
        conversation_ids = await self._delete_conversation_threads(ids, checkpointer)
        id_strings = [str(x) for x in ids]
        await self.session.execute(
            delete(BackgroundJob).where(
                or_(
                    BackgroundJob.payload["incident_id"].astext.in_(id_strings),
                    BackgroundJob.payload["conversation_id"].astext.in_(
                        [str(item) for item in conversation_ids]
                    ),
                )
            )
        )
        await self.session.execute(delete(Conversation).where(Conversation.incident_id.in_(ids)))
        result = await self.session.execute(delete(Incident).where(Incident.id.in_(ids)))
        await self.session.commit()
        return int(result.rowcount or 0)

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
