param(
    [string]$Rev = 'worktree',                                   # the .dal sources: HEAD, another revision, or worktree (what is in als/ now)
    [string]$Out = 'G:\DCC\scratch\h5\cycle',                    # takes, comparison files and logs go here
    [string]$Takes = 'lane,speed_jumps,stance_changes,rotation_modes,view_yaw_sweeps,in_air,view_mode,roll,mantle,features',
    [switch]$NoRefresh,                                          # the host already has the generated code of this revision, built
    [switch]$NoChecks,
    [switch]$WithC3
)
# The whole PIE cycle for the generated ALS (about 12 minutes, no window): generate + swap into the host + build, the 14 DalRuntime checks, the truth
# takes recorded and compared with G:\DCC\AlsTruth, a table with one row per take.  -WithC3 also runs the c3 scenarios of the Blender side.
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
& (Join-Path $PSScriptRoot 'record_takes.ps1') -Takes $Takes -Out (Join-Path $Out 'takes') -Res (Join-Path $Out 'res') -Tag cycle
foreach ($t in $Takes.Split(',')) {
    $seg = Join-Path $Out "res\r1_info_$t.json"                  # the route segments the recorder driver wrote
    $args_ = @((Join-Path $PSScriptRoot 'compare_takes.py'), (Join-Path $Out "takes\als_$t.dvrec"), (Join-Path $truth "als_$t.dvrec"),
               '--json', (Join-Path $Out "cmp_$t.json"), '--md', (Join-Path $Out "cmp_$t.md"))
    if (Test-Path $seg) { $args_ += @('--segments', $seg) }
    python -I @args_ | Out-Null
}
python -I (Join-Path $PSScriptRoot 'summarize_takes.py') (Join-Path $Out 'cmp_')
if ($WithC3) { python -I (Join-Path $PSScriptRoot 'c3_host_compare.py') --set als_ext --out (Join-Path $Out 'c3') }
