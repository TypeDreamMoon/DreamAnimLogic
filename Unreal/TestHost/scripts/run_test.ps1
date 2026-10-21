param(
    [Parameter(Mandatory = $true)][string]$Scenario,
    [Parameter(Mandatory = $true)][ValidateSet('compiled', 'interp')][string]$Mode,
    [string]$Name = '',
    [int]$Fps = 60,
    [int]$TimeoutSec = 600,
    [string]$Out = 'G:\DCC\scratch\w4_logs'
)
# One PIE run of scenario_dal.py: scenario melee | lockon | gait | melee_swap | gait_swap | bench | bench_cb | overhead,
# mode compiled (generated C++) | interp (DalLive).  The result is <Out>\runs\<Name>.json, the editor log <Out>\<Name>.log.
if (-not $Name) { $Name = "${Scenario}_${Mode}" }
$scripts = $PSScriptRoot
$env:DALTEST_SCRIPTS = $scripts
$env:DALTEST_MODE = $Mode
$env:DALTEST_SCENARIO = $Scenario
$env:DALTEST_NAME = $Name
$env:DALTEST_OUT = Join-Path $Out 'runs'
& "$scripts\run_editor.ps1" -Script "$scripts\scenario_dal.py" -Map '/Game/DalTest/L_DalTest' -Fps $Fps -Log (Join-Path $Out "$Name.log") -TimeoutSec $TimeoutSec
