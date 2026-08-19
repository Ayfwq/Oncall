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

if ($marker) {
  Write-Host "== pytest layer: $Layer =="
  uv run pytest -m $marker -q --durations=10
} else {
  Write-Host "== pytest layer: all =="
  uv run pytest -q --durations=10
}
