# Server deployment

The production stack is defined in `compose.server.yaml`. It exposes only the
Web entry point (port 3000 by default); PostgreSQL, Milvus, MinIO, etcd and the
API remain on the private Compose network.

Build acceleration is enabled at three layers:

- Docker Hub pulls use the DaoCloud/Alibaba Cloud registry mirrors.
- Python packages use the Alibaba Cloud PyPI mirror; all 941 wheel URLs in
  `uv.lock` point to `mirrors.aliyun.com/pypi/packages`, and PyTorch wheels
  come from `mirrors.aliyun.com/pytorch-wheels/cpu` (pinned in `uv.lock`).
- npm packages use npmmirror, with BuildKit cache mounts for uv and npm.

## Expected time per change type

| 你改了什么                                   | update.sh 会做什么              | 预期耗时 |
| -------------------------------------------- | ------------------------------- | -------- |
| 只改 `backend/` 代码（最常见）              | 只 restart 后端容器（代码已挂载）| ~30 秒   |
| 只改 `frontend/src` / `frontend/public`      | 只重建前端镜像（npm 缓存命中）  | 2~4 分钟 |
| 改 `pyproject.toml` / `uv.lock` / Dockerfile | 重建后端镜像（torch/docling 重新打包）| 7~10 分钟（已走国内镜像） |

torch/docling 这类大依赖只会在依赖文件变化时才重新下载打包；
纯前后端代码更新**不会**触发它们（实测后端代码更新 29 秒完成、零镜像构建）。

## First-time deploy

```bash
cp .env.server.example .env
# Edit all passwords and secrets before continuing.
DOCKER_BUILDKIT=1 docker compose -f compose.server.yaml build api frontend
docker compose -f compose.server.yaml up -d
docker compose -f compose.server.yaml ps
curl -fsS http://127.0.0.1:3000/api/health
```

## Daily incremental update (recommended)

Local machine (Windows):

```powershell
powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1
```

`deploy.ps1` SFTP-syncs the code to the server (`/opt/oncall-ai-sre/current`)
and then runs `deploy/update.sh` on the server. `update.sh` compares checksums
stored in `.deploy/checksums` and only does what is needed:

- `backend` code changed (bind-mounted into the containers) -> just restarts the
  backend services, no rebuild, takes seconds.
- `Dockerfile.backend` / `pyproject.toml` / `uv.lock` changed -> rebuilds the
  backend image from the domestic mirrors, reuses the cached PyTorch/Docling
  layer whenever the dependency list is unchanged.
- `frontend` source or `package-lock.json` changed -> rebuilds the static bundle.
- compose file or `.env` changed -> `docker compose up -d` reconciles services.

The server's `.env` is never overwritten by the sync, so secrets stay local.
