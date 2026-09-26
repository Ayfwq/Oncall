from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import ValidationError

from oncall.agent.prompts import DECISION_SCHEMA, STREAM_ANSWER_PROMPT, SYSTEM_PROMPT
from oncall.bootstrap.config import get_settings
from oncall.domain.schemas import AgentDecision, DiagnosisReport
from oncall.monitoring.signals import SUPPORTED_SIGNALS


class ModelProvider:
    async def decide(self, context: dict[str, Any]) -> AgentDecision:
        raise NotImplementedError

    async def summarize(self, text: str) -> str:
        return text[:6000]

    async def stream_answer(self, context: dict[str, Any], on_token=None) -> str:
        """Generate the final prose answer. Streaming providers override this; the
        deterministic fallback reuses decide() so Mock keeps working without tokens."""
        d = await self.decide(context)
        ans = d.answer or ""
        if on_token and ans:
            on_token(ans)
        return ans


class ModelServiceError(RuntimeError):
    """An upstream model endpoint could not serve a model request."""

    category = "model_service_unavailable"


def _model_service_failure(status_code: int) -> str:
    if status_code in (401, 403):
        reason = "API Key 无效或没有调用权限"
    elif status_code == 402:
        reason = "服务商账户余额或计费状态异常"
    elif status_code == 429:
        reason = "调用频率受限或模型额度不足"
    elif status_code == 404:
        reason = "接口地址或模型名称不存在"
    elif status_code >= 500:
        reason = "服务商暂时不可用"
    else:
        reason = "请求配置错误"
    return f"大模型服务异常：{reason}（HTTP {status_code}）"


class MockProvider(ModelProvider):
    """Deterministic development provider. It proves graph/tool/persistence wiring without external API keys."""

    @staticmethod
    def _citation_suffix(context: dict[str, Any]) -> str:
        refs = [x for x in context.get("knowledge_refs", []) if x.get("citation_id")]
        if not refs:
            return ""
        return "\n\n知识库依据：" + " ".join(f"[{x['citation_id']}]" for x in refs[:5])

    async def decide(self, context: dict[str, Any]) -> AgentDecision:
        mode = context.get("mode", "chat")
        called_keys = set(context.get("called_tools", []))
        called = {x.split(":", 1)[0] for x in called_keys}
        msg = (context.get("user_message") or "").lower()
        incident = context.get("incident_context") or {}
        if mode == "chat":
            if context.get("intent") == "casual_chat":
                return AgentDecision(
                    action="final",
                    answer="你好，我是 PulseOps（巡脉智能运维平台）的 AI 助手，可以协助回答运维问题、检索知识库，并在绑定项目后查询实时运行状态。",
                )
            realtime = any(
                k in msg
                for k in [
                    "cpu",
                    "内存",
                    "memory",
                    "磁盘",
                    "gpu",
                    "进程",
                    "日志",
                    "log",
                    "报错",
                    "docker",
                    "数据库",
                    "postgres",
                    "接口",
                    "api",
                    "健康",
                    "health",
                    "指标",
                    "现在",
                    "当前",
                ]
            )
            if realtime and context.get("project_id"):
                if any(k in msg for k in ["日志", "log", "报错", "exception"]):
                    name, args = (
                        "search_logs",
                        {"since_minutes": 30, "limit": 200, "group_by_signature": True},
                    )
                elif any(k in msg for k in ["数据库", "postgres", "锁", "慢 sql", "慢sql"]):
                    name, args = "query_database_health", {}
                elif any(k in msg for k in ["容器", "docker", "进程", "oom", "重启"]):
                    name, args = "query_runtime_resources", {}
                else:
                    name, args = "query_current_metrics", {}
                if name not in called:
                    return AgentDecision(
                        action="tool",
                        rationale="需要读取远程项目的真实运行状态",
                        tool_name=name,
                        tool_args=args,
                    )
            if "search_knowledge" not in called:
                return AgentDecision(
                    action="tool",
                    rationale="先检索运维知识库",
                    tool_name="search_knowledge",
                    tool_args={"query": context.get("user_message", "")},
                )
            ev = context.get("evidence", [])
            summary = "；".join(x.get("summary", "") for x in ev[-3:])
            if not summary and context.get("knowledge_refs"):
                summary = "已检索到相关运维知识库内容"
            summary = summary or "知识库暂未提供可用内容"
            if context.get("project_id"):
                answer = f"基于当前可用信息：{summary}\n\n如果你希望我继续检查该项目的实时状态，我可以调用监控工具复查。{self._citation_suffix(context)}"
            else:
                answer = f"基于当前可用信息：{summary}\n\n当前为通用运维问答模式。{self._citation_suffix(context)}"
            return AgentDecision(action="final", answer=answer)
        if mode == "follow_up":
            realtime = any(
                k in msg
                for k in [
                    "现在",
                    "当前",
                    "cpu",
                    "内存",
                    "memory",
                    "gpu",
                    "进程",
                    "日志",
                    "log",
                    "报错",
                    "指标",
                    "docker",
                    "数据库",
                    "postgres",
                    "接口",
                    "api",
                    "health",
                    "健康",
                    "恢复",
                ]
            )
            if realtime and context.get("project_id"):
                if any(k in msg for k in ["日志", "log", "报错", "exception"]):
                    name, args = (
                        "search_logs",
                        {"since_minutes": 30, "limit": 200, "group_by_signature": True},
                    )
                elif any(k in msg for k in ["数据库", "postgres", "锁", "慢 sql", "慢sql"]):
                    name, args = "query_database_health", {}
                elif any(k in msg for k in ["容器", "docker", "进程", "oom", "重启"]):
                    name, args = "query_runtime_resources", {}
                else:
                    name, args = "query_current_metrics", {}
                if name not in called:
                    return AgentDecision(
                        action="tool",
                        rationale="追问涉及实时状态，需要复查",
                        tool_name=name,
                        tool_args=args,
                    )
            prev = context.get("incident_context") or {}
            ev = context.get("evidence", [])
            summary = "；".join(x.get("summary", "") for x in ev[-4:])
            return AgentDecision(
                action="final",
                answer=f"这是同一个 Incident 的后续追问。当前事故：{prev.get('summary', '')}。已掌握证据：{summary or '暂无新增实时证据'}。如需确认当前状态，我可以继续调用只读工具复查。",
            )
        # automatic incident investigation
        sequence = ["query_incident_context", "query_current_metrics"]
        anomaly = str(incident.get("anomaly_type", ""))
        if anomaly in SUPPORTED_SIGNALS:
            sequence.append("query_metric_history")
        if anomaly.startswith("app."):
            sequence.extend(["search_logs", "query_runtime_resources"])
        elif anomaly.startswith("db."):
            sequence.extend(["query_database_health", "search_logs"])
        elif anomaly.startswith("log."):
            sequence.extend(["search_logs", "query_runtime_resources"])
        else:
            sequence.extend(["query_runtime_resources", "search_logs"])
        sequence.append("search_knowledge")
        for name in sequence:
            if name not in called:
                if name == "search_knowledge":
                    args = {"query": f"{anomaly} 远程 Python 服务运维处理方案"}
                elif name == "search_logs":
                    args = {
                        "level": "ERROR",
                        "since_minutes": 30,
                        "limit": 200,
                        "group_by_signature": True,
                    }
                elif name == "query_metric_history":
                    args = {"metrics": [anomaly], "minutes": 30, "include_samples": False}
                else:
                    args = {}
                return AgentDecision(
                    action="tool", rationale=f"收集 {name} 证据", tool_name=name, tool_args=args
                )
        evidence = context.get("evidence", [])
        summaries = [x.get("summary", "") for x in evidence if x.get("summary")]
        report = DiagnosisReport(
            summary=f"检测到 {anomaly or '运行异常'}",
            severity=incident.get("severity", "warning"),
            affected_service=incident.get("project_name"),
            symptoms=[incident.get("summary", "异常触发")],
            evidence=summaries[-8:],
            root_cause="当前证据显示存在运行异常；Mock 模型不会虚构更具体根因，配置真实 LLM 后将基于 Evidence 进行因果判断。",
            confidence=0.55 if summaries else 0.2,
            remediation=[
                "按照报告中的 Evidence 逐项确认异常资源",
                "参考知识库命中的 SOP 进行人工处置",
                "当前不自动执行有副作用操作",
            ],
            verification=[
                "重新检查触发指标已越过 recovery threshold",
                "确认 Prometheus 连续两个监测周期恢复正常",
            ],
            risks=["执行任何重启/终止进程前先确认业务任务状态"],
            knowledge_refs=context.get("knowledge_refs", []),
            unknowns=[] if summaries else ["缺少有效工具证据"],
        )
        return AgentDecision(action="final", diagnosis=report)


class OpenAICompatibleProvider(ModelProvider):
    def __init__(self):
        self.s = get_settings()

    async def _chat(self, messages: list[dict[str, str]], temperature: float = 0.1) -> str:
        last: BaseException | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=90) as client:
                    r = await client.post(
                        f"{self.s.model_base_url.rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {self.s.model_api_key}"},
                        json={
                            "model": self.s.model_name,
                            "temperature": temperature,
                            "messages": messages,
                        },
                    )
                    r.raise_for_status()
                    body = r.json()
                    return str(body["choices"][0]["message"]["content"])
            except httpx.HTTPStatusError as exc:
                last = exc
                if (exc.response.status_code == 429 or exc.response.status_code >= 500) and attempt < 2:
                    await __import__("asyncio").sleep(2**attempt)
                    continue
                raise ModelServiceError(_model_service_failure(exc.response.status_code)) from exc
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last = exc
                if attempt < 2:
                    await __import__("asyncio").sleep(2**attempt)
                    continue
        reason = "连接超时" if isinstance(last, httpx.TimeoutException) else "无法连接"
        raise ModelServiceError(f"大模型服务异常：{reason}，请检查 Endpoint 和网络。") from last

    @staticmethod
    def _json_candidate(content: str) -> str:
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I | re.S).strip()
        if text.startswith("{") and text.endswith("}"):
            return text
        start = text.find("{")
        end = text.rfind("}")
        return text[start : end + 1] if start >= 0 and end > start else text

    @staticmethod
    def _normalize_knowledge_refs(candidate: str, context: dict[str, Any]) -> str:
        """Keep citations grounded in refs returned by the retrieval tool.

        Some compatible models emit a title-only citation even after schema repair.
        A title can safely be completed only when it exactly matches an available
        retrieval result; otherwise it is discarded instead of failing the whole
        incident investigation or inventing a document id.
        """
        try:
            payload = json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            return candidate
        diagnosis = payload.get("diagnosis") if isinstance(payload, dict) else None
        if not isinstance(diagnosis, dict) or not isinstance(diagnosis.get("knowledge_refs"), list):
            return candidate
        available = [
            item
            for item in context.get("knowledge_refs", [])
            if isinstance(item, dict) and item.get("document_id")
        ]
        by_citation_id = {
            str(item.get("citation_id")): item
            for item in available
            if item.get("citation_id")
        }
        by_chunk_id = {
            str(item.get("chunk_id")): item
            for item in available
            if item.get("chunk_id")
        }
        by_title = {str(item.get("title")): item for item in available if item.get("title")}
        normalized = []
        for item in diagnosis["knowledge_refs"]:
            if not isinstance(item, dict):
                continue
            match = None
            if item.get("citation_id"):
                match = by_citation_id.get(str(item["citation_id"]))
            if match is None and item.get("chunk_id"):
                match = by_chunk_id.get(str(item["chunk_id"]))
            if match is None and item.get("title"):
                match = by_title.get(str(item.get("title")))
            if match:
                normalized.append(dict(match))
        diagnosis["knowledge_refs"] = normalized
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _normalize_diagnosis_lists(candidate: str) -> str:
        """Coerce common structured evidence items into the report's text fields.

        Compatible models sometimes return evidence as ``{"source": ..., "detail": ...}``
        objects even though the public DiagnosisReport contract intentionally keeps
        evidence as a list of human-readable strings. Preserve the model's facts in
        text form instead of failing the whole investigation at schema validation.
        """
        try:
            payload = json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            return candidate
        diagnosis = payload.get("diagnosis") if isinstance(payload, dict) else None
        if not isinstance(diagnosis, dict):
            return candidate
        for field in ("symptoms", "evidence", "remediation", "verification", "risks", "unknowns"):
            values = diagnosis.get(field)
            if not isinstance(values, list):
                continue
            normalized = []
            for value in values:
                if isinstance(value, str):
                    normalized.append(value)
                elif isinstance(value, dict):
                    source = str(value.get("source") or value.get("tool") or "").strip()
                    detail = str(
                        value.get("detail") or value.get("summary") or value.get("message") or ""
                    ).strip()
                    text = "：".join(x for x in (source, detail) if x)
                    normalized.append(text or json.dumps(value, ensure_ascii=False, default=str))
                else:
                    normalized.append(str(value))
            diagnosis[field] = normalized
        return json.dumps(payload, ensure_ascii=False)

    async def decide(self, context: dict[str, Any]) -> AgentDecision:
        payload_context = json.dumps(context, ensure_ascii=False, default=str)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n" + DECISION_SCHEMA},
            {"role": "user", "content": payload_context},
        ]
        content = await self._chat(messages, 0.1)
        candidate = self._normalize_diagnosis_lists(
            self._normalize_knowledge_refs(self._json_candidate(content), context)
        )
        try:
            return AgentDecision.model_validate_json(candidate)
        except ValidationError as first_error:
            repair = [
                {
                    "role": "system",
                    "content": "把下面内容修复为严格符合指定 AgentDecision schema 的单个 JSON 对象。不得增加事实，不要 Markdown。\n"
                    + DECISION_SCHEMA,
                },
                {
                    "role": "user",
                    "content": f"Validation error: {first_error}\nOriginal output:\n{content[:12000]}",
                },
            ]
            fixed = self._normalize_diagnosis_lists(
                self._normalize_knowledge_refs(
                    self._json_candidate(await self._chat(repair, 0.0)), context
                )
            )
            try:
                return AgentDecision.model_validate_json(fixed)
            except ValidationError as second_error:
                raise RuntimeError(
                    f"LLM returned invalid AgentDecision after one repair: {second_error}; content={fixed[:1000]}"
                ) from second_error

    async def summarize(self, text: str) -> str:
        messages = [
            {
                "role": "system",
                "content": "你是 PulseOps 的会话记忆压缩器。只保留可验证事实、用户目标、已执行检查、结论和未决事项；不要编造。",
            },
            {"role": "user", "content": text[:50000]},
        ]
        return (await self._chat(messages, 0.0))[:12000]

    async def stream_answer(self, context: dict[str, Any], on_token=None) -> str:
        """Stream the final prose answer token-by-token via SSE (stream=True)."""
        payload_context = json.dumps(context, ensure_ascii=False, default=str)
        messages = [
            {"role": "system", "content": STREAM_ANSWER_PROMPT},
            {"role": "user", "content": payload_context},
        ]
        last: BaseException | None = None
        for attempt in range(3):
            parts: list[str] = []
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    async with client.stream(
                        "POST",
                        f"{self.s.model_base_url.rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {self.s.model_api_key}"},
                        json={
                            "model": self.s.model_name,
                            "temperature": 0.2,
                            "stream": True,
                            "messages": messages,
                        },
                    ) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            payload = line[5:].strip()
                            if payload == "[DONE]":
                                break
                            try:
                                obj = json.loads(payload)
                                delta = obj.get("choices", [{}])[0].get("delta", {}).get("content")
                            except Exception:
                                continue
                            if delta:
                                parts.append(delta)
                                if on_token:
                                    on_token(delta)
                return "".join(parts).strip()
            except httpx.HTTPStatusError as exc:
                last = exc
                if (exc.response.status_code == 429 or exc.response.status_code >= 500) and attempt < 2:
                    await __import__("asyncio").sleep(2**attempt)
                    continue
                raise ModelServiceError(_model_service_failure(exc.response.status_code)) from exc
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last = exc
                if attempt < 2:
                    await __import__("asyncio").sleep(2**attempt)
                    continue
        reason = "连接超时" if isinstance(last, httpx.TimeoutException) else "无法连接"
        raise ModelServiceError(f"大模型服务异常：{reason}，请检查 Endpoint 和网络。") from last


def get_model_provider() -> ModelProvider:
    s = get_settings()
    if s.model_provider == "mock":
        return MockProvider()
    if not s.model_api_key:
        raise ModelServiceError("大模型服务异常：尚未配置 API Key，请前往“模型接入”补充密钥。")
    return OpenAICompatibleProvider()
