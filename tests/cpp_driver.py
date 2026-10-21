"""Build and run generated DAL C++ against the stand-in engine headers in tests/cpp_stub.

``build_and_run(module, frames)`` writes the generated sources, a driver that replays the frames through the
generated class (``TickComponent`` for actor logic; ``NativeUpdateAnimation`` / ``NativeThreadSafeUpdateAnimation``
and the entries for anim logic), compiles everything with whatever C++ compiler is available and returns the
per-frame states printed by the program in the same layout as ``LogicBase.snapshot()``, plus ``"__cmds__"``: the
host effects the generated code produced.  ``find_compiler()`` returns None when there is none.

Events of a frame (``"events"``) are delivered the way the engine delivers them: the montage / notify delegates of the
stub anim instance are broadcast and the stub enhanced input component fires the action; ``AnimNotify_<Name>`` is
called for notifies of an anim instance.  The scripted scene (``"montages"``, ``"targets"``, ``"world"``) sets the
state behind the montage reads, the target tag search and the traces.
"""

from __future__ import annotations

import functools
import glob
import os
import re
import shutil
import subprocess
import tempfile

from dal import ir
from dal.cppbackend import CppGen, CppOptions, generate_cpp
from dal.types import BOOL, FLOAT, INT, MOVEMENT_MODE, QUAT, ROTATOR, VEC2, VEC3, EnumType, StructType, TagType

HERE = os.path.dirname(os.path.abspath(__file__))
STUB = os.path.join(HERE, "cpp_stub")


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
    if isinstance(ty, TagType):
        return _tag_lit(ty, v)
    if isinstance(ty, StructType):
        sets = " ".join(f"S.{f.name} = {_lit(f.ty, v[f.name])};" for f in ty.fields if f.name in v)
        return f"[&]() {{ {ty.cpp_type} S; {sets} return S; }}()"
    raise ValueError(ty)


def _tag_lit(ty, v):
    member = ty.member_of(v)
    return "FGameplayTag::EmptyTag" if member in (None, "None") and v == "" else f"{ty.cpp_namespace}::{member}"


def leaves(name, ty):
    """(path, leaf type) of every leaf of a state / input: struct members are flattened with '.'."""
    if isinstance(ty, StructType):
        for f in ty.fields:
            yield from leaves(f"{name}.{f.name}", f.ty)
    else:
        yield name, ty


def _print_value(name, ty, obj="Obj"):
    """C++ statement printing ``<obj>.name`` as 'name v0 v1 ...' (nan / inf spelled out)."""
    ref = f"{obj}.{name}"
    if isinstance(ty, TagType):
        return f'PvS("{name}", {ref}.ToString());'
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


def _comps(ty, v):
    """The three components of an action / event value as the stub's FInputActionValue stores them."""
    comps = [float(v)] if ty in (BOOL, FLOAT) else [float(c) for c in v]
    return comps + [0.0] * (3 - len(comps))


def _action_type(module, name):
    for i in module.inputs:
        if i.host and i.host.item == "action" and i.host.arg == name:
            return i.ty
    for a in module.actions:
        if a.name == name:
            return a.ty
    return BOOL


_SIMPLE = {
    "velocity": lambda v: f"Move.Velocity = FVector({_num(v[0])}, {_num(v[1])}, {_num(v[2])});",
    "acceleration": lambda v: f"Move.StubAcceleration = FVector({_num(v[0])}, {_num(v[1])}, {_num(v[2])});",
    "max_acceleration": lambda v: f"Move.StubMaxAcceleration = {_num(v)}f;",
    "max_braking_deceleration": lambda v: f"Move.StubMaxBrakingDeceleration = {_num(v)}f;",
    "max_walk_speed": lambda v: f"Move.MaxWalkSpeed = {_num(v)}f;",
    "movement_mode": lambda v: f"Move.MovementMode = static_cast<EMovementMode>({int(v)});",
    "is_grounded": lambda v: f"Move.StubGrounded = {'true' if v else 'false'};",
    "is_crouching": lambda v: f"Move.StubCrouching = {'true' if v else 'false'};",
    "actor_location": lambda v: f"Pawn.StubLocation = FVector({_num(v[0])}, {_num(v[1])}, {_num(v[2])});",
    "actor_rotation": lambda v: f"Pawn.StubQuat = FQuat({_num(v[0])}, {_num(v[1])}, {_num(v[2])}, {_num(v[3])});",
    "control_rotation": lambda v: f"Pawn.StubControlRotation = FRotator({_num(v[0])}, {_num(v[1])}, {_num(v[2])});",
}
_TRIGGER = {"started": "Started", "triggered": "Triggered", "ongoing": "Ongoing", "completed": "Completed", "canceled": "Canceled"}


def make_driver(module: ir.Module, frames: list[dict]) -> str:
    gen = CppGen(module, CppOptions())
    actor = module.kind == "actor"
    target = "Anim" if actor else "Obj"
    cls = f"U{module.name}"
    slots = gen.action_slots()
    has_cmds = bool(gen.cmds)
    L = [
        '#include "StubEngine.h"',
        f'#include "{module.name}.h"',
        "",
        "#include <initializer_list>",
        "",
        "[[maybe_unused]] static void Pv(const char* Name, std::initializer_list<double> Values)",
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
        "[[maybe_unused]] static void PvS(const char* Name, const std::string& Value)",
        "{",
        '	std::printf("%s %s\\n", Name, Value.empty() ? "-" : Value.c_str());',
        "}",
        "",
        "static void Pcmds()",
        "{",
        "	for (const std::string& Line : StubLog::Lines())",
        "	{",
        '		std::printf("CMD %s\\n", Line.c_str());',
        "	}",
        "	StubLog::Lines().clear();",
        "}",
        "",
        "int main()",
        "{",
        f"\t{cls} Obj;",
        "\tUWorld World;",
        "\tACharacter Pawn;",
        "\tUCharacterMovementComponent Move;",
        "\tAPlayerController Controller;",
        "\tULocalPlayer LocalPlayer;",
        "\tUEnhancedInputLocalPlayerSubsystem Subsystem;",
        "\tUEnhancedPlayerInput PlayerInput;",
        "\tUSkeletalMeshComponent Mesh;",
        "\tUAnimInstance Anim;",
        "\tUSpringArmComponent Arm;",
        "\tUCameraComponent Camera;",
        "\tUEnhancedInputComponent InputComp;",
        "\tAActor Targets[8];",
        "\tPawn.StubMovement = &Move;",
        "\tPawn.StubController = &Controller;",
        "\tController.StubLocalPlayer = &LocalPlayer;",
        "\tLocalPlayer.StubSubsystem = &Subsystem;",
        "\tSubsystem.StubInput = &PlayerInput;",
    ]
    if actor:
        L += [
            "\tObj.StubOwner = &Pawn;",
            "\tObj.StubWorld = &World;",
            "\tPawn.StubComponents = {&Mesh, &Arm, &Camera};",
            "\tPawn.InputComponent = &InputComp;",
            "\tMesh.StubAnim = &Anim;",
        ]
    else:
        L += [
            "\tObj.StubPawn = &Pawn;",
            "\tObj.StubWorld = &World;",
        ]
    for k in range(8):
        L.append(f'\tTargets[{k}].Tags.push_back(FName(TEXT("DalTarget")));')
    for a in slots:
        L.append(f"\tUInputAction Action_{a};")
        L.append(f"\tObj.DalAction_{a} = &Action_{a};")
    for md in module.montages:
        L.append(f"\tUAnimMontage Montage_{md.name};")
        L.append(f'\tMontage_{md.name}.StubName = "{md.name}";')
        L.append(f"\tObj.DalMontage_{md.name} = &Montage_{md.name};")
    if gen.phased:
        L.append("\tObj.RegisterComponentTickFunctions(true);")
    L.append("\tObj.BeginPlay();" if actor else "\tObj.NativeInitializeAnimation();")
    if not actor and gen.begin_handlers:
        L.append("\tObj.NativeBeginPlay();")
    by_name = {i.host.arg: i for i in module.inputs if i.host and i.host.item == "action"}
    notify_inputs = {i.host.arg for i in module.inputs if i.host and i.host.item == "notify"}
    anim_notifies = set(gen.anim_notify_funcs())
    for n, f in enumerate(frames):
        L.append(f"\t// ---- frame {n}")
        L.append("\t{")
        keep_begin = n == 0 and actor and bool(gen.begin_handlers)
        if keep_begin:
            L.append("\t\tconst std::vector<std::string> BeginLog = StubLog::Lines();")        # what BeginPlay did belongs to the first frame
        if "time" in f:
            L.append(f"\t\tWorld.Time = {_num(f['time'])};")
        for key, fmt in _SIMPLE.items():
            if key in f:
                L.append("\t\t" + fmt(f[key]))
        for a, v in f.get("actions", {}).items():
            if a not in by_name:
                continue
            c = _comps(by_name[a].ty, v)
            L.append(f"\t\tPlayerInput.Values[&Action_{a}] = FInputActionValue({_num(c[0])}, {_num(c[1])}, {_num(c[2])});")
        for name, v in f.get("free", {}).items():
            inp = module.find_input(name)
            L.append(f"\t\tObj.{name} = {_lit(inp.ty, v)};")
        # the scene behind the montage reads, the target search and the traces
        for name, st in f.get("montages", {}).items():
            if module.find_montage(name) is None:
                continue
            sec = f'FName(TEXT("{st["section"]}"))' if st.get("section") else "FName()"
            playing = "true" if st.get("playing") else "false"
            active = "true" if st.get("active") else "false"
            L.append(f"\t\t{target}.StubMontages[&Montage_{name}] = UAnimInstance::FStubMontage{{{playing}, {active}, {_num(st.get('position', 0.0))}f, {sec}}};")
        if actor:
            L.append("\t\tWorld.StubActors.clear();")
            for k, (tid, loc) in enumerate(f.get("targets", [])[:8]):
                L.append(f"\t\tTargets[{k}].StubId = {int(tid)}u; Targets[{k}].StubLocation = FVector({_num(loc[0])}, {_num(loc[1])}, {_num(loc[2])}); "
                         f"World.StubActors.push_back(&Targets[{k}]);")
            world = f.get("world", {})
            if "ground_z" in world:
                if world["ground_z"] is None:
                    L.append("\t\tWorld.HasGround = false;")
                else:
                    L.append(f"\t\tWorld.HasGround = true; World.GroundZ = {_num(world['ground_z'])};")
            if "spheres" in world:
                L.append("\t\tWorld.Spheres.clear();")
                for c, r in world["spheres"]:
                    L.append(f"\t\tWorld.Spheres.push_back({{FVector({_num(c[0])}, {_num(c[1])}, {_num(c[2])}), {_num(r)}}});")
        # a notify read through host.notify(...) that fired this frame
        for name, fired in f.get("notifies", {}).items():
            if not fired or name not in notify_inputs:
                continue
            if actor:
                L.append(f'\t\t{{ FBranchingPointNotifyPayload Payload; Anim.OnPlayMontageNotifyBegin.Broadcast(FName(TEXT("{name}")), Payload); }}')
            else:
                L.append(f"\t\tObj.AnimNotify_{name}();")
        # events, oldest first
        for ev in f.get("events", []):
            kind, name = ev[0], ev[1]
            if kind == "notify":
                if not actor and name in anim_notifies:
                    L.append(f"\t\tObj.AnimNotify_{name}();")
                else:
                    L.append(f'\t\t{{ FBranchingPointNotifyPayload Payload; {target}.OnPlayMontageNotifyBegin.Broadcast(FName(TEXT("{name}")), Payload); }}')
            elif kind in ("notify_begin", "notify_end"):
                delegate = "OnPlayMontageNotifyBegin" if kind == "notify_begin" else "OnPlayMontageNotifyEnd"
                L.append("\t\t{ FBranchingPointNotifyPayload Payload; FAnimNotifyEvent StateEvent; UAnimNotifyState StateClass; "
                         "StateEvent.NotifyStateClass = &StateClass; Payload.NotifyEvent = &StateEvent; "
                         f'{target}.{delegate}.Broadcast(FName(TEXT("{name}")), Payload); }}')
            elif kind in ("montage_ended", "montage_blending_out"):
                delegate = "OnMontageEnded" if kind == "montage_ended" else "OnMontageBlendingOut"
                L.append(f"\t\t{target}.{delegate}.Broadcast(&Montage_{name}, {'true' if ev[2] else 'false'});")
            elif kind == "input":
                c = _comps(_action_type(module, name), ev[3])
                L.append(f"\t\tInputComp.Fire(&Action_{name}, ETriggerEvent::{_TRIGGER[ev[2]]}, "
                         f"FInputActionValue({_num(c[0])}, {_num(c[1])}, {_num(c[2])}));")
        dt = _num(f["dt"])
        L.append("\t\tStubLog::Lines() = BeginLog;" if keep_begin else "\t\tStubLog::Lines().clear();")
        if actor and gen.phased:
            for slot_index in range(len(gen.slots)):
                L.append(f"\t\tObj.DalRunPhase({slot_index}, {dt}f);")
        elif actor:
            L.append(f"\t\tObj.TickComponent({dt}f, LEVELTICK_All, nullptr);")
        else:
            L.append(f"\t\tObj.NativeUpdateAnimation({dt}f);")
            L.append(f"\t\tObj.NativeThreadSafeUpdateAnimation({dt}f);")
        for call in f.get("calls", []):
            L.append(f"\t\tObj.{call}();")
        if f.get("end_play"):
            L.append("\t\tObj.EndPlay(EEndPlayReason::Destroyed);" if actor else "\t\tObj.NativeUninitializeAnimation();")
        L.append(f'\t\tstd::printf("FRAME {n}\\n");')
        for d in [*module.inputs, *module.states]:
            for path, ty in leaves(d.name, d.ty):
                L.append("\t\t" + _print_value(path, ty))
        L.append("\t\tPcmds();")
        L.append("\t}")
    if not actor and has_cmds:
        # on an anim instance the commands of the last update run at the start of the next one
        L.append("\tStubLog::Lines().clear();")
        L.append("\tObj.NativeUpdateAnimation(0.0f);")
        L.append('\tstd::printf("FINAL\\n");')
        L.append("\tPcmds();")
    L.append("\treturn 0;")
    L.append("}")
    return "\n".join(L) + "\n"


def parse_output(module: ir.Module, text: str) -> list[dict]:
    """Rebuild snapshots (plain tuples / numbers) from the driver's output.

    Each frame has ``"__cmds__"``: the host effects the generated code produced (tuples of a name and its arguments).
    For anim logic with commands the last element is ``{"__final__": True, "__cmds__": ...}``: the commands the last
    update queued, run by one more NativeUpdateAnimation (an anim instance runs them one update late).
    """
    types = {}
    for d in [*module.inputs, *module.states]:
        types.update(dict(leaves(d.name, d.ty)))
    frames: list[dict] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "FRAME":
            frames.append({"__cmds__": []})
            continue
        if parts[0] == "FINAL":
            frames.append({"__final__": True, "__cmds__": []})
            continue
        if parts[0] == "CMD":
            toks = []
            for t in parts[2:]:
                try:
                    toks.append(float(t))
                except ValueError:
                    toks.append(t)
            frames[-1]["__cmds__"].append((parts[1], *toks))
            continue
        name, vals = parts[0], parts[1:]
        frames[-1][name] = parse_value(types[name], vals)
    return frames


def parse_value(ty, vals):
    """One printed value ('v0 v1 ...') as the plain data the Python snapshot holds."""
    if isinstance(ty, TagType):
        return "" if vals[0] == "-" else vals[0]
    if ty is FLOAT:
        return float(vals[0])
    if ty is INT or isinstance(ty, EnumType):
        return int(vals[0])
    if ty is BOOL:
        return bool(int(vals[0]))
    return tuple(float(v) for v in vals)


def forwarding_headers(files: dict[str, str], module_name: str, extra_include: str | None) -> list[str]:
    """Engine headers the generated sources include that the stub folder does not have: they all forward to StubEngine.h."""
    known = set(files) | {"CoreMinimal.h", "StubEngine.h", f"{module_name}.generated.h", "stub_empty.h"}
    if extra_include:
        known |= set(os.listdir(extra_include))
    wanted = []
    for text in files.values():
        for m in re.finditer(r'^#include "([^"]+)"', text, re.M):
            if m.group(1) not in known and m.group(1) not in wanted:
                wanted.append(m.group(1))
    return wanted


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
        files = generate_cpp(module, options or CppOptions())
        for name, text in files.items():
            with open(os.path.join(gen, name), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        for name in ("CoreMinimal.h", "StubEngine.h"):
            shutil.copy(os.path.join(STUB, name), os.path.join(inc, name))
        for name in forwarding_headers(files, module.name, extra_include) + ["stub_empty.h"]:
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
        srcs = [os.path.join(gen, n) for n in sorted(files) if n.endswith(".cpp")]
        parent = CppGen(module, options or CppOptions()).parent
        if kind == "msvc":
            cmd = [path, "/nologo", "/std:c++20", "/EHsc", "/W4", f"/DDAL_STUB_SUPER={parent}", *[f"/D{d}" for d in (defines or [])], *[f"/I{d}" for d in includes],
                   driver, *srcs, f"/Fe:{exe}", f"/Fo:{work}\\"]
            if warnings_as_errors:
                cmd.insert(5, "/WX")
        else:
            cmd = [path, "-std=c++20", "-Wall", "-Wextra", f"-DDAL_STUB_SUPER={parent}", *[f"-D{d}" for d in (defines or [])], *[f"-I{d}" for d in includes], driver, *srcs, "-o", exe]
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
