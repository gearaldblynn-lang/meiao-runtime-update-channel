$ErrorActionPreference = "Stop"

try {
  $processEnv = [Environment]::GetEnvironmentVariables("Process")
  if ($processEnv.Contains("Path") -and $processEnv.Contains("PATH")) {
    [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
  }
} catch {
}

$runtimeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$goRuntimePath = Join-Path $runtimeRoot "meiao-runtime.exe"
$pythonPath = Join-Path $runtimeRoot "python\python.exe"
$serverPath = Join-Path $runtimeRoot "server.py"
$runtimePackagePath = Join-Path $runtimeRoot "meiao_runtime"
$ffmpegRepairScript = Join-Path $runtimeRoot "tools\repair_ffmpeg_runtime.ps1"
$outLog = Join-Path $runtimeRoot "runtime-out.log"
$errLog = Join-Path $runtimeRoot "runtime-err.log"
$pidFile = Join-Path $runtimeRoot "runtime.pid"
$startupLog = Join-Path $runtimeRoot "startup-runtime.log"
$healthUrl = "http://127.0.0.1:8787/api/health"
$environmentUrl = "http://127.0.0.1:8787/api/system/environment"
$exportFolderCheckUrl = "http://127.0.0.1:8787/api/system/check-export-folder"
$port = 8787
$capcutMatePort = 30000
$capcutMateRoot = Join-Path $runtimeRoot "integrations\capcut_mate\upstream\capcut-mate-main"
$capcutMateOutLog = Join-Path $runtimeRoot "integrations\capcut_mate\capcut-mate.out.log"
$capcutMateErrLog = Join-Path $runtimeRoot "integrations\capcut_mate\capcut-mate.err.log"
$resolvedRuntimeRoot = [System.IO.Path]::GetFullPath($runtimeRoot).TrimEnd([char]"\").ToLowerInvariant()
$resolvedServerPath = [System.IO.Path]::GetFullPath($serverPath).ToLowerInvariant()

function Write-StartupLog {
  param([string]$Message)
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
  Add-Content -Path $startupLog -Value $line -Encoding UTF8
}

function Repair-FFmpegRuntimeIfNeeded {
  $ffmpegExe = Join-Path $runtimeRoot "runtime\ffmpeg.exe"
  $ffprobeExe = Join-Path $runtimeRoot "runtime\ffprobe.exe"
  if ((Test-Path -LiteralPath $ffmpegExe -PathType Leaf) -and (Test-Path -LiteralPath $ffprobeExe -PathType Leaf)) {
    return
  }
  if (-not (Test-Path -LiteralPath $ffmpegRepairScript -PathType Leaf)) {
    throw "FFmpeg runtime dependency is missing and repair script was not found: $ffmpegRepairScript"
  }
  Write-StartupLog "ffmpeg dependency missing; attempting repair"
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ffmpegRepairScript -RuntimeRoot $runtimeRoot
  if ($LASTEXITCODE -ne 0) {
    throw "FFmpeg runtime dependency repair failed with exit code $LASTEXITCODE."
  }
}

function Normalize-PathForCompare {
  param([string]$Value)
  if ([string]::IsNullOrWhiteSpace($Value)) {
    return ""
  }
  return $Value.ToLowerInvariant().Replace("/", "\")
}

function Get-EnvironmentPayload {
  try {
    $response = Invoke-WebRequest -UseBasicParsing $environmentUrl -TimeoutSec 5
    if ($response.StatusCode -ne 200) {
      return $null
    }
    return $response.Content | ConvertFrom-Json
  } catch {
    return $null
  }
}

function Test-RuntimeHealth {
  try {
    $response = Invoke-WebRequest -UseBasicParsing $healthUrl -TimeoutSec 3
    if ($response.StatusCode -ne 200 -or $response.Content -notlike "*ok*") {
      return $false
    }
    if (-not (Test-RuntimeRouteFreshness)) {
      return $false
    }
    if (-not (Test-RuntimeProcessFreshness)) {
      return $false
    }
    return $true
  } catch {
    return $false
  }
}

function Test-RuntimeRouteFreshness {
  try {
    $body = '{"path":"","projectId":"__startup_check__"}'
    $response = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $exportFolderCheckUrl -ContentType "application/json" -Body $body -TimeoutSec 5
    if ($response.StatusCode -eq 400) {
      return $true
    }
    Write-StartupLog ("runtime route freshness unexpected status={0}" -f $response.StatusCode)
    return $false
  } catch {
    $statusCode = 0
    if ($_.Exception.Response) {
      $statusCode = [int]$_.Exception.Response.StatusCode
    }
    if ($statusCode -eq 400) {
      return $true
    }
    if ($statusCode -eq 404) {
      Write-StartupLog "runtime route freshness failed; check-export-folder route missing"
      return $false
    }
    Write-StartupLog ("runtime route freshness failed status={0} message={1}" -f $statusCode, $_.Exception.Message)
    return $false
  }
}

function Get-ListeningProcessIds {
  param([int]$TargetPort)
  $processIds = @()
  try {
    $lines = & netstat -ano -p tcp 2>$null
    foreach ($line in $lines) {
      $trimmed = ([string]$line).Trim()
      if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed -notmatch "\sLISTENING\s") {
        continue
      }
      $parts = $trimmed -split "\s+"
      if ($parts.Count -lt 5) {
        continue
      }
      $localAddress = [string]$parts[1]
      if (-not $localAddress.EndsWith(":$TargetPort")) {
        continue
      }
      $parsedPid = 0
      if ([int]::TryParse([string]$parts[-1], [ref]$parsedPid) -and $parsedPid -gt 0) {
        $processIds += $parsedPid
      }
    }
    if ($processIds.Count -gt 0) {
      return @($processIds | Select-Object -Unique)
    }
  } catch {
  }
  try {
    $connections = @(Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue)
    return @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
  } catch {
    return @()
  }
}

function Test-RuntimeProcessFreshness {
  $processIds = @(Get-ListeningProcessIds -TargetPort $port)
  if ($processIds.Count -eq 0) {
    Write-StartupLog "runtime freshness failed; no listening process"
    return $false
  }
  $serverWriteTime = (Get-Item $serverPath).LastWriteTime
  if (Test-Path -LiteralPath $runtimePackagePath) {
    $packageNewest = Get-ChildItem -LiteralPath $runtimePackagePath -Recurse -File -Filter "*.py" |
      Sort-Object LastWriteTime -Descending |
      Select-Object -First 1
    if ($packageNewest -and $packageNewest.LastWriteTime -gt $serverWriteTime) {
      $serverWriteTime = $packageNewest.LastWriteTime
    }
  }
  foreach ($processId in $processIds) {
    if (-not (Test-ProcessBelongsToRuntime $processId)) {
      Write-StartupLog ("runtime freshness failed; port owner pid={0} does not belong to this runtime" -f $processId)
      return $false
    }
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if (-not $process) {
      Write-StartupLog ("runtime freshness failed; port owner pid={0} disappeared" -f $processId)
      return $false
    }
    if ($process.StartTime.AddSeconds(2) -lt $serverWriteTime) {
      Write-StartupLog ("runtime freshness failed; pid={0} started={1:o} server={2:o}" -f $processId, $process.StartTime, $serverWriteTime)
      return $false
    }
  }
  return $true
}

function Write-EnvironmentSummary {
  $payload = Get-EnvironmentPayload
  if ($payload) {
    Write-StartupLog ("environment overall={0} dataRoot={1}" -f $payload.overall, $payload.dataRoot)
    if ($payload.overall -ne "ok") {
      Write-Host ("Runtime environment: {0}" -f $payload.overall)
      @($payload.issues | Select-Object -First 5) | ForEach-Object {
        Write-Host ("  - {0}: {1}" -f $_.label, $_.message)
      }
    }
  } else {
    Write-StartupLog "environment check skipped"
  }
}

function Get-ProcessCommandLine {
  param([int]$ProcessId)
  try {
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
    return [string]$processInfo.CommandLine
  } catch {
    return ""
  }
}

function Test-ProcessBelongsToRuntime {
  param([int]$ProcessId)
  $commandLine = Normalize-PathForCompare (Get-ProcessCommandLine $ProcessId)
  if ([string]::IsNullOrWhiteSpace($commandLine)) {
    return $false
  }
  return $commandLine.Contains($resolvedServerPath) -or $commandLine.Contains($resolvedRuntimeRoot)
}

function Stop-RuntimeServerProcesses {
  if (-not (Test-Path $pidFile)) {
    return
  }

  $pidText = (Get-Content -Path $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
  $runtimePid = 0
  if (-not [int]::TryParse($pidText, [ref]$runtimePid)) {
    Remove-Item $pidFile -ErrorAction SilentlyContinue
    return
  }

  $process = Get-Process -Id $runtimePid -ErrorAction SilentlyContinue
  if ($process) {
    if (-not (Test-ProcessBelongsToRuntime $runtimePid)) {
      Write-StartupLog ("pid file points to non-runtime process pid={0}; leaving it untouched" -f $runtimePid)
      Remove-Item $pidFile -ErrorAction SilentlyContinue
      return
    }
    Write-StartupLog ("stopping stale runtime process pid={0}" -f $runtimePid)
    Stop-Process -Id $runtimePid -Force -ErrorAction SilentlyContinue
  }
  Remove-Item $pidFile -ErrorAction SilentlyContinue
}

function Stop-BrokenPortOwner {
  $processIds = @(Get-ListeningProcessIds -TargetPort $port)
  foreach ($processId in $processIds) {
    $ownerProcess = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($ownerProcess -and $ownerProcess.ProcessName -eq "python" -and (Test-ProcessBelongsToRuntime $processId)) {
      Write-StartupLog ("stopping broken port owner pid={0}" -f $processId)
      Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    } else {
      throw "Port $port is occupied by pid $processId, but it is not this runtime process. Close it or change the runtime port."
    }
  }
}

function Test-PortListening {
  param([int]$TargetPort)
  return @(Get-ListeningProcessIds -TargetPort $TargetPort).Count -gt 0
}

function Test-UsablePythonPath {
  param([string]$CandidatePath)
  if ([string]::IsNullOrWhiteSpace($CandidatePath)) {
    return $false
  }
  if ($CandidatePath.ToLowerInvariant().Contains("\windowsapps\")) {
    return $false
  }
  return (Test-Path -LiteralPath $CandidatePath -PathType Leaf)
}

function Resolve-CapCutMatePython {
  $candidates = @()
  if (Test-UsablePythonPath $pythonPath) {
    $candidates += $pythonPath
  }

  $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
  if ($pyLauncher) {
    try {
      # Keep this explicit for support: py -3.11 resolves the real Python executable instead of WindowsApps python.exe.
      $resolved = & $pyLauncher.Source -3.11 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1
      if (Test-UsablePythonPath $resolved) {
        $candidates += [string]$resolved
      }
    } catch {
      Write-StartupLog ("capcut-mate python launcher probe failed: {0}" -f $_.Exception.Message)
    }
  }

  $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if ($pythonCommand -and (Test-UsablePythonPath $pythonCommand.Source)) {
    $candidates += $pythonCommand.Source
  }

  $localPythonRoot = Join-Path $env:LOCALAPPDATA "Programs\Python"
  foreach ($versionDir in @("Python311", "Python312", "Python310")) {
    $candidate = Join-Path $localPythonRoot "$versionDir\python.exe"
    if (Test-UsablePythonPath $candidate) {
      $candidates += $candidate
    }
  }

  foreach ($candidate in @($candidates | Select-Object -Unique)) {
    try {
      & $candidate -c "import sys" 2>$null | Out-Null
      if ($LASTEXITCODE -eq 0 -and (Test-UsablePythonPath $candidate)) {
        return [string]$candidate
      }
    } catch {
      Write-StartupLog ("capcut-mate python probe failed path={0} error={1}" -f $candidate, $_.Exception.Message)
    }
  }
  return ""
}

function Resolve-ConfiguredCapCutPath {
  $configPath = Join-Path $runtimeRoot "config.local.json"
  if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    return ""
  }
  try {
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $values = @()
    if ($config.global_settings -and $config.global_settings.integrations) {
      $values += [string]$config.global_settings.integrations.capcutPath
    }
    if ($config.settings -and $config.settings.integrations) {
      $values += [string]$config.settings.integrations.capcutPath
    }
    foreach ($value in $values) {
      if ([string]::IsNullOrWhiteSpace($value)) {
        continue
      }
      if ([System.IO.Path]::IsPathRooted($value)) {
        return $value
      }
      return (Join-Path $runtimeRoot $value)
    }
  } catch {
    Write-StartupLog ("capcut config path read failed: {0}" -f $_.Exception.Message)
  }
  return ""
}

function Resolve-CapCutExecutable {
  $candidates = @()
  foreach ($envName in @("MEIAO_CAPCUT_PATH", "CAPCUT_PATH")) {
    $value = [Environment]::GetEnvironmentVariable($envName, "Process")
    if (-not $value) {
      $value = [Environment]::GetEnvironmentVariable($envName, "User")
    }
    if (-not $value) {
      $value = [Environment]::GetEnvironmentVariable($envName, "Machine")
    }
    if (-not [string]::IsNullOrWhiteSpace($value)) {
      $candidates += $value
    }
  }
  $configuredCapCutPath = Resolve-ConfiguredCapCutPath
  if (-not [string]::IsNullOrWhiteSpace($configuredCapCutPath)) {
    $candidates += $configuredCapCutPath
  }
  $candidates += @(
    "D:\JianyingPro\5.9.0.11632\JianyingPro.exe",
    "C:\Program Files\JianyingPro\JianyingPro.exe",
    "C:\Program Files (x86)\JianyingPro\JianyingPro.exe"
  )
  foreach ($candidate in @($candidates | Select-Object -Unique)) {
    if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
      return [string]$candidate
    }
  }
  return ""
}

function Start-CapCutMate {
  if (Test-PortListening $capcutMatePort) {
    Write-StartupLog "capcut-mate sidecar already listening"
    return
  }
  if (-not (Test-Path (Join-Path $capcutMateRoot "main.py"))) {
    Write-StartupLog "capcut-mate sidecar missing; skip"
    return
  }
  $capcutMatePython = Resolve-CapCutMatePython
  if ([string]::IsNullOrWhiteSpace($capcutMatePython)) {
    Write-StartupLog "capcut-mate sidecar skipped; usable Python 3.11+ missing or WindowsApps fake python detected"
    return
  }
  Remove-Item $capcutMateOutLog, $capcutMateErrLog -ErrorAction SilentlyContinue
  $preferredDraftPath = "D:\JianyingPro Drafts"
  if (Test-Path $preferredDraftPath) {
    $draftSavePath = $preferredDraftPath
  } else {
    $draftSavePath = Join-Path $env:LOCALAPPDATA "JianyingPro\User Data\Projects\com.lveditor.draft"
  }
  $env:DRAFT_SAVE_PATH = $draftSavePath
  $env:CAPCUT_REQUIRED_VERSION = "5.9"
  $capcutExecutable = Resolve-CapCutExecutable
  if ([string]::IsNullOrWhiteSpace($capcutExecutable)) {
    Remove-Item Env:\CAPCUT_PATH -ErrorAction SilentlyContinue
    Write-StartupLog "capcut-mate CAPCUT_PATH not set; no configured/fixed CapCut executable was found"
  } else {
    $env:CAPCUT_PATH = $capcutExecutable
    Write-StartupLog ("capcut-mate CAPCUT_PATH={0}" -f $capcutExecutable)
  }
  $env:ENABLE_APIKEY = "false"
  $env:DRAFT_URL = "http://127.0.0.1:30000/openapi/capcut-mate/v1/get_draft"
  $env:DOWNLOAD_URL = "http://127.0.0.1:30000"
  $vendorPath = Join-Path $runtimeRoot "vendor"
  $env:MEIAO_CAPCUT_MATE_ROOT = $capcutMateRoot
  $env:MEIAO_RUNTIME_VENDOR = $vendorPath
  $previousPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")
  $pythonPathParts = @($capcutMateRoot)
  if (Test-Path -LiteralPath $vendorPath -PathType Container) {
    $pythonPathParts += $vendorPath
  }
  if (-not [string]::IsNullOrWhiteSpace($previousPythonPath)) {
    $pythonPathParts += $previousPythonPath
  }
  $env:PYTHONPATH = ($pythonPathParts -join [System.IO.Path]::PathSeparator)
  $capcutMateBootstrap = "integrations\capcut_mate\bootstrap.py"
  $process = Start-Process `
    -FilePath $capcutMatePython `
    -ArgumentList @("-u", $capcutMateBootstrap) `
    -WorkingDirectory $runtimeRoot `
    -RedirectStandardOutput $capcutMateOutLog `
    -RedirectStandardError $capcutMateErrLog `
    -PassThru `
    -WindowStyle Hidden
  Write-StartupLog ("started capcut-mate pid={0}" -f $process.Id)
}

Write-StartupLog "startup requested"

if (-not (Test-Path $pythonPath)) {
  throw "Bundled Python is missing: $pythonPath"
}
if (-not (Test-Path $serverPath)) {
  throw "Runtime server is missing: $serverPath"
}

Repair-FFmpegRuntimeIfNeeded

if (Test-RuntimeHealth) {
  Write-StartupLog "existing runtime is healthy; reusing it before capcut-mate sidecar preflight"
  Write-Host "MEIAO runtime is already running."
  Write-Host "Open http://127.0.0.1:8787 in your browser."
  exit 0
}

Start-CapCutMate

if (Test-Path -LiteralPath $goRuntimePath) {
  Write-StartupLog "delegating runtime start to Go after capcut-mate sidecar preflight"
  & $goRuntimePath start --root $runtimeRoot
  exit $LASTEXITCODE
}

if (Test-RuntimeHealth) {
  Write-StartupLog "existing runtime is healthy; reusing it"
  Write-EnvironmentSummary
  Write-Host "MEIAO runtime is already running."
  Write-Host "Open http://127.0.0.1:8787 in your browser."
  exit 0
}

Stop-RuntimeServerProcesses
Start-Sleep -Milliseconds 500
Stop-BrokenPortOwner
Start-Sleep -Milliseconds 500

Remove-Item $outLog, $errLog -ErrorAction SilentlyContinue

$process = Start-Process `
  -FilePath $pythonPath `
  -ArgumentList @("-u", $serverPath) `
  -WorkingDirectory $runtimeRoot `
  -RedirectStandardOutput $outLog `
  -RedirectStandardError $errLog `
  -PassThru `
  -WindowStyle Hidden

Set-Content -Path $pidFile -Value $process.Id -Encoding ASCII
Write-StartupLog ("started runtime pid={0}" -f $process.Id)

$deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Milliseconds 500
  if ($process.HasExited) {
    Write-StartupLog ("runtime exited early pid={0} exitCode={1}" -f $process.Id, $process.ExitCode)
    throw "Runtime exited early. See runtime-err.log."
  }
  if (Test-RuntimeHealth) {
    Write-StartupLog ("health passed pid={0}" -f $process.Id)
    Write-EnvironmentSummary
    Write-Host "MEIAO runtime started."
    Write-Host "Open http://127.0.0.1:8787 in your browser."
    exit 0
  }
}

Write-StartupLog ("health timed out pid={0}" -f $process.Id)
throw "Runtime did not pass health check in 20 seconds. See runtime-err.log and startup-runtime.log."
