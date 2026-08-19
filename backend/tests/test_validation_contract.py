"""Offline contracts for the test and release-validation harness itself."""
from __future__ import annotations

import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHECK_EXTERNAL = runpy.run_path(str(ROOT / "scripts" / "check_external.py"))


def test_pytest_layers_are_declared_in_project_config():
    config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for marker in ("offline", "local", "integration", "rag"):
        assert f'"{marker}:' in config
    assert "--strict-markers" in config


def test_external_checker_has_bounded_timeout_and_strict_mode():
    args = CHECK_EXTERNAL["parse_args"](["--required", "--timeout", "7"])
    assert args.required is True
    assert args.timeout == 7


def test_external_checker_does_not_print_secret_values_in_status_format():
    format_failure = CHECK_EXTERNAL["format_failure"]
    message = format_failure(RuntimeError("upstream rejected request"))
    assert message.startswith("FAIL RuntimeError:")
    assert "api_key" not in message.lower()
