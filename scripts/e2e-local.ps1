param([ValidateRange(10, 1800)][int]$Timeout=120)
$ErrorActionPreference="Stop"
$root=(Resolve-Path "$PSScriptRoot\..").Path
Set-Location $root
uv run python scripts/e2e_smoke.py --timeout $Timeout
