"""Token budgets for a 64K model context."""

from __future__ import annotations

import gzip
from functools import lru_cache
from math import ceil
from pathlib import Path

from oncall.bootstrap.config import get_settings


@lru_cache(maxsize=4)
def _tokenizer(path: str):
    from tokenizers import Tokenizer

    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return Tokenizer.from_str(stream.read())
    return Tokenizer.from_file(path)


def count_tokens(value: str) -> int:
    if not value:
        return 0
    settings = get_settings()
    tokenizer_path = settings.memory_tokenizer_path
    if not tokenizer_path or not Path(tokenizer_path).is_file():
        if settings.model_name == "mimo-v2.6-flash":
            tokenizer_path = Path(__file__).resolve().parents[1] / "tokenizers/mimo-v2.6-flash.json.gz"
    if tokenizer_path and Path(tokenizer_path).is_file():
        try:
            return len(_tokenizer(str(tokenizer_path)).encode(value).ids)
        except (ImportError, OSError, ValueError):
            pass
    # Conservative fallback for other model families without a local tokenizer.
    return ceil(len(value.encode("utf-8")) / 2)


def message_tokens(role: str, content: str) -> int:
    return count_tokens(content) + count_tokens(role) + 8


def take_recent_whole_turns(messages: list, budget: int) -> tuple[list, list]:
    """Split completed history into older and recent turns without cutting a pair."""
    turns: list[list] = []
    pending: list = []
    for message in messages:
        if message.role == "user" and pending:
            turns.append(pending)
            pending = []
        pending.append(message)
        if message.role == "assistant":
            turns.append(pending)
            pending = []
    if pending:
        turns.append(pending)

    recent: list[list] = []
    used = 0
    for turn in reversed(turns):
        size = sum(message_tokens(m.role, m.content) for m in turn)
        if recent and used + size > budget:
            break
        if size > budget:
            break
        recent.append(turn)
        used += size
    recent.reverse()
    split = len(turns) - len(recent)
    return [m for turn in turns[:split] for m in turn], [m for turn in recent for m in turn]


def trim_to_tokens(value: str, budget: int) -> str:
    if count_tokens(value) <= budget:
        return value
    lo, hi = 0, len(value)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if count_tokens(value[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return value[:lo]
