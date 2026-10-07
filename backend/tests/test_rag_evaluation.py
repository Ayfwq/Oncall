from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from oncall.agent.graph import OncallGraphRuntime
from oncall.agent.model_gateway import ModelProvider
from oncall.domain.schemas import AgentDecision
from oncall.evaluation.collector import RecordingProvider, context_passages, source_available
from oncall.evaluation.dataset import EvalSample, import_template, read_dataset, write_dataset
from oncall.evaluation.judge import RagasJudge
from oncall.evaluation.report import DEFAULT_THRESHOLDS, report_data, save_report
from pydantic import ValidationError


def sample(**changes):
    return EvalSample(
        **{
            "id": "sample-1",
            "user_input": "采集失败时是否可以将缺失数据当成零？",
            "reference": "不能把缺失值当成零。",
            "reference_contexts": ["不能把缺失值当成零。"],
            "source_titles": ["指标采集链路异常处置手册"],
            **changes,
        }
    )


def row(**changes):
    return {
        **sample().model_dump(),
        "status": "ok",
        "source_available": True,
        "reviewed": True,
        "response": "不能。",
        "scores": {"faithfulness": 1.0},
        "latency_ms": 30.0,
        **changes,
    }


def metadata(**changes):
    return {
        "run_id": "test",
        "dataset_hash": "data",
        "corpus_hash": "corpus",
        "metrics": ["faithfulness"],
        "judge": {"model": "judge-a"},
        "metric_prompt_hash": "prompts",
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"user_input": " "},
        {"reference_contexts": []},
        {"reference_contexts": [""]},
        {"id": "../../bad"},
        {"response": "cannot import answer as gold"},
    ],
)
def test_reject_invalid_or_leaking_dataset(changes):
    with pytest.raises(ValidationError):
        sample(**changes)


def test_negative_sample_can_have_no_evidence():
    assert not sample(expected_answerable=False, reference_contexts=[]).expected_answerable


def test_duplicate_dataset_ids_rejected(tmp_path):
    path = tmp_path / "dataset.jsonl"
    write_dataset(path, [sample(), sample()])
    with pytest.raises(ValueError, match="duplicate"):
        read_dataset(path)


def test_legacy_display_titles_cannot_break_source_binding(tmp_path):
    source = tmp_path / "legacy.jsonl"
    source.write_text(
        json.dumps(
            {
                "id": "latency-01",
                "user_input": "延迟如何排查",
                "reference": "检查依赖耗时",
                "reference_contexts": ["检查依赖耗时"],
                "source_title": "应用 P95/P99 延迟高处置手册",
                "source_document": "08-应用P95延迟高处置手册.docx",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    imported = import_template(source, tmp_path / "new.jsonl")
    assert imported[0].source_titles == ["应用P95延迟高处置手册"]


def test_shipped_dataset_has_all_runbook_categories():
    path = Path(__file__).resolve().parents[2] / "evaluation/datasets/runbooks.jsonl"
    samples = read_dataset(path)
    assert len(samples) == 36
    assert len({s.category for s in samples}) == 12
    assert not any(s.reviewed for s in samples)


@pytest.mark.asyncio
async def test_capture_production_trimming_and_citation_excerpts():
    class Delegate(ModelProvider):
        async def decide(self, context):
            return AgentDecision(action="final", answer="依据知识库回答")

    runtime = OncallGraphRuntime.__new__(OncallGraphRuntime)
    runtime.tool_specs = []
    context = runtime._context(
        {
            "user_message": "如何排查采集异常？",
            "mode": "chat",
            "knowledge_hits": [{"id": "hit", "content": "a" * 3000, "context_text": "b" * 5000}],
            "knowledge_refs": [{"chunk_id": "extra", "excerpt": "still visible citation"}],
        }
    )
    recorder = RecordingProvider(Delegate())
    await recorder.decide(context)
    passages = context_passages(recorder.answer_context)
    assert "a" * 1201 not in passages[0]
    assert "b" * 2401 not in passages[0]
    assert "still visible citation" in passages
    context["knowledge_hits"].clear()
    assert recorder.answer_context["knowledge_hits"]  # stored snapshot cannot be mutated later


def test_sources_require_every_bound_version():
    corpus = [{"title": "02-主机CPU高负载处置手册.pdf", "document_id": "doc", "version_id": "v1"}]
    assert source_available(sample(source_titles=["主机 CPU 高负载处置手册"]), corpus)
    assert not source_available(sample(source_titles=[], source_version_ids=["v2"]), corpus)
    assert source_available(sample(source_titles=[]), corpus) is None


def test_missing_scores_and_unreviewed_data_never_pass():
    report = report_data(
        [row(scores={"faithfulness": None}, reviewed=False)],
        metadata(),
        ["faithfulness"],
        DEFAULT_THRESHOLDS,
    )
    assert report["summary"]["faithfulness"]["mean"] is None
    assert not report["gate"]["passed"]
    assert any("incomplete" in reason for reason in report["gate"]["failures"])
    assert any("unreviewed" in reason for reason in report["gate"]["failures"])


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "failed"},
        {"source_available": False},
        {"rerank_fallback": True},
        {"scores": {"faithfulness": float("nan")}},
        {"scores": {"faithfulness": 0.5}},
    ],
)
def test_quality_or_pipeline_failure_blocks_gate(changes):
    report = report_data([row(**changes)], metadata(), ["faithfulness"], DEFAULT_THRESHOLDS)
    assert not report["gate"]["passed"]


def test_baseline_comparability_and_regression():
    baseline = report_data(
        [row()], metadata(), ["faithfulness"], DEFAULT_THRESHOLDS, require_core_metrics=False
    )
    lower = report_data(
        [row(scores={"faithfulness": 0.94})],
        metadata(),
        ["faithfulness"],
        DEFAULT_THRESHOLDS,
        baseline=baseline,
    )
    assert lower["comparison"]["comparable"]
    assert not lower["gate"]["passed"]
    assert any("regression" in reason for reason in lower["gate"]["failures"])
    different = report_data(
        [row()],
        metadata(judge={"model": "judge-b"}),
        ["faithfulness"],
        DEFAULT_THRESHOLDS,
        baseline=baseline,
    )
    assert not different["comparison"]["comparable"]
    assert different["comparison"]["deltas"] == {}


def test_failed_baseline_is_not_comparable():
    baseline = report_data([row(status="failed")], metadata(), ["faithfulness"], DEFAULT_THRESHOLDS)
    report = report_data(
        [row()], metadata(), ["faithfulness"], DEFAULT_THRESHOLDS, baseline=baseline
    )
    assert not report["comparison"]["comparable"]
    assert "baseline gate did not pass" in report["comparison"]["reasons"]


def test_a_partial_metric_run_cannot_pass_release_gate():
    report = report_data([row()], metadata(), ["faithfulness"], DEFAULT_THRESHOLDS)
    assert any(
        "missing required release metrics" in reason for reason in report["gate"]["failures"]
    )


def test_full_metric_run_can_pass_with_reviewed_sources():
    names = [name for name in DEFAULT_THRESHOLDS if name != "safe_abstention"]
    report = report_data(
        [row(scores={name: 1.0 for name in names})], metadata(), names, DEFAULT_THRESHOLDS
    )
    assert report["gate"]["passed"]


def test_evidence_normalization_does_not_accept_changed_facts():
    from oncall.evaluation.generate import normalize_evidence

    original = "Prometheus 指标缺失\n不能当作零。"
    assert normalize_evidence("Prometheus指标缺失 不能当作零。") == normalize_evidence(original)
    assert normalize_evidence("Prometheus指标缺失可以当作零。") != normalize_evidence(original)


def test_negative_scoring_coverage_does_not_dilute_positive_metrics():
    rows = [
        row(),
        row(
            id="negative",
            expected_answerable=False,
            scores={"safe_abstention": 1.0},
            source_available=None,
        ),
    ]
    report = report_data(
        rows,
        metadata(),
        ["faithfulness", "safe_abstention"],
        DEFAULT_THRESHOLDS,
        require_core_metrics=False,
    )
    assert report["gate"]["passed"]
    assert report["summary"]["faithfulness"]["count"] == 1


def test_report_csv_neutralizes_spreadsheet_formulas(tmp_path):
    report = report_data(
        [row(category="=cmd()")],
        metadata(),
        ["faithfulness"],
        DEFAULT_THRESHOLDS,
        require_core_metrics=False,
    )
    save_report(tmp_path, report)
    assert "'=cmd()" in (tmp_path / "scores.csv").read_text(encoding="utf-8-sig")
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["gate"]["passed"]


@pytest.mark.asyncio
async def test_metric_timeout_and_nan_are_explicit_errors():
    class FailingMetric:
        async def ascore(self, response: str):
            raise TimeoutError("remote judge timed out")

    class NanMetric:
        async def ascore(self, response: str):
            return SimpleNamespace(value=float("nan"), reason=None, traces=None)

    judge = RagasJudge.__new__(RagasJudge)
    judge.names = ["factual_correctness", "answer_relevancy"]
    judge.metrics = {"factual_correctness": FailingMetric(), "answer_relevancy": NanMetric()}
    judge.timeout = 1
    result = await judge.score(row(retrieved_contexts=["evidence"]))
    assert result["scores"] == {"factual_correctness": None, "answer_relevancy": None}
    assert "TimeoutError" in result["metric_errors"]["factual_correctness"]
    assert "non-finite" in result["metric_errors"]["answer_relevancy"]


@pytest.mark.asyncio
async def test_failed_retrieval_never_calls_judge():
    judge = RagasJudge.__new__(RagasJudge)
    judge.names = ["faithfulness"]
    judge.metrics = {}  # touching a metric would fail the test
    result = await judge.score(row(status="failed"))
    assert result["scores"]["faithfulness"] is None
    assert "pipeline failed" in result["metric_errors"]["faithfulness"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_timed_out_evaluation_does_not_leave_running_agent_run(monkeypatch, service_gate):
    from uuid import UUID

    from oncall.evaluation import collector
    from oncall.infrastructure.db.models import AgentRun, User
    from oncall.infrastructure.db.session import SessionFactory
    from sqlalchemy import delete, select, text

    try:
        async with SessionFactory() as session:
            await session.execute(text("select 1"))
    except Exception:
        service_gate(False, "PostgreSQL unavailable for evaluation timeout audit test")

    class TimeoutService:
        def __init__(self, session, *, model):
            self.session = session

        async def run(self, cid, question, channel):
            self.session.add(AgentRun(conversation_id=cid, mode="chat", status="running"))
            await self.session.commit()
            raise TimeoutError("injected evaluation timeout")

    monkeypatch.setattr(collector, "AgentService", TimeoutService)
    monkeypatch.setattr(collector, "get_model_provider", ModelProvider)
    user_id = await collector.create_eval_user()
    try:
        result = await collector.collect_sample(sample(), user_id, timeout=1)
        assert result["status"] == "failed"
        async with SessionFactory() as session:
            run = await session.scalar(
                select(AgentRun).where(AgentRun.conversation_id == UUID(result["conversation_id"]))
            )
            assert run.status == "failed"
            assert run.finished_at is not None
    finally:
        async with SessionFactory() as session:
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
