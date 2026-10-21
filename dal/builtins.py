"""The DAL standard library: built-in functions, constants and constructors.

One table drives everything: the type checker (overload resolution), the Python
backend (``dal.runtime.<py>``), the C++ backend (``DalMath::<cpp>`` in
``dal/cpp/DalMath.h``) and the reference documentation (``ue`` names the UE
function whose behaviour is reproduced).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import runtime as rt
from .types import BLEND_OPTION, BOOL, CURVE, FLOAT, INT, QUAT, ROTATOR, SPRING, SPRING3, TRANSFORM, VEC2, VEC3, Type

_TYPES = {"float": FLOAT, "int": INT, "bool": BOOL, "vec2": VEC2, "vec3": VEC3, "rotator": ROTATOR, "quat": QUAT, "spring": SPRING, "spring3": SPRING3, "BlendOption": BLEND_OPTION, "curve": CURVE, "transform": TRANSFORM}


@dataclass(frozen=True)
class Sig:
    params: tuple[Type, ...]
    ret: Type

    def text(self, name: str) -> str:
        return f"{name}({', '.join(t.name for t in self.params)}) -> {self.ret.name}"


@dataclass(frozen=True)
class Builtin:
    name: str
    sigs: tuple[Sig, ...]
    py: str      # attribute of dal.runtime
    cpp: str     # function in namespace DalMath
    ue: str      # UE function reproduced
    doc: str = ""


BUILTINS: dict[str, Builtin] = {}


def _parse_sig(text: str) -> Sig:
    left, right = text.split("->")
    params = tuple(_TYPES[w] for w in left.split())
    return Sig(params, _TYPES[right.strip()])


def _pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def _b(name: str, sigs: list[str], ue: str, py: str | None = None, cpp: str | None = None, doc: str = "") -> None:
    BUILTINS[name] = Builtin(
        name,
        tuple(_parse_sig(s) for s in sigs),
        py or name,
        cpp or _pascal(name),
        ue,
        doc,
    )


# ---- scalar --------------------------------------------------------------------
_b("abs", ["int -> int", "float -> float"], "FMath::Abs", py="abs_")
_b("sign", ["int -> int", "float -> float"], "FMath::Sign")
_b("min", ["int int -> int", "float float -> float"], "FMath::Min", py="min_")
_b("max", ["int int -> int", "float float -> float"], "FMath::Max", py="max_")
_b("clamp", ["int int int -> int", "float float float -> float"], "FMath::Clamp")
_b("saturate", ["float -> float"], "FMath::Clamp(x, 0, 1)")
_b("square", ["int -> int", "float -> float"], "FMath::Square")
_b("lerp", ["float float float -> float", "vec2 vec2 float -> vec2", "vec3 vec3 float -> vec3"], "FMath::Lerp")
_b("floor", ["float -> float"], "FMath::FloorToFloat", py="floor_")
_b("ceil", ["float -> float"], "FMath::CeilToFloat", py="ceil_")
_b("round", ["float -> float"], "FMath::RoundToFloat", py="round_")
_b("trunc", ["float -> float"], "FMath::TruncToFloat", py="trunc_")
_b("frac", ["float -> float"], "FMath::Frac")
_b("to_int", ["float -> int"], "FMath::TruncToInt")
_b("floor_to_int", ["float -> int"], "FMath::FloorToInt")
_b("ceil_to_int", ["float -> int"], "FMath::CeilToInt")
_b("round_to_int", ["float -> int"], "FMath::RoundToInt")
_b("fmod", ["float float -> float"], "FMath::Fmod")
_b("sqrt", ["float -> float"], "FMath::Sqrt")
_b("inv_sqrt", ["float -> float"], "FMath::InvSqrt")
_b("pow", ["float float -> float"], "FMath::Pow", py="pow_")
_b("exp", ["float -> float"], "FMath::Exp")
_b("log", ["float -> float"], "FMath::Loge")
_b("sin", ["float -> float"], "FMath::Sin")
_b("cos", ["float -> float"], "FMath::Cos")
_b("tan", ["float -> float"], "FMath::Tan")
_b("asin", ["float -> float"], "FMath::Asin")
_b("acos", ["float -> float"], "FMath::Acos")
_b("atan", ["float -> float"], "FMath::Atan")
_b("atan2", ["float float -> float"], "FMath::Atan2", doc="atan2(y, x), radians")
_b("deg_to_rad", ["float -> float"], "FMath::DegreesToRadians", cpp="DegToRad")
_b("rad_to_deg", ["float -> float"], "FMath::RadiansToDegrees", cpp="RadToDeg")
_b("inv_exp_approx", ["float -> float"], "FMath::InvExpApprox")
_b("is_nearly_zero", ["float -> bool", "float float -> bool"], "FMath::IsNearlyZero", doc="default tolerance SMALL_NUMBER")
_b("is_nearly_equal", ["float float -> bool", "float float float -> bool"], "FMath::IsNearlyEqual", doc="default tolerance SMALL_NUMBER")

# ---- angles / ranges -------------------------------------------------------------
_b("unwind_degrees", ["float -> float"], "FMath::UnwindDegrees")
_b("normalize_axis", ["float -> float"], "FRotator::NormalizeAxis", doc="(-180, 180]")
_b("clamp_axis", ["float -> float"], "FRotator::ClampAxis", doc="[0, 360)")
_b("delta_angle", ["float float -> float"], "FMath::FindDeltaAngleDegrees", doc="shortest signed angle from a to b")
_b("clamp_angle", ["float float float -> float"], "FMath::ClampAngle")
_b("range_pct", ["float float float -> float"], "FMath::GetRangePct")
_b("map_range_clamped", ["float float float float float -> float"], "FMath::GetMappedRangeValueClamped",
   doc="map_range_clamped(in_lo, in_hi, out_lo, out_hi, x)")
_b("map_range", ["float float float float float -> float"], "FMath::GetMappedRangeValueUnclamped",
   doc="map_range(in_lo, in_hi, out_lo, out_hi, x)")

# ---- interpolation -----------------------------------------------------------------
_b("interp_to",
   ["float float float float -> float", "vec2 vec2 float float -> vec2", "vec3 vec3 float float -> vec3",
    "rotator rotator float float -> rotator", "quat quat float float -> quat"],
   "FMath::FInterpTo / Vector2DInterpTo / VInterpTo / RInterpTo / QInterpTo",
   doc="interp_to(current, target, dt, speed)")
_b("interp_constant_to",
   ["float float float float -> float", "vec2 vec2 float float -> vec2", "vec3 vec3 float float -> vec3",
    "rotator rotator float float -> rotator", "quat quat float float -> quat"],
   "FMath::FInterpConstantTo / Vector2DInterpConstantTo / VInterpConstantTo / RInterpConstantTo / QInterpConstantTo",
   doc="interp_constant_to(current, target, dt, speed)")
_b("exponential_smoothing",
   ["float float float float -> float", "vec2 vec2 float float -> vec2", "vec3 vec3 float float -> vec3"],
   "FMath::ExponentialSmoothingApprox", doc="exponential_smoothing(current, target, dt, smoothing_time) returns the new value")

# ---- vectors -------------------------------------------------------------------------
_b("length", ["vec2 -> float", "vec3 -> float"], "TVector::Size")
_b("length_sq", ["vec2 -> float", "vec3 -> float"], "TVector::SizeSquared")
_b("normalize", ["vec2 -> vec2", "vec3 -> vec3", "quat -> quat"], "TVector2::GetSafeNormal / TVector::GetSafeNormal / TQuat::GetNormalized")
_b("dot", ["vec2 vec2 -> float", "vec3 vec3 -> float"], "TVector::DotProduct")
_b("cross", ["vec3 vec3 -> vec3"], "TVector::CrossProduct")
_b("distance", ["vec2 vec2 -> float", "vec3 vec3 -> float"], "TVector::Dist")
_b("clamp_length", ["vec2 float -> vec2", "vec3 float -> vec3"], "TVector::GetClampedToMaxSize")

# ---- rotations -------------------------------------------------------------------------
_b("to_quat", ["rotator -> quat"], "FRotator::Quaternion")
_b("to_rotator", ["quat -> rotator"], "FQuat::Rotator")
_b("quat_inverse", ["quat -> quat"], "FQuat::Inverse")
_b("slerp", ["quat quat float -> quat"], "FQuat::Slerp")
_b("rotate", ["quat vec3 -> vec3", "rotator vec3 -> vec3"], "FQuat::RotateVector / FRotator::RotateVector")
_b("unrotate", ["quat vec3 -> vec3", "rotator vec3 -> vec3"], "FQuat::UnrotateVector / FRotator::UnrotateVector")
_b("delta_rotator", ["rotator rotator -> rotator"], "(A - B).GetNormalized()", doc="delta_rotator(a, b)")
_b("normalize_rotator", ["rotator -> rotator"], "FRotator::GetNormalized")
_b("look_at", ["vec3 vec3 -> rotator"], "(To - From).ToOrientationRotator()", doc="look_at(from, to): yaw / pitch that face `to` (roll 0)")
_b("angle_between", ["vec3 vec3 -> float"], "Acos(Dot(GetSafeNormal(A), GetSafeNormal(B)))", doc="angle in degrees, 0 if either vector is zero")
_b("fast_lerp", ["quat quat float -> quat"], "FQuat::FastLerp", doc="shortest-route linear blend, not normalized")
_b("quat_from_axis_angle", ["vec3 float -> quat"], "FQuat(Axis, AngleRad)", doc="quat_from_axis_angle(axis, radians); the axis must be a unit vector")
_b("quat_from_rotation_vector", ["vec3 -> quat"], "FQuat::MakeFromRotationVector", doc="the rotation about v by |v| radians")
_b("to_orientation_quat", ["vec3 -> quat"], "FVector::ToOrientationQuat", doc="the rotation that turns +X to the vector, without roll")
_b("get_twist", ["quat vec3 -> quat"], "FQuat::ToSwingTwist (the twist)", doc="get_twist(q, axis): the part of q that rotates about the axis")
_b("twist_angle", ["quat vec3 -> float"], "FQuat::GetTwistAngle", doc="twist_angle(q, axis) in radians, [-pi, pi]")

# ---- transforms -------------------------------------------------------------------------
_b("transform_position", ["transform vec3 -> vec3"], "FTransform::TransformPosition", doc="rotate(scale * v) + location")
_b("inverse_transform_position", ["transform vec3 -> vec3"], "FTransform::InverseTransformPosition")
_b("transform_rotation", ["transform quat -> quat"], "FTransform::TransformRotation", doc="rotation * q")
_b("inverse_transform_rotation", ["transform quat -> quat"], "FTransform::InverseTransformRotation")
_b("transform_vector_no_scale", ["transform vec3 -> vec3"], "FTransform::TransformVectorNoScale")
_b("relative_transform", ["transform transform -> transform"], "FTransform::GetRelativeTransform", doc="relative_transform(a, b): a in the space of b")
_b("relative_transform_reverse", ["transform transform -> transform"], "FTransform::GetRelativeTransformReverse", doc="inverse(a) * b")
_b("inverse", ["transform -> transform"], "FTransform::Inverse", cpp="InverseTransform")
_b("compose", ["transform transform -> transform"], "FTransform::operator*", doc="compose(a, b) = a * b: apply a first, then b")
_b("with_scale", ["transform vec3 -> transform"], "FTransform::SetScale3D")

# ---- vectors, more ----------------------------------------------------------------------
_b("project_on_normal", ["vec3 vec3 -> vec3"], "FVector::ProjectOnToNormal", doc="project_on_normal(v, n): n * dot(v, n)")
_b("safe_normal_2d", ["vec3 -> vec3"], "FVector::GetSafeNormal2D", cpp="SafeNormal2D", doc="the horizontal direction (z = 0), zero for a vertical vector")
_b("size_2d", ["vec3 -> float"], "FVector::Size2D", cpp="Size2D", doc="the length of the horizontal part")

_b("blend_alpha", ["float BlendOption curve -> float"], "FAlphaBlend::AlphaToBlendOption",
   doc="blend_alpha(alpha, option, curve): the eased alpha in [0, 1]; the curve is used by BlendOption.Custom")

# ---- springs --------------------------------------------------------------------------------
_b("half_life_to_smoothing_time", ["float -> float"], "SpringMath::HalfLifeToSmoothingTime")
_b("critical_spring_damper", ["float float float float float -> spring", "vec3 vec3 vec3 float float -> spring3"], "SpringMath::CriticalSpringDamper",
   doc="critical_spring_damper(x, v, target, smoothing_time, dt) -> (value, velocity)")


# ---- constructors (lowered to IR 'Make' nodes, not calls) -----------------------------------
CONSTRUCTORS: dict[str, tuple[Sig, ...]] = {
    "vec2": (_parse_sig("float float -> vec2"),),
    "vec3": (_parse_sig("float float float -> vec3"), _parse_sig("vec2 float -> vec3")),
    "rotator": (_parse_sig("float float float -> rotator"),),
    "quat": (_parse_sig("float float float float -> quat"),),
    "transform": (_parse_sig("vec3 quat -> transform"), _parse_sig("vec3 quat vec3 -> transform")),
}


# ---- constants -----------------------------------------------------------------------------
@dataclass(frozen=True)
class BuiltinConst:
    name: str
    ty: Type
    value: object
    cpp: str


BUILTIN_CONSTS: dict[str, BuiltinConst] = {
    c.name: c
    for c in (
        BuiltinConst("PI", FLOAT, rt.PI, "UE_PI"),
        BuiltinConst("TWO_PI", FLOAT, rt.TWO_PI, "UE_TWO_PI"),
        BuiltinConst("HALF_PI", FLOAT, rt.HALF_PI, "UE_HALF_PI"),
        BuiltinConst("LN2", FLOAT, rt.LN2, "UE_LN2"),
        BuiltinConst("SMALL_NUMBER", FLOAT, rt.SMALL_NUMBER, "UE_SMALL_NUMBER"),
        BuiltinConst("KINDA_SMALL_NUMBER", FLOAT, rt.KINDA_SMALL_NUMBER, "UE_KINDA_SMALL_NUMBER"),
        BuiltinConst("VEC2_ZERO", VEC2, rt.VEC2_ZERO, "FVector2f::ZeroVector"),
        BuiltinConst("VEC3_ZERO", VEC3, rt.VEC3_ZERO, "FVector3f::ZeroVector"),
        BuiltinConst("ROTATOR_ZERO", ROTATOR, rt.ROTATOR_ZERO, "FRotator3f::ZeroRotator"),
        BuiltinConst("QUAT_IDENTITY", QUAT, rt.QUAT_IDENTITY, "FQuat4f::Identity"),
    )
}


# Type names and other words a program may not redefine.
TYPE_NAMES = frozenset({"float", "int", "bool", "vec2", "vec3", "rotator", "quat", "hit", "spring", "spring3", "transform"})


def all_reserved_names() -> frozenset[str]:
    return frozenset(BUILTINS) | frozenset(CONSTRUCTORS) | frozenset(BUILTIN_CONSTS) | TYPE_NAMES | {"MovementMode", "BlendOption", "Channel", "RootMotionMode"}


def resolve_overload(sigs: tuple[Sig, ...], arg_types: list[Type]) -> tuple[int, list[bool]] | None:
    """Pick the signature for ``arg_types``.

    Returns ``(index, widen)`` where ``widen[i]`` says argument i needs an int -> float
    conversion, or ``None`` if no signature fits.  Exact matches win over
    matches that need int -> float widening.
    """

    n = len(arg_types)
    for i, sig in enumerate(sigs):
        if len(sig.params) == n and all(p is a for p, a in zip(sig.params, arg_types)):
            return i, [False] * n
    for i, sig in enumerate(sigs):
        if len(sig.params) != n:
            continue
        widen = []
        ok = True
        for p, a in zip(sig.params, arg_types):
            if p is a:
                widen.append(False)
            elif p is FLOAT and a is INT:
                widen.append(True)
            else:
                ok = False
                break
        if ok:
            return i, widen
    return None
