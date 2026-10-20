"""The DAL type system: float, int, bool, vec2, vec3, rotator, quat and enums."""

from __future__ import annotations

import keyword


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
        return self is INT or self is FLOAT

    @property
    def is_vector(self) -> bool:
        return self is VEC2 or self is VEC3


FLOAT = Type("float")
INT = Type("int")
BOOL = Type("bool")
VEC2 = Type("vec2")
VEC3 = Type("vec3")
ROTATOR = Type("rotator")
QUAT = Type("quat")


class _ErrorType(Type):
    """Poison type used after an error so one mistake does not cascade."""

    def __init__(self):
        super().__init__("<error>")


ERROR = _ErrorType()

# v1 / v2: the result of a trace.  A value type usable in locals, function parameters and results;
# it cannot be stored (state / input / param / const) because Unreal cannot reflect it.
HIT = Type("hit")
HIT_MEMBERS = {"blocking": "bool", "point": "vec3", "normal": "vec3", "distance": "float", "time": "float"}

# v1: the type of a montage reference in a host command (compile-time only, never a value)
MONTAGE = Type("montage")

BASIC_TYPES: dict[str, Type] = {
    t.name: t for t in (FLOAT, INT, BOOL, VEC2, VEC3, ROTATOR, QUAT, HIT)
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

    @property
    def is_enum(self) -> bool:
        return True

    def index(self, member: str) -> int:
        return self.members.index(member)


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
BUILTIN_ENUMS: dict[str, EnumType] = {"MovementMode": MOVEMENT_MODE}


def py_member_name(member: str) -> str:
    """Enum member name as written in generated Python (keywords get a trailing '_')."""
    return member + "_" if keyword.iskeyword(member) else member


def zero_value(ty: Type):
    """Default value of a type as a Python runtime value (imported lazily to avoid cycles)."""
    from . import runtime as rt

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
    raise ValueError(f"no zero value for {ty}")
