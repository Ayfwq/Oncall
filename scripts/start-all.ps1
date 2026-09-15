$ErrorActionPreference="Stop"
$root=(Resolve-Path "$PSScriptRoot\..").Path
Set-Location $root
if (!(Test-Path .env)) { Copy-Item .env.example .env }

$venv = Join-Path $root '.venv\Scripts'
if (!(Test-Path (Join-Path $venv 'oncall-api.exe'))) {
  Write-Host "Missing .venv console scripts. Run first:  uv sync --all-extras" -ForegroundColor Red
  exit 1
}

Write-Host "Starting Oncall processes in separate PowerShell windows..."
foreach ($svc in @('oncall-api','oncall-monitor-worker','oncall-agent-worker','oncall-notification-worker','oncall-rag-worker')) {
  $exe = Join-Path $venv "$svc.exe"
  Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location '$root'; & '$exe'"
}
Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location '$root\frontend'; npm run dev"
Write-Host "API: http://127.0.0.1:9900  Web: http://localhost:5173"
