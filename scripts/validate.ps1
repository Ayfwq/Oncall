$ErrorActionPreference="Stop"
Write-Host "validate.ps1 is the dependency-installed build check. For full target-machine verification use scripts\validate-local.ps1 and scripts\e2e-local.ps1."
& "$PSScriptRoot\validate-local.ps1"
