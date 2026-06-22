param(
  [ValidateSet("quick", "full")]
  [string]$Mode = "full",
  [string]$RuntimeRoot = "",
  [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $root "tools\backup_runtime_data.py"

$argsList = @($script, "--mode", $Mode)
if ($RuntimeRoot -ne "") {
  $argsList += @("--runtime-root", $RuntimeRoot)
}
if ($OutputRoot -ne "") {
  $argsList += @("--output-root", $OutputRoot)
}

python @argsList
