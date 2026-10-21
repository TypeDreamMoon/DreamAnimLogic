# DalLive -- hot reload of `.dal` logic in the Unreal editor

Edit a `.dal` file, see the change in PIE a quarter of a second later: no UBT, no Live Coding. The program is read from the
file and run by the reference interpreter (`dal.interp`, Python) in the editor; a thin C++ component calls it every tick.
The compiled path (`python -m dal compile --cpp`, generated C++ in a game module) stays the way to ship.

```
.dal file --(dal_live, editor Python: check, interpret, hot swap)--> UDalInterpHost (C++ component, same tick group as the
                                                                      generated one) --> CMC / montages / input / camera
```

Status: version 0.1.0, verified in PIE with UE 5.8.2 (installed engine) against the generated C++ -- see "Verification".

## Why it lives here

* **Next to the compiler (`DreamAnimLogic/Unreal/DalLive`)**: the plugin is the third implementation of the host protocol
  (`dal/hostapi.py`: Blender's `dal_host.py`, the generated C++, and this one). It is versioned and tested with the
  tables it implements, and it uses the `dal` package directly (no copy).
* Not in DreamAnim: that plugin is the `.dab` toolchain and owned by another session; DalLive needs nothing from it (a
  `.dab` Animation Blueprint only has to read the same variable names, see "Anim logic").
* Not in DreamVEdForUnreal: that is the Blender bridge, hot reload is not DreamVEd specific.
* The generated C++ lives in game modules; the component here has the **same property names, the same tick group and the
  same order inside the tick**, so switching between interpreted and compiled changes the class of the component (or of the
  AnimInstance parent), not the content that reads it.

## Layout

```
DalLive.uplugin
Source/DalLive/                  Runtime module (the component also exists in packaged games; there it only logs once)
  Public/DalInterpHost.h         UDalInterpHost: the component. Delegates with payloads, Enhanced Input, traces, targets,
                                 writes by property name -- what editor Python cannot reach
  Public/DalLiveSubsystem.h      UDalLiveSubsystem (engine subsystem): the three delegates Python binds, toasts, Message Log
  Public/DalLiveTypes.h          FDalLiveEvent, FDalLiveHit
Content/Python/                  put on sys.path by PythonScriptPlugin; init_unreal.py starts everything
  init_unreal.py
  dal_live/bootstrap.py          finds the `dal` package
  dal_live/loader.py             check_file -> diagnostics as `file(line,col): error DALnnnn: message`, fingerprints, param text
  dal_live/swap.py               which state survives a swap
  dal_live/session.py            one program on one host: load, tick, swap, errors  (no `unreal` import)
  dal_live/host.py               UeHost: HostV1 on the `unreal` module and the component
  dal_live/manager.py            delegates, file watching (Slate post tick, 10 Hz), toasts
```

## Use

1. Copy `DalLive/` into `<Project>/Plugins/` (PythonScriptPlugin and EnhancedInput are enabled by the .uplugin).
2. Tell it where the compiler is: environment variable `DREAMANIM_DAL` (the folder that contains `dal/`, the same variable
   `dab.ps1` uses) or `<Project>/Config/DalLive.json` `{"compiler": "I:/DCC/DreamAnimLogic"}`. Inside the DreamAnimLogic
   checkout nothing is needed.
3. Add **DAL Interpreter Host** to the character (Blueprint or `AddComponentByClass`) and fill in
   * `DalFile` -- absolute or project relative path of the `.dal`;
   * `Montages` -- `montage Light1 {...}` name -> `UAnimMontage` asset;
   * `Actions` -- `action Attack: bool;` (and the names used in `host.action("Move")`) -> `UInputAction` asset;
   * `TargetTag`, `TraceChannel` -- as the generated `DalTargetTag` / `DalTraceChannel`;
   * `ParamOverrides` -- `param` name -> text (`"200"`, `"true"`, `"(1,2,3)"`, an enum member); they beat every file default;
   * `bHotReload`, `bPublishToAnimInstance`.
4. Play. The `Status` fields of the component show `running melee_combo.dal (generation 2)` or the first error.

The Output Log lines start with `DalLive:`; the Message Log has a page "DAL Live"; a toast appears on every swap and every
error. Errors are `path(line,col): error DAL4002: message` plus the source line and a caret, so editors that parse the
Visual Studio format can jump to them.

## What it does every tick (`logic X : actor;`)

Same order as `TickComponent` of the generated component (`TG_PrePhysics`, game thread):

```
UDalInterpHost::TickComponent
    DalBindHost            C++  subscribe to the AnimInstance delegates and the pawn's UEnhancedInputComponent (retried every tick)
    OnHostTick.Broadcast   C++ -> Python  (carries the number of queued events / notify flags so Python can skip the fetch)
        Session.tick           begin_update (cached engine objects), then Interpreter.update(dt):
            sample inputs          UeHost reads (`unreal` module: CMC, pawn, montage state; the component: action values)
            collect events         comp.take_events() -> notify / notify_begin / notify_end / montage_ended / montage_blending_out / input
            timers, handlers, update body
            flush commands         UeHost commands, in order, at the end of the update (actor logic)
```

### HostV1 on Unreal

| protocol | implemented with |
|---|---|
| reads `time velocity acceleration max_* movement_mode is_grounded is_crouching actor_location/rotation control_rotation` | `unreal` module on `CharacterMovementComponent` / pawn |
| reads `montage_playing/active/position/section` | `AnimInstance.montage_*` |
| reads `action(name)` | component: `UEnhancedPlayerInput::GetActionValue` |
| read `notify("N")` | component: flag set by `OnPlayMontageNotifyBegin` for point notifies |
| the 26 commands | `unreal` module (CMC properties by `set_editor_property`, `Character.jump/crouch/launch_character`, `AnimInstance.montage_*`, `Controller.set_control_rotation`, spring arm, camera) -- semantics as in the generated `DalExecuteCommand` (negative blend out = the montage's own) |
| queries `trace_line/trace_sphere/target_*` | component (same `LineTraceSingleByChannel` / `SweepSingleByChannel` / tag search as the generated code) |
| events | component: `OnMontageEnded`, `OnMontageBlendingOut`, `OnPlayMontageNotifyBegin/End` (needs the payload, so not reachable from Python), `BindActionValueLambda` for `on input` |

## Hot swap

`Session.poll` looks at the file (and its `_standins.py`) ten times a second; a changed file is compiled once it has been
quiet for 150 ms (editors write in steps). Compile takes 1-2 ms for the examples. Then:

* **compile error**: reported (log, Message Log, toast), the last good program keeps running, the broken file is not
  recompiled until it changes again;
* **good file**: a new interpreter is built, the surviving values are copied in (`swap.py`), the component is told the new
  event / input watch list, the program runs from the next tick. No pause, no reset of the character.
* **runtime error** in the running program (a Python exception): the program stops, the error and traceback are logged and
  toasted, the next good save restarts it.

What survives: a **state** (and a free input) with the same name and type keeps its value; an enum keeps its *member* by
name (inserting a combo step or a montage section shifts nothing); new states start at their default, removed ones are
dropped, a changed type or a vanished enum member restarts that value. `param`s are never carried (they come from the new
defaults and `ParamOverrides`), host-bound inputs are sampled again. Pending events stay queued. Changing the kind
(`actor` <-> `anim`) restarts from the defaults.

## Anim logic (`logic X;`) -- option B

The AnimBP reads variables of its AnimInstance (`Main.Speed`). The generated `U<Logic>` has them as UPROPERTYs. For the
interpreter use the **property bag**: `python -m dal compile hero_gait.dal --cpp Source/<Module>/Dal --module <MODULE>_API --bag`
also writes `U<Logic>Bag`, a `UAnimInstance` with the same params / inputs / states and no logic. Make it the parent class of
the Animation Blueprint (instead of `U<Logic>`), add the host component, point it at the `.dal`; going back is changing the
parent class again -- the graph and the `.dab` do not change.

Each tick, before the mesh's animation update (`OrderBeforeMesh`, the generated parent runs in `NativeUpdateAnimation`):

* the interpreter's **free inputs** are read from the AnimInstance (game code writes them there, as with the generated class);
* the update runs; its **commands run at the start of the next update** (the generated anim instance queues them the same way);
* params, host-bound inputs and states are **written by property name** (`SetPublishPlan` resolves the `FProperty`s once;
  one call with the packed values per tick). Names without a matching property are reported once and skipped.

Actor logic can publish to the AnimInstance too (`bPublishToAnimInstance`).

## Verification (PIE, UE 5.8.2, fixed step 60 Hz, `-nullrhi`)

`Unreal/TestHost` creates the test project (`G:\DCC\DalLiveHost`) and runs the same scripted input against the generated
C++ and against DalLive, comparing every frame. Results of 2026-10-21:

| program | frames | result |
|---|---|---|
| `melee_combo.dal` (3-hit chain, input buffer, cancel windows, montage events) | 540 | identical: states, CMC speed, montage state per montage, control rotation; largest numeric difference 1.4e-8 |
| `lockon_camera.dal` (target choice, traces, camera, timers) | 700 | identical except the lost-sight timer: it expires one frame earlier in double than in float (`7.6e-7` s left in float32 after 90 ticks); numbers within 3e-4 |
| `hero_gait.dal` (anim logic through `UHeroGaitBag`) | 520 | identical: every param / input / state of the AnimInstance; `Speed` within 9e-5 |

Hot swap during PIE (`melee_swap`, `gait_swap`): 225-335 ms from saving the file to the new generation running (six measurements) (100 ms poll
+ 150 ms quiet time + compile); `Hits` kept across the swap and the next `Hit` notify adds 10 instead of 1; a file with four
compile errors is reported in 243-298 ms and generation 2 keeps running (the next attack still adds 10); a new state appears
and works; a `param` override survives every generation; anim logic: `Speed` continuous across the swap, the gait changes
the frame the new threshold takes effect.

### Cost of one tick (microseconds, median of 3 runs, `FPlatformTime` around `TickComponent`)

| | mean | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| melee, compiled C++ | 3.7 | 3.3 | 5.0 | 13.8 | 53 |
| melee, DalLive | 86 | 78 | 130 | 351 | 718 |
| lock-on, compiled C++ | 17.6 | 21.5 | 36 | 50 | 156 |
| lock-on, DalLive (locked on for 2/3 of the run) | 357 | 442 | 706 | 1025 | 1557 |
| hero_gait, DalLive (anim logic, ~20 values published) | 182 | 177 | 251 | 342 | 731 |

That is 0.5 % (melee) to 2 % (lock-on) of a 60 Hz frame for one character. Breakdown of the melee tick: the delegate ->
Python call round trip alone is **19 us** (a host without a program); the interpreter itself about 3 us (measured alone against the mock host); the rest are the
crossings from Python into the engine (1.2-3 us each when repeated in a tight loop, several times that in a real frame
because the caches are cold): CMC lookup and read, `take_events`, command writes. Already done: engine objects are cached
across ticks (dropped when the AnimInstance moves or a call fails), `take_events` / `consume_notify_flag` are skipped when the
tick delegate says nothing is queued, publishing is one call with a packed array. Not done (would roughly halve it): sample
all common reads in one C++ call. Bytecode / C++ is what removes it (`dal-syntax-v1.md` section 16 option C).

## Limits

* Editor and PIE / Simulate only (needs PythonScriptPlugin). A packaged game has the component but no Python: it logs once and
  idles. Ship the generated C++.
* Notifies reach the logic through **Play Montage Notify / Play Montage Notify Window** (as the generated actor logic); the
  custom `AnimNotify_<Name>()` functions of `logic X;` are not intercepted.
* Python cannot read `FRotator3f` / `FQuat4f` / `FVector2f` (the float variants have no Python fields): tests read them through
  `export_text()`. The component's own reflection code handles them.
* Bool properties lose a leading `In` / `Is` / `Has` in Python (`InAir` is `air`); only the test scripts care.
* Float against double: programs with hard thresholds can differ by one frame from the compiled version (documented in
  `dal-syntax-v1.md` section 15); the interpreter computes in double, the generated C++ in float.
* Free inputs of actor logic have no property to write to; set them through `dal_live.get_manager().session_for(comp).interp.set_input`.
* One session per component; the file path is read at `BeginPlay` (changing `DalFile` needs a new play session).
* `montage_stop_all` with a negative blend out uses the blend out of the montage that is active (same approximation as the
  generated code).

## Tests

* Pure Python half (`loader`, `swap`, `session`, `bootstrap`): `python -I -m unittest discover -s tests -p test_live_session.py`
  in the DreamAnimLogic repository (swap rules, error handling, debounce, deferral of anim commands, overrides).
* Unreal half: `Unreal/TestHost/scripts/run_all.ps1` (about ten minutes; needs the test project, see `Unreal/TestHost/README.md`).
