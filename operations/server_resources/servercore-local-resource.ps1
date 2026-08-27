param(
    [ValidateSet('Inspect', 'Resize')]
    [string]$Action = 'Inspect',
    [int]$Vcpus = 8,
    [int]$RamMb = 8192
)

$ErrorActionPreference = 'Stop'
$passwordFile = 'C:\Users\alex\.config\servercore\robot-password.dpapi'
$authUrl = 'https://cloud.api.selcloud.ru/identity/v3'
$accountDomain = '602367'
$projectId = '2f252a6d86b745d9bc886b4af8b7e7fa'
$region = 'kz-1'
$username = 'robot'

function Get-PlainPassword {
    $encrypted = (Get-Content -Raw -LiteralPath $passwordFile).Trim()
    $secure = $encrypted | ConvertTo-SecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Wait-ServerStatus([string]$ComputeUrl, [hashtable]$Headers, [string]$ServerId, [string[]]$Expected, [int]$TimeoutSeconds = 600) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $server = (Invoke-RestMethod -Method Get -Uri "$ComputeUrl/servers/$ServerId" -Headers $Headers).server
        if ($Expected -contains [string]$server.status) { return $server }
        if ([string]$server.status -eq 'ERROR') { throw "Server entered ERROR state" }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for server status: $($Expected -join ',')"
}

$password = Get-PlainPassword
try {
    $body = @{
        auth = @{
            identity = @{
                methods = @('password')
                password = @{
                    user = @{
                        name = $username
                        domain = @{ name = $accountDomain }
                        password = $password
                    }
                }
            }
            scope = @{ project = @{ id = $projectId } }
        }
    } | ConvertTo-Json -Depth 12
    $requestParams = @{
        Method = 'Post'
        Uri = "$authUrl/auth/tokens"
        ContentType = 'application/json'
        Body = $body
    }
    if ((Get-Command Invoke-WebRequest).Parameters.ContainsKey('UseBasicParsing')) {
        $requestParams.UseBasicParsing = $true
    }
    $response = Invoke-WebRequest @requestParams
}
finally {
    $password = $null
    $body = $null
}

$token = [string]($response.Headers['X-Subject-Token'] | Select-Object -First 1)
if (-not $token) { throw 'Authentication succeeded without X-Subject-Token' }
$catalog = ($response.Content | ConvertFrom-Json).token.catalog
$computeService = $catalog | Where-Object type -eq 'compute' | Select-Object -First 1
$computeUrl = ($computeService.endpoints | Where-Object { $_.interface -eq 'public' -and $_.region -eq $region } | Select-Object -First 1).url.TrimEnd('/')
if (-not $computeUrl) { throw "Compute endpoint for $region not found" }
$headers = @{ 'X-Auth-Token' = $token; 'OpenStack-API-Version' = 'compute 2.91' }

$servers = (Invoke-RestMethod -Method Get -Uri "$computeUrl/servers/detail" -Headers $headers).servers
$server = $servers | Where-Object name -eq 'robot' | Select-Object -First 1
if (-not $server) { throw 'Server robot not found' }
$flavors = (Invoke-RestMethod -Method Get -Uri "$computeUrl/flavors/detail?is_public=None" -Headers $headers).flavors
$verifiedBaseFlavorId = '0daaa6b9-b6f4-4137-8bca-9dd560dd1061'
if ($Vcpus -eq 1 -and $RamMb -eq 2048) {
    $target = $flavors | Where-Object id -eq $verifiedBaseFlavorId | Select-Object -First 1
} else {
    $target = $flavors |
        Where-Object { [int]$_.vcpus -eq $Vcpus -and [int]$_.ram -ge $RamMb -and [int]$_.ram -le ($RamMb + 1024) } |
        Sort-Object { [math]::Abs([int]$_.ram - $RamMb) } |
        Select-Object -First 1
}

$currentFlavor = if ($server.flavor.id) {
    $flavors | Where-Object id -eq ([string]$server.flavor.id) | Select-Object -First 1
} else {
    $flavors | Where-Object name -eq ([string]$server.flavor.original_name) | Select-Object -First 1
}
$currentFlavorId = if ($currentFlavor) { [string]$currentFlavor.id } else { [string]$server.flavor.original_name }
$inspection = [ordered]@{
    server_id = [string]$server.id
    status = [string]$server.status
    current_flavor_id = $currentFlavorId
    current_vcpus = if ($currentFlavor) { [int]$currentFlavor.vcpus } else { $null }
    current_ram_mb = if ($currentFlavor) { [int]$currentFlavor.ram } else { $null }
    target_flavor_id = if ($target) { [string]$target.id } else { $null }
    target_vcpus = if ($target) { [int]$target.vcpus } else { $null }
    target_ram_mb = if ($target) { [int]$target.ram } else { $null }
}
$inspection | ConvertTo-Json
if ($Action -eq 'Inspect') { exit 0 }
if (-not $target) { throw "No accessible flavor with $Vcpus vCPU and $RamMb MB RAM" }
if ($currentFlavorId -eq [string]$target.id -and [string]$server.status -eq 'ACTIVE') {
    Write-Output 'ALREADY_TARGET_ACTIVE'
    exit 0
}

if ([string]$server.status -ne 'SHUTOFF') {
    Invoke-RestMethod -Method Post -Uri "$computeUrl/servers/$($server.id)/action" -Headers $headers -ContentType 'application/json' -Body '{"os-stop":null}' | Out-Null
    $server = Wait-ServerStatus $computeUrl $headers $server.id @('SHUTOFF')
}
if ([string]$server.flavor.id -ne [string]$target.id) {
    $resizeBody = @{ resize = @{ flavorRef = [string]$target.id } } | ConvertTo-Json -Depth 4
    Invoke-RestMethod -Method Post -Uri "$computeUrl/servers/$($server.id)/action" -Headers $headers -ContentType 'application/json' -Body $resizeBody | Out-Null
    $server = Wait-ServerStatus $computeUrl $headers $server.id @('VERIFY_RESIZE','SHUTOFF')
    if ([string]$server.status -eq 'VERIFY_RESIZE') {
        Invoke-RestMethod -Method Post -Uri "$computeUrl/servers/$($server.id)/action" -Headers $headers -ContentType 'application/json' -Body '{"confirmResize":null}' | Out-Null
        $server = Wait-ServerStatus $computeUrl $headers $server.id @('SHUTOFF','ACTIVE')
    }
}
if ([string]$server.status -ne 'ACTIVE') {
    Invoke-RestMethod -Method Post -Uri "$computeUrl/servers/$($server.id)/action" -Headers $headers -ContentType 'application/json' -Body '{"os-start":null}' | Out-Null
    $server = Wait-ServerStatus $computeUrl $headers $server.id @('ACTIVE')
}
Write-Output "RESIZE_COMPLETE status=$($server.status) flavor=$($server.flavor.id)"
