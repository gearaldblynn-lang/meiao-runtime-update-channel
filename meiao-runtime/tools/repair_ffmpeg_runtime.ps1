param(
  [string]$RuntimeRoot = "",
  [string]$DownloadUrl = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
  $RuntimeRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
if ([string]::IsNullOrWhiteSpace($DownloadUrl)) {
  $DownloadUrl = $env:MEIAO_FFMPEG_DOWNLOAD_URL
}
if ([string]::IsNullOrWhiteSpace($DownloadUrl)) {
  $DownloadUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip"
}

$runtimeRootFull = [System.IO.Path]::GetFullPath($RuntimeRoot)
$runtimeDir = Join-Path $runtimeRootFull "runtime"
$ffmpegExe = Join-Path $runtimeDir "ffmpeg.exe"
$ffprobeExe = Join-Path $runtimeDir "ffprobe.exe"

if ((Test-Path -LiteralPath $ffmpegExe -PathType Leaf) -and (Test-Path -LiteralPath $ffprobeExe -PathType Leaf)) {
  Write-Host "FFmpeg runtime already available."
  exit 0
}

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$workRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("meiao-ffmpeg-repair-{0}" -f ([System.Guid]::NewGuid().ToString("N")))
$extractRoot = Join-Path $workRoot "extract"
$archivePath = Join-Path $workRoot "ffmpeg.zip"

try {
  New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null

  if (Test-Path -LiteralPath $DownloadUrl -PathType Leaf) {
    Copy-Item -LiteralPath $DownloadUrl -Destination $archivePath -Force
  } else {
    Write-Host "Downloading FFmpeg runtime dependency..."
    Invoke-WebRequest -UseBasicParsing -Uri $DownloadUrl -OutFile $archivePath -TimeoutSec 300
  }

  Expand-Archive -LiteralPath $archivePath -DestinationPath $extractRoot -Force
  $ffmpegSource = Get-ChildItem -LiteralPath $extractRoot -Recurse -File -Filter "ffmpeg.exe" | Select-Object -First 1
  $ffprobeSource = Get-ChildItem -LiteralPath $extractRoot -Recurse -File -Filter "ffprobe.exe" | Select-Object -First 1
  if (-not $ffmpegSource -or -not $ffprobeSource) {
    throw "Downloaded FFmpeg archive does not contain ffmpeg.exe and ffprobe.exe."
  }

  $binDir = Split-Path -Parent $ffmpegSource.FullName
  Copy-Item -LiteralPath $ffmpegSource.FullName -Destination $ffmpegExe -Force
  Copy-Item -LiteralPath $ffprobeSource.FullName -Destination $ffprobeExe -Force
  Get-ChildItem -LiteralPath $binDir -File -Filter "*.dll" -ErrorAction SilentlyContinue |
    ForEach-Object {
      Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $runtimeDir $_.Name) -Force
    }

  if (-not (Test-Path -LiteralPath $ffmpegExe -PathType Leaf) -or -not (Test-Path -LiteralPath $ffprobeExe -PathType Leaf)) {
    throw "FFmpeg repair did not produce runtime\ffmpeg.exe and runtime\ffprobe.exe."
  }
  Write-Host "FFmpeg runtime dependency repaired."
} catch {
  throw "FFmpeg runtime dependency is missing and automatic repair failed. Check network access, or publish Git update with -IncludeHeavyDeps. Details: $($_.Exception.Message)"
} finally {
  Remove-Item -LiteralPath $workRoot -Recurse -Force -ErrorAction SilentlyContinue
}
