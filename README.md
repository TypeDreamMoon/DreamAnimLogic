# DreamAnimLogic -- the `.dal` compiler

`.dal` (Dream Anim Logic) is a small logic language for animation and game logic. **One Python
compiler, two backends**:

| Backend | Output | Runs in |
|---|---|---|
| Python | a self-contained module with one class (`__init__(host)`, `update(dt)`, state fields) | Blender (DreamVEd), tests, tools |
| Unreal C++ | `U<Logic>.h/.cpp`: a `UAnimInstance` parent class + `DalMath.h` | Unreal Engine 5.8, as the C++ parent of a `.dab` Animation Blueprint |

The language is specified in `I:\DCC\Plan\dal-syntax-v0.md` (Chinese). This README covers the repository.

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

## Layout

```
dal/                  the package (pure Python 3.11+, no dependencies) -- this is what gets vendored
  lexer.py parser.py syntax.py    source -> syntax tree (errors with line/column)
  check.py names.py types.py      name resolution, static types, constant folding -> IR
  ir.py                           typed IR (both backends and the interpreter read only this)
  builtins.py hostapi.py          the standard library table and host interface v0
  runtime.py                      value types (Vec2/Vec3/Rotator/Quat) + math with UE semantics
  interp.py                       reference interpreter over the IR
  pybackend.py                    Python backend
  cppbackend.py  cpp/DalMath.h    C++ backend and its static math header
  harness.py                      interpreter-vs-generated-Python consistency harness
  api.py cli.py __main__.py       entry points
examples/
  als_lean.dal                    ALS-Refactored's lean logic (+ als_lean_standins.py, AlsLeanLibrary.h)
  hero_gait.dal                   enums, input actions, notifies, entries
tests/                            python -I -m unittest discover -s tests
  oracles/als_lean_oracle.py      independent port of ALS's lean code (does not import dal)
  cpp_stub/ cpp_driver.py         stand-in engine headers + build/run helper for the C++ tests
  golden/                         golden C++ output
```

## Command line

Run from the repository root (or with the folder that contains `dal/` on `PYTHONPATH`; note that
`python -I` ignores `PYTHONPATH` and the current directory, so use plain `python -m dal`):

```
python -m dal compile examples/als_lean.dal --py out/py --cpp out/cpp --module MYGAME_API
python -m dal check   examples/als_lean.dal
python -m dal verify  examples/als_lean.dal --frames 500 --seed 7   # interpreter vs generated Python
python -m dal dump-ir examples/als_lean.dal
```

* `--py DIR` writes `<Logic>.py`; `--cpp DIR` writes `<Logic>.h`, `<Logic>.cpp` and `DalMath.h`.
  Output is deterministic (no timestamps) and files are only rewritten when the content changes, so
  Unreal's Live Coding / UBT only rebuild what changed.
* `--module MYGAME_API` puts the module's export macro on the generated `UCLASS`.
* `--parent-class UMyBase --parent-include MyBase.h` changes the C++ base class (default `UAnimInstance`).
* `--standins FILE` provides the Python stand-ins of `extern fn`s (default: `<source>_standins.py`).
* Exit code 0 = ok, 1 = compile errors (`file:line:col: error DALnnnn: message` plus the source line and a
  caret), 2 = usage error.

From Python:

```python
import dal
from dal.pybackend import generate_python, load_class
from dal.cppbackend import generate_cpp, CppOptions

result = dal.check_file("examples/hero_gait.dal")        # raises dal.DalError with all diagnostics
Hero = load_class(result.module)                         # the generated class, ready to use
hero = Hero(host)                                        # host: object with the attributes of dal.hostapi.HOST_ITEMS
hero.update(1 / 60)
print(hero.Speed, hero.snapshot())
files = generate_cpp(result.module, CppOptions(module_api="MYGAME_API"))   # {filename: text}
```

## Generated Python class

* `Class(host=None, **params)`; `update(dt)`; `reset()`; `snapshot()` (inputs then states as plain data);
  `set_input(name, value)` for free inputs; `call_entry(name)` / `Entry()` for entries;
  `overrides = {"Velocity": ...}` forces a host-bound input (for a variable panel).
* Metadata for UIs: `DAL_PARAMS`, `DAL_INPUTS`, `DAL_STATES`, `DAL_ENTRIES`.
* The host is any object with the attributes listed in `dal.hostapi.HOST_ITEMS` (`velocity`, `acceleration`,
  `is_grounded`, ... and the methods `action(name, type)` and `notify(name)`); subclass `dal.hostapi.HostV0`
  to get neutral defaults for everything you do not provide.
* Imports only `dal.runtime` (and `dal.hostapi` lazily): the `dal` package must be importable, which is
  why it is vendored as a whole.

## Generated C++ (UE 5.8)

```
UCLASS() class [API] U<Logic> : public UAnimInstance
  UPROPERTY params (EditAnywhere), host-bound inputs (VisibleAnywhere), free inputs (BlueprintReadWrite),
           state (VisibleAnywhere, BlueprintReadOnly)   -- names exactly as in the .dal, so a .dab reads Main.Speed
  NativeUpdateAnimation (game thread)           -> DalSampleInputs(): owning pawn / CMC / enhanced input / world time
  NativeThreadSafeUpdateAnimation (worker)      -> DalUpdate(dt): only this object's data, params, constants, pure fns
  entries                                       -> UFUNCTION(BlueprintCallable, Meta = (BlueprintThreadSafe)) for Call(Self.Entry)
  notifies                                      -> UFUNCTION() AnimNotify_<Name>() sets a flag read by host.notify("<Name>")
```

A `.dab` uses the generated class as `Parent = U<Logic>`. Modules needed: `Core`, `CoreUObject`, `Engine`
(+ `EnhancedInput` if `host.action` is used) -- the first lines of the generated header say so. For a
`host.action("Move")` input, assign the `UInputAction` asset to the generated `DalAction_Move` property in
the class defaults.

`extern fn` declarations call hand-written C++ (`cpp "Ns::Fn" include "Header.h"`); see
`examples/AlsLeanLibrary.h` for the pattern. The functions must be pure and thread safe.

## Vendoring (DreamVEd and DreamAnim)

Both consumers carry **the same version of the package folder**:

* DreamVEd: `blender-moon-engine/scripts/modules/dal`
* DreamAnim: `DreamAnim/Tools/dal`

Copy the `dal/` directory (including `dal/cpp/DalMath.h`, excluding `__pycache__`); do not copy `tests/`
or `examples/`. `dal.__version__` identifies the version; the generated files record it in their first
line. Blender calls `dal.check_file(...)` + `load_class(...)`; Unreal's editor Python calls
`dal.cli.main(["compile", file, "--cpp", dir, "--module", API])` (or `python -m dal`). The compiler does
not depend on Unreal or on the project directory.

## Tests

```
python -I -m unittest discover -s tests
```

180+ tests, about 15 s. Python 3.11+ syntax; also run under Blender 5.2's embedded Python 3.13.

| File | What it checks |
|---|---|
| `test_syntax.py` | lexer and parser, error positions |
| `test_check.py` | name resolution, types, diagnostics (codes + line/column), IR shape, stand-in checks |
| `test_runtime.py` | the math library against hand-derived UE semantics |
| `test_pybackend.py` | the generated class: API, params, overrides, entries, integer / division semantics |
| `test_consistency.py` | interpreter vs generated Python, frame by frame: both examples, **every built-in signature**, operators, control flow, entries, externs, plus a mutation check that proves the comparison can fail |
| `test_als_lean.py` | `examples/als_lean.dal` vs the independent port of ALS's lean code (`oracles/`) over random sequences |
| `test_cpp.py` | golden C++ text, structural invariants (worker-thread code never touches the world), `DalMath.h` has an overload for every built-in signature |
| `test_cpp_compile.py` | compiles the generated C++ (MSVC `/W4 /WX`, or g++/clang++) against stand-in engine headers (`tests/cpp_stub`) and compares the results with the Python backend; skipped when no compiler is found |
| `test_cli.py` | `python -m dal` |

Regenerate golden files with `UPDATE_GOLDEN=1 python -I -m unittest discover -s tests -p test_cpp.py`
and review the diff. `DAL_CL=<path to cl.exe>` selects a specific MSVC for `test_cpp_compile.py`.

## What is not verified

* The generated C++ has **not** been compiled inside Unreal Engine. `test_cpp_compile.py` proves it is
  well-formed C++20 and numerically consistent against stand-in headers written from the UE 5.8 sources;
  engine-specific facts (UHT acceptance of the `UPROPERTY` set, `FVector3f`/`FQuat4f` conversions from the
  double-precision `FVector`/`FQuat`, `UEnhancedPlayerInput::GetActionValue`, module dependencies) still
  need the first real build.
* Float precision: the Python backend computes in double, the C++ backend in `float`. Programs with hard
  thresholds can differ by a frame at the boundary.
* `to_rotator()` uses an exact `asin`; UE's float `FQuat::Rotator()` uses `FMath::FastAsin` (error up to
  about 7e-5 rad). `atan2()` likewise uses the C library; UE's float version is a minimax approximation
  with relative error below 1e-6.
