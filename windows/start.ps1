# Start aiproxy in the background (Windows). Mirrors macos/start.sh.
# - Does NOT kill a healthy existing proxy
# - Reads .aiproxy_session.env / .aiproxy_runtime.env
# - Opens dashboard (?setup=1 unless SKIP_SETUP=1)
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Res = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path -LiteralPath (Join-Path $Res "..")).Path
$Support = Join-Path $env:LOCALAPPDATA "TokenSaver"
$LogDir = Join-Path $Support "logs"
$PidFile = Join-Path $Support "proxy.pid"
New-Item -ItemType Directory -Force -Path $Support, $LogDir | Out-Null

if (-not (Test-Path -LiteralPath (Join-Path $Root "config.yaml"))) {
    Write-Error "config.yaml not found at $Root"
    exit 1
}

Set-Location $Root

$DirectUrl = "http://127.0.0.1:8081/"
$SetupUrl = "http://127.0.0.1:8081/?setup=1"

function Test-Dashboard {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8081/api/stats" -UseBasicParsing -TimeoutSec 1
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Open-Dashboard([string]$Url) {
    if ($NoBrowser) { return }
    try { Start-Process $Url } catch {}
}

# Healthy already — focus dashboard, do not restart.
if (Test-Dashboard) {
    Open-Dashboard $DirectUrl
    Write-Host "Proxy already running — opened dashboard."
    exit 0
}

$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPy)) {
    Write-Error "Python venv missing. Run start-windows.bat once to install dependencies."
    exit 1
}

# Stale PID only if dashboard is down.
if (Test-Path -LiteralPath $PidFile) {
    & (Join-Path $Res "stop.ps1") -Quiet
}

$App = "1"
$Mode = "mitm"
$DryRun = "1"
$SkipSetup = "0"
$PrefFile = Join-Path $Root ".aiproxy_runtime.env"
$SessionFile = Join-Path $Root ".aiproxy_session.env"
$LoadFile = $null
if (Test-Path -LiteralPath $SessionFile) { $LoadFile = $SessionFile }
elseif (Test-Path -LiteralPath $PrefFile) { $LoadFile = $PrefFile }

function Read-EnvFile([string]$Path) {
    $map = @{}
    Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $i = $line.IndexOf("=")
        if ($i -lt 1) { return }
        $map[$line.Substring(0, $i)] = $line.Substring($i + 1)
    }
    return $map
}

if ($LoadFile) {
    $envMap = Read-EnvFile $LoadFile
    if ($envMap.ContainsKey("APP")) { $App = $envMap["APP"] }
    if ($envMap.ContainsKey("MODE")) { $Mode = $envMap["MODE"] }
    if ($envMap.ContainsKey("DRY_RUN")) { $DryRun = $envMap["DRY_RUN"] }
    if ($envMap.ContainsKey("SKIP_SETUP")) { $SkipSetup = $envMap["SKIP_SETUP"] }
}
# Prefer persistent quick-start flag for skipping the setup modal.
if (Test-Path -LiteralPath $PrefFile) {
    $prefMap = Read-EnvFile $PrefFile
    if ($prefMap.ContainsKey("SKIP_SETUP")) { $SkipSetup = $prefMap["SKIP_SETUP"] }
}

switch ($App) {
    "2" { $Mode = "anthropic" }
    "3" { $Mode = "mitm" }
}
if ($DryRun -notin @("0", "1")) { $DryRun = "1" }
if ($SkipSetup -notin @("0", "1")) { $SkipSetup = "0" }
if ($SkipSetup -eq "1" -and -not (Test-Path -LiteralPath $PrefFile)) { $SkipSetup = "0" }

$env:AIPROXY_CONFIG = (Join-Path $Root "config.yaml")
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$Root;$env:PYTHONPATH" } else { $Root }

$Log = Join-Path $LogDir "proxy.log"
$errLog = Join-Path $LogDir "proxy.err.log"
$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
Add-Content -LiteralPath (Join-Path $LogDir "starts.log") -Value "---- $stamp start mode=$Mode dry_run=$DryRun root=$Root ----"

$argList = @("-m", "aiproxy", "--mode", $Mode, "-c", (Join-Path $Root "config.yaml"))
if ($DryRun -eq "1") { $argList += "--dry-run" }

$proc = Start-Process -FilePath $VenvPy -ArgumentList $argList `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $Log `
    -RedirectStandardError $errLog `
    -PassThru

$proc.Id | Set-Content -LiteralPath $PidFile -Encoding ASCII
Set-Content -LiteralPath (Join-Path $Support "project_root") -Value $Root -Encoding UTF8
Set-Content -LiteralPath (Join-Path $Support "last_pid") -Value $proc.Id -Encoding ASCII

$ok = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Milliseconds 400
    if ($proc.HasExited) { break }
    if (Test-Dashboard) { $ok = $true; break }
}

if (-not $ok) {
    Write-Error "Token Saver started but dashboard is not responding on :8081. See log: $Log"
    exit 1
}

if ($SkipSetup -eq "1") {
    Open-Dashboard $DirectUrl
} else {
    Open-Dashboard $SetupUrl
}

Write-Host "Proxy running (pid $($proc.Id)). Dashboard: $DirectUrl"
exit 0
