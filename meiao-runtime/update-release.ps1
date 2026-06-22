param(
  [Parameter(Mandatory = $true)][string]$PackageZip,
  [Parameter(Mandatory = $true)][string]$TargetRoot,
  [string]$BackupRoot = ""
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$workRoot = Join-Path $scriptRoot ".tmp\release-update-work"
$preserveTopLevel = @("storage", "config.local.json", "logs", "drafts", "media")
$preserveIntegrationChild = "upstream"

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

function Test-IsPreservedTopLevel {
  param([string]$Name)
  return $preserveTopLevel -contains $Name
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

function Backup-ProgramEntries {
  param(
    [string]$SourceRoot,
    [string]$BackupRuntimeRoot
  )
  New-Item -ItemType Directory -Path $BackupRuntimeRoot -Force | Out-Null
  foreach ($child in Get-ChildItem -LiteralPath $SourceRoot -Force) {
    if (Test-IsPreservedTopLevel $child.Name) {
      continue
    }
    if ($child.Name -ieq "integrations" -and $child.PSIsContainer) {
      foreach ($integrationChild in Get-ChildItem -LiteralPath $child.FullName -Force) {
        if ($integrationChild.Name -ieq $preserveIntegrationChild) {
          continue
        }
        Copy-Entry $integrationChild.FullName (Join-Path (Join-Path $BackupRuntimeRoot "integrations") $integrationChild.Name)
      }
      continue
    }
    Copy-Entry $child.FullName (Join-Path $BackupRuntimeRoot $child.Name)
  }
}

function Remove-ProgramEntries {
  param([string]$RuntimeRoot)
  foreach ($child in Get-ChildItem -LiteralPath $RuntimeRoot -Force) {
    if (Test-IsPreservedTopLevel $child.Name) {
      continue
    }
    if ($child.Name -ieq "integrations" -and $child.PSIsContainer) {
      foreach ($integrationChild in Get-ChildItem -LiteralPath $child.FullName -Force) {
        if ($integrationChild.Name -ieq $preserveIntegrationChild) {
          continue
        }
        Assert-UnderRoot $integrationChild.FullName $RuntimeRoot
        Remove-Item -LiteralPath $integrationChild.FullName -Recurse -Force
      }
      continue
    }
    Assert-UnderRoot $child.FullName $RuntimeRoot
    Remove-Item -LiteralPath $child.FullName -Recurse -Force
  }
}

function Copy-PayloadEntries {
  param(
    [string]$PayloadRoot,
    [string]$RuntimeRoot
  )
  foreach ($child in Get-ChildItem -LiteralPath $PayloadRoot -Force) {
    if ($child.Name -ieq "integrations" -and $child.PSIsContainer) {
      $targetIntegrations = Join-Path $RuntimeRoot "integrations"
      if (-not (Test-Path -LiteralPath $targetIntegrations)) {
        New-Item -ItemType Directory -Path $targetIntegrations -Force | Out-Null
      }
      foreach ($integrationChild in Get-ChildItem -LiteralPath $child.FullName -Force) {
        Copy-Entry $integrationChild.FullName (Join-Path $targetIntegrations $integrationChild.Name)
      }
      continue
    }
    Copy-Entry $child.FullName (Join-Path $RuntimeRoot $child.Name)
  }
}

if (-not (Test-Path -LiteralPath $PackageZip)) {
  throw "Package zip not found: $PackageZip"
}
if (-not (Test-Path -LiteralPath $TargetRoot)) {
  throw "Update target not found: $TargetRoot"
}

$packageZipFull = Resolve-FullPath $PackageZip
$targetFull = Resolve-FullPath $TargetRoot
Assert-NotDriveRoot $targetFull
Assert-UnderRoot $workRoot $scriptRoot

if ([string]::IsNullOrWhiteSpace($BackupRoot)) {
  $BackupRoot = Join-Path $scriptRoot (".tmp\release-backups\{0}" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
}
$backupFull = Resolve-FullPath $BackupRoot
Assert-NotDriveRoot $backupFull
if (Test-Path -LiteralPath $backupFull) {
  $existingBackup = @(Get-ChildItem -LiteralPath $backupFull -Force)
  if ($existingBackup.Count -gt 0) {
    throw "Backup root already exists and is not empty: $backupFull"
  }
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

$backupRuntimeRoot = Join-Path $backupFull "meiao-runtime"
Backup-ProgramEntries $targetFull $backupRuntimeRoot
Remove-ProgramEntries $targetFull
Copy-PayloadEntries $payloadRoot $targetFull

Write-Host "Release updated:"
Write-Host "  target: $targetFull"
Write-Host "  backup: $backupFull"
