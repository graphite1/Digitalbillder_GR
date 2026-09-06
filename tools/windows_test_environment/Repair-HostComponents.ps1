[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$logs=Join-Path $root '作業補助/WindowsTest/host'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator required.' }
$report=[ordered]@{status='running';started_at=(Get-Date).ToString('o')}
$report | ConvertTo-Json | Set-Content (Join-Path $logs 'repair-status.json') -Encoding UTF8
& "$env:WINDIR\System32\dism.exe" /Online /Cleanup-Image /RestoreHealth /NoRestart *> (Join-Path $logs 'repair-output.log')
$report.exit_code=$LASTEXITCODE
$report.status=if ($LASTEXITCODE -in @(0,3010)) {'repaired'} else {'failed'}
$report.restart_required=($LASTEXITCODE -eq 3010)
$report.finished_at=(Get-Date).ToString('o')
$report | ConvertTo-Json | Set-Content (Join-Path $logs 'repair-status.json') -Encoding UTF8
if ($report.status -eq 'failed') { exit 1 }
if (-not $report.restart_required) { & (Join-Path $PSScriptRoot 'Enable-HostFeatures.ps1') }
