$ErrorActionPreference="Stop"
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d
uv sync --all-extras
uv run alembic -c backend/alembic.ini upgrade head
Write-Host "Infra ready. Open five terminals for: oncall-api / oncall-monitor-worker / oncall-agent-worker / oncall-notification-worker / oncall-rag-worker"
