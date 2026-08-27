$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
Set-Location (Split-Path -Parent $PSScriptRoot)

$python = Get-Command py -ErrorAction SilentlyContinue
if (-not $python) {
    throw "Python Launcher (py.exe) was not found. Install Python 3.13.12 x64."
}

& py -3.13 -c "import sys; assert (3, 13) <= sys.version_info < (3, 14), sys.version"
if ($LASTEXITCODE -ne 0) { throw "Python 3.13.x x64 is required." }
& py -3.13 -m pip install --disable-pip-version-check uv==0.11.33
if ($LASTEXITCODE -ne 0) { throw "Failed to install uv 0.11.33." }
& py -3.13 -m uv sync --locked --all-extras --python 3.13
if ($LASTEXITCODE -ne 0) { throw "Failed to create the environment from uv.lock." }

Write-Host "$(Get-Location)\.venv was created from uv.lock."
Write-Host "Next: powershell -ExecutionPolicy Bypass -File .\scripts\check\_windows.ps1"
