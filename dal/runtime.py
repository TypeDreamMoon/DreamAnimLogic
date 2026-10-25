"""DAL runtime for the Python backend and the reference interpreter.

Value types (``Vec2``, ``Vec3``, ``Rotator``, ``Quat``) and the math library with
Unreal Engine semantics.  Every function here is a re-implementation of the
behaviour of the UE function named in its comment (UE 5.8 ``FMath``,
``FRotator``, ``FQuat``, ``TVector``); no UE code is copied.

Differences from a UE float build that are inherent to Python:

* values are IEEE doubles, not floats (the C++ backend uses ``float``).  The float32 mode of the interpreter and of the generated Python
  rounds every ``float`` result to float32 (``f32`` below) and removes this difference for the arithmetic; the ``double`` types are Python
  floats in both modes;
* ``atan2`` uses the C library; UE's float ``FMath::Atan2`` is a minimax
  approximation with a relative error below 1e-6 against it;
* ``to_rotator`` computes pitch with an exact ``asin``; UE's float
  ``FQuat::Rotator`` uses ``FMath::FastAsin`` (error up to about 7e-5 rad).

Pure Python 3.11+, no dependencies.
"""

from __future__ import annotations

import math
import os as _os
import struct as _struct
from enum import IntEnum  # noqa: F401  (re-exported for generated code)

# ---- constants (UE macro values) --------------------------------------------

PI = math.pi
TWO_PI = 2.0 * math.pi
HALF_PI = 0.5 * math.pi
LN2 = math.log(2.0)
SMALL_NUMBER = 1.0e-8
KINDA_SMALL_NUMBER = 1.0e-4

_INT_MIN = -(2**31)
_INT_MAX = 2**31 - 1
_INF = math.inf
_NAN = math.nan


def i32(x: int) -> int:
    """Wrap to a signed 32-bit integer (two's complement)."""
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x & 0x80000000 else x


# ---- value types -------------------------------------------------------------

class Vec2(tuple):
    """2D float vector (UE ``FVector2f``); immutable."""

    __slots__ = ()

    def __new__(cls, x=0.0, y=0.0):
        return tuple.__new__(cls, (float(x), float(y)))

    x = property(lambda s: s[0])
    y = property(lambda s: s[1])

    def __add__(self, o):
        return Vec2(self[0] + o[0], self[1] + o[1])

    def __sub__(self, o):
        return Vec2(self[0] - o[0], self[1] - o[1])

    def __neg__(self):
        return Vec2(-self[0], -self[1])

    def __mul__(self, o):
        if isinstance(o, (int, float)):
            return Vec2(self[0] * o, self[1] * o)
        return Vec2(self[0] * o[0], self[1] * o[1])

    def __rmul__(self, o):
        return Vec2(o * self[0], o * self[1])

    def __truediv__(self, s):
        if s == 0:
            return Vec2(0.0, 0.0)
        return Vec2(self[0] / s, self[1] / s)

    def __repr__(self):
        return f"Vec2({self[0]!r}, {self[1]!r})"


class Vec3(tuple):
    """3D float vector (UE ``FVector3f``); immutable."""

    __slots__ = ()

    def __new__(cls, x=0.0, y=0.0, z=0.0):
        return tuple.__new__(cls, (float(x), float(y), float(z)))

    x = property(lambda s: s[0])
    y = property(lambda s: s[1])
    z = property(lambda s: s[2])

    def __add__(self, o):
        return Vec3(self[0] + o[0], self[1] + o[1], self[2] + o[2])

    def __sub__(self, o):
        return Vec3(self[0] - o[0], self[1] - o[1], self[2] - o[2])

    def __neg__(self):
        return Vec3(-self[0], -self[1], -self[2])

    def __mul__(self, o):
        if isinstance(o, (int, float)):
            return Vec3(self[0] * o, self[1] * o, self[2] * o)
        return Vec3(self[0] * o[0], self[1] * o[1], self[2] * o[2])

    def __rmul__(self, o):
        return Vec3(o * self[0], o * self[1], o * self[2])

    def __truediv__(self, s):
        if s == 0:
            return Vec3(0.0, 0.0, 0.0)
        return Vec3(self[0] / s, self[1] / s, self[2] / s)

    def __repr__(self):
        return f"Vec3({self[0]!r}, {self[1]!r}, {self[2]!r})"


class Rotator(tuple):
    """Euler rotation in degrees (UE ``FRotator3f``): pitch, yaw, roll."""

    __slots__ = ()

    def __new__(cls, pitch=0.0, yaw=0.0, roll=0.0):
        return tuple.__new__(cls, (float(pitch), float(yaw), float(roll)))

    pitch = property(lambda s: s[0])
    yaw = property(lambda s: s[1])
    roll = property(lambda s: s[2])

    # FRotator arithmetic is component-wise and does not normalise: the results are the sums and differences of the angles
    def __add__(self, o):
        return Rotator(self[0] + o[0], self[1] + o[1], self[2] + o[2])

    def __sub__(self, o):
        return Rotator(self[0] - o[0], self[1] - o[1], self[2] - o[2])

    def __mul__(self, s):
        return Rotator(self[0] * s, self[1] * s, self[2] * s)

    __rmul__ = __mul__

    def __repr__(self):
        return f"Rotator({self[0]!r}, {self[1]!r}, {self[2]!r})"


class Quat(tuple):
    """Quaternion (UE ``FQuat4f``): x, y, z, w.  ``a * b`` composes like UE (b first, then a)."""

    __slots__ = ()

    def __new__(cls, x=0.0, y=0.0, z=0.0, w=1.0):
        return tuple.__new__(cls, (float(x), float(y), float(z), float(w)))

    x = property(lambda s: s[0])
    y = property(lambda s: s[1])
    z = property(lambda s: s[2])
    w = property(lambda s: s[3])

    def __mul__(self, o):
        return quat_mul(self, o)

    def __repr__(self):
        return f"Quat({self[0]!r}, {self[1]!r}, {self[2]!r}, {self[3]!r})"


class Hit(tuple):
    """Result of a host trace (UE ``FHitResult``, reduced): blocking, point (ImpactPoint), normal (ImpactNormal), distance, time
    (0..1 along the trace), location (the swept shape's center at the hit), start_penetrating, primitive (the handle of the
    component that was hit: a name here, None for a miss) and face_normal (FHitResult::Normal)."""

    __slots__ = ()

    def __new__(cls, blocking=False, point=(0.0, 0.0, 0.0), normal=(0.0, 0.0, 0.0), distance=0.0, time=1.0,
                location=None, start_penetrating=False, primitive=None, face_normal=None, point_d=None, location_d=None):
        point, normal = as_vec3(point), as_vec3(normal)
        location = point if location is None else as_vec3(location)
        return tuple.__new__(cls, (bool(blocking), point, normal, float(distance), float(time),
                                   location, bool(start_penetrating),
                                   None if primitive is None else primitive, normal if face_normal is None else as_vec3(face_normal),
                                   point if point_d is None else as_vec3(point_d), location if location_d is None else as_vec3(location_d)))

    blocking = property(lambda s: s[0])
    point = property(lambda s: s[1])
    normal = property(lambda s: s[2])
    distance = property(lambda s: s[3])
    time = property(lambda s: s[4])
    location = property(lambda s: s[5])
    start_penetrating = property(lambda s: s[6])
    primitive = property(lambda s: s[7])
    face_normal = property(lambda s: s[8])
    point_d = property(lambda s: s[9])             # point and location at the precision the host gave them (the engine's are doubles)
    location_d = property(lambda s: s[10])

    def __repr__(self):
        return (f"Hit(blocking={self[0]!r}, point={self[1]!r}, normal={self[2]!r}, distance={self[3]!r}, time={self[4]!r}, "
                f"location={self[5]!r}, start_penetrating={self[6]!r}, primitive={self[7]!r}, face_normal={self[8]!r})")


class Transform(tuple):
    """A QST transform (UE ``FTransform3f``): location, rotation, scale.  A point is mapped by ``rotate(scale * p) + location``;
    ``compose(a, b)`` applies a first, then b."""

    __slots__ = ()

    def __new__(cls, location=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0)):
        return tuple.__new__(cls, (as_vec3(location), as_quat(rotation), as_vec3(scale)))

    location = property(lambda s: s[0])
    rotation = property(lambda s: s[1])
    scale = property(lambda s: s[2])

    def __repr__(self):
        return f"Transform({self[0]!r}, {self[1]!r}, {self[2]!r})"


class Spring(tuple):
    """The result of critical_spring_damper on a float: value, velocity."""

    __slots__ = ()

    def __new__(cls, value=0.0, velocity=0.0):
        return tuple.__new__(cls, (float(value), float(velocity)))

    value = property(lambda s: s[0])
    velocity = property(lambda s: s[1])


class Spring3(tuple):
    """The result of critical_spring_damper on a vec3: value, velocity."""

    __slots__ = ()

    def __new__(cls, value=(0.0, 0.0, 0.0), velocity=(0.0, 0.0, 0.0)):
        return tuple.__new__(cls, (as_vec3(value), as_vec3(velocity)))

    value = property(lambda s: s[0])
    velocity = property(lambda s: s[1])


VEC2_ZERO = Vec2(0.0, 0.0)
VEC3_ZERO = Vec3(0.0, 0.0, 0.0)
ROTATOR_ZERO = Rotator(0.0, 0.0, 0.0)
QUAT_IDENTITY = Quat(0.0, 0.0, 0.0, 1.0)


class MovementMode(IntEnum):
    """Host ``movement_mode`` values; same order as UE ``EMovementMode``."""

    None_ = 0
    Walking = 1
    NavWalking = 2
    Falling = 3
    Swimming = 4
    Flying = 5
    Custom = 6


class RootMotionMode(IntEnum):
    """``ERootMotionMode``: how an animation instance extracts root motion."""

    NoRootMotionExtraction = 0
    IgnoreRootMotion = 1
    RootMotionFromEverything = 2
    RootMotionFromMontagesOnly = 3


class Channel(IntEnum):
    """The collision channels of queries; same order as UE ``ECollisionChannel`` (the engine trace channels count up from 8)."""

    WorldStatic = 0
    WorldDynamic = 1
    Pawn = 2
    Visibility = 3
    Camera = 4
    PhysicsBody = 5
    Vehicle = 6
    Destructible = 7
    EngineTraceChannel1 = 8
    EngineTraceChannel2 = 9
    EngineTraceChannel3 = 10
    EngineTraceChannel4 = 11
    EngineTraceChannel5 = 12
    EngineTraceChannel6 = 13
    GameTraceChannel1 = 14
    GameTraceChannel2 = 15
    GameTraceChannel3 = 16
    GameTraceChannel4 = 17
    GameTraceChannel5 = 18
    GameTraceChannel6 = 19
    GameTraceChannel7 = 20
    GameTraceChannel8 = 21
    GameTraceChannel9 = 22
    GameTraceChannel10 = 23
    GameTraceChannel11 = 24
    GameTraceChannel12 = 25
    GameTraceChannel13 = 26
    GameTraceChannel14 = 27
    GameTraceChannel15 = 28
    GameTraceChannel16 = 29
    GameTraceChannel17 = 30
    GameTraceChannel18 = 31


class MontageBlendMode(IntEnum):
    """How a dynamic montage blends out; same order as UE ``EMontageBlendMode``."""

    Standard = 0
    Inertialization = 1


class BlendOption(IntEnum):
    """The easing options of ``blend_alpha``; same order as UE ``EAlphaBlendOption``."""

    Linear = 0
    Cubic = 1
    HermiteCubic = 2
    Sinusoidal = 3
    QuadraticInOut = 4
    CubicInOut = 5
    QuarticInOut = 6
    QuinticInOut = 7
    CircularIn = 8
    CircularOut = 9
    CircularInOut = 10
    ExpIn = 11
    ExpOut = 12
    ExpInOut = 13
    Custom = 14


# ---- float32 ------------------------------------------------------------------
#
# In float32 mode (``float32=True`` for the interpreter and the Python backend) every value of a ``float`` type -- the scalar and the components of
# vec2, vec3, rotator, quat and transform -- is a float32 value held in a Python float, and every operation that makes a new one rounds its result
# (round to nearest even, what ``float`` arithmetic does in C++): ``f32(a + b)`` is exactly ``a + b`` in C++ ``float``, because a double holds the exact
# sum, difference, product and quotient of two floats to 53 bits and the one rounding to 24 bits is then the correctly rounded float result.
# The library functions compute in double and round their result once; where C++ computes the same function in float the two can differ by an ulp.

# Whether the interpreter and the Python backend run in float32 mode when the caller does not say (``float32=None``).  DAL_FLOAT32=1 / 0 in the environment sets it.
FLOAT32_DEFAULT = _os.environ.get("DAL_FLOAT32", "0") not in ("0", "")


def want_float32(float32) -> bool:
    """The float32 flag a caller passed (None: the default of the process)."""
    return FLOAT32_DEFAULT if float32 is None else bool(float32)


_FLOAT_PACK = _struct.Struct("f")
_pack_f32 = _FLOAT_PACK.pack
_unpack_f32 = _FLOAT_PACK.unpack
_tuple_new = tuple.__new__


def f32(x):
    """``x`` rounded to the nearest float32 (ties to even) as a Python float; too large a magnitude is +-inf, NaN stays NaN."""
    try:
        return _unpack_f32(_pack_f32(x))[0]
    except OverflowError:
        return _INF if x > 0 else -_INF


def f32_vec2(v):
    return _tuple_new(Vec2, (f32(v[0]), f32(v[1])))


def f32_vec3(v):
    return _tuple_new(Vec3, (f32(v[0]), f32(v[1]), f32(v[2])))


def f32_rotator(v):
    return _tuple_new(Rotator, (f32(v[0]), f32(v[1]), f32(v[2])))


def f32_quat(v):
    return _tuple_new(Quat, (f32(v[0]), f32(v[1]), f32(v[2]), f32(v[3])))


def f32_transform(t):
    return _tuple_new(Transform, (f32_vec3(t[0]), f32_quat(t[1]), f32_vec3(t[2])))


def f32_spring(s):
    return _tuple_new(Spring, (f32(s[0]), f32(s[1])))


def f32_spring3(s):
    return _tuple_new(Spring3, (f32_vec3(s[0]), f32_vec3(s[1])))


def f32_hit(h):
    """A hit with its float members rounded; ``point_d`` and ``location_d`` keep the precision the host gave."""
    return _tuple_new(Hit, (h[0], f32_vec3(h[1]), f32_vec3(h[2]), f32(h[3]), f32(h[4]), f32_vec3(h[5]), h[6], h[7], f32_vec3(h[8]), h[9], h[10]))


def as_hit32(v):
    return f32_hit(as_hit(v))


def round32_any(v):
    """The float32 value of a number or of a (possibly nested) vector / rotator / quat / transform value: the literal folding of the checker uses it."""
    if isinstance(v, float):
        return f32(v)
    if isinstance(v, tuple) and not isinstance(v, (Hit, Spring, Spring3)):
        return type(v)(*[round32_any(c) for c in v])
    return v


# Coercions used by the generated ``_sample`` so hosts may return plain tuples.
def as_float(v):
    return float(v)


def as_bool(v):
    return bool(v)


def as_vec2(v):
    return v if type(v) is Vec2 else Vec2(*v)


def as_vec3(v):
    return v if type(v) is Vec3 else Vec3(*v)


def as_rotator(v):
    return v if type(v) is Rotator else Rotator(*v)


def as_quat(v):
    return v if type(v) is Quat else Quat(*v)


def as_hit(v):
    return v if type(v) is Hit else Hit(*v)


def as_transform(v):
    return v if type(v) is Transform else Transform(*v)


def as_spring(v):
    return v if type(v) is Spring else Spring(*v)


def as_spring3(v):
    return v if type(v) is Spring3 else Spring3(*v)


def path_get(v, path):
    """The member chain ``path`` of a value (``("Left", "Axis")``): how an input reads inside a peer's struct."""
    for member in path:
        v = getattr(v, member)
    return v


def curve_eval(curve, x):
    """curve.eval(x): 0 for a curve that is not set."""
    return 0.0 if curve is None else curve.eval(x)


def curve3_eval(curve, x):
    return VEC3_ZERO if curve is None else curve.eval(x)


_MISSING = object()


def map_get(table, key, default):
    """table[key]: a missing key gives the default (called when it is a factory)."""
    v = table.get(key, _MISSING)
    if v is _MISSING:
        return default() if callable(default) else default
    return v


def array_get(items, index, default):
    """items[index]: out of range gives the default."""
    if 0 <= index < len(items):
        return items[index]
    return default() if callable(default) else default


def as_tag(v):
    return "" if v is None else str(v)


def as_asset(v):
    return v


def as_enum(cls, v):
    """A member of the IntEnum ``cls`` from a member, an index, or a member name (also 'EType::Member' / 'Type.Member')."""
    if isinstance(v, cls):
        return v
    if isinstance(v, str):
        name = v.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
        for m in cls:
            if m.name == name or m.name.rstrip("_") == name:
                return m
        raise ValueError(f"'{v}' is not a member of {cls.__name__} ({', '.join(m.name.rstrip('_') for m in cls)})")
    return cls(int(v))


# ---- arithmetic with defined edge cases ---------------------------------------

def fdiv(a, b):
    """float division; ``x / 0`` is 0 (never inf/NaN from the operator)."""
    if b == 0:
        return 0.0
    return float(a) / float(b)


def idiv(a: int, b: int) -> int:
    """int division truncating toward zero (C++); ``x / 0`` is 0."""
    if b == 0:
        return 0
    q = abs(a) // abs(b)
    return i32(-q if (a < 0) != (b < 0) else q)


def imod(a: int, b: int) -> int:
    """int remainder with the sign of the dividend (C++); ``x % 0`` is 0."""
    if b == 0:
        return 0
    r = abs(a) % abs(b)
    return -r if a < 0 else r


def vdiv(v, s):
    """vector / scalar; ``v / 0`` is the zero vector."""
    return v / s


def vdiv_recip(v, s):
    """vector / scalar the way the engine does it (TVector::operator/): each component times the reciprocal of the scalar, in double; ``v / 0`` is the zero vector.
    The double types use it always (their results agree with C++ to the last bit); the float types in the float32 mode (``vdiv_recip32``)."""
    cls = type(v)
    if s == 0:
        return cls(*([0.0] * len(v)))
    r = 1.0 / s
    return cls(*[c * r for c in v])


def vdiv_recip32(v, s):
    """``vdiv_recip`` in float32: the reciprocal and every product are rounded to float32."""
    cls = type(v)
    if s == 0:
        return cls(*([0.0] * len(v)))
    r = f32(1.0 / s)
    return _tuple_new(cls, tuple([f32(c * r) for c in v]))


# ---- scalar math --------------------------------------------------------------

def abs_(x):  # FMath::Abs (int32 results wrap, as in C++)
    if x < 0:
        return i32(-x) if isinstance(x, int) else -x
    return x


def sign(x):  # FMath::Sign
    r = 1 if x > 0 else (-1 if x < 0 else 0)
    return float(r) if isinstance(x, float) else r


def min_(a, b):  # FMath::Min: (A < B) ? A : B
    return a if a < b else b


def max_(a, b):  # FMath::Max: (B < A) ? A : B
    return a if b < a else b


def clamp(x, lo, hi):  # FMath::Clamp = Max(Min(X, Max), Min)
    return max_(min_(x, hi), lo)


def saturate(x):
    return clamp(x, 0.0, 1.0)


def square(x):  # FMath::Square
    return i32(x * x) if isinstance(x, int) else x * x


def lerp(a, b, t):  # FMath::Lerp: A + Alpha * (B - A), unclamped
    if isinstance(a, float):
        return a + t * (b - a)
    return a + (b - a) * t


def _finite(x: float) -> bool:
    return not (math.isinf(x) or math.isnan(x))


def floor_(x):  # FMath::FloorToFloat
    return float(math.floor(x)) if _finite(x) else x


def ceil_(x):  # FMath::CeilToFloat
    return float(math.ceil(x)) if _finite(x) else x


def trunc_(x):  # FMath::TruncToFloat
    return float(math.trunc(x)) if _finite(x) else x


def round_(x):  # FMath::RoundToFloat: FloorToFloat(F + 0.5)
    return floor_(x + 0.5)


def frac(x):  # FMath::Frac: Value - FloorToFloat(Value)
    return x - floor_(x)


def _sat_int(x: float) -> int:
    if math.isnan(x):
        return 0
    if x >= 2147483647.0:
        return _INT_MAX
    if x <= -2147483648.0:
        return _INT_MIN
    return int(x)


def to_int(x):  # FMath::TruncToInt (saturating, NaN -> 0)
    return _sat_int(math.trunc(x)) if _finite(x) else _sat_int(x)


def floor_to_int(x):  # FMath::FloorToInt
    return _sat_int(math.floor(x)) if _finite(x) else _sat_int(x)


def ceil_to_int(x):  # FMath::CeilToInt
    return _sat_int(math.ceil(x)) if _finite(x) else _sat_int(x)


def round_to_int(x):  # FMath::RoundToInt: FloorToInt(F + 0.5)
    return floor_to_int(x + 0.5)


def fmod(x, y):  # FMath::Fmod: |Y| <= SMALL_NUMBER gives 0
    if abs(y) <= SMALL_NUMBER:
        return 0.0
    if not _finite(x) or math.isnan(y):
        return _NAN
    return math.fmod(x, y)


def sqrt(x):  # FMath::Sqrt (IEEE: negative gives NaN)
    if x >= 0:
        return math.sqrt(x)
    return x if math.isnan(x) else _NAN


def inv_sqrt(x):  # FMath::InvSqrt: 1 / sqrt(x)
    if x > 0:
        return 1.0 / math.sqrt(x)
    if x == 0:
        return _INF
    return x if math.isnan(x) else _NAN


def pow_(a, b):  # FMath::Pow (powf)
    try:
        return math.pow(a, b)
    except OverflowError:
        return _INF
    except ValueError:
        if a == 0 and b < 0:
            return _INF
        return _NAN


def exp(x):  # FMath::Exp
    try:
        return math.exp(x)
    except OverflowError:
        return _INF


def log(x):  # FMath::Loge
    if x > 0:
        return math.log(x) if _finite(x) else x
    if x == 0:
        return -_INF
    return x if math.isnan(x) else _NAN


def sin(x):  # FMath::Sin
    return math.sin(x) if _finite(x) else _NAN


def cos(x):  # FMath::Cos
    return math.cos(x) if _finite(x) else _NAN


def tan(x):  # FMath::Tan
    return math.tan(x) if _finite(x) else _NAN


def _clamp_unit(v):
    # UE: (Value<-1) ? -1 : ((Value<1) ? Value : 1)   (NaN falls through to 1)
    return -1.0 if v < -1.0 else (v if v < 1.0 else 1.0)


def asin(x):  # FMath::Asin clamps its argument to [-1, 1]
    return math.asin(_clamp_unit(x))


def acos(x):  # FMath::Acos clamps its argument to [-1, 1]
    return math.acos(_clamp_unit(x))


def atan(x):  # FMath::Atan
    return math.atan(x)


def atan2(y, x):  # FMath::Atan2 (C library here; see module docstring)
    return math.atan2(y, x)


_DEG_TO_RAD = math.pi / 180.0
_RAD_TO_DEG = 180.0 / math.pi


def deg_to_rad(x):  # FMath::DegreesToRadians
    return x * _DEG_TO_RAD


def rad_to_deg(x):  # FMath::RadiansToDegrees
    return x * _RAD_TO_DEG


def inv_exp_approx(x):
    """FMath::InvExpApprox: rational approximation of exp(-x) (coefficients from UE 5.8)."""
    a = 1.00746054
    b = 0.45053901
    c = 0.25724632
    d = 1.0 + a * x + b * x * x + c * x * x * x
    if d == 0:
        return _INF
    return 1.0 / d


def is_nearly_zero(x, tol=SMALL_NUMBER):  # FMath::IsNearlyZero
    return abs_(x) <= tol


def is_nearly_equal(a, b, tol=SMALL_NUMBER):  # FMath::IsNearlyEqual
    return abs_(a - b) <= tol


# ---- angles ------------------------------------------------------------------

def unwind_degrees(a):
    """FMath::UnwindDegrees: bring an angle into [-180, 180] by repeated +-360.

    Magnitudes above 1e9 are first reduced with fmod (the UE loop would not
    terminate for them); results are identical for ordinary angles."""
    if not _finite(a):
        return a
    if abs(a) > 1.0e9:
        a = math.fmod(a, 360.0)
    while a > 180.0:
        a -= 360.0
    while a < -180.0:
        a += 360.0
    return a


def clamp_axis(a):
    """FRotator::ClampAxis: [0, 360)."""
    a = fmod(a, 360.0)
    if a < 0.0:
        a += 360.0
    return a


def normalize_axis(a):
    """FRotator::NormalizeAxis: (-180, 180]."""
    a = clamp_axis(a)
    if a > 180.0:
        a -= 360.0
    return a


def _wrap(x, lo, hi):  # FMath::Wrap
    size = hi - lo
    if size == 0:
        return hi
    end = x
    if end < lo:
        mod = fmod(lo - end, size)
        end = (hi - mod) if mod != 0.0 else lo
    elif end > hi:
        mod = fmod(end - hi, size)
        end = (lo + mod) if mod != 0.0 else hi
    return end


def delta_angle(a1, a2):
    """FMath::FindDeltaAngleDegrees: shortest signed angle from a1 to a2."""
    return _wrap(a2 - a1, -180.0, 180.0)


def clamp_angle(angle, lo, hi):
    """FMath::ClampAngle: clamp an angle to the arc from lo to hi (degrees), result in (-180, 180]."""
    max_delta = clamp_axis(hi - lo) * 0.5
    center = clamp_axis(lo + max_delta)
    delta = normalize_axis(angle - center)
    if delta > max_delta:
        return normalize_axis(center + max_delta)
    if delta < -max_delta:
        return normalize_axis(center - max_delta)
    return normalize_axis(angle)


def range_pct(lo, hi, x):
    """FMath::GetRangePct."""
    divisor = hi - lo
    if is_nearly_zero(divisor):
        return 1.0 if x >= hi else 0.0
    return (x - lo) / divisor


def map_range_clamped(in_lo, in_hi, out_lo, out_hi, x):
    """FMath::GetMappedRangeValueClamped."""
    pct = clamp(range_pct(in_lo, in_hi, x), 0.0, 1.0)
    return lerp(out_lo, out_hi, pct)


def map_range(in_lo, in_hi, out_lo, out_hi, x):
    """FMath::GetMappedRangeValueUnclamped."""
    return lerp(out_lo, out_hi, range_pct(in_lo, in_hi, x))


# ---- vectors -----------------------------------------------------------------

def length_sq(v):
    if len(v) == 2:
        return v[0] * v[0] + v[1] * v[1]
    return v[0] * v[0] + v[1] * v[1] + v[2] * v[2]


def length(v):  # FVector::Size
    return sqrt(length_sq(v))


def dot(a, b):
    if len(a) == 2:
        return a[0] * b[0] + a[1] * b[1]
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):  # FVector::CrossProduct
    return Vec3(
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def distance(a, b):  # FVector::Dist
    return length(a - b)


def normalize(v):
    """vec3: TVector::GetSafeNormal; vec2: TVector2::GetSafeNormal; quat: GetNormalized."""
    if type(v) is Quat:
        return quat_normalize(v)
    if len(v) == 2:
        sq = v[0] * v[0] + v[1] * v[1]
        if sq > SMALL_NUMBER:
            s = inv_sqrt(sq)
            return Vec2(v[0] * s, v[1] * s)
        return VEC2_ZERO
    sq = v[0] * v[0] + v[1] * v[1] + v[2] * v[2]
    if sq == 1.0:
        return v
    if sq < SMALL_NUMBER:
        return VEC3_ZERO
    s = inv_sqrt(sq)
    return Vec3(v[0] * s, v[1] * s, v[2] * s)


def clamp_length(v, max_size):
    """TVector::GetClampedToMaxSize."""
    zero = VEC2_ZERO if len(v) == 2 else VEC3_ZERO
    if max_size < KINDA_SMALL_NUMBER:
        return zero
    vsq = length_sq(v)
    if vsq > max_size * max_size:
        return v * (max_size * inv_sqrt(vsq))
    return v


def vec2(x, y):
    return Vec2(x, y)


def vec3(x, y, z):
    return Vec3(x, y, z)


def vec3_from_vec2(v, z):
    return Vec3(v[0], v[1], z)


def rotator(pitch, yaw, roll):
    return Rotator(pitch, yaw, roll)


def quat(x, y, z, w):
    return Quat(x, y, z, w)


# ---- rotations ---------------------------------------------------------------

def _half_angle(deg):
    # UE: Fmod(angle, 360) (no winding), then half of it in radians
    return (math.fmod(deg, 360.0) if _finite(deg) else _NAN) * math.pi / 360.0


def to_quat(r):
    """FRotator::Quaternion (UE axis conventions; angles are not wound)."""
    ha = _half_angle(r[0])
    hb = _half_angle(r[1])
    hc = _half_angle(r[2])
    sp, cp = math.sin(ha) if _finite(ha) else _NAN, math.cos(ha) if _finite(ha) else _NAN
    sy, cy = math.sin(hb) if _finite(hb) else _NAN, math.cos(hb) if _finite(hb) else _NAN
    sr, cr = math.sin(hc) if _finite(hc) else _NAN, math.cos(hc) if _finite(hc) else _NAN
    return Quat(
        cr * sp * sy - sr * cp * cy,
        -cr * sp * cy - sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def to_rotator(q):
    """FQuat::Rotator (singularity handling as in UE; exact asin, see module docstring)."""
    x, y, z, w = q
    singularity = z * x - w * y
    yaw_y = 2.0 * (w * z + x * y)
    yaw_x = 1.0 - 2.0 * (y * y + z * z)
    threshold = 0.4999995
    if singularity < -threshold:
        return Rotator(-90.0, normalize_axis(-2.0 * math.atan2(x, w) * _RAD_TO_DEG), 0.0)
    if singularity > threshold:
        return Rotator(90.0, normalize_axis(2.0 * math.atan2(x, w) * _RAD_TO_DEG), 0.0)
    pitch = math.asin(_clamp_unit(2.0 * singularity)) * _RAD_TO_DEG
    yaw = math.atan2(yaw_y, yaw_x) * _RAD_TO_DEG
    roll = math.atan2(-2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y)) * _RAD_TO_DEG
    return Rotator(pitch, yaw, roll)


def quat_mul(a, b):
    """FQuat::operator* (Hamilton product; the result applies b first, then a)."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return Quat(
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_inverse(q):
    """FQuat::Inverse (valid for normalized quaternions)."""
    return Quat(-q[0], -q[1], -q[2], q[3])


def quat_normalize(q, tol=SMALL_NUMBER):
    """FQuat::GetNormalized: identity when the quaternion is too small."""
    sq = q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]
    if sq >= tol:
        s = inv_sqrt(sq)
        return Quat(q[0] * s, q[1] * s, q[2] * s, q[3] * s)
    return QUAT_IDENTITY


def quat_equals(a, b, tol=KINDA_SMALL_NUMBER):
    """FQuat::Equals (either sign)."""
    sub = all(abs(a[i] - b[i]) <= tol for i in range(4))
    add = all(abs(a[i] + b[i]) <= tol for i in range(4))
    return sub or add


def quat_angular_distance(a, b):
    """FQuat::AngularDistance."""
    inner = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]
    return acos(2.0 * inner * inner - 1.0)


def slerp(a, b, t):
    """FQuat::Slerp: shortest path, normalized result."""
    cosom = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]
    sgn = 1.0 if cosom >= 0.0 else -1.0
    cosom *= sgn
    scale0 = 1.0 - t
    scale1 = t * sgn
    if cosom < 0.9999:
        omega = acos(cosom)
        inv_sin = 1.0 / math.sin(omega)
        scale0 = math.sin(scale0 * omega) * inv_sin
        scale1 = math.sin(scale1 * omega) * inv_sin
    return quat_normalize(
        Quat(
            scale0 * a[0] + scale1 * b[0],
            scale0 * a[1] + scale1 * b[1],
            scale0 * a[2] + scale1 * b[2],
            scale0 * a[3] + scale1 * b[3],
        )
    )


def _rotate_q(q, v, inverse: bool):
    qx, qy, qz = (-q[0], -q[1], -q[2]) if inverse else (q[0], q[1], q[2])
    w = q[3]
    # T = 2 (Q x V);  V' = V + w*T + (Q x T)
    tx = 2.0 * (qy * v[2] - qz * v[1])
    ty = 2.0 * (qz * v[0] - qx * v[2])
    tz = 2.0 * (qx * v[1] - qy * v[0])
    return Vec3(
        v[0] + w * tx + (qy * tz - qz * ty),
        v[1] + w * ty + (qz * tx - qx * tz),
        v[2] + w * tz + (qx * ty - qy * tx),
    )


def rotate(rot, v):
    """FQuat::RotateVector / FRotator::RotateVector."""
    q = rot if type(rot) is Quat else to_quat(rot)
    return _rotate_q(q, v, False)


def unrotate(rot, v):
    """FQuat::UnrotateVector / FRotator::UnrotateVector."""
    q = rot if type(rot) is Quat else to_quat(rot)
    return _rotate_q(q, v, True)


def look_at(src, dst):
    """(To - From).ToOrientationRotator(): yaw and pitch that face ``dst`` from ``src`` (roll 0)."""
    dx, dy, dz = dst[0] - src[0], dst[1] - src[1], dst[2] - src[2]
    yaw = math.atan2(dy, dx) * _RAD_TO_DEG
    pitch = math.atan2(dz, sqrt(dx * dx + dy * dy)) * _RAD_TO_DEG
    return Rotator(pitch, yaw, 0.0)


def angle_between(a, b):
    """Angle in degrees between two vectors (UE: Acos(Dot(A.GetSafeNormal(), B.GetSafeNormal()))), 0 if either is zero."""
    na, nb = normalize(a), normalize(b)
    if na == VEC3_ZERO or nb == VEC3_ZERO:
        return 0.0
    return acos(dot(na, nb)) * _RAD_TO_DEG


def normalize_rotator(r):
    """FRotator::GetNormalized: every axis through NormalizeAxis."""
    return Rotator(normalize_axis(r[0]), normalize_axis(r[1]), normalize_axis(r[2]))


def delta_rotator(a, b):
    """(A - B).GetNormalized(), as UKismetMathLibrary::NormalizedDeltaRotator."""
    return normalize_rotator(Rotator(a[0] - b[0], a[1] - b[1], a[2] - b[2]))


def _rot_nearly_zero(r, tol=KINDA_SMALL_NUMBER):
    return abs(normalize_axis(r[0])) <= tol and abs(normalize_axis(r[1])) <= tol and abs(normalize_axis(r[2])) <= tol


# ---- interpolation -----------------------------------------------------------

def interp_to(cur, target, dt, speed):
    """FInterpTo / Vector2DInterpTo / VInterpTo / RInterpTo / QInterpTo (by value type)."""
    if isinstance(cur, float):
        if speed <= 0.0:
            return target
        dist = target - cur
        if dist * dist < SMALL_NUMBER:
            return target
        return cur + dist * clamp(dt * speed, 0.0, 1.0)
    t = type(cur)
    if t is Vec2 or t is Vec3:
        if speed <= 0.0:
            return target
        dist = target - cur
        if length_sq(dist) < KINDA_SMALL_NUMBER:
            return target
        return cur + dist * clamp(dt * speed, 0.0, 1.0)
    if t is Rotator:
        if dt == 0.0 or cur == target:
            return cur
        if speed <= 0.0:
            return target
        step = speed * dt
        delta = delta_rotator(target, cur)
        if _rot_nearly_zero(delta):
            return target
        k = clamp(step, 0.0, 1.0)
        moved = Rotator(cur[0] + delta[0] * k, cur[1] + delta[1] * k, cur[2] + delta[2] * k)
        return normalize_rotator(moved)
    if t is Quat:
        if speed <= 0.0:
            return target
        if quat_equals(cur, target):
            return target
        return slerp(cur, target, clamp(speed * dt, 0.0, 1.0))
    raise TypeError(f"interp_to: unsupported value {cur!r}")


def interp_constant_to(cur, target, dt, speed):
    """FInterpConstantTo / Vector2DInterpConstantTo / VInterpConstantTo / RInterpConstantTo / QInterpConstantTo."""
    if isinstance(cur, float):
        dist = target - cur
        if dist * dist < SMALL_NUMBER:
            return target
        step = speed * dt
        return cur + clamp(dist, -step, step)
    t = type(cur)
    if t is Vec2 or t is Vec3:
        delta = target - cur
        delta_m = length(delta)
        max_step = speed * dt
        if delta_m > max_step:
            if max_step > 0.0:
                return cur + (delta / delta_m) * max_step
            return cur
        return target
    if t is Rotator:
        if dt == 0.0 or cur == target:
            return cur
        if speed <= 0.0:
            return target
        ds = speed * dt
        delta = delta_rotator(target, cur)
        moved = Rotator(
            cur[0] + clamp(delta[0], -ds, ds),
            cur[1] + clamp(delta[1], -ds, ds),
            cur[2] + clamp(delta[2], -ds, ds),
        )
        return normalize_rotator(moved)
    if t is Quat:
        if speed <= 0.0:
            return target
        if quat_equals(cur, target):
            return target
        step = clamp(dt * speed, 0.0, 1.0)
        ang = max_(SMALL_NUMBER, quat_angular_distance(target, cur))
        alpha = clamp(step / ang, 0.0, 1.0)
        return slerp(cur, target, alpha)
    raise TypeError(f"interp_constant_to: unsupported value {cur!r}")


def exponential_smoothing(cur, target, dt, smoothing_time):
    """FMath::ExponentialSmoothingApprox for float / vec2 / vec3."""
    if smoothing_time > KINDA_SMALL_NUMBER:
        e = inv_exp_approx(dt / smoothing_time)
        return target + (cur - target) * e
    return target


# ---- quaternion and vector helpers (G13) ---------------------------------------------------

def fast_lerp(a, b, alpha):
    """FQuat::FastLerp: the shortest-route linear blend, NOT normalized."""
    dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]
    k = (1.0 if dot >= 0.0 else -1.0) * (1.0 - alpha)
    return Quat(b[0] * alpha + a[0] * k, b[1] * alpha + a[1] * k, b[2] * alpha + a[2] * k, b[3] * alpha + a[3] * k)


def quat_from_axis_angle(axis, rad):
    """FQuat(Axis, AngleRad); the axis must be a unit vector."""
    half = 0.5 * rad
    s, c = sin(half), cos(half)
    return Quat(s * axis[0], s * axis[1], s * axis[2], c)


def quat_from_rotation_vector(v):
    """FQuat::MakeFromRotationVector: the rotation about v by |v| radians (Exp of the half vector)."""
    x, y, z = v[0] * 0.5, v[1] * 0.5, v[2] * 0.5
    angle = sqrt(x * x + y * y + z * z)
    sin_angle = sin(angle)
    if abs_(sin_angle) >= SMALL_NUMBER:
        k = sin_angle / angle
        return Quat(k * x, k * y, k * z, cos(angle))
    return Quat(x, y, z, cos(angle))


def to_orientation_quat(v):
    """FVector::ToOrientationQuat: the rotation that turns +X to v, without roll."""
    yaw = atan2(v[1], v[0])
    pitch = atan2(v[2], sqrt(v[0] * v[0] + v[1] * v[1]))
    sp, cp = sin(pitch * 0.5), cos(pitch * 0.5)
    sy, cy = sin(yaw * 0.5), cos(yaw * 0.5)
    return Quat(sp * sy, -sp * cy, cp * sy, cp * cy)


def get_twist(q, axis):
    """The twist part of FQuat::ToSwingTwist(axis, swing, twist): the rotation about `axis`."""
    d = axis[0] * q[0] + axis[1] * q[1] + axis[2] * q[2]
    t = Quat(d * axis[0], d * axis[1], d * axis[2], q[3])
    if t[0] * t[0] + t[1] * t[1] + t[2] * t[2] + t[3] * t[3] == 0.0:
        return QUAT_IDENTITY
    return quat_normalize(t)


def _unwind_radians(a):
    while a > PI:
        a -= TWO_PI
    while a < -PI:
        a += TWO_PI
    return a


def twist_angle(q, axis):
    """FQuat::GetTwistAngle: the twist about `axis` in radians, in [-pi, pi]."""
    d = axis[0] * q[0] + axis[1] * q[1] + axis[2] * q[2]
    return _unwind_radians(2.0 * atan2(d, q[3]))


def project_on_normal(v, n):
    """FVector::ProjectOnToNormal: n * (v . n)."""
    return n * (v[0] * n[0] + v[1] * n[1] + v[2] * n[2])


def safe_normal_2d(v):
    """FVector::GetSafeNormal2D: the horizontal direction (z = 0); zero for a (nearly) vertical vector."""
    sq = v[0] * v[0] + v[1] * v[1]
    if sq == 1.0:
        return v if v[2] == 0.0 else Vec3(v[0], v[1], 0.0)
    if sq < SMALL_NUMBER:
        return VEC3_ZERO
    k = inv_sqrt(sq)
    return Vec3(v[0] * k, v[1] * k, 0.0)


def size_2d(v):
    """FVector::Size2D."""
    return sqrt(v[0] * v[0] + v[1] * v[1])


def half_life_to_smoothing_time(half_life):
    """SpringMath::HalfLifeToSmoothingTime."""
    return half_life / LN2


# ---- transforms (G12): the QST formulas of UE's FTransform for non-negative scale --------------------------------

def _safe_recip(s, tol=SMALL_NUMBER):
    """FTransform::GetSafeScaleReciprocal: 1 / scale, 0 for a (nearly) zero component."""
    return Vec3(*(0.0 if abs(c) <= tol else 1.0 / c for c in s))


def make_transform(location, rotation, scale=(1.0, 1.0, 1.0)):
    return Transform(location, rotation, scale)


def transform_position(t, v):
    """FTransform::TransformPosition: rotate(scale * v) + location."""
    return _rotate_q(t[1], t[2] * v, False) + t[0]


def inverse_transform_position(t, v):
    """FTransform::InverseTransformPosition: unrotate(v - location) / scale."""
    return _rotate_q(t[1], v - t[0], True) * _safe_recip(t[2])


def transform_rotation(t, q):
    """FTransform::TransformRotation: rotation * q."""
    return quat_mul(t[1], q)


def inverse_transform_rotation(t, q):
    """FTransform::InverseTransformRotation: inverse(rotation) * q."""
    return quat_mul(quat_inverse(t[1]), q)


def transform_vector_no_scale(t, v):
    """FTransform::TransformVectorNoScale: rotate(v)."""
    return _rotate_q(t[1], v, False)


def compose(a, b):
    """FTransform::operator* (a * b): apply a, then b."""
    return Transform(_rotate_q(b[1], b[2] * a[0], False) + b[0], quat_mul(b[1], a[1]), a[2] * b[2])


def relative_transform(a, b):
    """FTransform::GetRelativeTransform: a relative to b (a * inverse(b)); the identity when b's rotation is not normalized."""
    if abs(1.0 - (b[1][0] ** 2 + b[1][1] ** 2 + b[1][2] ** 2 + b[1][3] ** 2)) >= 0.01:
        return Transform()
    safe = _safe_recip(b[2])
    inv = quat_inverse(b[1])
    return Transform(_rotate_q(inv, a[0] - b[0], False) * safe, quat_mul(inv, a[1]), a[2] * safe)


def relative_transform_reverse(a, b):
    """FTransform::GetRelativeTransformReverse: inverse(a) * b."""
    scale = b[2] * _safe_recip(a[2])
    rot = quat_mul(b[1], quat_inverse(a[1]))
    return Transform(b[0] - _rotate_q(rot, scale * a[0], False), rot, scale)


def inverse(t):
    """FTransform::Inverse (exact for uniform scale)."""
    inv_rot = quat_inverse(t[1])
    inv_scale = _safe_recip(t[2])
    return Transform(_rotate_q(inv_rot, inv_scale * (-t[0]), False), inv_rot, inv_scale)


def with_scale(t, scale):
    return Transform(t[0], t[1], scale)


def _ease_in(alpha, exp):
    return pow_(alpha, exp)


def _ease_out(alpha, exp):
    return 1.0 - pow_(1.0 - alpha, exp)


def _ease_in_out(alpha, exp):
    return _ease_in(alpha * 2.0, exp) * 0.5 if alpha < 0.5 else _ease_out(alpha * 2.0 - 1.0, exp) * 0.5 + 0.5


def _circ_in(a):
    return -1.0 * (sqrt(1.0 - a * a) - 1.0)


def _circ_out(a):
    a -= 1.0
    return sqrt(1.0 - a * a)


def _expo_in(a):
    return 0.0 if a == 0.0 else pow_(2.0, 10.0 * (a - 1.0))


def _expo_out(a):
    return 1.0 if a == 1.0 else -pow_(2.0, -10.0 * a) + 1.0


def _in_out(inn, out, a):
    return inn(a * 2.0) * 0.5 if a < 0.5 else out(a * 2.0 - 1.0) * 0.5 + 0.5


def blend_alpha(alpha, option, curve):
    """FAlphaBlend::AlphaToBlendOption: the eased alpha in [0, 1]; `Custom` reads the curve over its own time range
    (linear when there is no curve)."""
    o = int(option)
    if o == BlendOption.Sinusoidal:
        r = (sin(alpha * PI - HALF_PI) + 1.0) / 2.0
    elif o == BlendOption.Cubic:
        r = (-2.0 * alpha * alpha * alpha) + (3.0 * alpha * alpha)                   # CubicInterp(0, 0, 1, 0, alpha)
    elif o == BlendOption.HermiteCubic:
        r = 0.0 if alpha < 0.0 else (1.0 if alpha >= 1.0 else alpha * alpha * (3.0 - 2.0 * alpha))     # SmoothStep(0, 1, alpha)
    elif o == BlendOption.QuadraticInOut:
        r = _ease_in_out(alpha, 2.0)
    elif o == BlendOption.CubicInOut:
        r = _ease_in_out(alpha, 3.0)
    elif o == BlendOption.QuarticInOut:
        r = _ease_in_out(alpha, 4.0)
    elif o == BlendOption.QuinticInOut:
        r = _ease_in_out(alpha, 5.0)
    elif o == BlendOption.CircularIn:
        r = _circ_in(alpha)
    elif o == BlendOption.CircularOut:
        r = _circ_out(alpha)
    elif o == BlendOption.CircularInOut:
        r = _in_out(_circ_in, _circ_out, alpha)
    elif o == BlendOption.ExpIn:
        r = _expo_in(alpha)
    elif o == BlendOption.ExpOut:
        r = _expo_out(alpha)
    elif o == BlendOption.ExpInOut:
        r = _in_out(_expo_in, _expo_out, alpha)
    elif o == BlendOption.Custom and curve is not None:
        keys = getattr(curve, "keys", None) or []
        lo, hi = (keys[0].time, keys[-1].time) if keys else (0.0, 0.0)
        r = curve_eval(curve, lo + (hi - lo) * alpha)
    else:
        r = alpha
    return clamp(r, 0.0, 1.0)


def critical_spring_damper(x, v, target, smoothing_time, dt):
    """SpringMath::CriticalSpringDamper on a float or a vec3: the value and velocity after dt (a Spring or a Spring3)."""
    scalar = isinstance(x, (int, float))
    if smoothing_time < SMALL_NUMBER:
        return Spring(target, 0.0) if scalar else Spring3(target, VEC3_ZERO)
    y = 4.0 / max_(smoothing_time, SMALL_NUMBER) / 2.0                 # SmoothingTimeToDamping / 2
    ey_dt = inv_exp_approx(y * dt)
    if scalar:
        j0 = x - target
        j1 = v + j0 * y
        return Spring(ey_dt * (j0 + j1 * dt) + target, ey_dt * (v - j1 * y * dt))
    j0 = Vec3(x[0] - target[0], x[1] - target[1], x[2] - target[2])
    j1 = Vec3(v[0] + j0[0] * y, v[1] + j0[1] * y, v[2] + j0[2] * y)
    return Spring3(Vec3(*(ey_dt * (j0[i] + j1[i] * dt) + target[i] for i in range(3))),
                   Vec3(*(ey_dt * (v[i] - j1[i] * y * dt) for i in range(3))))


# ---- struct values --------------------------------------------------------------------

class Struct:
    """Base class of struct and settings values: the generated classes and the interpreter's classes derive from it.

    Subclasses define ``__slots__`` and ``_FIELDS`` (the member names in declaration order), ``_TYPE_NAME`` and
    ``_IMMUTABLE`` (settings are shared, never copied).
    """

    __slots__ = ()
    _FIELDS: tuple = ()
    _TYPE_NAME = ""
    _IMMUTABLE = False
    _UNFLAT: tuple = ()        # the members a flat snapshot leaves out: the tables, lists and curves of a settings value (they have no leaf form)

    def copy(self):
        new = object.__new__(type(self))
        for n in self._FIELDS:
            v = getattr(self, n)
            setattr(new, n, v.copy() if isinstance(v, Struct) and not v._IMMUTABLE else v)
        return new

    def to_plain(self) -> dict:
        """The value as nested plain data (dicts for structs, tuples for vectors)."""
        return {n: (v.to_plain() if isinstance(v, Struct) else to_plain(v)) for n in self._FIELDS for v in (getattr(self, n),)}

    def flatten(self, prefix: str, out: dict) -> None:
        for n in self._FIELDS:
            if n in self._UNFLAT:
                continue
            v = getattr(self, n)
            if isinstance(v, Struct):
                v.flatten(prefix + "." + n, out)
            else:
                out[prefix + "." + n] = to_plain(v)

    def __repr__(self):
        return f"{self._TYPE_NAME}(" + ", ".join(f"{n}={getattr(self, n)!r}" for n in self._FIELDS) + ")"

    _SPEC: tuple = ()          # (member, converter) -- how from_value turns a loose value into the member's representation

    @classmethod
    def from_value(cls, v):
        """A value of this class from another instance (a peer's copy of the same struct), a dict, or None (the defaults)."""
        if type(v) is cls:
            return v if cls._IMMUTABLE else v.copy()
        if v is None:
            return cls()
        if cls._IMMUTABLE and not isinstance(v, Struct):
            return cls.load(v)               # settings: the data conventions of dal.settings (files, names, tables)
        items = {n: getattr(v, n) for n in v._FIELDS} if isinstance(v, Struct) else v
        return cls(**{n: conv(items[n]) for n, conv in cls._SPEC if n in items})


def flatten_into(out: dict, name: str, value) -> None:
    """Add a state / input value to a flat snapshot: struct members become ``Name.Member`` entries."""
    if isinstance(value, Struct):
        value.flatten(name, out)
    else:
        out[name] = to_plain(value)


# ---- plain-data helpers --------------------------------------------------------

def to_plain(v):
    """Convert a runtime value to plain Python data (floats/ints/bools/tuples) for comparison."""
    if isinstance(v, IntEnum):
        return int(v)
    if isinstance(v, tuple):
        return tuple(to_plain(c) if not isinstance(c, (int, float)) or isinstance(c, bool) else float(c) for c in v)
    if isinstance(v, bool):
        return bool(v)
    return v


# ---- support for generated classes -----------------------------------------------

def swizzle(v, comps: str):
    """``v.xy`` / ``v.xyz`` on vec2/vec3 (components named x, y, z)."""
    picked = [v["xyz".index(c)] for c in comps]
    return Vec2(*picked) if len(picked) == 2 else Vec3(*picked)


_COERCE = {
    "float": float,
    "int": int,
    "bool": bool,
    "vec2": as_vec2,
    "vec3": as_vec3,
    "rotator": as_rotator,
    "quat": as_quat,
    "hit": as_hit,
    "transform": as_transform,
    "spring": as_spring,
    "spring3": as_spring3,
    # the double types are the same Python values
    "double": float,
    "vec2d": as_vec2,
    "vec3d": as_vec3,
    "rotatord": as_rotator,
    "quatd": as_quat,
    "transformd": as_transform,
}


def section_name(names, value):
    """Montage section name for an enum value (index 0 is 'None': no section)."""
    i = int(value)
    return None if i == 0 else names[i]


# float32 mode: the float types round what they take
_COERCE32 = dict(_COERCE, float=f32, vec2=f32_vec2, vec3=f32_vec3, rotator=f32_rotator, quat=f32_quat, transform=lambda v: f32_transform(as_transform(v)),
                 hit=as_hit32, spring=lambda v: f32_spring(as_spring(v)), spring3=lambda v: f32_spring3(as_spring3(v)))


def coerce_by_name(type_name: str, value, f32_mode: bool = False):
    """Coerce ``value`` to the runtime representation of the DAL type called ``type_name`` (enums -> int); ``f32_mode``: the float types are rounded to float32."""
    return (_COERCE32 if f32_mode else _COERCE).get(type_name, int)(value)


# the hook events of the character (hostapi.HOOK_EVENTS): how the host's values become the handler's parameters
def _as_mode(v):
    return MovementMode(int(v))


HOOK_ARGS = {                                   # event -> ((converter, value when the host left it out), ...)
    "movement_mode_changed": ((_as_mode, 0), (_as_mode, 0)),
    "jumped": (),
    "landed": ((as_hit, Hit()),),
    "crouch_started": (),
    "crouch_ended": (),
    "teleported": (),
    "physics_rotation": ((float, 0.0),),
}
HOOK_ARGS32 = dict(HOOK_ARGS, landed=((as_hit32, Hit()),), physics_rotation=((f32, 0.0),))        # (float32 mode)


class LogicBase:
    """Base class of generated Python logic classes.

    A subclass provides the class attributes ``DAL_PARAMS`` (name, type, default),
    ``DAL_INPUTS`` (name, type, host spec or None), ``DAL_STATES`` (name, type) and
    ``DAL_ENTRIES`` (names), plus ``reset()``, ``_sample()`` and ``update(dt)``.
    """

    DAL_NAME = ""
    DAL_VERSION = ""
    DAL_PARAMS: tuple = ()
    DAL_INPUTS: tuple = ()
    DAL_STATES: tuple = ()
    DAL_ENTRIES: tuple = ()
    DAL_KIND = "anim"
    DAL_HANDLERS: tuple = ()      # (event, name, phase, method, value type)

    DAL_PEERS: tuple = ()         # (peer name, logic name)
    DAL_METHODS: tuple = ()       # pub methods: other logics (and hosts) may call them
    DAL_VIEWS: tuple = ()         # pub views
    DAL_PHASES: tuple = ()        # tick slots of the logic (empty: one update)
    DAL_POST_FLUSH = False        # anim logic with `post_flush`: update() and the entries leave the commands queued; post_update() hands them over
    DAL_SLOT_EVENTS = False       # actor logic with `slot_events`: the events are handled at the start of every tick slot, not only the first
    DAL_SYNC_EVENTS = False       # logic with `sync_events`: receive_event() handles an event the host raises at once
    DAL_ORDER: tuple = ()         # actor logic: (relation, peer, peer logic, slots) -- in these tick slots the block runs 'before' / 'after' the peer's
    DAL_FLUSH_PEERS = False       # actor logic with `flush_peers`: the commands a peer queued in the calls of a block run when this logic hands its own over
    DAL_FLOAT32 = False           # generated in float32 mode: every float is a float32 (see f32)

    def __init__(self, host, params: dict, peers: dict | None = None):
        from .hostapi import HostV1

        params = dict(params)
        self.resolver = params.pop("resolver", None)         # where settings find their curves (dal.settings.AssetResolver)
        self.host = host if host is not None else HostV1()
        self.overrides: dict = {}
        self.peers: dict = dict(peers) if peers else {}     # peer name -> the other logic (a generated class or an Interpreter)
        self.unresolved: set = set()                         # peers that were used but not connected
        self._peer_queue: list = []
        self._begun = False
        self._dt = 0.0
        self._source = None           # the montage instance that raised the notify being handled (montage_stop_source)
        self._pending: list = []      # events posted by the caller
        self._now: list = []          # sync_events: events raised while an event was being handled
        self._in_now = False
        self._cmds: list = []         # commands queued during the update
        self._called: list = []       # `flush_peers`: the peers whose pub methods this logic called since it last handed its commands over
        known = {p[0] for p in self.DAL_PARAMS}
        unknown = sorted(set(params) - known)
        if unknown:
            raise TypeError(f"{self.DAL_NAME}: unknown param(s) {unknown}; known: {sorted(known)}")
        for name, type_name, default in self.DAL_PARAMS:
            setattr(self, name, self._coerce(type_name, params[name]) if name in params else default)

    def set_input(self, name: str, value) -> None:
        """Set a free input (an input without a host or peer binding)."""
        for n, type_name, host_spec in self.DAL_INPUTS:
            if n == name:
                if host_spec is not None:
                    raise ValueError(f"input {name!r} is bound to {host_spec}; use .overrides[{name!r}] to force a value")
                setattr(self, name, self._coerce(type_name, value))
                return
        raise KeyError(f"{self.DAL_NAME} has no input {name!r}")

    DAL_TYPES: dict = {}          # type name -> converter, for the struct / tag types the class uses

    def _coerce(self, type_name: str, value):
        conv = self.DAL_TYPES.get(type_name)
        if conv is None:
            return coerce_by_name(type_name, value, self.DAL_FLOAT32)
        cls = getattr(conv, "__self__", None)
        if getattr(cls, "_IMMUTABLE", False) and not isinstance(value, Struct):
            return cls.load(value, self.resolver)             # a settings class reads files, dicts and names
        return conv(value)

    def sample_inputs(self) -> None:
        self._sample()

    def update_phase(self, phase: str, dt: float) -> None:
        """One tick slot.  A logic without tick slots has one update, which runs in pre_movement (generated classes with
        slots override this)."""
        if phase == "pre_movement" and not self.DAL_PHASES:
            self.update(dt)

    # ---- peers (v3) --------------------------------------------------------------------------------------
    def connect(self, name: str, other) -> None:
        """Wire the peer ``name`` (a ``peer`` declaration) to another logic instance."""
        self.peers[name] = other

    def dal_get(self, name: str):
        """Another logic reads a state, input or param of this one."""
        return getattr(self, name)

    def dal_call(self, method: str, *args):
        """Another logic (or the host) calls a ``pub method``."""
        if method not in self.DAL_METHODS:
            raise KeyError(f"{self.DAL_NAME} has no pub method {method!r}")
        return getattr(self, method)(*args)

    def dal_enqueue(self, method: str, args: tuple) -> None:
        """A call queued by an actor logic: it runs at the start of this logic's next update."""
        self._peer_queue.append((method, tuple(args)))

    def _drain_peer_calls(self) -> None:
        queue, self._peer_queue = self._peer_queue, []
        for method, args in queue:
            self.dal_call(method, *args)

    def _peer_call(self, peer: str, method: str, args: tuple, default=None, queued: bool = False):
        other = self.peers.get(peer)
        if other is None:
            self.unresolved.add(peer)
            return default
        if queued:
            other.dal_enqueue(method, args)
            return None
        if self.DAL_FLUSH_PEERS and not any(other is p for p in self._called):
            self._called.append(other)
        return other.dal_call(method, *args)

    # ---- events and commands (v1) ---------------------------------------------------------------------
    def post_event(self, kind: str, name: str, *args) -> None:
        """Tell the logic that something happened; its ``on`` handlers run at the start of the next update."""
        self._pending.append((kind, name) + args)

    def receive_event(self, event: tuple) -> None:
        """`sync_events`: the host raised an event (a hook, a declared notify) and the logic handles it now, in the middle of whatever the host is
        doing: sample the inputs, run the handlers, hand the commands over.  An event raised while a handler's commands run waits for that handler."""
        self._now.append(tuple(event))
        if self._in_now:
            return
        self._in_now = True
        try:
            while self._now:
                ev = self._now.pop(0)
                begin = getattr(self, "begin_play", None)
                if begin is not None and not getattr(self, "_begun", True):
                    begin()
                if self.DAL_KIND == "actor":                          # (an animation logic does not sample: its update may be in flight on a worker thread)
                    self._sample_event()
                self._dispatch([ev])
                if not self.DAL_POST_FLUSH:
                    self._flush()
        finally:
            self._in_now = False

    def _sample_event(self) -> None:
        """Sample the inputs for an event handled at once (the generated class passes a slot that consumes nothing)."""
        self._sample()

    def _collect_events(self) -> list:
        events, self._pending = self._pending, []
        poll = getattr(self.host, "poll_events", None)
        if poll is not None:
            events = events + [tuple(ev) for ev in poll()]
        return events

    def _dispatch(self, events: list) -> None:
        for ev in events:
            kind = ev[0]
            if kind in HOOK_ARGS:                           # the hooks of the character have no name: the payload follows the kind
                conv = (HOOK_ARGS32 if self.DAL_FLOAT32 else HOOK_ARGS)[kind]
                args = [c(ev[i + 1] if i + 1 < len(ev) else d) for i, (c, d) in enumerate(conv)]
                for h_kind, _h_name, _h_phase, method, n_params in self.DAL_HANDLERS:
                    if h_kind == kind:
                        getattr(self, method)(*args[:int(n_params)])          # a handler that names no parameters gets none
                continue
            name = ev[1]
            for h_kind, h_name, h_phase, method, value_type in self.DAL_HANDLERS:
                if h_kind != kind or h_name != name:
                    continue
                if kind in ("montage_ended", "montage_blending_out"):
                    getattr(self, method)(bool(ev[2]))
                elif kind == "input":
                    if h_phase == ev[2]:
                        getattr(self, method)(coerce_by_name(value_type, ev[3] if len(ev) > 3 else 0.0, self.DAL_FLOAT32))
                elif value_type:                                  # a declared notify: the payload (its properties) and the montage instance
                    self._source = ev[3] if len(ev) > 3 else None
                    try:
                        getattr(self, method)(self._coerce(value_type, ev[2] if len(ev) > 2 else None))
                    finally:
                        self._source = None
                else:
                    getattr(self, method)()

    def _cmd(self, name: str, *args) -> None:
        self._cmds.append((name, args))

    def _flush(self) -> None:
        """Hand the queued commands to the host, in order (Unreal executes them on the game thread)."""
        cmds, self._cmds = self._cmds, []
        for name, args in cmds:
            fn = getattr(self.host, name, None)
            if fn is not None:
                fn(*args)
                if name == "snapshot_pose":
                    self._took_snapshot(args[0])
        called, self._called = self._called, []           # `flush_peers`: what the calls of the block made the peers queue runs now, in the order they were called
        for other in called:
            other.dal_flush()

    def dal_flush(self) -> None:
        """Hand the queued commands to the host now (a logic with `flush_peers` does it for the peers it called)."""
        self._flush()

    def take_commands(self) -> list:
        """The commands queued since the last flush (and forget them) -- for hosts that execute them themselves."""
        cmds, self._cmds = self._cmds, []
        for name, args in cmds:
            if name == "snapshot_pose":
                self._took_snapshot(args[0])
        called, self._called = self._called, []           # `flush_peers`: the commands the called peers queued follow
        for other in called:
            cmds = cmds + other.take_commands()
        return cmds

    def _took_snapshot(self, path: str) -> None:
        """host.snapshot_pose(State): once the host took the pose, the snapshot state holds its name (what valid() sees)."""
        head, *rest = path.split(".")
        if not rest:
            setattr(self, head, path)
            return
        target = getattr(self, head)
        for member in rest[:-1]:
            target = getattr(target, member)
        setattr(target, rest[-1], path)

    def call_entry(self, name: str, /, *args, **named) -> None:
        """Call an entry the way a graph does: by name, with the parameters by position or by name (missing ones take their defaults)."""
        if name not in self.DAL_ENTRIES:
            raise KeyError(f"{self.DAL_NAME} has no entry {name!r}")
        getattr(self, name)(*args, **named)

    def snapshot(self) -> dict:
        """Inputs then states as plain data (floats / ints / bools / tuples)."""
        out = {}
        for n, _t, _h in self.DAL_INPUTS:
            flatten_into(out, n, getattr(self, n))
        for n, _t in self.DAL_STATES:
            flatten_into(out, n, getattr(self, n))
        return out
