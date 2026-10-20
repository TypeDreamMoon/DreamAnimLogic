"""Build and run generated DAL C++ against the stand-in engine headers in tests/cpp_stub.

``build_and_run(module, frames)`` writes the generated sources, a driver that replays the frames through
``NativeUpdateAnimation`` / ``NativeThreadSafeUpdateAnimation`` (and the entries), compiles everything
with whatever C++ compiler is available and returns the per-frame states printed by the program in the
same layout as ``LogicBase.snapshot()``.  ``find_compiler()`` returns None when there is none.
"""

from __future__ import annotations

import functools
import glob
import os
import shutil
import subprocess
import tempfile

from dal import ir
from dal.cppbackend import CppOptions, generate_cpp
from dal.types import BOOL, FLOAT, INT, MOVEMENT_MODE, QUAT, ROTATOR, VEC2, VEC3, EnumType

HERE = os.path.dirname(os.path.abspath(__file__))
STUB = os.path.join(HERE, "cpp_stub")

FORWARDING_HEADERS = [
    "Animation/AnimInstance.h", "Engine/EngineTypes.h", "GameFramework/Character.h",
    "GameFramework/CharacterMovementComponent.h", "GameFramework/Pawn.h", "Engine/World.h",
    "Engine/LocalPlayer.h", "GameFramework/PlayerController.h", "EnhancedInputSubsystems.h",
    "EnhancedPlayerInput.h", "InputAction.h", "InputActionValue.h", "stub_empty.h",
]


@functools.cache
def find_compiler():
    """('msvc', env, cl_path) or ('gcc', None, path) or None."""
    override = os.environ.get("DAL_CL")
    candidates = []
    if override:
        candidates.append(override)
    found = shutil.which("cl")
    if found:
        candidates.append(found)
    for root in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""), r"D:\Program Files\VisualStudio", r"C:\Program Files\Microsoft Visual Studio"):
        if root:
            candidates += sorted(glob.glob(os.path.join(root, "*", "*", "VC", "Tools", "MSVC", "*", "bin", "Hostx64", "x64", "cl.exe")), reverse=True)
    for cl in candidates:
        env = _msvc_env(cl)
        if env is not None:
            return ("msvc", env, cl)
    for name in ("g++", "clang++"):
        p = shutil.which(name)
        if p:
            return ("gcc", None, p)
    return None


def _msvc_env(cl):
    if not os.path.isfile(cl):
        return None
    msvc_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(cl))))
    inc, lib = [os.path.join(msvc_root, "include")], [os.path.join(msvc_root, "lib", "x64")]
    if not os.path.isdir(inc[0]):
        return None
    kits = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Windows Kits", "10")
    versions = sorted((d for d in glob.glob(os.path.join(kits, "Include", "*")) if os.path.isdir(os.path.join(d, "ucrt"))), reverse=True)
    if not versions:
        return None
    ver = os.path.basename(versions[0])
    for sub in ("ucrt", "um", "shared"):
        inc.append(os.path.join(kits, "Include", ver, sub))
    for sub in ("ucrt", "um"):
        lib.append(os.path.join(kits, "Lib", ver, sub, "x64"))
    env = dict(os.environ)
    env["INCLUDE"] = os.pathsep.join(inc)
    env["LIB"] = os.pathsep.join(lib)
    env["PATH"] = os.path.dirname(cl) + os.pathsep + env.get("PATH", "")
    return env


def _num(x):
    return repr(float(x))


def _lit(ty, v):
    """C++ literal for a harness value of DAL type ``ty`` (floats as double expressions)."""
    if ty is FLOAT:
        return f"{_num(v)}f"
    if ty is INT:
        return str(int(v))
    if ty is BOOL:
        return "true" if v else "false"
    if ty is VEC2:
        return f"FVector2f({_num(v[0])}f, {_num(v[1])}f)"
    if ty is VEC3:
        return f"FVector3f({_num(v[0])}f, {_num(v[1])}f, {_num(v[2])}f)"
    if ty is ROTATOR:
        return f"FRotator3f({_num(v[0])}f, {_num(v[1])}f, {_num(v[2])}f)"
    if ty is QUAT:
        return f"FQuat4f({_num(v[0])}f, {_num(v[1])}f, {_num(v[2])}f, {_num(v[3])}f)"
    if ty is MOVEMENT_MODE:
        return f"static_cast<EMovementMode>({int(v)})"
    if isinstance(ty, EnumType):
        return f"static_cast<{ty.cpp_type}>({int(v)})"
    raise ValueError(ty)


def _print_value(name, ty):
    """C++ statement printing ``Obj.name`` as 'name v0 v1 ...' (nan / inf spelled out)."""
    ref = f"Obj.{name}"
    if ty is FLOAT:
        comps = [ref]
    elif ty is INT or ty is BOOL:
        comps = [ref]
    elif ty is VEC2:
        comps = [f"{ref}.X", f"{ref}.Y"]
    elif ty is VEC3:
        comps = [f"{ref}.X", f"{ref}.Y", f"{ref}.Z"]
    elif ty is ROTATOR:
        comps = [f"{ref}.Pitch", f"{ref}.Yaw", f"{ref}.Roll"]
    elif ty is QUAT:
        comps = [f"{ref}.X", f"{ref}.Y", f"{ref}.Z", f"{ref}.W"]
    elif ty is MOVEMENT_MODE:
        comps = [f"static_cast<int>(static_cast<EMovementMode>({ref}))"]
    elif isinstance(ty, EnumType):
        comps = [f"static_cast<int>({ref})"]
    else:
        raise ValueError(ty)
    return f'Pv("{name}", {{{", ".join(f"static_cast<double>({c})" for c in comps)}}});'


def make_driver(module: ir.Module, frames: list[dict]) -> str:
    L = [
        '#include "StubEngine.h"',
        f'#include "{module.name}.h"',
        "",
        "#include <initializer_list>",
        "",
        "static void Pv(const char* Name, std::initializer_list<double> Values)",
        "{",
        '	std::printf("%s", Name);',
        "	for (double V : Values)",
        "	{",
        '		if (V != V) { std::printf(" nan"); }',
        '		else if (V > 1.7e308) { std::printf(" inf"); }',
        '		else if (V < -1.7e308) { std::printf(" -inf"); }',
        '		else { std::printf(" %.17g", V); }',
        "	}",
        '\tstd::printf("\\n");',
        "}",
        "",
        "int main()",
        "{",
        f"\tU{module.name} Obj;",
        "\tUWorld World;",
        "\tACharacter Pawn;",
        "\tUCharacterMovementComponent Move;",
        "\tAPlayerController Controller;",
        "\tULocalPlayer LocalPlayer;",
        "\tUEnhancedInputLocalPlayerSubsystem Subsystem;",
        "\tUEnhancedPlayerInput PlayerInput;",
        "\tPawn.StubMovement = &Move;",
        "\tPawn.StubController = &Controller;",
        "\tController.StubLocalPlayer = &LocalPlayer;",
        "\tLocalPlayer.StubSubsystem = &Subsystem;",
        "\tSubsystem.StubInput = &PlayerInput;",
        "\tObj.StubPawn = &Pawn;",
        "\tObj.StubWorld = &World;",
    ]
    actions = []
    for i in module.inputs:
        if i.host and i.host.item == "action" and i.host.arg not in actions:
            actions.append(i.host.arg)
    for a in actions:
        L.append(f"\tUInputAction Action_{a};")
        L.append(f"\tObj.DalAction_{a} = &Action_{a};")
    L.append("\tObj.NativeInitializeAnimation();")
    for n, f in enumerate(frames):
        L.append(f"\t// ---- frame {n}")
        L.append("\t{")
        if "time" in f:
            L.append(f"\t\tWorld.Time = {_num(f['time'])};")
        simple = {
            "velocity": lambda v: f"Move.Velocity = FVector({_num(v[0])}, {_num(v[1])}, {_num(v[2])});",
            "acceleration": lambda v: f"Move.StubAcceleration = FVector({_num(v[0])}, {_num(v[1])}, {_num(v[2])});",
            "max_acceleration": lambda v: f"Move.StubMaxAcceleration = {_num(v)}f;",
            "max_braking_deceleration": lambda v: f"Move.StubMaxBrakingDeceleration = {_num(v)}f;",
            "max_walk_speed": lambda v: f"Move.MaxWalkSpeed = {_num(v)}f;",
            "movement_mode": lambda v: f"Move.MovementMode = static_cast<EMovementMode>({int(v)});",
            "is_grounded": lambda v: f"Move.StubGrounded = {'true' if v else 'false'};",
            "is_crouching": lambda v: f"Move.StubCrouching = {'true' if v else 'false'};",
            "actor_rotation": lambda v: f"Pawn.StubQuat = FQuat({_num(v[0])}, {_num(v[1])}, {_num(v[2])}, {_num(v[3])});",
            "control_rotation": lambda v: f"Pawn.StubControlRotation = FRotator({_num(v[0])}, {_num(v[1])}, {_num(v[2])});",
        }
        for key, fmt in simple.items():
            if key in f:
                L.append("\t\t" + fmt(f[key]))
        by_name = {i.host.arg: i for i in module.inputs if i.host and i.host.item == "action"}
        for a, v in f.get("actions", {}).items():
            if a not in by_name:
                continue
            ty = by_name[a].ty
            comps = [v] if ty in (BOOL, FLOAT) else list(v)
            comps = [float(c) for c in comps] + [0.0] * (3 - len(comps))
            L.append(f"\t\tPlayerInput.Values[&Action_{a}] = FInputActionValue({_num(comps[0])}, {_num(comps[1])}, {_num(comps[2])});")
        for name, v in f.get("free", {}).items():
            inp = module.find_input(name)
            L.append(f"\t\tObj.{name} = {_lit(inp.ty, v)};")
        for name, fired in f.get("notifies", {}).items():
            if fired and any(i.host and i.host.item == "notify" and i.host.arg == name for i in module.inputs):
                L.append(f"\t\tObj.AnimNotify_{name}();")
        dt = _num(f["dt"])
        L.append(f"\t\tObj.NativeUpdateAnimation({dt}f);")
        L.append(f"\t\tObj.NativeThreadSafeUpdateAnimation({dt}f);")
        for call in f.get("calls", []):
            L.append(f"\t\tObj.{call}();")
        L.append(f'\t\tstd::printf("FRAME {n}\\n");')
        for i in module.inputs:
            L.append("\t\t" + _print_value(i.name, i.ty))
        for s in module.states:
            L.append("\t\t" + _print_value(s.name, s.ty))
        L.append("\t}")
    L.append("\treturn 0;")
    L.append("}")
    return "\n".join(L) + "\n"


def parse_output(module: ir.Module, text: str) -> list[dict]:
    """Rebuild snapshots (plain tuples / numbers) from the driver's output."""
    types = {i.name: i.ty for i in module.inputs}
    types.update({s.name: s.ty for s in module.states})
    frames: list[dict] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "FRAME":
            frames.append({})
            continue
        name, vals = parts[0], parts[1:]
        ty = types[name]
        if ty is FLOAT:
            frames[-1][name] = float(vals[0])
        elif ty is INT or isinstance(ty, EnumType):
            frames[-1][name] = int(vals[0])
        elif ty is BOOL:
            frames[-1][name] = bool(int(vals[0]))
        else:
            frames[-1][name] = tuple(float(v) for v in vals)
    return frames


class CompileFailed(Exception):
    pass


def build_and_run(module: ir.Module, frames: list[dict], extra_include: str | None = None, options: CppOptions | None = None,
                  keep_dir: str | None = None, warnings_as_errors: bool = False, defines: list[str] | None = None):
    """Compile the generated C++ with the stand-in engine and run the driver; returns (states, compiler_output)."""
    compiler = find_compiler()
    if compiler is None:
        raise RuntimeError("no C++ compiler")
    kind, env, path = compiler
    work = keep_dir or tempfile.mkdtemp(prefix="dal_cpp_")
    try:
        gen, inc = os.path.join(work, "gen"), os.path.join(work, "inc")
        os.makedirs(gen, exist_ok=True)
        os.makedirs(inc, exist_ok=True)
        for name, text in generate_cpp(module, options or CppOptions()).items():
            with open(os.path.join(gen, name), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        for name in ("CoreMinimal.h", "StubEngine.h"):
            shutil.copy(os.path.join(STUB, name), os.path.join(inc, name))
        for name in FORWARDING_HEADERS:
            target = os.path.join(inc, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="\n") as f:
                f.write('#pragma once\n#include "StubEngine.h"\n' if name != "stub_empty.h" else "// intentionally empty\n")
        with open(os.path.join(gen, f"{module.name}.generated.h"), "w", encoding="utf-8", newline="\n") as f:
            f.write("// intentionally empty\n")
        driver = os.path.join(work, "driver.cpp")
        with open(driver, "w", encoding="utf-8", newline="\n") as f:
            f.write(make_driver(module, frames))
        exe = os.path.join(work, "run.exe" if kind == "msvc" else "run")
        includes = [inc, gen] + ([os.path.abspath(extra_include)] if extra_include else [])
        src = os.path.join(gen, f"{module.name}.cpp")
        if kind == "msvc":
            cmd = [path, "/nologo", "/std:c++20", "/EHsc", "/W4", "/DDAL_STUB_SUPER=UAnimInstance", *[f"/D{d}" for d in (defines or [])], *[f"/I{d}" for d in includes],
                   driver, src, f"/Fe:{exe}", f"/Fo:{work}\\"]
            if warnings_as_errors:
                cmd.insert(5, "/WX")
        else:
            cmd = [path, "-std=c++20", "-Wall", "-Wextra", "-DDAL_STUB_SUPER=UAnimInstance", *[f"-D{d}" for d in (defines or [])], *[f"-I{d}" for d in includes], driver, src, "-o", exe]
            if warnings_as_errors:
                cmd.append("-Werror")
        build = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True, errors="replace")
        output = build.stdout + build.stderr
        if build.returncode != 0:
            raise CompileFailed(output)
        run = subprocess.run([exe], cwd=work, capture_output=True, text=True)
        if run.returncode != 0:
            raise CompileFailed(f"driver exited with {run.returncode}\n{run.stderr}")
        return parse_output(module, run.stdout), output
    finally:
        if keep_dir is None:
            shutil.rmtree(work, ignore_errors=True)
