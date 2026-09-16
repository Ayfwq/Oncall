$ErrorActionPreference="Stop"
$root=(Resolve-Path "$PSScriptRoot\..").Path
Set-Location $root
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d
if (!(Test-Path ".venv\Scripts\python.exe")) { uv sync --all-extras }
& ".venv\Scripts\python.exe" -m alembic -c backend/alembic.ini upgrade head
Write-Host "PulseOps infrastructure ready. Start processes with: .\scripts\start-all.ps1"
