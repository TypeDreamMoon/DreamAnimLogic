param([string]$Log = 'G:\DCC\scratch\w4_logs\build.log')
# Builds the DalLiveHost editor target (UBT, installed UE 5.8). The user-level UE cache variables are imported first, like for the editor.
foreach ($n in 'UE-ZenDataPath', 'UE-LocalDataCachePath') {
    [Environment]::SetEnvironmentVariable($n, [Environment]::GetEnvironmentVariable($n, 'User'), 'Process')
}
New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null
& "$PSScriptRoot\build.cmd" $Log
Get-Content $Log | Select-String -Pattern 'error|warning C|Result:|Total execution|exit=' | Select-Object -First 30
