from __future__ import annotations

import csv
import math
import random
import statistics
from pathlib import Path

from oncall.evaluation.dataset import write_json

DEFAULT_THRESHOLDS = {
    "context_precision": 0.70,
    "context_recall": 0.80,
    "faithfulness": 0.90,
    "answer_relevancy": 0.80,
    "factual_correctness": 0.80,
    "safe_abstention": 1.0,
}


def numeric(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "mean": None, "ci95": None, "min": None}
    rng = random.Random(42)
    bootstrap = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(500))
    return {
        "count": len(values),
        "mean": statistics.mean(values),
        "min": min(values),
        "ci95": [bootstrap[12], bootstrap[487]],
    }


def summarize(rows: list[dict], metrics: list[str]) -> dict:
    return {
        name: stats(
            [
                row.get("scores", {}).get(name)
                for row in rows
                if numeric(row.get("scores", {}).get(name))
            ]
        )
        for name in metrics
    }


def report_data(
    rows: list[dict],
    metadata: dict,
    metrics: list[str],
    thresholds: dict,
    baseline: dict | None = None,
    require_reviewed: bool = True,
    max_regression: float = 0.03,
    require_core_metrics: bool = True,
) -> dict:
    failures: list[str] = []
    summary = summarize(rows, metrics)
    answerable = [row for row in rows if row.get("expected_answerable", True)]
    negatives = [row for row in rows if not row.get("expected_answerable", True)]
    if not rows:
        failures.append("no evaluation samples")
    if metadata.get("snapshot_unchanged") is False:
        failures.append("knowledge corpus, runtime configuration or code changed during collection")
    required = set(DEFAULT_THRESHOLDS) - {"safe_abstention"}
    if require_core_metrics and required - set(metrics):
        failures.append(
            "missing required release metrics: " + ", ".join(sorted(required - set(metrics)))
        )
    failed = [row for row in rows if row.get("status") != "ok"]
    if failed:
        failures.append(f"pipeline failures: {len(failed)}/{len(rows)}")
    missing = [row for row in answerable if row.get("source_available") is not True]
    if missing:
        failures.append(f"missing/unbound gold sources: {len(missing)}/{len(answerable)}")
    unreviewed = sum(not row.get("reviewed", False) for row in rows)
    if require_reviewed and unreviewed:
        failures.append(f"unreviewed reference samples: {unreviewed}/{len(rows)}")
    fallback = sum(bool(row.get("rerank_fallback")) for row in rows)
    if fallback:
        failures.append(f"reranker fallback: {fallback}/{len(rows)}")
    for name in metrics:
        eligible = negatives if name == "safe_abstention" else answerable
        if not eligible:
            continue
        count = sum(numeric(row.get("scores", {}).get(name)) for row in eligible)
        if count != len(eligible):
            failures.append(f"{name}: incomplete scores {count}/{len(eligible)}")
        mean = summary[name]["mean"]
        if numeric(mean) and mean < thresholds[name]:
            failures.append(f"{name}: {mean:.3f} < {thresholds[name]:.3f}")
    slices = {}
    for field in ("category", "question_type"):
        slices[field] = {
            label: summarize([row for row in rows if row.get(field) == label], metrics)
            for label in sorted({row.get(field, "general") for row in rows})
        }
    comparison = None
    if baseline is not None:
        comparison = {"comparable": True, "deltas": {}, "reasons": []}
        if not baseline.get("gate", {}).get("passed"):
            comparison["reasons"].append("baseline gate did not pass")
        for key in ("dataset_hash", "corpus_hash", "metrics", "judge", "metric_prompt_hash"):
            if baseline.get("metadata", {}).get(key) != metadata.get(key):
                comparison["reasons"].append(f"{key} changed")
        comparison["comparable"] = not comparison["reasons"]
        if not comparison["comparable"]:
            failures.append("baseline is not comparable: " + "; ".join(comparison["reasons"]))
        else:
            for name in metrics:
                old = baseline.get("summary", {}).get(name, {}).get("mean")
                new = summary[name]["mean"]
                if numeric(old) and numeric(new):
                    delta = new - old
                    comparison["deltas"][name] = delta
                    if delta < -max_regression:
                        failures.append(
                            f"{name}: regression {delta:.3f} exceeds {max_regression:.3f}"
                        )
    return {
        "schema_version": 1,
        "metadata": metadata,
        "summary": summary,
        "slices": slices,
        "comparison": comparison,
        "thresholds": thresholds,
        "gate": {
            "passed": not failures,
            "failures": failures,
            "require_reviewed": require_reviewed,
            "max_regression": max_regression,
        },
        "counts": {
            "total": len(rows),
            "pipeline_failed": len(failed),
            "unreviewed": unreviewed,
            "missing_sources": len(missing),
            "negative": len(negatives),
            "rerank_fallback": fallback,
        },
        "latency_ms": stats([row["latency_ms"] for row in rows if numeric(row.get("latency_ms"))]),
        "samples": rows,
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# PulseOps Ragas 评估报告",
        "",
        f"运行：`{report['metadata']['run_id']}`；门禁：**{'PASS' if report['gate']['passed'] else 'FAIL'}**。",
        "",
        "空值表示未获得有效评分，未计为 0，也不视为通过。自动生成的参考答案必须经人工审核才能用于发布门禁。",
        "",
        "| 指标 | 有效样本数 | 均值 | Bootstrap 95% 区间 | 门槛 |",
        "|---|---:|---:|---|---:|",
    ]
    for name, stat in report["summary"].items():
        mean = f"{stat['mean']:.3f}" if stat["mean"] is not None else "未评分"
        ci = "–" if stat["ci95"] is None else f"{stat['ci95'][0]:.3f}–{stat['ci95'][1]:.3f}"
        lines.append(
            f"| {name} | {stat['count']} | {mean} | {ci} | {report['thresholds'][name]:.2f} |"
        )
    lines += ["", "## 数据覆盖", "", json_text(report["counts"]), "", "## 门禁原因", ""]
    lines += [f"- {reason}" for reason in report["gate"]["failures"]] or ["- 无。"]
    if report["comparison"]:
        lines += ["", "## 基线比较", "", json_text(report["comparison"])]
    for field, groups in report["slices"].items():
        lines += [
            "",
            f"## 分组：{field}",
            "",
            "| 分组 | 指标 | 均值 | 有效样本数 |",
            "|---|---|---:|---:|",
        ]
        for label, metrics in groups.items():
            for name, stat in metrics.items():
                mean = "–" if stat["mean"] is None else f"{stat['mean']:.3f}"
                lines.append(f"| {label.replace('|', '/')} | {name} | {mean} | {stat['count']} |")
    lines += ["", "## 逐题诊断", ""]
    for row in report["samples"]:
        lines += [
            f"### {row['id']}",
            "",
            row["user_input"],
            "",
            f"状态：{row.get('status')}；召回上下文：{len(row.get('retrieved_contexts', []))}；来源可用：{row.get('source_available')}。",
            "",
            json_text(row.get("scores", {})),
            "",
        ]
        if row.get("error"):
            lines += [f"错误：{row['error']}", ""]
        for metric, error in row.get("metric_errors", {}).items():
            lines += [f"- {metric}: {error}"]
        lines += [
            "",
            "**实际回答**",
            "",
            row.get("response") or "（无回答）",
            "",
            "**参考答案**",
            "",
            row["reference"],
            "",
        ]
    return "\n".join(lines) + "\n"


def json_text(value) -> str:
    import json

    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


def save_report(directory: Path, report: dict) -> None:
    write_json(directory / "report.json", report)
    (directory / "report.md").write_text(render_markdown(report), encoding="utf-8")
    names = list(report["summary"])
    with (directory / "scores.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "id",
                "category",
                "question_type",
                "status",
                "source_available",
                "latency_ms",
                *names,
            ],
        )
        writer.writeheader()
        for row in report["samples"]:
            values = {key: row.get(key) for key in writer.fieldnames if key not in names}
            values.update({name: row.get("scores", {}).get(name) for name in names})
            # Spreadsheet exports must not execute model/dataset text as formulas.
            writer.writerow(
                {
                    key: "'" + value
                    if isinstance(value, str) and value.startswith(("=", "+", "-", "@"))
                    else value
                    for key, value in values.items()
                }
            )
