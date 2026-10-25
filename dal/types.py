"""The DAL type system: float, int, bool, vec2, vec3, rotator, quat, their double-precision counterparts and enums."""

from __future__ import annotations

import keyword
from dataclasses import dataclass


class Type:
    """A DAL type.  Scalar/vector types are singletons; enums are ``EnumType``."""

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return self.name

    def __str__(self) -> str:
        return self.name

    @property
    def is_enum(self) -> bool:
        return False

    @property
    def is_numeric(self) -> bool:
        return self is INT or self is FLOAT or self is DOUBLE

    @property
    def is_vector(self) -> bool:
        return self is VEC2 or self is VEC3 or self is VEC2D or self is VEC3D

    @property
    def is_double(self) -> bool:
        """A double-precision type (double, vec2d, vec3d, rotatord, quatd, transformd)."""
        return self in DOUBLE_TO_SINGLE

    # v3 families (overridden by the subclasses below)
    is_struct = False       # a struct or settings value (named members)
    is_tags = False         # a gameplay tag value
    is_asset = False        # an opaque asset reference (sequence, montage, curve ...)
    is_container = False    # a map or an array (settings only)


FLOAT = Type("float")
INT = Type("int")
BOOL = Type("bool")
VEC2 = Type("vec2")
VEC3 = Type("vec3")
ROTATOR = Type("rotator")
QUAT = Type("quat")

# v5: the double-precision counterparts of the float types (C++: double, FVector2D, FVector, FRotator, FQuat, FTransform -- the engine's own types; the float
# types are float, FVector2f, FVector3f, FRotator3f, FQuat4f, FTransform3f).  A float type converts to its counterpart implicitly (it is exact), the other way
# round needs float(x), vec3(x) ... (it rounds).  In Python a double and a float are the same kind of value (a Python float); only the type checker and the
# float32 mode tell them apart.
DOUBLE = Type("double")
VEC2D = Type("vec2d")
VEC3D = Type("vec3d")
ROTATORD = Type("rotatord")
QUATD = Type("quatd")
TRANSFORMD = Type("transformd")

SINGLE_TO_DOUBLE: dict = {FLOAT: DOUBLE, VEC2: VEC2D, VEC3: VEC3D, ROTATOR: ROTATORD, QUAT: QUATD}
DOUBLE_TO_SINGLE: dict = {}                    # filled below, after TRANSFORM is known


def double_of(ty: "Type") -> "Type":
    """The double-precision counterpart of a float type (any other type is returned as it is)."""
    return SINGLE_TO_DOUBLE.get(ty, ty)


def single_of(ty: "Type") -> "Type":
    """The float counterpart of a double type (any other type is returned as it is)."""
    return DOUBLE_TO_SINGLE.get(ty, ty)


def other_precision(ty: "Type") -> "Type | None":
    """The same type at the other precision (float <-> double ...), None for a type that has no counterpart."""
    return SINGLE_TO_DOUBLE.get(ty) or DOUBLE_TO_SINGLE.get(ty)


def scalar_of(ty: "Type") -> "Type":
    """The type of one component of a float or double type (the scalar of its precision)."""
    return DOUBLE if ty in DOUBLE_TO_SINGLE else FLOAT


class _ErrorType(Type):
    """Poison type used after an error so one mistake does not cascade."""

    def __init__(self):
        super().__init__("<error>")


ERROR = _ErrorType()

# v1 / v2: the result of a trace.  A value type usable in locals, function parameters and results;
# it cannot be stored (state / input / param / const) because Unreal cannot reflect it.
HIT = Type("hit")
HIT_MEMBERS = {"blocking": "bool", "point": "vec3", "normal": "vec3", "distance": "float", "time": "float",
               "location": "vec3", "start_penetrating": "bool", "primitive": "primitive", "face_normal": "vec3",
               # v5: the same two positions at the precision of the engine (FHitResult is double): point_d / location_d are vec3d
               "point_d": "vec3d", "location_d": "vec3d"}

# v3: a QST transform (UE FTransform3f): location (vec3), rotation (quat), scale (vec3); a value type that can be stored
TRANSFORM = Type("transform")
TRANSFORM_MEMBERS = {"location": "vec3", "rotation": "quat", "scale": "vec3"}
TRANSFORM_MEMBERS_D = {"location": "vec3d", "rotation": "quatd", "scale": "vec3d"}      # the members of a transformd

SINGLE_TO_DOUBLE[TRANSFORM] = TRANSFORMD
DOUBLE_TO_SINGLE.update({d: s for s, d in SINGLE_TO_DOUBLE.items()})


def transform_members(ty: "Type") -> dict:
    """The members of a transform or transformd, with the type names of their values."""
    return TRANSFORM_MEMBERS_D if ty is TRANSFORMD else TRANSFORM_MEMBERS

# v3: the result of critical_spring_damper: the new value and velocity.  Like `hit`, a value type for locals, function
# parameters and results; it is not stored (copy .value and .velocity into states).
SPRING = Type("spring")
SPRING3 = Type("spring3")
SPRING_MEMBERS = {SPRING: {"value": "float", "velocity": "float"}, SPRING3: {"value": "vec3", "velocity": "vec3"}}

# v3: the type of a call that returns nothing (a method without `->`); never stored
VOID = Type("void")

# v1: the type of a montage reference in a host command (compile-time only, never a value)
MONTAGE = Type("montage")

BASIC_TYPES: dict[str, Type] = {
    t.name: t for t in (FLOAT, INT, BOOL, VEC2, VEC3, ROTATOR, QUAT, HIT, SPRING, SPRING3, TRANSFORM,
                        DOUBLE, VEC2D, VEC3D, ROTATORD, QUATD, TRANSFORMD)
}


class EnumType(Type):
    """A user enum, or the built-in ``MovementMode``."""

    def __init__(self, name: str, members: list[str], builtin: bool = False,
                 cpp_type: str = "", cpp_prop_type: str = "",
                 cpp_members: dict[str, str] | None = None):
        Type.__init__(self, name)
        self.members = list(members)
        self.builtin = builtin
        # C++ spelling: the enum type, the UPROPERTY type, and each member expression
        self.cpp_type = cpp_type or f"E{name}"
        self.cpp_prop_type = cpp_prop_type or self.cpp_type
        self.cpp_members = dict(cpp_members) if cpp_members else {
            m: f"{self.cpp_type}::{m}" for m in members
        }
        self.origin: str | None = None         # key of the library file that declares it

    @property
    def is_enum(self) -> bool:
        return True

    def index(self, member: str) -> int:
        return self.members.index(member)


class AssetType(Type):
    """An asset reference: ``sequence`` and ``montage`` can be stored anywhere, ``curve`` / ``curve3`` only in settings.
    The Python value is the asset's name (or None), the C++ value an object pointer."""

    is_asset = True

    def __init__(self, name: str, settings_only: bool = False, cpp_class: str = "", by_value: bool = False):
        Type.__init__(self, name)
        self.settings_only = settings_only
        self.cpp_class = cpp_class
        self.by_value = by_value               # the C++ value is a struct (FPoseSnapshot), not an object pointer: no comparison, only valid() and none


SEQUENCE = AssetType("sequence", False, "UAnimSequenceBase")
MONTAGE_ASSET = AssetType("montage", False, "UAnimMontage")
CURVE = AssetType("curve", True, "UCurveFloat")
CURVE3 = AssetType("curve3", True, "UCurveVector")
PRIMITIVE = AssetType("primitive", False, "UPrimitiveComponent")      # an opaque handle of a component a trace hit
# v4: a pose snapshot (FPoseSnapshot).  A value of the state or of a struct of the state, filled by host.snapshot_pose(place); the Python value is the
# dotted path of the place the host took it for (None: not taken), valid() tells.  It is never in settings, params, inputs or entry parameters.
SNAPSHOT = AssetType("snapshot", False, "FPoseSnapshot", by_value=True)
ASSET_TYPES: dict[str, AssetType] = {t.name: t for t in (SEQUENCE, MONTAGE_ASSET, CURVE, CURVE3, PRIMITIVE, SNAPSHOT)}

# the type of `none` before the context gives it one
NONE_T = Type("none")


class MapType(Type):
    """map<K, V>: a read-only table inside a settings type (keys: tags, enums, ints)."""

    is_container = True

    _interned: dict = {}

    def __init__(self, key: Type, value: Type):
        Type.__init__(self, f"map<{key.name},{value.name}>")
        self.key = key
        self.value = value

    @classmethod
    def of(cls, key: Type, value: Type) -> "MapType":
        found = cls._interned.get((id(key), id(value)))
        if found is None or found.key is not key or found.value is not value:
            found = cls._interned[(id(key), id(value))] = cls(key, value)
        return found


class ArrayType(Type):
    """array<T>: a read-only list inside a settings type."""

    is_container = True
    _interned: dict = {}

    def __init__(self, elem: Type):
        Type.__init__(self, f"array<{elem.name}>")
        self.elem = elem

    @classmethod
    def of(cls, elem: Type) -> "ArrayType":
        """The one array<elem> type (types are compared by identity)."""
        found = cls._interned.get(id(elem))
        if found is None or found.elem is not elem:
            found = cls._interned[id(elem)] = cls(elem)
        return found


@dataclass
class FieldDef:
    """A member of a struct or settings type.  ``default`` is a runtime value (never None for a stored member
    except an empty asset reference); a ``derived`` member is computed from the others (``expr``, an IR expression)."""

    name: str
    ty: "Type"
    default: object = None
    derived: bool = False
    expr: object = None
    init_expr: object = None         # a settings member built by its declaration (Inner: A = A(X: 2.0)): the expression, folded again when the defaults are rebuilt


class StructType(Type):
    """A ``struct`` (mutable value type) or a ``settings`` type (read-only data with curves, maps, assets).

    ``fields`` is filled by the checker after every type name is known; ``origin`` is the key of the library
    file that declares it (None = the file being compiled)."""

    is_struct = True

    def __init__(self, name: str, kind: str = "struct"):
        Type.__init__(self, name)
        self.kind = kind
        self.fields: list[FieldDef] = []
        self.origin: str | None = None
        self.cpp_type = "F" + name
        self.asset_root = False           # a settings type no other settings type holds: a UDataAsset in C++ (U<Name>)
        self.defaults_ready = False

    @property
    def is_settings(self) -> bool:
        return self.kind == "settings"

    @property
    def is_notify(self) -> bool:
        """The payload of a declared animation notify (`notify Name { ... }`): a value in Python, the notify object in C++."""
        return self.kind == "notify"

    def field(self, name: str) -> "FieldDef | None":
        return next((f for f in self.fields if f.name == name), None)


class TagType(Type):
    """A tag set: ``tags AlsGait = "Als.Gait" { Walking, Running }``.  Values are the full tag names (str); '' is no tag."""

    is_tags = True

    def __init__(self, name: str, prefix: str, members: list[str]):
        Type.__init__(self, name)
        self.prefix = prefix
        self.members = list(members)
        self.origin: str | None = None
        self.cpp_namespace = name + "Tags"

    def full_name(self, member: str) -> str:
        return "" if member == "None" else f"{self.prefix}.{member}"

    def member_of(self, full: str) -> str | None:
        """The declared member for a full tag name ('None' for the empty tag), or None if it is not a member."""
        if full == "":
            return "None"
        for m in self.members:
            if self.full_name(m) == full:
                return m
        return None


def make_movement_mode() -> EnumType:
    members = ["None", "Walking", "NavWalking", "Falling", "Swimming", "Flying", "Custom"]
    return EnumType(
        "MovementMode",
        members,
        builtin=True,
        cpp_type="EMovementMode",
        cpp_prop_type="TEnumAsByte<EMovementMode>",
        cpp_members={m: f"MOVE_{m}" for m in members},
    )


MOVEMENT_MODE = make_movement_mode()

# the easing options of FAlphaBlend (EAlphaBlendOption), in the engine's order
BLEND_OPTION_MEMBERS = ["Linear", "Cubic", "HermiteCubic", "Sinusoidal", "QuadraticInOut", "CubicInOut", "QuarticInOut", "QuinticInOut",
                        "CircularIn", "CircularOut", "CircularInOut", "ExpIn", "ExpOut", "ExpInOut", "Custom"]
BLEND_OPTION = EnumType("BlendOption", BLEND_OPTION_MEMBERS, builtin=True, cpp_type="EAlphaBlendOption", cpp_prop_type="EAlphaBlendOption")
# how an animation instance extracts root motion: ERootMotionMode::Type (an old-style enum in a namespace)
ROOT_MOTION_MEMBERS = ["NoRootMotionExtraction", "IgnoreRootMotion", "RootMotionFromEverything", "RootMotionFromMontagesOnly"]
ROOT_MOTION_MODE = EnumType("RootMotionMode", ROOT_MOTION_MEMBERS, builtin=True, cpp_type="ERootMotionMode::Type",
                            cpp_prop_type="TEnumAsByte<ERootMotionMode::Type>", cpp_members={m: f"ERootMotionMode::{m}" for m in ROOT_MOTION_MEMBERS})

# the collision channels of ECollisionChannel (the engine trace channels that have no name here are EngineTraceChannel1..6)
CHANNEL_MEMBERS = (["WorldStatic", "WorldDynamic", "Pawn", "Visibility", "Camera", "PhysicsBody", "Vehicle", "Destructible"]
                   + [f"EngineTraceChannel{i}" for i in range(1, 7)] + [f"GameTraceChannel{i}" for i in range(1, 19)])
CHANNEL = EnumType("Channel", CHANNEL_MEMBERS, builtin=True, cpp_type="ECollisionChannel", cpp_prop_type="TEnumAsByte<ECollisionChannel>",
                   cpp_members={m: f"ECC_{m}" for m in CHANNEL_MEMBERS})
# how a dynamic montage blends out: EMontageBlendMode (an enum class)
MONTAGE_BLEND_MODE = EnumType("MontageBlendMode", ["Standard", "Inertialization"], builtin=True, cpp_type="EMontageBlendMode", cpp_prop_type="EMontageBlendMode")
BUILTIN_ENUMS: dict[str, EnumType] = {"MovementMode": MOVEMENT_MODE, "BlendOption": BLEND_OPTION, "Channel": CHANNEL, "RootMotionMode": ROOT_MOTION_MODE,
                                      "MontageBlendMode": MONTAGE_BLEND_MODE}

# the type of a string literal: only the name of a slot in a host command (a compile-time name, never a value)
NAME_T = Type("name")
CHANNEL_SET = ArrayType.of(CHANNEL)         # `channel_set`: the channels that block a query (the same type as array<Channel>)


def py_member_name(member: str) -> str:
    """Enum member name as written in generated Python (keywords get a trailing '_')."""
    return member + "_" if keyword.iskeyword(member) else member


def zero_value(ty: Type):
    """Default value of a type as a Python runtime value (imported lazily to avoid cycles)."""
    from . import runtime as rt

    ty = DOUBLE_TO_SINGLE.get(ty, ty)          # a double has the value of its float counterpart
    if ty is FLOAT:
        return 0.0
    if ty is INT:
        return 0
    if ty is BOOL:
        return False
    if ty is VEC2:
        return rt.Vec2(0.0, 0.0)
    if ty is VEC3:
        return rt.Vec3(0.0, 0.0, 0.0)
    if ty is ROTATOR:
        return rt.Rotator(0.0, 0.0, 0.0)
    if ty is QUAT:
        return rt.Quat(0.0, 0.0, 0.0, 1.0)
    if isinstance(ty, EnumType):
        return 0
    if ty is HIT:
        return rt.Hit(False, rt.Vec3(0.0, 0.0, 0.0), rt.Vec3(0.0, 0.0, 0.0), 0.0, 1.0)
    if ty is TRANSFORM:
        return rt.Transform()
    if ty is SPRING:
        return rt.Spring(0.0, 0.0)
    if ty is SPRING3:
        return rt.Spring3(rt.Vec3(0.0, 0.0, 0.0), rt.Vec3(0.0, 0.0, 0.0))
    if isinstance(ty, TagType):
        return ""
    if isinstance(ty, AssetType):
        return None
    if isinstance(ty, MapType):
        return {}
    if isinstance(ty, ArrayType):
        return ()
    if isinstance(ty, StructType):
        from . import values

        return values.default_of(ty)
    raise ValueError(f"no zero value for {ty}")
