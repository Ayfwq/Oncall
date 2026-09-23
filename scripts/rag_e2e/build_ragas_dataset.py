"""Build a RAGAS-compatible golden evaluation set from the 12 runbooks.

The generated template intentionally leaves ``response`` and
``retrieved_contexts`` empty. They must be filled with the real PulseOps RAG
answer and retrieved chunks before running RAGAS. ``reference`` and
``reference_contexts`` are the gold answer and source evidence.
"""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path


ROOT = Path(r"D:\Oncall")
BUILDER = ROOT / "tmp" / "pulseops_runbooks" / "build_12_runbooks.py"
OUT = ROOT / "output" / "evaluation" / "pulseops-ragas-12-runbooks"
SOURCE_PDF = ROOT / "output" / "pdf" / "pulseops-运维处置手册-12类-合并版.pdf"


def load_topics() -> list[dict]:
    spec = importlib.util.spec_from_file_location("pulseops_runbook_builder", BUILDER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {BUILDER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TOPICS


def compact(items: list[str]) -> list[str]:
    return [x.strip() for x in items if x and x.strip()]


def make_sample(
    sample_id: str,
    item: dict,
    question: str,
    reference: str,
    contexts: list[str],
    expected_keywords: list[str],
    kind: str,
    start_page: int,
) -> dict:
    end_page = start_page + 6
    context_list = compact(contexts)
    return {
        "id": sample_id,
        "user_input": question,
        "question": question,
        "response": "",
        "retrieved_contexts": [],
        "reference": reference,
        "ground_truth": reference,
        "reference_contexts": context_list,
        "gold_contexts": context_list,
        "source_document": item["file"],
        "source_title": item["title"],
        "source_pdf": str(SOURCE_PDF),
        "source_pages": f"{start_page}-{end_page}",
        "category": item["title"],
        "question_type": kind,
        "expected_keywords": compact(expected_keywords)[:10],
    }


def build() -> list[dict]:
    topics = load_topics()
    rows: list[dict] = []
    for index, item in enumerate(topics, start=1):
        start_page = (index - 1) * 7 + 1
        prefix = f"{index:02d}"
        checks = item["checks"]
        branches = item["branches"]
        actions = item["actions"]
        verify = item["verify"]
        prevent = item["prevent"]

        q1 = f"{item['title']}告警触发后，值班人员首先应该如何判断问题范围和根因方向？"
        ref1 = (
            f"先确认告警持续时间、受影响实例、业务影响、最近变更和并发告警；"
            f"再区分问题属于指标采集、主机或容器资源、进程或服务、依赖或数据库、发布或配置哪一层。"
            f"{item['why']}"
        )
        ctx1 = [
            item["why"],
            "；".join(f"{name}：{text}" for name, text in checks[:2]),
        ]
        rows.append(
            make_sample(
                f"ragas-{index:02d}-01",
                item,
                q1,
                ref1,
                ctx1,
                item["metrics"][:4] + [checks[0][0], checks[1][0]],
                "根因定位与分层判断",
                start_page,
            )
        )

        branch_name, branch_text = branches[0]
        q2 = f"{item['title']}中，如果出现“{branch_name}”，应该如何安全处置？"
        ref2 = f"{branch_text}处置时应先完成证据确认，并明确负责人、影响范围和回滚条件。" \
            f"建议同时遵循：{actions[0]}"
        ctx2 = [
            f"原因分支 {branch_name}：{branch_text}",
            "；".join(actions[:2]),
        ]
        rows.append(
            make_sample(
                f"ragas-{index:02d}-02",
                item,
                q2,
                ref2,
                ctx2,
                [branch_name, *item["metrics"][:3], "回滚", "证据"],
                "分支处置与风险控制",
                start_page,
            )
        )

        q3 = f"{item['title']}处置完成后，如何验证恢复并防止问题再次发生？"
        ref3 = (
            "恢复验证：" + "；".join(verify) +
            "。长期治理：" + "；".join(prevent)
        )
        ctx3 = ["；".join(verify), "；".join(prevent)]
        rows.append(
            make_sample(
                f"ragas-{index:02d}-03",
                item,
                q3,
                ref3,
                ctx3,
                ["恢复验证", "告警 resolved", "观察窗口", "预防", "长期治理"],
                "恢复验证与预防治理",
                start_page,
            )
        )
    return rows


def write_outputs(rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    jsonl = OUT / "ragas_eval_template.jsonl"
    with jsonl.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    csv_path = OUT / "ragas_eval_template.csv"
    fields = [
        "id",
        "user_input",
        "response",
        "retrieved_contexts",
        "reference",
        "reference_contexts",
        "source_document",
        "source_pages",
        "category",
        "question_type",
        "expected_keywords",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(row[key], ensure_ascii=False)
                if isinstance(row[key], list)
                else row[key]
                for key in fields
            })

    readme = OUT / "README.md"
    readme.write_text(
        "# PulseOps RAGAS 评估数据集\n\n"
        "本目录包含基于 12 类运维手册生成的 36 条 RAGAS 评估样本，每类 3 条：根因定位、分支处置、恢复验证。\n\n"
        "## 字段说明\n\n"
        "- `user_input`：评估问题。\n"
        "- `reference` / `ground_truth`：标准参考答案。\n"
        "- `reference_contexts`：标准答案对应的知识库证据。\n"
        "- `response`：留空，待填入 PulseOps 实际生成回答。\n"
        "- `retrieved_contexts`：留空，待填入 PulseOps 实际召回的 chunk 内容。\n"
        "- `source_pages`：合并 PDF 中的参考页码范围。\n\n"
        "## 使用方式\n\n"
        "先针对每条 `user_input` 调用 PulseOps 的真实检索和问答链路，把返回答案写入 `response`，把召回 chunk 文本写入 `retrieved_contexts`，再转换为 RAGAS 的 `SingleTurnSample` 或 Hugging Face Dataset。\n\n"
        "当前项目的 `pyproject.toml` 没有安装 `ragas`，因此本目录提供的是可直接填充的评估模板，而不是已经计算出的分数。\n",
        encoding="utf-8",
    )
    print(f"rows={len(rows)}")
    print(f"jsonl={jsonl}")
    print(f"csv={csv_path}")


if __name__ == "__main__":
    write_outputs(build())
