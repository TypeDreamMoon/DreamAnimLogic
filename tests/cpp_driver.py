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
from dal.types import BOOL, FLOAT, INT, MOVEMENT_MODE, QUAT, ROTATOR, TRANSFORM, VEC2, VEC3, ArrayType, AssetType, EnumType, MapType, StructType, TagType

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
    if ty is TRANSFORM:
        return f"FTransform3f({_lit(QUAT, v[1])}, {_lit(VEC3, v[0])}, {_lit(VEC3, v[2])})"
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


class SettingsInit:
    """C++ statements that fill a settings object (a ``UDataAsset`` or a value struct) with the members of a loaded instance,
    the way the engine fills one from a data asset; curves become stand-in polylines, assets named stand-in objects."""

    def __init__(self):
        self.lines: list[str] = []
        self.n = 0

    def fresh(self) -> str:
        self.n += 1
        return f"Dal_E{self.n}"

    def fill(self, ty, v, target: str) -> None:
        if isinstance(ty, StructType):
            for f in ty.fields:
                if not f.derived:
                    self.fill(f.ty, getattr(v, f.name), f"{target}.{f.name}")
        elif isinstance(ty, MapType):
            for k, x in v.items():
                e = self.fresh()
                self.lines.append(f"{{ auto& {e} = {target}.Add({_lit(ty.key, k)}, {cpp_type(ty.value)}());")
                self.fill(ty.value, x, e)
                self.lines.append("}")
        elif isinstance(ty, ArrayType):
            for x in v:
                e = self.fresh()
                self.lines.append(f"{{ auto& {e} = {target}[{target}.Add({cpp_type(ty.elem)}())];")
                self.fill(ty.elem, x, e)
                self.lines.append("}")
        elif isinstance(ty, AssetType):
            if v is None:
                return
            if ty.name == "curve":
                self.lines.append(f"{target} = {_curve_expr(v)};")
            elif ty.name == "curve3":
                self.lines.append(f"{{ UCurveVector* Dal_V = DalCurveV(); {' '.join(f'Dal_V->StubCurves[{i}].StubKeys = {_keys(c)};' for i, c in enumerate(v.channels))} {target} = Dal_V; }}")
            else:
                self.lines.append(f'{target} = Dal{"Sequence" if ty.name == "sequence" else "Montage"}("{v}");')
        else:
            self.lines.append(f"{target} = {_lit(ty, v)};")


def cpp_type(ty):
    from dal.cppbackend import cpp_type as ct

    return ct(ty)


def _keys(curve) -> str:
    if curve.pre != "constant" or curve.post != "constant" or any(k.interp not in ("linear", "constant") for k in curve.keys):
        raise ValueError("the stand-in engine evaluates polylines with constant extrapolation only")
    keys = ", ".join(f"{{{_num(k.time)}f, {_num(k.value)}f, {'true' if k.interp == 'constant' else 'false'}}}" for k in curve.keys)
    return f"std::vector<FStubCurveKey>{{{keys}}}"


def _curve_expr(curve) -> str:
    return f"[&]() {{ UCurveFloat* Dal_C = DalCurveF(); Dal_C->StubKeys = {_keys(curve)}; return Dal_C; }}()"


def settings_setup(module, params) -> list[str]:
    """Driver lines that give the settings params of ``module`` the data in ``params`` (before BeginPlay)."""
    from dal import settings as dal_settings

    L: list[str] = []
    for p in module.params:
        if p.name not in params or not (isinstance(p.ty, StructType) and p.ty.is_settings):
            continue
        inst = dal_settings.load_ir(p.ty, params[p.name])
        init = SettingsInit()
        if p.ty.asset_root:
            var = f"Dal_P_{p.name}"
            L.append(f"\t{p.ty.cpp_type} {var};")
            init.fill(p.ty, inst, var)
            L += ["\t" + x for x in init.lines]
            L.append(f"\t{var}.PostLoad();")
            L.append(f"\tObj.{p.name} = &{var};")
        else:
            init.fill(p.ty, inst, f"Obj.{p.name}")
            L += ["\t" + x for x in init.lines]
            L.append(f"\tObj.{p.name}.DalRecomputeDerived();")
    return L


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
    if isinstance(ty, AssetType) and ty.by_value:
        return f'PvS("{name}", {ref}.bIsValid ? {ref}.StubName : std::string());'
    if isinstance(ty, AssetType):
        return f'PvS("{name}", {ref} != nullptr ? {ref}->StubName : std::string());'
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
    elif ty is TRANSFORM:
        comps = [f"{ref}.GetLocation().{c}" for c in "XYZ"] + [f"{ref}.GetRotation().{c}" for c in "XYZW"] + [f"{ref}.GetScale3D().{c}" for c in "XYZ"]
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
    "capsule_radius": lambda v: f"Capsule.StubRadius = {_num(v)}f;",
    "capsule_half_height": lambda v: f"Capsule.StubHalfHeight = {_num(v)}f;",
    "actor_scale": lambda v: f"Capsule.StubScale = FVector({_num(v)}, {_num(v)}, {_num(v)});",
    "walkable_floor_z": lambda v: f"Move.StubWalkableFloorZ = {_num(v)}f;",
    "actor_ready": lambda v: f"Pawn.StubRegistered = {'true' if v else 'false'};",
    "has_root_motion": lambda v: f"Pawn.StubRootMotion = {'true' if v else 'false'};",
    "movement_mode_custom": lambda v: f"Move.CustomMovementMode = {int(v)};",
    "pre_penetration_velocity": lambda v: f"Move.StubPrePenetration = FVector({_num(v[0])}, {_num(v[1])}, {_num(v[2])});",
    "pre_penetration_velocity_valid": lambda v: f"Move.StubPrePenetrationValid = {'true' if v else 'false'};",
}
_TRIGGER = {"started": "Started", "triggered": "Triggered", "ongoing": "Ongoing", "completed": "Completed", "canceled": "Canceled"}


ASSET_HELPERS = [
    "[[maybe_unused]] static UAnimSequenceBase* DalSequence(const char* Name)",
    "{",
    "	static std::map<std::string, UAnimSequenceBase*> Pool;",
    "	UAnimSequenceBase*& Found = Pool[Name];",
    "	if (Found == nullptr) { Found = new UAnimSequenceBase(); Found->StubName = Name; }",
    "	return Found;",
    "}",
    "",
    "[[maybe_unused]] static UAnimMontage* DalMontage(const char* Name)",
    "{",
    "	static std::map<std::string, UAnimMontage*> Pool;",
    "	UAnimMontage*& Found = Pool[Name];",
    "	if (Found == nullptr) { Found = new UAnimMontage(); Found->StubName = Name; }",
    "	return Found;",
    "}",
    "",
    "[[maybe_unused]] static UCurveFloat* DalCurveF() { return new UCurveFloat(); }",
    "[[maybe_unused]] static UCurveVector* DalCurveV() { return new UCurveVector(); }",
    "",
]


def _arg_lit(ty, v):
    """C++ for a loosely typed entry argument (enum member name or index, tag name, asset name, struct dict)."""
    if isinstance(ty, EnumType) and isinstance(v, str):
        v = ty.members.index(v.rsplit("::", 1)[-1].rsplit(".", 1)[-1])
    if isinstance(ty, AssetType):
        if v is None:
            return "nullptr"
        return f'Dal{"Sequence" if ty.name == "sequence" else "Montage"}("{v}")'
    if isinstance(ty, StructType):
        sets = " ".join(f"S.{f.name} = {_arg_lit(f.ty, v[f.name])};" for f in ty.fields if f.name in v)
        return f"[&]() {{ {ty.cpp_type} S; {sets} return S; }}()"
    return _lit(ty, v)


def entry_call(module: ir.Module, target: str, item) -> str:
    """`Obj.Name(args);` for an entry call of a frame: the name, or (name, {parameter: argument}).  Arguments up to the last one
    given are written (the declared default fills the gaps); the rest is left to the C++ default arguments."""
    from dal.cppbackend import cpp_value

    name, args = (item, {}) if isinstance(item, str) else (item[0], dict(item[1]) if len(item) > 1 else {})
    entries = [*module.entries, *(module.linked.entries if module.linked is not None else [])]
    e = next(x for x in entries if x.name == name)
    last = max((i for i, p in enumerate(e.params) if p.name in args), default=-1)
    parts = [_arg_lit(p.ty, args[p.name]) if p.name in args else cpp_value(p.ty, p.default) for p in e.params[:last + 1]]
    return f"{target}.{name}({', '.join(parts)});"


_HOOK_KINDS = {"movement_mode_changed": "MovementModeChanged", "jumped": "Jumped", "landed": "Landed", "crouch_started": "CrouchStarted",
               "crouch_ended": "CrouchEnded", "teleported": "Teleported"}


def hook_event(ev, owner: str = "Pawn") -> str:
    """The statement that posts a hook event of the character the way DalRuntime's movement component does: through the router."""
    kind = ev[0]
    parts = [f"DalHost::FEvent E; E.Kind = DalHost::EEventKind::{_HOOK_KINDS[kind]};"]
    if kind == "movement_mode_changed":
        parts.append(f"E.ModeA = {int(ev[1])}; E.ModeB = {int(ev[2]) if len(ev) > 2 else 0};")
    elif kind == "landed" and len(ev) > 1:
        h = list(ev[1]) + [None] * (9 - len(ev[1]))
        v3 = lambda v: f"FVector3f({_num(v[0])}f, {_num(v[1])}f, {_num(v[2])}f)"
        parts.append(f"E.Hit.bBlocking = {'true' if h[0] else 'false'}; E.Hit.Point = {v3(h[1])}; E.Hit.Normal = {v3(h[2])}; "
                     f"E.Hit.Distance = {_num(h[3])}f; E.Hit.Time = {_num(h[4])}f; E.Hit.Location = {v3(h[5] if h[5] is not None else h[1])}; "
                     f"E.Hit.bStartPenetrating = {'true' if h[6] else 'false'}; E.Hit.FaceNormal = {v3(h[8] if h[8] is not None else h[2])};")
    parts.append(f"DalHost::FEventRouter::Post(&{owner}, E);")
    return "{ " + " ".join(parts) + " }"


def scene_for_commands(module, gen, target: str) -> list[str]:
    """Driver lines for what the montage commands act on: one active instance per slot of a stop_montages_with_slot, five numbered instances for
    montage_stop_source, the pose snapshots' names (the stand-in logs them)."""
    L: list[str] = []
    slots: list[str] = []
    for _label, body in module.bodies():
        for st in ir.walk_stmts(body):
            if isinstance(st, ir.Command) and st.name == "stop_montages_with_slot" and st.args[0].value not in slots:
                slots.append(st.args[0].value)
    for k, slot in enumerate(slots):
        L += [f"\tUAnimMontage SlotMontage_{k}; SlotMontage_{k}.StubName = \"Slot_{slot}\"; SlotMontage_{k}.SlotAnimTracks.Add(FSlotAnimationTrack{{FName(TEXT(\"{slot}\"))}});",
              f"\tFAnimMontageInstance SlotInstance_{k}; SlotInstance_{k}.StubId = {100 + k}; SlotInstance_{k}.Montage = &SlotMontage_{k};",
              f"\t{target}.MontageInstances.Add(&SlotInstance_{k});"]
    if any(isinstance(st, ir.Command) and st.name == "montage_stop_source" for _label, body in module.bodies() for st in ir.walk_stmts(body)):
        for ident in range(1, 6):
            L += [f"\tUAnimMontage SourceMontage_{ident}; SourceMontage_{ident}.StubName = \"Instance_{ident}\";",
                  f"\tFAnimMontageInstance SourceInstance_{ident}; SourceInstance_{ident}.StubId = {ident}; SourceInstance_{ident}.Montage = &SourceMontage_{ident};",
                  f"\t{target}.MontageInstances.Add(&SourceInstance_{ident});"]
    for dotted, member in gen.snapshots:
        L.append(f"\t{target}.StubSnapshotNames[&{target}.{member}] = \"{dotted}\";")
    return L


def notify_event(module, ev, k: int):
    """(declaration, statement) for an event of a notify class the program declares: the notify object with its properties set, and the call
    the engine makes on it.  An event with a montage instance (4th element) comes through the branching-point entry points."""
    kind, name = ev[0], ev[1]
    nd = module.find_notify(name)
    payload = ev[2] if len(ev) > 2 and ev[2] else {}
    source = ev[3] if len(ev) > 3 else None
    var = f"Notify_{k}"
    sets = "".join(f" {var}.{f.name} = {_arg_lit(f.ty, payload[f.name])};" for f in nd.struct.fields if f.name in payload)
    decl = f"U{nd.cpp_class} {var};{sets}"
    ref = "FAnimNotifyEventReference()"
    if source is not None and nd.branching:
        pre = f"FBranchingPointNotifyPayload P; P.SkelMeshComponent = &Mesh; P.MontageInstanceID = {int(source)};"
        call = {"notify": f"{var}.BranchingPointNotify(P);", "notify_begin": f"{var}.BranchingPointNotifyBegin(P);",
                "notify_tick": f"{var}.BranchingPointNotifyTick(P, 0.0625f);", "notify_end": f"{var}.BranchingPointNotifyEnd(P);"}[kind]
        return decl, f"{{ {pre} {call} }}"
    call = {"notify": f"{var}.Notify(&Mesh, nullptr, {ref});", "notify_begin": f"{var}.NotifyBegin(&Mesh, nullptr, 1.0f, {ref});",
            "notify_tick": f"{var}.NotifyTick(&Mesh, nullptr, 0.0625f, {ref});", "notify_end": f"{var}.NotifyEnd(&Mesh, nullptr, {ref});"}[kind]
    return decl, call


def _dtransform(t) -> str:
    """A double-precision FTransform for a harness transform ((location), (rotation x y z w), (scale))."""
    return (f"FTransform(FQuat({_num(t[1][0])}, {_num(t[1][1])}, {_num(t[1][2])}, {_num(t[1][3])}), "
            f"FVector({_num(t[0][0])}, {_num(t[0][1])}, {_num(t[0][2])}), FVector({_num(t[2][0])}, {_num(t[2][1])}, {_num(t[2][2])}))")


def make_driver(module: ir.Module, frames: list[dict], params: dict | None = None) -> str:
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
        *ASSET_HELPERS,
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
        "\tUDalCharacterMovementComponent Move;",
        "\tUCapsuleComponent Capsule;",
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
        "\tUPrimitiveComponent Prims[9];",
        "\tUSkeletalMesh MeshAsset;",
        "\tUPhysicsAsset Phys;",
        "\tPawn.StubMovement = &Move;",
        "\tPawn.StubCapsule = &Capsule;",
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
            "\tObj.StubSkelMesh = &Mesh;",
        ]
    L.append("\tMesh.StubPhysics = &Phys; Mesh.StubAsset = &MeshAsset; Mesh.StubOwner = &Pawn;")
    for k in range(8):
        L.append(f'\tTargets[{k}].Tags.push_back(FName(TEXT("DalTarget")));')
    for a in slots:
        L.append(f"\tUInputAction Action_{a};")
        L.append(f"\tObj.DalAction_{a} = &Action_{a};")
    L += scene_for_commands(module, gen, target)
    for md in module.montages:
        L.append(f"\tUAnimMontage Montage_{md.name};")
        L.append(f'\tMontage_{md.name}.StubName = "{md.name}";')
        L.append(f"\tObj.DalMontage_{md.name} = &Montage_{md.name};")
    L += settings_setup(module, params or {})
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
        if "mesh_world_transform" in f:
            L.append(f"		Mesh.StubTransform = {_dtransform(f['mesh_world_transform'])};")
        if "root_motion_mode" in f:
            L.append(f"		{target}.RootMotionMode = static_cast<ERootMotionMode::Type>({int(f['root_motion_mode'])});")
        if "ragdoll_supported" in f:
            L.append('		Phys.StubBodies = {"pelvis", "spine_03"};' if f["ragdoll_supported"] else '		Phys.StubBodies = {"pelvis"};')
        if "bones" in f:
            L.append("		Mesh.StubBones.clear();")
            for bname, bt in f["bones"].items():
                L.append(f'		Mesh.StubBones["{bname}"] = {{{_dtransform(bt["component"])}, {_dtransform(bt["world"])}}};')
        if "skeleton" in f:
            names = list(f["skeleton"])
            L.append("		MeshAsset.StubRef = FReferenceSkeleton();")
            for bname, info in f["skeleton"].items():
                parent = names.index(info["parent"]) if info.get("parent") in f["skeleton"] else -1
                tr = info["translation"]
                L.append(f'		MeshAsset.StubRef.StubNames.push_back("{bname}"); MeshAsset.StubRef.StubParents.push_back({parent}); '
                         f"MeshAsset.StubRef.StubPose.Add(FTransform(FQuat(), FVector({_num(tr[0])}, {_num(tr[1])}, {_num(tr[2])}), FVector(1.0, 1.0, 1.0)));")
        for cname, cv in f.get("curves", {}).items():
            L.append(f'		{target}.StubCurves["{cname}"] = {_num(cv)}f;')
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
            sec = f'FName(TEXT("{st["section"]}"))' if st.get("section") else "FName()"
            playing = "true" if st.get("playing") else "false"
            active = "true" if st.get("active") else "false"
            key = f"&Montage_{name}" if module.find_montage(name) is not None else f'DalMontage("{name}")'       # a montage value: the asset of that name
            L.append(f"\t\t{target}.StubMontages[{key}] = UAnimInstance::FStubMontage{{{playing}, {active}, {_num(st.get('position', 0.0))}f, {sec}}};")
        if actor:
            L.append("\t\tWorld.StubActors.clear();")
            for k, (tid, loc) in enumerate(f.get("targets", [])[:8]):
                L.append(f"\t\tTargets[{k}].StubId = {int(tid)}u; Targets[{k}].StubLocation = FVector({_num(loc[0])}, {_num(loc[1])}, {_num(loc[2])}); "
                         f"World.StubActors.push_back(&Targets[{k}]);")
        world = f.get("world", {})                                    # the scene behind the traces (an anim instance may ask it too)
        if "ground_z" in world:
            if world["ground_z"] is None:
                L.append("\t\tWorld.HasGround = false;")
            else:
                L.append(f"\t\tWorld.HasGround = true; World.GroundZ = {_num(world['ground_z'])};")
        if "spheres" in world:
            L.append("\t\tWorld.Spheres.clear();")
            L.append("\t\tWorld.StubPrims.clear();")
            L.append('\t\tPrims[0].StubName = "Ground"; World.StubPrims.push_back(&Prims[0]);')
            for k, (c, r) in enumerate(world["spheres"][:8]):
                L.append(f"\t\tWorld.Spheres.push_back({{FVector({_num(c[0])}, {_num(c[1])}, {_num(c[2])}), {_num(r)}}});")
                L.append(f'\t\tPrims[{k + 1}].StubName = "Sphere{k}"; World.StubPrims.push_back(&Prims[{k + 1}]);')
        if world:
            for k in range(9):                                        # every primitive starts from its defaults again
                L.append(f"		Prims[{k}].StubVelocity = FVector(); Prims[{k}].StubCanStepUp = true; Prims[{k}].StubTransform = FTransform();")
        for name, props in world.get("primitives", {}).items():
            k = 0 if name == "Ground" else int(name[6:]) + 1
            if k > 8:
                continue
            t = props.get("transform", ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)))
            L.append(f"\t\tPrims[{k}].StubVelocity = FVector({_num(props.get('speed', 0.0))}, 0.0, 0.0); "
                     f"Prims[{k}].StubCanStepUp = {'true' if props.get('can_step_up', True) else 'false'}; "
                     f"Prims[{k}].StubTransform = {_dtransform(t)};")
        # a notify read through host.notify(...) that fired this frame
        for name, fired in f.get("notifies", {}).items():
            if not fired or name not in notify_inputs:
                continue
            if actor:
                L.append(f'\t\t{{ FBranchingPointNotifyPayload Payload; Anim.OnPlayMontageNotifyBegin.Broadcast(FName(TEXT("{name}")), Payload); }}')
            else:
                L.append(f"\t\tObj.AnimNotify_{name}();")
        # events, oldest first
        for k, ev in enumerate(f.get("events", [])):
            kind, name = ev[0], (ev[1] if len(ev) > 1 else "")
            if kind in ("notify", "notify_begin", "notify_end", "notify_tick") and module.find_notify(name) is not None:
                decl, call = notify_event(module, ev, k)
                L.append("\t\t" + decl)
                L.append("\t\t" + call)
                continue
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
            elif kind in _HOOK_KINDS:
                L.append("\t\t" + hook_event(ev))
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
            L.append("\t\t" + entry_call(module, "Obj", call))
        if gen.post:
            L.append("\t\tObj.StubPostUpdate();")                  # the animation graph has updated: the proxy's PostUpdate
        if f.get("end_play"):
            L.append("\t\tObj.EndPlay(EEndPlayReason::Destroyed);" if actor else "\t\tObj.NativeUninitializeAnimation();")
        L.append(f'\t\tstd::printf("FRAME {n}\\n");')
        for d in [*module.inputs, *module.states]:
            for path, ty in leaves(d.name, d.ty):
                L.append("\t\t" + _print_value(path, ty))
        L.append("\t\tPcmds();")
        L.append("\t}")
    if not actor and has_cmds and not gen.post_flush:
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
    if isinstance(ty, AssetType):
        return None if vals[0] == "-" else vals[0]
    if ty is FLOAT:
        return float(vals[0])
    if ty is INT or isinstance(ty, EnumType):
        return int(vals[0])
    if ty is BOOL:
        return bool(int(vals[0]))
    if ty is TRANSFORM:
        f = [float(v) for v in vals]
        return (tuple(f[0:3]), tuple(f[3:7]), tuple(f[7:10]))
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
                  keep_dir: str | None = None, warnings_as_errors: bool = False, defines: list[str] | None = None,
                  params: dict | None = None):
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
            f.write(make_driver(module, frames, params))
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
