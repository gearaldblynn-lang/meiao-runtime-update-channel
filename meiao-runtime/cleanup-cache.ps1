$ErrorActionPreference = "SilentlyContinue"

$runtimeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$storageRoot = Join-Path $runtimeRoot "storage"

$relativeTargets = @(
  "logs",
  "auth-profiles\*\BrowserMetrics",
  "auth-profiles\*\BrowserMetrics-spare.pma",
  "auth-profiles\*\Crashpad",
  "auth-profiles\*\component_crx_cache",
  "auth-profiles\*\Default\Cache",
  "auth-profiles\*\Default\Code Cache",
  "auth-profiles\*\Default\DawnGraphiteCache",
  "auth-profiles\*\Default\DawnWebGPUCache",
  "auth-profiles\*\Default\GPUCache",
  "auth-profiles\*\Default\Service Worker\CacheStorage",
  "auth-profiles\*\Default\Service Worker\ScriptCache",
  "auth-profiles\*\Default\ShaderCache",
  "auth-profiles\*\Default\Storage\ext",
  "auth-profiles\*\DawnGraphiteCache",
  "auth-profiles\*\DawnWebGPUCache",
  "auth-profiles\*\GrShaderCache",
  "auth-profiles\*\GraphiteDawnCache",
  "auth-profiles\*\ShaderCache",
  "flow-chrome\*\BrowserMetrics",
  "flow-chrome\*\BrowserMetrics-spare.pma",
  "flow-chrome\*\Crashpad",
  "flow-chrome\*\component_crx_cache",
  "flow-chrome\*\extensions_crx_cache",
  "flow-chrome\*\OptGuideOnDeviceModel",
  "flow-chrome\*\optimization_guide_model_store",
  "flow-chrome\*\Default\Cache",
  "flow-chrome\*\Default\Code Cache",
  "flow-chrome\*\Default\DawnGraphiteCache",
  "flow-chrome\*\Default\DawnWebGPUCache",
  "flow-chrome\*\Default\GPUCache",
  "flow-chrome\*\Default\Service Worker\CacheStorage",
  "flow-chrome\*\Default\Service Worker\ScriptCache",
  "flow-chrome\*\Default\ShaderCache",
  "flow-chrome\*\DawnGraphiteCache",
  "flow-chrome\*\DawnWebGPUCache",
  "flow-chrome\*\GrShaderCache",
  "flow-chrome\*\GraphiteDawnCache",
  "flow-chrome\*\ShaderCache",
  "tmp"
)

foreach ($relativeTarget in $relativeTargets) {
  $pattern = Join-Path $storageRoot $relativeTarget
  Get-ChildItem -Path $pattern -Force | ForEach-Object {
    Remove-Item -LiteralPath $_.FullName -Recurse -Force
  }
}

Get-ChildItem -Path $runtimeRoot -Force -Directory -Filter "__pycache__" | ForEach-Object {
  Remove-Item -LiteralPath $_.FullName -Recurse -Force
}
