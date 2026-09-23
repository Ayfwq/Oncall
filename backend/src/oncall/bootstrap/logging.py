from __future__ import annotations

import contextvars
import logging
import re
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import structlog

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | request=%(request_id)s | %(message)s"
_LOGFILE_NAME = "oncall.log"

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def set_request_id(request_id: str) -> None:
    _request_id_var.set(request_id)


def get_request_id() -> str:
    return _request_id_var.get()


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()
        return True


def cleanup_old_logs(log_dir: Path, keep_days: int, logfile_name: str = _LOGFILE_NAME) -> None:
    cutoff = (datetime.now() - timedelta(days=keep_days)).date()
    rotated_re = re.compile(rf"^{re.escape(logfile_name)}\.(\d{{4}}-\d{{2}}-\d{{2}})$")
    if not log_dir.is_dir():
        return
    for path in log_dir.iterdir():
        match = rotated_re.match(path.name)
        if not match:
            continue
        try:
            file_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            path.unlink(missing_ok=True)


def configure_logging(
    level: str = "INFO",
    log_dir: Path | None = None,
    retention_days: int = 2,
    component: str = "oncall",
) -> None:
    level_num = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter(_FORMAT)
    root = logging.getLogger()
    root.setLevel(level_num)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler()
    console.setLevel(level_num)
    console.setFormatter(formatter)
    console.addFilter(_RequestIdFilter())
    root.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        safe_component = re.sub(r"[^a-zA-Z0-9_-]+", "-", component).strip("-") or "oncall"
        logfile_name = f"{safe_component}.log"
        file_handler = TimedRotatingFileHandler(
            log_dir / logfile_name,
            when="midnight",
            backupCount=max(1, retention_days - 1),
            encoding="utf-8",
        )
        file_handler.setLevel(level_num)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(_RequestIdFilter())
        root.addHandler(file_handler)
        cleanup_old_logs(log_dir, retention_days, logfile_name)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
