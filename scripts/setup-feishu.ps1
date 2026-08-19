<#
.SYNOPSIS
    One-shot Feishu bot setup for Oncall AI SRE.

.DESCRIPTION
    Writes the Feishu block into .env, validates credentials against the
    official Feishu auth endpoint, restarts oncall-api and the agent
    worker, and waits for the WebSocket to connect.

    Active push auto-binds to the first chat that messages the bot, so a
    brand-new user does NOT need to look up any chat_id. To pin proactive
    push to a specific group/user, pass -DefaultReceiveId.

    Prerequisite: the Feishu app must already exist and be published in
    the Feishu developer console. See docs/FEISHU_SETUP.md for the short
    checklist.

.PARAMETER AppId
    Feishu app App ID (cli_xxx). Prompts if omitted.

.PARAMETER AppSecret
    Feishu app App Secret. Prompts (masked) if omitted.

.PARAMETER DefaultReceiveId
    Optional. chat_id or open_id to receive proactive push. If omitted,
    the most recent inbound chat is used (auto-bind; message the bot
    once to activate).

.PARAMETER WaitSeconds
    Max seconds to wait for the WebSocket to connect. Default 360.

.EXAMPLE
    .\scripts\setup-feishu.ps1

.EXAMPLE
    .\scripts\setup-feishu.ps1 -AppId cli_xxx -AppSecret yyy -DefaultReceiveId oc_zzz
#>
[CmdletBinding()]
param(
    [string]$AppId = "",
    [string]$AppSecret = "",
    [string]$DefaultReceiveId = "",
    [int]$WaitSeconds = 360
)

$ErrorActionPreference = "Stop"

$scriptDir = $PSScriptRoot
if (-not $scriptDir) { $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition }
$root     = (Resolve-Path (Join-Path $scriptDir "..")).Path
Set-Location $root

$venvPy  = Join-Path $root ".venv\Scripts\python.exe"
$logsDir = Join-Path $root "logs"
$envFile = Join-Path $root ".env"

function Step($m) { Write-Host "[setup-feishu] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[setup-feishu] $m" -ForegroundColor Green }
function Err($m)  { Write-Host "[setup-feishu] $m" -ForegroundColor Red }

# --- credentials ---
if (-not $AppId)     { $AppId     = Read-Host "Feishu App ID (cli_...)" }
if (-not $AppSecret) {
    $secure = Read-Host "Feishu App Secret" -AsSecureString
    $bstr   = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $AppSecret = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
$AppId           = $AppId.Trim()
$AppSecret       = $AppSecret.Trim()
$DefaultReceiveId = $DefaultReceiveId.Trim()

# --- validate against the official Feishu auth endpoint ---
Step "validating Feishu credentials ..."
$env:ONCALL_SETUP_APP_ID     = $AppId
$env:ONCALL_SETUP_APP_SECRET = $AppSecret
$validateOut = & $venvPy -c @"
import httpx, os, sys
r = httpx.post('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
                json={'app_id': os.environ['ONCALL_SETUP_APP_ID'],
                      'app_secret': os.environ['ONCALL_SETUP_APP_SECRET']},
                timeout=20)
d = r.json()
if d.get('code') == 0 and d.get('tenant_access_token'):
    print('OK')
    sys.exit(0)
print('FAIL', d.get('msg', d), file=sys.stderr)
sys.exit(1)
"@ 2>&1
$env:ONCALL_SETUP_APP_ID     = $null
$env:ONCALL_SETUP_APP_SECRET = $null
if ($LASTEXITCODE -ne 0) {
    Err "credential validation failed."
    Write-Host "  $validateOut"
    Err "open https://open.feishu.cn/app and double-check App ID / App Secret."
    exit 1
}
Ok "credentials valid (tenant_access_token issued)."

# --- update .env (UTF-8, no BOM; preserves other vars) ---
Step "updating .env ..."
if (Test-Path $envFile) {
    $content = [System.IO.File]::ReadAllText($envFile)
} else {
    $content = ""
}

function Set-EnvValue($text, $key, $value) {
    $pattern     = "(?m)^(?:\s*#\s*)?" + [regex]::Escape($key) + "\s*=.*$"
    $replacement = $key + "=" + ($value -replace '\$','$$')
    if ($text -and [regex]::IsMatch($text, $pattern)) {
        return [regex]::Replace($text, $pattern, $replacement)
    }
    $prefix = ""
    if ($text -and -not $text.EndsWith("`n")) { $prefix = "`n" }
    return $text + $prefix + "$key=$value`n"
}

$content = Set-EnvValue $content "ONCALL_FEISHU_ENABLED"            "true"
$content = Set-EnvValue $content "ONCALL_FEISHU_APP_ID"              $AppId
$content = Set-EnvValue $content "ONCALL_FEISHU_APP_SECRET"          $AppSecret
$content = Set-EnvValue $content "ONCALL_FEISHU_DEFAULT_RECEIVE_ID" $DefaultReceiveId
if (-not ($content -match "(?m)^ONCALL_FEISHU_DEFAULT_RECEIVE_ID_TYPE=")) {
    if ($content -and -not $content.EndsWith("`n")) { $content += "`n" }
    $content += "ONCALL_FEISHU_DEFAULT_RECEIVE_ID_TYPE=chat_id`n"
}
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($envFile, $content, $utf8NoBom)
Ok ".env updated."

# --- pre-warm lark_oapi (first run on Windows can take 1-4 min for AV scan) ---
Step "pre-warming lark_oapi ..."
$warmOut = & $venvPy -c "import time; _t=time.time(); import lark_oapi; print('ok in {0:.1f}s'.format(time.time()-_t))" 2>&1
Step "pre-warm: $warmOut"

# --- restart oncall-api and oncall-agent-worker ---
Step "restarting oncall-api and oncall-agent-worker ..."
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) { Err "'uv' not found on PATH. Install uv first: https://docs.astral.sh/uv/"; exit 1 }

Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -match 'oncall-(api|agent-worker)'
} | ForEach-Object {
    # taskkill returns non-zero (NativeCommandError) if the process already
    # exited between the CIM snapshot and the kill. Scope a relaxed
    # ErrorActionPreference so a missing PID does not abort the script.
    $prevPref = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { taskkill /F /T /PID $_.ProcessId 2>$null | Out-Null } finally { $ErrorActionPreference = $prevPref }
}
Start-Sleep -Seconds 2

if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Force -Path $logsDir | Out-Null }
Remove-Item "$logsDir\api.out.log","$logsDir\api.err.log","$logsDir\agent.out.log","$logsDir\agent.err.log" -ErrorAction SilentlyContinue

Start-Process -FilePath $uv.Source -ArgumentList "run","oncall-api" `
    -WorkingDirectory $root `
    -RedirectStandardOutput "$logsDir\api.out.log" `
    -RedirectStandardError  "$logsDir\api.err.log" `
    -NoNewWindow | Out-Null
Start-Process -FilePath $uv.Source -ArgumentList "run","oncall-agent-worker" `
    -WorkingDirectory $root `
    -RedirectStandardOutput "$logsDir\agent.out.log" `
    -RedirectStandardError  "$logsDir\agent.err.log" `
    -NoNewWindow | Out-Null
Ok "processes launched."

# --- wait for WebSocket connect ---
$apiErr = Join-Path $logsDir "api.err.log"
Step "waiting for Feishu WebSocket to connect (up to $WaitSeconds s) ..."
$start   = Get-Date
$end     = $start.AddSeconds($WaitSeconds)
$spinner = @('|','/','-','\')
$i = 0
$connected = $false
$connLine  = ""
while ((Get-Date) -lt $end) {
    if (Test-Path $apiErr) {
        $m = Select-String -Path $apiErr -Pattern 'connected to wss://' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($m) { $connected = $true; $connLine = $m.Line.Trim(); break }
    }
    $elapsed = [int]((Get-Date) - $start).TotalSeconds
    Write-Host -NoNewline ("`r  {0} waiting ... {1}s   " -f $spinner[$i % 4], $elapsed)
    $i++
    Start-Sleep -Seconds 2
}
Write-Host ""
if (-not $connected) {
    Err "timed out after $WaitSeconds s."
    Err "tail of logs\api.err.log:"
    Get-Content $apiErr -Tail 20 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" }
    Err "common causes: app not published, or event subscription not set to long-connection."
    Err "see docs/FEISHU_SETUP.md."
    exit 1
}
Ok "Feishu WebSocket connected."
Write-Host "  $connLine" -ForegroundColor DarkGray

# --- final health ---
try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:9900/api/health" -TimeoutSec 5
    Ok ("API health: ok={0}  database={1}  checkpointer={2}  feishu_ws_error={3}" -f $h.ok,$h.database,$h.checkpointer,$h.feishu_ws_error)
} catch {
    Err "could not query /api/health: $_"
}

# --- next steps ---
Write-Host ""
Write-Host "Feishu bot is live." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
if ($DefaultReceiveId) {
    Write-Host ("  * Proactive push target: {0}" -f $DefaultReceiveId)
    Write-Host "    (auto-bind is bypassed because a fixed target is set.)"
} else {
    Write-Host "  * Proactive push is in auto-bind mode."
    Write-Host "    Open Feishu, find the bot, and send it ONE message (e.g. 'hello')."
    Write-Host "    Subsequent Incident cards will be pushed to that same chat automatically."
}
Write-Host "  * Group chats: @-mention the bot to trigger it."
Write-Host "  * Bot commands: /new (new session), /help."
Write-Host "  * To reconfigure later, re-run this script."
