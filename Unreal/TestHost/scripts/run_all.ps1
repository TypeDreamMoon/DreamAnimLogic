param([string]$Out = 'G:\DCC\scratch\w4_logs', [int]$Repeats = 3)
# The whole W4 acceptance: compiled vs interpreted frame by frame, hot swap, cost.  About ten minutes.
$scripts = $PSScriptRoot
$runs = Join-Path $Out 'runs'
foreach ($s in 'melee', 'lockon', 'gait') {
    foreach ($m in 'compiled', 'interp') { & "$scripts\run_test.ps1" -Scenario $s -Mode $m -Out $Out | Out-Null }
    Write-Output "== $s: compiled vs interpreted"
    python "$scripts\compare_runs.py" "$runs\${s}_compiled.json" "$runs\${s}_interp.json" --skew 1 $(if ($s -eq 'lockon') { '--ignore'; 'TargetId' })
}
foreach ($s in 'melee_swap', 'gait_swap') { & "$scripts\run_test.ps1" -Scenario $s -Mode interp -Out $Out | Out-Null }
$env:DALTEST_PARAM_OVERRIDE = '1'
& "$scripts\run_test.ps1" -Scenario melee_swap -Mode interp -Name melee_swap_override -Out $Out | Out-Null
Remove-Item Env:\DALTEST_PARAM_OVERRIDE
foreach ($r in 1..$Repeats) {
    foreach ($s in 'melee', 'lockon') { foreach ($m in 'compiled', 'interp') { & "$scripts\run_test.ps1" -Scenario $s -Mode $m -Name "${s}_${m}_r$r" -Out $Out | Out-Null } }
}
& "$scripts\run_test.ps1" -Scenario overhead -Mode interp -Out $Out | Out-Null
python "$scripts\perf_summary.py" $runs melee lockon
Write-Output "swap results: $runs\melee_swap_interp.json, $runs\gait_swap_interp.json, $runs\melee_swap_override.json (notes)"
