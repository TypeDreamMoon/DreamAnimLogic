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

import contextlib
import os
import struct
from dataclasses import dataclass, field

from . import ir
from .builtins import BUILTIN_CONSTS, BUILTINS
from .hostapi import COMMANDS, HOOK_EVENTS, QUERIES
from .names import SAMPLE_LOCALS
from .types import (
    VOID,
    BOOL,
    CHANNEL,
    CHANNEL_SET,
    FLOAT,
    HIT,
    INT,
    MOVEMENT_MODE,
    MONTAGE_ASSET,
    NAME_T,
    NONE_T,
    SPRING,
    SPRING3,
    TRANSFORM,
    QUAT,
    ROTATOR,
    VEC2,
    VEC3,
    ArrayType,
    AssetType,
    EnumType,
    MapType,
    StructType,
    TagType,
    Type,
    zero_value,
)

_SCENE_QUERIES = ("sweep_capsule", "sweep_sphere", "line_trace", "overlap_capsule")          # the v3 queries that ask the physics scene
_COMPONENT_QUERIES = ("primitive_valid", "primitive_speed", "primitive_can_step_up", "primitive_transform")
_NEW_HIT_MEMBERS = ("location", "start_penetrating", "primitive", "face_normal")

T = "\t"

LIFECYCLE = ("begin_play", "end_play")


@dataclass
class CppOptions:
    module_api: str = ""                           # e.g. "MYGAME_API"
    parent_class: str = ""                         # "" = UAnimInstance for anim logic, UActorComponent for actor logic
    parent_include: str = ""                       # "" = Animation/AnimInstance.h resp. Components/ActorComponent.h
    property_bag: bool = False                     # anim logic: also emit U<Logic>Bag, the same UPROPERTYs without the logic (DalLive)
    double_members: bool = False                   # vector / rotator / quat / transform members of structs are the double-precision engine types (MEMBER_TYPES)
    object_settings: tuple = ()                    # names of settings types an asset holds as Instanced UObjects: U<Name> (the object) + F<Name>Value (what the logic reads)
    native_structs: tuple = ()                     # "Name=FEngineType[=Header]": a struct that is an engine type with the same members (FFloatInterval); not declared
    channel_defaults: tuple = ()                   # "Struct.Member=Channel,Channel": the default of a channel_set member of a settings type (the constructor of the original sets it)


KIND_PARENT = {
    "anim": ("UAnimInstance", "Animation/AnimInstance.h"),
    "actor": ("UActorComponent", "Components/ActorComponent.h"),
}

TRANSFORM_GETTER = {"location": "GetLocation", "rotation": "GetRotation", "scale": "GetScale3D"}
HIT_MEMBER_CPP = {"blocking": "bBlocking", "point": "Point", "normal": "Normal", "distance": "Distance", "time": "Time",
                  "location": "Location", "start_penetrating": "bStartPenetrating", "primitive": "Primitive", "face_normal": "FaceNormal"}


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
    if ty is TRANSFORM:
        return "FTransform3f"
    if ty is HIT:
        return "DalHost::FHit"
    if ty is SPRING:
        return "DalMath::FSpring"
    if ty is SPRING3:
        return "DalMath::FSpring3"
    if isinstance(ty, EnumType):
        return ty.cpp_type
    if isinstance(ty, StructType):
        return ty.cpp_type
    if isinstance(ty, TagType):
        return "FGameplayTag"
    if isinstance(ty, AssetType):
        return ty.cpp_class if ty.by_value else f"TObjectPtr<{ty.cpp_class}>"
    if isinstance(ty, MapType):
        return f"TMap<{cpp_prop_type(ty.key)}, {cpp_prop_type(ty.value)}>"
    if isinstance(ty, ArrayType):
        return f"TArray<{cpp_prop_type(ty.elem)}>"
    raise ValueError(ty)


def holds_tags(ty: Type) -> bool:
    """A tag, or a table / list that has tags in its keys, values or elements."""
    if isinstance(ty, TagType):
        return True
    if isinstance(ty, MapType):
        return holds_tags(ty.key) or holds_tags(ty.value)
    if isinstance(ty, ArrayType):
        return holds_tags(ty.elem)
    return False


_ASSET_HEADERS = {"sequence": "Animation/AnimSequenceBase.h", "montage": "Animation/AnimMontage.h",
                  "curve": "Curves/CurveFloat.h", "curve3": "Curves/CurveVector.h", "primitive": "Components/PrimitiveComponent.h",
                  "snapshot": "Animation/PoseSnapshot.h"}


def asset_headers(ty: Type, out: list[str]) -> None:
    """The engine headers that declare the asset classes named by a (member) type."""
    if isinstance(ty, AssetType):
        if _ASSET_HEADERS[ty.name] not in out:
            out.append(_ASSET_HEADERS[ty.name])
    elif isinstance(ty, MapType):
        asset_headers(ty.key, out)
        asset_headers(ty.value, out)
    elif isinstance(ty, ArrayType):
        asset_headers(ty.elem, out)


def needs_recompute(ty: Type) -> bool:
    """The settings type has derived members (itself, or in a settings type it holds)."""
    if isinstance(ty, MapType):
        return needs_recompute(ty.value)
    if isinstance(ty, ArrayType):
        return needs_recompute(ty.elem)
    if isinstance(ty, StructType) and ty.is_settings:
        return any(f.derived or needs_recompute(f.ty) for f in ty.fields)
    return False


def is_by_ref(ty: Type) -> bool:
    """Settings values and their tables / lists are passed and bound by reference (they are data, often large)."""
    return (isinstance(ty, StructType) and ty.is_settings) or isinstance(ty, (MapType, ArrayType))


def cpp_ufunc_type(ty: Type) -> str:
    """The type of a UFUNCTION parameter or return value: UHT does not accept TObjectPtr there, only the raw pointer."""
    if isinstance(ty, AssetType) and not ty.by_value:
        return f"{ty.cpp_class}*"
    return cpp_type(ty)


def cpp_param(ty: Type, name: str, unused: bool = False, ufunc: bool = False) -> str:
    """One parameter of a function or method (ufunc: of a UFUNCTION)."""
    mark = "[[maybe_unused]] " if unused else ""
    if isinstance(ty, StructType) and ty.is_notify:
        return f"{mark}const {ty.cpp_type}*{' const' if unused else ''} {name}"       # the notify object: its properties are the payload
    if is_by_ref(ty):
        return f"{mark}const {cpp_type(ty)}& {name}"
    if ufunc and isinstance(ty, AssetType) and not ty.by_value:
        return f"{mark}{cpp_ufunc_type(ty)}{' const' if unused else ''} {name}"
    return f"{mark}{'const ' if unused else ''}{cpp_type(ty)} {name}"


def cpp_params(params, unused: bool = False, ufunc: bool = False) -> str:
    return ", ".join(cpp_param(t, n, unused, ufunc) for n, t in params)


def enum_cast(ty: Type) -> str | None:
    """The engine enum a TEnumAsByte<...> property is cast to when it is read (EMovementMode, ECollisionChannel), or None."""
    if isinstance(ty, EnumType) and ty.cpp_prop_type != ty.cpp_type:
        return ty.cpp_type
    return None


def cpp_prop_type(ty: Type) -> str:
    return ty.cpp_prop_type if isinstance(ty, EnumType) else cpp_type(ty)


# The members of structs (state structs, settings, notify payloads) are what Blueprint pins, Control Rig variables and data assets see.  ALS's own
# structs have the double-precision types there (FVector, FQuat, ...), and a Control Rig does not convert a pin of FVector3f to one of FVector, so
# with CppOptions.double_members the members have the engine's default types while the logic keeps computing in the single-precision ones: every
# read of such a member converts to the logic's type, every write converts back.  Top-level states, inputs and params keep the logic's types.
MEMBER_TYPES: dict = {}                              # filled below, after the types are known
_DOUBLE_MEMBERS = False                              # set by generate_cpp for the duration of one generation


def double_member(ty: Type) -> bool:
    return _DOUBLE_MEMBERS and ty in MEMBER_TYPES


def member_type(ty: Type) -> str:
    """C++ type of a member of a struct."""
    return MEMBER_TYPES[ty] if double_member(ty) else cpp_prop_type(ty)


def to_member(text: str, ty: Type) -> str:
    """An expression of the logic's type, stored into a member of a struct."""
    return f"{MEMBER_TYPES[ty]}({text})" if double_member(ty) else text


def from_member(text: str, ty: Type) -> str:
    """A member of a struct, as the logic's type."""
    return f"{cpp_type(ty)}({text})" if double_member(ty) else text


# Settings that must read what an asset made with the original ALS holds.  The original has three things in its settings types that a plain struct
# of the same members does not give (the property is skipped when the asset is loaded, or the asset does not hold it and the default is another):
#   * UAlsTurnInPlaceSettings is an Instanced UObject: the asset stores a sub-object of that class (CppOptions.object_settings);
#   * FFloatInterval is the engine's, here a struct of two floats (CppOptions.native_structs);
#   * the collision channels of the mantling and the ground prediction are defaults the constructors of UAlsCharacterSettings and
#     UAlsAnimationInstanceSettings set, so no asset holds them (CppOptions.channel_defaults).
_OBJECT_SETTINGS: frozenset = frozenset()                  # set by generate_cpp for the duration of one generation
_CHANNEL_DEFAULTS: dict = {}                               # (struct, member) -> the C++ names of the channels
_NATIVE_STRUCTS: dict = {}                                 # name -> (C++ type, header or "")


def object_settings(ty: Type) -> bool:
    """A settings type the assets hold as an object: its members are TObjectPtr<U<Name>> (null = the defaults), the logic reads F<Name>Value."""
    return isinstance(ty, StructType) and ty.is_settings and ty.name in _OBJECT_SETTINGS


def settings_member_type(ty: Type) -> str:
    """C++ type of a member of a settings type."""
    if object_settings(ty):
        return f"TObjectPtr<U{ty.name}>"
    return member_type(ty)


def settings_member_read(text: str, owner: Type, ty: Type) -> str:
    """A member of a struct, as the logic's type (a member of a settings type may be an object)."""
    if isinstance(owner, StructType) and owner.is_settings and object_settings(ty):
        return f"DalValueOf({text})"
    return from_member(text, ty)


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
    if ty is TRANSFORM:
        return "FTransform3f(%s, %s, %s)" % (cpp_value(QUAT, v[1]), cpp_value(VEC3, v[0]), cpp_value(VEC3, v[2]))
    if isinstance(ty, EnumType):
        return ty.cpp_members[ty.members[int(v)]]
    if isinstance(ty, StructType):
        return cpp_struct_value(ty, v)
    if isinstance(ty, TagType):
        return cpp_tag_value(ty, v)
    if isinstance(ty, AssetType):
        return f"{ty.cpp_class}()" if ty.by_value else "nullptr"
    if ty is NAME_T:
        return f'FName(TEXT("{v}"))'
    if isinstance(ty, (MapType, ArrayType)):
        return f"{cpp_type(ty)}()"
    raise ValueError(ty)


MEMBER_TYPES.update({VEC2: "FVector2D", VEC3: "FVector", ROTATOR: "FRotator", QUAT: "FQuat", TRANSFORM: "FTransform"})


def member_value(ty: Type, v) -> str:
    """The default value of a member of a struct."""
    if not double_member(ty):
        return cpp_value(ty, v)
    if ty is TRANSFORM:
        return "FTransform(%s, %s, %s)" % (member_value(QUAT, v[1]), member_value(VEC3, v[0]), member_value(VEC3, v[2]))
    return "%s(%s)" % (MEMBER_TYPES[ty], ", ".join(fmt_float(c) for c in v))


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

    diffs = [f for f in ty.fields
             if not f.derived and not isinstance(f.ty, (AssetType, MapType, ArrayType)) and not _plain_equal(getattr(v, f.name), values.member_default(f))]
    if not diffs:
        return f"{ty.cpp_type}()"
    sets = " ".join(f"{var}.{f.name} = {member_value(f.ty, getattr(v, f.name))};" for f in diffs)
    return f"[]() {{ {ty.cpp_type} {var}; {sets} return {var}; }}()"


def member_init(ty: Type, v, in_struct: bool = False) -> tuple[str, str | None]:
    """A default member initializer for a UPROPERTY: ``(" = value", None)``, or ``("", "value")`` when the value is an immediately-invoked
    lambda (a struct with non-default members): UHT cannot parse those inside a USTRUCT / UCLASS, so the value goes into a constructor.
    in_struct: a member of a struct (with CppOptions.double_members it has the engine's own types)."""
    text = member_value(ty, v) if in_struct else cpp_value(ty, v)
    if "[]()" in text:
        return "", text
    return f" = {text}", None


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
            if enum_cast(e.ty):
                return f"static_cast<{enum_cast(e.ty)}>({e.name})"
            if k is ir.ParamRef and isinstance(e.ty, StructType) and e.ty.asset_root:
                return f"DalSettings_{e.name}()"                    # the data asset (or the class defaults when none is assigned)
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
            if e.ctor == "transform":
                return f"FTransform3f({a[1]}, {a[0]}{', ' + a[2] if len(a) > 2 else ''})"
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
        if k is ir.PeerValid:
            return f"({e.peer} != nullptr)"
        if k is ir.Valid:
            if e.x.ty.is_asset:
                return f"{self.x(e.x)}.bIsValid" if e.x.ty.by_value else f"IsValid({unparen(self.x(e.x))})"
            return f"{self.x(e.x)}.IsValid()"
        if k is ir.CurveEval:
            fn = "EvalCurve" if e.ty is FLOAT else "EvalCurve3"
            return f"DalData::{fn}({unparen(self.x(e.curve))}, {unparen(self.x(e.x))})"
        if k is ir.Index:
            fn = "MapGet" if isinstance(e.base.ty, MapType) else "ArrayGet"
            return f"DalData::{fn}({unparen(self.x(e.base))}, {unparen(self.x(e.index))}, {self.default_ref(e.ty)})"
        if k is ir.MapHas:
            return f"{self.x(e.table)}.Contains({unparen(self.x(e.key))})"
        if k is ir.ArrayLen:
            return f"{self.x(e.items)}.Num()"
        if k is ir.CallMethod or k is ir.CallView:
            return f"{self.method_name(e.name)}({', '.join(unparen(self.x(a)) for a in e.args)})"
        if k is ir.CallPeer:
            call = self.peer_invoke(e)
            if e.ty is VOID:
                return call
            return f"({e.peer} != nullptr ? {call} : {cpp_value(e.ty, zero_value(e.ty))})"
        if k is ir.MakeStruct:
            return self.make_struct(e)
        raise AssertionError(k)

    def default_ref(self, ty: Type) -> str:
        """What a missing table entry / list element reads as, as an expression of the element type."""
        if isinstance(ty, StructType):
            return f"DalData::DefaultOf<{ty.cpp_type}>()"
        if isinstance(ty, AssetType):
            return f"{cpp_type(ty)}()"
        return cpp_value(ty, zero_value(ty))

    def method_name(self, name: str) -> str:
        """C++ name of a method or view: the DAL name when it is pub, a private Dal... name otherwise."""
        x = self.m.find_method(name)
        if x is None or x.pub:
            return name
        return ("DalView_" if x.is_view else "DalMethod_") + name

    def peer_invoke(self, e: ir.CallPeer) -> str:
        """`Peer->Method(args)`; a call of an animation instance goes through its queue (`DalQueue_Method`)."""
        args = ", ".join(unparen(self.x(a)) for a in e.args)
        return f"{e.peer}->{'DalQueue_' if e.queued else ''}{e.method}({args})"

    def make_struct(self, e: ir.MakeStruct) -> str:
        var = f"Dal_S{self.struct_depth}"
        self.struct_depth += 1
        sets = " ".join(f"{var}.{n} = {to_member(unparen(self.x(v)), v.ty)};" for n, v in e.fields)
        self.struct_depth -= 1
        return f"([&]() {{ {cpp_type(e.ty)} {var}; {sets} return {var}; }})()" if sets else f"{cpp_type(e.ty)}()"

    def member(self, e: ir.Member) -> str:
        base = self.x(e.x)
        if isinstance(e.x.ty, StructType):
            text = f"{base}->{e.comps}" if e.x.ty.is_notify else f"{base}.{e.comps}"
            return f"static_cast<{enum_cast(e.ty)}>({text})" if enum_cast(e.ty) else settings_member_read(text, e.x.ty, e.ty)
        if e.x.ty is HIT:
            return f"{base}.{HIT_MEMBER_CPP[e.comps]}"
        if e.x.ty is SPRING or e.x.ty is SPRING3:
            return f"{base}.{e.comps.capitalize()}"
        if e.x.ty is TRANSFORM:
            return f"{base}.{TRANSFORM_GETTER[e.comps]}()"
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
_G_RESOLVED = "if (UAnimMontage* DalAsset = DalResolveMontage(DalCommand))"       # a program that passes `montage` values: the asset of the command or of the declared montage

_G_ALS_MOVEMENT = "if (UDalCharacterMovementComponent* DalAlsMovement = Cast<UDalCharacterMovementComponent>(DalMovement))"      # the movement component of DalRuntime
_G_RAGDOLL = "if (UDalRagdollService* DalRagdoll = DalPawn->FindComponentByClass<UDalRagdollService>())"                         # the ragdoll service of DalRuntime

EXEC: dict[str, tuple[list[str], list[str]]] = {
    "set_max_walk_speed_crouched": ([_G_MOVEMENT], ["DalMovement->MaxWalkSpeedCrouched = DalCommand.F;"]),
    "set_max_acceleration_walking": ([_G_MOVEMENT, _G_ALS_MOVEMENT], ["DalAlsMovement->MaxAccelerationWalking = DalCommand.F;"]),
    "set_ground_friction": ([_G_MOVEMENT], ["DalMovement->GroundFriction = DalCommand.F;"]),
    "set_braking_friction_factor": ([_G_MOVEMENT], ["DalMovement->BrakingFrictionFactor = DalCommand.F;"]),
    "set_input_blocked": ([_G_MOVEMENT, _G_ALS_MOVEMENT], ["DalAlsMovement->bInputBlocked = DalCommand.B0;"]),
    "set_actor_rotation": ([_G_PAWN], ["DalPawn->SetActorRotation(FRotator(DalCommand.R), DalCommand.B0 ? ETeleportType::TeleportPhysics : ETeleportType::None);"]),
    "add_controller_yaw_input": ([_G_PAWN], ["DalPawn->AddControllerYawInput(DalCommand.F);"]),
    "add_controller_pitch_input": ([_G_PAWN], ["DalPawn->AddControllerPitchInput(DalCommand.F);"]),
    "set_max_walk_speed": ([_G_MOVEMENT], ["DalMovement->MaxWalkSpeed = DalCommand.F;"]),
    "set_max_acceleration": ([_G_MOVEMENT], ["DalMovement->MaxAcceleration = DalCommand.F;"]),
    "set_braking_deceleration_walking": ([_G_MOVEMENT], ["DalMovement->BrakingDecelerationWalking = DalCommand.F;"]),
    "set_gravity_scale": ([_G_MOVEMENT], ["DalMovement->GravityScale = DalCommand.F;"]),
    "set_jump_z_velocity": ([_G_MOVEMENT], ["DalMovement->JumpZVelocity = DalCommand.F;"]),
    "set_air_control": ([_G_MOVEMENT], ["DalMovement->AirControl = DalCommand.F;"]),
    "set_rotation_rate": ([_G_MOVEMENT], ["DalMovement->RotationRate = FRotator(DalCommand.R);"]),
    "set_orient_rotation_to_movement": ([_G_MOVEMENT], ["DalMovement->bOrientRotationToMovement = DalCommand.B0;"]),
    "set_use_controller_desired_rotation": ([_G_MOVEMENT], ["DalMovement->bUseControllerDesiredRotation = DalCommand.B0;"]),
    "set_movement_mode": ([_G_MOVEMENT], ["DalMovement->SetMovementMode(static_cast<EMovementMode>(DalCommand.Mode));"]),        # (with a sub mode: see execute_body)
    "root_motion_override": ([_G_MOVEMENT, _G_ALS_MOVEMENT], ["DalAlsMovement->SetRootMotionOverride(FVector(DalCommand.V), FQuat(DalCommand.Q));"]),
    "root_motion_clear": ([_G_MOVEMENT, _G_ALS_MOVEMENT], ["DalAlsMovement->ClearRootMotionOverride();"]),
    "set_movement_mode_locked": ([_G_MOVEMENT, _G_ALS_MOVEMENT], ["DalAlsMovement->bMovementModeLocked = DalCommand.B0;"]),
    "set_actor_location": ([_G_PAWN], ["DalPawn->SetActorLocation(FVector(DalCommand.V), DalCommand.B0, nullptr, DalCommand.B1 ? ETeleportType::TeleportPhysics : ETeleportType::None);"]),
    "set_velocity": ([_G_MOVEMENT], ["DalMovement->Velocity = FVector(DalCommand.V);"]),
    "set_anim_root_motion_translation_scale": ([_G_CHARACTER], ["DalCharacter->SetAnimRootMotionTranslationScale(DalCommand.F);"]),
    "ragdoll_begin": ([_G_PAWN, _G_RAGDOLL], ["DalRagdoll->Begin(DalCommand.B0, DalCommand.F);"]),
    "ragdoll_end": ([_G_PAWN, _G_RAGDOLL], ["DalRagdoll->End();"]),
    "ragdoll_limit_speed": ([_G_PAWN, _G_RAGDOLL], ["DalRagdoll->LimitSpeed(DalCommand.F);"]),
    "ragdoll_set_motor_strength": ([_G_PAWN, _G_RAGDOLL], ["DalRagdoll->SetMotorStrength(DalCommand.F);"]),
    "ragdoll_add_force": ([_G_PAWN, _G_RAGDOLL], ["DalRagdoll->AddForce(FVector(DalCommand.V), DalCommand.B0);"]),
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
    "montage_set_position": ([_G_ANIM, _G_ASSET], ["DalAnim->Montage_SetPosition(DalAsset, DalCommand.F);"]),
    "montage_stop_source": ([_G_ANIM, "if (FAnimMontageInstance* DalInstance = DalAnim->GetMontageInstanceForID(DalCommand.Instance))",
                             "if (const UAnimMontage* DalMontage = DalInstance->Montage.Get())"], [
        "FMontageBlendSettings DalBlendOut(DalMontage->BlendOut);",
        "if (DalCommand.F >= 0.0f)",
        "{",
        "\tDalBlendOut.Blend.BlendTime = DalCommand.F;",
        "}",
        "DalBlendOut.BlendMode = DalMontage->BlendModeOut;",
        "DalBlendOut.BlendProfile = DalMontage->BlendProfileOut;",
        "DalInstance->Stop(DalBlendOut);",
    ]),
    "play_slot_animation": ([_G_ANIM, "if (UAnimSequenceBase* DalSequence = DalCommand.Sequence)"], [
        "if (DalCommand.Mode == static_cast<int32>(EMontageBlendMode::Inertialization))",
        "{",
        "\tconst FMontageBlendSettings DalBlendIn(DalCommand.F);",
        "\tFMontageBlendSettings DalBlendOut(DalCommand.F1);",
        "\tDalBlendOut.BlendMode = EMontageBlendMode::Inertialization;",
        "\tDalAnim->PlaySlotAnimationAsDynamicMontage_WithBlendSettings(DalSequence, DalCommand.Name, DalBlendIn, DalBlendOut, DalCommand.F2, DalCommand.Count, DalCommand.F3, DalCommand.F4);",
        "}",
        "else",
        "{",
        "\tDalAnim->PlaySlotAnimationAsDynamicMontage(DalSequence, DalCommand.Name, DalCommand.F, DalCommand.F1, DalCommand.F2, DalCommand.Count, DalCommand.F3, DalCommand.F4);",
        "}",
    ]),
    "stop_montages_with_slot": ([_G_ANIM], [
        "for (FAnimMontageInstance* DalInstance : DalAnim->MontageInstances)",
        "{",
        "\tconst UAnimMontage* DalMontage = DalInstance != nullptr && DalInstance->IsActive() ? DalInstance->Montage.Get() : nullptr;",
        "\tif (DalMontage == nullptr)",
        "\t{",
        "\t\tcontinue;",
        "\t}",
        "\tfor (const FSlotAnimationTrack& DalTrack : DalMontage->SlotAnimTracks)",
        "\t{",
        "\t\tif (DalTrack.SlotName != DalCommand.Name)",
        "\t\t{",
        "\t\t\tcontinue;",
        "\t\t}",
        "\t\tFMontageBlendSettings DalBlendOut(DalMontage->BlendOut);",
        "\t\tif (DalCommand.F >= 0.0f)",
        "\t\t{",
        "\t\t\tDalBlendOut.Blend.BlendTime = DalCommand.F;",
        "\t\t}",
        "\t\tDalBlendOut.BlendMode = DalMontage->BlendModeOut;",
        "\t\tDalBlendOut.BlendProfile = DalMontage->BlendProfileOut;",
        "\t\tDalInstance->Stop(DalBlendOut);",
        "\t\tbreak;",
        "\t}",
        "}",
    ]),
    "snapshot_pose": ([_G_ANIM], []),             # the body is written per program: one FPoseSnapshot property per name (execute_body)
    "set_control_rotation": ([_G_PAWN, _G_CONTROLLER], ["DalController->SetControlRotation(FRotator(DalCommand.R));"]),
    "camera_set_arm_length": ([_G_PAWN, _G_ARM], ["DalArm->TargetArmLength = DalCommand.F;"]),
    "camera_set_socket_offset": ([_G_PAWN, _G_ARM], ["DalArm->SocketOffset = FVector(DalCommand.V);"]),
    "camera_set_fov": ([_G_PAWN, _G_CAMERA], ["DalCamera->SetFieldOfView(DalCommand.F);"]),
}

# input trigger events (handler phase -> ETriggerEvent / DalHost::EInputPhase members)
_TRIGGER = {"started": "Started", "triggered": "Triggered", "ongoing": "Ongoing", "completed": "Completed", "canceled": "Canceled"}

_CHARACTER_ITEMS = ("capsule_radius", "capsule_half_height", "actor_scale", "has_root_motion")      # read from the ACharacter
_CAPSULE_ITEMS = ("capsule_radius", "capsule_half_height", "actor_scale")
_CONSUME_ITEMS = ("pre_penetration_velocity", "pre_penetration_velocity_valid")                    # DalRuntime's movement component
_MESH_ITEMS = ("ragdoll_supported", "mesh_world_transform", "bone_component_transform", "bone_world_transform", "ref_path_child_translation", "mesh_scale")
_RAGDOLL_ITEMS = ("ragdoll_pelvis_location", "ragdoll_velocity", "ragdoll_facing_up")             # read from the UDalRagdollService (DalRuntime) of the actor
_WORLD_ITEMS = ("time", "time_dilation", "real_delta_time")                                          # read from the UWorld
_MOVEMENT_ITEMS = ("velocity", "acceleration", "max_acceleration", "max_braking_deceleration", "walkable_floor_z", "movement_mode_custom",
                   "max_walk_speed", "movement_mode", "is_grounded", "is_crouching")
_MONTAGE_ASSET_QUERIES = frozenset(("montage_length", "montage_rate_scale", "montage_sampling_rate", "montage_blend_in_time", "montage_blend_in_option",
                                    "montage_root_transform", "montage_last_root_transform"))
_PAWN_ITEMS = ("actor_location", "actor_rotation", "control_rotation", "action", "actor_ready", "actor_time_dilation")


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
    spec: str = ""                                      # UFUNCTION specifiers (empty: a bare UFUNCTION())
    override: bool = False                              # `virtual ... override` in the declaration
    external: bool = False                              # declared here, defined by source() itself

    def decl(self) -> list[str]:
        out = []
        if self.doc:
            out.append(f"{T}/** {self.doc} */")
        if self.ufunction:
            out.append(f"{T}UFUNCTION({self.spec})")
        head = f"{T}{'virtual ' if self.override else ''}{self.ret} {self.name}({self.params}){' const' if self.const else ''}"
        out.append(head + (" override;" if self.override else ";"))
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
        self.loops = 0                    # `while` loops generated so far (their counters are numbered)
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
        self.uses_source = "montage_stop_source" in used_cmds        # a command that names the montage instance of the notify being handled
        self.mode_sub = any(isinstance(s, ir.Command) and s.name == "set_movement_mode" and s.written == 2          # a program that writes the custom sub mode
                            for _label, body in module.bodies() for s in ir.walk_stmts(body))
        # a command whose montage is a `montage` value (settings data) rather than a declared montage
        self.asset_montages = any(isinstance(s, ir.Command) and s.args and s.args[0].ty is MONTAGE_ASSET and COMMANDS[s.name].params
                                  and COMMANDS[s.name].params[0] == "montage"
                                  for _label, body in module.bodies() for s in ir.walk_stmts(body))
        self.snapshots: list[tuple[str, str]] = []                   # the pose snapshots the program takes: (dotted state path, C++ member path)
        for _label, body in module.bodies():
            for s in ir.walk_stmts(body):
                if isinstance(s, ir.Command) and s.name == "snapshot_pose" and s.place is not None:
                    dotted = s.args[0].value                          # type: ignore[attr-defined]
                    if all(dotted != d for d, _c in self.snapshots):
                        self.snapshots.append((dotted, ".".join((s.place[0], *s.place[1]))))
        self.queries = [n for n in QUERIES if n in used_queries]
        self.world_queries = [n for n in self.queries if n.startswith("trace_") or n in _SCENE_QUERIES]
        # the new members of a hit are filled by every trace of the program, the old trace queries included, but only when the
        # program can read them (so that programs of the earlier versions generate exactly what they did)
        self.rich_hits = bool(set(self.queries) & set(_SCENE_QUERIES)) or any(
            isinstance(e, ir.Member) and e.x.ty is HIT and e.comps in _NEW_HIT_MEMBERS
            for _label, body in module.bodies() for e in ir.walk_stmt_exprs(body))
        self.event_handlers = [h for h in module.handlers if h.event not in LIFECYCLE]
        self.has_events = bool(self.event_handlers)
        self.hook_handlers = [h for h in module.handlers if h.event in HOOK_EVENTS]
        self.notify_handlers = [h for h in module.handlers if h.params and isinstance(h.params[0][1], StructType) and h.params[0][1].is_notify]
        self.hook_slots = [u.phase for u in module.updates if u.phase in ir.HOOK_SLOTS]            # slots the movement component runs itself (DalRunHook)
        self.sync_events = bool(module.sync_events and (self.hook_handlers or self.notify_handlers))     # DalReceiveEvent handles the event at once
        self.sink = bool(self.hook_handlers or self.notify_handlers or self.hook_slots)     # the class receives events through DalHost::FEventRouter (hooks, declared notifies, hook slots)
        self.post = module.kind == "anim" and (module.post_update is not None or module.post_flush)   # a proxy hook runs post_update (and flushes the commands)
        self.post_flush = module.kind == "anim" and module.post_flush
        self.declared_notifies = {n.name for n in module.notifies}
        self.begin_handlers = [k for k, h in enumerate(module.handlers) if h.event == "begin_play"]
        self.end_handlers = [k for k, h in enumerate(module.handlers) if h.event == "end_play"]
        self.phased = module.phases_explicit or len(module.updates) > 1
        self.consume_only = module.consume_slots() if (self.phased or module.sync_events) else None   # the slots that sample the consume-on-read inputs (None: all)
        self.slots = [u.phase for u in module.updates] or [module.default_phase]
        self.queue_target = module.kind == "anim" and any(x.pub and not x.is_view for x in module.methods)
        self.has_cmds = bool(self.cmds) or module.flush_peers
        # an animation logic that asks where it runs (host.is_game_thread) wants what it queued to happen at once when the graph updates on the game thread
        self.game_thread_flush = bool(self.post_flush and self.cmds and "is_game_thread" in self.queries)
        self.flush_peer_names: list[str] = []                            # `flush_peers`: the peers whose pub methods this logic calls at once
        if module.flush_peers:
            for _label, body in module.bodies():
                for e in ir.walk_stmt_exprs(body):
                    if isinstance(e, ir.CallPeer) and not e.queued and e.peer not in self.flush_peer_names:
                        self.flush_peer_names.append(e.peer)
        self.hosth = self.actor or self.has_events or self.has_cmds or bool(self.world_queries) or bool(module.notifies)       # DalHost.h is part of the output
        kinds = {h.event for h in module.handlers if h not in self.notify_handlers}      # (a declared notify arrives through the router, not a delegate)
        self.bind_begin = bool(kinds & {"notify", "notify_begin"}) or (self.actor and bool(self.notify_names()))
        self.bind_end = "notify_end" in kinds
        self.bind_ended = "montage_ended" in kinds
        self.bind_blend = "montage_blending_out" in kinds
        self.bind_input = "input" in kinds
        self.bind_anim = self.bind_begin or self.bind_end or self.bind_ended or self.bind_blend
        self.montage_reads = [i for i in module.inputs if i.host is not None and i.host.item.startswith("montage_")]
        self.section_reads = [i for i in self.montage_reads if i.host.item == "montage_section"]  # type: ignore[union-attr]
        self.section_cmds = bool({"montage_play", "montage_jump_to_section", "montage_set_next_section"} & set(self.cmds)) and bool(module.montages)
        self.montage_cmds = [c for c in self.cmds if c.startswith("montage_")]
        self.curve_reads = [i for i in module.inputs if i.host is not None and i.host.item == "anim_curve"]
        self.curve_reads = self.curve_reads + [i for i in module.inputs if i.host is not None and i.host.item == "root_motion_mode"]   # reads of the anim instance
        self.needs_anim = bool(self.montage_reads or self.curve_reads or self.montage_cmds or self.bind_anim or self.snapshots
                               or {"play_slot_animation", "stop_montages_with_slot", "montage_stop_source"} & set(self.cmds) or "montage_is_playing" in self.queries)
        self.arm = bool({"camera_set_arm_length", "camera_set_socket_offset"} & set(self.cmds))
        self.camera = "camera_set_fov" in self.cmds
        self.controller = "set_control_rotation" in self.cmds
        self.movement_cmds = [c for c in self.cmds if EXEC[c][0][0] == _G_MOVEMENT]
        self.als_movement_cmds = [c for c in self.cmds if _G_ALS_MOVEMENT in EXEC[c][0]]      # they need UDalCharacterMovementComponent (module DalRuntime)
        self.ragdoll_cmds = [c for c in self.cmds if _G_RAGDOLL in EXEC[c][0]]                 # they need UDalRagdollService (module DalRuntime)
        self.character_cmds = [c for c in self.cmds if EXEC[c][0][0] == _G_CHARACTER]

    # ---- statements ---------------------------------------------------------------------------
    def entry_params(self, e: ir.EntryDef) -> str:
        """The parameter list of an entry in the header: the declared defaults become C++ default arguments."""
        parts = []
        for p in e.params:
            text = cpp_param(p.ty, p.name, ufunc=True)
            if p.has_default:
                text += f" = {cpp_value(p.ty, p.default)}"
            parts.append(text)
        return ", ".join(parts)

    def cmd_arg(self, e: ir.Expr) -> str:
        if isinstance(e, ir.MontageRef):
            return f"Dal_MontageId_{e.name}"
        if isinstance(e.ty, EnumType):
            return f"static_cast<int32>({unparen(self.ex.x(e))})"
        return unparen(self.ex.x(e))

    def cmd_args(self, s: ir.Command) -> list[str]:
        """The arguments of the DalHost::Cmd:: constructor: the implicit ones first (the montage instance of the notify being handled), then
        what the program wrote; a montage value has no section, so the `none` that stands for it is left out."""
        out = ["DalCurrentInstance" for _x in COMMANDS[s.name].implicit]
        sent = s.args if s.written is None else s.args[:s.written]                       # a sparse command: only what the program wrote
        out += [self.cmd_arg(a) for a in sent if not (isinstance(a, ir.Lit) and a.ty is NONE_T)]
        return out

    def stmts(self, body: list[ir.Stmt], depth: int) -> list[str]:
        pad = T * depth
        out: list[str] = []
        for s in body:
            if isinstance(s, ir.Let):
                const = "" if s.mutable else "const "
                if is_by_ref(s.ty):
                    out.append(f"{pad}const {cpp_type(s.ty)}& {s.name} = {unparen(self.ex.x(s.init))};")
                else:
                    out.append(f"{pad}{const}{cpp_type(s.ty)} {s.name} = {unparen(self.ex.x(s.init))};")
            elif isinstance(s, ir.Assign):
                out.append(f"{pad}{s.name} = {unparen(self.ex.x(s.value))};")
            elif isinstance(s, ir.ExprStmt):
                if isinstance(s.expr, ir.CallPeer):
                    out += [f"{pad}if ({s.expr.peer} != nullptr)", f"{pad}{{", f"{pad}{T}{self.ex.peer_invoke(s.expr)};", f"{pad}}}"]
                else:
                    out.append(f"{pad}{unparen(self.ex.x(s.expr))};")
            elif isinstance(s, ir.AssignField):
                out.append(f"{pad}{s.name}{''.join('.' + m for m in s.path)} = {to_member(unparen(self.ex.x(s.value)), s.value.ty)};")
            elif isinstance(s, ir.If):
                out.extend(self.if_chain(s, depth))
            elif isinstance(s, ir.Return):
                out.append(f"{pad}return" + (f" {unparen(self.ex.x(s.value))}" if s.value is not None else "") + ";")
            elif isinstance(s, ir.Command):
                args = ", ".join(self.cmd_args(s))
                out.append(f"{pad}DalEnqueue(DalHost::Cmd::{cmd_cpp(s.name)}({args}));")
            elif isinstance(s, ir.For):
                out.append(f"{pad}for (int32 {s.var} = {s.lo}; {s.var} < {s.hi}; ++{s.var})")
                out.append(f"{pad}{{")
                out.extend(self.stmts(s.body, depth + 1))
                out.append(f"{pad}}}")
            elif isinstance(s, ir.While):
                self.loops += 1
                counter = f"DalLoop{self.loops}"
                out.append(f"{pad}for (int32 {counter} = 0; {counter} < {s.bound} && {self.ex.x(s.cond)}; ++{counter})")
                out.append(f"{pad}{{")
                out.extend(self.stmts(s.body, depth + 1))
                out.append(f"{pad}}}")
            elif isinstance(s, ir.Break):
                out.append(f"{pad}break;")
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
            if h.event in ("notify", "notify_begin", "notify_end", "notify_tick") and h.name not in seen:
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
            if h.event == "notify" and h.name not in seen and h.name not in self.declared_notifies:
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
        if self.uses(*_CONSUME_ITEMS, *_RAGDOLL_ITEMS) or self.als_movement_cmds or self.ragdoll_cmds:
            mods += ["DalRuntime"]
        return mods

    @property
    def needs_movement_read(self) -> bool:
        return self.uses(*_MOVEMENT_ITEMS)

    @property
    def needs_character_read(self) -> bool:
        return self.needs_movement_read or self.uses(*_CHARACTER_ITEMS, *_CONSUME_ITEMS)

    @property
    def needs_pawn_helper(self) -> bool:
        return (bool(self.cmds and any(EXEC[c][0][0] in (_G_PAWN, _G_MOVEMENT, _G_CHARACTER) for c in self.cmds)) or self.bind_input
                or "is_walkable" in self.queries or "primitive_can_step_up" in self.queries)

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
        if m.linked is not None:
            out.append(Method("void", "DalResolveParent", doc="Find the main animation instance of the mesh and the owning character.", body=[
                "const USkeletalMeshComponent* DalMesh = GetSkelMeshComponent();",
                f"Parent = DalMesh != nullptr ? Cast<U{m.linked.parent}>(DalMesh->GetAnimInstance()) : nullptr;",
                "Character = Cast<ACharacter>(GetOwningActor());"]))
        if self.needs_pawn_helper:
            out.append(Method("APawn*", "DalGetPawn", body=[
                "return TryGetPawnOwner();" if not self.actor else "return Cast<APawn>(GetOwner());"]))
        if self.character_cmds or self.movement_cmds:
            out.append(Method("ACharacter*", "DalGetCharacter", body=["return Cast<ACharacter>(DalGetPawn());"]))
        if self.movement_cmds:
            out.append(Method("UCharacterMovementComponent*", "DalGetMovement", body=[
                "ACharacter* DalCharacter = DalGetCharacter();",
                "return DalCharacter != nullptr ? DalCharacter->GetCharacterMovement() : nullptr;"]))
        if self.uses(*_MESH_ITEMS):
            out.append(Method("USkeletalMeshComponent*", "DalGetMesh", const=True, body=(
                ["const AActor* DalOwner = GetOwner();", "return DalOwner != nullptr ? DalOwner->FindComponentByClass<USkeletalMeshComponent>() : nullptr;"]
                if self.actor else ["return GetSkelMeshComponent();"])))
        if "ref_path_child_translation" in [h.item for _i, h in self.host_items()]:
            out.append(Method("FVector3f", "DalRefPathChildTranslation", "const FName DalDescendant, const FName DalAncestor", const=True, body=[
                "const USkeletalMeshComponent* DalMesh = DalGetMesh();",
                "const USkeletalMesh* DalAsset = DalMesh != nullptr ? DalMesh->GetSkeletalMeshAsset() : nullptr;",
                "if (DalAsset == nullptr)",
                "{",
                "\treturn FVector3f(0.0f, 0.0f, 0.0f);",
                "}",
                "const FReferenceSkeleton& DalRef = DalAsset->GetRefSkeleton();",
                "const int32 DalAncestorIndex = DalRef.FindBoneIndex(DalAncestor);",
                "int32 DalBone = DalRef.FindBoneIndex(DalDescendant);",
                "int32 DalChild = INDEX_NONE;",
                "while (DalBone != INDEX_NONE && DalBone != DalAncestorIndex)",
                "{",
                "\tDalChild = DalBone;",
                "\tDalBone = DalRef.GetParentIndex(DalBone);",
                "}",
                "if (DalBone == INDEX_NONE || DalAncestorIndex == INDEX_NONE || DalChild == INDEX_NONE)",
                "{",
                "\treturn FVector3f(0.0f, 0.0f, 0.0f);",
                "}",
                "return FVector3f(DalRef.GetRefBonePose()[DalChild].GetTranslation());"]))
        if self.needs_anim:
            if self.actor:
                out.append(Method("UAnimInstance*", "DalGetAnimInstance", body=[
                    "const AActor* DalOwner = GetOwner();",
                    "USkeletalMeshComponent* DalMesh = DalOwner != nullptr ? DalOwner->FindComponentByClass<USkeletalMeshComponent>() : nullptr;",
                    "return DalMesh != nullptr ? DalMesh->GetAnimInstance() : nullptr;"]))
            else:
                out.append(Method("UAnimInstance*", "DalGetAnimInstance", body=["return this;"]))
        if self.montage_cmds and m.montages:                # (the lookup of a declared montage's asset; montage_stop_all needs none)
            out.append(Method("UAnimMontage*", "DalMontageAsset", "const int32 DalMontage", body=self.montage_asset_body()))
        if self.asset_montages:
            out.append(Method("UAnimMontage*", "DalResolveMontage", "const DalHost::FCommand& DalCommand",
                              doc="The montage a command acts on: a `montage` value, or the asset of the declared montage.",
                              body=["return DalCommand.MontageAsset != nullptr ? DalCommand.MontageAsset : DalMontageAsset(DalCommand.Montage);"] if m.montages
                              else ["return DalCommand.MontageAsset;"]))
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
                params = cpp_params(h.params)
                dparams = cpp_params(h.params, True)
                out.append(Method("void", f"DalHandler_{k}", params, def_params=dparams, body=self.stmts(h.body, 0),
                                  doc="on " + h.event + " " + h.name + (f" {h.phase}" if h.phase else "")))
        elif m.handlers:
            for k, h in enumerate(m.handlers):
                out.append(Method("void", f"DalHandler_{k}", doc="on " + h.event, body=self.stmts(h.body, 0)))
        # ---- events that arrive through the router
        if self.sink:
            out.append(Method("const AActor*", "DalSinkOwner", "", [f"return {'GetOwner()' if self.actor else 'GetOwningActor()'};"], const=True,
                              override=True, access="public", doc="The actor whose events this logic receives (DalHost::IEventSink)."))
            out.append(Method("void", "DalReceiveEvent", "const DalHost::FEvent& DalEvent", def_params="const DalHost::FEvent& DalEvent",
                              body=self.receive_body(), override=True, access="public",
                              doc="Called through DalHost::FEventRouter::Post: the hooks of the character and the notify classes of the program."))
            if self.sync_events:
                out.append(Method("void", "DalHandleEventNow", "const DalHost::FEvent& DalEvent", def_params="const DalHost::FEvent& DalEvent",
                                  body=self.handle_now_body(),
                                  doc="sync_events: the event is handled now, in the middle of whatever the engine is doing (inputs sampled, handlers, commands run); "
                                      "one raised by the commands of a handler waits for it."))
        # ---- post_update
        if self.post:
            u = m.post_update
            body: list[str] = []
            if u is not None:
                if u.dt_name:
                    body.append(f"[[maybe_unused]] const float {u.dt_name} = Dal_DeltaSeconds;")
                body.extend(self.stmts(u.body, 0))
            if cmds and self.post_flush:
                body.append("DalFlushCommands();")
            out.append(Method("void", "DalPostUpdate", "", body or ["// (no post_update block)"], access="public",
                              doc="Game thread, after the animation graph updated: the post_update block" + (", then the commands of the frame run." if self.post_flush else ".")))
        # ---- commands
        if cmds:
            out.append(Method("void", "DalEnqueue", "const DalHost::FCommand& DalCommand", doc="Queue a host command; it runs on the game thread after the update.", body=[
                "FScopeLock DalLock(&DalQueueLock);",
                "DalCommandQueue.Add(DalCommand);"]))
            flush_body = [
                "TArray<DalHost::FCommand> DalPending;",
                "{",
                "\tFScopeLock DalLock(&DalQueueLock);",
                "\tDalPending = MoveTemp(DalCommandQueue);",
                "\tDalCommandQueue.Reset();",
                "}",
                "for (const DalHost::FCommand& DalCommand : DalPending)",
                "{",
                "\tDalExecuteCommand(DalCommand);",
                "}"]
            flush_body += [f"DalHost::FlushPeerCommands({p}.Get(), 0);" for p in self.flush_peer_names]       # flush_peers: what the calls made the peers queue
            callable_by_peers = any(x.pub and not x.is_view for x in m.methods)
            out.append(Method("void", "DalFlushCommands", doc="Game thread: run the queued host commands, oldest first." + (
                "  Public: a logic that declares flush_peers runs it on the peers it called." if callable_by_peers else ""),
                access="public" if callable_by_peers else "private", body=flush_body))
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
        out.extend(self.peer_methods())
        if self.phased:
            out.extend(self.phase_methods())
        out.extend(self.user_methods())
        return out

    def phase_methods(self) -> list[Method]:
        """Declarations for an actor logic with tick slots: one tick function per update block, run through DalRunPhase."""
        out = [Method("void", "RegisterComponentTickFunctions", "bool bRegister", [], "const bool bRegister", override=True, access="public", external=True),
               Method("void", "DalRunPhase", "int32 Phase, float DeltaTime", [], "const int32 Phase, const float DeltaTime", access="public", external=True,
                      doc="Run one update block (the slot number is its place in the list: " + ", ".join(f"{i} = {s}" for i, s in enumerate(self.slots)) + ")."),
               Method("void", "DalSetupTickOrder", external=True, doc="Order the tick functions around the movement component, the controller and the mesh.")]
        out.append(Method("FActorComponentTickFunction&", "DalGetPhaseTick", "const int32 Slot", [], "const int32 Slot", access="public", external=True,
                          doc="The tick function of an update block (the other logics of the actor order theirs around it)."))
        if self.hook_slots:
            out.append(Method("void", "DalRunHook", "FName DalHook, float DeltaSeconds", [], "const FName DalHook, const float DeltaSeconds", override=True, access="public", external=True,
                              doc="DalHost::IEventSink: the movement component runs a hook slot (" + ", ".join(self.hook_slots) + ") where it happens."))
        for slot in self.slots:
            out.append(Method("void", f"DalUpdate_{camel(slot)}", "float DeltaSeconds", external=True, doc=f"update in {slot}"))
        return out

    def phase_blocks(self) -> list[list[str]]:
        """Definitions of the tick-slot machinery (registration, ordering, the tick function structs, DalRunPhase)."""
        cls = self.cls
        tick = f"F{self.m.name}PhaseTick"
        blocks: list[list[str]] = []
        reg = [f"void {cls}::RegisterComponentTickFunctions(const bool bRegister)", "{",
               f"{T}Super::RegisterComponentTickFunctions(bRegister);", "",
               f"{T}for (int32 DalIndex = 0; DalIndex < {len(self.slots)}; ++DalIndex)", f"{T}{{",
               f"{T}{T}{tick}& DalTick = DalPhaseTicks[DalIndex];",
               f"{T}{T}if (bRegister)", f"{T}{T}{{",
               f"{T}{T}{T}if (SetupActorComponentTickFunction(&DalTick))", f"{T}{T}{T}{{",
               f"{T}{T}{T}{T}DalTick.Target = this;", f"{T}{T}{T}}}", f"{T}{T}}}",
               f"{T}{T}else if (DalTick.IsTickFunctionRegistered())", f"{T}{T}{{",
               f"{T}{T}{T}DalTick.UnRegisterTickFunction();", f"{T}{T}}}", f"{T}}}", "}"]
        blocks.append(reg)
        need = {"owner": False, "pawn": False, "move": False, "mesh": False, "ctrl": False}
        for slot in self.slots:
            if slot in ir.HOOK_SLOTS:
                continue
            if slot == "pre_movement":
                need.update(owner=True, pawn=True, move=True, ctrl=True)
            elif slot == "after_movement":
                need.update(owner=True, move=True, mesh=True)
            else:
                need.update(owner=True, mesh=True)
        so = [f"void {cls}::DalSetupTickOrder()", "{", f"{T}AActor* DalOwner = GetOwner();", f"{T}if (DalOwner == nullptr)", f"{T}{{", f"{T}{T}return;", f"{T}}}", ""]
        if need["ctrl"]:
            so.append(f"{T}const APawn* DalPawn = Cast<APawn>(DalOwner);")
            so.append(f"{T}AController* DalController = DalPawn != nullptr ? DalPawn->GetController() : nullptr;")
        if need["move"]:
            so.append(f"{T}UCharacterMovementComponent* DalMovement = DalOwner->FindComponentByClass<UCharacterMovementComponent>();")
        if need["mesh"]:
            so.append(f"{T}USkeletalMeshComponent* DalMesh = DalOwner->FindComponentByClass<USkeletalMeshComponent>();")
        for i, slot in enumerate(self.slots):
            if slot in ir.HOOK_SLOTS:
                continue                                                  # (run by the movement component, no tick function to order)
            so.append("")
            so.append(f"{T}// update in {slot}")
            if slot == "pre_movement":
                so += [f"{T}if (DalController != nullptr)", f"{T}{{", f"{T}{T}DalPhaseTicks[{i}].AddPrerequisite(DalController, DalController->PrimaryActorTick);", f"{T}}}",
                       f"{T}if (DalMovement != nullptr)", f"{T}{{", f"{T}{T}DalMovement->PrimaryComponentTick.AddPrerequisite(this, DalPhaseTicks[{i}]);", f"{T}}}"]
            elif slot == "after_movement":
                so += [f"{T}if (DalMovement != nullptr)", f"{T}{{", f"{T}{T}DalPhaseTicks[{i}].AddPrerequisite(DalMovement, DalMovement->PrimaryComponentTick);", f"{T}}}",
                       f"{T}if (DalMesh != nullptr)", f"{T}{{", f"{T}{T}DalMesh->PrimaryComponentTick.AddPrerequisite(this, DalPhaseTicks[{i}]);", f"{T}}}"]
            else:
                so += [f"{T}if (DalMesh != nullptr)", f"{T}{{", f"{T}{T}DalPhaseTicks[{i}].AddPrerequisite(DalMesh, DalMesh->PrimaryComponentTick);", f"{T}}}"]
        for o in self.m.order:
            so.append("")
            so.append(f"{T}// {o.relation} {o.peer}")
            so.append(f"{T}if ({o.peer} != nullptr)")
            so.append(f"{T}{{")
            for slot in o.slots:
                i = self.slots.index(slot)
                j = next(p for p in self.m.peers if p.name == o.peer).slots.index(slot)
                if o.relation == "before":
                    so.append(f"{T}{T}{o.peer}->DalGetPhaseTick({j}).AddPrerequisite(this, DalPhaseTicks[{i}]);                       // {slot}")
                else:
                    so.append(f"{T}{T}DalPhaseTicks[{i}].AddPrerequisite({o.peer}, {o.peer}->DalGetPhaseTick({j}));                    // {slot}")
            so.append(f"{T}}}")
        so.append("}")
        blocks.append(so)
        blocks.append([f"FActorComponentTickFunction& {cls}::DalGetPhaseTick(const int32 Slot)", "{", f"{T}return DalPhaseTicks[Slot];", "}"])
        blocks.append([f"void {tick}::ExecuteTick(const float DeltaTime, const ELevelTick TickType, [[maybe_unused]] ENamedThreads::Type CurrentThread, "
                       f"[[maybe_unused]] const FGraphEventRef& MyCompletionGraphEvent)", "{",
                       f"{T}FActorComponentTickFunction::ExecuteTickHelper(Target, false, DeltaTime, TickType, [this](const float DilatedTime)", f"{T}{{",
                       f"{T}{T}Target->DalRunPhase(Phase, DilatedTime);", f"{T}}});", "}"])
        blocks.append([f"FString {tick}::DiagnosticMessage()", "{", f'{T}return FString(TEXT("{cls}::DalRunPhase"));', "}"])
        rp = [f"void {cls}::DalRunPhase(const int32 Phase, const float DeltaTime)", "{"]
        if self.bind_anim or self.bind_input:
            rp.append(f"{T}DalBindHost();")
        if any(q.startswith("target_") for q in self.queries):
            rp.append(f"{T}bDalTargetsFresh = false;")
        rp.append(f"{T}DalSampleInputs({'Phase' if self.consume_only is not None else ''});")
        if self.has_events:
            if self.m.slot_events:
                rp.append(f"{T}DalTakeEvents();                                  // slot_events: what was raised since the slot before is handled in this one")
            else:
                rp += [f"{T}if (Phase == 0)", f"{T}{{", f"{T}{T}DalTakeEvents();", f"{T}}}"]
        rp.append(f"{T}Dal_DeltaSeconds = DeltaTime;")
        rp += [f"{T}switch (Phase)", f"{T}{{"]
        for i, slot in enumerate(self.slots):
            rp += [f"{T}case {i}:", f"{T}{T}DalUpdate_{camel(slot)}(DeltaTime);", f"{T}{T}break;"]
        rp += [f"{T}default:", f"{T}{T}break;", f"{T}}}"]
        if self.has_cmds:
            rp.append(f"{T}DalFlushCommands();")
        rp.append("}")
        blocks.append(rp)
        if self.hook_slots:
            rh = [f"void {cls}::DalRunHook(const FName DalHook, const float DeltaSeconds)", "{"]
            for slot in self.hook_slots:
                rh += [f"{T}if (DalHook == FName(TEXT(\"{slot}\")))", f"{T}{{", f"{T}{T}DalRunPhase({self.slots.index(slot)}, DeltaSeconds);", f"{T}}}"]
            rh.append("}")
            blocks.append(rh)
        return blocks

    def peer_methods(self) -> list[Method]:
        """Finding the peers, and (animation instance) the queue that actor logics call into."""
        m = self.m
        out: list[Method] = []
        if m.peers:
            body: list[str] = []
            if self.actor:
                body.append("const AActor* DalOwner = GetOwner();")
            else:
                body.append("const AActor* DalOwner = TryGetPawnOwner();")
            for peer in m.peers:
                body.append(f"if ({peer.name} == nullptr)")
                body.append("{")
                if peer.kind == "actor":
                    body.append(f"\t{peer.name} = DalOwner != nullptr ? DalOwner->FindComponentByClass<U{peer.logic}>() : nullptr;")
                else:
                    body.append("\tconst USkeletalMeshComponent* DalMesh = DalOwner != nullptr ? DalOwner->FindComponentByClass<USkeletalMeshComponent>() : nullptr;")
                    body.append(f"\t{peer.name} = DalMesh != nullptr ? Cast<U{peer.logic}>(DalMesh->GetAnimInstance()) : nullptr;")
                body.append("}")
            out.append(Method("void", "DalBindPeers", doc="Find the peer logics (the actor's components, or its animation instance).", body=body))
        if self.queue_target:
            out.append(Method("void", "DalDrainPeerCalls", doc="Run what actor logics asked of this animation instance since the last update.", body=[
                "TArray<TFunction<void()>> DalPending;",
                "{",
                "\tFScopeLock DalLock(&DalPeerLock);",
                "\tDalPending = MoveTemp(DalPeerQueue);",
                "\tDalPeerQueue.Reset();",
                "}",
                "for (const TFunction<void()>& DalCall : DalPending)",
                "{",
                "\tDalCall();",
                "}"]))
        return out

    def user_methods(self) -> list[Method]:
        """The `method`s and `view`s of the program; the `pub` ones are public (and callable from Blueprints)."""
        out: list[Method] = []
        for x in self.m.methods:
            name = self.ex.method_name(x.name)
            params = cpp_params(x.params, ufunc=x.pub)
            dparams = cpp_params(x.params, True, ufunc=x.pub)
            ret = (cpp_ufunc_type(x.ret) if x.pub else cpp_type(x.ret)) if x.ret is not None else "void"
            spec = ""
            if x.pub:
                spec = 'BlueprintPure, Category = "DAL|View"' if x.is_view else 'BlueprintCallable, Category = "DAL|Method"'
                if x.is_view and not self.actor:
                    spec += ", Meta = (BlueprintThreadSafe)"
            doc = ("view " if x.is_view else "method ") + x.name
            out.append(Method(ret, name, params, self.stmts(x.body, 0), dparams or None, x.pub, "public" if x.pub else "private", doc, x.is_view, spec))
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

    def handle_now_body(self) -> list[str]:
        """DalHandleEventNow: sample, dispatch the one event, hand the commands over -- not re-entrant (an event raised meanwhile is handled after this one)."""
        flush = ["\t\tDalFlushCommands();"] if (self.has_cmds and not self.post_flush) else []
        sample = []                                                       # an animation logic does not sample: its update is in flight on a worker thread, or just done with the inputs
        if self.actor:
            sample = [("\tDalSampleInputs(MAX_int32);                // (a slot that consumes nothing: the update blocks keep what they read once)" if self.consume_only is not None
                       else "\tDalSampleInputs();")]
        return ["DalNowEvents.Add(DalEvent);",
                "if (bDalInEvent)",
                "{",
                "\treturn;                                   // raised by the commands of the event being handled: handled right after it",
                "}",
                "bDalInEvent = true;",
                "for (int32 DalIndex = 0; DalIndex < DalNowEvents.Num(); ++DalIndex)",
                "{",
                "\tconst DalHost::FEvent DalNext = DalNowEvents[DalIndex];",
                *sample,
                "\tTArray<DalHost::FEvent> DalOuter = MoveTemp(DalFrameEvents);",
                "\tDalFrameEvents.Reset();",
                "\tDalFrameEvents.Add(DalNext);",
                "\tDalDispatchEvents();",
                "\tDalFrameEvents = MoveTemp(DalOuter);",
                *flush,
                "}",
                "DalNowEvents.Reset();",
                "bDalInEvent = false;"]

    def receive_body(self) -> list[str]:
        """DalReceiveEvent: queue an event the router posted.  The events of a notify class carry its declared name: they are queued
        under the id this logic gave it (and dropped when the logic does not handle that notify)."""
        if not self.has_events:
            return ["static_cast<void>(DalEvent);                    // (this logic only has hook slots: nothing to queue)"]
        if not self.notify_handlers:
            return ["DalHandleEventNow(DalEvent);"] if self.sync_events else ["DalPostEvent(DalEvent);"]
        return ["DalHost::FEvent DalQueued = DalEvent;",
                "if (DalQueued.Source != nullptr)",
                "{",
                "\tDalQueued.Id = DalFindNotifyId(DalQueued.Name);",
                "\tif (DalQueued.Id == INDEX_NONE)",
                "\t{",
                "\t\treturn;",
                "\t}",
                "}",
                "DalHandleEventNow(DalQueued);" if self.sync_events else "DalPostEvent(DalQueued);"]

    def dispatch_body(self) -> list[str]:
        m = self.m
        out = ["for (const DalHost::FEvent& DalEvent : DalFrameEvents)", "{"]
        kinds = {"notify": "Notify", "notify_begin": "NotifyBegin", "notify_end": "NotifyEnd", "notify_tick": "NotifyTick",
                 "montage_ended": "MontageEnded", "montage_blending_out": "MontageBlendingOut", "input": "Input",
                 "movement_mode_changed": "MovementModeChanged", "jumped": "Jumped", "landed": "Landed",
                 "crouch_started": "CrouchStarted", "crouch_ended": "CrouchEnded", "teleported": "Teleported",
                 "physics_rotation": "PhysicsRotation"}
        for k, h in enumerate(m.handlers):
            if h.event in LIFECYCLE:
                continue
            if h.event in HOOK_EVENTS:
                args = {"movement_mode_changed": "static_cast<EMovementMode>(DalEvent.ModeA), static_cast<EMovementMode>(DalEvent.ModeB)",
                        "landed": "DalEvent.Hit", "physics_rotation": "DalEvent.Value.X"}.get(h.event, "") if h.params else ""
                out += [f"\t// on {h.event}", f"\tif (DalEvent.Kind == DalHost::EEventKind::{kinds[h.event]})", "\t{", f"\t\tDalHandler_{k}({args});", "\t}"]
                continue
            what = f"on {h.event} {h.name}" + (f" {h.phase}" if h.phase else "")
            kind = kinds[h.event]
            if h in self.notify_handlers:
                nstruct = h.params[0][1]
                out += [f"\t// {what}", f"\tif (DalEvent.Kind == DalHost::EEventKind::{kind} && DalEvent.Id == Dal_NotifyId_{h.name})", "\t{",
                        f"\t\tif (const {nstruct.cpp_type}* DalPayload = Cast<{nstruct.cpp_type}>(DalEvent.Source))", "\t\t{"]
                if self.uses_source:
                    out.append("\t\t\tDalCurrentInstance = DalEvent.MontageInstance;")
                out.append(f"\t\t\tDalHandler_{k}(DalPayload);")
                if self.uses_source:
                    out.append("\t\t\tDalCurrentInstance = INDEX_NONE;")
                out += ["\t\t}", "\t}"]
                continue
            if h.event in ("notify", "notify_begin", "notify_end", "notify_tick"):
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

    def game_thread_flush_lines(self) -> list[str]:
        return [f"{T}// The graph updates on the game thread here: what was queued runs at once, as it does in the C++ it was ported from.",
                f"{T}// (On a worker thread the commands wait for the post update.)",
                f"{T}if (IsInGameThread())", f"{T}{{", f"{T}{T}DalFlushCommands();", f"{T}}}"]

    def execute_body(self) -> list[str]:
        if not self.cmds:                                        # a logic with flush_peers and no commands of its own: nothing to run here
            return ["(void)DalCommand;"]
        out = ["switch (DalCommand.Kind)", "{"]
        for c in self.cmds:
            guards, body = EXEC[c]
            if self.asset_montages:
                guards = [_G_RESOLVED if g == _G_ASSET else g for g in guards]
            if c == "montage_play" and not self.m.montages:
                body = body[:1]                                  # only montage values: no sections to jump to
            if c == "set_movement_mode" and self.mode_sub:
                body = ["DalMovement->SetMovementMode(static_cast<EMovementMode>(DalCommand.Mode), static_cast<uint8>(DalCommand.Count));"]
            if c == "snapshot_pose":
                body = []
                for k, (dotted, member) in enumerate(self.snapshots):
                    body += [("if" if k == 0 else "else if") + f" (DalCommand.Name == FName(TEXT(\"{dotted}\")))", "{", f"\tDalAnim->SnapshotPose({member});", "}"]
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
        out.extend(self.new_query_methods())
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

    def new_query_methods(self) -> list[Method]:
        out: list[Method] = []
        q = self.queries
        chan = "const ECollisionChannel DalChannel"
        resp = "const TArray<TEnumAsByte<ECollisionChannel>>& DalResponses"
        vec = "const FVector3f& DalStart, const FVector3f& DalEnd"
        if "sweep_capsule" in q:
            out.append(Method("DalHost::FHit", "DalSweepCapsule", f"{vec}, const float DalRadius, const float DalHalfHeight, {chan}, {resp}",
                              doc="host.sweep_capsule: a vertical capsule swept from start to end; only the listed channels block.",
                              body=self.scene_body("MakeCapsule(DalRadius, DalHalfHeight)")))
        if "sweep_sphere" in q:
            out.append(Method("DalHost::FHit", "DalSweepSphere", f"{vec}, const float DalRadius, {chan}, {resp}",
                              doc="host.sweep_sphere: a sphere swept from start to end; only the listed channels block.",
                              body=self.scene_body("MakeSphere(DalRadius)")))
        if "line_trace" in q:
            out.append(Method("DalHost::FHit", "DalLineTrace", f"{vec}, {chan}",
                              doc="host.line_trace: the first blocking hit on a channel, the owner ignored.", body=self.scene_body(None)))
        if "overlap_capsule" in q:
            ignore = "GetOwner()" if self.actor else "GetOwningActor()"
            out.append(Method("bool", "DalOverlapCapsule", f"const FVector3f& DalCenter, const float DalRadius, const float DalHalfHeight, {chan}, {resp}",
                              doc="host.overlap_capsule: does a vertical capsule at this center overlap anything that blocks.", body=[
                "const UWorld* DalWorld = GetWorld();",
                "if (DalWorld == nullptr)",
                "{",
                "\treturn false;",
                "}",
                f"FCollisionQueryParams DalParams(SCENE_QUERY_STAT(DalOverlap), false, {ignore});",
                *self.response_lines(),
                "return DalWorld->OverlapBlockingTestByChannel(FVector(DalCenter), FQuat::Identity, DalChannel, "
                "FCollisionShape::MakeCapsule(DalRadius, DalHalfHeight), DalParams, DalResponseParams);"]))
        if "is_walkable" in q:
            out.append(Method("bool", "DalIsWalkable", "const DalHost::FHit& DalHit",
                              doc="host.is_walkable: a blocking hit that does not start inside the shape and whose normal is flat enough to stand on.", body=[
                "const ACharacter* DalCharacter = Cast<ACharacter>(DalGetPawn());",
                "const UCharacterMovementComponent* DalMovement = DalCharacter != nullptr ? DalCharacter->GetCharacterMovement() : nullptr;",
                "if (!DalHit.bBlocking || DalHit.bStartPenetrating || DalMovement == nullptr)",
                "{",
                "\treturn false;",
                "}",
                "return DalHit.Normal.Z >= UE_KINDA_SMALL_NUMBER && DalHit.Normal.Z >= DalMovement->GetWalkableFloorZ();"]))
        if "is_game_thread" in q:
            out.append(Method("bool", "DalIsGameThread", doc="host.is_game_thread: does the code run on the game thread.", body=["return IsInGameThread();"]))
        if "proxy_component_transform" in q:
            if self.actor:
                body = ["const AActor* DalOwner = GetOwner();",
                        "const USkeletalMeshComponent* DalMesh = DalOwner != nullptr ? DalOwner->FindComponentByClass<USkeletalMeshComponent>() : nullptr;",
                        "return DalMesh != nullptr ? FTransform3f(DalMesh->GetComponentTransform()) : FTransform3f();"]
            else:
                body = ["return FTransform3f(GetProxyOnAnyThread<FAnimInstanceProxy>().GetComponentTransform());"]
            out.append(Method("FTransform3f", "DalProxyComponentTransform",
                              doc="host.proxy_component_transform: the component transform the animation proxy cached (the mesh's, for an actor logic).", body=body))
        if "montage_is_playing" in q:
            out.append(Method("bool", "DalMontageIsPlaying", "const UAnimMontage* DalMontage",
                              doc="host.montage_is_playing: is the montage (an asset value) playing on the animation instance.",
                              body=["const UAnimInstance* DalAnim = DalGetAnimInstance();",
                                    "return DalAnim != nullptr && DalMontage != nullptr && DalAnim->Montage_IsPlaying(DalMontage);"]))
        for name, ret, expr in (("montage_length", "float", "DalMontage->GetPlayLength()"), ("montage_rate_scale", "float", "DalMontage->RateScale"),
                                ("montage_sampling_rate", "float", "static_cast<float>(DalMontage->GetSamplingFrameRate().AsDecimal())"),
                                ("montage_blend_in_time", "float", "DalMontage->BlendIn.GetBlendTime()"),
                                ("montage_blend_in_option", "EAlphaBlendOption", "DalMontage->BlendIn.GetBlendOption()")):
            if name in q:
                none = {"montage_length": "0.0f", "montage_rate_scale": "1.0f", "montage_sampling_rate": "0.0f", "montage_blend_in_time": "0.25f",
                        "montage_blend_in_option": "EAlphaBlendOption::Linear"}[name]
                out.append(Method(ret, query_cpp(name), "const UAnimMontage* DalMontage",
                                  doc=f"host.{name}: what the montage asset says (a pure read of the asset).",
                                  body=[f"return DalMontage != nullptr ? {expr} : {none};"]))
        if "montage_root_transform" in q:
            out.append(Method("FTransform3f", "DalMontageRootTransform", "const UAnimMontage* DalMontage, const float DalTime",
                              doc="host.montage_root_transform: the root bone's absolute transform in component space at a time, "
                                  "from the first slot track of the montage (as UAlsMontageUtility::ExtractRootTransformFromMontage).", body=[
                "if (DalMontage == nullptr || DalMontage->SlotAnimTracks.IsEmpty())",
                "{",
                "\treturn FTransform3f();",
                "}",
                "const FAnimTrack& DalTrack = DalMontage->SlotAnimTracks[0].AnimTrack;",
                "const FAnimSegment* DalSegment = DalTrack.GetSegmentAtTime(FMath::Max(0.0f, DalTime));",
                "if (DalSegment == nullptr && !DalTrack.AnimSegments.IsEmpty())",
                "{",
                "\tDalSegment = &DalTrack.AnimSegments.Last();",
                "}",
                "const UAnimSequence* DalSequence = DalSegment != nullptr ? Cast<UAnimSequence>(DalSegment->GetAnimReference()) : nullptr;",
                "if (DalSequence == nullptr)",
                "{",
                "\treturn FTransform3f();",
                "}",
                "const FAnimExtractContext DalContext(static_cast<double>(DalSegment->ConvertTrackPosToAnimPos(DalTime)));",
                "return FTransform3f(DalSequence->ExtractRootTrackTransform(DalContext, nullptr));"]))
        if "montage_last_root_transform" in q:
            out.append(Method("FTransform3f", "DalMontageLastRootTransform", "const UAnimMontage* DalMontage",
                              doc="host.montage_last_root_transform: the root bone's absolute transform at the end of the montage's last segment.", body=[
                "if (DalMontage == nullptr || DalMontage->SlotAnimTracks.IsEmpty() || DalMontage->SlotAnimTracks[0].AnimTrack.AnimSegments.IsEmpty())",
                "{",
                "\treturn FTransform3f();",
                "}",
                "const FAnimSegment& DalSegment = DalMontage->SlotAnimTracks[0].AnimTrack.AnimSegments.Last();",
                "const UAnimSequence* DalSequence = Cast<UAnimSequence>(DalSegment.GetAnimReference());",
                "if (DalSequence == nullptr)",
                "{",
                "\treturn FTransform3f();",
                "}",
                "const FAnimExtractContext DalContext(static_cast<double>(DalSegment.GetEndPos()));",
                "return FTransform3f(DalSequence->ExtractRootTrackTransform(DalContext, nullptr));"]))
        if "primitive_valid" in q:
            out.append(Method("bool", "DalPrimitiveValid", "const UPrimitiveComponent* DalPrimitive", body=["return IsValid(DalPrimitive);"]))
        if "primitive_speed" in q:
            out.append(Method("float", "DalPrimitiveSpeed", "const UPrimitiveComponent* DalPrimitive",
                              body=["return DalPrimitive != nullptr ? static_cast<float>(DalPrimitive->GetComponentVelocity().Size()) : 0.0f;"]))
        if "primitive_can_step_up" in q:
            out.append(Method("bool", "DalPrimitiveCanStepUp", "const UPrimitiveComponent* DalPrimitive",
                              body=["return DalPrimitive != nullptr && DalPrimitive->CanCharacterStepUp(DalGetPawn());"]))
        if "primitive_transform" in q:
            out.append(Method("FTransform3f", "DalPrimitiveTransform", "const UPrimitiveComponent* DalPrimitive",
                              body=["return DalPrimitive != nullptr ? FTransform3f(DalPrimitive->GetComponentTransform()) : FTransform3f();"]))
        return out

    def response_lines(self) -> list[str]:
        return [
            "FCollisionResponseParams DalResponseParams(ECR_Ignore);",
            "for (const TEnumAsByte<ECollisionChannel> DalBlocking : DalResponses)",
            "{",
            "\tDalResponseParams.CollisionResponse.SetResponse(DalBlocking, ECR_Block);",
            "}",
        ]

    def scene_body(self, shape: str | None) -> list[str]:
        """A sweep (shape given) or line trace on a channel; the blocking channels are the ones listed (a line trace uses the channel only)."""
        ignore = "GetOwner()" if self.actor else "GetOwningActor()"
        out = [
            "DalHost::FHit DalResult;",
            "DalResult.Point = DalEnd;",
            "DalResult.Location = DalEnd;",
            "const UWorld* DalWorld = GetWorld();",
            "if (DalWorld == nullptr)",
            "{",
            "\treturn DalResult;",
            "}",
            "FHitResult DalHit;",
            f"FCollisionQueryParams DalParams(SCENE_QUERY_STAT(DalTrace), false, {ignore});",
        ]
        if shape is None:
            out.append("const bool bDalBlocked = DalWorld->LineTraceSingleByChannel(DalHit, FVector(DalStart), FVector(DalEnd), DalChannel, DalParams);")
        else:
            out += self.response_lines()
            out.append("const bool bDalBlocked = DalWorld->SweepSingleByChannel(DalHit, FVector(DalStart), FVector(DalEnd), FQuat::Identity, "
                       f"DalChannel, FCollisionShape::{shape}, DalParams, DalResponseParams);")
        out += ["if (bDalBlocked)", "{", "\tDalResult.bBlocking = true;"] + [f"\t{line}" for line in self.hit_lines()] + ["}", "return DalResult;"]
        return out

    def hit_lines(self) -> list[str]:
        return [
            "DalResult.Point = FVector3f(DalHit.ImpactPoint);",
            "DalResult.Normal = FVector3f(DalHit.ImpactNormal);",
            "DalResult.Distance = DalHit.Distance;",
            "DalResult.Time = DalHit.Time;",
            "DalResult.Location = FVector3f(DalHit.Location);",
            "DalResult.bStartPenetrating = DalHit.bStartPenetrating;",
            "DalResult.Primitive = DalHit.GetComponent();",
            "DalResult.FaceNormal = FVector3f(DalHit.Normal);",
        ]

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
        if self.rich_hits:
            out.insert(2, "DalResult.Location = DalEnd;")
            out += ["if (bDalBlocked)", "{", "\tDalResult.bBlocking = true;"] + [f"\t{line}" for line in self.hit_lines()] + ["}", "return DalResult;"]
            return out
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

    # ---- animation notify classes ---------------------------------------------------------------------------------
    def notify_decl(self, nd: ir.NotifyDef) -> list[str]:
        """The UCLASS of a declared notify: the members are properties, the notify events go to the logics of the actor (DalHost::FEventRouter)."""
        cls = "U" + nd.cpp_class
        base = "UAnimNotifyState" if nd.state else "UAnimNotify"
        out = [f"/** {'Notify state' if nd.state else 'Notify'} `{nd.name}` of the program: its properties are the payload the `on notify` handlers read. */",
               "UCLASS()", f"class {self.api}{cls} : public {base}", "{", f"{T}GENERATED_BODY()", "", "public:"]
        if nd.branching:
            out += [f"{T}{cls}();", ""]
        for f in nd.struct.fields:
            out.append(f'{T}UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "DAL|Notify")')
            init = "" if isinstance(f.ty, (MapType, ArrayType, AssetType)) else f" = {cpp_value(f.ty, f.default)}"
            out.append(f"{T}{cpp_prop_type(f.ty)} {f.name}{init};")
            out.append("")
        out.append(f"{T}virtual FString GetNotifyName_Implementation() const override;")
        mesh = "USkeletalMeshComponent* MeshComp, UAnimSequenceBase* Animation"
        if nd.state:
            out += [f"{T}virtual void NotifyBegin({mesh}, float TotalDuration, const FAnimNotifyEventReference& EventReference) override;",
                    f"{T}virtual void NotifyTick({mesh}, float FrameDeltaTime, const FAnimNotifyEventReference& EventReference) override;",
                    f"{T}virtual void NotifyEnd({mesh}, const FAnimNotifyEventReference& EventReference) override;"]
            if nd.branching:
                out += [f"{T}virtual void BranchingPointNotifyBegin(FBranchingPointNotifyPayload& BranchingPointPayload) override;",
                        f"{T}virtual void BranchingPointNotifyTick(FBranchingPointNotifyPayload& BranchingPointPayload, float FrameDeltaTime) override;",
                        f"{T}virtual void BranchingPointNotifyEnd(FBranchingPointNotifyPayload& BranchingPointPayload) override;"]
        else:
            out.append(f"{T}virtual void Notify({mesh}, const FAnimNotifyEventReference& EventReference) override;")
            if nd.branching:
                out.append(f"{T}virtual void BranchingPointNotify(FBranchingPointNotifyPayload& BranchingPointPayload) override;")
        out.append("};")
        return out

    def notify_def(self, nd: ir.NotifyDef) -> list[list[str]]:
        """The member functions of a declared notify class (one block per function)."""
        cls = "U" + nd.cpp_class
        base = "UAnimNotifyState" if nd.state else "UAnimNotify"
        name = f'FName(TEXT("{nd.name}"))'
        mesh = "USkeletalMeshComponent* MeshComp, UAnimSequenceBase* Animation"

        def post(kind: str, mesh_expr: str, instance: str) -> str:
            return (f"{T}DalHost::FEventRouter::PostNotify({mesh_expr} != nullptr ? {mesh_expr}->GetOwner() : nullptr, "
                    f"DalHost::EEventKind::{kind}, {name}, this, {instance});")

        blocks: list[list[str]] = []
        if nd.branching:
            blocks.append([f"{cls}::{cls}()", "{", f"{T}bIsNativeBranchingPoint = true;", "}"])
        blocks.append([f"FString {cls}::GetNotifyName_Implementation() const", "{", f"{T}return FString(TEXT(\"{nd.name}\"));", "}"])
        if nd.state:
            blocks.append([f"void {cls}::NotifyBegin({mesh}, const float TotalDuration, const FAnimNotifyEventReference& EventReference)", "{",
                           f"{T}{base}::NotifyBegin(MeshComp, Animation, TotalDuration, EventReference);", "", post("NotifyBegin", "MeshComp", "INDEX_NONE"), "}"])
            blocks.append([f"void {cls}::NotifyTick({mesh}, const float FrameDeltaTime, const FAnimNotifyEventReference& EventReference)", "{",
                           f"{T}{base}::NotifyTick(MeshComp, Animation, FrameDeltaTime, EventReference);", "", post("NotifyTick", "MeshComp", "INDEX_NONE"), "}"])
            blocks.append([f"void {cls}::NotifyEnd({mesh}, const FAnimNotifyEventReference& EventReference)", "{",
                           f"{T}{base}::NotifyEnd(MeshComp, Animation, EventReference);", "", post("NotifyEnd", "MeshComp", "INDEX_NONE"), "}"])
            if nd.branching:
                # a montage raises the branching point itself: the event names the montage instance (montage_stop_source)
                for fn, kind, extra, unused in (("Begin", "NotifyBegin", "", ""), ("Tick", "NotifyTick", ", const float FrameDeltaTime", "[[maybe_unused]] "),
                                                ("End", "NotifyEnd", "", "")):
                    blocks.append([f"void {cls}::BranchingPointNotify{fn}(FBranchingPointNotifyPayload& BranchingPointPayload{extra.replace('const float', unused + 'const float')})", "{",
                                   post(kind, "BranchingPointPayload.SkelMeshComponent", "BranchingPointPayload.MontageInstanceID"), "}"])
        else:
            blocks.append([f"void {cls}::Notify({mesh}, const FAnimNotifyEventReference& EventReference)", "{",
                           f"{T}{base}::Notify(MeshComp, Animation, EventReference);", "", post("Notify", "MeshComp", "INDEX_NONE"), "}"])
            if nd.branching:
                blocks.append([f"void {cls}::BranchingPointNotify(FBranchingPointNotifyPayload& BranchingPointPayload)", "{",
                               post("Notify", "BranchingPointPayload.SkelMeshComponent", "BranchingPointPayload.MontageInstanceID"), "}"])
        return blocks

    def notify_structs(self, origin: str | None) -> list:
        """The payload types of the notify classes declared in one file (their members are what the class declaration needs headers for)."""
        return [n.struct for n in self.m.notifies if n.origin == origin]

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
        if any(holds_tags(f.ty) for n in m.notifies if n.origin == origin or any_origin for f in n.struct.fields):
            return True
        return any(holds_tags(f.ty) for s in m.structs if s.origin == origin or any_origin for f in s.fields)

    @property
    def uses_data(self) -> bool:
        """The program has settings types (own or from a library): DalData.h is part of the output."""
        return any(st.is_settings for st in self.m.structs)

    def data_includes(self, origin: str | None, skip: tuple[str, ...] = ()) -> list[str]:
        """DalMath.h / DalData.h and the engine headers for the settings and asset values declared in this file."""
        inc: list[str] = []
        own = [st for st in self.m.structs if st.origin == origin]
        settings = [st for st in own if st.is_settings]
        if settings:
            inc += ["DalMath.h", "DalData.h"]
            if any(st.asset_root for st in settings):
                inc.append("Engine/DataAsset.h")
        for st in own + self.notify_structs(origin):
            for f in st.fields:
                asset_headers(f.ty, inc)
        for st in own:
            native = _NATIVE_STRUCTS.get(st.name)
            if native and native[1] and native[1] not in inc:
                inc.append(native[1])
        if origin is None:
            for d in [*self.m.params, *self.m.inputs, *self.m.states]:
                asset_headers(d.ty, inc)
        return [x for x in inc if x not in skip]

    def struct_decl(self, st: StructType) -> list[str]:
        if st.name in _NATIVE_STRUCTS:
            return []                                        # an engine type (CppOptions.native_structs)
        if st.is_settings:
            return self.settings_decl(st)
        out = ["USTRUCT(BlueprintType)", f"struct {st.cpp_type}", "{", f"{T}GENERATED_BODY()"]
        ctor: list[str] = []
        for f in st.fields:
            out.append("")
            out.append(f'{T}UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "DAL")')
            init, late = member_init(f.ty, f.default, True)
            out.append(f"{T}{member_type(f.ty)} {f.name}{init};")
            if late is not None:
                ctor.append(f"{T}{T}{f.name} = {late};")
        if ctor:
            out += ["", f"{T}{st.cpp_type}()", f"{T}{{", *ctor, f"{T}}}"]
        out.append("};")
        return out

    def settings_members(self, st: StructType) -> tuple[list[str], list[str]]:
        """The member declarations of a settings type, and the constructor lines for the members whose default UHT cannot read as an initializer."""
        from . import values

        inst = values.default_of(st) if any(f.derived for f in st.fields) else None
        ctor: list[str] = []
        members: list[str] = []
        for k, f in enumerate(st.fields):
            if k:
                members.append("")
            if f.derived:
                members.append(f'{T}UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL", Transient)')
                init, late = member_init(f.ty, getattr(inst, f.name), True)
            elif object_settings(f.ty):
                members.append(f'{T}UPROPERTY(EditAnywhere, BlueprintReadOnly, Instanced, Category = "DAL")')
                init, late = "", None
            else:
                members.append(f'{T}UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "DAL")')
                channels = _CHANNEL_DEFAULTS.get((st.name, f.name))
                if channels is not None:
                    init, late = "", "{ " + ", ".join(channels) + " }"
                else:
                    init, late = ("", None) if isinstance(f.ty, (MapType, ArrayType, AssetType)) else member_init(f.ty, f.default, True)
            members.append(f"{T}{settings_member_type(f.ty)} {f.name}{init};")
            if late is not None:
                ctor.append(f"{T}{T}{f.name} = {late};")
        return members, ctor

    def object_settings_decl(self, st: StructType, members: list[str], ctor: list[str]) -> list[str]:
        """A settings type the assets hold as an Instanced UObject (CppOptions.object_settings): the value struct the logic reads, the object
        (EditInlineNew; the settings types that hold it have a TObjectPtr), and the read of a member as a value (a null object is the defaults)."""
        if st.asset_root or any(f.derived or object_settings(f.ty) for f in st.fields):
            raise ValueError(f"object settings `{st.name}`: it must be held by another settings type and have no derived members or members of its own kind")
        obj = f"U{st.name}"
        out = ["USTRUCT(BlueprintType)", f"struct {st.cpp_type}", "{", f"{T}GENERATED_BODY()", ""]
        out += members
        if ctor:
            out += ["", f"{T}{st.cpp_type}()", f"{T}{{", *ctor, f"{T}}}"]
        out += ["};", "",
                "UCLASS(BlueprintType, EditInlineNew)",
                f"class {self.api}{obj} : public UObject",
                "{", f"{T}GENERATED_BODY()", "", "public:"]
        if ctor:
            out += [f"{T}{obj}()", f"{T}{{", *ctor, f"{T}}}", ""]
        out += members
        out += ["", f"{T}/** The values of this object, as the logic reads them. */",
                f"{T}{st.cpp_type} ToValue() const", f"{T}{{", f"{T}{T}{st.cpp_type} Value;"]
        out += [f"{T}{T}Value.{f.name} = {f.name};" for f in st.fields]
        out += [f"{T}{T}return Value;", f"{T}}}", "};", "",
                "/** What the logic reads of a member that holds the object: the values of the declared defaults when there is none. */",
                f"inline {st.cpp_type} DalValueOf(const {obj}* Object)", "{",
                f"{T}return Object != nullptr ? Object->ToValue() : {st.cpp_type}();", "}"]
        return out

    def settings_decl(self, st: StructType) -> list[str]:
        """A settings type: a USTRUCT inside another settings type, a UDataAsset at the top (no other settings type holds it)."""
        root = st.asset_root
        recompute = needs_recompute(st)
        members, ctor = self.settings_members(st)           # ctor: members whose default is an expression UHT cannot read as an initializer
        if object_settings(st):
            return self.object_settings_decl(st, members, ctor)
        if root:
            out = ["UCLASS(BlueprintType)", f"class {self.api}{st.cpp_type} : public UDataAsset", "{", f"{T}GENERATED_BODY()", "", "public:"]
            if recompute or ctor:
                out += [f"{T}{st.cpp_type}()", f"{T}{{", *ctor, *([f"{T}{T}DalRecomputeDerived();"] if recompute else []), f"{T}}}", ""]
        else:
            out = ["USTRUCT(BlueprintType)", f"struct {st.cpp_type}", "{", f"{T}GENERATED_BODY()", ""]
            if ctor:
                members += ["", f"{T}{st.cpp_type}()", f"{T}{{", *ctor, f"{T}}}"]
        out += members
        if recompute:
            out.append("")
            out.append(f"{T}/** Computes the derived members from the others: call it after the data was loaded or edited. */")
            out.append(f"{T}void DalRecomputeDerived()")
            out.append(f"{T}{{")
            for f in st.fields:
                if not f.derived and needs_recompute(f.ty):
                    if isinstance(f.ty, StructType):
                        out.append(f"{T}{T}{f.name}.DalRecomputeDerived();")
                    elif isinstance(f.ty, MapType):
                        out += [f"{T}{T}for (auto& Pair : {f.name})", f"{T}{T}{{", f"{T}{T}{T}Pair.Value.DalRecomputeDerived();", f"{T}{T}}}"]
                    else:
                        out += [f"{T}{T}for (auto& Item : {f.name})", f"{T}{T}{{", f"{T}{T}{T}Item.DalRecomputeDerived();", f"{T}{T}}}"]
            for f in st.fields:
                if f.derived:
                    out.append(f"{T}{T}{f.name} = {to_member(unparen(self.ex.x(f.expr)), f.ty)};")
            out.append(f"{T}}}")
            if root:
                out += ["",
                        f"{T}virtual void PostLoad() override",
                        f"{T}{{", f"{T}{T}UDataAsset::PostLoad();", f"{T}{T}DalRecomputeDerived();", f"{T}}}",
                        "",
                        "#if WITH_EDITOR",
                        f"{T}virtual void PostEditChangeProperty(FPropertyChangedEvent& PropertyChangedEvent) override",
                        f"{T}{{", f"{T}{T}UDataAsset::PostEditChangeProperty(PropertyChangedEvent);", f"{T}{T}DalRecomputeDerived();", f"{T}}}",
                        "#endif"]
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
        params = cpp_params(f.params)
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
        own_notifies = [n for n in m.notifies if n.origin == origin]
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
        if own_notifies:
            L.append('#include "DalHost.h"')
            L.append('#include "Animation/AnimNotifies/AnimNotify.h"')
            if any(n.state for n in own_notifies):
                L.append('#include "Animation/AnimNotifies/AnimNotifyState.h"')
        for inc in self.data_includes(origin, skip=("DalMath.h",)):
            L.append(f'#include "{inc}"')
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
            if st.origin == origin and st.name not in _NATIVE_STRUCTS:
                L.extend(self.struct_decl(st))
                L.append("")
        for nd in own_notifies:
            L.extend(self.notify_decl(nd))
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
        if own_tags or own_notifies:
            S = [f"// Generated by dal {__version__} from {filename} (sha256 {sha256[:16]}...).",
                 "// Do not edit: change the .dal source and recompile.",
                 f'#include "{stem}.h"', ""]
            if own_tags:
                S += ['#include "NativeGameplayTags.h"']
            if own_notifies:
                S += ['#include "GameFramework/Actor.h"', '#include "Components/SkeletalMeshComponent.h"']
            S += ["", f"#include UE_INLINE_GENERATED_CPP_BY_NAME({stem})", ""]
            blocks_: list[list[str]] = []
            for ts in own_tags:
                blocks_.append(self.tags_define(ts))
            for nd in own_notifies:
                blocks_.extend(self.notify_def(nd))
            for k, blk in enumerate(blocks_):
                if k:
                    S.append("")
                S.extend(blk)
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
                if isinstance(p.ty, StructType) and p.ty.asset_root:
                    L.append(f"{T}TObjectPtr<{p.ty.cpp_type}> {p.name};")
                else:
                    L.append(f"{T}{cpp_prop_type(p.ty)} {p.name}{member_init(p.ty, p.default)[0]};")
                L.append("")
        if m.inputs:
            L.append(f"{T}// ---- inputs: filled from the host each frame (or set by game code when free) ----")
            for i in m.inputs:
                if i.host is not None:
                    h = i.host
                    src = f"host.{h.item}" + (f'("{h.arg}"' + (f', "{h.arg2}"' if h.arg2 is not None else "") + ")" if h.arg is not None else "")
                    L.append(f"{T}// <- {src}")
                    L.append(f'{T}UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Input", Transient)')
                else:
                    L.append(f'{T}UPROPERTY(VisibleAnywhere, BlueprintReadWrite, Category = "DAL|Input", Transient)')
                L.append(f"{T}{cpp_prop_type(i.ty)} {i.name}{member_init(i.ty, i.default)[0]};")
                L.append("")
        if m.states:
            L.append(f"{T}// ---- state: written by the logic, readable from the animation graph ----")
            for s in m.states:
                L.append(f'{T}UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|State", Transient)')
                L.append(f"{T}{cpp_prop_type(s.ty)} {s.name}{member_init(s.ty, s.default)[0]};")
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
        if m.linked is not None:
            L.append(f'#include "{m.linked.parent}.h"')
        if self.hosth:
            L.append('#include "DalHost.h"')
        for lib in m.libs:
            L.append(f'#include "{lib.stem}.h"')
        if self.uses_tags(any_origin=True) or any(isinstance(d.ty, TagType) for d in [*m.params, *m.inputs, *m.states]):
            L.append('#include "GameplayTagContainer.h"')
        if any(t.origin is None for t in m.tagsets):
            L.append('#include "NativeGameplayTags.h"')
        for inc in self.data_includes(None):
            L.append(f'#include "{inc}"')
        if (any(enum_cast(d.ty) or (isinstance(d.ty, (MapType, ArrayType)) and enum_cast(getattr(d.ty, "elem", None))) for d in [*m.params, *m.inputs, *m.states])
                or any(enum_cast(f.ty) or enum_cast(getattr(f.ty, "elem", None)) for s in [*m.structs, *self.notify_structs(None)] if s.origin is None for f in s.fields)
                or self.uses("movement_mode") or self.world_queries):
            L.append('#include "Engine/EngineTypes.h"')
        if self.post:
            L.append('#include "Animation/AnimInstanceProxy.h"')
        if self.bind_begin or self.bind_end or any(n.origin is None for n in m.notifies):
            L.append('#include "Animation/AnimNotifies/AnimNotify.h"')
        if any(n.origin is None and n.state for n in m.notifies):
            L.append('#include "Animation/AnimNotifies/AnimNotifyState.h"')
        if self.queue_target:
            L.append('#include "Templates/Function.h"')
        L.append(f'#include "{m.name}.generated.h"')
        L.append("")
        fwd: list[str] = []
        for peer in m.peers:
            fwd.append(f"class U{peer.logic};")
        if m.linked is not None:
            fwd.append("class ACharacter;")
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
            if st.origin is None and st.name not in _NATIVE_STRUCTS:
                L.extend(self.struct_decl(st))
                L.append("")
        for nd in m.notifies:
            if nd.origin is None:
                L.extend(self.notify_decl(nd))
                L.append("")
        if self.post:
            L.append(f"/** The animation instance proxy of {self.cls}: its PostUpdate runs {self.cls}::DalPostUpdate (game thread, after the animation graph updated). */")
            L.append("USTRUCT()")
            L.append(f"struct {self.api}F{m.name}Proxy : public FAnimInstanceProxy")
            L.append("{")
            L.append(f"{T}GENERATED_BODY()")
            L.append("")
            L.append("public:")
            L.append(f"{T}F{m.name}Proxy() = default;")
            L.append("")
            L.append(f"{T}explicit F{m.name}Proxy(UAnimInstance* AnimationInstance);")
            L.append("")
            L.append("protected:")
            L.append(f"{T}virtual void PostUpdate(UAnimInstance* AnimationInstance) const override;")
            L.append("};")
            L.append("")
        if self.phased:
            L.append(f"class {self.cls};")
            L.append("")
            L.append(f"/** The tick function of one update block of {self.cls}: it runs {self.cls}::DalRunPhase for its slot. */")
            L.append(f"struct {self.api}F{m.name}PhaseTick : public FActorComponentTickFunction")
            L.append("{")
            L.append(f"{T}{self.cls}* Target = nullptr;")
            L.append(f"{T}int32 Phase = 0;")
            L.append("")
            L.append(f"{T}virtual void ExecuteTick(float DeltaTime, ELevelTick TickType, ENamedThreads::Type CurrentThread, const FGraphEventRef& MyCompletionGraphEvent) override;")
            L.append(f"{T}virtual FString DiagnosticMessage() override;")
            L.append("};")
            L.append("")
        if self.actor:
            L.append("UCLASS(ClassGroup = (Dal), Meta = (BlueprintSpawnableComponent))")
        else:
            L.append("UCLASS()")
        L.append(f"class {self.api}{self.cls} : public {self.parent}{', public DalHost::IEventSink' if self.sink else ''}")
        L.append("{")
        L.append(f"{T}GENERATED_BODY()")
        L.append("")
        L.append("public:")
        if self.actor:
            L.append(f"{T}{self.cls}();")
            L.append("")
        L.extend(self.property_lines())
        for p in m.params:
            if isinstance(p.ty, StructType) and p.ty.asset_root:
                L.append(f"{T}/** The settings asset assigned to {p.name}; the class defaults when there is none. */")
                L.append(f"{T}const {p.ty.cpp_type}& DalSettings_{p.name}() const {{ return {p.name} != nullptr ? *{p.name} : *GetDefault<{p.ty.cpp_type}>(); }}")
                L.append("")
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
        if m.peers:
            L.append(f"{T}// ---- peers: the other logics on this actor, found when play begins ----")
            for peer in m.peers:
                L.append(f'{T}UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Peer", Transient)')
                L.append(f"{T}TObjectPtr<U{peer.logic}> {peer.name};")
                L.append("")
        cls_name = self.cls
        if self.actor:
            L.append(f"{T}virtual void BeginPlay() override;")
            if self.bind_anim or self.end_handlers or self.sink:
                L.append(f"{T}virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;")
            if not self.phased:
                L.append(f"{T}virtual void TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction) override;")
        else:
            if m.linked is not None:
                L.append(f"{T}{cls_name}();")
                L.append("")
            L.append(f"{T}virtual void NativeInitializeAnimation() override;")
            if self.begin_handlers:
                L.append(f"{T}virtual void NativeBeginPlay() override;")
            L.append(f"{T}virtual void NativeUpdateAnimation(float DeltaSeconds) override;")
            L.append(f"{T}virtual void NativeThreadSafeUpdateAnimation(float DeltaSeconds) override;")
            if self.end_handlers or self.sink:
                L.append(f"{T}virtual void NativeUninitializeAnimation() override;")
            if self.post:
                L.append(f"{T}virtual FAnimInstanceProxy* CreateAnimInstanceProxy() override;")
        L.append("")
        L.append(f"{T}/** Restore every input and state to its declared default. */")
        L.append(f"{T}void DalResetState();")
        L.append("")
        thread_safe = "" if self.actor else ", Meta = (BlueprintThreadSafe)"
        if m.linked is not None:
            L.append(f"{T}// ---- linked instance: the main instance of the mesh, and the entries this one forwards to it ----")
            L.append(f'{T}UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Linked", Transient)')
            L.append(f"{T}TWeakObjectPtr<U{m.linked.parent}> Parent;")
            L.append("")
            L.append(f'{T}UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "DAL|Linked", Transient)')
            L.append(f"{T}TObjectPtr<ACharacter> Character;")
            L.append("")
            L.append(f'{T}UFUNCTION(BlueprintPure, Category = "DAL|Linked"{thread_safe})')
            L.append(f"{T}U{m.linked.parent}* GetParent() const {{ return Parent.Get(); }}")
            L.append("")
            for e in m.linked.entries:
                L.append(f"{T}/** Forwarded to the main instance: Parent->{e.name}(...). */")
                L.append(f'{T}UFUNCTION(BlueprintCallable, Category = "DAL|Entry"{thread_safe})')
                L.append(f"{T}void {e.name}({self.entry_params(e)});")
                L.append("")
        for e in m.entries:
            L.append(f"{T}/** Entry: call from a .dab with Call(Self.{e.name}). Uses the delta time of the current update. */"
                     if not self.actor else f"{T}/** Entry: callable from Blueprints. Uses the delta time of the last tick. */")
            L.append(f'{T}UFUNCTION(BlueprintCallable, Category = "DAL|Entry"{thread_safe})')
            L.append(f"{T}void {e.name}({self.entry_params(e)});")
            L.append("")
        for mt in self.methods():
            if mt.access == "public":
                L.extend(mt.decl())
                L.append("")
        if self.queue_target:
            for x in m.methods:
                if x.pub and not x.is_view:
                    params = cpp_params(x.params)
                    L.append(f"{T}/** Called by an actor logic: {x.name} runs at the start of the next animation update. */")
                    L.append(f"{T}void DalQueue_{x.name}({params});")
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
        L.append(f"{T}void DalSampleInputs(const int32 Phase = -1);" if self.consume_only is not None else f"{T}void DalSampleInputs();")
        L.append("")
        if not self.phased:
            queued = " (host commands are queued)" if (not self.actor and self.has_cmds) else ""
            L.append(f"{T}/** {'Game thread' if self.actor else 'Worker thread'}: the update body. Touches only this object's properties{queued}. */")
            L.append(f"{T}void DalUpdate(float DeltaSeconds);")
            L.append("")
        L.append("private:")
        L.append(f"{T}float Dal_DeltaSeconds = 0.0f;")
        if self.phased:
            L.append(f"{T}F{m.name}PhaseTick DalPhaseTicks[{len(self.slots)}];             // one tick function per update block")
        for n in notify_flags:
            L.append(f"{T}bool DalNotify_{n} = false;")
        if self.hosth:
            L.append("")
            if self.has_events or self.has_cmds:
                L.append(f"{T}FCriticalSection DalQueueLock;                   // guards the two queues below")
            if self.has_events:
                L.append(f"{T}TArray<DalHost::FEvent> DalEventQueue;           // posted by the host callbacks")
                L.append(f"{T}TArray<DalHost::FEvent> DalFrameEvents;          // taken for the running update")
                if self.sync_events:
                    L.append(f"{T}TArray<DalHost::FEvent> DalNowEvents;            // sync_events: the events being handled right now, in order")
                    L.append(f"{T}bool bDalInEvent = false;")
            if self.has_cmds:
                L.append(f"{T}TArray<DalHost::FCommand> DalCommandQueue;       // queued by the update, run on the game thread")
            if self.actor and self.bind_anim:
                L.append(f"{T}TWeakObjectPtr<UAnimInstance> DalBoundAnim;")
            if self.actor and self.bind_input:
                L.append(f"{T}TWeakObjectPtr<UEnhancedInputComponent> DalBoundInput;")
            if any(q.startswith("target_") for q in self.queries):
                L.append(f"{T}TArray<TWeakObjectPtr<AActor>> DalTargets;")
                L.append(f"{T}bool bDalTargetsFresh = false;")
        if self.uses_source:
            L.append("")
            L.append(f"{T}int32 DalCurrentInstance = INDEX_NONE;            // the montage instance that raised the notify whose handler is running")
        if self.queue_target:
            L.append("")
            L.append(f"{T}FCriticalSection DalPeerLock;                    // guards the queue of calls from actor logics")
            L.append(f"{T}TArray<TFunction<void()>> DalPeerQueue;          // calls from actor logics, run at the start of the update")
        methods = [mt for mt in self.methods() if mt.access != "public"]
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
        if self.uses(*_WORLD_ITEMS) or self.queries:
            add("Engine/World.h")
        if self.uses("time_dilation"):
            add("GameFramework/WorldSettings.h")
        if self.needs_character_read or self.movement_cmds or self.character_cmds:
            add("GameFramework/Character.h")
            add("GameFramework/CharacterMovementComponent.h")
        if self.uses(*_CAPSULE_ITEMS):
            add("Components/CapsuleComponent.h")
        if self.uses(*_MESH_ITEMS):
            add("Components/SkeletalMeshComponent.h")
            add("Engine/SkeletalMesh.h")
            add("PhysicsEngine/PhysicsAsset.h")
        if self.uses(*_CONSUME_ITEMS) or self.als_movement_cmds:
            add("DalCharacterMovementComponent.h")
        if self.ragdoll_cmds or self.uses(*_RAGDOLL_ITEMS):
            add("DalRagdollService.h")
        if "set_actor_rotation" in self.cmds:
            add("Engine/EngineTypes.h")
        if self.uses(*_PAWN_ITEMS, *_RAGDOLL_ITEMS) or self.needs_pawn_helper or self.controller or self.arm or self.camera:
            add("GameFramework/Pawn.h")
        if self.action_names():
            for x in ("Engine/LocalPlayer.h", "GameFramework/PlayerController.h", "EnhancedInputSubsystems.h",
                      "EnhancedPlayerInput.h", "InputAction.h", "InputActionValue.h"):
                add(x)
        # ---- v1
        if self.bind_input:
            for x in ("EnhancedInputComponent.h", "InputAction.h", "InputActionValue.h"):
                add(x)
        if "is_game_thread" in self.queries:
            add("CoreGlobals.h")
        if "proxy_component_transform" in self.queries:
            add("Animation/AnimInstanceProxy.h" if not self.actor else "Components/SkeletalMeshComponent.h")
            if self.actor:
                add("GameFramework/Actor.h")
        if _MONTAGE_ASSET_QUERIES & set(self.queries):
            add("Animation/AnimMontage.h")
        if {"montage_root_transform", "montage_last_root_transform"} & set(self.queries):
            add("Animation/AnimSequence.h")
        if self.needs_anim:
            add("Animation/AnimInstance.h")
            add("Animation/AnimMontage.h")
            if self.actor:
                add("Components/SkeletalMeshComponent.h")
        if self.bind_begin:
            add("Animation/AnimTypes.h")
        if self.post:
            add("Animation/AnimInstanceProxy.h")
        if any(n.origin is None for n in m.notifies):
            add("GameFramework/Actor.h")
            add("Components/SkeletalMeshComponent.h")
        if self.controller:
            add("GameFramework/Controller.h")
        if self.arm:
            add("GameFramework/SpringArmComponent.h")
        if self.camera:
            add("Camera/CameraComponent.h")
        if self.world_queries:
            add("CollisionQueryParams.h")
            add("Engine/HitResult.h")
            add("Engine/World.h")
        if self.rich_hits or any(q in _COMPONENT_QUERIES for q in self.queries):
            add("Components/PrimitiveComponent.h")
        if "is_walkable" in self.queries or "primitive_can_step_up" in self.queries:
            add("GameFramework/Character.h")
            add("GameFramework/CharacterMovementComponent.h")
        if any(q.startswith("target_") for q in self.queries):
            add("GameFramework/Actor.h")
            add("Kismet/GameplayStatics.h")
        if self.has_events or self.has_cmds:
            add("Misc/ScopeLock.h")
        if any(t.origin is None for t in m.tagsets):
            add("NativeGameplayTags.h")
        if self.phased:
            for x in ("GameFramework/Actor.h", "GameFramework/Pawn.h", "GameFramework/Controller.h", "GameFramework/Character.h",
                      "GameFramework/CharacterMovementComponent.h", "Components/SkeletalMeshComponent.h"):
                add(x)
        if m.linked is not None:
            for x in ("GameFramework/Character.h", "Components/SkeletalMeshComponent.h", "Animation/AnimInstance.h"):
                add(x)
        if m.peers:
            add("GameFramework/Actor.h")
            if any(p.kind == "anim" for p in m.peers):
                add("Components/SkeletalMeshComponent.h")
                add("Animation/AnimInstance.h")
        if self.queue_target or any(p.kind == "anim" for p in m.peers) or self.has_events or self.has_cmds:
            add("Misc/ScopeLock.h")
        for p in m.peers:
            add(f"{p.logic}.h")
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
        if self.uses_data:
            L.append('#include "DalData.h"')
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
        for nd in m.notifies:                                           # the notify classes of this file come first
            if nd.origin is None:
                blocks.extend(self.notify_def(nd))

        # ---- lifecycle
        if self.actor:
            if self.phased:
                ctor = [f"{cls}::{cls}()", "{",
                        f"{T}PrimaryComponentTick.bCanEverTick = false;              // every update block has its own tick function"]
                for i, slot in enumerate(self.slots):
                    ctor += [f"{T}DalPhaseTicks[{i}].Target = this;",
                             f"{T}DalPhaseTicks[{i}].Phase = {i};",
                             f"{T}DalPhaseTicks[{i}].bCanEverTick = {'false' if slot in ir.HOOK_SLOTS else 'true'};" + ("                // a hook slot: the movement component runs it" if slot in ir.HOOK_SLOTS else ""),
                             f"{T}DalPhaseTicks[{i}].TickGroup = {'TG_PostPhysics' if slot == 'after_anim' else 'TG_PrePhysics'};"]
                ctor.append("}")
                blocks.append(ctor)
            else:
                blocks.append([f"{cls}::{cls}()", "{",
                               f"{T}PrimaryComponentTick.bCanEverTick = true;",
                               f"{T}PrimaryComponentTick.TickGroup = TG_PrePhysics;", "}"])
            begin = [f"void {cls}::BeginPlay()", "{", f"{T}Super::BeginPlay();", "", f"{T}DalResetState();"]
            if self.sink:
                begin.append(f"{T}DalHost::FEventRouter::Register(this);")
            if m.peers:
                begin.append(f"{T}DalBindPeers();")
            if self.bind_anim or self.bind_input:
                begin.append(f"{T}DalBindHost();")
            if self.phased:
                begin.append(f"{T}DalSetupTickOrder();")
            if self.begin_handlers:
                begin.append(f"{T}DalSampleInputs();")
                begin.extend(f"{T}DalHandler_{k}();" for k in self.begin_handlers)
                if self.has_cmds:
                    begin.append(f"{T}DalFlushCommands();")
            begin.append("}")
            blocks.append(begin)
            if self.bind_anim or self.end_handlers or self.sink:
                end_lines = [f"void {cls}::EndPlay(const EEndPlayReason::Type EndPlayReason)", "{"]
                if self.end_handlers:
                    end_lines.append(f"{T}DalSampleInputs();")
                    end_lines.extend(f"{T}DalHandler_{k}();" for k in self.end_handlers)
                    if self.has_cmds:
                        end_lines.append(f"{T}DalFlushCommands();")
                    if self.bind_anim:
                        end_lines.append("")
                if self.bind_anim:
                    end_lines += [f"{T}if (DalBoundAnim.IsValid())", f"{T}{{",
                                  f"{T}{T}DalUnbindAnim(DalBoundAnim.Get());", f"{T}}}",
                                  f"{T}DalBoundAnim.Reset();", ""]
                elif self.end_handlers or not self.sink:
                    end_lines.append("")
                if self.sink:
                    end_lines += [f"{T}DalHost::FEventRouter::Unregister(this);", ""]
                end_lines += [f"{T}Super::EndPlay(EndPlayReason);", "}"]
                blocks.append(end_lines)
            if not self.phased:
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
            if m.linked is not None:
                blocks.append([f"{cls}::{cls}()", "{", f"{T}bUseMainInstanceMontageEvaluationData = true;", "}"])
            init = [f"void {cls}::NativeInitializeAnimation()", "{", f"{T}Super::NativeInitializeAnimation();", "", f"{T}DalResetState();"]
            if self.sink:
                init.append(f"{T}DalHost::FEventRouter::Register(this);")
            if m.linked is not None:
                init.append(f"{T}DalResolveParent();")
            if self.bind_anim:
                init.append(f"{T}DalBindAnim(this);")
            init.append("}")
            blocks.append(init)
            if self.begin_handlers:
                bp = [f"void {cls}::NativeBeginPlay()", "{", f"{T}Super::NativeBeginPlay();", "", f"{T}DalSampleInputs();"]
                bp.extend(f"{T}DalHandler_{k}();" for k in self.begin_handlers)
                if self.has_cmds:
                    bp.append(f"{T}DalFlushCommands();")
                bp.append("}")
                blocks.append(bp)
            upd = [f"void {cls}::NativeUpdateAnimation(const float DeltaSeconds)", "{",
                   f"{T}Super::NativeUpdateAnimation(DeltaSeconds);", ""]
            if m.linked is not None:
                upd += [f"{T}if (!Parent.IsValid())", f"{T}{{", f"{T}{T}DalResolveParent();", f"{T}}}", ""]
            if traffic:
                if self.post_flush:
                    upd.append(f"{T}// Game thread: gather the host values and the events; the worker thread only reads this object.")
                    upd.append(f"{T}// (The commands of the frame run in DalPostUpdate, after the animation graph updated.)")
                else:
                    upd.append(f"{T}// Game thread: run what the previous update asked for, gather the host values and the events;")
                    upd.append(f"{T}// the worker thread only reads this object.")
                    if self.has_cmds:
                        upd.append(f"{T}DalFlushCommands();")
                upd.append(f"{T}DalSampleInputs();")
                if self.queue_target:
                    upd.append(f"{T}DalDrainPeerCalls();")
                    if self.game_thread_flush:                          # the commands of what other logics asked of this one run now: the graph updates next
                        upd.append(f"{T}DalFlushCommands();")
                if self.has_events:
                    upd.append(f"{T}DalTakeEvents();")
            else:
                upd.append(f"{T}// Game thread: gather the host values; the worker thread only reads this object.")
                upd.append(f"{T}DalSampleInputs();")
                if self.queue_target:
                    upd.append(f"{T}DalDrainPeerCalls();")
            upd.append("}")
            blocks.append(upd)
            tsu = [f"void {cls}::NativeThreadSafeUpdateAnimation(const float DeltaSeconds)", "{",
                   f"{T}Super::NativeThreadSafeUpdateAnimation(DeltaSeconds);", ""]
            if m.linked is not None:
                # The engine calls NativeUpdateAnimation (the game thread update) for the main instance only; a linked graph instance is updated through
                # its proxy, which runs this one.  At NativeInitializeAnimation the mesh does not always know the main instance yet (a graph that links
                # instances while it is being initialised), so the parent is looked up again here until it is found.
                tsu += [f"{T}if (!Parent.IsValid())", f"{T}{{", f"{T}{T}DalResolveParent();", f"{T}}}", ""]
            tsu += [f"{T}Dal_DeltaSeconds = DeltaSeconds;", f"{T}DalUpdate(DeltaSeconds);"]
            if self.game_thread_flush:
                tsu += self.game_thread_flush_lines()
            tsu.append("}")
            blocks.append(tsu)
            if self.post:
                blocks.append([f"F{m.name}Proxy::F{m.name}Proxy(UAnimInstance* AnimationInstance) : FAnimInstanceProxy(AnimationInstance)", "{", "}"])
                blocks.append([f"void F{m.name}Proxy::PostUpdate(UAnimInstance* AnimationInstance) const", "{",
                               f"{T}FAnimInstanceProxy::PostUpdate(AnimationInstance);", "",
                               f"{T}// UAnimInstance::PostUpdateAnimation() cannot be overridden, so the proxy's hook is where the game thread resumes after the graph update.",
                               f"{T}if ({cls}* DalLogic = Cast<{cls}>(AnimationInstance))", f"{T}{{", f"{T}{T}DalLogic->DalPostUpdate();", f"{T}}}", "}"])
                blocks.append([f"FAnimInstanceProxy* {cls}::CreateAnimInstanceProxy()", "{", f"{T}return new F{m.name}Proxy(this);", "}"])
            if self.end_handlers or self.sink:
                ep = [f"void {cls}::NativeUninitializeAnimation()", "{"]
                if self.end_handlers:
                    ep.append(f"{T}DalSampleInputs();")
                    ep.extend(f"{T}DalHandler_{k}();" for k in self.end_handlers)
                    if self.has_cmds:
                        ep.append(f"{T}DalFlushCommands();")
                    ep.append("")
                if self.sink:
                    ep += [f"{T}DalHost::FEventRouter::Unregister(this);", ""]
                ep += [f"{T}Super::NativeUninitializeAnimation();", "}"]
                blocks.append(ep)

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
            if self.sync_events:
                reset.append(f"{T}DalNowEvents.Reset();")
                reset.append(f"{T}bDalInEvent = false;")
        if self.has_cmds:
            reset.append(f"{T}DalCommandQueue.Reset();")
        if self.queue_target:
            reset.append(f"{T}DalPeerQueue.Reset();")
        if any(q.startswith("target_") for q in self.queries):
            reset.append(f"{T}DalTargets.Reset();")
            reset.append(f"{T}bDalTargetsFresh = false;")
        reset.append("}")
        blocks.append(reset)
        blocks.append(self.sample_function())

        # ---- v1 members (helpers, events, commands, binding, queries)
        for mt in self.methods():
            if mt.name == "DalDispatchEvents" or mt.name.startswith("DalHandler_") or mt.external:
                continue
            blocks.append(mt.define(cls))

        # ---- update
        if self.phased:
            for mt in self.methods():
                if mt.name == "DalDispatchEvents" or mt.name.startswith("DalHandler_"):
                    blocks.append(mt.define(cls))
            for k, slot in enumerate(self.slots):
                u = next((x for x in m.updates if x.phase == slot), None)
                lines = [f"void {cls}::DalUpdate_{camel(slot)}([[maybe_unused]] const float {u.dt_name if u else 'DeltaSeconds'})", "{"]
                body = []
                if k == 0 and u is not None and u.pre:
                    body.extend(self.stmts(u.pre, 1))
                if (k == 0 or self.m.slot_events) and self.has_events:
                    body.append(f"{T}DalDispatchEvents();")
                if u is not None:
                    body.extend(self.stmts(u.body, 1))
                if not body:
                    body.append(f"{T}// (no update body)")
                lines.extend(body)
                lines.append("}")
                blocks.append(lines)
            blocks.extend(self.phase_blocks())
        else:
            u = m.update
            upd_lines = [f"void {cls}::DalUpdate([[maybe_unused]] const float {u.dt_name if u else 'DeltaSeconds'})", "{"]
            body = []
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
        if m.linked is not None:
            for e in m.linked.entries:
                args = ", ".join(p.name for p in e.params)
                blocks.append([f"void {cls}::{e.name}({cpp_params([(p.name, p.ty) for p in e.params], True, True)})", "{",
                               f"{T}if (Parent.IsValid())", f"{T}{{", f"{T}{T}Parent->{e.name}({args});", f"{T}}}", "}"])
        for e in m.entries:
            lines = [f"void {cls}::{e.name}({cpp_params([(p.name, p.ty) for p in e.params], True, True)})", "{"]
            if e.dt_name:
                lines.append(f"{T}[[maybe_unused]] const float {e.dt_name} = Dal_DeltaSeconds;")
            lines.extend(self.stmts(e.body, 1))
            if self.actor and self.has_cmds:
                lines.append(f"{T}DalFlushCommands();")
            elif self.game_thread_flush:
                lines.extend(self.game_thread_flush_lines())
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
        if self.queue_target:
            for x in m.methods:
                if x.pub and not x.is_view:
                    params = cpp_params(x.params)
                    caps = ", ".join(["this"] + [n for n, _t in x.params])
                    args = ", ".join(n for n, _t in x.params)
                    blocks.append([f"void {cls}::DalQueue_{x.name}({params})", "{",
                                   f"{T}FScopeLock DalLock(&DalPeerLock);",
                                   f"{T}DalPeerQueue.Add([{caps}]() {{ {x.name}({args}); }});", "}"])
        for k, blk in enumerate(blocks):
            if k:
                L.append("")
            L.extend(blk)
        return "\n".join(L) + "\n"

    def sample_function(self) -> list[str]:
        """DalSampleInputs: host reads, then peer reads.  A peer called like one of the locals (`Character`) renames the local."""
        import re

        m = self.m
        peer_inputs = [i for i in m.inputs if i.peer is not None]
        if not peer_inputs and not m.peers:
            return self.host_sample()
        host_lines = self.host_sample()
        head, body = host_lines[:2], host_lines[2:-1]
        clash = [p.name for p in m.peers if p.name in SAMPLE_LOCALS]
        for n in clash:
            body = [re.sub(rf"\b{n}\b", "Dal" + n, ln) for ln in body]
        if body == [f"{T}// (no host-bound inputs)"]:
            body = []
        L = list(head)
        if m.peers:
            L.append(f"{T}DalBindPeers();")
            if body:
                L.append("")
        L.extend(body)
        if peer_inputs and body and body[-1] != "":
            L.append("")
        for i in peer_inputs:
            pu = i.peer
            assert pu is not None
            if pu.settings_asset:                                      # a member of the peer's settings data asset: through its accessor
                value = f"{pu.peer}->DalSettings_{pu.path[0]}()" + "".join("." + p for p in pu.path[1:])
            else:
                value = f"{pu.peer}->" + ".".join(pu.path)
            if enum_cast(i.ty):
                value = f"static_cast<{enum_cast(i.ty)}>({value})"
            elif len(pu.path) > 1:                                    # a member of a struct (or of a settings asset): see double_member
                value = from_member(value, i.ty)
            L.append(f"{T}{i.name} = {pu.peer} != nullptr ? {value} : {cpp_value(i.ty, i.default)};")
        L.append("}")
        return L

    def host_sample(self) -> list[str]:
        m = self.m
        L = [f"void {self.cls}::DalSampleInputs(" + ("[[maybe_unused]] const int32 Phase" if self.consume_only is not None else "") + ")", "{"]
        host = self.host_items()
        if not host:
            L.append(f"{T}// (no host-bound inputs)")
            L.append("}")
            return L
        needs_movement = self.needs_movement_read
        needs_character = self.needs_character_read
        needs_pawn = needs_character or self.uses(*_PAWN_ITEMS, *_RAGDOLL_ITEMS)
        if needs_pawn:
            L.append(f"{T}const APawn* Pawn = " + ("Cast<APawn>(GetOwner());" if self.actor else "TryGetPawnOwner();"))
        if needs_character:
            L.append(f"{T}const ACharacter* Character = Cast<ACharacter>(Pawn);")
        if needs_movement:
            L.append(f"{T}const UCharacterMovementComponent* Movement = IsValid(Character) ? Character->GetCharacterMovement() : nullptr;")
        if self.uses(*_CAPSULE_ITEMS):
            L.append(f"{T}const UCapsuleComponent* Capsule = IsValid(Character) ? Character->GetCapsuleComponent() : nullptr;")
        if self.uses(*_CONSUME_ITEMS):
            gate = ""
            if self.consume_only is not None:                                 # tick slots that do not read it leave it to the ones that do
                slots = "".join(f" || Phase == {self.slots.index(s)}" for s in self.consume_only)
                L.append(f"{T}const bool bDalConsume = Phase < 0{slots};")
                gate = "bDalConsume && "
            L += [f"{T}// consume-on-read: the movement component hands the pre-penetration velocity out once (DalRuntime)",
                  f"{T}FVector DalPrePenetration = FVector::ZeroVector;",
                  f"{T}bool bDalPrePenetration = false;",
                  f"{T}if (UDalCharacterMovementComponent* DalMovement = {gate}IsValid(Character) ? Cast<UDalCharacterMovementComponent>(Character->GetCharacterMovement()) : nullptr)",
                  f"{T}{{",
                  f"{T}{T}bDalPrePenetration = DalMovement->TryConsumePrePenetrationAdjustmentVelocity(DalPrePenetration);",
                  f"{T}}}"]
        if self.action_names():
            L.append(f"{T}const UEnhancedPlayerInput* PlayerInput = DalGetPlayerInput(Pawn);")
        if self.uses(*_WORLD_ITEMS):
            L.append(f"{T}const UWorld* World = GetWorld();")
        if self.montage_reads or self.curve_reads:
            L.append(f"{T}const UAnimInstance* DalAnim = DalGetAnimInstance();")
        if self.uses(*_MESH_ITEMS):
            L.append(f"{T}const USkeletalMeshComponent* DalMesh = DalGetMesh();")
        if self.uses(*_RAGDOLL_ITEMS):
            L.append(f"{T}const UDalRagdollService* DalRagdoll = Pawn != nullptr ? Pawn->FindComponentByClass<UDalRagdollService>() : nullptr;")
        L.append("")
        for i, h in host:
            default = cpp_value(i.ty, i.default)
            name = i.name
            it = h.item
            if it == "time":
                L.append(f"{T}{name} = World != nullptr ? static_cast<float>(World->GetTimeSeconds()) : 0.0f;")
                continue
            if it == "time_dilation":
                L.append(f"{T}{name} = World != nullptr && World->GetWorldSettings() != nullptr ? World->GetWorldSettings()->GetEffectiveTimeDilation() : 1.0f;")
                continue
            if it == "real_delta_time":
                L.append(f"{T}{name} = World != nullptr ? World->DeltaRealTimeSeconds : 0.0f;")
                continue
            if it == "mesh_scale":
                L.append(f"{T}{name} = DalMesh != nullptr ? static_cast<float>(DalMesh->GetComponentScale().Z) : 1.0f;")
                continue
            if it == "notify":
                L.append(f"{T}{name} = DalNotify_{h.arg};")
                L.append(f"{T}DalNotify_{h.arg} = false;")
                continue
            if it == "anim_curve":
                L.append(f"{T}{name} = DalAnim != nullptr ? DalAnim->GetCurveValue(FName(TEXT(\"{h.arg}\"))) : 0.0f;")
                continue
            if it == "root_motion_mode":
                L.append(f"{T}{name} = DalAnim != nullptr ? DalAnim->RootMotionMode.GetValue() : {default};")
                continue
            if it == "ragdoll_supported":
                L.append(f"{T}{name} = false;")
                L.append(f"{T}if (const UPhysicsAsset* DalPhysics = DalMesh != nullptr ? DalMesh->GetPhysicsAsset() : nullptr)")
                L.append(f"{T}{{")
                L.append(f'{T}{T}{name} = DalPhysics->FindBodyIndex(FName(TEXT("pelvis"))) != INDEX_NONE && DalPhysics->FindBodyIndex(FName(TEXT("spine_03"))) != INDEX_NONE;')
                L.append(f"{T}}}")
                continue
            if it in _RAGDOLL_ITEMS:                                          # the pelvis body of the ragdoll service (ALS RefreshRagdolling, StopRagdollingImplementation)
                value = {"ragdoll_pelvis_location": "FVector3f(DalRagdoll->GetPelvisLocation())",
                         "ragdoll_velocity": "FVector3f(DalRagdoll->GetPelvisVelocity())",
                         "ragdoll_facing_up": "DalRagdoll->IsFacingUp()"}[it]
                L.append(f"{T}{name} = DalRagdoll != nullptr ? {value} : {default};")
                continue
            if it == "mesh_world_transform":
                L.append(f"{T}{name} = DalMesh != nullptr ? FTransform3f(DalMesh->GetComponentTransform()) : FTransform3f();")
                continue
            if it in ("bone_component_transform", "bone_world_transform"):
                space = "RTS_Component" if it == "bone_component_transform" else "RTS_World"
                L.append(f'{T}{name} = DalMesh != nullptr ? FTransform3f(DalMesh->GetSocketTransform(FName(TEXT("{h.arg}")), {space})) : FTransform3f();')
                continue
            if it == "ref_path_child_translation":
                L.append(f'{T}{name} = DalRefPathChildTranslation(FName(TEXT("{h.arg}")), FName(TEXT("{h.arg2}")));')
                continue
            if it in ("pre_penetration_velocity", "pre_penetration_velocity_valid"):
                value = f"FVector3f(DalPrePenetration)" if it == "pre_penetration_velocity" else "bDalPrePenetration"
                if self.consume_only is not None:
                    L += [f"{T}if (bDalConsume)", f"{T}{{", f"{T}{T}{name} = {value};", f"{T}}}"]
                else:
                    L.append(f"{T}{name} = {value};")
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
                "walkable_floor_z": "Movement->GetWalkableFloorZ()",
                "movement_mode_custom": "static_cast<int32>(Movement->CustomMovementMode)",
            }
            pawn_expr = {
                "actor_location": "FVector3f(Pawn->GetActorLocation())",
                "actor_rotation": "FQuat4f(Pawn->GetActorQuat())",
                "control_rotation": "FRotator3f(Pawn->GetControlRotation())",
                "actor_ready": "Pawn->HasActorRegisteredAllComponents()",
                "actor_time_dilation": "Pawn->CustomTimeDilation",
            }
            character_expr = {
                "has_root_motion": ("Character", "Character->HasAnyRootMotion()"),
                "capsule_radius": ("Capsule", "Capsule->GetScaledCapsuleRadius()"),
                "capsule_half_height": ("Capsule", "Capsule->GetScaledCapsuleHalfHeight()"),
                "actor_scale": ("Capsule", "static_cast<float>(Capsule->GetComponentScale().Z)"),
            }
            if it in character_expr:
                guard, value = character_expr[it]
                L.append(f"{T}{name} = {default};")
                L.append(f"{T}if ({guard} != nullptr)")
                L.append(f"{T}{{")
                L.append(f"{T}{T}{name} = {value};")
                L.append(f"{T}}}")
            elif it in movement_expr:
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
    with generation_scope(module, options):
        return _generate_cpp(module, options)


@contextlib.contextmanager
def generation_scope(module: ir.Module, options: CppOptions):
    """What the options change in how the types of the module are written (member types, the C++ names of settings and native structs), for the
    duration of the block: the generation itself, and whatever writes code against the generated types (the test driver)."""
    global _DOUBLE_MEMBERS, _OBJECT_SETTINGS, _NATIVE_STRUCTS, _CHANNEL_DEFAULTS
    _DOUBLE_MEMBERS = options.double_members
    renamed: list = []                                       # (struct, its C++ type before): restored when the block is over
    objects: set = set()
    natives: dict = {}
    try:
        declared = {st.name: st for st in module.structs}
        for name in options.object_settings:
            st = declared.get(name)
            if st is None:
                continue                                     # a file that does not use the type
            if not st.is_settings:
                raise ValueError(f"object settings `{name}` is not a settings type")
            objects.add(name)
            renamed.append((st, st.cpp_type))
            st.cpp_type = f"F{name}Value"                    # U<Name> is the object; the value the logic reads must not share its engine name
        for spec in options.native_structs:
            name, _, rest = spec.partition("=")
            ctype, _, header = rest.partition("=")
            if not name or not ctype:
                raise ValueError(f"native struct `{spec}`: Name=FEngineType[=Header] is needed")
            st = declared.get(name)
            if st is None:
                continue
            natives[name] = (ctype, header)
            renamed.append((st, st.cpp_type))
            st.cpp_type = ctype
        channels: dict = {}
        for spec in options.channel_defaults:
            target, _, names = spec.partition("=")
            owner, _, member = target.partition(".")
            if not owner or not member or not names:
                raise ValueError(f"channel default `{spec}`: Struct.Member=Channel,Channel is needed")
            st = declared.get(owner)
            if st is None:
                continue
            field_ = st.field(member)
            if field_ is None or field_.ty is not CHANNEL_SET:
                raise ValueError(f"channel default `{spec}`: {owner}.{member} is not a channel_set member")
            listed = [x.strip() for x in names.split(",") if x.strip()]
            for x in listed:
                if x not in CHANNEL.cpp_members:
                    raise ValueError(f"channel default `{spec}`: `{x}` is not a channel")
            channels[(owner, member)] = [CHANNEL.cpp_members[x] for x in listed]
        _OBJECT_SETTINGS = frozenset(objects)
        _NATIVE_STRUCTS = natives
        _CHANNEL_DEFAULTS = channels
        yield
    finally:
        for st, old in renamed:
            st.cpp_type = old
        _DOUBLE_MEMBERS = False
        _OBJECT_SETTINGS = frozenset()
        _NATIVE_STRUCTS = {}
        _CHANNEL_DEFAULTS = {}


def _generate_cpp(module: ir.Module, options: CppOptions) -> dict[str, str]:
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
    if g.uses_data:
        files["DalData.h"] = read("DalData.h")
    if g.hosth:
        files["DalHost.h"] = read("DalHost.h")
    return files
