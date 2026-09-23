from __future__ import annotations

import asyncio
import hashlib
import json
import time
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
    AlertmanagerAlert,
    Incident,
    IncidentEvidence,
    RetrievalTrace,
    ToolRun,
)
from oncall.integrations.observability import RemoteObservabilityIntegration
from oncall.integrations.prometheus_api import PrometheusClient, project_metric_promql
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
        alerts = list(
            (
                await self.session.scalars(
                    select(AlertmanagerAlert)
                    .where(AlertmanagerAlert.project_id == incident.project_id)
                    .order_by(AlertmanagerAlert.received_at.desc())
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
            "alerts": [
                {
                    "fingerprint": x.fingerprint,
                    "status": x.status,
                    "alertname": x.alertname,
                    "severity": x.severity,
                    "labels": x.labels,
                    "annotations": x.annotations,
                    "value": x.value,
                }
                for x in alerts
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
            summary=f"事件包含 Prometheus 告警和 {len(evidence)} 条已有证据",
            data=data,
        )

    @staticmethod
    def _metric_matches_groups(metric: str, groups: set[str]) -> bool:
        if not groups:
            return True
        if "gpu" in groups and metric.startswith("host.gpu."):
            return True
        prefixes = {
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
        requested = list(args.get("metrics") or sorted(SUPPORTED_SIGNALS))
        invalid = sorted(set(requested) - SUPPORTED_SIGNALS)
        if invalid:
            return ToolResult(
                ok=False, summary=f"未知指标: {', '.join(invalid)}", error_code="INVALID_METRIC"
            )
        groups = set(args.get("groups") or [])
        client = PrometheusClient()
        values: dict[str, list[dict]] = {}
        unsupported: list[str] = []
        for metric in requested:
            if self._metric_matches_groups(metric, groups):
                expression = project_metric_promql(metric, ctx.project_id)
                if expression is None:
                    unsupported.append(metric)
                    continue
                try:
                    values[metric] = await client.query(expression)
                except Exception as exc:
                    values[metric] = [{"error": str(exc)}]
        return ToolResult(
            ok=True,
            summary=f"Prometheus 返回 {len(values)} 个指标查询结果",
            data={"source": "prometheus", "metrics": values, "diagnostic_only": unsupported},
        )

    async def _metric_history(self, args: dict, ctx: ToolExecutionContext) -> ToolResult:
        metrics = list(args["metrics"])
        invalid = sorted(set(metrics) - SUPPORTED_SIGNALS)
        if invalid:
            return ToolResult(
                ok=False, summary=f"未知指标: {', '.join(invalid)}", error_code="INVALID_METRIC"
            )
        minutes = int(args.get("minutes", 30))
        end = datetime.now().astimezone()
        start = end - timedelta(minutes=minutes)
        client = PrometheusClient()
        series = []
        unsupported: list[str] = []
        for metric in metrics:
            expression = project_metric_promql(metric, ctx.project_id)
            if expression is None:
                unsupported.append(metric)
                continue
            try:
                result = await client.query_range(expression, start.isoformat(), end.isoformat())
                series.append({"metric": metric, "result": result})
            except Exception as exc:
                series.append({"metric": metric, "error": str(exc)})
        return ToolResult(
            ok=True,
            summary=f"Prometheus 返回过去 {minutes} 分钟的 {len(series)} 条序列",
            data={"series": series, "diagnostic_only": unsupported},
        )

    async def _dispatch(self, name: str, args: dict, ctx: ToolExecutionContext) -> ToolResult:
        if name == "search_knowledge":
            if self._retriever is None:
                self._retriever = KnowledgeRetriever()
            return await self._retriever.search(
                str(args.get("query", "")), top_k=int(args.get("top_k", 5))
            )
        if name == "query_incident_context":
            return await self._incident_context(ctx)
        if name == "query_current_metrics":
            return await self._current_metrics(args, ctx)
        if name == "query_metric_history":
            return await self._metric_history(args, ctx)
        cfg = await ProjectService(self.session).runtime_config(ctx.project_id)
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
