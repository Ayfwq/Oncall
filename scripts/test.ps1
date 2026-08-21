param(
  [ValidateSet("offline", "local", "integration", "rag", "all")]
  [string]$Layer = "offline"
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $root

$marker = switch ($Layer) {
  "offline" { "offline" }
  "local" { "local" }
  "integration" { "integration" }
  "rag" { "rag" }
  "all" { "" }
}

# integration/rag/all are explicit service gates: missing services must fail
# the run instead of being silently skipped (see backend/tests/conftest.py).
$requireServices = @()
if ($Layer -in @("integration", "rag", "all")) {
  $requireServices = @("--require-services")
}

if ($marker) {
  Write-Host "== pytest layer: $Layer =="
  uv run pytest -m $marker -q --durations=10 @requireServices
} else {
  Write-Host "== pytest layer: all =="
  uv run pytest -q --durations=10 @requireServices
}
