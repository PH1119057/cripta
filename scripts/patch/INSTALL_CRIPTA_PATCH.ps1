param(
    [Parameter(Mandatory=$true)][string]$Patch,
    [ValidateSet('inspect','precheck','install')][string]$Action = 'precheck',
    [string]$Server = 'robot-admin',
    [string]$RemoteIncoming = '/srv/cripta-share/incoming/patches'
)
$ErrorActionPreference = 'Stop'
$resolved = (Resolve-Path -LiteralPath $Patch).Path
$hash = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToLowerInvariant()
$name = [IO.Path]::GetFileName($resolved)
$remote = "$RemoteIncoming/$name"
ssh $Server "mkdir -p '$RemoteIncoming'"
scp $resolved "${Server}:$remote"
$remoteHash = (ssh $Server "sha256sum '$remote' | cut -d' ' -f1").Trim().ToLowerInvariant()
if ($remoteHash -ne $hash) { throw "SHA256 mismatch: local=$hash remote=$remoteHash" }
ssh $Server "cripta-patch patch $Action '$remote'"
if ($LASTEXITCODE -ne 0) { throw "Server-side patch $Action failed" }

