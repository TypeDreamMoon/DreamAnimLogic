param(
    [string]$Rev = 'HEAD',
    [string]$Gen = 'G:\DCC\scratch\h5\gen',
    [string]$Host_ = 'I:\UnrealProject_Moon\DEV_58\DreamVEdAnimHost',
    [switch]$NoBuild
)
# Generates the C++ of als/*.dal (the revision Rev), swaps it into the sandbox host (restoring a previous swap first), copies DalRuntime into the
# host's Plugins and builds the host's editor target (hidden, below-normal priority).  No editor may be running on the host project.
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
Set-Location $repo
if (Test-Path $Gen) { Remove-Item -Recurse -Force $Gen }
python Unreal\AlsHost\gen_als.py --out $Gen --rev $Rev | Select-Object -Last 3
python Unreal\AlsHost\host_als.py restore --host $Host_
python Unreal\AlsHost\host_als.py apply --gen $Gen --host $Host_
# DalHost.h in the plugin is a copy of the compiler's (the event router must be one object; tests/test_dalruntime.py checks they agree)
Copy-Item (Join-Path $repo 'dal\cpp\DalHost.h') (Join-Path $repo 'Unreal\DalRuntime\Source\DalRuntime\Public\DalHost.h') -Force
robocopy (Join-Path $repo 'Unreal\DalRuntime') (Join-Path $Host_ 'Plugins\DalRuntime') /MIR /XD Binaries Intermediate /NFL /NDL /NJH /NJS /NP | Out-Null
if (-not $NoBuild) { & (Join-Path $PSScriptRoot 'build_host.ps1') -Project (Join-Path $Host_ 'DreamAnimTestHost.uproject') }
