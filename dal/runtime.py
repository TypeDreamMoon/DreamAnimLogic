"""DAL runtime for the Python backend and the reference interpreter.

Value types (``Vec2``, ``Vec3``, ``Rotator``, ``Quat``) and the math library with
Unreal Engine semantics.  Every function here is a re-implementation of the
behaviour of the UE function named in its comment (UE 5.8 ``FMath``,
``FRotator``, ``FQuat``, ``TVector``); no UE code is copied.

Differences from a UE float build that are inherent to Python:

* values are IEEE doubles, not floats (the C++ backend uses ``float``);
* ``atan2`` uses the C library; UE's float ``FMath::Atan2`` is a minimax
  approximation with a relative error below 1e-6 against it;
* ``to_rotator`` computes pitch with an exact ``asin``; UE's float
  ``FQuat::Rotator`` uses ``FMath::FastAsin`` (error up to about 7e-5 rad).

Pure Python 3.11+, no dependencies.
"""

from __future__ import annotations

import math
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
    """Result of a host trace (UE ``FHitResult``, reduced): blocking, point, normal, distance, time (0..1 along the trace)."""

    __slots__ = ()

    def __new__(cls, blocking=False, point=(0.0, 0.0, 0.0), normal=(0.0, 0.0, 0.0), distance=0.0, time=1.0):
        return tuple.__new__(cls, (bool(blocking), as_vec3(point), as_vec3(normal), float(distance), float(time)))

    blocking = property(lambda s: s[0])
    point = property(lambda s: s[1])
    normal = property(lambda s: s[2])
    distance = property(lambda s: s[3])
    time = property(lambda s: s[4])

    def __repr__(self):
        return f"Hit(blocking={self[0]!r}, point={self[1]!r}, normal={self[2]!r}, distance={self[3]!r}, time={self[4]!r})"


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
}


def section_name(names, value):
    """Montage section name for an enum value (index 0 is 'None': no section)."""
    i = int(value)
    return None if i == 0 else names[i]


def coerce_by_name(type_name: str, value):
    """Coerce ``value`` to the runtime representation of the DAL type called ``type_name`` (enums -> int)."""
    return _COERCE.get(type_name, int)(value)


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

    def __init__(self, host, params: dict):
        from .hostapi import HostV1

        self.host = host if host is not None else HostV1()
        self.overrides: dict = {}
        self._dt = 0.0
        self._pending: list = []      # events posted by the caller
        self._cmds: list = []         # commands queued during the update
        known = {p[0] for p in self.DAL_PARAMS}
        unknown = sorted(set(params) - known)
        if unknown:
            raise TypeError(f"{self.DAL_NAME}: unknown param(s) {unknown}; known: {sorted(known)}")
        for name, type_name, default in self.DAL_PARAMS:
            setattr(self, name, coerce_by_name(type_name, params[name]) if name in params else default)

    def set_input(self, name: str, value) -> None:
        """Set a free input (an input without a host binding)."""
        for n, type_name, host_spec in self.DAL_INPUTS:
            if n == name:
                if host_spec is not None:
                    raise ValueError(f"input {name!r} is bound to {host_spec}; use .overrides[{name!r}] to force a value")
                setattr(self, name, coerce_by_name(type_name, value))
                return
        raise KeyError(f"{self.DAL_NAME} has no input {name!r}")

    def sample_inputs(self) -> None:
        self._sample()

    # ---- events and commands (v1) ---------------------------------------------------------------------
    def post_event(self, kind: str, name: str, *args) -> None:
        """Tell the logic that something happened; its ``on`` handlers run at the start of the next update."""
        self._pending.append((kind, name) + args)

    def _collect_events(self) -> list:
        events, self._pending = self._pending, []
        poll = getattr(self.host, "poll_events", None)
        if poll is not None:
            events = events + [tuple(ev) for ev in poll()]
        return events

    def _dispatch(self, events: list) -> None:
        for ev in events:
            kind, name = ev[0], ev[1]
            for h_kind, h_name, h_phase, method, value_type in self.DAL_HANDLERS:
                if h_kind != kind or h_name != name:
                    continue
                if kind in ("montage_ended", "montage_blending_out"):
                    getattr(self, method)(bool(ev[2]))
                elif kind == "input":
                    if h_phase == ev[2]:
                        getattr(self, method)(coerce_by_name(value_type, ev[3] if len(ev) > 3 else 0.0))
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

    def take_commands(self) -> list:
        """The commands queued since the last flush (and forget them) -- for hosts that execute them themselves."""
        cmds, self._cmds = self._cmds, []
        return cmds

    def call_entry(self, name: str) -> None:
        if name not in self.DAL_ENTRIES:
            raise KeyError(f"{self.DAL_NAME} has no entry {name!r}")
        getattr(self, name)()

    def snapshot(self) -> dict:
        """Inputs then states as plain data (floats / ints / bools / tuples)."""
        out = {}
        for n, _t, _h in self.DAL_INPUTS:
            out[n] = to_plain(getattr(self, n))
        for n, _t in self.DAL_STATES:
            out[n] = to_plain(getattr(self, n))
        return out
