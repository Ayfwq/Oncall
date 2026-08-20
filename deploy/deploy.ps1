# deploy.ps1 - 一键同步代码到服务器并增量更新（无需重建时只 restart，秒级生效）
# 前置：本机已安装 python3 + paramiko（pip install paramiko）
# 用法：powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python "$PSScriptRoot\sync.py"
if ($LASTEXITCODE -ne 0) {
    Write-Host "deploy failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host ""
Write-Host "deploy done. Health check:" -ForegroundColor Green
Write-Host "  curl http://8.138.47.45:3000/api/health"
