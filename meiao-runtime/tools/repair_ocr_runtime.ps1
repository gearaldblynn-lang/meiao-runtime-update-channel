param(
  [string]$RuntimeRoot = "",
  [string]$BundleUrl = "",
  [switch]$UsePipFallback
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
  $RuntimeRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
if ([string]::IsNullOrWhiteSpace($BundleUrl)) {
  $BundleUrl = $env:MEIAO_OCR_BUNDLE_URL
}

$runtimeRootFull = [System.IO.Path]::GetFullPath($RuntimeRoot)
$vendorDir = Join-Path $runtimeRootFull "vendor"
$rapidOcrDir = Join-Path $vendorDir "rapidocr_onnxruntime"
$onnxRuntimeDir = Join-Path $vendorDir "onnxruntime"

if ((Test-Path -LiteralPath $rapidOcrDir -PathType Container) -and (Test-Path -LiteralPath $onnxRuntimeDir -PathType Container)) {
  Write-Host "OCR runtime already available."
  exit 0
}

New-Item -ItemType Directory -Path $vendorDir -Force | Out-Null

function Install-OcrBundle {
  param([string]$Source)

  $workRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("meiao-ocr-repair-{0}" -f ([System.Guid]::NewGuid().ToString("N")))
  $extractRoot = Join-Path $workRoot "extract"
  $archivePath = Join-Path $workRoot "ocr-bundle.zip"
  try {
    New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null
    if (Test-Path -LiteralPath $Source -PathType Leaf) {
      Copy-Item -LiteralPath $Source -Destination $archivePath -Force
    } else {
      Write-Host "Downloading OCR runtime dependency bundle..."
      Invoke-WebRequest -UseBasicParsing -Uri $Source -OutFile $archivePath -TimeoutSec 600
    }
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractRoot -Force
    $bundleRoot = $extractRoot
    $nestedVendor = Get-ChildItem -LiteralPath $extractRoot -Directory -Recurse -ErrorAction SilentlyContinue |
      Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "rapidocr_onnxruntime") -PathType Container } |
      Select-Object -First 1
    if ($nestedVendor) {
      $bundleRoot = $nestedVendor.FullName
    }
    Copy-Item -Path (Join-Path $bundleRoot "*") -Destination $vendorDir -Recurse -Force
  } finally {
    Remove-Item -LiteralPath $workRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
}

function Find-PipPython {
  $candidates = @()
  $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if ($pythonCommand) {
    $candidates += $pythonCommand.Source
  }
  $localPythonRoot = Join-Path $env:LOCALAPPDATA "Programs\Python"
  foreach ($versionDir in @("Python312", "Python311", "Python310")) {
    $candidate = Join-Path $localPythonRoot "$versionDir\python.exe"
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
      $candidates += $candidate
    }
  }
  foreach ($candidate in @($candidates | Select-Object -Unique)) {
    if ([string]::IsNullOrWhiteSpace($candidate) -or $candidate.ToLowerInvariant().Contains("\windowsapps\")) {
      continue
    }
    try {
      & $candidate -m pip --version *> $null
      if ($LASTEXITCODE -eq 0) {
        return $candidate
      }
    } catch {
    }
  }
  return ""
}

try {
  if (-not [string]::IsNullOrWhiteSpace($BundleUrl)) {
    Install-OcrBundle -Source $BundleUrl
  } elseif ($UsePipFallback) {
    $pipPython = Find-PipPython
    if ([string]::IsNullOrWhiteSpace($pipPython)) {
      throw "No pip-capable Python was found and MEIAO_OCR_BUNDLE_URL is not configured."
    }
    Write-Host "Installing OCR runtime dependency with local pip-capable Python..."
    & $pipPython -m pip install --target $vendorDir rapidocr-onnxruntime --no-warn-script-location
    if ($LASTEXITCODE -ne 0) {
      throw "pip install rapidocr-onnxruntime failed with exit code $LASTEXITCODE."
    }
  } else {
    throw "MEIAO_OCR_BUNDLE_URL is not configured."
  }

  if (-not (Test-Path -LiteralPath $rapidOcrDir -PathType Container) -or -not (Test-Path -LiteralPath $onnxRuntimeDir -PathType Container)) {
    throw "OCR repair did not produce rapidocr_onnxruntime and onnxruntime under vendor."
  }
  Write-Host "OCR runtime dependency repaired."
} catch {
  throw "OCR runtime dependency is missing and automatic repair failed. Configure MEIAO_OCR_BUNDLE_URL to a prepared OCR vendor bundle or install with a pip-capable Python. Details: $($_.Exception.Message)"
}
