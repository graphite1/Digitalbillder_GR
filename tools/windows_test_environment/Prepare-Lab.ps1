[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$metadata = Join-Path $root '作業補助/配布サイト/lib/windows-setup.json'
$manifest = Get-Content -LiteralPath $metadata -Raw | ConvertFrom-Json
if ([IO.Path]::GetFileName($manifest.filename) -cne $manifest.filename) { throw 'Invalid setup filename' }
if ([DateTimeOffset]::Parse($manifest.expires_at) -le [DateTimeOffset]::UtcNow) { throw 'Published setup metadata expired; obtain a new verified release.' }
$setup = Join-Path $root ('作業補助/配布検証/' + $manifest.filename)
if ((Get-Item -LiteralPath $setup).Length -ne $manifest.size -or (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash -ine $manifest.sha256) { throw 'Setup integrity check failed' }
$run = Join-Path $root ('作業補助/WindowsTest/runs/' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))
$inputDir = Join-Path $run 'input'
$outputDir = Join-Path $run 'results'
New-Item -ItemType Directory -Path $inputDir,$outputDir | Out-Null
Copy-Item -LiteralPath $setup -Destination $inputDir
Copy-Item -LiteralPath $metadata -Destination (Join-Path $inputDir 'setup.json')
foreach ($file in @('Start-GuestTest.ps1','guest_smoke.py')) { Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination $inputDir }
$safeInput = [Security.SecurityElement]::Escape($inputDir)
$safeOutput = [Security.SecurityElement]::Escape($outputDir)
$config = @"
<Configuration>
  <vGPU>Disable</vGPU>
  <Networking>Enable</Networking>
  <AudioInput>Disable</AudioInput>
  <VideoInput>Disable</VideoInput>
  <PrinterRedirection>Disable</PrinterRedirection>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <MemoryInMB>4096</MemoryInMB>
  <MappedFolders>
    <MappedFolder><HostFolder>$safeInput</HostFolder><SandboxFolder>C:\DBGR-Input</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$safeOutput</HostFolder><SandboxFolder>C:\DBGR-Results</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\DBGR-Input\Start-GuestTest.ps1</Command></LogonCommand>
</Configuration>
"@
[xml]$config | Out-Null
$config | Set-Content -LiteralPath (Join-Path $run '初回導入テスト.wsb') -Encoding UTF8
Write-Output $run
