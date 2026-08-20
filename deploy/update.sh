#!/usr/bin/env bash
# Smart incremental deploy. Run on the server after deploy/deploy.ps1 syncs code.
# Decides what to rebuild/restart by comparing checksums stored in .deploy/checksums.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
COMPOSE=(docker compose -f compose.server.yaml)
STAMP=".deploy/checksums"
mkdir -p "$STAMP"

files_changed() {
  local name="$1"; shift
  local cur prev
  cur="$(sha256sum "$@" | sha256sum | cut -d' ' -f1)"
  prev="$(cat "$STAMP/$name" 2>/dev/null || echo none)"
  if [ "$cur" != "$prev" ]; then
    echo "$cur" > "$STAMP/$name"
    return 0
  fi
  return 1
}

tree_changed() {
  local name="$1"; shift
  local cur prev
  cur="$(find "$@" -type f -not -path '*/__pycache__/*' -print0 2>/dev/null | sort -z | xargs -0 sha256sum 2>/dev/null | sha256sum | cut -d' ' -f1)"
  prev="$(cat "$STAMP/$name" 2>/dev/null || echo none)"
  if [ "$cur" != "$prev" ]; then
    echo "$cur" > "$STAMP/$name"
    return 0
  fi
  return 1
}

NEED_BACKEND_BUILD=0
NEED_FRONTEND_BUILD=0
NEED_BACKEND_RESTART=0
NEED_MIGRATE=0

if files_changed deps.backend Dockerfile.backend pyproject.toml uv.lock .dockerignore; then
  echo "==> backend 依赖/镜像定义变化：重建后端镜像（国内镜像加速）"
  NEED_BACKEND_BUILD=1
  NEED_MIGRATE=1
fi

if files_changed deps.frontend Dockerfile.frontend frontend/package.json frontend/package-lock.json; then
  echo "==> frontend 依赖/镜像定义变化：重建前端镜像"
  NEED_FRONTEND_BUILD=1
fi

if tree_changed src.backend backend; then
  echo "==> backend 代码变化：无需重建镜像，restart 生效（代码已挂载）"
  NEED_BACKEND_RESTART=1
  NEED_MIGRATE=1
fi

if tree_changed src.frontend frontend/src frontend/public; then
  echo "==> frontend 代码变化：重新构建静态产物"
  NEED_FRONTEND_BUILD=1
fi

if [ "$NEED_BACKEND_BUILD" = 1 ]; then
  "${COMPOSE[@]}" build api
fi
if [ "$NEED_FRONTEND_BUILD" = 1 ]; then
  "${COMPOSE[@]}" build frontend
fi
if [ "$NEED_MIGRATE" = 1 ]; then
  echo "==> 执行数据库迁移"
  "${COMPOSE[@]}" run --rm migrate
fi

RECREATE=()
[ "$NEED_BACKEND_BUILD" = 1 ] && RECREATE+=(api monitor-worker agent-worker rag-worker)
[ "$NEED_BACKEND_RESTART" = 1 ] && RECREATE+=(api monitor-worker agent-worker rag-worker)
[ "$NEED_FRONTEND_BUILD" = 1 ] && RECREATE+=(frontend)
if [ "${#RECREATE[@]}" -gt 0 ]; then
  "${COMPOSE[@]}" up -d --no-deps --force-recreate "${RECREATE[@]}"
fi

"${COMPOSE[@]}" up -d
"${COMPOSE[@]}" ps
echo "==> 更新完成"
