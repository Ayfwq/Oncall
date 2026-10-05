param(
  [string]$Dataset = 'evaluation/datasets/knowledge.jsonl',
  [string]$Output = '',
  [string]$Baseline = '',
  [int]$Limit = 0,
  [switch]$Resume,
  [switch]$Gate
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..").Path
Push-Location $root
try {
  # --no-sync preserves the explicitly installed evaluation extra.
  $evalArgs = @('run', '--dataset', $Dataset, '--thresholds', 'evaluation/thresholds.json')
  if ($Output) { $evalArgs += @('--output', $Output) }
  if ($Baseline) { $evalArgs += @('--baseline', $Baseline) }
  if ($Limit -gt 0) { $evalArgs += @('--limit', "$Limit") }
  if ($Resume) { $evalArgs += '--resume' }
  if ($Gate) { $evalArgs += '--gate' }
  uv run --no-sync python -m oncall.evaluation @evalArgs
  $evalExitCode = $LASTEXITCODE
} finally { Pop-Location }
exit $evalExitCode
