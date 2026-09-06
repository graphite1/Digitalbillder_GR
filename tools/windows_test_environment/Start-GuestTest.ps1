[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
if ($env:USERNAME -ne 'WDAGUtilityAccount' -or $PSScriptRoot -ne 'C:\DBGR-Input') { throw 'Only run inside the configured Windows Sandbox.' }
$outputDir = 'C:\DBGR-Results'
$report = [ordered]@{status='running';started_at=(Get-Date).ToString('o');error=$null;untested=@('Application UI workflows','Real invoices and credentials','Updates and recovery','OBIC7')}
try {
    $report | ConvertTo-Json -Depth 5 | Set-Content "$outputDir\install-result.json" -Encoding UTF8
    $manifest = Get-Content "$PSScriptRoot\setup.json" -Raw | ConvertFrom-Json
    if ([IO.Path]::GetFileName($manifest.filename) -cne $manifest.filename) { throw 'Invalid filename' }
    $setup = Join-Path $PSScriptRoot $manifest.filename
    if ((Get-Item $setup).Length -ne $manifest.size -or (Get-FileHash $setup -Algorithm SHA256).Hash -ine $manifest.sha256) { throw 'Setup integrity check failed' }
    $installRoot = Join-Path $env:LOCALAPPDATA 'Programs\DBGR-CleanInstall'
    if (Test-Path $installRoot) { throw 'Installation already exists; close Sandbox and start a fresh session.' }
    $shortcuts = Join-Path $env:USERPROFILE 'Desktop\DBGR-Test'
    $process = Start-Process -FilePath $setup -ArgumentList @('/quiet',('/dir:"' + $installRoot + '"'),('/shortcuts:"' + $shortcuts + '"')) -Wait -PassThru -RedirectStandardOutput "$outputDir\setup-stdout.log" -RedirectStandardError "$outputDir\setup-stderr.log"
    $report.installer_exit_code = $process.ExitCode
    if ($process.ExitCode -ne 0) { throw 'Installer failed; inspect setup logs.' }
    $python = Join-Path $installRoot 'runtime\python.exe'
    if (-not (Test-Path $python)) { throw 'Bundled Python missing' }
    & $python -B "$PSScriptRoot\guest_smoke.py" --install-root $installRoot --output "$outputDir\smoke-result.json"
    if ($LASTEXITCODE -ne 0) { throw 'Runtime smoke failed; inspect smoke-result.json' }
    $report.status = 'install-and-runtime-smoke-passed'
} catch { $report.status='failed'; $report.error=$_.Exception.Message }
$report.finished_at=(Get-Date).ToString('o')
$report | ConvertTo-Json -Depth 5 | Set-Content "$outputDir\install-result.json" -Encoding UTF8
if ($report.status -eq 'failed') { exit 1 }
