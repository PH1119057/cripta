$ErrorActionPreference = "Stop"
$target = Join-Path (Split-Path -Parent $PSScriptRoot) "release_windows.ps1"
& $target
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
