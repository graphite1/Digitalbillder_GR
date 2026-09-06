[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$IsoPath)
$ErrorActionPreference = 'Stop'
$iso = Get-Item -LiteralPath $IsoPath
if ($iso.PSIsContainer -or $iso.Extension -ine '.iso') { throw 'Specify a Windows 11 ISO file.' }
Import-Module Hyper-V -ErrorAction Stop
$name = 'Digitalbuilder-GR-Test'
if (Get-VM -Name $name -ErrorAction SilentlyContinue) { throw 'Test VM already exists; it was not changed.' }
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$vmRoot = Join-Path $root '作業補助/WindowsTest/HyperV'
$disk = Join-Path $vmRoot 'Digitalbuilder-GR-Test.vhdx'
if (Test-Path $disk) { throw 'Existing disk was not overwritten.' }
$switch = Get-VMSwitch -Name 'Default Switch' -ErrorAction Stop
New-Item -ItemType Directory -Path $vmRoot -Force | Out-Null
New-VM -Name $name -Generation 2 -MemoryStartupBytes 4GB -NewVHDPath $disk -NewVHDSizeBytes 64GB -Path $vmRoot -SwitchName $switch.Name | Out-Null
Set-VMProcessor -VMName $name -Count 2
Set-VM -Name $name -AutomaticStartAction Nothing -AutomaticCheckpointsEnabled $false
Set-VMFirmware -VMName $name -EnableSecureBoot On -SecureBootTemplate MicrosoftWindows
Set-VMKeyProtector -VMName $name -NewLocalKeyProtector
Enable-VMTPM -VMName $name
$dvd = Add-VMDvdDrive -VMName $name -Path $iso.FullName -Passthru
Set-VMFirmware -VMName $name -FirstBootDevice $dvd
Get-VM -Name $name | Select-Object Name,State,Generation
Write-Output 'VM created but not started. Windows installation and licensing are still required.'
