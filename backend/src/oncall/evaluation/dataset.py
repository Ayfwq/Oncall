from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EvalSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120, pattern=r"^[\w.-]+$")
    user_input: str = Field(min_length=1, max_length=1000)
    reference: str = Field(min_length=1)
    reference_contexts: list[str] = Field(default_factory=list)
    category: str = "general"
    question_type: str = "single_hop"
    expected_answerable: bool = True
    reviewed: bool = False
    source_titles: list[str] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)
    source_version_ids: list[str] = Field(default_factory=list)
    source_pages: str | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("user_input", "reference")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain non-whitespace text")
        return value.strip()

    @field_validator("reference_contexts", "source_titles")
    @classmethod
    def nonempty_items(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("context/source entries cannot be blank")
        return values

    @model_validator(mode="after")
    def evidence_required(self):
        if self.expected_answerable and not self.reference_contexts:
            raise ValueError("answerable samples require reference_contexts")
        return self


def read_dataset(path: Path) -> list[EvalSample]:
    samples: list[EvalSample] = []
    seen: set[str] = set()
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            sample = EvalSample.model_validate_json(line)
        except ValueError as exc:
            raise ValueError(f"{path}:{line_no}: {exc}") from exc
        if sample.id in seen:
            raise ValueError(f"duplicate sample id: {sample.id}")
        seen.add(sample.id)
        samples.append(sample)
    if not samples:
        raise ValueError("empty evaluation dataset")
    return samples


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_dataset(path: Path, samples: list[EvalSample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(sample.model_dump_json() + "\n" for sample in samples), encoding="utf-8"
    )


def dataset_hash(samples: list[EvalSample]) -> str:
    content = json.dumps(
        [s.model_dump() for s in samples], sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def import_template(source: Path, target: Path) -> list[EvalSample]:
    """Migrate the old empty-answer template; never import its response as gold."""
    samples = []
    for line in source.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        title = row.get("source_document") or row.get("source_title", "")
        # Match KB titles independently of file format or the legacy numeric prefix.
        if title:
            title = Path(title).stem
            title = title.lstrip("0123456789-_ ")
        samples.append(
            EvalSample(
                id=row["id"],
                user_input=row.get("user_input") or row["question"],
                reference=row.get("reference") or row["ground_truth"],
                reference_contexts=row.get("reference_contexts") or row.get("gold_contexts", []),
                category=row.get("category", "general"),
                question_type=row.get("question_type", "single_hop"),
                source_titles=[title] if title else [],
                source_pages=row.get("source_pages"),
                tags=["legacy-runbook", "draft"],
            )
        )
    write_dataset(target, samples)
    return read_dataset(target)
