# Server deployment

The production stack is defined in `compose.server.yaml`. It exposes only the
Web entry point (port 3000 by default); PostgreSQL, Milvus, MinIO, etcd and the
API remain on the private Compose network.

Build acceleration is enabled at three layers:

- Docker Hub pulls use the DaoCloud/Alibaba Cloud registry mirrors.
- Python packages use the Alibaba Cloud PyPI mirror; PyTorch wheels come from
  the Huawei Cloud `pytorch-wheels` mirror (pinned in `uv.lock`).
- npm packages use npmmirror, with BuildKit cache mounts for uv and npm.

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
