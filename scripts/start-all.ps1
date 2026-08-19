$ErrorActionPreference="Stop"
$root=(Resolve-Path "$PSScriptRoot\..").Path
Set-Location $root
if (!(Test-Path .env)) { Copy-Item .env.example .env }
Write-Host "Starting Oncall processes in separate PowerShell windows..."
Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location '$root'; uv run oncall-api"
Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location '$root'; uv run oncall-monitor-worker"
Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location '$root'; uv run oncall-agent-worker"
Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location '$root'; uv run oncall-rag-worker"
Start-Process powershell -ArgumentList '-NoExit','-Command',"Set-Location '$root\frontend'; npm run dev"
Write-Host "API: http://127.0.0.1:9900  Web: http://127.0.0.1:5173"
