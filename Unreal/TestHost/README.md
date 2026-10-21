# TestHost -- the W4 test project and PIE harness for DalLive

A throw-away Unreal 5.8 C++ project (`G:\DCC\DalLiveHost`, made from the installed Third Person C++ template, nothing
downloaded) plus editor-Python scripts that play the same scripted input against

* **compiled**: the C++ the dal compiler generates (`MeleeCombo`, `LockOnCamera`, `HeroGait`, in `Source/DalLiveHost/Dal`), and
* **interp**: the same `.dal` run by DalLive,

record the logic state and the host effects every frame, and compare. The acceptance of `dal-syntax-v1.md` section 16.

## Make the project

```
python Unreal\TestHost\create_project.py            # --dest G:\DCC\DalLiveHost --engine I:\UnrealEngine\UE_5.8
Unreal\TestHost\scripts\build.cmd                   # UBT, DalLiveHostEditor Win64 Development (about a minute)
Unreal\TestHost\scripts\run_editor.ps1 -Script Unreal\TestHost\scripts\make_content.py -NoFixedStep   # once: actions, montages, map
```

`create_project.py` copies and renames the template (like the engine's project creation does with `TemplateDefs.ini`),
drops the template's variants, lays `overlay/` on top (module Build.cs, test character / game mode / `UDalTestLibrary` /
timed components, config, .uproject), copies the shared Mannequin and Input content, compiles the examples into the module
(`hero_gait` with `--bag`) and copies the plugin. After changing the plugin run `scripts\sync_plugin.cmd` and `build.cmd`.

**Remove it:** delete `G:\DCC\DalLiveHost`. Nothing outside that folder is changed (the engine is not touched; logs and
results go to `G:\DCC\scratch\w4_logs`, delete that too).

## Run

```
Unreal\TestHost\scripts\run_all.ps1                 # everything below, about ten minutes
Unreal\TestHost\scripts\run_test.ps1 -Scenario melee -Mode interp      # one run; result: <Out>\runs\melee_interp.json
python Unreal\TestHost\scripts\compare_runs.py runs\melee_compiled.json runs\melee_interp.json --skew 1
```

| scenario | what happens | modes |
|---|---|---|
| `melee` | nine Attack taps (chain, early taps buffered, cancel window), a jump mid-combo (combo reset), a tap in the air | compiled, interp |
| `lockon` | three targets, lock-on, the target walks away, a wall blocks the view (the lock is lost after 1.5 s), unblocked, relock, target destroyed | compiled, interp |
| `gait` | anim logic: walk, run, sprint (`IA_Sprint`), strafe (`IA_Move`), jump, control rotation, `Aiming` written on the AnimInstance by "game code" | compiled (`UHeroGait`), interp (`UHeroGaitBag` + DalLive) |
| `melee_swap` | edits the `.dal` while PIE runs: a changed behaviour, a file with four errors, a new state and handler | interp (`DALTEST_PARAM_OVERRIDE=1` adds a param override) |
| `gait_swap` | same for anim logic: a changed constant, a new state the AnimInstance does not have | interp |
| `bench`, `bench_cb`, `overhead` | cost of the `unreal` calls the host makes (tight loop, inside the delegate callback) and of the delegate -> Python round trip alone | interp |

The harness (`harness_common.py`) starts PIE from a Slate post-tick callback, runs a scenario generator one engine frame at a
time (`yield` = next frame), records after every world tick and injects input for the next one, writes JSON, and lets the
editor quit. `-UseFixedTimeStep -FPS=60 -nullrhi -unattended`; **`-ExecCmds="Slate.bAllowThrottling 0"` matters**: without it
the PIE world sometimes did not tick for hundreds of frames (Slate's responsive-mode throttle, nothing to do with the logic).
Input goes through `UEnhancedInputLocalPlayerSubsystem::InjectInputVectorForAction` (`UDalTestLibrary::InjectVector`), the same
path for both modes; both components get the same tick prerequisites (controller, movement) so the order inside a frame
is the same in every run.

`compare_runs.py` compares per frame: every input / state of the logic and the effects (CMC speed, montage playing / active /
position / section, control rotation, spring arm, location). Floats: `1e-4` relative + absolute (the compiled side is float).
`--skew 1` accepts a bool / enum that flips one frame apart (a hard threshold crossed in float vs double) and reports it.
A mutated copy of a result is flagged (checked when it was written).

Results of 2026-10-21 are in `Unreal/DalLive/README.md` ("Verification").
