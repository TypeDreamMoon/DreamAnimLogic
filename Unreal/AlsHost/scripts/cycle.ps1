param(
    [string]$Rev = 'worktree',                                   # the .dal sources: HEAD, another revision, or worktree (what is in als/ now)
    [string]$Out = 'G:\DCC\scratch\h5\cycle',                    # takes, comparison files and logs go here
    [string]$Takes = 'lane,speed_jumps,stance_changes,rotation_modes,view_yaw_sweeps,in_air,view_mode,roll,mantle,features,ragdoll',
    [switch]$NoRefresh,                                          # the host already has the generated code of this revision, built
    [switch]$NoChecks,
    [switch]$WithC3,
    [switch]$Windowless,                                         # record without a renderer (-nullrhi): faster, but the mesh does not count as rendered and the notifies of montages differ
    [switch]$NoPhase,                                            # do not start each take at the animation phase of the truth take (see below)
    [int]$PhaseOffset = 0                                        # g_record.py R1_PHASE_OFFSET
)
# The whole PIE cycle for the generated ALS (about 20 minutes; the takes render offscreen at 1024x576 like the original ones, -Windowless: no renderer at all): generate + swap into the host + build, the 14 DalRuntime checks, the truth
# takes recorded and compared with G:\DCC\AlsTruth, a table with one row per take.  No window is ever shown (the takes render offscreen).  -WithC3 also runs the c3 scenarios of the Blender side.
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$truth = 'G:\DCC\AlsTruth'
New-Item -ItemType Directory -Force $Out | Out-Null
Set-Location $repo
if (-not $NoRefresh) { & (Join-Path $PSScriptRoot 'refresh_host.ps1') -Rev $Rev }
if (-not $NoChecks) {
    $env:DALCHK_OUT = Join-Path $Out 'checks.json'
    & (Join-Path $PSScriptRoot 'run_editor.ps1') -Script (Join-Path $PSScriptRoot 'runtime_checks.py') -Log (Join-Path $Out 'editor_checks.log') -Fixed60
    python -c "import json,sys; d=json.load(open(sys.argv[1])); print(len(d), 'checks, failed:', [k for k,v in d.items() if not v.get('ok')])" $env:DALCHK_OUT
}
# The pose of a run depends on the phase of the graph's 160-frame loops, i.e. on how many animation updates the instance had before the first recorded frame: two runs
# whose phases differ get poses up to 2.4 cm apart whatever the logic does, with the same phase they agree to 0.00-0.15 cm.  G:\DCC\AlsTruth\phase.json (phase_ref.py) has the
# phase of the truth takes; the recorder waits for it before it starts.
$phase = Join-Path $truth 'phase.json'
if ($NoPhase -or -not (Test-Path $phase)) { $phase = '' }
& (Join-Path $PSScriptRoot 'record_takes.ps1') -Takes $Takes -Out (Join-Path $Out 'takes') -Res (Join-Path $Out 'res') -Tag cycle -Rendered:(-not $Windowless) -Phase $phase -PhaseOffset $PhaseOffset
foreach ($t in $Takes.Split(',')) {
    if ($t -eq 'ragdoll') {                                      # a ragdoll cannot be compared frame by frame: the state machine, the timings, the get up
        python -I (Join-Path $PSScriptRoot 'compare_ragdoll.py') (Join-Path $Out 'takes\als_ragdoll.dvrec') (Join-Path $truth 'als_ragdoll.dvrec') `
               --json (Join-Path $Out 'ragdoll_cmp.json') --md (Join-Path $Out 'ragdoll_cmp.md') | Out-Null
        continue
    }
    $seg = Join-Path $Out "res\r1_info_$t.json"                  # the route segments the recorder driver wrote
    $args_ = @((Join-Path $PSScriptRoot 'compare_takes.py'), (Join-Path $Out "takes\als_$t.dvrec"), (Join-Path $truth "als_$t.dvrec"),
               '--json', (Join-Path $Out "cmp_$t.json"), '--md', (Join-Path $Out "cmp_$t.md"))
    if (Test-Path $seg) { $args_ += @('--segments', $seg) }
    python -I @args_ | Out-Null
}
python -I (Join-Path $PSScriptRoot 'summarize_takes.py') (Join-Path $Out 'cmp_')
# The truth takes were recorded before the recorder could call GetControlRigInput.  The original ALS recorded again with the current recorder (G:\DCC\AlsTruth\rig, same
# routes, rendered offscreen) has those reads: against it the foot IK input of the graph is compared too (and the pose, with the run-to-run noise of the original in it).
$rigRef = Join-Path $truth 'rig'
if (Test-Path $rigRef) {
    foreach ($t in $Takes.Split(',')) {
        $ref = Join-Path $rigRef "als_$t.dvrec"
        if ($t -eq 'ragdoll' -or -not (Test-Path $ref)) { continue }
        python -I (Join-Path $PSScriptRoot 'compare_takes.py') (Join-Path $Out "takes\als_$t.dvrec") $ref --json (Join-Path $Out "rig_$t.json") --md (Join-Path $Out "rig_$t.md") | Out-Null
    }
    Write-Host "against the original recorded with the current recorder (G:\DCC\AlsTruth\rig; read the rig_values_off / rig_max columns: the pose columns here depend on that run's animation phase, which is not the one of the truth takes):"
    python -I (Join-Path $PSScriptRoot 'summarize_takes.py') (Join-Path $Out 'rig_')
}
if (Test-Path (Join-Path $Out 'ragdoll_cmp.md')) { Get-Content (Join-Path $Out 'ragdoll_cmp.md') }
if ($WithC3) { python -I (Join-Path $PSScriptRoot 'c3_host_compare.py') --set als_ext --out (Join-Path $Out 'c3') }
