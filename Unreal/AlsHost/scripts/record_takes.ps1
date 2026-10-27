param(
    [string]$Takes = 'smoke',
    [string]$Out = 'G:\DCC\scratch\h5\takes',
    [string]$Res = 'G:\DCC\scratch\h5\res',
    [string]$Suffix = '',
    [string]$Tag = 'gen',
    [int]$TimeoutMinutes = 40,
    [string]$Truth = '1',
    [string]$Map = '/Game/DreamVEdTest/Maps/L_DreamVEd_AlsRecord',
    [switch]$Rendered,                     # with a renderer, without a window (the original ALS needs one, see run_editor.ps1)
    [string]$Phase = '',                   # phase.json (phase_ref.py): start each take at the same point of the graph's 160-frame loops as the reference take
    [int]$PhaseOffset = 0,                 # the driver's constant (see g_record.py R1_PHASE_OFFSET)
    [int]$Fps = 60                         # the fixed frame rate of the run (the takes fps30_mix / fps120_mix need 30 / 120)
)
# Records takes of the truth routes (G:\DCC\AlsTruth\tools\r1_record.py) with the generated ALS logic: starts the host editor without a window
# (-nullrhi, hidden, below-normal priority) at a fixed 60 fps like launch_editor.ps1 did for the original takes.  g_record.py keeps the mesh ticking
# the way a rendered one does (a hidden mesh would only tick its montages).
# Takes: smoke, lane, speed_jumps, stance_changes, rotation_modes, view_yaw_sweeps, in_air, view_mode, roll, mantle, features (comma separated).
# Files: <Out>\als_<take><Suffix>.dvrec
$env:R1_RENDERED = if ($Rendered) { '1' } else { '0' }
$env:R1_PHASE = $Phase
$env:R1_PHASE_OFFSET = "$PhaseOffset"
$env:R1_FPS = "$Fps"
$env:R1_TAKES = $Takes
$env:R1_SUFFIX = $Suffix
$env:R1_OUT = $Out
$env:R1_TRUTH = $Truth
$env:R1_RES = $Res
New-Item -ItemType Directory -Force $Res, $Out | Out-Null
& "$PSScriptRoot\run_editor.ps1" -Script (Join-Path $PSScriptRoot 'g_record.py') -Map $Map -Log (Join-Path $Res "editor_$Tag.log") -TimeoutMinutes $TimeoutMinutes -Fixed60 -Fps $Fps -Rendered:$Rendered
exit $LASTEXITCODE
