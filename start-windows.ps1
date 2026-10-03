# Interactive installer / launcher for aiproxy on Windows.
# Run via start-windows.bat (double-click) or: powershell -ExecutionPolicy Bypass -File .\start-windows.ps1

$ErrorActionPreference = "Stop"
# Resolve this script's directory (never hardcode a username/home path).
$Root = (Resolve-Path -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
Set-Location $Root

function Read-Choice([string]$Prompt, [string]$Default) {
    $v = Read-Host $Prompt
    if ([string]::IsNullOrWhiteSpace($v)) { return $Default }
    return $v.Trim()
}

Clear-Host
Write-Host "========================================"
Write-Host "  aiproxy — install & start (Windows)"
Write-Host "========================================"
Write-Host "Project: $Root"
Write-Host ""

# --- Python / venv ---
function Invoke-BootPython {
    param([Parameter(ValueFromRemainingArguments = $true)]$Rest)
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 @Rest
        return
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        & python @Rest
        return
    }
    throw "Python 3 not found"
}

function Test-VenvPython([string]$PyPath) {
    if (-not (Test-Path -LiteralPath $PyPath)) { return $false }
    try {
        & $PyPath -c "import sys" | Out-Null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

try {
    Invoke-BootPython --version | Out-Null
} catch {
    Write-Host "ERROR: Python 3 not found. Install from https://www.python.org/downloads/ (check 'Add to PATH') then re-run."
    Read-Host "Press Enter to close"
    exit 1
}

$venvDir = Join-Path $Root ".venv"
$venvPy = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-VenvPython $venvPy)) {
    if (Test-Path -LiteralPath $venvDir) {
        Write-Host "Existing .venv is missing or broken — recreating …"
        Remove-Item -Recurse -Force $venvDir
    } else {
        Write-Host "Creating virtualenv …"
    }
    Invoke-BootPython -m venv $venvDir
    $venvPy = Join-Path $venvDir "Scripts\python.exe"
}

if (-not (Test-VenvPython $venvPy)) {
    Write-Host "ERROR: Could not create a working venv at:"
    Write-Host "  $venvPy"
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host "Installing / updating dependencies …"
& $venvPy -m pip install --upgrade pip | Out-Null
& $venvPy -m pip install -r (Join-Path $Root "requirements.txt")
& $venvPy -m pip install -e $Root | Out-Null
Write-Host "Deps OK."
Write-Host ""

# --- Questions ---
Write-Host "Which app are you connecting?"
Write-Host "  1) Cursor  (default models — MITM HTTPS proxy)"
Write-Host "  2) Claude Code  (ANTHROPIC_BASE_URL — no CA needed)"
Write-Host "  3) Both  (MITM — Cursor + Claude via HTTPS_PROXY)"
Write-Host ""
$app = Read-Choice "Choice [1/2/3] (default 1)" "1"

$dry = Read-Choice "Dry-run first? (log savings, do not rewrite bodies) [Y/n]" "Y"
$dryFlag = @()
if ($dry -notmatch '^[nN]') {
    $dryFlag = @("--dry-run")
    Write-Host "→ dry-run ON"
} else {
    Write-Host "→ dry-run OFF (will strip when rules match)"
}

$proxyUrl = "http://127.0.0.1:8080"
$dash = "http://127.0.0.1:8081/"
$ca = Join-Path $env:USERPROFILE ".mitmproxy\mitmproxy-ca-cert.pem"
$cer = Join-Path $env:USERPROFILE ".mitmproxy\mitmproxy-ca-cert.cer"

$mode = "mitm"
switch ($app) {
    "2" { $mode = "anthropic" }
    "3" { $mode = "mitm" }
    default { $mode = "mitm"; $app = "1" }
}

Write-Host ""
Write-Host "----------------------------------------"
if ($mode -eq "anthropic") {
    Write-Host "Mode: anthropic reverse proxy"
    Write-Host "Start Claude in another terminal with:"
    Write-Host "  `$env:ANTHROPIC_BASE_URL = `"$proxyUrl`""
    Write-Host "  claude"
    Write-Host "  /status   # confirm base URL"
    Write-Host ""
    $writeClaude = Read-Choice "Write ANTHROPIC_BASE_URL into ~/.claude/settings.json? [y/N]" "N"
    if ($writeClaude -match '^[yY]') {
        $claudeDir = Join-Path $env:USERPROFILE ".claude"
        $settings = Join-Path $claudeDir "settings.json"
        New-Item -ItemType Directory -Force -Path $claudeDir | Out-Null
        if (Test-Path $settings) {
            Write-Host "Existing $settings — leave it unchanged and set the env var manually,"
            Write-Host "or merge ANTHROPIC_BASE_URL under env yourself."
        } else {
            @{
                env = @{
                    ANTHROPIC_BASE_URL = $proxyUrl
                }
            } | ConvertTo-Json -Depth 5 | Set-Content -Path $settings -Encoding UTF8
            Write-Host "Wrote $settings"
        }
    }
} else {
    Write-Host "Mode: mitm (Cursor default models)"
    Write-Host "Dashboard: $dash"
    Write-Host ""
    Write-Host "Cursor settings.json needs:"
    Write-Host @"
  {
    "http.proxy": "$proxyUrl",
    "http.proxySupport": "override",
    "http.proxyStrictSSL": false,
    "cursor.general.disableHttp2": true
  }
"@
    Write-Host ""
    Write-Host "Open: Ctrl+Shift+P → Open User Settings (JSON)"
    Write-Host "File: $env:APPDATA\Cursor\User\settings.json"
    Write-Host ""

    if (-not (Test-Path $ca)) {
        Write-Host "Generating mitmproxy CA …"
        & $venvPy -c @"
from pathlib import Path
from mitmproxy.certs import CertStore
p = Path.home() / '.mitmproxy'
p.mkdir(parents=True, exist_ok=True)
CertStore.from_store(str(p), 'mitmproxy', 2048)
print('CA ready')
"@
    }

    $trust = Read-Choice "Open mitmproxy CA for install into Trusted Root? [y/N]" "N"
    if ($trust -match '^[yY]') {
        $openPath = if (Test-Path $cer) { $cer } else { $ca }
        if (Test-Path $openPath) {
            Start-Process $openPath
            Write-Host "In the cert dialog: Install Certificate → Local Machine → Trusted Root Certification Authorities"
        } else {
            Write-Host "CA not found at $openPath — start proxy once, then re-run."
        }
    }

    Write-Host ""
    Write-Host "After proxy starts: fully quit Cursor, then relaunch from this PowerShell with:"
    Write-Host "  `$env:NODE_EXTRA_CA_CERTS = `"$ca`""
    Write-Host "  Start-Process `"`$env:LOCALAPPDATA\Programs\cursor\Cursor.exe`""
    if ($app -eq "3") {
        Write-Host ""
        Write-Host "For Claude Code in another terminal:"
        Write-Host "  `$env:HTTPS_PROXY = `"$proxyUrl`""
        Write-Host "  `$env:HTTP_PROXY = `"$proxyUrl`""
        Write-Host "  `$env:NODE_EXTRA_CA_CERTS = `"$ca`""
        Write-Host "  claude"
    }
}

Write-Host "----------------------------------------"
Write-Host "Starting aiproxy (mode=$mode) …"
Write-Host "Dashboard: $dash"
Write-Host "Ctrl+C to stop."
Write-Host "========================================"
Write-Host ""

$args = @("-m", "aiproxy", "--mode", $mode) + $dryFlag
& $venvPy @args
Read-Host "Proxy stopped. Press Enter to close"
