param(
    [string]$Log = 'G:\DCC\scratch\h5\build.log',
    [string]$Target = 'DreamAnimTestHostEditor',
    [string]$Project = 'I:\UnrealProject_Moon\DEV_58\DreamVEdAnimHost\DreamAnimTestHost.uproject',
    [int]$MaxParallelActions = 8
)
# Builds the sandbox host's editor target (UBT, I:\UnrealEngine\UE_5.8) hidden, at below-normal priority and with at most 8 parallel actions.
# The user-level UE cache variables are imported first.  Prints the errors and the result line.
foreach ($n in 'UE-ZenDataPath','UE-LocalDataCachePath') { [Environment]::SetEnvironmentVariable($n, [Environment]::GetEnvironmentVariable($n,'User'), 'Process') }
$bat = 'I:\UnrealEngine\UE_5.8\Engine\Build\BatchFiles\Build.bat'
$err = "$Log.err"
$p = Start-Process -FilePath $bat -ArgumentList @($Target, 'Win64', 'Development', "-Project=$Project", '-WaitMutex', '-NoHotReloadFromIDE', "-MaxParallelActions=$MaxParallelActions") `
    -RedirectStandardOutput $Log -RedirectStandardError $err -PassThru -WindowStyle Hidden
try { $p.PriorityClass = 'BelowNormal' } catch {}
$p.WaitForExit()
if (Test-Path $err) { Get-Content $err | Add-Content $Log }
"exit=$($p.ExitCode)" | Add-Content $Log
Get-Content $Log | Select-String -Pattern 'error|Error:|warning C|Result:|Total execution|exit=' | Select-Object -First 60
if ($p.ExitCode -ne 0) { throw "the build of the sandbox host failed (exit $($p.ExitCode)), see $Log" }       # (a cycle must not record takes with the binaries of an earlier build)
