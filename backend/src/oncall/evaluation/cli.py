from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from oncall.evaluation.dataset import (
    dataset_hash,
    import_template,
    read_dataset,
    write_dataset,
    write_json,
)
from oncall.evaluation.judge import ALL_METRICS, CORE_METRICS, RagasJudge
from oncall.evaluation.report import DEFAULT_THRESHOLDS, report_data, save_report

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DATASET = ROOT / "evaluation" / "datasets" / "knowledge.jsonl"


def pipeline_hash() -> str:
    paths = []
    for folder in ("agent", "rag", "evaluation"):
        paths.extend((ROOT / "backend/src/oncall" / folder).rglob("*.py"))
    paths.extend(
        ROOT / f"backend/src/oncall/application/{name}.py"
        for name in (
            "agent_service",
            "memory_policy",
            "conversation_service",
            "long_term_memory",
        )
    )
    return hashlib.sha256(b"".join(path.read_bytes() for path in sorted(paths))).hexdigest()


def git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def choose_samples(args):
    samples = read_dataset(args.dataset)
    if args.sample_id:
        unknown = set(args.sample_id) - {s.id for s in samples}
        if unknown:
            raise ValueError(f"unknown sample ids: {sorted(unknown)}")
        samples = [s for s in samples if s.id in args.sample_id]
    if args.limit:
        samples = samples[: args.limit]
    return samples


def thresholds_from_file(path: Path | None) -> dict:
    thresholds = dict(DEFAULT_THRESHOLDS)
    if path:
        configured = json.loads(path.read_text(encoding="utf-8"))
        if set(configured) - set(thresholds):
            raise ValueError("unknown threshold metric")
        if any(not isinstance(v, int | float) or not 0 <= v <= 1 for v in configured.values()):
            raise ValueError("thresholds must be between 0 and 1")
        thresholds.update(configured)
    return thresholds


async def collect(args):
    from oncall.evaluation.collector import (
        collect_sample,
        corpus_snapshot,
        create_eval_user,
        runtime_metadata,
        source_available,
    )

    samples = choose_samples(args)
    directory = args.output or ROOT / "output/evaluation" / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]
    )
    manifest_path = directory / "manifest.json"
    corpus = await corpus_snapshot()
    metadata = {
        "dataset_hash": dataset_hash(samples),
        "corpus": corpus,
        "corpus_hash": hashlib.sha256(json.dumps(corpus, sort_keys=True).encode()).hexdigest(),
        "pipeline_hash": pipeline_hash(),
        "runtime": runtime_metadata(),
    }
    if manifest_path.exists():
        if not args.resume:
            raise ValueError(f"run already exists: {directory}; use --resume or a new directory")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key, value in metadata.items():
            if manifest.get(key) != value:
                raise ValueError(f"cannot resume: {key} changed; use a new output directory")
    else:
        manifest = {
            **metadata,
            "run_id": directory.name,
            "started_at": datetime.now(UTC).isoformat(),
            "git_revision": git_revision(),
            "sample_ids": [s.id for s in samples],
            "python": sys.version.split()[0],
            "ragas_version": importlib.metadata.version("ragas"),
            "evaluation_scope": "single-turn production Agent, shared workspace KB, no project/incident",
            "sample_timeout": args.timeout,
            "concurrency": args.concurrency,
            "user_id": str(await create_eval_user()),
        }
        write_json(manifest_path, manifest)
    semaphore = asyncio.Semaphore(args.concurrency)
    from uuid import UUID

    async def one(sample):
        path = directory / "samples" / f"{sample.id}.json"
        if args.resume and path.exists():
            previous = json.loads(path.read_text(encoding="utf-8"))
            if previous.get("status") == "ok":
                print(f"reuse collected {sample.id}", flush=True)
                return previous
        async with semaphore:
            print(f"collect {sample.id}", flush=True)
            row = await collect_sample(sample, UUID(manifest["user_id"]), args.timeout)
            row["source_available"] = source_available(sample, corpus)
            write_json(path, row)
            print(
                f"collected {sample.id}: {row['status']}, contexts={len(row['retrieved_contexts'])}",
                flush=True,
            )
            return row

    rows = await asyncio.gather(*(one(sample) for sample in samples))
    write_json(directory / "collected.json", rows)
    manifest["snapshot_unchanged"] = (
        await corpus_snapshot() == corpus
        and runtime_metadata() == metadata["runtime"]
        and pipeline_hash() == metadata["pipeline_hash"]
    )
    write_json(manifest_path, manifest)
    print(f"captured: {directory}", flush=True)
    return directory


async def score(args, directory: Path):
    manifest_path = directory / "manifest.json"
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = json.loads((directory / "collected.json").read_text(encoding="utf-8"))
    names = args.metrics.split(",")
    if len(names) != len(set(names)) or set(names) - set(ALL_METRICS):
        raise ValueError(f"metrics must be unique and selected from {ALL_METRICS}")
    if (
        any(not row.get("expected_answerable", True) for row in rows)
        and "safe_abstention" not in names
    ):
        names.append("safe_abstention")
    judge = RagasJudge(names)
    scoring_config = {
        "metrics": names,
        "judge": judge.metadata,
        "metric_prompt_hash": judge.prompt_hash,
        "ragas_version": importlib.metadata.version("ragas"),
    }
    try:
        if args.resume and "judge" in metadata:
            for key, value in scoring_config.items():
                if metadata.get(key) != value:
                    raise ValueError(
                        f"cannot resume scoring: {key} changed; score without --resume to rescore"
                    )
        metadata.update(scoring_config)
        write_json(manifest_path, metadata)
        for row in rows:
            path = directory / "scored" / f"{row['id']}.json"
            if args.resume and path.exists():
                previous = json.loads(path.read_text(encoding="utf-8"))
                if not previous.get("metric_errors"):
                    row.update(
                        {
                            key: previous[key]
                            for key in ("scores", "metric_errors", "metric_details")
                        }
                    )
                    continue
            print(f"score {row['id']}", flush=True)
            row.update(await judge.score(row))
            write_json(path, row)
            print(f"scored {row['id']}: {row['scores']}", flush=True)
    finally:
        await judge.close()
    metadata["finished_at"] = datetime.now(UTC).isoformat()
    write_json(manifest_path, metadata)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    report = report_data(
        rows,
        metadata,
        names,
        thresholds_from_file(args.thresholds),
        baseline=baseline,
        require_reviewed=True,
        max_regression=args.max_regression,
    )
    save_report(directory, report)
    print(f"report: {directory / 'report.md'}", flush=True)
    print(f"GATE: {'PASS' if report['gate']['passed'] else 'FAIL'}", flush=True)
    return 2 if args.gate and not report["gate"]["passed"] else 0


async def dispatch(args):
    if args.command in ("run", "score"):
        names = args.metrics.split(",")
        if len(names) != len(set(names)) or set(names) - set(ALL_METRICS):
            raise ValueError(f"metrics must be unique and selected from {ALL_METRICS}")
        thresholds_from_file(args.thresholds)
        if args.baseline:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            if not baseline.get("gate", {}).get("passed"):
                raise ValueError(
                    "baseline did not pass its quality gate; select a validated baseline"
                )
    if args.command == "run":
        # Validate dependencies and judge configuration before paid answer calls.
        preview = RagasJudge(names)
        await preview.close()
    if args.command == "calibrate":
        from oncall.evaluation.calibration import calibrate

        names = args.metrics.split(",")
        if len(names) != len(set(names)) or set(names) - set(CORE_METRICS):
            raise ValueError(f"calibration metrics must be unique and selected from {CORE_METRICS}")
        result = await calibrate(args.output, names)
        return 0 if result["passed"] else 1
    if args.command == "merge":
        samples = [sample for path in args.input for sample in read_dataset(path)]
        if len({sample.id for sample in samples}) != len(samples):
            raise ValueError("duplicate IDs across input datasets")
        write_dataset(args.output, samples)
        print(f"merged {len(samples)} samples: {args.output}")
        return 0
    if args.command == "doctor":
        from oncall.evaluation.preflight import preflight

        result = await preflight()
        if args.output:
            write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    if args.command == "compare":
        current = json.loads(args.current.read_text(encoding="utf-8"))
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        report = report_data(
            current["samples"],
            current["metadata"],
            current["metadata"]["metrics"],
            current["thresholds"],
            baseline=baseline,
            max_regression=args.max_regression,
        )
        save_report(args.output, report)
        print(json.dumps(report["comparison"], ensure_ascii=False, indent=2))
        return 2 if args.gate and not report["gate"]["passed"] else 0
    if args.command == "import-template":
        samples = import_template(args.input, args.output)
        print(f"imported {len(samples)} draft samples: {args.output}")
        return 0
    if args.command == "validate":
        samples = read_dataset(args.dataset)
        print(
            json.dumps(
                {
                    "samples": len(samples),
                    "hash": dataset_hash(samples),
                    "reviewed": sum(s.reviewed for s in samples),
                    "categories": sorted({s.category for s in samples}),
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "generate":
        from oncall.evaluation.generate import generate_dataset

        if args.output.exists():
            raise ValueError("draft dataset already exists; choose a new output path")
        await generate_dataset(args.output, args.size, args.seed)
        return 0
    if args.command in ("collect", "run"):
        directory = await collect(args)
        if args.command == "collect":
            return 0
    else:
        directory = args.run_dir
    return await score(args, directory)


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def regression_limit(value: str) -> float:
    number = float(value)
    if not 0 <= number <= 1:
        raise argparse.ArgumentTypeError("regression allowance must be between 0 and 1")
    return number


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="PulseOps knowledge evaluation with Ragas 0.4.3")
    commands = root.add_subparsers(dest="command", required=True)
    calibration = commands.add_parser("calibrate")
    calibration.add_argument("--metrics", default="faithfulness,factual_correctness")
    calibration.add_argument("--output", type=Path, required=True)
    merger = commands.add_parser("merge")
    merger.add_argument("--input", type=Path, action="append", required=True)
    merger.add_argument("--output", type=Path, required=True)
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--output", type=Path)
    comparison = commands.add_parser("compare")
    comparison.add_argument("--current", type=Path, required=True)
    comparison.add_argument("--baseline", type=Path, required=True)
    comparison.add_argument("--output", type=Path, required=True)
    comparison.add_argument("--max-regression", type=regression_limit, default=0.03)
    comparison.add_argument("--gate", action="store_true")
    importer = commands.add_parser("import-template")
    importer.add_argument("--input", type=Path, required=True)
    importer.add_argument("--output", type=Path, required=True)
    validator = commands.add_parser("validate")
    validator.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    generator = commands.add_parser("generate")
    generator.add_argument("--output", type=Path, required=True)
    generator.add_argument("--size", type=positive_int, default=12)
    generator.add_argument("--seed", type=int, default=42)
    for command in ("collect", "run", "score"):
        sub = commands.add_parser(command)
        sub.add_argument("--resume", action="store_true")
        if command in ("collect", "run"):
            sub.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
            sub.add_argument("--output", type=Path)
            sub.add_argument("--limit", type=positive_int)
            sub.add_argument("--sample-id", action="append")
            sub.add_argument("--concurrency", type=positive_int, default=1)
            sub.add_argument("--timeout", type=positive_int, default=240)
        else:
            sub.add_argument("--run-dir", type=Path, required=True)
        if command != "collect":
            sub.add_argument("--metrics", default=",".join(CORE_METRICS))
            sub.add_argument("--baseline", type=Path)
            sub.add_argument("--thresholds", type=Path)
            sub.add_argument("--max-regression", type=regression_limit, default=0.03)
            sub.add_argument(
                "--gate", action="store_true", help="exit 2 if the quality/review gate fails"
            )
    return root


def run():
    args = parser().parse_args()
    try:
        code = asyncio.run(dispatch(args))
    except (ValueError, OSError, ImportError) as exc:
        print(f"evaluation error: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)
