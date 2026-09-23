from __future__ import annotations

import json

from oncall.agent.model_gateway import OpenAICompatibleProvider


def test_title_only_citation_is_grounded_or_removed() -> None:
    candidate = json.dumps(
        {
            "action": "final",
            "diagnosis": {
                "knowledge_refs": [
                    {"title": "数据库排障手册"},
                    {"title": "模型自行编造的文档"},
                ],
            },
        },
        ensure_ascii=False,
    )
    context = {
        "knowledge_refs": [
            {"document_id": "doc-1", "version_id": "v1", "title": "数据库排障手册"},
        ]
    }

    normalized = json.loads(OpenAICompatibleProvider._normalize_knowledge_refs(candidate, context))

    assert normalized["diagnosis"]["knowledge_refs"] == [
        {"document_id": "doc-1", "version_id": "v1", "title": "数据库排障手册"},
    ]


def test_structured_evidence_is_coerced_to_report_text() -> None:
    candidate = json.dumps(
        {
            "action": "final",
            "diagnosis": {
                "evidence": [
                    {"source": "query_current_metrics", "detail": "CPU 已回落至 28%."},
                    "数据库连接正常",
                ],
            },
        },
        ensure_ascii=False,
    )

    normalized = json.loads(OpenAICompatibleProvider._normalize_diagnosis_lists(candidate))

    assert normalized["diagnosis"]["evidence"] == [
        "query_current_metrics：CPU 已回落至 28%.",
        "数据库连接正常",
    ]
