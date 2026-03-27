
# launch_chrome_debug.ps1
# Launches Chrome with remote debugging enabled so WSL2 can connect via CDP.
#
# Usage: Right-click → "Run with PowerShell"
# Or from PowerShell: .\launch_chrome_debug.ps1
#
# After running, Chrome will listen on http://127.0.0.1:9222
# Then in WSL2: bash ~/ai-archive/tools/gemini_sync.sh

$ChromeExe = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$UserDataDir = "$env:LOCALAPPDATA\Microsoft\Edge\User Data"
$Profile = "Default"          # Change to "IA_Profile" if preferred
$DebugPort = 9222

# Check if Chrome is already running with debugging
$existing = netstat -ano 2>$null | Select-String ":$DebugPort "
if ($existing) {
    Write-Host "Chrome debugging port $DebugPort already open. Reusing existing session." -ForegroundColor Yellow
    exit 0
}

if (-not (Test-Path $ChromeExe)) {
    Write-Host "Chrome not found at: $ChromeExe" -ForegroundColor Red
    Write-Host "Edit this script to set the correct path." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "Launching Chrome with remote debugging on port $DebugPort..." -ForegroundColor Green
Write-Host "Profile: $Profile" -ForegroundColor Cyan
Write-Host ""
Write-Host "Keep this window open. Switch to WSL2 and run:" -ForegroundColor White
Write-Host "  bash ~/ai-archive/tools/gemini_sync.sh" -ForegroundColor Yellow
Write-Host ""

Start-Process -FilePath $ChromeExe -ArgumentList @(
    "--remote-debugging-port=$DebugPort",
    "--user-data-dir=`"$UserDataDir`"",
    "--profile-directory=`"$Profile`"",
    "--no-first-run",
    "--no-default-browser-check"
)
