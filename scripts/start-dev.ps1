$ErrorActionPreference="Stop"
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d
uv sync --all-extras
uv run alembic -c backend/alembic.ini upgrade head
Write-Host "Infra ready. Open four terminals for: oncall-api / oncall-monitor-worker / oncall-agent-worker / oncall-rag-worker"
