param(
    [ValidateSet('readiness', 'plan', 'apply')]
    [string]$Command = 'readiness',
    [ValidateSet('normal', 'research_8', 'research_12', 'research_20')]
    [string]$Profile = 'normal',
    [string]$ConfirmPlanId = ''
)

$remoteScript = '/srv/cripta/operations/server_resources/servercore_resource_control.py'
$arguments = @('robot-admin', 'sudo', '/usr/bin/python3', $remoteScript, $Command)
if ($Command -in @('plan', 'apply')) {
    $arguments += @('--profile', $Profile)
}
if ($Command -eq 'apply') {
    if ([string]::IsNullOrWhiteSpace($ConfirmPlanId)) {
        throw 'Для apply требуется -ConfirmPlanId из свежего плана.'
    }
    $arguments += @('--confirm-plan-id', $ConfirmPlanId)
}

& ssh @arguments
exit $LASTEXITCODE
