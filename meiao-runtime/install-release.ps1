param(
  [Parameter(Mandatory = $true)][string]$PackageZip,
  [Parameter(Mandatory = $true)][string]$TargetRoot
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$workRoot = Join-Path $scriptRoot ".tmp\release-install-work"

function Resolve-FullPath {
  param([string]$Path)
  return [System.IO.Path]::GetFullPath($Path)
}

function Assert-UnderRoot {
  param(
    [string]$Path,
    [string]$Base
  )
  $resolvedPath = Resolve-FullPath $Path
  $resolvedBase = (Resolve-FullPath $Base).TrimEnd([char]"\")
  if (-not $resolvedPath.StartsWith($resolvedBase, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to operate outside root: $resolvedPath"
  }
}

function Assert-NotDriveRoot {
  param([string]$Path)
  $resolved = Resolve-FullPath $Path
  $root = [System.IO.Path]::GetPathRoot($resolved).TrimEnd([char]"\")
  if ($resolved.TrimEnd([char]"\") -eq $root) {
    throw "Refusing to use drive root as target: $resolved"
  }
}

if (-not (Test-Path -LiteralPath $PackageZip)) {
  throw "Package zip not found: $PackageZip"
}

$packageZipFull = Resolve-FullPath $PackageZip
$targetFull = Resolve-FullPath $TargetRoot
Assert-NotDriveRoot $targetFull
Assert-UnderRoot $workRoot $scriptRoot

if (Test-Path -LiteralPath $targetFull) {
  $existing = @(Get-ChildItem -LiteralPath $targetFull -Force)
  if ($existing.Count -gt 0) {
    throw "Install target already exists and is not empty: $targetFull"
  }
} else {
  New-Item -ItemType Directory -Path $targetFull -Force | Out-Null
}

if (Test-Path -LiteralPath $workRoot) {
  Assert-UnderRoot $workRoot $scriptRoot
  Remove-Item -LiteralPath $workRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $workRoot | Out-Null

Expand-Archive -LiteralPath $packageZipFull -DestinationPath $workRoot -Force
$payloadRoot = Join-Path $workRoot "meiao-runtime"
if (-not (Test-Path -LiteralPath $payloadRoot)) {
  throw "Package payload missing meiao-runtime root."
}

foreach ($child in Get-ChildItem -LiteralPath $payloadRoot -Force) {
  Copy-Item -LiteralPath $child.FullName -Destination (Join-Path $targetFull $child.Name) -Recurse -Force
}

Write-Host "Release installed:"
Write-Host "  $targetFull"
