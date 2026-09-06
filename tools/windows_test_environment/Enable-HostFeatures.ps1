[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$ReportRoot = Join-Path $RepoRoot '作業補助/WindowsTest/host'
New-Item -ItemType Directory -Path $ReportRoot -Force | Out-Null
$report = [ordered]@{started_at=(Get-Date).ToString('o'); status='running'; restart_required=$false; features=@(); error=$null}
function Save-Report {
    $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $ReportRoot 'feature-status.json') -Encoding UTF8
}
try {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this script as administrator. No host features were changed.'
    }
    Save-Report
    foreach ($name in @('Containers-DisposableClientVM', 'Microsoft-Hyper-V-All')) {
        $before = Get-WindowsOptionalFeature -Online -FeatureName $name
        $result = $null
        if ($before.State -notin @('Enabled', 'EnablePending')) {
            $result = Enable-WindowsOptionalFeature -Online -FeatureName $name -All -NoRestart -LogPath (Join-Path $ReportRoot ($name + '.log'))
        }
        $after = Get-WindowsOptionalFeature -Online -FeatureName $name
        $pending = ($after.State -eq 'EnablePending') -or ($null -ne $result -and $result.RestartNeeded)
        $report.restart_required = $report.restart_required -or $pending
        $report.features += [ordered]@{name=$name;before=[string]$before.State;after=[string]$after.State;restart_required=[bool]$pending}
        Save-Report
    }
    $report.status = if ($report.restart_required) { 'restart-required' } else { 'enabled' }
} catch {
    $report.status = 'failed'
    $report.error = $_.Exception.Message
} finally {
    $report.finished_at = (Get-Date).ToString('o')
    Save-Report
}
if ($report.status -eq 'failed') { Write-Error $report.error; exit 1 }
Write-Output ($report | ConvertTo-Json -Depth 6)
