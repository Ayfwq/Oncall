param([switch]$External)
$ErrorActionPreference="Stop"
$root=(Resolve-Path "$PSScriptRoot\..").Path
Set-Location $root
Write-Host "== Docker infrastructure =="
docker compose ps
Write-Host "== Python doctor =="
uv run oncall-doctor
Write-Host "== Python compile =="
uv run python -m compileall -q backend/src backend/tests scripts
Write-Host "== Offline contract/unit tests =="
& "$root\scripts\test.ps1" -Layer offline
Write-Host "== Local host/integration tests =="
& "$root\scripts\test.ps1" -Layer local
Write-Host "== API/PostgreSQL integration tests =="
& "$root\scripts\test.ps1" -Layer integration
Write-Host "== RAG/Milvus integration tests =="
& "$root\scripts\test.ps1" -Layer rag
uv run ruff check backend/src backend/tests scripts
Write-Host "== Frontend production build =="
Push-Location frontend
try { npm run build } finally { Pop-Location }
if ($External) {
  Write-Host "== External credentials/API checks =="
  uv run python scripts/check_external.py --required
}
Write-Host "LOCAL VALIDATION: PASS"
