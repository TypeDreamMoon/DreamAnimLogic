"""C++ backend: IR -> an Unreal Engine 5.8 class.

Generated files (``generate_cpp``):

* ``<Logic>.h`` / ``<Logic>.cpp`` -- ``UCLASS() class [API] U<Logic>``, a ``UAnimInstance`` parent class for
  ``logic X;`` and a ``UActorComponent`` (the character component) for ``logic X : actor;``;
* ``DalMath.h`` -- the static math library (``dal/cpp/DalMath.h``);
* ``DalHost.h`` -- event / command / trace-result types, only for programs that use host traffic
  (``on`` handlers, ``host.<command>(...)``, actor logic).

Threading model of the anim instance (matches ``UAnimInstance``):

* ``NativeUpdateAnimation`` (game thread) runs the commands the previous update queued, then reads the
  host -- owning pawn, character movement, enhanced input, montages, world time -- into the input
  properties (``DalSampleInputs``) and takes the events that arrived (``DalTakeEvents``);
* ``NativeThreadSafeUpdateAnimation`` (worker thread) runs the logic (``DalUpdate``): timer ticks, event
  handlers, the update body.  It only touches the class's own properties, params, constants and pure
  functions; host commands go into a queue;
* entries are ``BlueprintThreadSafe`` UFUNCTIONs that ``.dab`` can name in ``Call(Self.Entry)``.

Actor component (``TickComponent``, game thread, ``TG_PrePhysics``): bind the animation / input delegates,
sample, take events, run ``DalUpdate`` (which may ask the world: traces, targets) and execute the queued
commands -- all in one tick.

The output is deterministic (no timestamps) and tab-indented like UE code.  Every name the backend adds to
the class starts with ``Dal`` (``bDal`` for booleans); ``names.py`` keeps user names away from them.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

from . import ir
from .builtins import BUILTIN_CONSTS, BUILTINS
from .hostapi import COMMANDS, QUERIES
from .types import (
    BOOL,
    FLOAT,
    HIT,
    INT,
    MOVEMENT_MODE,
    QUAT,
    ROTATOR,
    VEC2,
    VEC3,
    EnumType,
    StructType,
    TagType,
    Type,
)

T = "\t"


@dataclass
class CppOptions:
    module_api: str = ""                           # e.g. "MYGAME_API"
    parent_class: str = ""                         # "" = UAnimInstance for anim logic, UActorComponent for actor logic
    parent_include: str = ""                       # "" = Animation/AnimInstance.h resp. Components/ActorComponent.h
    property_bag: bool = False                     # anim logic: also emit U<Logic>Bag, the same UPROPERTYs without the logic (DalLive)


KIND_PARENT = {
    "anim": ("UAnimInstance", "Animation/AnimInstance.h"),
    "actor": ("UActorComponent", "Components/ActorComponent.h"),
}

HIT_MEMBER_CPP = {"blocking": "bBlocking", "point": "Point", "normal": "Normal", "distance": "Distance", "time": "Time"}


def camel(name: str) -> str:
    """set_max_walk_speed -> SetMaxWalkSpeed"""
    return "".join(p[:1].upper() + p[1:] for p in name.split("_"))


def cmd_cpp(name: str) -> str:
    """C++ name of a host command: the DalHost::ECommand member and the DalHost::Cmd:: constructor."""
    return "UnCrouch" if name == "uncrouch" else camel(name)


def query_cpp(name: str) -> str:
    return "Dal" + camel(name)


# ---- literals and types ---------------------------------------------------------------------

def _f32(x: float) -> float:
    return struct.unpack("f", struct.pack("f", x))[0]


def fmt_float(x: float) -> str:
    """Shortest decimal that rounds to the same float32, with an 'f' suffix."""
    from decimal import Decimal

    x = float(x)
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError("non-finite float literal")
    target = _f32(x)
    s = repr(x)
    for p in range(1, 10):
        cand = f"{x:.{p}g}"
        if _f32(float(cand)) == target:
            s = cand
            break
    if x != 0.0 and 1e-4 <= abs(x) < 1e7:
        s = format(Decimal(s), "f")
    elif "e" in s:
        mant, exp = s.split("e")
        s = f"{mant}e{int(exp)}"
    if "e" not in s and "." not in s:
        s += ".0"
    return s + "f"


def unparen(s: str) -> str:
    """Drop one redundant pair of outer parentheses: '(a + b)' -> 'a + b'."""
    if not (s.startswith("(") and s.endswith(")")):
        return s
    depth = 0
    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return s[1:-1] if i == len(s) - 1 else s
    return s


def cpp_type(ty: Type) -> str:
    if ty is FLOAT:
        return "float"
    if ty is INT:
        return "int32"
    if ty is BOOL:
        return "bool"
    if ty is VEC2:
        return "FVector2f"
    if ty is VEC3:
        return "FVector3f"
    if ty is ROTATOR:
        return "FRotator3f"
    if ty is QUAT:
        return "FQuat4f"
    if ty is HIT:
        return "DalHost::FHit"
    if isinstance(ty, EnumType):
        return ty.cpp_type
    if isinstance(ty, StructType):
        return ty.cpp_type
    if isinstance(ty, TagType):
        return "FGameplayTag"
    raise ValueError(ty)


def cpp_prop_type(ty: Type) -> str:
    return ty.cpp_prop_type if isinstance(ty, EnumType) else cpp_type(ty)


def cpp_value(ty: Type, v) -> str:
    if ty is FLOAT:
        return fmt_float(v)
    if ty is INT:
        return str(int(v))
    if ty is BOOL:
        return "true" if v else "false"
    if ty is VEC2:
        return "FVector2f(%s)" % ", ".join(fmt_float(c) for c in v)
    if ty is VEC3:
        return "FVector3f(%s)" % ", ".join(fmt_float(c) for c in v)
    if ty is ROTATOR:
        return "FRotator3f(%s)" % ", ".join(fmt_float(c) for c in v)
    if ty is QUAT:
        return "FQuat4f(%s)" % ", ".join(fmt_float(c) for c in v)
    if isinstance(ty, EnumType):
        return ty.cpp_members[ty.members[int(v)]]
    if isinstance(ty, StructType):
        return cpp_struct_value(ty, v)
    if isinstance(ty, TagType):
        return cpp_tag_value(ty, v)
    raise ValueError(ty)


def cpp_tag_value(ty: TagType, v) -> str:
    member = ty.member_of(str(v))
    if member == "None":
        return "FGameplayTag::EmptyTag"
    if member is None:
        return f'FGameplayTag::RequestGameplayTag(FName(TEXT("{v}")))'
    return f"{ty.cpp_namespace}::{member}"


def _plain_equal(a, b) -> bool:
    from . import runtime as rt

    if isinstance(a, rt.Struct) and isinstance(b, rt.Struct):
        return all(_plain_equal(getattr(a, n), getattr(b, n)) for n in a._FIELDS)
    return rt.to_plain(a) == rt.to_plain(b)


def cpp_struct_value(ty: StructType, v, var: str = "Dal_S") -> str:
    """C++ expression for a constant struct value: the plain default constructor when nothing differs from the declared
    defaults, otherwise an immediately-invoked lambda that sets the members that do."""
    from . import values

    diffs = [f for f in ty.fields if not _plain_equal(getattr(v, f.name), values.member_default(f))]
    if not diffs:
        return f"{ty.cpp_type}()"
    sets = " ".join(f"{var}.{f.name} = {cpp_value(f.ty, getattr(v, f.name))};" for f in diffs)
    return f"[]() {{ {ty.cpp_type} {var}; {sets} return {var}; }}()"


def _is_nonzero_number(e: ir.Expr) -> bool:
    if isinstance(e, (ir.Lit, ir.ConstRef)) and isinstance(e.value, (int, float)) and not isinstance(e.value, bool):
        return e.value != 0
    return False


_SWIZ_IDX = {"x": 0, "y": 1, "z": 2}
_COMP_CPP = {"x": "X", "y": "Y", "z": "Z", "w": "W", "pitch": "Pitch", "yaw": "Yaw", "roll": "Roll"}


class CppExprGen:
    def __init__(self, module: ir.Module):
        self.m = module
        self.struct_depth = 0

    def x(self, e: ir.Expr) -> str:
        k = type(e)
        if k is ir.Lit:
            return cpp_value(e.ty, e.value)
        if k is ir.EnumLit:
            return e.ty.cpp_members[e.member]  # type: ignore[attr-defined]
        if k is ir.ConstRef:
            if e.name in BUILTIN_CONSTS:
                return BUILTIN_CONSTS[e.name].cpp
            return f"Dal_K_{e.name}"
        if k in (ir.ParamRef, ir.InputRef, ir.StateRef):
            if e.ty is MOVEMENT_MODE:
                return f"static_cast<EMovementMode>({e.name})"
            return e.name
        if k is ir.LocalRef:
            return e.name
        if k is ir.ToFloat:
            return f"static_cast<float>({self.x(e.x)})"
        if k is ir.Unary:
            return f"(!{self.x(e.x)})" if e.op == "!" else f"(-{self.x(e.x)})"
        if k is ir.Binary:
            return self.binary(e)
        if k is ir.Cond:
            return f"({self.x(e.c)} ? {self.x(e.a)} : {self.x(e.b)})"
        if k is ir.Member:
            return self.member(e)
        if k is ir.Make:
            a = [unparen(self.x(v)) for v in e.args]
            if e.ctor == "vec2":
                return f"FVector2f({', '.join(a)})"
            if e.ctor == "vec3":
                return f"DalMath::MakeVec3({', '.join(a)})" if e.sig_index == 1 else f"FVector3f({', '.join(a)})"
            if e.ctor == "rotator":
                return f"FRotator3f({', '.join(a)})"
            return f"FQuat4f({', '.join(a)})"
        if k is ir.CallBuiltin:
            return f"DalMath::{BUILTINS[e.name].cpp}({', '.join(unparen(self.x(a)) for a in e.args)})"
        if k is ir.CallFn:
            return f"Dal_Fn_{e.name}({', '.join(unparen(self.x(a)) for a in e.args)})"
        if k is ir.CallExtern:
            ext = self.m.find_extern(e.name)
            return f"{ext.cpp}({', '.join(unparen(self.x(a)) for a in e.args)})"  # type: ignore[union-attr]
        if k is ir.HostQuery:
            return f"{query_cpp(e.name)}({', '.join(unparen(self.x(a)) for a in e.args)})"
        if k is ir.Valid:
            return f"{self.x(e.x)}.IsValid()"
        if k is ir.MakeStruct:
            return self.make_struct(e)
        raise AssertionError(k)

    def make_struct(self, e: ir.MakeStruct) -> str:
        var = f"Dal_S{self.struct_depth}"
        self.struct_depth += 1
        sets = " ".join(f"{var}.{n} = {unparen(self.x(v))};" for n, v in e.fields)
        self.struct_depth -= 1
        return f"([&]() {{ {cpp_type(e.ty)} {var}; {sets} return {var}; }})()" if sets else f"{cpp_type(e.ty)}()"

    def member(self, e: ir.Member) -> str:
        base = self.x(e.x)
        if isinstance(e.x.ty, StructType):
            text = f"{base}.{e.comps}"
            return f"static_cast<EMovementMode>({text})" if e.ty is MOVEMENT_MODE else text
        if e.x.ty is HIT:
            return f"{base}.{HIT_MEMBER_CPP[e.comps]}"
        if len(e.comps) == 1 or e.comps in ("pitch", "yaw", "roll"):
            return f"{base}.{_COMP_CPP[e.comps]}"
        idx = ", ".join(str(_SWIZ_IDX[c]) for c in e.comps)
        fn = "Swizzle2" if len(e.comps) == 2 else "Swizzle3"
        return f"DalMath::{fn}<{idx}>({unparen(base)})"

    def binary(self, e: ir.Binary) -> str:
        a, b, op = self.x(e.a), self.x(e.b), e.op
        ta, tb = e.a.ty, e.b.ty
        if op in ("&&", "||", "==", "!=", "<", "<=", ">", ">="):
            return f"({a} {op} {b})"
        if op == "/":
            if _is_nonzero_number(e.b) and not (ta is INT and tb is INT and e.b.value == -1):  # type: ignore[attr-defined]
                return f"({a} / {b})"
            return f"DalMath::Div({a}, {b})"
        if op == "%":
            if _is_nonzero_number(e.b) and e.b.value != -1:  # type: ignore[attr-defined]
                return f"({a} % {b})"
            return f"DalMath::Mod({a}, {b})"
        return f"({a} {op} {b})"

# ---- host commands: how the game thread executes them -------------------------------------------------------------
#
# name -> (guards, body).  Every guard opens an `if (...)` block (nested in order); the body lines use relative tabs.

_G_MOVEMENT = "if (UCharacterMovementComponent* DalMovement = DalGetMovement())"
_G_CHARACTER = "if (ACharacter* DalCharacter = DalGetCharacter())"
_G_PAWN = "if (APawn* DalPawn = DalGetPawn())"
_G_CONTROLLER = "if (AController* DalController = DalPawn->GetController())"
_G_ARM = "if (USpringArmComponent* DalArm = DalPawn->FindComponentByClass<USpringArmComponent>())"
_G_CAMERA = "if (UCameraComponent* DalCamera = DalPawn->FindComponentByClass<UCameraComponent>())"
_G_ANIM = "if (UAnimInstance* DalAnim = DalGetAnimInstance())"
_G_ASSET = "if (UAnimMontage* DalAsset = DalMontageAsset(DalCommand.Montage))"

EXEC: dict[str, tuple[list[str], list[str]]] = {
    "set_max_walk_speed": ([_G_MOVEMENT], ["DalMovement->MaxWalkSpeed = DalCommand.F;"]),
    "set_max_acceleration": ([_G_MOVEMENT], ["DalMovement->MaxAcceleration = DalCommand.F;"]),
    "set_braking_deceleration_walking": ([_G_MOVEMENT], ["DalMovement->BrakingDecelerationWalking = DalCommand.F;"]),
    "set_gravity_scale": ([_G_MOVEMENT], ["DalMovement->GravityScale = DalCommand.F;"]),
    "set_jump_z_velocity": ([_G_MOVEMENT], ["DalMovement->JumpZVelocity = DalCommand.F;"]),
    "set_air_control": ([_G_MOVEMENT], ["DalMovement->AirControl = DalCommand.F;"]),
    "set_rotation_rate": ([_G_MOVEMENT], ["DalMovement->RotationRate = FRotator(DalCommand.R);"]),
    "set_orient_rotation_to_movement": ([_G_MOVEMENT], ["DalMovement->bOrientRotationToMovement = DalCommand.B0;"]),
    "set_use_controller_desired_rotation": ([_G_MOVEMENT], ["DalMovement->bUseControllerDesiredRotation = DalCommand.B0;"]),
    "set_movement_mode": ([_G_MOVEMENT], ["DalMovement->SetMovementMode(static_cast<EMovementMode>(DalCommand.Mode));"]),
    "jump": ([_G_CHARACTER], ["DalCharacter->Jump();"]),
    "stop_jumping": ([_G_CHARACTER], ["DalCharacter->StopJumping();"]),
    "crouch": ([_G_CHARACTER], ["DalCharacter->Crouch();"]),
    "uncrouch": ([_G_CHARACTER], ["DalCharacter->UnCrouch();"]),
    "add_movement_input": ([_G_PAWN], ["DalPawn->AddMovementInput(FVector(DalCommand.V), DalCommand.F);"]),
    "launch": ([_G_CHARACTER], ["DalCharacter->LaunchCharacter(FVector(DalCommand.V), DalCommand.B0, DalCommand.B1);"]),
    "montage_play": ([_G_ANIM, _G_ASSET], [
        "DalAnim->Montage_Play(DalAsset, DalCommand.F);",
        "if (DalCommand.SectionA != 0)",
        "{",
        "\tDalAnim->Montage_JumpToSection(DalSectionName(DalCommand.Montage, DalCommand.SectionA), DalAsset);",
        "}",
    ]),
    "montage_stop": ([_G_ANIM, _G_ASSET], [
        "DalAnim->Montage_Stop(DalCommand.F >= 0.0f ? DalCommand.F : DalAsset->GetDefaultBlendOutTime(), DalAsset);",
    ]),
    "montage_stop_all": ([_G_ANIM], [
        "const UAnimMontage* DalActive = DalAnim->GetCurrentActiveMontage();",
        "DalAnim->Montage_Stop(DalCommand.F >= 0.0f ? DalCommand.F : (DalActive != nullptr ? DalActive->GetDefaultBlendOutTime() : 0.0f));",
    ]),
    "montage_jump_to_section": ([_G_ANIM, _G_ASSET], [
        "DalAnim->Montage_JumpToSection(DalSectionName(DalCommand.Montage, DalCommand.SectionA), DalAsset);",
    ]),
    "montage_set_next_section": ([_G_ANIM, _G_ASSET], [
        "DalAnim->Montage_SetNextSection(DalSectionName(DalCommand.Montage, DalCommand.SectionA), "
        "DalSectionName(DalCommand.Montage, DalCommand.SectionB), DalAsset);",
    ]),
    "montage_set_play_rate": ([_G_ANIM, _G_ASSET], ["DalAnim->Montage_SetPlayRate(DalAsset, DalCommand.F);"]),
    "set_control_rotation": ([_G_PAWN, _G_CONTROLLER], ["DalController->SetControlRotation(FRotator(DalCommand.R));"]),
    "camera_set_arm_length": ([_G_PAWN, _G_ARM], ["DalArm->TargetArmLength = DalCommand.F;"]),
    "camera_set_socket_offset": ([_G_PAWN, _G_ARM], ["DalArm->SocketOffset = FVector(DalCommand.V);"]),
    "camera_set_fov": ([_G_PAWN, _G_CAMERA], ["DalCamera->SetFieldOfView(DalCommand.F);"]),
}

# input trigger events (handler phase -> ETriggerEvent / DalHost::EInputPhase members)
_TRIGGER = {"started": "Started", "triggered": "Triggered", "ongoing": "Ongoing", "completed": "Completed", "canceled": "Canceled"}

_MOVEMENT_ITEMS = ("velocity", "acceleration", "max_acceleration", "max_braking_deceleration",
                   "max_walk_speed", "movement_mode", "is_grounded", "is_crouching")
_PAWN_ITEMS = ("actor_location", "actor_rotation", "control_rotation", "action")


@dataclass
class Method:
    """A member function of the generated class: declared in the header, defined in the source."""

    ret: str
    name: str
    params: str = ""
    body: list[str] = field(default_factory=list)       # relative tabs inside the string; indented by one level on output
    def_params: str | None = None                       # parameter list of the definition when it differs (attributes, const)
    ufunction: bool = False
    access: str = "private"
    doc: str = ""
    const: bool = False

    def decl(self) -> list[str]:
        out = []
        if self.doc:
            out.append(f"{T}/** {self.doc} */")
        if self.ufunction:
            out.append(f"{T}UFUNCTION()")
        out.append(f"{T}{self.ret} {self.name}({self.params}){' const' if self.const else ''};")
        return out

    def define(self, cls: str) -> list[str]:
        params = self.def_params if self.def_params is not None else self.params
        out = [f"{self.ret} {cls}::{self.name}({params}){' const' if self.const else ''}", "{"]
        out.extend(T + line if line else "" for line in self.body)
        out.append("}")
        return out


def _guarded(guards: list[str], body: list[str], depth: int) -> list[str]:
    out: list[str] = []
    pad = T * depth
    for g in guards:
        out.append(f"{pad}{g}")
        out.append(f"{pad}{{")
        pad += T
    out.extend(pad + line for line in body)
    for _ in guards:
        pad = pad[:-1]
        out.append(f"{pad}}}")
    return out


class CppGen:
    def __init__(self, module: ir.Module, options: CppOptions):
        self.m = module
        self.o = options
        self.ex = CppExprGen(module)
        self.cls = "U" + module.name
        self.api = (options.module_api + " ") if options.module_api else ""
        self.actor = module.kind == "actor"
        parent, include = KIND_PARENT[module.kind]
        self.parent = options.parent_class or parent
        self.parent_include = options.parent_include or include
        # ---- what the program uses
        used_cmds, used_queries = set(), set()
        for _label, body in module.bodies():
            for s in ir.walk_stmts(body):
                if isinstance(s, ir.Command):
                    used_cmds.add(s.name)
            for e in ir.walk_stmt_exprs(body):
                if isinstance(e, ir.HostQuery):
                    used_queries.add(e.name)
        self.cmds = [n for n in COMMANDS if n in used_cmds]
        self.queries = [n for n in QUERIES if n in used_queries]
        self.has_events = bool(module.handlers)
        self.has_cmds = bool(self.cmds)
        self.hosth = self.actor or self.has_events or self.has_cmds         # DalHost.h is part of the output
        kinds = {h.event for h in module.handlers}
        self.bind_begin = bool(kinds & {"notify", "notify_begin"}) or (self.actor and bool(self.notify_names()))
        self.bind_end = "notify_end" in kinds
        self.bind_ended = "montage_ended" in kinds
        self.bind_blend = "montage_blending_out" in kinds
        self.bind_input = "input" in kinds
        self.bind_anim = self.bind_begin or self.bind_end or self.bind_ended or self.bind_blend
        self.montage_reads = [i for i in module.inputs if i.host is not None and i.host.item.startswith("montage_")]
        self.section_reads = [i for i in self.montage_reads if i.host.item == "montage_section"]  # type: ignore[union-attr]
        self.section_cmds = bool({"montage_play", "montage_jump_to_section", "montage_set_next_section"} & set(self.cmds))
        self.montage_cmds = [c for c in self.cmds if c.startswith("montage_")]
        self.needs_anim = bool(self.montage_reads or self.montage_cmds or self.bind_anim)
        self.arm = bool({"camera_set_arm_length", "camera_set_socket_offset"} & set(self.cmds))
        self.camera = "camera_set_fov" in self.cmds
        self.controller = "set_control_rotation" in self.cmds
        self.movement_cmds = [c for c in self.cmds if EXEC[c][0][0] == _G_MOVEMENT]
        self.character_cmds = [c for c in self.cmds if EXEC[c][0][0] == _G_CHARACTER]

    # ---- statements ---------------------------------------------------------------------------
    def cmd_arg(self, e: ir.Expr) -> str:
        if isinstance(e, ir.MontageRef):
            return f"Dal_MontageId_{e.name}"
        if isinstance(e.ty, EnumType):
            return f"static_cast<int32>({unparen(self.ex.x(e))})"
        return unparen(self.ex.x(e))

    def stmts(self, body: list[ir.Stmt], depth: int) -> list[str]:
        pad = T * depth
        out: list[str] = []
        for s in body:
            if isinstance(s, ir.Let):
                const = "" if s.mutable else "const "
                out.append(f"{pad}{const}{cpp_type(s.ty)} {s.name} = {unparen(self.ex.x(s.init))};")
            elif isinstance(s, ir.Assign):
                out.append(f"{pad}{s.name} = {unparen(self.ex.x(s.value))};")
            elif isinstance(s, ir.AssignField):
                out.append(f"{pad}{s.name}{''.join('.' + m for m in s.path)} = {unparen(self.ex.x(s.value))};")
            elif isinstance(s, ir.If):
                out.extend(self.if_chain(s, depth))
            elif isinstance(s, ir.Return):
                out.append(f"{pad}return" + (f" {unparen(self.ex.x(s.value))}" if s.value is not None else "") + ";")
            elif isinstance(s, ir.Command):
                args = ", ".join(self.cmd_arg(a) for a in s.args)
                out.append(f"{pad}DalEnqueue(DalHost::Cmd::{cmd_cpp(s.name)}({args}));")
            elif isinstance(s, ir.For):
                out.append(f"{pad}for (int32 {s.var} = {s.lo}; {s.var} < {s.hi}; ++{s.var})")
                out.append(f"{pad}{{")
                out.extend(self.stmts(s.body, depth + 1))
                out.append(f"{pad}}}")
        return out

    def if_chain(self, s: ir.If, depth: int) -> list[str]:
        pad = T * depth
        out = [f"{pad}if ({unparen(self.ex.x(s.cond))})", f"{pad}{{"]
        out.extend(self.stmts(s.then, depth + 1))
        out.append(f"{pad}}}")
        cur = s
        while cur.orelse:
            if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ir.If):
                cur = cur.orelse[0]
                out.append(f"{pad}else if ({unparen(self.ex.x(cur.cond))})")
                out.append(f"{pad}{{")
                out.extend(self.stmts(cur.then, depth + 1))
                out.append(f"{pad}}}")
                continue
            out.append(f"{pad}else")
            out.append(f"{pad}{{")
            out.extend(self.stmts(cur.orelse, depth + 1))
            out.append(f"{pad}}}")
            break
        return out

    # ---- analysis ------------------------------------------------------------------------------------
    def host_items(self) -> list[tuple[ir.InputDef, ir.HostUse]]:
        return [(i, i.host) for i in self.m.inputs if i.host is not None]

    def action_names(self) -> list[str]:
        """Actions read by `input X <- host.action(...)`."""
        seen: list[str] = []
        for _i, h in self.host_items():
            if h.item == "action" and h.arg not in seen:
                seen.append(h.arg)  # type: ignore[arg-type]
        return seen

    def action_slots(self) -> list[str]:
        """Every action the class needs an asset slot for: the read ones, then those only handled by `on input`."""
        seen = self.action_names()
        for h in self.m.handlers:
            if h.event == "input" and h.name not in seen:
                seen.append(h.name)
        return seen

    def notify_names(self) -> list[str]:
        """Notifies read by `input X <- host.notify("N")`."""
        seen: list[str] = []
        for _i, h in self.host_items():
            if h.item == "notify" and h.arg not in seen:
                seen.append(h.arg)  # type: ignore[arg-type]
        return seen

    def notify_ids(self) -> list[str]:
        """Notify names that travel as events: the handled ones, and (actor logic) the read ones."""
        seen: list[str] = []
        for h in self.m.handlers:
            if h.event in ("notify", "notify_begin", "notify_end") and h.name not in seen:
                seen.append(h.name)
        if self.actor:
            for n in self.notify_names():
                if n not in seen:
                    seen.append(n)
        return seen

    def anim_notify_funcs(self) -> list[str]:
        """AnimNotify_<N> functions of an anim instance: the read notifies, then those with an `on notify` handler."""
        seen = self.notify_names()
        for h in self.m.handlers:
            if h.event == "notify" and h.name not in seen:
                seen.append(h.name)
        return seen

    def uses(self, *items: str) -> bool:
        return any(h.item in items for _i, h in self.host_items())

    def modules(self) -> list[str]:
        mods = ["Core", "CoreUObject", "Engine"]
        if self.action_slots():
            mods += ["EnhancedInput"]
        if self.uses_tags(any_origin=True) or any(isinstance(d.ty, TagType) for d in [*self.m.params, *self.m.inputs, *self.m.states]):
            mods += ["GameplayTags"]
        return mods

    @property
    def needs_movement_read(self) -> bool:
        return self.uses(*_MOVEMENT_ITEMS)

    @property
    def needs_pawn_helper(self) -> bool:
        return bool(self.cmds and any(EXEC[c][0][0] in (_G_PAWN, _G_MOVEMENT, _G_CHARACTER) for c in self.cmds)) or self.bind_input

    # ---- header -------------------------------------------------------------------------------------------
    def banner(self) -> list[str]:
        from . import __version__

        m = self.m
        return [
            f"// Generated by dal {__version__} from {m.filename} (sha256 {m.source_sha256[:16]}...).",
            f"// Do not edit: change the .dal source and recompile.",
        ]

    def methods(self) -> list[Method]:
        """The v1 member functions (everything beyond the v0 overrides, sampling and update)."""
        m = self.m
        out: list[Method] = []
        events, cmds = self.has_events, self.has_cmds

        # ---- helpers
        if self.needs_pawn_helper:
            out.append(Method("APawn*", "DalGetPawn", body=[
                "return TryGetPawnOwner();" if not self.actor else "return Cast<APawn>(GetOwner());"]))
        if self.character_cmds or self.movement_cmds:
            out.append(Method("ACharacter*", "DalGetCharacter", body=["return Cast<ACharacter>(DalGetPawn());"]))
        if self.movement_cmds:
            out.append(Method("UCharacterMovementComponent*", "DalGetMovement", body=[
                "ACharacter* DalCharacter = DalGetCharacter();",
                "return DalCharacter != nullptr ? DalCharacter->GetCharacterMovement() : nullptr;"]))
        if self.needs_anim:
            if self.actor:
                out.append(Method("UAnimInstance*", "DalGetAnimInstance", body=[
                    "const AActor* DalOwner = GetOwner();",
                    "USkeletalMeshComponent* DalMesh = DalOwner != nullptr ? DalOwner->FindComponentByClass<USkeletalMeshComponent>() : nullptr;",
                    "return DalMesh != nullptr ? DalMesh->GetAnimInstance() : nullptr;"]))
            else:
                out.append(Method("UAnimInstance*", "DalGetAnimInstance", body=["return this;"]))
        if self.montage_cmds:
            out.append(Method("UAnimMontage*", "DalMontageAsset", "const int32 DalMontage", body=self.montage_asset_body()))
        if self.bind_ended or self.bind_blend:
            out.append(Method("int32", "DalMontageIndex", "const UAnimMontage* DalMontage", body=self.montage_index_body()))

        # ---- events
        if events:
            out.append(Method("void", "DalPostEvent", "const DalHost::FEvent& DalEvent", doc="Called by the host callbacks; the update takes the events at its start.", body=[
                "FScopeLock DalLock(&DalQueueLock);",
                "DalEventQueue.Add(DalEvent);"]))
            out.append(Method("void", "DalTakeEvents", body=[
                "FScopeLock DalLock(&DalQueueLock);",
                "DalFrameEvents.Append(DalEventQueue);",
                "DalEventQueue.Reset();"]))
            out.append(Method("void", "DalDispatchEvents", doc="Run the handlers for the events taken at the start of the update, oldest first.", body=self.dispatch_body()))
            for k, h in enumerate(m.handlers):
                params = ", ".join(f"{cpp_type(t)} {n}" for n, t in h.params)
                dparams = ", ".join(f"[[maybe_unused]] const {cpp_type(t)} {n}" for n, t in h.params)
                out.append(Method("void", f"DalHandler_{k}", params, def_params=dparams, body=self.stmts(h.body, 0),
                                  doc="on " + h.event + " " + h.name + (f" {h.phase}" if h.phase else "")))
        # ---- commands
        if cmds:
            out.append(Method("void", "DalEnqueue", "const DalHost::FCommand& DalCommand", doc="Queue a host command; it runs on the game thread after the update.", body=[
                "FScopeLock DalLock(&DalQueueLock);",
                "DalCommandQueue.Add(DalCommand);"]))
            out.append(Method("void", "DalFlushCommands", doc="Game thread: run the queued host commands, oldest first.", body=[
                "TArray<DalHost::FCommand> DalPending;",
                "{",
                "\tFScopeLock DalLock(&DalQueueLock);",
                "\tDalPending = MoveTemp(DalCommandQueue);",
                "\tDalCommandQueue.Reset();",
                "}",
                "for (const DalHost::FCommand& DalCommand : DalPending)",
                "{",
                "\tDalExecuteCommand(DalCommand);",
                "}"]))
            out.append(Method("void", "DalExecuteCommand", "const DalHost::FCommand& DalCommand", body=self.execute_body()))
        # ---- binding to the animation instance / input component
        if self.bind_anim:
            out.append(Method("void", "DalBindAnim", "UAnimInstance* DalAnim", doc="Subscribe to the animation instance's montage and notify delegates.", body=self.bind_body(True)))
            out.append(Method("void", "DalUnbindAnim", "UAnimInstance* DalAnim", body=self.bind_body(False)))
            if self.bind_ended:
                out.append(Method("void", "DalOnMontageEnded", "UAnimMontage* DalMontage, bool bDalInterrupted", ufunction=True, body=self.montage_callback("MontageEnded")))
            if self.bind_blend:
                out.append(Method("void", "DalOnMontageBlendingOut", "UAnimMontage* DalMontage, bool bDalInterrupted", ufunction=True, body=self.montage_callback("MontageBlendingOut")))
            if self.bind_begin:
                out.append(Method("void", "DalOnPlayMontageNotifyBegin", "FName DalNotifyName, const FBranchingPointNotifyPayload& DalPayload", ufunction=True, body=self.notify_begin_body()))
            if self.bind_end:
                out.append(Method("void", "DalOnPlayMontageNotifyEnd", "FName DalNotifyName, const FBranchingPointNotifyPayload& DalPayload",
                                  def_params="const FName DalNotifyName, [[maybe_unused]] const FBranchingPointNotifyPayload& DalPayload", ufunction=True, body=[
                    "const int32 DalId = DalFindNotifyId(DalNotifyName);",
                    "if (DalId != INDEX_NONE)",
                    "{",
                    "\tDalHost::FEvent DalEvent;",
                    "\tDalEvent.Kind = DalHost::EEventKind::NotifyEnd;",
                    "\tDalEvent.Id = DalId;",
                    "\tDalPostEvent(DalEvent);",
                    "}"]))
        if self.actor and (self.bind_anim or self.bind_input):
            out.append(Method("void", "DalBindHost", doc="Subscribe to the owner's animation instance and input component (retried every tick until they exist).", body=self.bind_host_body()))
        if self.bind_input:
            out.append(Method("void", "DalPostInput", "const int32 DalAction, const DalHost::EInputPhase DalPhase, const FInputActionValue& DalValue", body=[
                "DalHost::FEvent DalEvent;",
                "DalEvent.Kind = DalHost::EEventKind::Input;",
                "DalEvent.Id = DalAction;",
                "DalEvent.Phase = DalPhase;",
                "DalEvent.Value = FVector3f(DalValue.Get<FVector>());",
                "DalPostEvent(DalEvent);"]))
            for a, phase in self.input_pairs():
                out.append(Method("void", f"DalOnInput_{a}_{_TRIGGER[phase]}", "const FInputActionValue& DalValue", body=[
                    f"DalPostInput(Dal_ActionId_{a}, DalHost::EInputPhase::{_TRIGGER[phase]}, DalValue);"]))
        # ---- queries
        out.extend(self.query_methods())
        return out

    def input_pairs(self) -> list[tuple[str, str]]:
        seen: list[tuple[str, str]] = []
        for h in self.m.handlers:
            if h.event == "input" and (h.name, h.phase) not in seen:
                seen.append((h.name, h.phase))  # type: ignore[arg-type]
        return seen

    def montage_asset_body(self) -> list[str]:
        out = ["switch (DalMontage)", "{"]
        for md in self.m.montages:
            out.append(f"case Dal_MontageId_{md.name}:")
            out.append(f"\treturn DalMontage_{md.name};")
        out += ["default:", "\treturn nullptr;", "}"]
        return out

    def montage_index_body(self) -> list[str]:
        out = ["if (DalMontage != nullptr)", "{"]
        for md in self.m.montages:
            out += [f"\tif (DalMontage == DalMontage_{md.name}.Get())", "\t{", f"\t\treturn Dal_MontageId_{md.name};", "\t}"]
        out += ["}", "return INDEX_NONE;"]
        return out

    def montage_callback(self, kind: str) -> list[str]:
        return [
            "const int32 DalId = DalMontageIndex(DalMontage);",
            "if (DalId != INDEX_NONE)",
            "{",
            "\tDalHost::FEvent DalEvent;",
            f"\tDalEvent.Kind = DalHost::EEventKind::{kind};",
            "\tDalEvent.Id = DalId;",
            "\tDalEvent.bFlag = bDalInterrupted;",
            "\tDalPostEvent(DalEvent);",
            "}",
        ]

    def notify_begin_body(self) -> list[str]:
        out = [
            "const int32 DalId = DalFindNotifyId(DalNotifyName);",
            "if (DalId == INDEX_NONE)",
            "{",
            "\treturn;",
            "}",
            "",
            "// a notify window (PlayMontageNotifyWindow) is a state; a PlayMontageNotify is a point",
            "const bool bDalWindow = DalPayload.NotifyEvent != nullptr && DalPayload.NotifyEvent->NotifyStateClass != nullptr;",
        ]
        if self.has_events:
            out += [
                "DalHost::FEvent DalEvent;",
                "DalEvent.Kind = bDalWindow ? DalHost::EEventKind::NotifyBegin : DalHost::EEventKind::Notify;",
                "DalEvent.Id = DalId;",
                "DalPostEvent(DalEvent);",
            ]
        if self.actor and self.notify_names():
            out += ["", "if (!bDalWindow)", "{"]
            for n in self.notify_names():
                out += [f"\tif (DalId == Dal_NotifyId_{n})", "\t{", f"\t\tDalNotify_{n} = true;", "\t}"]
            out += ["}"]
        return out

    def bind_body(self, add: bool) -> list[str]:
        verb = "AddUniqueDynamic" if add else "RemoveDynamic"
        cls = self.cls
        out = []
        if self.bind_ended:
            out.append(f"DalAnim->OnMontageEnded.{verb}(this, &{cls}::DalOnMontageEnded);")
        if self.bind_blend:
            out.append(f"DalAnim->OnMontageBlendingOut.{verb}(this, &{cls}::DalOnMontageBlendingOut);")
        if self.bind_begin:
            out.append(f"DalAnim->OnPlayMontageNotifyBegin.{verb}(this, &{cls}::DalOnPlayMontageNotifyBegin);")
        if self.bind_end:
            out.append(f"DalAnim->OnPlayMontageNotifyEnd.{verb}(this, &{cls}::DalOnPlayMontageNotifyEnd);")
        return out

    def bind_host_body(self) -> list[str]:
        cls = self.cls
        out: list[str] = []
        if self.bind_anim:
            out += [
                "UAnimInstance* DalAnim = DalGetAnimInstance();",
                "if (DalAnim != DalBoundAnim.Get())",
                "{",
                "\tif (DalBoundAnim.IsValid())",
                "\t{",
                "\t\tDalUnbindAnim(DalBoundAnim.Get());",
                "\t}",
                "\tDalBoundAnim = DalAnim;",
                "\tif (DalAnim != nullptr)",
                "\t{",
                "\t\tDalBindAnim(DalAnim);",
                "\t}",
                "}",
            ]
        if self.bind_input:
            if out:
                out.append("")
            out += [
                "const APawn* DalPawn = DalGetPawn();",
                "UEnhancedInputComponent* DalInput = DalPawn != nullptr ? Cast<UEnhancedInputComponent>(DalPawn->InputComponent) : nullptr;",
                "if (DalInput != nullptr && DalInput != DalBoundInput.Get())",
                "{",
                "\tDalBoundInput = DalInput;",
            ]
            for a, phase in self.input_pairs():
                out += [
                    f"\tif (DalAction_{a} != nullptr)",
                    "\t{",
                    f"\t\tDalInput->BindAction(DalAction_{a}, ETriggerEvent::{_TRIGGER[phase]}, this, &{cls}::DalOnInput_{a}_{_TRIGGER[phase]});",
                    "\t}",
                ]
            out.append("}")
        return out

    def dispatch_body(self) -> list[str]:
        m = self.m
        out = ["for (const DalHost::FEvent& DalEvent : DalFrameEvents)", "{"]
        kinds = {"notify": "Notify", "notify_begin": "NotifyBegin", "notify_end": "NotifyEnd",
                 "montage_ended": "MontageEnded", "montage_blending_out": "MontageBlendingOut", "input": "Input"}
        for k, h in enumerate(m.handlers):
            what = f"on {h.event} {h.name}" + (f" {h.phase}" if h.phase else "")
            kind = kinds[h.event]
            if h.event in ("notify", "notify_begin", "notify_end"):
                ident = f"Dal_NotifyId_{h.name}"
            elif h.event == "input":
                ident = f"Dal_ActionId_{h.name}"
            else:
                ident = f"Dal_MontageId_{h.name}"
            cond = f"DalEvent.Kind == DalHost::EEventKind::{kind} && DalEvent.Id == {ident}"
            args = ""
            if h.event == "input":
                cond += f" && DalEvent.Phase == DalHost::EInputPhase::{_TRIGGER[h.phase]}"  # type: ignore[index]
                ty = h.params[0][1]
                args = {BOOL: "DalEvent.Value.X != 0.0f", FLOAT: "DalEvent.Value.X",
                        VEC2: "FVector2f(DalEvent.Value.X, DalEvent.Value.Y)", VEC3: "DalEvent.Value"}[ty]
            elif h.event in ("montage_ended", "montage_blending_out"):
                args = "DalEvent.bFlag"
            out += [f"\t// {what}", f"\tif ({cond})", "\t{", f"\t\tDalHandler_{k}({args});", "\t}"]
        out += ["}", "DalFrameEvents.Reset();"]
        return out

    def execute_body(self) -> list[str]:
        out = ["switch (DalCommand.Kind)", "{"]
        for c in self.cmds:
            guards, body = EXEC[c]
            out.append(f"case DalHost::ECommand::{cmd_cpp(c)}:")
            out.extend(_guarded(guards, body, 1))
            out.append("\tbreak;")
        out += ["default:", "\tbreak;", "}"]
        return out

    def query_methods(self) -> list[Method]:
        out: list[Method] = []
        q = self.queries
        targets = [n for n in q if n.startswith("target_")]
        if "trace_line" in q:
            out.append(Method("DalHost::FHit", "DalTraceLine", "const FVector3f& DalStart, const FVector3f& DalEnd",
                              doc="host.trace_line: the first blocking hit on the trace channel, the owner ignored.",
                              body=self.trace_body(False)))
        if "trace_sphere" in q:
            out.append(Method("DalHost::FHit", "DalTraceSphere", "const FVector3f& DalStart, const FVector3f& DalEnd, const float DalRadius",
                              doc="host.trace_sphere: a sphere sweep from start to end.", body=self.trace_body(True)))
        if targets:
            out.append(Method("void", "DalRefreshTargets", doc="Collect the actors tagged DalTargetTag, once per tick.", body=[
                "if (bDalTargetsFresh)",
                "{",
                "\treturn;",
                "}",
                "bDalTargetsFresh = true;",
                "DalTargets.Reset();",
                "const UWorld* DalWorld = GetWorld();",
                "if (DalWorld == nullptr)",
                "{",
                "\treturn;",
                "}",
                "TArray<AActor*> DalFound;",
                "UGameplayStatics::GetAllActorsWithTag(DalWorld, DalTargetTag, DalFound);",
                "for (AActor* DalActor : DalFound)",
                "{",
                "\tif (DalActor != GetOwner())",
                "\t{",
                "\t\tDalTargets.Add(DalActor);",
                "\t}",
                "}"]))
        if "target_count" in q:
            out.append(Method("int32", "DalTargetCount", body=["DalRefreshTargets();", "return DalTargets.Num();"]))
        if "target_id" in q:
            out.append(Method("int32", "DalTargetId", "const int32 DalIndex", body=[
                "DalRefreshTargets();",
                "const AActor* DalActor = DalTargets.IsValidIndex(DalIndex) ? DalTargets[DalIndex].Get() : nullptr;",
                "return DalActor != nullptr ? static_cast<int32>(DalActor->GetUniqueID()) : -1;"]))
        if "target_location" in q:
            out.append(Method("FVector3f", "DalTargetLocation", "const int32 DalIndex", body=[
                "DalRefreshTargets();",
                "const AActor* DalActor = DalTargets.IsValidIndex(DalIndex) ? DalTargets[DalIndex].Get() : nullptr;",
                "return DalActor != nullptr ? FVector3f(DalActor->GetActorLocation()) : FVector3f(0.0f, 0.0f, 0.0f);"]))
        if "target_index" in q:
            out.append(Method("int32", "DalTargetIndex", "const int32 DalId", body=[
                "DalRefreshTargets();",
                "for (int32 DalIndex = 0; DalIndex < DalTargets.Num(); ++DalIndex)",
                "{",
                "\tconst AActor* DalActor = DalTargets[DalIndex].Get();",
                "\tif (DalActor != nullptr && static_cast<int32>(DalActor->GetUniqueID()) == DalId)",
                "\t{",
                "\t\treturn DalIndex;",
                "\t}",
                "}",
                "return -1;"]))
        return out

    def trace_body(self, sweep: bool) -> list[str]:
        out = [
            "DalHost::FHit DalResult;",
            "DalResult.Point = DalEnd;",
            "const UWorld* DalWorld = GetWorld();",
            "if (DalWorld == nullptr)",
            "{",
            "\treturn DalResult;",
            "}",
            "FHitResult DalHit;",
            "FCollisionQueryParams DalParams(SCENE_QUERY_STAT(DalTrace), false, GetOwner());",
        ]
        if sweep:
            out.append("const bool bDalBlocked = DalWorld->SweepSingleByChannel(DalHit, FVector(DalStart), FVector(DalEnd), FQuat::Identity, "
                       "DalTraceChannel, FCollisionShape::MakeSphere(DalRadius), DalParams);")
        else:
            out.append("const bool bDalBlocked = DalWorld->LineTraceSingleByChannel(DalHit, FVector(DalStart), FVector(DalEnd), DalTraceChannel, DalParams);")
        out += [
            "if (bDalBlocked)",
            "{",
            "\tDalResult.bBlocking = true;",
            "\tDalResult.Point = FVector3f(DalHit.ImpactPoint);",
            "\tDalResult.Normal = FVector3f(DalHit.ImpactNormal);",
            "\tDalResult.Distance = DalHit.Distance;",
            "\tDalResult.Time = DalHit.Time;",
            "}",
            "return DalResult;",
        ]
        return out

    # ---- declarations shared by the logic header and the library headers -----------------------------------------
    def tags_decl(self, ts: TagType) -> list[str]:
        out = [f"namespace {ts.cpp_namespace}", "{"]
        for mem in ts.members:
            out.append(f"{T}{self.api}UE_DECLARE_GAMEPLAY_TAG_EXTERN({mem})")
        out.append("}")
        return out

    def tags_define(self, ts: TagType) -> list[str]:
        out = [f"namespace {ts.cpp_namespace}", "{"]
        for mem in ts.members:
            out.append(f'{T}UE_DEFINE_GAMEPLAY_TAG({mem}, TEXT("{ts.full_name(mem)}"))')
        out.append("}")
        return out

    def uses_tags(self, origin: str | None = None, any_origin: bool = False) -> bool:
        """The program declares tag sets (of this origin) or stores tag values."""
        m = self.m
        if any(t.origin == origin or any_origin for t in m.tagsets):
            return True
        return any(isinstance(f.ty, TagType) for s in m.structs if s.origin == origin or any_origin for f in s.fields)

    def struct_decl(self, st: StructType) -> list[str]:
        out = ["USTRUCT(BlueprintType)", f"struct {st.cpp_type}", "{", f"{T}GENERATED_BODY()"]
        for f in st.fields:
            out.append("")
            out.append(f'{T}UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL")')
            out.append(f"{T}{cpp_prop_type(f.ty)} {f.name} = {cpp_value(f.ty, f.default)};")
        out.append("};")
        return out

    def enum_decl(self, en: EnumType) -> list[str]:
        out = ["UENUM(BlueprintType)", f"enum class {en.cpp_type} : uint8", "{"]
        for mem in en.members:
            out.append(f"{T}{mem},")
        out.append("};")
        return out

    def const_decl(self, c: ir.ConstDef, inline: bool) -> list[str]:
        decl = "inline const" if inline else "[[maybe_unused]] const"
        return [f"{T}// const {c.name}: {c.ty} = {c.source.rstrip(';').split('=', 1)[-1].strip()}",
                f"{T}{decl} {cpp_type(c.ty)} Dal_K_{c.name} = {cpp_value(c.ty, c.value)};"]

    def fn_decl(self, f: ir.FnDef, inline: bool) -> list[str]:
        params = ", ".join(f"{cpp_type(t)} {n}" for n, t in f.params)
        head = "inline" if inline else "[[maybe_unused]]"
        out = [f"{T}{head} {cpp_type(f.ret)} Dal_Fn_{f.name}({params})", f"{T}{{"]
        out.extend(self.stmts(f.body, 2))
        out.append(f"{T}}}")
        return out

    # ---- library headers ---------------------------------------------------------------------------------------------
    def lib_includes(self, origin: str | None) -> list[str]:
        inc: list[str] = []
        for x in self.m.externs:
            if x.origin == origin and x.include and x.include not in inc:
                inc.append(x.include)
        return inc

    def lib_files(self, origin: str | None, stem: str, filename: str, sha256: str) -> dict[str, str]:
        """The shared header (and, when it needs one, the source) of a library: its enums, constants and functions."""
        from . import __version__

        m = self.m
        has_tags = self.uses_tags(origin)
        own_tags = [t for t in m.tagsets if t.origin == origin]
        L = [f"// Generated by dal {__version__} from {filename} (sha256 {sha256[:16]}...).",
             "// Do not edit: change the .dal source and recompile.",
             f"// Module dependencies: Core, CoreUObject, Engine" + (", GameplayTags" if has_tags else ""),
             "#pragma once",
             "",
             '#include "CoreMinimal.h"',
             '#include "DalMath.h"']
        if has_tags:
            L.append('#include "GameplayTagContainer.h"')
        if own_tags:
            L.append('#include "NativeGameplayTags.h"')
        for inc in self.lib_includes(origin):
            L.append(f'#include "{inc}"')
        deps = next((lib.deps for lib in m.libs if lib.key == origin), [lib.key for lib in m.libs])
        for lib in m.libs:
            if lib.key in deps:
                L.append(f'#include "{lib.stem}.h"')
        L.append(f'#include "{stem}.generated.h"')
        L.append("")
        for en in m.enums:
            if en.origin == origin:
                L.extend(self.enum_decl(en))
                L.append("")
        for ts in own_tags:
            L.extend(self.tags_decl(ts))
            L.append("")
        for st in m.structs:
            if st.origin == origin:
                L.extend(self.struct_decl(st))
                L.append("")
        consts = [c for c in m.consts if c.origin == origin]
        funcs = [f for f in m.funcs if f.origin == origin]
        if consts or funcs:
            L.append(f"namespace DalLib_{stem}")
            L.append("{")
            for c in consts:
                L.extend(self.const_decl(c, True))
            if consts and funcs:
                L.append("")
            for k, f in enumerate(funcs):
                if k:
                    L.append("")
                L.extend(self.fn_decl(f, True))
            L.append("}")
        while L[-1] == "":
            L.pop()
        files = {f"{stem}.h": "\n".join(L) + "\n"}
        if own_tags:
            S = [f"// Generated by dal {__version__} from {filename} (sha256 {sha256[:16]}...).",
                 "// Do not edit: change the .dal source and recompile.",
                 f'#include "{stem}.h"', "", '#include "NativeGameplayTags.h"', "", f"#include UE_INLINE_GENERATED_CPP_BY_NAME({stem})", ""]
            for k, ts in enumerate(own_tags):
                if k:
                    S.append("")
                S.extend(self.tags_define(ts))
            files[f"{stem}.cpp"] = "\n".join(S) + "\n"
        return files

    # ---- header text ---------------------------------------------------------------------------------------------
    def property_lines(self) -> list[str]:
        """The UPROPERTY declarations of params, inputs and states (indented for a class body), as in the generated class."""
        m = self.m
        L: list[str] = []
        if m.params:
            L.append(f"{T}// ---- params: tunable in the editor, read-only for the logic ----")
            for p in m.params:
                L.append(f'{T}UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "DAL|Param")')
                L.append(f"{T}{cpp_prop_type(p.ty)} {p.name} = {cpp_value(p.ty, p.default)};")
                L.append("")
        if m.inputs:
            L.append(f"{T}// ---- inputs: filled from the host each frame (or set by game code when free) ----")
            for i in m.inputs:
                if i.host is not None:
                    h = i.host
                    src = f"host.{h.item}" + (f'("{h.arg}")' if h.arg is not None else "")
                    L.append(f"{T}// <- {src}")
                    L.append(f'{T}UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Input", Transient)')
                else:
                    L.append(f'{T}UPROPERTY(VisibleAnywhere, BlueprintReadWrite, Category = "DAL|Input", Transient)')
                L.append(f"{T}{cpp_prop_type(i.ty)} {i.name} = {cpp_value(i.ty, i.default)};")
                L.append("")
        if m.states:
            L.append(f"{T}// ---- state: written by the logic, readable from the animation graph ----")
            for s in m.states:
                L.append(f'{T}UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|State", Transient)')
                L.append(f"{T}{cpp_prop_type(s.ty)} {s.name} = {cpp_value(s.ty, s.default)};")
                L.append("")
        return L

    def bag_class(self) -> list[str]:
        """``U<Logic>Bag``: a UAnimInstance with the same params, inputs and states and none of the logic.

        The interpreted twin of an anim logic (DalLive, ``Unreal/DalLive``) writes its values into the properties of an
        AnimInstance by name; this class gives an Animation Blueprint those properties without the generated update code,
        so the same ``.dab`` / AnimBP reads ``Main.Speed`` whether the parent is ``U<Logic>`` (compiled) or ``U<Logic>Bag``
        (interpreted).  Only emitted for ``logic X;`` (anim) programs and only on request (``CppOptions.property_bag``)."""
        L: list[str] = []
        L.append(f"/** Property bag of {self.cls}: the same params, inputs and state, without the update logic (for DalLive's interpreter). */")
        L.append("UCLASS()")
        L.append(f"class {self.api}{self.cls}Bag : public {self.parent}")
        L.append("{")
        L.append(f"{T}GENERATED_BODY()")
        L.append("")
        L.append("public:")
        body = self.property_lines()
        while body and body[-1] == "":
            body.pop()
        L.extend(body)
        L.append("};")
        return L

    def header(self) -> str:
        m, o = self.m, self.o
        L = self.banner()
        L.append(f"// Module dependencies: {', '.join(self.modules())}")
        L.append("#pragma once")
        L.append("")
        L.append('#include "CoreMinimal.h"')
        L.append(f'#include "{self.parent_include}"')
        if self.hosth:
            L.append('#include "DalHost.h"')
        for lib in m.libs:
            L.append(f'#include "{lib.stem}.h"')
        if self.uses_tags(any_origin=True) or any(isinstance(d.ty, TagType) for d in [*m.params, *m.inputs, *m.states]):
            L.append('#include "GameplayTagContainer.h"')
        if any(t.origin is None for t in m.tagsets):
            L.append('#include "NativeGameplayTags.h"')
        if (any(i.ty is MOVEMENT_MODE for i in [*m.params, *m.inputs, *m.states]) or self.uses("movement_mode")
                or any(q.startswith("trace_") for q in self.queries)):
            L.append('#include "Engine/EngineTypes.h"')
        if self.bind_begin or self.bind_end:
            L.append('#include "Animation/AnimNotifies/AnimNotify.h"')
        L.append(f'#include "{m.name}.generated.h"')
        L.append("")
        fwd: list[str] = []
        # Engine classes named in the declarations of the helper methods below. The header must not lean on what the
        # parent class' header happens to include.
        if self.needs_pawn_helper:
            fwd.append("class APawn;")
        if self.character_cmds or self.movement_cmds:
            fwd.append("class ACharacter;")
        if self.movement_cmds:
            fwd.append("class UCharacterMovementComponent;")
        if self.action_slots():
            fwd.append("class UInputAction;")
        if self.bind_input:
            fwd += ["class UEnhancedInputComponent;", "struct FInputActionValue;"]
        if m.montages:
            fwd.append("class UAnimMontage;")
        if self.actor and self.needs_anim:
            fwd.append("class UAnimInstance;")
        if fwd:
            L.extend(fwd)
            L.append("")
        for en in m.enums:
            if en.origin is None:
                L.extend(self.enum_decl(en))
                L.append("")
        for ts in m.tagsets:
            if ts.origin is None:
                L.extend(self.tags_decl(ts))
                L.append("")
        for st in m.structs:
            if st.origin is None:
                L.extend(self.struct_decl(st))
                L.append("")
        if self.actor:
            L.append("UCLASS(ClassGroup = (Dal), Meta = (BlueprintSpawnableComponent))")
        else:
            L.append("UCLASS()")
        L.append(f"class {self.api}{self.cls} : public {self.parent}")
        L.append("{")
        L.append(f"{T}GENERATED_BODY()")
        L.append("")
        L.append("public:")
        if self.actor:
            L.append(f"{T}{self.cls}();")
            L.append("")
        L.extend(self.property_lines())
        slots = self.action_slots()
        if slots:
            if self.bind_input:
                L.append(f"{T}// ---- enhanced input assets read by host.action(...) or handled by `on input`; assign them in the class defaults ----")
            else:
                L.append(f"{T}// ---- enhanced input assets read by host.action(...); assign them in the class defaults ----")
            for a in slots:
                L.append(f'{T}UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "DAL|Host")')
                L.append(f"{T}TObjectPtr<UInputAction> DalAction_{a};")
                L.append("")
        if m.montages:
            L.append(f"{T}// ---- montage assets declared with `montage Name {{...}};`; assign them in the class defaults ----")
            for md in m.montages:
                L.append(f'{T}UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "DAL|Host")')
                L.append(f"{T}TObjectPtr<UAnimMontage> DalMontage_{md.name};")
                L.append("")
        if any(q.startswith("target_") for q in self.queries):
            L.append(f"{T}// ---- targets: actors carrying this tag are what host.target_*() sees ----")
            L.append(f'{T}UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "DAL|Host")')
            L.append(f'{T}FName DalTargetTag = FName(TEXT("DalTarget"));')
            L.append("")
        if any(q.startswith("trace_") for q in self.queries):
            L.append(f"{T}// ---- the collision channel host.trace_line / host.trace_sphere use ----")
            L.append(f'{T}UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "DAL|Host")')
            L.append(f"{T}TEnumAsByte<ECollisionChannel> DalTraceChannel = ECC_Visibility;")
            L.append("")
        if self.actor:
            L.append(f"{T}virtual void BeginPlay() override;")
            if self.bind_anim:
                L.append(f"{T}virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;")
            L.append(f"{T}virtual void TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction) override;")
        else:
            L.append(f"{T}virtual void NativeInitializeAnimation() override;")
            L.append(f"{T}virtual void NativeUpdateAnimation(float DeltaSeconds) override;")
            L.append(f"{T}virtual void NativeThreadSafeUpdateAnimation(float DeltaSeconds) override;")
        L.append("")
        L.append(f"{T}/** Restore every input and state to its declared default. */")
        L.append(f"{T}void DalResetState();")
        L.append("")
        thread_safe = "" if self.actor else ", Meta = (BlueprintThreadSafe)"
        for e in m.entries:
            L.append(f"{T}/** Entry: call from a .dab with Call(Self.{e.name}). Uses the delta time of the current update. */"
                     if not self.actor else f"{T}/** Entry: callable from Blueprints. Uses the delta time of the last tick. */")
            L.append(f'{T}UFUNCTION(BlueprintCallable, Category = "DAL|Entry"{thread_safe})')
            L.append(f"{T}void {e.name}();")
            L.append("")
        notify_flags = self.notify_names()
        if not self.actor:
            for n in self.anim_notify_funcs():
                if n in notify_flags:
                    L.append(f"{T}/** Animation notify '{n}': read through host.notify(\"{n}\")" + (" and `on notify`" if self.has_events else "") + ". */")
                else:
                    L.append(f"{T}/** Animation notify '{n}': handled by `on notify {n}`. */")
                L.append(f"{T}UFUNCTION()")
                L.append(f"{T}void AnimNotify_{n}();")
                L.append("")
        L.append("protected:")
        L.append(f"{T}/** Game thread: read the host into the input properties. */")
        L.append(f"{T}void DalSampleInputs();")
        L.append("")
        queued = " (host commands are queued)" if (not self.actor and self.has_cmds) else ""
        L.append(f"{T}/** {'Game thread' if self.actor else 'Worker thread'}: the update body. Touches only this object's properties{queued}. */")
        L.append(f"{T}void DalUpdate(float DeltaSeconds);")
        L.append("")
        L.append("private:")
        L.append(f"{T}float Dal_DeltaSeconds = 0.0f;")
        for n in notify_flags:
            L.append(f"{T}bool DalNotify_{n} = false;")
        if self.hosth:
            L.append("")
            if self.has_events or self.has_cmds:
                L.append(f"{T}FCriticalSection DalQueueLock;                   // guards the two queues below")
            if self.has_events:
                L.append(f"{T}TArray<DalHost::FEvent> DalEventQueue;           // posted by the host callbacks")
                L.append(f"{T}TArray<DalHost::FEvent> DalFrameEvents;          // taken for the running update")
            if self.has_cmds:
                L.append(f"{T}TArray<DalHost::FCommand> DalCommandQueue;       // queued by the update, run on the game thread")
            if self.actor and self.bind_anim:
                L.append(f"{T}TWeakObjectPtr<UAnimInstance> DalBoundAnim;")
            if self.actor and self.bind_input:
                L.append(f"{T}TWeakObjectPtr<UEnhancedInputComponent> DalBoundInput;")
            if any(q.startswith("target_") for q in self.queries):
                L.append(f"{T}TArray<TWeakObjectPtr<AActor>> DalTargets;")
                L.append(f"{T}bool bDalTargetsFresh = false;")
        methods = self.methods()
        if methods:
            L.append("")
            for mt in methods:
                L.extend(mt.decl())
                L.append("")
            while L[-1] == "":
                L.pop()
        L.append("};")
        if o.property_bag and not self.actor:
            L.append("")
            L.extend(self.bag_class())
        return "\n".join(L) + "\n"

    # ---- source -------------------------------------------------------------------------------------------------
    def source_includes(self) -> list[str]:
        m = self.m
        inc: list[str] = []

        def add(x: str) -> None:
            if x not in inc:
                inc.append(x)

        if self.actor:
            add("GameFramework/Actor.h")
        if self.uses("time") or self.queries:
            add("Engine/World.h")
        if self.needs_movement_read or self.movement_cmds or self.character_cmds:
            add("GameFramework/Character.h")
            add("GameFramework/CharacterMovementComponent.h")
        if self.uses(*_PAWN_ITEMS) or self.needs_pawn_helper or self.controller or self.arm or self.camera:
            add("GameFramework/Pawn.h")
        if self.action_names():
            for x in ("Engine/LocalPlayer.h", "GameFramework/PlayerController.h", "EnhancedInputSubsystems.h",
                      "EnhancedPlayerInput.h", "InputAction.h", "InputActionValue.h"):
                add(x)
        # ---- v1
        if self.bind_input:
            for x in ("EnhancedInputComponent.h", "InputAction.h", "InputActionValue.h"):
                add(x)
        if self.needs_anim:
            add("Animation/AnimInstance.h")
            add("Animation/AnimMontage.h")
            if self.actor:
                add("Components/SkeletalMeshComponent.h")
        if self.bind_begin:
            add("Animation/AnimTypes.h")
        if self.controller:
            add("GameFramework/Controller.h")
        if self.arm:
            add("GameFramework/SpringArmComponent.h")
        if self.camera:
            add("Camera/CameraComponent.h")
        if any(q.startswith("trace_") for q in self.queries):
            add("CollisionQueryParams.h")
            add("Engine/HitResult.h")
        if any(q.startswith("target_") for q in self.queries):
            add("GameFramework/Actor.h")
            add("Kismet/GameplayStatics.h")
        if self.has_events or self.has_cmds:
            add("Misc/ScopeLock.h")
        if any(t.origin is None for t in m.tagsets):
            add("NativeGameplayTags.h")
        for x in m.externs:
            if x.include and x.origin is None:
                add(x.include)
        return inc

    def namespace_block(self) -> list[str]:
        m = self.m
        v1_ids = bool(m.montages or self.notify_ids() or (self.bind_input and self.action_slots()))
        v1_tables = bool(self.notify_ids() or self.section_cmds or self.section_reads)
        consts = [c for c in m.consts if c.origin is None]
        funcs = [f for f in m.funcs if f.origin is None]
        if not (consts or funcs or self.action_names() or v1_ids or v1_tables):
            return []
        L = ["namespace", "{"]
        for c in consts:
            L.extend(self.const_decl(c, False))
        if consts:
            L.append("")
        for f in funcs:
            L.extend(self.fn_decl(f, False))
            L.append("")
        if self.action_names():
            L.append(f"{T}const UEnhancedPlayerInput* DalGetPlayerInput(const APawn* Pawn)")
            L.append(f"{T}{{")
            L.append(f"{T}{T}const APlayerController* Controller = Pawn != nullptr ? Cast<APlayerController>(Pawn->GetController()) : nullptr;")
            L.append(f"{T}{T}const ULocalPlayer* LocalPlayer = Controller != nullptr ? Controller->GetLocalPlayer() : nullptr;")
            L.append(f"{T}{T}const UEnhancedInputLocalPlayerSubsystem* Subsystem =")
            L.append(f"{T}{T}{T}LocalPlayer != nullptr ? LocalPlayer->GetSubsystem<UEnhancedInputLocalPlayerSubsystem>() : nullptr;")
            L.append(f"{T}{T}return Subsystem != nullptr ? Subsystem->GetPlayerInput() : nullptr;")
            L.append(f"{T}}}")
            L.append("")
        if v1_ids:
            L.append(f"{T}// ---- ids of the host traffic: indices into this logic's tables ----")
            for i, md in enumerate(m.montages):
                L.append(f"{T}[[maybe_unused]] constexpr int32 Dal_MontageId_{md.name} = {i};")
            for i, n in enumerate(self.notify_ids()):
                L.append(f"{T}[[maybe_unused]] constexpr int32 Dal_NotifyId_{n} = {i};")
            if self.bind_input:
                for i, a in enumerate(self.action_slots()):
                    L.append(f"{T}[[maybe_unused]] constexpr int32 Dal_ActionId_{a} = {i};")
            L.append("")
        if self.notify_ids():
            L.append(f"{T}[[maybe_unused]] int32 DalFindNotifyId(const FName Name)")
            L.append(f"{T}{{")
            L.append(f"{T}{T}static const FName Names[] =")
            L.append(f"{T}{T}{{")
            for n in self.notify_ids():
                L.append(f'{T}{T}{T}FName(TEXT("{n}")),')
            L.append(f"{T}{T}}};")
            L.append(f"{T}{T}for (int32 Index = 0; Index < static_cast<int32>(UE_ARRAY_COUNT(Names)); ++Index)")
            L.append(f"{T}{T}{{")
            L.append(f"{T}{T}{T}if (Names[Index] == Name)")
            L.append(f"{T}{T}{T}{{")
            L.append(f"{T}{T}{T}{T}return Index;")
            L.append(f"{T}{T}{T}}}")
            L.append(f"{T}{T}}}")
            L.append(f"{T}{T}return INDEX_NONE;")
            L.append(f"{T}}}")
            L.append("")
        if self.section_cmds or self.section_reads:
            L.append(f"{T}// The sections of a montage, indexed like its section enum (0 = None).")
            L.append(f"{T}[[maybe_unused]] const FName* DalSectionTable(const int32 Montage, int32& OutCount)")
            L.append(f"{T}{{")
            L.append(f"{T}{T}switch (Montage)")
            L.append(f"{T}{T}{{")
            for md in m.montages:
                L.append(f"{T}{T}case Dal_MontageId_{md.name}:")
                L.append(f"{T}{T}{{")
                L.append(f"{T}{T}{T}static const FName Names[] =")
                L.append(f"{T}{T}{T}{{")
                L.append(f"{T}{T}{T}{T}NAME_None,")
                for sec in md.sections:
                    L.append(f'{T}{T}{T}{T}FName(TEXT("{sec}")),')
                L.append(f"{T}{T}{T}}};")
                L.append(f"{T}{T}{T}OutCount = static_cast<int32>(UE_ARRAY_COUNT(Names));")
                L.append(f"{T}{T}{T}return Names;")
                L.append(f"{T}{T}}}")
            L.append(f"{T}{T}default:")
            L.append(f"{T}{T}{T}OutCount = 0;")
            L.append(f"{T}{T}{T}return nullptr;")
            L.append(f"{T}{T}}}")
            L.append(f"{T}}}")
            L.append("")
            if self.section_cmds:
                L.append(f"{T}[[maybe_unused]] FName DalSectionName(const int32 Montage, const int32 Section)")
                L.append(f"{T}{{")
                L.append(f"{T}{T}int32 Count = 0;")
                L.append(f"{T}{T}const FName* Names = DalSectionTable(Montage, Count);")
                L.append(f"{T}{T}return Names != nullptr && Section >= 0 && Section < Count ? Names[Section] : FName(NAME_None);")
                L.append(f"{T}}}")
                L.append("")
            if self.section_reads:
                L.append(f"{T}[[maybe_unused]] int32 DalSectionIndex(const int32 Montage, const FName Section)")
                L.append(f"{T}{{")
                L.append(f"{T}{T}int32 Count = 0;")
                L.append(f"{T}{T}const FName* Names = DalSectionTable(Montage, Count);")
                L.append(f"{T}{T}for (int32 Index = 1; Index < Count; ++Index)")
                L.append(f"{T}{T}{{")
                L.append(f"{T}{T}{T}if (Names[Index] == Section)")
                L.append(f"{T}{T}{T}{{")
                L.append(f"{T}{T}{T}{T}return Index;")
                L.append(f"{T}{T}{T}}}")
                L.append(f"{T}{T}}}")
                L.append(f"{T}{T}return 0;")
                L.append(f"{T}}}")
                L.append("")
        while L[-1] == "":
            L.pop()
        L.append("}")
        return L

    def source(self) -> str:
        m = self.m
        cls = self.cls
        L = self.banner()
        L.append(f'#include "{m.name}.h"')
        L.append("")
        L.append('#include "DalMath.h"')
        for inc in self.source_includes():
            L.append(f'#include "{inc}"')
        L.append("")
        L.append(f"#include UE_INLINE_GENERATED_CPP_BY_NAME({m.name})")
        L.append("")
        own_tag_sets = [t for t in m.tagsets if t.origin is None]
        for k, ts in enumerate(own_tag_sets):
            L.extend(self.tags_define(ts))
            L.append("")
        for lib in m.libs:
            if any(c.origin == lib.key for c in m.consts) or any(f.origin == lib.key for f in m.funcs):
                L.append(f"using namespace DalLib_{lib.stem};")
        if any(c.origin is not None for c in m.consts) or any(f.origin is not None for f in m.funcs):
            L.append("")
        ns = self.namespace_block()
        if ns:
            L.extend(ns)
            L.append("")

        blocks: list[list[str]] = []
        traffic = self.has_events or self.has_cmds

        # ---- lifecycle
        if self.actor:
            blocks.append([f"{cls}::{cls}()", "{",
                           f"{T}PrimaryComponentTick.bCanEverTick = true;",
                           f"{T}PrimaryComponentTick.TickGroup = TG_PrePhysics;", "}"])
            begin = [f"void {cls}::BeginPlay()", "{", f"{T}Super::BeginPlay();", "", f"{T}DalResetState();"]
            if self.bind_anim or self.bind_input:
                begin.append(f"{T}DalBindHost();")
            begin.append("}")
            blocks.append(begin)
            if self.bind_anim:
                blocks.append([f"void {cls}::EndPlay(const EEndPlayReason::Type EndPlayReason)", "{",
                               f"{T}if (DalBoundAnim.IsValid())", f"{T}{{",
                               f"{T}{T}DalUnbindAnim(DalBoundAnim.Get());", f"{T}}}",
                               f"{T}DalBoundAnim.Reset();", "",
                               f"{T}Super::EndPlay(EndPlayReason);", "}"])
            tick = [f"void {cls}::TickComponent(const float DeltaTime, const ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)", "{",
                    f"{T}Super::TickComponent(DeltaTime, TickType, ThisTickFunction);", ""]
            if any(q.startswith("target_") for q in self.queries):
                tick.append(f"{T}bDalTargetsFresh = false;")
            if self.bind_anim or self.bind_input:
                tick.append(f"{T}DalBindHost();")
            tick.append(f"{T}DalSampleInputs();")
            if self.has_events:
                tick.append(f"{T}DalTakeEvents();")
            tick.append(f"{T}Dal_DeltaSeconds = DeltaTime;")
            tick.append(f"{T}DalUpdate(DeltaTime);")
            if self.has_cmds:
                tick.append(f"{T}DalFlushCommands();")
            tick.append("}")
            blocks.append(tick)
        else:
            init = [f"void {cls}::NativeInitializeAnimation()", "{", f"{T}Super::NativeInitializeAnimation();", "", f"{T}DalResetState();"]
            if self.bind_anim:
                init.append(f"{T}DalBindAnim(this);")
            init.append("}")
            blocks.append(init)
            upd = [f"void {cls}::NativeUpdateAnimation(const float DeltaSeconds)", "{",
                   f"{T}Super::NativeUpdateAnimation(DeltaSeconds);", ""]
            if traffic:
                upd.append(f"{T}// Game thread: run what the previous update asked for, gather the host values and the events;")
                upd.append(f"{T}// the worker thread only reads this object.")
                if self.has_cmds:
                    upd.append(f"{T}DalFlushCommands();")
                upd.append(f"{T}DalSampleInputs();")
                if self.has_events:
                    upd.append(f"{T}DalTakeEvents();")
            else:
                upd.append(f"{T}// Game thread: gather the host values; the worker thread only reads this object.")
                upd.append(f"{T}DalSampleInputs();")
            upd.append("}")
            blocks.append(upd)
            blocks.append([f"void {cls}::NativeThreadSafeUpdateAnimation(const float DeltaSeconds)", "{",
                           f"{T}Super::NativeThreadSafeUpdateAnimation(DeltaSeconds);", "",
                           f"{T}Dal_DeltaSeconds = DeltaSeconds;",
                           f"{T}DalUpdate(DeltaSeconds);", "}"])

        # ---- reset
        reset = [f"void {cls}::DalResetState()", "{", f"{T}Dal_DeltaSeconds = 0.0f;"]
        for n in self.notify_names():
            reset.append(f"{T}DalNotify_{n} = false;")
        for i in m.inputs:
            reset.append(f"{T}{i.name} = {cpp_value(i.ty, i.default)};")
        for s in m.states:
            reset.append(f"{T}{s.name} = {cpp_value(s.ty, s.default)};")
        if self.has_events:
            reset.append(f"{T}DalEventQueue.Reset();")
            reset.append(f"{T}DalFrameEvents.Reset();")
        if self.has_cmds:
            reset.append(f"{T}DalCommandQueue.Reset();")
        if any(q.startswith("target_") for q in self.queries):
            reset.append(f"{T}DalTargets.Reset();")
            reset.append(f"{T}bDalTargetsFresh = false;")
        reset.append("}")
        blocks.append(reset)
        blocks.append(self.sample_function())

        # ---- v1 members (helpers, events, commands, binding, queries)
        for mt in self.methods():
            if mt.name == "DalDispatchEvents" or mt.name.startswith("DalHandler_"):
                continue
            blocks.append(mt.define(cls))

        # ---- update
        u = m.update
        upd_lines = [f"void {cls}::DalUpdate([[maybe_unused]] const float {u.dt_name if u else 'DeltaSeconds'})", "{"]
        body: list[str] = []
        if u is not None and u.pre:
            body.extend(self.stmts(u.pre, 1))
        if self.has_events:
            body.append(f"{T}DalDispatchEvents();")
        if u is not None:
            body.extend(self.stmts(u.body, 1))
        if not body and u is None:
            body.append(f"{T}// (no update body)")
        upd_lines.extend(body)
        upd_lines.append("}")
        for mt in self.methods():
            if mt.name == "DalDispatchEvents" or mt.name.startswith("DalHandler_"):
                blocks.append(mt.define(cls))
        blocks.append(upd_lines)
        for e in m.entries:
            lines = [f"void {cls}::{e.name}()", "{",
                     f"{T}[[maybe_unused]] const float {e.dt_name} = Dal_DeltaSeconds;"]
            lines.extend(self.stmts(e.body, 1))
            if self.actor and self.has_cmds:
                lines.append(f"{T}DalFlushCommands();")
            lines.append("}")
            blocks.append(lines)
        if not self.actor:
            handled = {h.name for h in m.handlers if h.event == "notify"}
            flags = set(self.notify_names())
            for n in self.anim_notify_funcs():
                lines = [f"void {cls}::AnimNotify_{n}()", "{"]
                if n in flags:
                    lines.append(f"{T}DalNotify_{n} = true;")
                if n in handled:
                    lines += [f"{T}DalHost::FEvent DalEvent;",
                              f"{T}DalEvent.Kind = DalHost::EEventKind::Notify;",
                              f"{T}DalEvent.Id = Dal_NotifyId_{n};",
                              f"{T}DalPostEvent(DalEvent);"]
                lines.append("}")
                blocks.append(lines)
        for k, blk in enumerate(blocks):
            if k:
                L.append("")
            L.extend(blk)
        return "\n".join(L) + "\n"

    def sample_function(self) -> list[str]:
        m = self.m
        L = [f"void {self.cls}::DalSampleInputs()", "{"]
        host = self.host_items()
        if not host:
            L.append(f"{T}// (no host-bound inputs)")
            L.append("}")
            return L
        needs_movement = self.needs_movement_read
        needs_pawn = needs_movement or self.uses(*_PAWN_ITEMS)
        if needs_pawn:
            L.append(f"{T}const APawn* Pawn = " + ("Cast<APawn>(GetOwner());" if self.actor else "TryGetPawnOwner();"))
        if needs_movement:
            L.append(f"{T}const ACharacter* Character = Cast<ACharacter>(Pawn);")
            L.append(f"{T}const UCharacterMovementComponent* Movement = IsValid(Character) ? Character->GetCharacterMovement() : nullptr;")
        if self.action_names():
            L.append(f"{T}const UEnhancedPlayerInput* PlayerInput = DalGetPlayerInput(Pawn);")
        if self.uses("time"):
            L.append(f"{T}const UWorld* World = GetWorld();")
        if self.montage_reads:
            L.append(f"{T}const UAnimInstance* DalAnim = DalGetAnimInstance();")
        L.append("")
        for i, h in host:
            default = cpp_value(i.ty, i.default)
            name = i.name
            it = h.item
            if it == "time":
                L.append(f"{T}{name} = World != nullptr ? static_cast<float>(World->GetTimeSeconds()) : 0.0f;")
                continue
            if it == "notify":
                L.append(f"{T}{name} = DalNotify_{h.arg};")
                L.append(f"{T}DalNotify_{h.arg} = false;")
                continue
            if it.startswith("montage_"):
                slot = f"DalMontage_{h.arg}"
                L.append(f"{T}{name} = {default};")
                cond = f"DalAnim != nullptr && {slot} != nullptr"
                if it == "montage_playing":
                    value = f"DalAnim->Montage_IsPlaying({slot})"
                elif it == "montage_active":
                    value = f"DalAnim->Montage_IsActive({slot})"
                elif it == "montage_position":
                    value = f"DalAnim->Montage_GetPosition({slot})"
                else:
                    cond += f" && DalAnim->Montage_IsPlaying({slot})"
                    value = f"static_cast<{i.ty.cpp_type}>(DalSectionIndex(Dal_MontageId_{h.arg}, DalAnim->Montage_GetCurrentSection({slot})))"  # type: ignore[attr-defined]
                L.append(f"{T}if ({cond})")
                L.append(f"{T}{{")
                L.append(f"{T}{T}{name} = {value};")
                L.append(f"{T}}}")
                continue
            movement_expr = {
                "velocity": "FVector3f(Movement->Velocity)",
                "acceleration": "FVector3f(Movement->GetCurrentAcceleration())",
                "max_acceleration": "Movement->GetMaxAcceleration()",
                "max_braking_deceleration": "Movement->GetMaxBrakingDeceleration()",
                "max_walk_speed": "Movement->MaxWalkSpeed",
                "movement_mode": "Movement->MovementMode",
                "is_grounded": "Movement->IsMovingOnGround()",
                "is_crouching": "Movement->IsCrouching()",
            }
            pawn_expr = {
                "actor_location": "FVector3f(Pawn->GetActorLocation())",
                "actor_rotation": "FQuat4f(Pawn->GetActorQuat())",
                "control_rotation": "FRotator3f(Pawn->GetControlRotation())",
            }
            if it in movement_expr:
                L.append(f"{T}{name} = {default};")
                L.append(f"{T}if (Movement != nullptr)")
                L.append(f"{T}{{")
                L.append(f"{T}{T}{name} = {movement_expr[it]};")
                L.append(f"{T}}}")
            elif it in pawn_expr:
                L.append(f"{T}{name} = {default};")
                L.append(f"{T}if (Pawn != nullptr)")
                L.append(f"{T}{{")
                L.append(f"{T}{T}{name} = {pawn_expr[it]};")
                L.append(f"{T}}}")
            elif it == "action":
                getter = {
                    BOOL: ".Get<bool>()",
                    FLOAT: ".Get<float>()",
                    VEC2: ".Get<FVector2D>()",
                    VEC3: ".Get<FVector>()",
                }[i.ty]
                value = f"PlayerInput->GetActionValue(DalAction_{h.arg}){getter}"
                if i.ty is VEC2:
                    value = f"FVector2f({value})"
                elif i.ty is VEC3:
                    value = f"FVector3f({value})"
                L.append(f"{T}{name} = {default};")
                L.append(f"{T}if (PlayerInput != nullptr && DalAction_{h.arg} != nullptr)")
                L.append(f"{T}{{")
                L.append(f"{T}{T}{name} = {value};")
                L.append(f"{T}}}")
            else:
                raise AssertionError(it)
        L.append("}")
        return L


def generate_cpp(module: ir.Module, options: CppOptions | None = None) -> dict[str, str]:
    """Return ``{filename: text}`` for the generated header, source, the math header and (v1 host traffic) DalHost.h."""
    options = options or CppOptions()
    g = CppGen(module, options)
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cpp")

    def read(name: str) -> str:
        with open(os.path.join(here, name), "r", encoding="utf-8", newline="") as f:
            return f.read().replace("\r\n", "\n")

    files: dict[str, str] = {}
    if module.is_library:
        files.update(g.lib_files(None, module.name, module.filename, module.source_sha256))
    else:
        files[f"{module.name}.h"] = g.header()
        files[f"{module.name}.cpp"] = g.source()
    for lib in module.libs:
        files.update(g.lib_files(lib.key, lib.stem, lib.filename, lib.sha256))
    files["DalMath.h"] = read("DalMath.h")
    if g.hosth:
        files["DalHost.h"] = read("DalHost.h")
    return files
