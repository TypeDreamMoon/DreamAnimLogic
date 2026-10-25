param(
    [Parameter(Mandatory)][string]$Script,
    [string]$Map = '/Game/DreamVEdTest/Maps/L_DreamVEd_AlsRecord',
    [string]$Log = 'G:\DCC\scratch\h5\editor.log',
    [int]$TimeoutMinutes = 20,
    [string]$Project = 'I:\UnrealProject_Moon\DEV_58\DreamVEdAnimHost\DreamAnimTestHost.uproject',
    [string]$ExtraArgs = '',
    [switch]$Fixed60
)
# Runs a Python script in the sandbox host's editor WITHOUT a window and at below-normal priority: -nullrhi (no renderer, no window), started hidden.
# The user-level UE cache variables are imported first (caches on G:).  Kills only the editor this script started, when the timeout is over.
foreach ($n in 'UE-ZenDataPath','UE-LocalDataCachePath') { [Environment]::SetEnvironmentVariable($n, [Environment]::GetEnvironmentVariable($n,'User'), 'Process') }
$exe = 'I:\UnrealEngine\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe'
$py = $Script -replace '\\','/'
$a = @("`"$Project`"")
if ($Map) { $a += $Map }
$a += @("-ExecCmds=`"py $py`"", "-abslog=`"$Log`"", '-unattended', '-nopause', '-nosplash', '-NoSound', '-NoLiveCoding', '-notelemetry', '-NoVerifyGC', '-nullrhi')
if ($Fixed60) { $a += @('-benchmark', '-fps=60') }
if ($ExtraArgs) { $a += $ExtraArgs }
Write-Host "launching (hidden, below normal): $exe $($a -join ' ')"
$p = Start-Process -FilePath $exe -ArgumentList ($a -join ' ') -PassThru -WindowStyle Hidden -WorkingDirectory (Split-Path $Project)
try { $p.PriorityClass = 'BelowNormal' } catch {}
$ok = $p.WaitForExit($TimeoutMinutes * 60 * 1000)
if (-not $ok) { Write-Host "TIMEOUT after $TimeoutMinutes min: killing editor pid $($p.Id) (started by this script)"; try { $p.Kill($true) } catch {}; exit 3 }
Write-Host "editor exited with code $($p.ExitCode)"
exit $p.ExitCode
