"""Validate configured external model/embedding/rerank/Feishu endpoints.

The default mode is diagnostic: disabled or unconfigured optional services are
reported as SKIP.  ``--required`` turns the same checks into a release gate and
fails when any selected check is skipped or fails.
"""
from __future__ import annotations

import argparse
import asyncio

import httpx
from oncall.bootstrap.config import get_settings

DEFAULT_TIMEOUT = 15.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--required",
        action="store_true",
        help="fail if a selected external dependency is disabled or unconfigured",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"per-request timeout in seconds (default: {DEFAULT_TIMEOUT:g})",
    )
    parser.add_argument(
        "--only",
        choices=("llm", "embedding", "rerank", "feishu"),
        action="append",
        help="check only the named dependency; repeat for multiple dependencies",
    )
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    return args


def format_failure(error: Exception) -> str:
    """Format an error without exposing settings or credential values."""
    return f"FAIL {type(error).__name__}: {error}"


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout))


async def check_llm(s, timeout: float = DEFAULT_TIMEOUT):
    if s.model_provider == "mock":
        return "SKIP (mock provider)"
    if not s.model_api_key or not s.model_base_url:
        return "SKIP (missing model endpoint or key)"
    async with _client(timeout) as c:
        r = await c.post(
            f"{s.model_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {s.model_api_key}"},
            json={
                "model": s.model_name,
                "temperature": 0,
                "messages": [{"role": "user", "content": "Reply exactly: ONCALL_OK"}],
            },
        )
        r.raise_for_status()
        text = str(r.json()["choices"][0]["message"]["content"])
    return 'PASS' if 'ONCALL_OK' in text else 'PASS (unexpected content but API responded)'


async def check_embedding(s, timeout: float = DEFAULT_TIMEOUT):
    key = s.embedding_api_key or s.model_api_key
    base = (s.embedding_base_url or s.model_base_url).rstrip('/')
    if not key or not base or not s.embedding_model:
        return "SKIP (hash fallback or missing endpoint/key)"
    async with _client(timeout) as c:
        r = await c.post(
            f"{base}/embeddings",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": s.embedding_model, "input": ["oncall health check"]},
        )
        r.raise_for_status()
        vec = r.json()["data"][0]["embedding"]
    return (
        f"PASS (dimension={len(vec)})"
        if len(vec) == s.embedding_dimension
        else f"FAIL dimension={len(vec)} expected={s.embedding_dimension}"
    )


async def check_rerank(s, timeout: float = DEFAULT_TIMEOUT):
    if not (s.rerank_base_url and s.rerank_api_key and s.rerank_model):
        return "SKIP (local fallback or incomplete configuration)"
    async with _client(timeout) as c:
        r = await c.post(
            s.rerank_base_url,
            headers={"Authorization": f"Bearer {s.rerank_api_key}"},
            json={
                "model": s.rerank_model,
                "query": "CPU 高",
                "documents": ["CPU 使用率过高处理方案", "数据库备份方案"],
                "top_n": 1,
            },
        )
        r.raise_for_status()
        r.json()
    return 'PASS'


async def check_feishu(s, timeout: float = DEFAULT_TIMEOUT):
    if not s.feishu_enabled:
        return "SKIP (disabled)"
    if not s.feishu_app_id or not s.feishu_app_secret:
        return "FAIL missing Feishu app credentials"
    async with _client(timeout) as c:
        r = await c.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": s.feishu_app_id, "app_secret": s.feishu_app_secret},
        )
        r.raise_for_status()
        body = r.json()
    return 'PASS' if body.get('tenant_access_token') else f"FAIL {body.get('msg','no token')}"


async def main(argv: list[str] | None = None):
    args = parse_args(argv)
    s = get_settings()
    checks = [
        ("llm", "LLM", check_llm),
        ("embedding", "Embedding", check_embedding),
        ("rerank", "Rerank", check_rerank),
        ("feishu", "Feishu", check_feishu),
    ]
    selected = set(args.only or (name for name, _, _ in checks))
    failed = False
    for key, label, fn in checks:
        if key not in selected:
            continue
        try:
            result = await fn(s, timeout=args.timeout)
        except Exception as error:
            result = format_failure(error)
        print(f"[{label}] {result}")
        failed = failed or result.startswith("FAIL") or (args.required and result.startswith("SKIP"))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
