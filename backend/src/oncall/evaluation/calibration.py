from __future__ import annotations

from pathlib import Path

from oncall.evaluation.dataset import write_json
from oncall.evaluation.judge import RagasJudge


async def calibrate(output: Path, names: list[str]) -> dict:
    """Sanity-check the judge against explicitly synthetic, labeled fixtures."""
    judge = RagasJudge(names)
    common = {
        "user_input": "监控指标缺失时应该如何处理？",
        "reference": "先检查采集链路；不能将缺失指标当作零。",
        "retrieved_contexts": ["指标缺失时应先检查采集链路，不能将缺失指标当作零。"],
        "status": "ok",
        "expected_answerable": True,
    }
    rows = [
        {**common, "id": "faithful-fixture", "response": "先检查采集链路，不能把缺失指标当作零。"},
        {
            **common,
            "id": "hallucinated-fixture",
            "response": "把缺失指标当作零，并自动重启所有服务。",
        },
    ]
    try:
        for row in rows:
            row.update(await judge.score(row))
            print(f"calibration {row['id']}: {row['scores']}", flush=True)
    finally:
        await judge.close()
    checks = {}
    for name in ("faithfulness", "factual_correctness"):
        if name in names:
            good = rows[0]["scores"].get(name)
            bad = rows[1]["scores"].get(name)
            checks[name] = good is not None and bad is not None and good >= 0.9 and bad <= 0.3
    result = {
        "scope": "synthetic judge calibration; NOT production Agent or retrieval quality",
        "judge": judge.metadata,
        "metric_prompt_hash": judge.prompt_hash,
        "checks": checks,
        "passed": bool(checks)
        and all(checks.values())
        and not any(row["metric_errors"] for row in rows),
        "samples": rows,
    }
    write_json(output, result)
    return result
