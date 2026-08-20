#!/usr/bin/env python3
"""Sync project files to the server and trigger the smart update.sh.

Requires: pip install paramiko
Usage:   python deploy/sync.py [--sync-only]
         --sync-only  upload files but skip running update.sh on the server
Env:     ONCALL_HOST / ONCALL_SSH_USER / ONCALL_SSH_PASSWORD (optional overrides)
"""
import os
import select
import stat
import sys
import time

import paramiko

HOST = os.environ.get("ONCALL_HOST", "8.138.47.45")
USER = os.environ.get("ONCALL_SSH_USER", "root")
PWD = os.environ.get("ONCALL_SSH_PASSWORD", "Hwq020223@")
REMOTE = "/opt/oncall-ai-sre/current"

EXCLUDE_DIRS = {
    ".git", ".github", ".venv", "__pycache__", "node_modules", "dist",
    ".next", ".deploy", "data", "logs", "work", "outputs",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
}
EXCLUDE_EXT = {".pyc", ".pyo", ".log", ".tmp"}

FILES = [
    "pyproject.toml",
    "uv.lock",
    "Dockerfile.backend",
    "Dockerfile.frontend",
    "compose.server.yaml",
    ".dockerignore",
    ".env.server.example",
    "deploy/update.sh",
    "deploy/nginx.conf",
]

DIRS = ["backend", "frontend"]


def is_excluded(rel: str, is_dir: bool) -> bool:
    parts = rel.split("/")
    for p in parts:
        if p in EXCLUDE_DIRS:
            return True
    if not is_dir:
        ext = os.path.splitext(parts[-1])[1]
        if ext in EXCLUDE_EXT:
            return True
    return False


def sftp_mkdirs(sftp, path: str) -> None:
    if not path or path == "/":
        return
    parts = [p for p in path.split("/") if p]
    cur = ""
    for p in parts:
        cur = f"{cur}/{p}"
        try:
            sftp.stat(cur)
        except FileNotFoundError:
            sftp.mkdir(cur)


def upload_file(sftp, local: str, remote: str) -> None:
    remote = remote.replace("\\", "/")
    sftp_mkdirs(sftp, os.path.dirname(remote))
    sftp.put(local, remote)
    print(f"    {local}")


def upload_tree(sftp, local_root: str, remote_root: str) -> None:
    for root, dirs, files in os.walk(local_root):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        rel_root = os.path.relpath(root, ".")
        if is_excluded(rel_root.replace(os.sep, "/"), True):
            continue
        for f in files:
            local = os.path.join(root, f)
            rel = os.path.relpath(local, ".").replace(os.sep, "/")
            if is_excluded(rel, False):
                continue
            upload_file(sftp, local, os.path.join(REMOTE, rel))


def main() -> int:
    sync_only = "--sync-only" in sys.argv
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"==> connecting {USER}@{HOST} ...")
    client.connect(HOST, username=USER, password=PWD, timeout=20)
    sftp = client.open_sftp()

    try:
        print("==> uploading config files ...")
        for f in FILES:
            if os.path.isfile(f):
                upload_file(sftp, f, REMOTE + "/" + f)
        for d in DIRS:
            if os.path.isdir(d):
                print(f"==> uploading {d}/ ...")
                upload_tree(sftp, d, REMOTE + "/" + d)
    finally:
        sftp.close()

    if sync_only:
        print("==> --sync-only: files uploaded, update.sh skipped")
        client.close()
        return 0

    print("==> trigger update.sh on server ...")
    client.exec_command(f"chmod +x {REMOTE}/deploy/update.sh", timeout=15)
    stdin, stdout, stderr = client.exec_command(
        f"bash {REMOTE}/deploy/update.sh", timeout=3600, get_pty=True
    )
    chan = stdout.channel
    chan.settimeout(0.2)
    code = None
    while True:
        try:
            data = chan.recv(4096).decode("utf-8", "replace")
            if data:
                sys.stdout.write(data)
                sys.stdout.flush()
            if chan.exit_status_ready():
                code = chan.recv_exit_status()
                break
        except socket_timeout:
            continue
        except Exception:
            break
    while True:
        try:
            data = chan.recv(4096).decode("utf-8", "replace")
            if not data:
                break
            sys.stdout.write(data)
            sys.stdout.flush()
        except Exception:
            break
    err = stderr.read().decode("utf-8", "replace")
    if err.strip():
        print("STDERR:", err.strip()[-2000:], file=sys.stderr)
    client.close()
    print(f"==> update.sh exit code: {code}")
    return code if code is not None else 1


if __name__ == "__main__":
    try:
        import socket

        socket_timeout = socket.timeout
    except ImportError:  # pragma: no cover
        socket_timeout = TimeoutError
    sys.exit(main())
