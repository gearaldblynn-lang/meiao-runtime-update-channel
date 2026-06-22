param(
  [int]$Port = 9222
)

$ErrorActionPreference = "Stop"

function Get-ChromePath {
  $candidates = @(
    (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe"),
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
  )

  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) {
      return $candidate
    }
  }

  throw "Chrome executable not found."
}

$chrome = Get-ChromePath
$userDataDir = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data"
$profileDir = "Profile 3"

try {
  $existing = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/json/version" -TimeoutSec 2
  Write-Host "CDP already enabled: $($existing.Content)"
  exit 0
} catch {
  # continue
}

if (-not (Test-Path (Join-Path $userDataDir $profileDir))) {
  throw "Chrome profile not found: $profileDir"
}

$args = @(
  "--remote-debugging-port=$Port",
  "--remote-debugging-address=127.0.0.1",
  "--user-data-dir=$userDataDir",
  "--profile-directory=$profileDir",
  "--no-first-run",
  "--no-default-browser-check",
  "--start-maximized"
)

Start-Process -FilePath $chrome -ArgumentList $args
Write-Host "Started Chrome CDP: 127.0.0.1:$Port / $profileDir"
