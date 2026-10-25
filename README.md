# DreamAnimLogic -- the `.dal` compiler

`.dal` (Dream Anim Logic) is a small logic language for animation and game logic. **One Python
compiler, two backends**:

| Backend | Output | Runs in |
|---|---|---|
| Python | a self-contained module with one class (`__init__(host)`, `update(dt)`, state fields) | Blender (DreamVEd), tests, tools |
| Unreal C++ | `U<Logic>.h/.cpp` (+ `DalMath.h`, `DalHost.h`): a `UAnimInstance` parent class (`logic X;`) or a `UActorComponent` character component (`logic X : actor;`) | Unreal Engine 5.8: the C++ parent of a `.dab` Animation Blueprint, or a component on the character |

The language is specified in Chinese in `I:\DCC\Plan\dal-syntax-v0.md` (v0: values, math, anim instance logic),
`I:\DCC\Plan\dal-syntax-v1.md` (v1 / v2: host commands, events, montages, combos, queries, camera) and
`I:\DCC\Plan\dal-syntax-v3.md` (v3, versions 0.3.0 and 0.4.0: libraries, structs, tags, methods and peers, tick slots, settings and curves,
entries with parameters, transforms, scene queries -- the language ALS-Refactored is rewritten in, see `als-dal-rewrite.md`; 0.4.0 adds timer
and hook events, notify declarations with payloads, `post_update`, dynamic montages / slots / pose snapshots, loops, root motion override and
montage asset queries, and what the ALS rewrite found missing).
This README covers the repository.

```
logic HeroGait;

enum Gait { Walk, Run }

input Velocity: vec3 <- host.velocity;      // read from the host (CMC) every frame
state Speed: float;                          // persisted, exposed as UPROPERTY
state CurrentGait: Gait = Gait.Walk;

update(dt) {
    Speed = interp_to(Speed, length(Velocity.xy), dt, 8.0);
    CurrentGait = Speed > 450.0 ? Gait.Run : Gait.Walk;
}
```

and, from v1, logic that acts on the character (`examples/melee_combo.dal`, abridged):

```
logic MeleeCombo : actor;                    // a character component, game thread

montage Light1 { Windup, Strike, Recover }   // assets the host plays
montage Light2 { Windup, Strike, Recover }
action Attack: bool;                         // an Enhanced Input action

window Cancel <- notify_state CancelWindow closes_on Light1, Light2;
buffer AttackBuffer = 0.3 <- input Attack started;     // a press stays valid for 0.3 s

combo Melee {
    start on AttackBuffer when Grounded -> Hit1;
    step Hit1 { play Light1; on AttackBuffer within Cancel -> Hit2; }
    step Hit2 { play Light2; }
}

on notify Hit { Hits += 1; }                 // event handlers
update(dt) { host.set_max_walk_speed(Melee != MeleeStep.Idle ? 120.0 : 600.0); }   // host commands are queued
```

## Layout

```
dal/                  the package (pure Python 3.11+, no dependencies) -- this is what gets vendored
  lexer.py parser.py syntax.py    source -> syntax tree (errors with line/column)
  libs.py                         `use "x.dal";`: finds, loads and merges library files (shared declarations)
  lower.py                        timer / window / buffer / combo -> core language (on the syntax tree)
  check.py names.py types.py      name resolution, static types, constant folding -> IR
  peers.py                        what one logic knows about another (`peer`): its variables, pub methods, linked entries
  values.py curve.py settings.py  struct / settings values, FRichCurve evaluation, the loader of settings data (JSON, dicts)
  ir.py                           typed IR (both backends and the interpreter read only this)
  builtins.py                     the standard library table
  hostapi.py                      the host protocol: reads, commands, queries, events; HostV0 / HostV1 base classes
  mockhost.py                     MockHost (recording) and SimHost (plays montages, produces events) for tests and tools
  runtime.py                      value types (Vec2/Vec3/Rotator/Quat/Transform/Hit/...) + math with UE semantics + LogicBase
  interp.py                       reference interpreter over the IR
  pybackend.py                    Python backend
  cppbackend.py  cpp/             C++ backend and its static headers (DalMath.h, DalHost.h, DalData.h)
  harness.py                      interpreter-vs-generated-Python consistency harness (states and host commands)
  api.py cli.py __main__.py       entry points
examples/
  als_lean.dal                    ALS-Refactored's lean logic (+ als_lean_standins.py, AlsLeanLibrary.h)
  hero_gait.dal                   enums, input actions, notifies, entries
  melee_combo.dal                 three-hit chain: montages, input buffer, cancel windows, combo (actor logic)
  lockon_camera.dal               lock-on target selection, line-of-sight trace, camera framing (actor logic)
als/                              ALS-Refactored rewritten in .dal (library + character + input + animation instances)
  als_lib.dal                     the shared declarations: 8 tag sets, enums, the state structs, 23 settings types (they load the data
                                  assets of G:\DCC\AlsPack) and the pure math helpers
  AlsCharacter.dal                AAlsCharacter: states, view, locomotion, gait, rotation, landing, jumping, the CMC gait settings push, and the
                                  actions of AlsCharacter_Actions.cpp: rolling, mantling (with the root motion source as a driver), get up,
                                  ragdolling, plus the three notify states they end on
  AlsInput.dal                    the example character's Enhanced Input actions
  bindings.json                   which data asset of the ALS pack each root settings param of the logics takes (the mantling kinds)
  AlsAnimationInstance.dal        UAlsAnimationInstance: layering, pose, view, spine, head, locomotion, lean, grounded, standing, crouching, in air with
                                  the ground prediction sweep, the foot lock, transitions and dynamic transitions, rotate and turn in place with their
                                  montages, the final ragdoll pose, the ControlRig input, the notifies of the instance (82 of the 82 state paths of
                                  the AnimBPs, 23 entries)
  AlsLinkedAnimationInstance.dal  the linked instance: one forwarder per `linked` entry, generated
tests/                            python -I -m unittest discover -s tests
  oracles/                        independent hand-written ports (ALS lean, melee, lock-on); they do not import dal
  cpp_stub/ cpp_driver.py         stand-in engine headers + build/run helper for the C++ tests
  cpp_system.py                   the same for several logics wired to one actor (peers, tick slots, linked instances)
  golden/                         golden C++ output
Unreal/
  DalLive/                        UE plugin: hot reload of .dal in the editor (editor-Python interpreter + a thin C++ component)
  DalRuntime/                     UE plugin: the native half of the generated code (movement component with the ALS extensions, root motion
                                  override source, ragdoll service, hook events and the event router, test character); README.md
  AlsHost/                        puts the C++ generated from als/*.dal in for ALS's own logic layer in a UE sandbox host, records the truth
                                  routes in PIE and compares them with the original ALS's takes; DalRuntime checks, the c3 scenarios; README.md
  TestHost/                       generator of the test project + PIE harness: generated C++ vs DalLive, frame by frame
```

## Command line

Run from the repository root (or with the folder that contains `dal/` on `PYTHONPATH`; note that
`python -I` ignores `PYTHONPATH` and the current directory, so use plain `python -m dal`):

```
python -m dal compile examples/als_lean.dal --py out/py --cpp out/cpp --module MYGAME_API
python -m dal compile examples/hero_gait.dal --cpp out/cpp --bag      # also U<Logic>Bag, the property-bag AnimInstance for DalLive
python -m dal check   examples/als_lean.dal
python -m dal verify  examples/melee_combo.dal --frames 500 --seed 7   # interpreter vs generated Python
python -m dal verify  prog.dal --param Cfg=cs_default.json --pack G:\DCC\AlsPack   # settings params from data files, curves from the pack
python -m dal dump-ir examples/als_lean.dal
python -m dal host                                                      # the host protocol as Markdown tables
```

* `--py DIR` writes `<Logic>.py`; `--cpp DIR` writes `<Logic>.h`, `<Logic>.cpp`, `DalMath.h` and, for programs with
  host traffic (handlers, commands, actor logic), `DalHost.h`.
  Output is deterministic (no timestamps) and files are only rewritten when the content changes, so
  Unreal's Live Coding / UBT only rebuild what changed.
* `--module MYGAME_API` puts the module's export macro on the generated `UCLASS`.
* `--parent-class UMyBase --parent-include MyBase.h` changes the C++ base class (default `UAnimInstance`, or
  `UActorComponent` for `logic X : actor;`).
* `--standins FILE` provides the Python stand-ins of `extern fn`s (default: `<source>_standins.py`).
* `--double-members`: the vector / rotator / quaternion / transform members of structs (state structs, settings, notify payloads) get the
  engine's double-precision types (`FVector`, `FRotator`, `FQuat`, `FTransform`) instead of `FVector3f` ...; the logic still computes in single
  precision (reads convert, writes convert back), top-level states / inputs / params keep theirs.  A Control Rig or a Blueprint pin made for
  ALS's own structs does not convert a `FVector3f`; the ALS rewrite is generated with it.
* The next three make settings read what an asset made with the original ALS holds (a plain struct of the same members does not load; the
  engine skips the property, or the asset does not hold a value that equals the original's constructor default, and the logic runs on the
  defaults):
  * `--object-settings NAME[,NAME]`: a settings type the assets hold as an `Instanced` `UObject` (ALS: `AlsTurnInPlaceSettings`).  It is generated as
    `U<Name>` (`EditInlineNew`), the settings types that hold it have a `TObjectPtr<U<Name>>`, the logic reads `F<Name>Value` through
    `DalValueOf(pointer)` (a null object reads as the declared defaults).
  * `--native-struct Name=FEngineType[=Header]`: a struct that is an engine type with the same members (ALS: `AlsFloatInterval=FFloatInterval=Math/Interval.h`);
    it is not declared, the engine's type is used.
  * `--channel-default Struct.Member=Channel,Channel`: the default of a `channel_set` member of a settings type, set in the constructor (ALS:
    `AlsGeneralMantlingSettings.MantlingTraceResponseChannels=WorldStatic,WorldDynamic,Destructible` -- the constructors of `UAlsCharacterSettings`
    and `UAlsAnimationInstanceSettings` set them, so no asset holds them).
  Names that the file being compiled does not declare are ignored (one option list serves every file of a system); a name that is declared but
  of the wrong kind is an error.  `Unreal/AlsHost/gen_als.py` has the list for the ALS rewrite (`ASSET_COMPAT`).
* `--bag` (anim logic): also write `U<Logic>Bag` into `<Logic>.h`, a `UAnimInstance` with the same params / inputs / states
  and none of the logic -- the parent class of an Animation Blueprint while DalLive interprets the program.
* A file without a `logic` line that only declares shared things is a library (`use "x.dal";`): it checks and compiles on
  its own (`python -m dal compile als/als_lib.dal --cpp out`) into `<Stem>.h` / `.cpp` (the types, tags, inline functions).
  `--cpp` also writes `DalData.h` when the program has settings.
* Exit code 0 = ok, 1 = compile errors (`file:line:col: error DALnnnn: message` plus the source line and a
  caret), 2 = usage error.

From Python:

```python
import dal
from dal.pybackend import generate_python, load_class
from dal.cppbackend import generate_cpp, CppOptions

result = dal.check_file("examples/hero_gait.dal")        # raises dal.DalError with all diagnostics
Hero = load_class(result.module)                         # the generated class, ready to use
hero = Hero(host)                                        # host: a dal.hostapi.HostV1 (or HostV0) object
hero.update(1 / 60)
print(hero.Speed, hero.snapshot())
files = generate_cpp(result.module, CppOptions(module_api="MYGAME_API"))   # {filename: text}
```

## The host protocol (what Blender / Unreal provide)

A logic talks to its host in four ways; all of them are listed in the tables of `dal/hostapi.py`
(`python -m dal host` prints them), the checker validates programs against those tables, and both backends and the
mock hosts implement them.

| | what | in the language | when it takes effect |
|---|---|---|---|
| reads | CMC values, actor transform, capsule, input actions, notify flags, montage state, animation curves, bone and mesh transforms, reference skeleton, the ragdoll's pelvis body (location, velocity, facing up), the consumed pre-penetration velocity, time dilation, mesh scale | `input X: type <- host.item`; the name of a bone or curve may have single spaces: `host.bone_world_transform("VB foot_l")` | sampled once at the start of an update |
| events | notify / notify window begin+end (declared ones with a payload), montage ended / blending out, input action phases, the character's hooks (mode changed, jumped, landed, crouch, teleported, physics rotation), timers | `on notify Hit { ... }`, `on landed(h) { ... }` | handlers run at the start of the next update, oldest first (`slot_events`: at the start of every tick slot) |
| commands | CMC writes, jump / crouch / launch, montage play / stop / jump to section ... (a montage value works too), dynamic montages in slots, pose snapshots, root motion override, ragdoll, control rotation, spring arm, FOV | `host.set_max_walk_speed(v);` | **queued**; the host runs them in order after the update |
| queries | line / sphere / capsule traces on a channel, overlap, walkable test, component reads, target list | `let h = host.trace_line(a, b);`, `host.sweep_capsule(a, b, r, hh, ch, blocking)` | answered immediately; the scene queries (marked in `python -m dal host`) also from an anim instance's worker thread, the rest game thread only |

Python hosts subclass `dal.hostapi.HostV1` (every method documents the DreamVEd call it maps to:
`MontageManager.play / stop / jump_to_section / set_next_section`, `CharacterMovement.jump / add_movement_input /
launch / set_movement_mode / p.max_walk_speed ...`, `CollisionWorld.line_trace / sweep_sphere`, `EnhancedInput.bind /
action_value`, the spring arm parameters). `dal.mockhost.MockHost` records commands and answers traces against a
ground plane and spheres; `SimHost` also plays montages (length, sections, notifies, notify windows, blend out) and turns
what happens into the events the logic polls -- enough to test combos without Blender or Unreal.

## Generated Python class

* `Class(host=None, **params)` (a settings param takes a JSON path, a dict or an instance; `resolver=` finds curves by name);
  `update(dt)`; `reset()`; `snapshot()` (inputs then states as plain data, struct members as `State.Member`);
  `set_input(name, value)` for free inputs; `call_entry(name, *args, **named)` / `Entry(...)` for entries (parameters by name,
  enum arguments by member name, defaults filled in); `connect(peer, other)` for peers and the `Parent` of a linked instance;
  `overrides = {"Velocity": ...}` forces a host-bound input (for a variable panel).
* One `update(dt)`: sample the inputs, collect the events (`post_event(...)` and `host.poll_events()`), tick the timers,
  run the handlers, run the body, hand the queued commands to the host (`take_commands()` returns them instead, for
  hosts that execute commands themselves).
* Metadata for UIs: `DAL_PARAMS`, `DAL_INPUTS`, `DAL_STATES`, `DAL_ENTRIES`, `DAL_ENTRY_PARAMS`, `DAL_KIND`, `DAL_HANDLERS`,
  `DAL_PEERS`, `DAL_METHODS`, `DAL_VIEWS`, `DAL_PHASES`, `DAL_ORDER`, `DAL_POST_FLUSH`, `DAL_SLOT_EVENTS`, `DAL_FLUSH_PEERS`; `post_update()` for an animation logic that has the block; a settings class has `DAL_SCHEMA` / `.load(source, resolver, report)`.
* Imports only `dal.runtime` (and `dal.hostapi` lazily): the `dal` package must be importable, which is
  why it is vendored as a whole.

## Generated C++ (UE 5.8)

Anim logic (`logic X;`, v0 shape, unchanged):

```
UCLASS() class [API] U<Logic> : public UAnimInstance
  UPROPERTY params (EditAnywhere), host-bound inputs (VisibleAnywhere), free inputs (BlueprintReadWrite),
           state (VisibleAnywhere, BlueprintReadOnly)   -- names exactly as in the .dal, so a .dab reads Main.Speed
  NativeUpdateAnimation (game thread)           -> DalSampleInputs(): owning pawn / CMC / enhanced input / world time
  NativeThreadSafeUpdateAnimation (worker)      -> DalUpdate(dt): only this object's data, params, constants, pure fns
  entries                                       -> UFUNCTION(BlueprintCallable, Meta = (BlueprintThreadSafe)) for Call(Self.Entry)
  notifies                                      -> UFUNCTION() AnimNotify_<Name>() sets a flag read by host.notify("<Name>")
```

With host traffic (handlers, `host.` commands, montage reads) an anim instance additionally queues commands under a
lock; **the next `NativeUpdateAnimation` runs them on the game thread, before it samples** (so a command takes effect one
update later than on the Python side, where `update` flushes at its end). Events from the animation delegates
(`OnMontageEnded`, `OnMontageBlendingOut`, `OnPlayMontageNotifyBegin/End`, `AnimNotify_<Name>`) are queued and handled
by the next update.

Actor logic (`logic X : actor;`):

```
UCLASS(ClassGroup = (Dal), Meta = (BlueprintSpawnableComponent)) class [API] U<Logic> : public UActorComponent
  TickComponent (TG_PrePhysics, game thread):  DalBindHost()      lazily subscribe to the owner's AnimInstance delegates and its
                                                                  UEnhancedInputComponent (BindAction per `on input` handler)
                                               DalSampleInputs()  reads, as above (+ montage state of the owner's mesh)
                                               DalTakeEvents()    events queued by the delegates since the last tick
                                               DalUpdate(dt)      timer ticks, handlers (DalDispatchEvents), update body; may call
                                                                  the queries (traces, targets)
                                               DalFlushCommands() DalExecuteCommand(): CMC / Character / montage / controller / camera calls
  assets to assign in the class defaults:  DalMontage_<Name> (UAnimMontage), DalAction_<Name> (UInputAction),
                                           DalTargetTag (default "DalTarget"), DalTraceChannel (default ECC_Visibility)
```

A `.dab` uses an anim-instance class as `Parent = U<Logic>`. Modules needed: `Core`, `CoreUObject`, `Engine`
(+ `EnhancedInput` if actions are used) -- the first lines of the generated header say so. For a
`host.action("Move")` input or an `on input` handler, assign the `UInputAction` asset to the generated
`DalAction_<Name>` property. Notifies reach actor logic through **Play Montage Notify** (point) and **Play Montage Notify
Window** (state) notifies named like the handler; an anim instance additionally receives custom `AnimNotify_<Name>` ones.
Enum names (user enums and the `<Montage>Section` enums) become `E<Name>` in the module: keep them unique across the logics
of one module.

From v3 on (`Generated C++` is described in `dal-syntax-v3.md` section 11): structs become `USTRUCT`s, tag sets native gameplay
tags (`FGameplayTag`), `settings` types `UDataAsset` classes (`U<Name>`, `TObjectPtr` param with the class defaults as fallback, derived
members recomputed in `PostLoad`) or nested `USTRUCT`s, curves `TObjectPtr<UCurveFloat/UCurveVector>`, tables and lists `TMap` /
`TArray`; `method` / `view` / `entry` become member functions (`pub` ones `UFUNCTION`s with the thread-safe meta for anim
instances); a peer is a `TObjectPtr<U<Logic>>` found when play begins and a call to an anim instance goes through a queue;
`update(dt) in <slot>` blocks get their own `FActorComponentTickFunction` with prerequisites; a `linked(Main)` anim logic gets
`Parent` / `Character` / `GetParent()` and a forwarder per `linked` entry. Libraries share one header per file, the enum name
clash of several logics in one module disappears for enums declared in a library. Two modules are assumed that stock UE does not
have: `DalRuntime` (`UDalCharacterMovementComponent::TryConsumePrePenetrationAdjustmentVelocity`, only for that read).

`extern fn` declarations call hand-written C++ (`cpp "Ns::Fn" include "Header.h"`); see
`examples/AlsLeanLibrary.h` for the pattern. The functions must be pure and thread safe.

## Hot reload in the Unreal editor (`Unreal/DalLive`)

Edit a `.dal` file, see the change in PIE a quarter of a second later, without UBT or Live Coding. The plugin runs the program
with `dal.interp` in the editor's Python through a thin C++ component (`UDalInterpHost`, same tick group and order as the
generated one), swaps a changed file in while keeping the state whose name and type still match, reports compile errors
(log, Message Log, toast) and keeps running the last good version. `logic X : actor;` is interpreted on the component;
`logic X;` writes the AnimInstance's properties by name (use `--bag`). Verified in PIE (UE 5.8.2) frame by frame against the
generated C++; cost 86 us per tick for the three-hit combo (3.7 us compiled). `Unreal/DalLive/README.md` has the use, the
rules and the numbers, `Unreal/TestHost/README.md` the test project. Since 0.4.0 the session also runs an animation logic's `post_update`
block (right after the update; its commands wait for the next one like the others). The live host (`UeHost`) implements the commands the editor's
Python API reaches; hook events, declared-notify payloads, `snapshot_pose`, `stop_montages_with_slot`, root motion override, the ragdoll
commands and the montage asset queries are not available in the editor session (`dal-syntax-v3.md` section 25 lists them).

## Vendoring (DreamVEd and DreamAnim)

Both consumers carry **the same version of the package folder**:

* DreamVEd: `blender-moon-engine/scripts/modules/dal`
* DreamAnim: `DreamAnim/Tools/dal`

Copy the `dal/` directory (including `dal/cpp/DalMath.h`, `dal/cpp/DalHost.h` and `dal/cpp/DalData.h`, excluding `__pycache__`);
do not copy `tests/` or `examples/`. `dal.__version__` identifies the version (0.4.0); the generated files record it in their first
line. Blender calls `dal.check_file(...)` + `load_class(...)`; Unreal's editor Python calls
`dal.cli.main(["compile", file, "--cpp", dir, "--module", API])` (or `python -m dal`). The compiler does
not depend on Unreal or on the project directory. Programs written for 0.1.0 to 0.3.0 compile unchanged (their generated C++ is
identical apart from the version in the banner; the goldens in `tests/golden` changed by that line only; the one addition is a public
`DalGetPhaseTick` in the logics that have tick slots). Version 0.3.0 changed one behaviour of a v0 program: assigning to a vector component
(`v.x = 1.0;`) is allowed instead of error DAL2010. Version 0.4.0 makes `while` and `break` keywords (they were reserved words) and allows a
value-returning method call inside an expression (it was DAL4042 everywhere but the whole right-hand side).

## Tests

```
python -I -m unittest discover -s tests
```

1128 tests (about 10 minutes; the C++ compile tests are most of it): 608 at 0.3.0, 379 when DalLive was added, 184 of them from v0 unchanged. Python 3.11+ syntax; also run under Blender 5.2's embedded Python 3.13.

| File | What it checks |
|---|---|
| `test_syntax.py` | lexer and parser, error positions |
| `test_check.py` | name resolution, types, diagnostics (codes + line/column), IR shape, stand-in checks |
| `test_runtime.py` | the math library against hand-derived UE semantics |
| `test_pybackend.py` | the generated class: API, params, overrides, entries, integer / division semantics |
| `test_consistency.py` | interpreter vs generated Python, frame by frame: both v0 examples, **every built-in signature**, operators, control flow, entries, externs, plus a mutation check that proves the comparison can fail |
| `test_v1.py` | v1 / v2 language: diagnostics, commands (queued, flushed, plain arguments), events, timers, windows, buffers, montage reads, queries, loops, random-frame consistency of interpreter and Python backend |
| `test_host_protocol.py` | the protocol tables vs `HostV1` / `MockHost` / `SimHost` (arity, documentation, simulation) |
| `test_als_lean.py` | `examples/als_lean.dal` vs the independent port of ALS's lean code (`oracles/`) over random sequences |
| `test_melee.py` | `examples/melee_combo.dal` vs `oracles/melee_oracle.py`: scripted sessions through the montage simulation and 30 random sessions (states and commands per frame) |
| `test_lockon.py` | `examples/lockon_camera.dal` vs `oracles/lockon_oracle.py`: scenarios and 24 random sessions |
| `test_cpp.py` | golden C++ text of the v0 examples, structural invariants (worker-thread code never touches the world), `DalMath.h` has an overload for every built-in signature |
| `test_cpp_v1.py` | golden C++ text of the two actor examples and an anim instance with host traffic; invariants of the component (tick order, queues, delegates, input binding, executors) |
| `test_cpp_compile.py` | compiles the generated C++ (MSVC `/W4 /WX`, or g++/clang++) against stand-in engine headers (`tests/cpp_stub`) and compares states **and the host effects** (CMC writes, montage calls ...) with the Python backend, for the v0 examples, both actor examples, every host command, traces / targets, an anim instance with events and **every built-in signature** (`everything_source`); skipped when no compiler is found. `run_case(..., params=...)` feeds settings data to both sides |
| `test_cli.py` | `python -m dal` |
| `test_cpp_bag.py` | the property bag (`--bag`): same UPROPERTYs as the main class, no logic, off by default (other output byte-identical) |
| `test_use.py` (21) | libraries: loading, merging, cycles, root escape, diagnostics that point into the library, one shared C++ header, a library compiling alone |
| `test_asset_compat.py` (12) | `--object-settings`, `--native-struct`, `--channel-default`: the generated text, the restoration of the types after a generation, errors, the stand-in engine compile and run against Python with object-held settings |
| `test_struct.py` (22), `test_tags.py` (13) | structs (defaults, construction by name, value semantics, dotted-path snapshots, library sharing) and tag sets: checker, interpreter vs Python, C++ compile-and-run |
| `test_methods.py` (18), `test_peers.py` (23), `test_cpp_system.py` (13) | methods, views, call statements, `begin_play`, peers (sampled reads, direct and queued calls), tick slots; the three-logic ALS-shaped system in all three implementations (several logics wired to one stand-in actor) |
| `test_settings.py` (60) | settings / curves / tables / lists / asset values: diagnostics, the loader (every spelling, error paths, resolvers), FRichCurve evaluation by hand, random settings data through interpreter, Python and C++, the real ALS pack (`G:\DCC\AlsPack`, skipped when absent), `als_lib.dal` in all three |
| `test_entries.py` (22) | entry parameters and defaults, `linked` instances and their forwarders (system run in Python and C++) |
| `test_host_reads.py` (17), `test_host_mesh.py` (10) | the v3 host reads: capsule, curves, consume-on-read, bones, mesh transform, reference skeleton, root motion mode |
| `test_math3.py` (43) | component assignment, quaternion / vector / spring / easing built-ins by hand, `transform` (also against the animation runtime of the Blender host when it is on the machine) |
| `test_queries.py` (23) | scene queries with channels, the extended hit, primitives, the thread-safe set; mock host vs stand-in engine scene |
| `test_als_actions.py` (40), `als_world.py` | the actions of `AlsCharacter.dal`: rolling, mantling against a scene of boxes (a wall, a ledge, a ceiling) with a movement component that obeys the commands, get up and ragdolling against the host's `ragdoll_*` commands and pelvis bone, the notify states they end on; interpreter and generated Python play the same scenarios |
| `test_als_truth.py` (6) | the ten truth takes of the UE recorder (`G:\DCC\AlsTruth`) through the T2 teacher of the Blender side: none of the 39 state fields of the character differs in any frame of any take; the mantling driver against `Cmc.Velocity` of the mantle take (skipped when the takes, the pack or the DreamVEd modules are not there) |
| `test_als_character.py` (64) | `als/`: every function cites its ALS line; the library's structs / settings against the C++ headers; the pack's settings load without a warning; the library math against the UE 5.8 goldens (`als/goldens`); interpreter / Python / C++ (stand-in engine) agree on random frames (hooks of the movement component, root motion mode, a ragdoll or none in the host); gait, rotation, stance, landing, jump and input behaviour against the C++ semantics. `als_host_v2.py` was the shim that registered the host commands `hostapi.py` did not list yet; the compiler has them now (0.4.0) and the shim only carries the settings data of the system driver |
| `test_als_anim.py` (11), `test_als_anim_scenarios.py` (110), `test_als_contract.py` (30) | `als/AlsAnimationInstance.dal`: against the independent port `oracles/als_anim_oracle.py` over random character / pose / curve / notify sequences (every state and every host command of every frame; 24 sequences, counters that prove the branches were reached, mutation checks), hand-checked scenarios (foot lock with both limits, teleports, dynamic transitions, the ground prediction against spheres and a plane, turn in place, notifies, time dilation, the pack's real curves), interpreter / Python / C++ agree on random and on character-like frames, the 82 state paths and 17 entries the AnimBPs use against the `.dab` corpus and the ALS headers |
| `test_als_anim_truth.py` (5) | the same instance teacher forced against the recordings of the original ALS in Unreal (`G:\DCC\AlsTruth`, `truth_als_anim.py`): every frame starts from the recorded state and the recorded inputs, the result is compared with the recorded end state (foot lock: every leaf of both feet; ground prediction: the sweep it asks and its amount; rotate / turn in place; the montage commands). Skipped without the recordings |
| `test_timer_hooks.py` (26), `test_notify.py` (20), `test_post_update.py` (20) | 0.4.0: `on timer`, the character's hook events, notify declarations with payloads (branching, ticks), `post_update` / `post_flush` and the animation instance proxy; interpreter vs Python on random frames, C++ compile-and-run |
| `test_montage_commands.py` (38), `test_character_commands.py` (19), `test_character_gaps.py` (23) | 0.4.0: dynamic montages / slots / sources / pose snapshots, montage values as command arguments, the 17 character commands of the ALS rewrite, new host reads, settings values in state, a peer's settings member in C++, long C++ runs |
| `test_loops.py` (19), `test_root_motion.py` (19), `test_slot_order.py` (17), `test_consume_slots.py` (8), `test_small_additions.py` (14), `test_method_calls_in_expressions.py` (11) | 0.4.0: `break` / `while`, root motion override and montage asset queries, `slot_events` and `before` / `after` (system runs against the C++), consume-on-read sampling per slot, rotator arithmetic / `valid(Peer)` / settings member initialisers, method calls inside expressions |
| `test_host_names.py` | host names with spaces (`"VB foot_l"`) for bones and curves in the checker, both Python implementations and C++; a `notify` or action name stays an identifier |
| `test_ragdoll_reads.py` | the ragdoll reads `ragdoll_pelvis_location`, `ragdoll_velocity`, `ragdoll_facing_up` (what ALS reads off the pelvis body): checker, both Python implementations, C++ against the stand-in engine, and the `UDalRagdollService` getters the generated code calls |
| `test_flush_peers.py` | `logic X : actor flush_peers`: the host commands a peer queued in the calls of a block run with the caller's own at the end of its block, in the caller's tick slot (interpreter, Python and C++ against the stand-in engine agree); without the option a peer's commands wait for its own block |
| `test_live_session.py` | DalLive's pure-Python half with the mock hosts: which state survives a swap, compile errors keep the last good program, debounce, runtime faults, anim commands one update late, param overrides, compiler lookup |

Regenerate golden files with `UPDATE_GOLDEN=1 python -I -m unittest discover -s tests -p test_cpp.py` (and
`-p test_cpp_v1.py`) and review the diff. `DAL_CL=<path to cl.exe>` selects a specific MSVC for `test_cpp_compile.py`.

## What is not verified

* The generated C++ was compiled in UE 5.8.2 and run in PIE (2026-10-21, `Unreal/TestHost`): `melee_combo`, `lockon_camera` and
  `hero_gait` behave like the interpreter frame by frame, which settles what the stand-in headers could not: UHT accepts the
  private delegate targets with a `const FBranchingPointNotifyPayload&` parameter, `AddUniqueDynamic` works on
  `OnPlayMontageNotifyBegin/End`, the `BindAction` overload, `SCENE_QUERY_STAT` in a component and the `FVector3f` / `FQuat4f`
  conversions all compile and behave. Not covered by those runs: several logics with same-named enums in one module, the custom
  `AnimNotify_<Name>()` functions of anim logic, a cooked / packaged build.
* v3 / 0.4.0 was compiled in UE 5.8.2 and run in PIE on 2026-10-25 (`Unreal/AlsHost`, `Unreal/DalRuntime`): the generated ALS (`als/*.dal`, about
  7.4k lines) replaced ALS-Refactored's logic layer in a sandbox host and was compared with ten recordings of the original.  UHT rejected two things (a
  lambda as a default member initializer, `TObjectPtr` in a `UFUNCTION` signature); loading and running found a Control Rig that does not accept
  `FVector3f` members (`--double-members`) and a linked animation instance whose parent stayed null (the engine updates it through its proxy, so the
  parent is looked up again in the thread-safe update).  Comparing the settings assets as the engine loads them (original vs generated classes) found
  three more: the turn-in-place settings are objects, the mantling / ground prediction channels are constructor defaults, `FFloatInterval` is the
  engine's (the three options above).  `dal-syntax-v3.md` section 32 has the numbers.  Not covered: a cooked / packaged build, several logics with
  same-named enums in one module, the ragdoll against the original (no ragdoll in the truth takes).
* Settings in C++ are filled by the engine's property system (`.uasset`); the JSON loader is Python only. A response container
  (`FCollisionResponseContainer`) in exported data is read as the list of channels that block; curves are `UCurveFloat` /
  `UCurveVector` objects.
* Float precision: the Python backend computes in double, the C++ backend in `float`. Programs with hard
  thresholds can differ by a frame at the boundary. (A float32 mode for the Python backend is not implemented: it would have to
  round after every operation; the tests compare with tolerances instead.)
* `to_rotator()` uses an exact `asin`; UE's float `FQuat::Rotator()` uses `FMath::FastAsin` (error up to
  about 7e-5 rad). `atan2()` likewise uses the C library; UE's float version is a minimax approximation
  with relative error below 1e-6.
* The Blender side is an interface plus mocks: no `HostV1` implementation on top of the real DreamVEd runtime exists yet.
