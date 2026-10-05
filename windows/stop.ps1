# Stop Token Saver / aiproxy for this project (Windows).
# Mirrors macos/stop.sh — used by wizard Apply & restart and start-windows.bat.
param(
    [switch]$Quiet
)

$ErrorActionPreference = "Continue"
$Res = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path -LiteralPath (Join-Path $Res "..")).Path
$Support = Join-Path $env:LOCALAPPDATA "TokenSaver"
$PidFile = Join-Path $Support "proxy.pid"

function Stop-PidTree([int]$ProcessId) {
    if ($ProcessId -le 0) { return }
    try {
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue
        foreach ($c in $children) {
            Stop-PidTree -ProcessId ([int]$c.ProcessId)
        }
    } catch {}
    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    } catch {}
}

if (Test-Path -LiteralPath $PidFile) {
    $raw = (Get-Content -LiteralPath $PidFile -Raw -ErrorAction SilentlyContinue)
    if ($raw -match '(\d+)') {
        Stop-PidTree -ProcessId ([int]$Matches[1])
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

# Belt-and-suspenders: anything still running from this project's venv / config.
try {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ForEach-Object {
        $cmd = $_.CommandLine
        if (-not $cmd) { return }
        if ($cmd -like "*$Root*" -and $cmd -match 'aiproxy|mitmdump|mitm_entry') {
            try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
} catch {}

# Do NOT stop the tokensaver.local scheduled task / port alias — that stays installed.
if (-not $Quiet) {
    Write-Host "Proxy stopped."
}
exit 0
