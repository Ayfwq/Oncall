$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $root
if (!(Test-Path .env)) { Copy-Item .env.example .env }

$venv = Join-Path $root '.venv\Scripts'
$services = @('oncall-api', 'oncall-agent-worker', 'oncall-notification-worker', 'oncall-rag-worker')
foreach ($service in $services) {
  if (!(Test-Path (Join-Path $venv "$service.exe"))) {
    throw 'Missing .venv console scripts. Run first: uv sync --locked --extra dev'
  }
}
$viteCli = Join-Path $root 'frontend\node_modules\vite\bin\vite.js'
if (!(Test-Path $viteCli)) { throw 'Missing frontend dependencies. Run npm ci in frontend first.' }
$nodeExe = (Get-Command node -ErrorAction Stop).Source
$logDir = Join-Path $root 'logs\local'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

docker compose up -d
if ($LASTEXITCODE -ne 0) { throw 'Failed to start database and vector-store containers.' }
docker compose -f compose.local.monitoring.yaml up -d
if ($LASTEXITCODE -ne 0) { throw 'Failed to start monitoring containers.' }

# Independent background processes survive closing the launching terminal.
foreach ($service in $services) {
  $exe = Join-Path $venv "$service.exe"
  $running = Get-Process -Name $service -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $exe }
  if ($running) {
    Write-Host "$service is already running; skip duplicate process."
    continue
  }
  $process = Start-Process -FilePath $exe -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir "$service.out.log") `
    -RedirectStandardError (Join-Path $logDir "$service.err.log") -PassThru
  $process.Id | Set-Content (Join-Path $logDir "$service.pid")
  Write-Host "Started $service (PID $($process.Id))."
}

if (Get-NetTCPConnection -State Listen -LocalPort 5173 -ErrorAction SilentlyContinue) {
  Write-Host 'Port 5173 is already listening; skip duplicate frontend.'
} else {
  $process = Start-Process -FilePath $nodeExe -WorkingDirectory (Join-Path $root 'frontend') `
    -ArgumentList @("`"$viteCli`"", '--host', '127.0.0.1', '--port', '5173', '--strictPort') `
    -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'frontend.out.log') `
    -RedirectStandardError (Join-Path $logDir 'frontend.err.log') -PassThru
  $process.Id | Set-Content (Join-Path $logDir 'frontend.pid')
  Write-Host "Started frontend (PID $($process.Id))."
}

$ready = $false
for ($attempt = 0; $attempt -lt 45; $attempt++) {
  try {
    # Validate both the web page and the API proxy before reporting success.
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:5173/api/health' -TimeoutSec 2
    $page = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:5173/' -TimeoutSec 2
    if ($health.ok -and $page.StatusCode -eq 200) {
      $ready = $true
      break
    }
  } catch { }
  Start-Sleep -Seconds 1
}
if (!$ready) { throw "Local services did not become ready. Inspect logs in $logDir" }
Write-Host 'API: http://127.0.0.1:9900  Web: http://127.0.0.1:5173'
Write-Host "Services run in the background. Logs: $logDir"
