# Server deployment

The production stack is defined in `compose.server.yaml`. It exposes only the
Web entry point (port 3000 by default); PostgreSQL, Milvus, MinIO, etcd and the
API remain on the private Compose network.

Build acceleration is enabled at three layers:

- Docker Hub pulls use the server's Alibaba Cloud registry mirrors.
- Python packages use the Alibaba Cloud PyPI mirror.
- npm packages use npmmirror, with BuildKit cache mounts for uv and npm.

Deploy:

```bash
cp .env.server.example .env
# Edit all passwords and secrets before continuing.
DOCKER_BUILDKIT=1 docker compose -f compose.server.yaml build api frontend
docker compose -f compose.server.yaml up -d
docker compose -f compose.server.yaml ps
curl -fsS http://127.0.0.1:3000/api/health
```

The API container has a writable bind mount for `.env`, so Feishu settings
saved from the Web UI persist on the host. Restart the API and Agent Worker
after changing those settings.
