param(
    [Parameter(Mandatory = $true)][string]$Script,
    [string]$Map = '/Engine/Maps/Entry',
    [int]$Fps = 60,
    [string]$Log = 'G:\DCC\scratch\w4_logs\editor.log',
    [int]$TimeoutSec = 600,
    [string]$Extra = '',
    [string]$Engine = 'I:\UnrealEngine\UE_5.8',
    [string]$Project = 'G:\DCC\DalLiveHost\DalLiveHost.uproject',
    [switch]$Gui,
    [switch]$NoFixedStep
)
# Runs the DalLiveHost editor headless (-nullrhi) with an editor Python script and waits for it (stops it only on timeout).
# The user-level UE cache variables are imported first (zen / DDC live on G:).
# Slate.bAllowThrottling 0: without it the PIE world can sit still for hundreds of frames (Slate's "responsive mode" throttle).
$ErrorActionPreference = 'Stop'
foreach ($n in 'UE-ZenDataPath', 'UE-LocalDataCachePath') {
    [Environment]::SetEnvironmentVariable($n, [Environment]::GetEnvironmentVariable($n, 'User'), 'Process')
}
$exe = Join-Path $Engine ('Engine\Binaries\Win64\' + $(if ($Gui) { 'UnrealEditor.exe' } else { 'UnrealEditor-Cmd.exe' }))
$args1 = @($Project, $Map, '-nullrhi', '-unattended', '-nosplash', '-NoSound', '-stdout', '-FullStdOutLogOutput', '-ExecCmds="Slate.bAllowThrottling 0"', "-abslog=$Log", "-ExecutePythonScript=$Script")
if (-not $NoFixedStep) { $args1 += @('-UseFixedTimeStep', "-FPS=$Fps") }
if ($Extra) { $args1 += $Extra.Split(' ') }
New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null
$sw = [Diagnostics.Stopwatch]::StartNew()
$p = Start-Process -FilePath $exe -ArgumentList $args1 -PassThru -WindowStyle Hidden
if (-not $p.WaitForExit($TimeoutSec * 1000)) {
    Write-Output "TIMEOUT after $TimeoutSec s; stopping pid $($p.Id) (started by this script)"
    Stop-Process -Id $p.Id -Force
    exit 3
}
Write-Output ("editor exit code {0} after {1:N1} s" -f $p.ExitCode, $sw.Elapsed.TotalSeconds)
exit $p.ExitCode
