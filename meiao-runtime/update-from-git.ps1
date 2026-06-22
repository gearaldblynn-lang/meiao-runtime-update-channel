param(
  [string]$RepoUrl = "https://github.com/gearaldblynn-lang/meiao-runtime-update-channel.git",
  [string]$TargetRoot = "",
  [string]$CacheRoot = "",
  [string]$Branch = "main",
  [string]$BackupRoot = "",
  [switch]$Force
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$preserveTopLevel = @("storage", "config.local.json", "logs", "drafts", "media")
$preserveNestedPaths = @("integrations\upstream", "integrations\capcut_mate\upstream")

if ([string]::IsNullOrWhiteSpace($TargetRoot)) {
  $TargetRoot = $scriptRoot
}
if ([string]::IsNullOrWhiteSpace($CacheRoot)) {
  $CacheRoot = Join-Path $env:LOCALAPPDATA "Meiao\update-channel\meiao-runtime-update-channel"
}
if ([string]::IsNullOrWhiteSpace($BackupRoot)) {
  $BackupRoot = Join-Path $env:LOCALAPPDATA ("Meiao\backups\git-update-{0}" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
}

function Resolve-FullPath {
  param([string]$Path)
  return [System.IO.Path]::GetFullPath($Path)
}

function Assert-NotDriveRoot {
  param([string]$Path)
  $resolved = Resolve-FullPath $Path
  $driveRoot = [System.IO.Path]::GetPathRoot($resolved).TrimEnd([char]"\")
  if ($resolved.TrimEnd([char]"\") -eq $driveRoot) {
    throw "Refusing to operate on drive root: $resolved"
  }
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

function Assert-NotUnderRoot {
  param(
    [string]$Path,
    [string]$Base,
    [string]$Message
  )
  $resolvedPath = Resolve-FullPath $Path
  $resolvedBase = (Resolve-FullPath $Base).TrimEnd([char]"\")
  if ($resolvedPath.StartsWith($resolvedBase, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw $Message
  }
}

function Normalize-RelativePath {
  param([string]$RelativePath)
  if ([string]::IsNullOrWhiteSpace($RelativePath)) {
    throw "Manifest contains an empty relative path."
  }
  $normalized = $RelativePath.Replace("/", "\").TrimStart([char]"\")
  if ([System.IO.Path]::IsPathRooted($normalized)) {
    throw "Manifest path must be relative: $RelativePath"
  }
  foreach ($part in $normalized.Split("\")) {
    if ($part -eq "..") {
      throw "Manifest path must not contain '..': $RelativePath"
    }
  }
  return $normalized
}

function Test-IsPreservedRelativePath {
  param([string]$RelativePath)
  $normalized = (Normalize-RelativePath $RelativePath).ToLowerInvariant()
  foreach ($name in $preserveTopLevel) {
    $lower = $name.ToLowerInvariant()
    if ($normalized -eq $lower -or $normalized.StartsWith("$lower\")) {
      return $true
    }
  }
  foreach ($path in $preserveNestedPaths) {
    $lowerPath = $path.ToLowerInvariant()
    if ($normalized -eq $lowerPath -or $normalized.StartsWith("$lowerPath\")) {
      return $true
    }
  }
  return $false
}

function Copy-Entry {
  param(
    [string]$Source,
    [string]$Destination
  )
  $parent = Split-Path -Parent $Destination
  if (-not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
  }
  Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
}

function Remove-Entry {
  param(
    [string]$Path,
    [string]$Root
  )
  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }
  Assert-UnderRoot $Path $Root
  Remove-Item -LiteralPath $Path -Recurse -Force
}

function Get-FileSha256 {
  param([string]$Path)
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-StringSha256 {
  param([string]$Value)
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
  } finally {
    $sha.Dispose()
  }
}

function Invoke-Git {
  param(
    [string[]]$Arguments,
    [string]$WorkingDirectory = ""
  )
  if ([string]::IsNullOrWhiteSpace($WorkingDirectory)) {
    & $gitCommand @Arguments
  } else {
    Push-Location $WorkingDirectory
    try {
      & $gitCommand @Arguments
    } finally {
      Pop-Location
    }
  }
  if ($LASTEXITCODE -ne 0) {
    throw "git $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
  }
}

function Read-JsonFile {
  param([string]$Path)
  $content = [System.IO.File]::ReadAllText($Path, [System.Text.UTF8Encoding]::new($false))
  return ($content | ConvertFrom-Json)
}

function Verify-PayloadManifest {
  param(
    [string]$PayloadRoot,
    [object]$Manifest
  )
  if ($Manifest.channel -ne "git-update") {
    throw "Manifest is not a git-update payload."
  }
  if (-not $Manifest.programEntries -or $Manifest.programEntries.Count -eq 0) {
    throw "Manifest has no programEntries."
  }
  foreach ($entry in $Manifest.programEntries) {
    $relative = Normalize-RelativePath ([string]$entry)
    if (Test-IsPreservedRelativePath $relative) {
      throw "Refusing to update preserved user data path from Git: $relative"
    }
  }

  $files = @($Manifest.files)
  $hashInput = [System.Text.StringBuilder]::new()
  foreach ($file in $files) {
    $relative = Normalize-RelativePath ([string]$file.path)
    if (Test-IsPreservedRelativePath $relative) {
      throw "Payload file targets preserved user data path: $relative"
    }
    $path = Join-Path $PayloadRoot $relative
    Assert-UnderRoot $path $PayloadRoot
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
      throw "Manifest file is missing from payload: $relative"
    }
    $actualHash = Get-FileSha256 $path
    $expectedHash = ([string]$file.sha256).ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
      throw "Payload hash mismatch for $relative"
    }
    [void]$hashInput.Append(([string]$file.path).Replace("\", "/"))
    [void]$hashInput.Append("`t")
    [void]$hashInput.Append($expectedHash)
    [void]$hashInput.Append("`n")
  }
  $actualPayloadHash = Get-StringSha256 $hashInput.ToString()
  if ($actualPayloadHash -ne ([string]$Manifest.payloadHash).ToLowerInvariant()) {
    throw "Payload manifest hash mismatch."
  }
}

function Backup-EntryIfExists {
  param(
    [string]$RuntimeRoot,
    [string]$BackupRuntimeRoot,
    [string]$RelativePath
  )
  $relative = Normalize-RelativePath $RelativePath
  $source = Join-Path $RuntimeRoot $relative
  if (-not (Test-Path -LiteralPath $source)) {
    return
  }
  Assert-UnderRoot $source $RuntimeRoot
  Copy-Entry $source (Join-Path $BackupRuntimeRoot $relative)
}

function Apply-Payload {
  param(
    [string]$PayloadRoot,
    [string]$RuntimeRoot,
    [string]$BackupRuntimeRoot,
    [object]$Manifest
  )
  New-Item -ItemType Directory -Path $BackupRuntimeRoot -Force | Out-Null
  foreach ($entry in $Manifest.programEntries) {
    $relative = Normalize-RelativePath ([string]$entry)
    $source = Join-Path $PayloadRoot $relative
    $target = Join-Path $RuntimeRoot $relative
    Assert-UnderRoot $source $PayloadRoot
    Assert-UnderRoot $target $RuntimeRoot
    if (-not (Test-Path -LiteralPath $source)) {
      throw "Program entry missing from payload: $relative"
    }
    Backup-EntryIfExists $RuntimeRoot $BackupRuntimeRoot $relative
    Remove-Entry $target $RuntimeRoot
    Copy-Entry $source $target
  }
  Backup-EntryIfExists $RuntimeRoot $BackupRuntimeRoot "release-manifest.json"
  Copy-Entry (Join-Path $PayloadRoot "release-manifest.json") (Join-Path $RuntimeRoot "release-manifest.json")
}

$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
  throw "Git is required for updates. Please install Git for Windows, then run this script again."
}
$gitCommand = $git.Source

if (-not (Test-Path -LiteralPath $TargetRoot)) {
  throw "Target runtime not found: $TargetRoot"
}
$targetFull = Resolve-FullPath $TargetRoot
$cacheFull = Resolve-FullPath $CacheRoot
$backupFull = Resolve-FullPath $BackupRoot
Assert-NotDriveRoot $targetFull
Assert-NotDriveRoot $cacheFull
Assert-NotDriveRoot $backupFull
Assert-NotUnderRoot $cacheFull $targetFull "Refusing to put Git update cache inside the runtime directory. Choose a CacheRoot outside TargetRoot."

$cacheParent = Split-Path -Parent $cacheFull
if (-not (Test-Path -LiteralPath $cacheParent)) {
  New-Item -ItemType Directory -Path $cacheParent -Force | Out-Null
}

if (Test-Path -LiteralPath (Join-Path $cacheFull ".git")) {
  Invoke-Git @("fetch", "--prune", "origin") $cacheFull
  Invoke-Git @("checkout", $Branch) $cacheFull
  Invoke-Git @("pull", "--ff-only", "origin", $Branch) $cacheFull
} else {
  if (Test-Path -LiteralPath $cacheFull) {
    $existing = @(Get-ChildItem -LiteralPath $cacheFull -Force)
    if ($existing.Count -gt 0) {
      throw "CacheRoot exists but is not a Git checkout and is not empty: $cacheFull"
    }
  }
  Invoke-Git @("clone", "--branch", $Branch, $RepoUrl, $cacheFull)
}

$payloadRoot = Join-Path $cacheFull "meiao-runtime"
$manifestPath = Join-Path $payloadRoot "release-manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath)) {
  throw "Git update payload missing release-manifest.json: $manifestPath"
}
$manifest = Read-JsonFile $manifestPath
Verify-PayloadManifest $payloadRoot $manifest

$localManifestPath = Join-Path $targetFull "release-manifest.json"
if ((-not $Force) -and (Test-Path -LiteralPath $localManifestPath)) {
  $localManifest = Read-JsonFile $localManifestPath
  if (($localManifest.payloadHash -eq $manifest.payloadHash) -and ($localManifest.version -eq $manifest.version)) {
    Write-Host "Runtime already matches Git update payload:"
    Write-Host "  target: $targetFull"
    Write-Host "  version: $($manifest.version)"
    return
  }
}

if (Test-Path -LiteralPath $backupFull) {
  $existingBackup = @(Get-ChildItem -LiteralPath $backupFull -Force)
  if ($existingBackup.Count -gt 0) {
    throw "Backup root already exists and is not empty: $backupFull"
  }
}
$backupRuntimeRoot = Join-Path $backupFull "meiao-runtime"
Apply-Payload $payloadRoot $targetFull $backupRuntimeRoot $manifest

Write-Host "Git runtime update applied:"
Write-Host "  target: $targetFull"
Write-Host "  cache:  $cacheFull"
Write-Host "  backup: $backupFull"
Write-Host "  version: $($manifest.version)"
