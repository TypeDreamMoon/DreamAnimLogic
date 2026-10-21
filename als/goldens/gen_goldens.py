# gen_goldens.py -- editor-side generator of the "T1" golden values for the ALS math library.
#
# What it does
#   Calls the REAL compiled ALS statics (UAlsMath, UAlsRotation, UAlsVector, UAlsUtility, UAlsMontageUtility) and a set of engine
#   math functions (unreal.MathLibrary) from the UE 5.8 editor Python API with fixed-seed random inputs plus hand-picked edge inputs,
#   and writes compact JSON files (one case per line) that the DAL (.dal) rewrite is tested against offline.
#
# How to run (Windows; host project DreamAnimTestHost with the ALS plugin already built; nothing is built or saved):
#   1. Import the user environment variables that put the UE caches on G: (PowerShell):
#        foreach ($n in 'UE-ZenDataPath','UE-LocalDataCachePath') { [Environment]::SetEnvironmentVariable($n, [Environment]::GetEnvironmentVariable($n,'User'), 'Process') }
#   2. Optional: $env:ALS_GOLDENS_OUT = '<output folder>'   (default: the folder this script lives in)
#   3. I:\UnrealEngine\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe I:\UnrealProject_Moon\DEV_58\DreamVEdAnimHost\DreamAnimTestHost.uproject
#        -run=pythonscript -script="<path>\gen_goldens.py" -unattended -nopause -nosplash -NoSound -NoLiveCoding -notelemetry -abslog=<log file>
#   The run takes about a minute. The exit code is 0 when the script reached its end (the process is terminated by the script itself,
#   see terminate_process(): the UE 5.8 pythonscript commandlet otherwise crashes in Python finalization at exit in this host).
#
# Determinism
#   Every function has its own random generator seeded from a string, there are no timestamps in the output and the JSON text is
#   produced by dump_doc() (one case per line). Running the script twice gives byte-identical files.
#
# Value rules (see README.md next to the output)
#   * float (32 bit) parameters are generated as exactly float32-representable values (rounded with struct '<f') and the rounded value
#     is what is stored; the UE return values of float functions are exact float32 values stored as the Python double (repr).
#   * double parameters/returns (FVector, FRotator, FQuat, double returns) keep double precision; the random inputs for them are
#     also rounded to float32 so that a float32 implementation sees the same inputs (a few edge cases use real doubles).
#   * NaN / inf are encoded as the JSON strings "nan", "inf", "-inf". Signed zero is kept ("-0.0").
#   * Vectors [x,y,z], 2D vectors [x,y], rotators [pitch,yaw,roll] (FRotator member order), quaternions [x,y,z,w].
#
# Safety notes
#   * FMath::UnwindDegrees loops by +-360 until the value is in range: it never terminates for inf and for |x| >= ~8.6e9 (float ulp > 720),
#     so every angle input here is finite and <= 1e7 in magnitude.
#   * NaN inputs are only used for scalar functions (no FVector/FQuat paths, where editor builds may run NaN diagnostics).
import ctypes
import json
import math
import os
import random
import re
import struct
import sys
import traceback

import unreal

SCHEMA = "als-goldens/1"
GENERATOR = "gen_goldens.py"

try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:  # pragma: no cover
    _HERE = os.getcwd()
OUT_DIR = os.environ.get("ALS_GOLDENS_OUT") or _HERE

INF = float("inf")
NAN = float("nan")
FLT_MAX = 3.4028234663852886e+38
FLT_MIN = 1.1754943508222875e-38      # smallest normal float32
FLT_TRUE_MIN = 1.401298464324817e-45  # smallest denormal float32


# =====================================================================================================================
# float32 helpers
# =====================================================================================================================
def f32(x):
    """Round a Python float to the nearest float32 (as a Python double)."""
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0]
    except OverflowError:
        return INF if x > 0 else -INF


def f32_step(x, up=True):
    """Next float32 above (up=True) or below (up=False) x."""
    if x != x or x in (INF, -INF):
        return x
    if x == 0.0:
        return FLT_TRUE_MIN if up else -FLT_TRUE_MIN
    bits = struct.unpack("<I", struct.pack("<f", x))[0]
    if (x > 0) == up:
        bits += 1
    else:
        bits -= 1
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def around(x, n=2):
    """x and its n float32 neighbours on both sides (1 ulp, 2 ulp, ...)."""
    out = [x]
    a = b = x
    for _ in range(n):
        a = f32_step(a, True)
        b = f32_step(b, False)
        out += [a, b]
    return out


SMALL_NUMBER = f32(1e-8)        # UE_SMALL_NUMBER (float)
KINDA_SMALL_NUMBER = f32(1e-4)  # UE_KINDA_SMALL_NUMBER (float)


# =====================================================================================================================
# random helpers (every function has its own generator: independent of the order of the functions)
# =====================================================================================================================
def rng_for(key):
    return random.Random("als-goldens/1/" + key)


def U(rng, lo, hi):
    return f32(rng.uniform(lo, hi))


def LU(rng, lo, hi):
    """Log-uniform float32 in [lo, hi]."""
    return f32(math.exp(rng.uniform(math.log(lo), math.log(hi))))


def unit3(rng):
    while True:
        g = [rng.gauss(0, 1) for _ in range(3)]
        n = math.sqrt(sum(c * c for c in g))
        if n > 1e-3:
            return [f32(c / n) for c in g]


def unit2(rng):
    a = rng.uniform(-math.pi, math.pi)
    return [f32(math.cos(a)), f32(math.sin(a))]


def unit_quat(rng):
    while True:
        g = [rng.gauss(0, 1) for _ in range(4)]
        n = math.sqrt(sum(c * c for c in g))
        if n > 1e-3:
            return [f32(c / n) for c in g]


def vec_range(rng, lo, hi):
    return [U(rng, lo, hi) for _ in range(3)]


def scaled(v, s):
    return [f32(c * s) for c in v]


def rot_random(rng, wide=False):
    if wide:
        return [U(rng, -540, 540), U(rng, -540, 540), U(rng, -540, 540)]
    return [U(rng, -90, 90), U(rng, -180, 180), U(rng, -180, 180)]


# =====================================================================================================================
# encoding of values (JSON-safe)
# =====================================================================================================================
def enc(x):
    if isinstance(x, bool) or x is None or isinstance(x, (int, str)):
        return x
    if isinstance(x, float):
        if x != x:
            return "nan"
        if x == INF:
            return "inf"
        if x == -INF:
            return "-inf"
        return x
    if isinstance(x, (list, tuple)):
        return [enc(i) for i in x]
    if isinstance(x, dict):
        return {k: enc(v) for k, v in x.items()}
    raise TypeError("cannot encode %r" % (type(x),))


def jline(x):
    return json.dumps(enc(x), separators=(",", ":"), allow_nan=False, ensure_ascii=True)


# kinds: float32 float64 vec3 vec3f vec2 rotator quat bool name tag int
def to_ue(kind, v):
    if kind in ("float32", "float64", "bool", "int", "name"):
        return v
    if kind == "vec3":
        return unreal.Vector(x=v[0], y=v[1], z=v[2])
    if kind == "vec3f":
        return unreal.Vector3f(x=v[0], y=v[1], z=v[2])
    if kind == "vec2":
        return unreal.Vector2D(x=v[0], y=v[1])
    if kind == "rotator":
        return unreal.Rotator(roll=v[2], pitch=v[0], yaw=v[1])  # NOTE: keyword args; the positional order is (roll, pitch, yaw)
    if kind == "quat":
        return unreal.Quat(x=v[0], y=v[1], z=v[2], w=v[3])
    if kind == "tag":
        t = unreal.GameplayTag()
        t.import_text('(TagName="%s")' % v)
        return t
    raise ValueError(kind)


def dbl(x, exact=False):
    """Input policy for double-typed parameters: round to float32 so that a float32 implementation sees the same input, unless the case is a
    deliberate double-precision case (exact=True, tag contains "double_precision") or the value is outside the float32 range
    (overflow to inf / underflow to 0): then the double is kept."""
    x = float(x)
    if exact or x == 0.0 or x != x or x in (INF, -INF):
        return x
    r = f32(x)
    if r in (INF, -INF) or r == 0.0:
        return x
    return r


def norm_in(kind, v, exact=False):
    """The value that is stored in the JSON (and passed to UE): float32 kinds are rounded to float32, double kinds see dbl()."""
    if kind == "float32":
        return f32(float(v))
    if kind == "vec3f":
        return [f32(float(c)) for c in v]
    if kind == "float64":
        return dbl(v, exact)
    if kind in ("vec3", "vec2", "rotator", "quat"):
        return [dbl(c, exact) for c in v]
    return v


_MOVE_DIR = {0: "Forward", 1: "Backward", 2: "Left", 3: "Right"}
_TAG_RE = re.compile(r'TagName="([^"]*)"')


def from_ue(kind, o):
    if kind in ("float32", "float64"):
        return float(o)
    if kind == "bool":
        return bool(o)
    if kind in ("vec3", "vec3f"):
        return [o.x, o.y, o.z]
    if kind == "vec2":
        return [o.x, o.y]
    if kind == "rotator":
        return [o.pitch, o.yaw, o.roll]
    if kind == "quat":
        return [o.x, o.y, o.z, o.w]
    if kind == "transform":
        return {"translation": [o.translation.x, o.translation.y, o.translation.z],
                "rotation": [o.rotation.x, o.rotation.y, o.rotation.z, o.rotation.w],
                "scale": [o.scale3d.x, o.scale3d.y, o.scale3d.z]}
    if kind == "pole":
        # TryCalculatePoleVector: Python returns None for a false result and (ProjectionLocation, PoleDirection) for true
        if o is None:
            return [False, None, None]
        return [True, [o[0].x, o[0].y, o[0].z], [o[1].x, o[1].y, o[1].z]]
    if kind == "enum_move_dir":
        v = int(o.value)
        return {"name": _MOVE_DIR[v], "value": v}
    if kind == "str":
        return str(o)
    if kind == "name":
        return str(o)
    if kind == "tags":
        return [_TAG_RE.search(str(t.export_text())).group(1) for t in o.gameplay_tags]
    raise ValueError(kind)


# =====================================================================================================================
# function registry / document writer
# =====================================================================================================================
REG = {}  # file name -> [Fn]


class Fn:
    def __init__(self, file, cname, py, sig, params, returns, call, note=None):
        self.file, self.cname, self.py, self.sig = file, cname, py, sig
        self.params, self.returns, self.call, self.note = params, returns, call, note
        self.cases = []
        self.sequences = []
        self.extra = {}
        REG.setdefault(file, []).append(self)

    def add(self, tag, **inp):
        exact = "double_precision" in tag
        norm = {n: norm_in(k, inp[n], exact) for n, k in self.params}
        case = {"in": norm}
        try:
            args = {n: to_ue(k, norm[n]) for n, k in self.params}
            case["out"] = from_ue(self.returns, self.call(args))
        except Exception as exc:  # recorded, never aborts the run
            case["out"] = None
            case["error"] = "%s: %s" % (type(exc).__name__, str(exc).splitlines()[0][:160])
        case["tag"] = tag
        self.cases.append(case)
        return case

    def header(self):
        h = {"function": self.cname, "python": self.py, "signature": self.sig,
             "params": [n for n, _ in self.params],
             "param_types": {n: k for n, k in self.params}, "returns": self.returns}
        if self.note:
            h["note"] = self.note
        h.update(self.extra)
        return h


def E(why):
    return "edge:" + why


def dump_fn(fn):
    h = jline(fn.header())
    lines = [h[:-1] + ',"cases":[']
    lines += [("  " + jline(c) + ("," if i + 1 < len(fn.cases) else "")) for i, c in enumerate(fn.cases)]
    if fn.sequences:
        lines.append(' ],"sequences":[')
        lines += [("  " + jline(s) + ("," if i + 1 < len(fn.sequences) else "")) for i, s in enumerate(fn.sequences)]
        lines.append(" ]}")
    else:
        lines.append(" ]}")
    return "\n".join(lines)


def dump_doc(file, meta, fns):
    head = jline(dict(meta, file=file))
    body = ",\n".join(dump_fn(f) for f in fns)
    return head[:-1] + ',"functions":[\n' + body + "\n]}\n"


# =====================================================================================================================
A = unreal.AlsMath
R = unreal.AlsRotation
V = unreal.AlsVector
AU = unreal.AlsUtility
ML = unreal.MathLibrary

F32 = "float32"
F64 = "float64"


# =====================================================================================================================
# UAlsMath
# =====================================================================================================================
def gen_als_math():
    F = "als_math.json"

    # ---- Clamp01 ----
    fn = Fn(F, "UAlsMath::Clamp01", "AlsMath.clamp01", "float Clamp01(float Value)",
            [("Value", F32)], F32, lambda a: A.clamp01(a["Value"]))
    rng = rng_for("Clamp01")
    for _ in range(150):
        fn.add("random", Value=U(rng, -1.5, 2.5))
    for _ in range(30):
        fn.add("random", Value=U(rng, -10, 10))
    for k in range(-2, 11):
        fn.add("random:grid", Value=k * 0.125)
    for why, v in [("zero", 0.0), ("negative_zero", -0.0), ("one", 1.0), ("above_zero_1ulp", f32_step(0.0, True)),
                   ("below_zero_1ulp", f32_step(0.0, False)), ("below_one_1ulp", f32_step(1.0, False)),
                   ("above_one_1ulp", f32_step(1.0, True)), ("tiny_negative", -1e-30), ("tiny_positive", 1e-30),
                   ("huge_positive", 1e30), ("huge_negative", -1e30), ("flt_max", FLT_MAX), ("neg_flt_max", -FLT_MAX),
                   ("inf", INF), ("neg_inf", -INF), ("nan", NAN), ("min_normal", FLT_MIN), ("denormal", FLT_TRUE_MIN)]:
        fn.add(E(why), Value=v)

    # ---- LerpClamped ----
    fn = Fn(F, "UAlsMath::LerpClamped", "AlsMath.lerp_clamped", "float LerpClamped(float From, float To, float Ratio)",
            [("From", F32), ("To", F32), ("Ratio", F32)], F32, lambda a: A.lerp_clamped(a["From"], a["To"], a["Ratio"]))
    rng = rng_for("LerpClamped")
    for _ in range(160):
        fn.add("random", From=U(rng, -500, 500), To=U(rng, -500, 500), Ratio=U(rng, -0.5, 1.5))
    for _ in range(40):
        fn.add("random:ratio_in_range", From=U(rng, -500, 500), To=U(rng, -500, 500), Ratio=U(rng, 0, 1))
    for why, f, t, r in [("ratio_zero", 3.0, 9.0, 0.0), ("ratio_one", 3.0, 9.0, 1.0), ("ratio_below", 3.0, 9.0, -1.0),
                         ("ratio_above", 3.0, 9.0, 2.0), ("ratio_half", 3.0, 9.0, 0.5), ("ratio_neg_zero", 3.0, 9.0, -0.0),
                         ("ratio_nan", 3.0, 9.0, NAN), ("from_equals_to", 5.0, 5.0, 0.37), ("from_neg_to", -7.0, 7.0, 0.25),
                         ("to_inf_ratio_half", 0.0, INF, 0.5), ("to_inf_ratio_zero_is_nan", 0.0, INF, 0.0),
                         ("both_inf", INF, INF, 0.5), ("huge_values", 1e30, -1e30, 0.5), ("flt_max_span", -FLT_MAX, FLT_MAX, 0.5),
                         ("ratio_one_ulp_below_one", 0.0, 100.0, f32_step(1.0, False)),
                         ("ratio_1ulp_above_zero", 0.0, 100.0, f32_step(0.0, True)),
                         ("from_nan", NAN, 1.0, 0.5), ("to_nan", 1.0, NAN, 0.5)]:
        fn.add(E(why), From=f, To=t, Ratio=r)

    # ---- DamperExactAlpha ----
    fn = Fn(F, "UAlsMath::DamperExactAlpha", "AlsMath.damper_exact_alpha", "float DamperExactAlpha(float DeltaTime, float HalfLife)",
            [("DeltaTime", F32), ("HalfLife", F32)], F32, lambda a: A.damper_exact_alpha(a["DeltaTime"], a["HalfLife"]),
            note="Alpha = 1 - FMath::InvExpApprox(UE_LN2 / (HalfLife + UE_SMALL_NUMBER) * DeltaTime) with "
                 "InvExpApprox(x) = 1 / (1 + 1.00746054 x + 0.45053901 x^2 + 0.25724632 x^3): an approximation of 1 - 2^(-dt/hl), "
                 "NOT bit-exact to it. The approximation is the truth.")
    rng = rng_for("DamperExactAlpha")
    for _ in range(120):
        fn.add("random:log_dt_log_hl", DeltaTime=LU(rng, 1e-4, 0.1), HalfLife=LU(rng, 1e-3, 2.0))
    for _ in range(50):
        fn.add("random", DeltaTime=U(rng, 1e-4, 0.1), HalfLife=U(rng, 0.0, 2.0))
    for _ in range(20):
        fn.add("random:typical_60fps", DeltaTime=f32(1.0 / 60.0), HalfLife=U(rng, 0.0, 2.0))
    dts = [("dt_zero", 0.0), ("dt_small_number", SMALL_NUMBER), ("dt_60fps", f32(1.0 / 60.0)), ("dt_0.1", f32(0.1)), ("dt_1", 1.0),
           ("dt_negative", f32(-1.0 / 60.0)), ("dt_nan", NAN), ("dt_inf", INF)]
    hls = [("hl_zero", 0.0), ("hl_negative_zero", -0.0), ("hl_small_number", SMALL_NUMBER), ("hl_minus_small_number", -SMALL_NUMBER),
           ("hl_0.2", f32(0.2)), ("hl_2", 2.0), ("hl_1e6", 1e6), ("hl_negative", -0.5), ("hl_nan", NAN), ("hl_inf", INF)]
    for dn, d in dts:
        for hn, h in hls:
            fn.add(E(dn + "+" + hn), DeltaTime=d, HalfLife=h)

    # ---- SpringDamperFloat (stateful) ----
    gen_spring_float(F)

    # ---- CalculateMovementDirection ----
    fn = Fn(F, "UAlsMath::CalculateMovementDirection", "AlsMath.calculate_movement_direction",
            "EAlsMovementDirection CalculateMovementDirection(float Angle, float ForwardHalfAngle, float AngleThreshold)",
            [("Angle", F32), ("ForwardHalfAngle", F32), ("AngleThreshold", F32)], "enum_move_dir",
            lambda a: A.calculate_movement_direction(a["Angle"], a["ForwardHalfAngle"], a["AngleThreshold"]),
            note="EAlsMovementDirection: Forward=0, Backward=1, Left=2, Right=3. ALS itself calls it with ForwardHalfAngle=70, AngleThreshold=5.")
    rng = rng_for("CalculateMovementDirection")
    for _ in range(70):
        fn.add("random:als_params_70_5", Angle=U(rng, -180, 180), ForwardHalfAngle=70.0, AngleThreshold=5.0)
    for _ in range(60):
        fn.add("random", Angle=U(rng, -180, 180), ForwardHalfAngle=U(rng, 10, 90), AngleThreshold=U(rng, 0, 30))
    for _ in range(20):
        fn.add("random:angle_out_of_range", Angle=U(rng, -540, 540), ForwardHalfAngle=70.0, AngleThreshold=5.0)

    def f32add(a, b):
        return f32(a + b)

    for fha, thr in [(70.0, 5.0), (45.0, 0.0), (90.0, 10.0), (60.0, 20.0)]:
        bounds = {
            "+fha": fha, "-fha": -fha,
            "+(fha+thr)": f32add(fha, thr), "-(fha+thr)": f32add(-fha, -thr),
            "+(fha-thr)": f32add(fha, -thr), "-(fha-thr)": -f32add(fha, -thr),
            "+(180-fha+thr)": f32add(f32add(180.0, -fha), thr), "-(180-fha+thr)": -f32add(f32add(180.0, -fha), thr),
            "+(180-fha)": f32add(180.0, -fha), "-(180-fha)": -f32add(180.0, -fha),
        }
        for bn, b in bounds.items():
            for sn, v in (("at", b), ("1ulp_above", f32_step(b, True)), ("1ulp_below", f32_step(b, False))):
                fn.add(E("boundary_%s_%s_fha%g_thr%g" % (bn, sn, fha, thr)), Angle=v, ForwardHalfAngle=fha, AngleThreshold=thr)
    for why, ang in [("zero", 0.0), ("neg_zero", -0.0), ("180", 180.0), ("-180", -180.0), ("90", 90.0), ("-90", -90.0),
                     ("179.99998", f32_step(180.0, False)), ("above_180", f32_step(180.0, True)), ("181", 181.0), ("-181", -181.0),
                     ("360", 360.0), ("nan", NAN)]:
        fn.add(E("angle_" + why), Angle=ang, ForwardHalfAngle=70.0, AngleThreshold=5.0)
    fn.add(E("fha_zero_thr_zero_angle_zero"), Angle=0.0, ForwardHalfAngle=0.0, AngleThreshold=0.0)
    fn.add(E("fha_180"), Angle=179.0, ForwardHalfAngle=180.0, AngleThreshold=5.0)
    fn.add(E("negative_threshold"), Angle=72.0, ForwardHalfAngle=70.0, AngleThreshold=-5.0)
    fn.add(E("negative_fha"), Angle=10.0, ForwardHalfAngle=-70.0, AngleThreshold=5.0)

    # ---- TryCalculatePoleVector ----
    gen_pole_vector(F)


def gen_pole_vector(F):
    fn = Fn(F, "UAlsMath::TryCalculatePoleVector", "AlsMath.try_calculate_pole_vector",
            "bool TryCalculatePoleVector(const FVector& ALocation, const FVector& BLocation, const FVector& CLocation, FVector& ProjectionLocation, FVector& PoleDirection)",
            [("ALocation", "vec3"), ("BLocation", "vec3"), ("CLocation", "vec3")], "pole",
            lambda a: A.try_calculate_pole_vector(a["ALocation"], a["BLocation"], a["CLocation"]),
            note="out = [bool, ProjectionLocation, PoleDirection]. Editor Python returns None when the C++ function returns false, so in the "
                 "false cases the two out vectors are NOT observable and are stored as null. C++ semantics for the false cases (from the source): "
                 "A==B (|AB| nearly zero, tolerance 1e-4) -> ProjectionLocation=A, PoleDirection=0; A,B,C collinear (the normalized pole direction "
                 "cannot be built, |B - Projection|^2 < 1e-8) -> ProjectionLocation = A + AB projected on AC, PoleDirection = unnormalized B - Projection. "
                 "A==C (AC cannot be normalized) returns true with ProjectionLocation=A and PoleDirection=normalized AB.")

    def add(tag, a, b, c):
        return fn.add(tag, ALocation=a, BLocation=b, CLocation=c)

    rng = rng_for("TryCalculatePoleVector")
    for _ in range(90):
        add("random", vec_range(rng, -100, 100), vec_range(rng, -100, 100), vec_range(rng, -100, 100))
    for _ in range(40):
        # leg like: A hip, C ankle, B knee in front of the AC line
        a = [U(rng, -20, 20), U(rng, -20, 20), U(rng, 80, 100)]
        c = [f32(a[0] + rng.uniform(-30, 30)), f32(a[1] + rng.uniform(-30, 30)), U(rng, 0, 20)]
        mid = [(a[i] + c[i]) / 2.0 for i in range(3)]
        off = [rng.uniform(-8, 8), rng.uniform(5, 15), rng.uniform(-8, 8)]
        b = [f32(mid[i] + off[i]) for i in range(3)]
        add("random:leg_like", a, b, c)
    for _ in range(6):
        add("random:large_coordinates", vec_range(rng, -1e6, 1e6), vec_range(rng, -1e6, 1e6), vec_range(rng, -1e6, 1e6))
    O = [0.0, 0.0, 0.0]
    add(E("right_angle_knee"), O, [1.0, 1.0, 0.0], [2.0, 0.0, 0.0])
    add(E("b_on_ac_line_collinear"), O, [1.0, 0.0, 0.0], [2.0, 0.0, 0.0])
    add(E("b_beyond_c_collinear"), O, [3.0, 0.0, 0.0], [2.0, 0.0, 0.0])
    add(E("b_behind_a_collinear"), O, [-3.0, 0.0, 0.0], [2.0, 0.0, 0.0])
    add(E("a_equals_b"), O, O, [2.0, 0.0, 0.0])
    add(E("a_equals_b_equals_c"), O, O, O)
    add(E("a_equals_c"), O, [0.0, 1.0, 0.0], O)
    add(E("b_equals_c"), O, [2.0, 0.0, 0.0], [2.0, 0.0, 0.0])
    add(E("a_equals_c_nonzero"), [5.0, 5.0, 5.0], [5.0, 8.0, 9.0], [5.0, 5.0, 5.0])
    add(E("ab_just_below_tolerance"), O, [f32(5e-5), 0.0, 0.0], [2.0, 0.0, 0.0])
    add(E("ab_just_above_tolerance"), O, [f32(2e-4), 0.0, 0.0], [2.0, 0.0, 0.0])
    add(E("ab_tolerance_per_component_1e-4"), O, [1e-4, 1e-4, 1e-4], [2.0, 0.0, 0.0])
    add(E("ac_tiny_below_normalize_tolerance"), O, [0.0, 1.0, 0.0], [5e-5, 0.0, 0.0])
    add(E("ac_tiny_above_normalize_tolerance"), O, [0.0, 1.0, 0.0], [2e-4, 0.0, 0.0])
    add(E("pole_nearly_degenerate_9e-5"), O, [1.0, 9e-5, 0.0], [2.0, 0.0, 0.0])
    add(E("pole_nearly_degenerate_1.1e-4"), O, [1.0, 1.1e-4, 0.0], [2.0, 0.0, 0.0])
    add(E("vertical_leg"), [0.0, 0.0, 100.0], [0.0, 10.0, 50.0], [0.0, 0.0, 0.0])
    add(E("diagonal_collinear"), [1.0, 1.0, 1.0], [2.0, 2.0, 2.0], [4.0, 4.0, 4.0])
    add(E("b_off_axis_by_1e-3"), O, [1.0, 1e-3, 0.0], [2.0, 0.0, 0.0])
    add(E("double_precision_inputs"), [0.1, 0.2, 0.3], [1.1, 1.7, 0.4], [2.3, 0.9, 0.7])
    add(E("huge_coordinates_1e9"), [1e9, 0.0, 0.0], [1e9 + 10.0, 5.0, 0.0], [1e9 + 20.0, 0.0, 0.0])


# =====================================================================================================================
# springs
# =====================================================================================================================
def mk_sf(s):
    return unreal.AlsSpringFloatState(velocity=s["Velocity"], previous_target=s["PreviousTarget"], state_valid=bool(s["bStateValid"]))


def rd_sf(st):
    return {"Velocity": st.velocity, "PreviousTarget": st.previous_target, "bStateValid": bool(st.state_valid)}


def mk_sv(s):
    return unreal.AlsSpringVectorState(velocity=unreal.Vector(x=s["Velocity"][0], y=s["Velocity"][1], z=s["Velocity"][2]),
                                       previous_target=unreal.Vector(x=s["PreviousTarget"][0], y=s["PreviousTarget"][1], z=s["PreviousTarget"][2]),
                                       state_valid=bool(s["bStateValid"]))


def rd_sv(st):
    v, p = st.velocity, st.previous_target
    return {"Velocity": [v.x, v.y, v.z], "PreviousTarget": [p.x, p.y, p.z], "bStateValid": bool(st.state_valid)}


def sf_step(state, cur, tgt, dt, freq, damp, tva):
    """One UAlsMath::SpringDamperFloat call. The ref struct is NOT mutated by Python: the new state is the 2nd item of the returned tuple."""
    out, new_state = A.spring_damper_float(mk_sf(state), cur, tgt, dt, freq, damp, tva)
    return float(out), rd_sf(new_state)


def sv_step(state, cur, tgt, dt, freq, damp, tva):
    out, new_state = V.spring_damper_vector(mk_sv(state), unreal.Vector(x=cur[0], y=cur[1], z=cur[2]),
                                            unreal.Vector(x=tgt[0], y=tgt[1], z=tgt[2]), dt, freq, damp, tva)
    return [out.x, out.y, out.z], rd_sv(new_state)


SPRING_NOTE = ("The Python call does NOT mutate the passed state struct; it returns (value, new_state). "
               "state_in / state_out are the FAlsSpring*State fields (Velocity, PreviousTarget, bStateValid). "
               "C++: DeltaTime <= UE_SMALL_NUMBER returns Current and leaves the state untouched; an invalid state returns Target, "
               "zeroes Velocity, stores PreviousTarget=Target and sets bStateValid; otherwise FMath::SpringDamper is run with "
               "TargetVelocity = (Target - PreviousTarget) * Clamp01(TargetVelocityAmount) / DeltaTime.")


def gen_spring_float(F):
    fn = Fn(F, "UAlsMath::SpringDamperFloat", "AlsMath.spring_damper_float",
            "float SpringDamperFloat(FAlsSpringFloatState& SpringState, float Current, float Target, float DeltaTime, float Frequency, float DampingRatio, float TargetVelocityAmount = 1.0f)",
            [("Current", F32), ("Target", F32), ("DeltaTime", F32), ("Frequency", F32), ("DampingRatio", F32), ("TargetVelocityAmount", F32)],
            F32, None, note=SPRING_NOTE)
    fn.extra["state_type"] = {"Velocity": F32, "PreviousTarget": F32, "bStateValid": "bool"}

    def add(tag, st, cur, tgt, dt, freq, damp, tva):
        st = {"Velocity": f32(st["Velocity"]), "PreviousTarget": f32(st["PreviousTarget"]), "bStateValid": bool(st["bStateValid"])}
        inp = {"Current": f32(cur), "Target": f32(tgt), "DeltaTime": f32(dt), "Frequency": f32(freq), "DampingRatio": f32(damp),
               "TargetVelocityAmount": f32(tva)}
        case = {"in": inp, "state_in": st}
        try:
            out, so = sf_step(st, inp["Current"], inp["Target"], inp["DeltaTime"], inp["Frequency"], inp["DampingRatio"], inp["TargetVelocityAmount"])
            case["out"], case["state_out"] = out, so
        except Exception as exc:
            case["out"], case["state_out"] = None, None
            case["error"] = "%s: %s" % (type(exc).__name__, str(exc).splitlines()[0][:160])
        case["tag"] = tag
        fn.cases.append(case)

    def S(v=0.0, p=0.0, valid=True):
        return {"Velocity": v, "PreviousTarget": p, "bStateValid": valid}

    rng = rng_for("SpringDamperFloat")
    for _ in range(120):
        valid = rng.random() < 0.85
        prev = U(rng, -100, 100)
        tgt = f32(prev + rng.uniform(-5, 5)) if rng.random() < 0.5 else U(rng, -100, 100)
        dt = f32(rng.choice([1.0 / 60.0, 1.0 / 30.0, 1.0 / 120.0])) if rng.random() < 0.3 else LU(rng, 1e-4, 0.1)
        damp = f32(rng.choice([0.5, 1.0, 2.0])) if rng.random() < 0.2 else U(rng, 0, 3)
        add("random", S(U(rng, -50, 50), prev, valid), U(rng, -100, 100), tgt, dt, U(rng, 0.5, 20), damp,
            1.0 if rng.random() < 0.6 else U(rng, 0, 1))
    # edges (single step)
    base = (S(3.0, 10.0, True), 8.0, 12.0)
    st, cur, tgt = base
    dt60 = f32(1 / 60.0)
    add(E("dt_zero_returns_current"), st, cur, tgt, 0.0, 5.0, 1.0, 1.0)
    add(E("dt_small_number_returns_current"), st, cur, tgt, SMALL_NUMBER, 5.0, 1.0, 1.0)
    add(E("dt_just_above_small_number"), st, cur, tgt, f32_step(SMALL_NUMBER, True), 5.0, 1.0, 1.0)
    add(E("dt_negative_returns_current"), st, cur, tgt, -dt60, 5.0, 1.0, 1.0)
    add(E("dt_1e-9_returns_current"), st, cur, tgt, f32(1e-9), 5.0, 1.0, 1.0)
    add(E("dt_zero_invalid_state_unchanged"), S(0.0, 0.0, False), cur, tgt, 0.0, 5.0, 1.0, 1.0)
    add(E("invalid_state_returns_target"), S(7.0, 3.0, False), cur, tgt, dt60, 5.0, 1.0, 1.0)
    add(E("invalid_state_nonzero_velocity_cleared"), S(99.0, -4.0, False), 1.0, -50.0, dt60, 5.0, 1.0, 1.0)
    add(E("freq_zero_W_below_small_number"), st, cur, tgt, dt60, 0.0, 1.0, 1.0)
    add(E("freq_1.5e-9_W_below_small_number"), st, cur, tgt, dt60, f32(1.5e-9), 1.0, 1.0)
    add(E("freq_1.6e-9_W_above_small_number"), st, cur, tgt, dt60, f32(1.6e-9), 1.0, 1.0)
    add(E("freq_negative"), st, cur, tgt, dt60, -5.0, 1.0, 1.0)
    add(E("damping_zero_undamped"), st, cur, tgt, dt60, 5.0, 0.0, 1.0)
    add(E("damping_1e-9_undamped"), st, cur, tgt, dt60, 5.0, f32(1e-9), 1.0)
    add(E("damping_small_number_underdamped"), st, cur, tgt, dt60, 5.0, SMALL_NUMBER, 1.0)
    add(E("damping_negative_undamped_branch"), st, cur, tgt, dt60, 5.0, -0.5, 1.0)
    add(E("damping_0.5_under"), st, cur, tgt, dt60, 5.0, 0.5, 1.0)
    add(E("damping_1ulp_below_one"), st, cur, tgt, dt60, 5.0, f32_step(1.0, False), 1.0)
    add(E("damping_exactly_one_critical"), st, cur, tgt, dt60, 5.0, 1.0, 1.0)
    add(E("damping_1ulp_above_one"), st, cur, tgt, dt60, 5.0, f32_step(1.0, True), 1.0)
    add(E("damping_2_over"), st, cur, tgt, dt60, 5.0, 2.0, 1.0)
    add(E("damping_10_over"), st, cur, tgt, dt60, 5.0, 10.0, 1.0)
    add(E("damping_100_over"), st, cur, tgt, dt60, 5.0, 100.0, 1.0)
    for name, tva in [("tva_zero", 0.0), ("tva_half", 0.5), ("tva_one", 1.0), ("tva_negative_clamped_to_zero", -1.0),
                      ("tva_two_clamped_to_one", 2.0), ("tva_nan_clamped_to_zero", NAN)]:
        add(E(name), st, cur, tgt, dt60, 5.0, 1.0, tva)
    add(E("target_jump_large_velocity"), S(0.0, 0.0, True), 0.0, 1000.0, dt60, 5.0, 1.0, 1.0)
    add(E("dt_large_0.25_critical"), st, cur, tgt, 0.25, 8.0, 1.0, 1.0)
    add(E("dt_large_1.0_under"), st, cur, tgt, 1.0, 8.0, 0.5, 1.0)
    add(E("dt_large_10_over"), st, cur, tgt, 10.0, 8.0, 2.0, 1.0)
    add(E("current_equals_target_zero_velocity"), S(0.0, 5.0, True), 5.0, 5.0, dt60, 5.0, 1.0, 1.0)
    add(E("target_nan"), st, cur, NAN, dt60, 5.0, 1.0, 1.0)
    add(E("current_nan"), st, NAN, tgt, dt60, 5.0, 1.0, 1.0)
    add(E("dt_nan_not_small_number"), st, cur, tgt, NAN, 5.0, 1.0, 1.0)
    add(E("velocity_huge"), S(1e20, 10.0, True), cur, tgt, dt60, 5.0, 1.0, 1.0)

    # ---- chained sequences ----
    def chain(tag, freq, damp, tva, steps, target_fn, dt_fn, cur0, resets=()):
        freq, damp, tva = f32(freq), f32(damp), f32(tva)
        state = S(0.0, 0.0, False)
        cur = f32(cur0)
        current_start = cur
        rows = []
        events = []
        for i in range(steps):
            if i in resets:
                state = S(0.0, 0.0, False)
                events.append({"step": i, "op": "Reset"})
            tgt = f32(target_fn(i))
            dt = f32(dt_fn(i))
            out, state = sf_step(state, cur, tgt, dt, freq, damp, tva)
            rows.append([tgt, dt, out, state["Velocity"], state["PreviousTarget"], state["bStateValid"]])
            cur = out
        fn.sequences.append({
            "tag": tag, "in": {"Frequency": freq, "DampingRatio": damp, "TargetVelocityAmount": tva},
            "current_start": current_start,
            "state_start": {"Velocity": 0.0, "PreviousTarget": 0.0, "bStateValid": False},
            "events": events,
            "columns": ["Target", "DeltaTime", "out", "state_out.Velocity", "state_out.PreviousTarget", "state_out.bStateValid"],
            "feedback": "Current of step 0 = current_start, Current of step n+1 = out of step n; state of step 0 = state_start, state of step n+1 = state_out of step n (events override the state before the step)",
            "rows": rows})

    d60 = lambda i: 1.0 / 60.0
    step10 = lambda i: 0.0 if i < 10 else 10.0
    sine = lambda i: 10.0 * math.sin(2 * math.pi * i / 45.0)
    ramp = lambda i: 0.5 * i
    chain("chain:critical_step", 4.0, 1.0, 1.0, 60, step10, d60, 0.0)
    chain("chain:underdamped_sine", 6.0, 0.5, 1.0, 60, sine, d60, 0.0)
    chain("chain:overdamped_ramp", 3.0, 2.0, 1.0, 60, ramp, d60, 0.0)
    chain("chain:critical_sine_no_target_velocity", 4.0, 1.0, 0.0, 60, sine, d60, 0.0)
    chain("chain:very_underdamped_half_tva_step", 10.0, 0.1, 0.5, 45, step10, d60, 0.0)
    chain("chain:undamped_step", 2.0, 0.0, 1.0, 40, step10, d60, 0.0)
    chain("chain:frequency_zero_ramp", 0.0, 1.0, 1.0, 30, ramp, d60, 0.0)
    dts = [1 / 60.0, 1 / 30.0, 1 / 120.0, 0.0, 1e-8, 0.1]
    chain("chain:variable_dt_with_zero_dt_steps", 5.0, 1.0, 1.0, 48, sine, lambda i: dts[i % len(dts)], 0.0)
    chain("chain:reset_at_step_25", 5.0, 0.7, 1.0, 50, sine, d60, 0.0, resets=(25,))
    chain("chain:large_dt_0.25", 8.0, 1.0, 1.0, 20, step10, lambda i: 0.25, 0.0)
    chain("chain:start_current_differs_from_target", 6.0, 0.6, 1.0, 40, lambda i: 5.0, d60, -20.0)


def gen_spring_vector(F):
    fn = Fn(F, "UAlsVector::SpringDamperVector", "AlsVector.spring_damper_vector",
            "FVector SpringDamperVector(FAlsSpringVectorState& SpringState, const FVector& Current, const FVector& Target, float DeltaTime, float Frequency, float DampingRatio, float TargetVelocityAmount = 1.0f)",
            [("Current", "vec3"), ("Target", "vec3"), ("DeltaTime", F32), ("Frequency", F32), ("DampingRatio", F32), ("TargetVelocityAmount", F32)],
            "vec3", None, note=SPRING_NOTE + " The state vectors are FVector (double).")
    fn.extra["state_type"] = {"Velocity": "vec3", "PreviousTarget": "vec3", "bStateValid": "bool"}

    def add(tag, st, cur, tgt, dt, freq, damp, tva):
        ex = "double_precision" in tag
        st = {"Velocity": [dbl(c, ex) for c in st["Velocity"]], "PreviousTarget": [dbl(c, ex) for c in st["PreviousTarget"]],
              "bStateValid": bool(st["bStateValid"])}
        inp = {"Current": [dbl(c, ex) for c in cur], "Target": [dbl(c, ex) for c in tgt], "DeltaTime": f32(dt), "Frequency": f32(freq),
               "DampingRatio": f32(damp), "TargetVelocityAmount": f32(tva)}
        case = {"in": inp, "state_in": st}
        try:
            out, so = sv_step(st, inp["Current"], inp["Target"], inp["DeltaTime"], inp["Frequency"], inp["DampingRatio"], inp["TargetVelocityAmount"])
            case["out"], case["state_out"] = out, so
        except Exception as exc:
            case["out"], case["state_out"] = None, None
            case["error"] = "%s: %s" % (type(exc).__name__, str(exc).splitlines()[0][:160])
        case["tag"] = tag
        fn.cases.append(case)

    def S(v=(0.0, 0.0, 0.0), p=(0.0, 0.0, 0.0), valid=True):
        return {"Velocity": list(v), "PreviousTarget": list(p), "bStateValid": valid}

    rng = rng_for("SpringDamperVector")
    for _ in range(90):
        valid = rng.random() < 0.85
        prev = vec_range(rng, -100, 100)
        tgt = [f32(p + rng.uniform(-5, 5)) for p in prev] if rng.random() < 0.5 else vec_range(rng, -100, 100)
        dt = f32(rng.choice([1.0 / 60.0, 1.0 / 30.0, 1.0 / 120.0])) if rng.random() < 0.3 else LU(rng, 1e-4, 0.1)
        damp = f32(rng.choice([0.5, 1.0, 2.0])) if rng.random() < 0.2 else U(rng, 0, 3)
        add("random", S(vec_range(rng, -50, 50), prev, valid), vec_range(rng, -100, 100), tgt, dt, U(rng, 0.5, 20), damp,
            1.0 if rng.random() < 0.6 else U(rng, 0, 1))
    st, cur, tgt = S((3.0, -2.0, 1.0), (10.0, 5.0, -3.0)), [8.0, 4.0, -1.0], [12.0, 6.0, -2.0]
    dt60 = f32(1 / 60.0)
    add(E("dt_zero_returns_current"), st, cur, tgt, 0.0, 5.0, 1.0, 1.0)
    add(E("dt_small_number_returns_current"), st, cur, tgt, SMALL_NUMBER, 5.0, 1.0, 1.0)
    add(E("dt_just_above_small_number"), st, cur, tgt, f32_step(SMALL_NUMBER, True), 5.0, 1.0, 1.0)
    add(E("dt_negative_returns_current"), st, cur, tgt, -dt60, 5.0, 1.0, 1.0)
    add(E("dt_zero_invalid_state_unchanged"), S(valid=False), cur, tgt, 0.0, 5.0, 1.0, 1.0)
    add(E("invalid_state_returns_target"), S((7.0, 7.0, 7.0), (3.0, 3.0, 3.0), False), cur, tgt, dt60, 5.0, 1.0, 1.0)
    add(E("freq_zero"), st, cur, tgt, dt60, 0.0, 1.0, 1.0)
    add(E("damping_zero_undamped"), st, cur, tgt, dt60, 5.0, 0.0, 1.0)
    add(E("damping_0.5_under"), st, cur, tgt, dt60, 5.0, 0.5, 1.0)
    add(E("damping_exactly_one_critical"), st, cur, tgt, dt60, 5.0, 1.0, 1.0)
    add(E("damping_2_over"), st, cur, tgt, dt60, 5.0, 2.0, 1.0)
    add(E("damping_negative"), st, cur, tgt, dt60, 5.0, -0.5, 1.0)
    for name, tva in [("tva_zero", 0.0), ("tva_half", 0.5), ("tva_negative", -1.0), ("tva_two", 2.0)]:
        add(E(name), st, cur, tgt, dt60, 5.0, 1.0, tva)
    add(E("target_jump_large_velocity"), S(), [0.0, 0.0, 0.0], [1000.0, -500.0, 250.0], dt60, 5.0, 1.0, 1.0)
    add(E("dt_large_0.25"), st, cur, tgt, 0.25, 8.0, 1.0, 1.0)
    add(E("current_equals_target"), S((0.0, 0.0, 0.0), (5.0, 5.0, 5.0)), [5.0, 5.0, 5.0], [5.0, 5.0, 5.0], dt60, 5.0, 1.0, 1.0)
    add(E("double_precision_inputs"), S((0.1, 0.2, 0.3), (0.7, 0.8, 0.9)), [0.123456789012345, -0.2, 0.3], [1.0000000001, 0.5, -0.25], dt60, 5.0, 1.0, 1.0)

    def chain(tag, freq, damp, tva, steps, target_fn, dt_fn, cur0):
        freq, damp, tva = f32(freq), f32(damp), f32(tva)
        state = S(valid=False)
        cur = [f32(c) for c in cur0]
        current_start = list(cur)
        rows = []
        for i in range(steps):
            tgt = [f32(c) for c in target_fn(i)]
            dt = f32(dt_fn(i))
            out, state = sv_step(state, cur, tgt, dt, freq, damp, tva)
            rows.append([tgt, dt, out, state["Velocity"], state["PreviousTarget"], state["bStateValid"]])
            cur = out
        fn.sequences.append({
            "tag": tag, "in": {"Frequency": freq, "DampingRatio": damp, "TargetVelocityAmount": tva},
            "current_start": current_start,
            "state_start": {"Velocity": [0.0, 0.0, 0.0], "PreviousTarget": [0.0, 0.0, 0.0], "bStateValid": False},
            "events": [],
            "columns": ["Target", "DeltaTime", "out", "state_out.Velocity", "state_out.PreviousTarget", "state_out.bStateValid"],
            "feedback": "Current of step 0 = current_start, Current of step n+1 = out of step n; state of step 0 = state_start, state of step n+1 = state_out of step n",
            "rows": rows})

    d60 = lambda i: 1.0 / 60.0
    circle = lambda i: [50 * math.cos(2 * math.pi * i / 60.0), 50 * math.sin(2 * math.pi * i / 60.0), 5.0 * i]
    step = lambda i: [0.0, 0.0, 0.0] if i < 10 else [100.0, -40.0, 25.0]
    chain("chain:critical_circle", 4.0, 1.0, 1.0, 60, circle, d60, [0.0, 0.0, 0.0])
    chain("chain:underdamped_step_half_tva", 6.0, 0.4, 0.5, 45, step, d60, [0.0, 0.0, 0.0])
    chain("chain:overdamped_step", 3.0, 2.0, 1.0, 40, step, d60, [0.0, 0.0, 0.0])
    dts = [1 / 60.0, 1 / 30.0, 0.0, 1 / 120.0]
    chain("chain:variable_dt_circle", 5.0, 0.8, 1.0, 40, circle, lambda i: dts[i % len(dts)], [10.0, 10.0, 10.0])


# =====================================================================================================================
# UAlsRotation
# =====================================================================================================================
ANGLE_BASES = [0.0, 175.0, 180.0, -180.0, 360.0, 540.0, -540.0]


def angle_specials():
    """Angles at/around the interesting boundaries: +-1 and +-2 float32 ulps and +-1e-3 (1e-7 is below float32 resolution there)."""
    vals = []
    for b in ANGLE_BASES:
        if b == 0.0:
            vals += [0.0, -0.0, 1e-7, -1e-7, 1e-5, -1e-5, 1e-3, -1e-3]
        else:
            vals += around(b, 1) + [f32(b + 1e-3), f32(b - 1e-3)]
    out = []
    for v in vals:
        v = f32(v)
        if v not in out or v == 0.0:
            out.append(v)
    return out


def gen_als_rotation():
    F = "als_rotation.json"
    SPECIAL = angle_specials()

    # ---- RemapAngleForCounterClockwiseRotation ----
    fn = Fn(F, "UAlsRotation::RemapAngleForCounterClockwiseRotation", "AlsRotation.remap_angle_for_counter_clockwise_rotation",
            "float RemapAngleForCounterClockwiseRotation(float Angle)", [("Angle", F32)], F32,
            lambda a: R.remap_angle_for_counter_clockwise_rotation(a["Angle"]),
            note="Angle > 175 ? Angle - 360 : Angle (strict >).")
    rng = rng_for("RemapAngle")
    for _ in range(140):
        fn.add("random", Angle=U(rng, -540, 540))
    for _ in range(60):
        fn.add("random:near_175_to_180", Angle=U(rng, 170, 181))
    for v in SPECIAL:
        fn.add(E("special_angle"), Angle=v)
    for why, v in [("nan", NAN), ("flt_max", FLT_MAX), ("neg_flt_max", -FLT_MAX), ("inf", INF), ("neg_inf", -INF), ("360.5", 360.5),
                   ("175.00001", f32(175.00001)), ("174.99999", f32(174.99999))]:
        fn.add(E(why), Angle=v)

    # ---- LerpAngle ----
    fn = Fn(F, "UAlsRotation::LerpAngle", "AlsRotation.lerp_angle", "float LerpAngle(float From, float To, float Ratio)",
            [("From", F32), ("To", F32), ("Ratio", F32)], F32, lambda a: R.lerp_angle(a["From"], a["To"], a["Ratio"]),
            note="Delta = Remap(UnwindDegrees(To - From)); result = UnwindDegrees(From + Delta * Ratio). Delta in (175, 180] is flipped to the negative side.")
    rng = rng_for("LerpAngle")
    for _ in range(80):
        fn.add("random:wide", From=U(rng, -540, 540), To=U(rng, -540, 540), Ratio=U(rng, 0, 1))
    for _ in range(50):
        fn.add("random:normalized", From=U(rng, -180, 180), To=U(rng, -180, 180), Ratio=U(rng, 0, 1))
    for _ in range(30):
        fn.add("random:ratio_outside_0_1", From=U(rng, -180, 180), To=U(rng, -180, 180), Ratio=U(rng, -0.5, 1.5))
    for f, t in [(170, -170), (-170, 170), (175, -175), (0, 180), (0, -180), (180, 0), (-180, 0), (0, 175), (0, 176), (0, 174), (90, -90),
                 (-90, 90), (179, -179), (350, 10), (10, 350), (0, 360), (0, 540), (0, -540), (45, 45), (180, -180), (-180, 180)]:
        for r in (0.0, 0.25, 0.5, 1.0):
            fn.add(E("pair_%g_%g_ratio_%g" % (f, t, r)), From=f, To=t, Ratio=r)
    for v in SPECIAL:
        fn.add(E("from_0_to_special"), From=0.0, To=v, Ratio=0.5)
        fn.add(E("from_special_to_0"), From=v, To=0.0, Ratio=0.5)
    for why, f, t, r in [("ratio_nan", 0.0, 90.0, NAN), ("from_nan", NAN, 90.0, 0.5), ("to_nan", 0.0, NAN, 0.5), ("huge_1e7", 1e7, -1e7, 0.5),
                         ("ratio_2", 0.0, 90.0, 2.0), ("ratio_neg", 0.0, 90.0, -1.0), ("1e5_turns", 100000.0, 0.0, 0.5)]:
        fn.add(E(why), From=f, To=t, Ratio=r)

    # ---- LerpRotation ----
    fn = Fn(F, "UAlsRotation::LerpRotation", "AlsRotation.lerp_rotation", "FRotator LerpRotation(const FRotator& From, const FRotator& To, float Ratio)",
            [("From", "rotator"), ("To", "rotator"), ("Ratio", F32)], "rotator", lambda a: R.lerp_rotation(a["From"], a["To"], a["Ratio"]),
            note="Per axis like LerpAngle (vector path: normalize delta, remap > 175 by -360, FMA, normalize). If every |Delta| <= 1e-4 the function returns To unchanged. Rotators are [pitch, yaw, roll].")
    rng = rng_for("LerpRotation")
    for _ in range(80):
        fn.add("random:normalized", From=rot_random(rng), To=rot_random(rng), Ratio=U(rng, 0, 1))
    for _ in range(30):
        fn.add("random:wide", From=rot_random(rng, True), To=rot_random(rng, True), Ratio=U(rng, 0, 1))
    for _ in range(20):
        fn.add("random:ratio_outside_0_1", From=rot_random(rng), To=rot_random(rng), Ratio=U(rng, -0.5, 1.5))
    r0 = [0.0, 0.0, 0.0]
    for why, f, t, r in [
        ("equal", [10.0, 20.0, 30.0], [10.0, 20.0, 30.0], 0.5),
        ("delta_1e-5_returns_to", [10.0, 20.0, 30.0], [f32(10.00001), 20.0, 30.0], 0.5),
        ("delta_just_below_1e-4", [0.0, 0.0, 0.0], [f32(9.9e-5), 0.0, 0.0], 0.5),
        ("delta_exactly_float_1e-4", [0.0, 0.0, 0.0], [KINDA_SMALL_NUMBER, 0.0, 0.0], 0.5),
        ("delta_just_above_1e-4", [0.0, 0.0, 0.0], [f32(1.1e-4), 0.0, 0.0], 0.5),
        ("yaw_170_to_-170", [0.0, 170.0, 0.0], [0.0, -170.0, 0.0], 0.5),
        ("yaw_0_to_180", r0, [0.0, 180.0, 0.0], 0.5),
        ("yaw_0_to_-180", r0, [0.0, -180.0, 0.0], 0.5),
        ("yaw_0_to_175", r0, [0.0, 175.0, 0.0], 0.5),
        ("yaw_0_to_175.001", r0, [0.0, f32(175.001), 0.0], 0.5),
        ("pitch_roll_yaw_all_180", r0, [180.0, 180.0, 180.0], 0.5),
        ("yaw_350_to_10", [0.0, 350.0, 0.0], [0.0, 10.0, 0.0], 0.5),
        ("ratio_zero", [10.0, 20.0, 30.0], [40.0, 50.0, 60.0], 0.0),
        ("ratio_one", [10.0, 20.0, 30.0], [40.0, 50.0, 60.0], 1.0),
        ("from_540", [0.0, 540.0, 0.0], [0.0, 0.0, 0.0], 0.5),
        ("to_-540", [0.0, 0.0, 0.0], [0.0, -540.0, 0.0], 0.5),
        ("ratio_nan", [10.0, 20.0, 30.0], [40.0, 50.0, 60.0], NAN)]:
        fn.add(E(why), From=f, To=t, Ratio=r)
    for v in [175.0, 180.0, -180.0, 360.0]:
        for sv_ in around(v, 1):
            fn.add(E("yaw_delta_special"), From=r0, To=[0.0, sv_, 0.0], Ratio=0.5)
    fn.add(E("double_precision_inputs"), From=[0.1, 0.2, 0.3], To=[10.123456789012345, -20.7, 30.1], Ratio=0.3)

    # ---- InterpolateAngleConstant ----
    fn = Fn(F, "UAlsRotation::InterpolateAngleConstant", "AlsRotation.interpolate_angle_constant",
            "float InterpolateAngleConstant(float Current, float Target, float DeltaTime, float Speed)",
            [("Current", F32), ("Target", F32), ("DeltaTime", F32), ("Speed", F32)], F32,
            lambda a: R.interpolate_angle_constant(a["Current"], a["Target"], a["DeltaTime"], a["Speed"]),
            note="Speed <= 0 or |Delta| <= Speed*DeltaTime returns Target (NOT unwound); otherwise UnwindDegrees(Current + Sign(Remap(Delta)) * MaxDelta).")
    rng = rng_for("InterpolateAngleConstant")
    for _ in range(120):
        fn.add("random", Current=U(rng, -540, 540), Target=U(rng, -540, 540), DeltaTime=LU(rng, 1e-4, 0.1), Speed=U(rng, 0, 1000))
    for _ in range(50):
        fn.add("random:normalized", Current=U(rng, -180, 180), Target=U(rng, -180, 180), DeltaTime=f32(1 / 60.0), Speed=U(rng, 0, 720))
    for _ in range(30):
        fn.add("random:small_delta", Current=(c := U(rng, -170, 170)), Target=f32(c + rng.uniform(-10, 10)), DeltaTime=f32(1 / 60.0),
               Speed=U(rng, 30, 360))
    dt60 = f32(1 / 60.0)
    for why, c, t, dt, sp in [
        ("speed_zero", 0.0, 90.0, dt60, 0.0), ("speed_negative_zero", 0.0, 90.0, dt60, -0.0), ("speed_negative", 0.0, 90.0, dt60, -360.0),
        ("speed_tiny", 0.0, 90.0, dt60, 1e-30), ("dt_zero_nonzero_delta", 0.0, 90.0, 0.0, 360.0), ("dt_zero_equal", 10.0, 10.0, 0.0, 360.0),
        ("current_equals_target", 33.0, 33.0, dt60, 360.0), ("speed_huge", 0.0, 90.0, dt60, 1e9),
        ("delta_180_goes_negative", 0.0, 180.0, dt60, 360.0), ("delta_-180_goes_negative", 0.0, -180.0, dt60, 360.0),
        ("delta_175", 0.0, 175.0, dt60, 360.0), ("delta_175.00002", 0.0, f32_step(175.0, True), dt60, 360.0),
        ("delta_174.99998", 0.0, f32_step(175.0, False), dt60, 360.0), ("delta_-175.00002", 0.0, -f32_step(175.0, True), dt60, 360.0),
        ("target_540_current_0", 0.0, 540.0, dt60, 360.0), ("target_0_current_540", 540.0, 0.0, dt60, 360.0),
        ("current_-540_target_540", -540.0, 540.0, dt60, 360.0), ("wrap_170_to_-170", 170.0, -170.0, dt60, 360.0),
        ("wrap_-170_to_170", -170.0, 170.0, dt60, 360.0), ("huge_angle_1e7", 1e7, 0.0, dt60, 360.0),
        ("speed_nan", 0.0, 90.0, dt60, NAN), ("dt_nan", 0.0, 90.0, NAN, 360.0), ("target_nan", 0.0, NAN, dt60, 360.0), ("current_nan", NAN, 90.0, dt60, 360.0),
    ]:
        fn.add(E(why), Current=c, Target=t, DeltaTime=dt, Speed=sp)
    for sp, dt in [(360.0, dt60), (100.0, 0.1), (720.0, f32(1 / 30.0))]:
        md = f32(sp * dt)
        for v in around(md, 1):
            fn.add(E("delta_equals_max_delta"), Current=0.0, Target=v, DeltaTime=dt, Speed=sp)

    # ---- DamperExactAngle ----
    fn = Fn(F, "UAlsRotation::DamperExactAngle", "AlsRotation.damper_exact_angle",
            "float DamperExactAngle(float Current, float Target, float DeltaTime, float HalfLife)",
            [("Current", F32), ("Target", F32), ("DeltaTime", F32), ("HalfLife", F32)], F32,
            lambda a: R.damper_exact_angle(a["Current"], a["Target"], a["DeltaTime"], a["HalfLife"]),
            note="Delta = UnwindDegrees(Target - Current); |Delta| <= 1e-4 returns Target; else UnwindDegrees(Current + Remap(Delta) * DamperExactAlpha(DeltaTime, HalfLife)).")
    rng = rng_for("DamperExactAngle")
    for _ in range(100):
        fn.add("random:wide", Current=U(rng, -540, 540), Target=U(rng, -540, 540), DeltaTime=LU(rng, 1e-4, 0.1), HalfLife=U(rng, 0, 2))
    for _ in range(80):
        fn.add("random:normalized", Current=U(rng, -180, 180), Target=U(rng, -180, 180), DeltaTime=f32(1 / 60.0), HalfLife=LU(rng, 1e-3, 2))
    for why, c, t, dt, hl in [
        ("delta_zero", 40.0, 40.0, dt60, 0.2),
        ("delta_5e-5_returns_target", 0.0, f32(5e-5), dt60, 0.2), ("delta_9.9e-5_returns_target", 0.0, f32(9.9e-5), dt60, 0.2),
        ("delta_exactly_float_1e-4", 0.0, KINDA_SMALL_NUMBER, dt60, 0.2), ("delta_1ulp_above_1e-4", 0.0, f32_step(KINDA_SMALL_NUMBER, True), dt60, 0.2),
        ("delta_1.1e-4", 0.0, f32(1.1e-4), dt60, 0.2), ("delta_-1.1e-4", 0.0, f32(-1.1e-4), dt60, 0.2),
        ("big_current_tiny_delta", 100.0, f32(100.0000076), dt60, 0.2), ("big_current_delta_1ulp", 100.0, f32_step(100.0, True), dt60, 0.2),
        ("halflife_zero", 0.0, 90.0, dt60, 0.0), ("halflife_small_number", 0.0, 90.0, dt60, SMALL_NUMBER), ("halflife_minus_small_number", 0.0, 90.0, dt60, -SMALL_NUMBER),
        ("halflife_negative", 0.0, 90.0, dt60, -0.5), ("dt_zero", 0.0, 90.0, 0.0, 0.2), ("dt_negative", 0.0, 90.0, -dt60, 0.2),
        ("dt_huge", 0.0, 90.0, 100.0, 0.2), ("halflife_huge", 0.0, 90.0, dt60, 1e9),
        ("delta_180_goes_negative", 0.0, 180.0, dt60, 0.2), ("delta_-180", 0.0, -180.0, dt60, 0.2),
        ("delta_175", 0.0, 175.0, dt60, 0.2), ("delta_175.00002", 0.0, f32_step(175.0, True), dt60, 0.2), ("delta_174.99998", 0.0, f32_step(175.0, False), dt60, 0.2),
        ("wrap_170_to_-170", 170.0, -170.0, dt60, 0.2), ("target_540", 0.0, 540.0, dt60, 0.2), ("current_540_target_0", 540.0, 0.0, dt60, 0.2),
        ("huge_angle_1e7", 1e7, 0.0, dt60, 0.2), ("dt_nan", 0.0, 90.0, NAN, 0.2), ("halflife_nan", 0.0, 90.0, dt60, NAN),
        ("target_nan", 0.0, NAN, dt60, 0.2), ("current_nan", NAN, 90.0, dt60, 0.2),
    ]:
        fn.add(E(why), Current=c, Target=t, DeltaTime=dt, HalfLife=hl)

    # ---- DamperExactRotation ----
    fn = Fn(F, "UAlsRotation::DamperExactRotation", "AlsRotation.damper_exact_rotation",
            "FRotator DamperExactRotation(const FRotator& Current, const FRotator& Target, float DeltaTime, float HalfLife)",
            [("Current", "rotator"), ("Target", "rotator"), ("DeltaTime", F32), ("HalfLife", F32)], "rotator",
            lambda a: R.damper_exact_rotation(a["Current"], a["Target"], a["DeltaTime"], a["HalfLife"]),
            note="Per axis like DamperExactAngle (vector path). If every |normalized delta| <= 1e-4 the function returns Target. Rotators are [pitch, yaw, roll]. Alpha is a float32 value used as a double.")
    rng = rng_for("DamperExactRotation")
    for _ in range(80):
        fn.add("random:normalized", Current=rot_random(rng), Target=rot_random(rng), DeltaTime=LU(rng, 1e-4, 0.1), HalfLife=U(rng, 0, 2))
    for _ in range(30):
        fn.add("random:wide", Current=rot_random(rng, True), Target=rot_random(rng, True), DeltaTime=f32(1 / 60.0), HalfLife=LU(rng, 1e-3, 2))
    for why, c, t, dt, hl in [
        ("equal", [10.0, 20.0, 30.0], [10.0, 20.0, 30.0], dt60, 0.2),
        ("delta_5e-5_returns_target", r0, [f32(5e-5), 0.0, 0.0], dt60, 0.2), ("delta_1.1e-4", r0, [f32(1.1e-4), 0.0, 0.0], dt60, 0.2),
        ("yaw_170_to_-170", [0.0, 170.0, 0.0], [0.0, -170.0, 0.0], dt60, 0.2), ("yaw_0_to_180", r0, [0.0, 180.0, 0.0], dt60, 0.2),
        ("yaw_0_to_175.001", r0, [0.0, f32(175.001), 0.0], dt60, 0.2), ("halflife_zero", r0, [30.0, 60.0, 90.0], dt60, 0.0),
        ("halflife_negative", r0, [30.0, 60.0, 90.0], dt60, -0.5), ("dt_zero", r0, [30.0, 60.0, 90.0], 0.0, 0.2),
        ("dt_huge", r0, [30.0, 60.0, 90.0], 100.0, 0.2), ("all_axes_180", r0, [180.0, 180.0, 180.0], dt60, 0.2),
        ("from_540", [0.0, 540.0, 0.0], r0, dt60, 0.2),
    ]:
        fn.add(E(why), Current=c, Target=t, DeltaTime=dt, HalfLife=hl)
    fn.add(E("double_precision_inputs"), Current=[0.1, 0.2, 0.3], Target=[10.123456789012345, -20.7, 30.1], DeltaTime=dt60, HalfLife=0.2)

    # ---- InterpolateQuaternionFast ----
    fn = Fn(F, "UAlsRotation::InterpolateQuaternionFast", "AlsRotation.interpolate_quaternion_fast",
            "FQuat InterpolateQuaternionFast(const FQuat& Current, const FQuat& Target, float DeltaTime, float Speed)",
            [("Current", "quat"), ("Target", "quat"), ("DeltaTime", F32), ("Speed", F32)], "quat",
            lambda a: R.interpolate_quaternion_fast(a["Current"], a["Target"], a["DeltaTime"], a["Speed"]),
            note="Speed <= 0 or Current.Equals(Target) (tolerance 1e-4 per component) returns Target; else GetNormalized(FastLerp(Current, Target, Clamp01(Speed*DeltaTime))). "
                 "FastLerp flips the sign of Current's weight when Dot < 0 (shortest route). Quats are [x,y,z,w].")
    rng = rng_for("InterpolateQuaternionFast")
    qid = [0.0, 0.0, 0.0, 1.0]
    for _ in range(90):
        fn.add("random", Current=unit_quat(rng), Target=unit_quat(rng), DeltaTime=LU(rng, 1e-4, 0.1), Speed=U(rng, 0, 40))
    for _ in range(30):
        fn.add("random:alpha_in_0_1", Current=unit_quat(rng), Target=unit_quat(rng), DeltaTime=f32(1 / 60.0), Speed=U(rng, 1, 60))
    q90z = [0.0, 0.0, f32(math.sqrt(0.5)), f32(math.sqrt(0.5))]
    q180z = [0.0, 0.0, 1.0, 0.0]
    for why, c, t, dt, sp in [
        ("speed_zero_returns_target", qid, q90z, dt60, 0.0), ("speed_negative", qid, q90z, dt60, -5.0), ("speed_neg_zero", qid, q90z, dt60, -0.0),
        ("equal_returns_target", q90z, q90z, dt60, 10.0),
        ("nearly_equal_5e-5", qid, [5e-5, 0.0, 0.0, 1.0], dt60, 10.0), ("not_equal_1.5e-4", qid, [1.5e-4, 0.0, 0.0, 1.0], dt60, 10.0),
        ("q_vs_minus_q", q90z, [-q90z[0], -q90z[1], -q90z[2], -q90z[3]], dt60, 10.0),
        ("identity_to_180", qid, q180z, dt60, 10.0), ("identity_to_180_alpha_half", qid, q180z, 0.05, 10.0),
        ("alpha_exactly_one", qid, q90z, 0.1, 10.0), ("alpha_above_one_clamped", qid, q90z, 1.0, 10.0), ("alpha_zero_dt_zero", qid, q90z, 0.0, 10.0),
        ("dt_negative", qid, q90z, -dt60, 10.0), ("speed_huge", qid, q90z, dt60, 1e9),
        ("unnormalized_current", [0.0, 0.0, 0.0, 2.0], q90z, dt60, 10.0), ("unnormalized_target", qid, [0.0, 0.0, 0.0, 3.0], dt60, 10.0),
        ("zero_quat_current", [0.0, 0.0, 0.0, 0.0], q90z, dt60, 10.0), ("zero_quat_target", qid, [0.0, 0.0, 0.0, 0.0], dt60, 10.0),
        ("antipodal_blend_cancels", [0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 0.0, -1.0], 0.05, 10.0),
    ]:
        fn.add(E(why), Current=c, Target=t, DeltaTime=dt, Speed=sp)

    # ---- GetTwist ----
    fn = Fn(F, "UAlsRotation::GetTwist", "AlsRotation.get_twist",
            "FQuat GetTwist(const FQuat& Quaternion, const FVector& TwistAxis = FVector::UpVector)",
            [("Quaternion", "quat"), ("TwistAxis", "vec3")], "quat", lambda a: R.get_twist(a["Quaternion"], a["TwistAxis"]),
            note="Projects the vector part of the quaternion on TwistAxis and normalizes (TwistAxis is not normalized by the function; the default axis is the up vector). Quats are [x,y,z,w].")
    rng = rng_for("GetTwist")
    up = [0.0, 0.0, 1.0]
    for _ in range(60):
        fn.add("random:up_axis", Quaternion=unit_quat(rng), TwistAxis=up)
    for _ in range(30):
        fn.add("random:down_axis", Quaternion=unit_quat(rng), TwistAxis=[0.0, 0.0, -1.0])
    for _ in range(40):
        fn.add("random:random_unit_axis", Quaternion=unit_quat(rng), TwistAxis=unit3(rng))
    for _ in range(20):
        ax = unit3(rng)
        ang = U(rng, -math.pi, math.pi)
        s, c = math.sin(ang / 2), math.cos(ang / 2)
        fn.add("random:pure_twist_about_axis", Quaternion=[f32(ax[0] * s), f32(ax[1] * s), f32(ax[2] * s), f32(c)], TwistAxis=ax)
    for why, q, ax in [
        ("identity", qid, up), ("pure_swing_90_about_x", [f32(math.sqrt(0.5)), 0.0, 0.0, f32(math.sqrt(0.5))], up),
        ("twist_180_about_z", q180z, up), ("minus_q180z", [0.0, 0.0, -1.0, 0.0], up),
        ("w_zero_swing_180_about_x", [1.0, 0.0, 0.0, 0.0], up), ("w_zero_pure_x_axis_x", [1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0]),
        ("zero_axis", [0.1, 0.2, 0.3, 0.9], [0.0, 0.0, 0.0]), ("non_unit_axis_2z", [0.1, 0.2, 0.3, 0.9], [0.0, 0.0, 2.0]),
        ("unnormalized_quaternion", [0.0, 0.0, 2.0, 2.0], up), ("zero_quaternion", [0.0, 0.0, 0.0, 0.0], up),
        ("tiny_twist_1e-5", [0.0, 0.0, 1e-5, 1.0], up), ("tiny_twist_1e-3_swing", [0.5, 0.5, 1e-3, 0.5], up),
        ("axis_diagonal_not_unit", [0.1, 0.2, 0.3, 0.9], [1.0, 1.0, 1.0]), ("negative_w", [0.0, 0.0, 0.5, -0.8660254037844386], up),
        ("projection_below_normalize_tolerance", [0.0, 0.0, 1e-5, 1e-5], up),
    ]:
        fn.add(E(why), Quaternion=q, TwistAxis=ax)


# =====================================================================================================================
# UAlsVector
# =====================================================================================================================
def gen_als_vector():
    F = "als_vector.json"

    # ---- ClampMagnitude01 (FVector) ----
    fn = Fn(F, "UAlsVector::ClampMagnitude01(FVector)", "AlsVector.clamp_magnitude01", "FVector ClampMagnitude01(const FVector& Vector)",
            [("Vector", "vec3")], "vec3", lambda a: V.clamp_magnitude01(a["Vector"]),
            note="SizeSquared <= 1 returns the vector; else Vector * InvSqrt(SizeSquared) in double precision. The FVector3f overload is not exposed.")
    rng = rng_for("ClampMagnitude01")
    for _ in range(80):
        fn.add("random:length_0_3", Vector=scaled(unit3(rng), U(rng, 0, 3)))
    for _ in range(30):
        fn.add("random:length_below_1", Vector=scaled(unit3(rng), U(rng, 0, 0.99)))
    for _ in range(20):
        fn.add("random:length_near_1", Vector=scaled(unit3(rng), U(rng, 0.999, 1.001)))
    for _ in range(20):
        fn.add("random:axis_aligned", Vector=[U(rng, -3, 3), 0.0, 0.0] if rng.random() < 0.5 else [0.0, U(rng, -3, 3), U(rng, -3, 3)])
    for why, v in [("zero", [0.0, 0.0, 0.0]), ("negative_zero", [-0.0, -0.0, -0.0]), ("unit_x", [1.0, 0.0, 0.0]), ("size_squared_1_plus_2.2e-16", [1.0, 1.4901161193847656e-08, 0.0]),
                   ("size_squared_just_above_1", [1.0, 1e-7, 0.0]), ("size_squared_just_below_1", [f32_step(1.0, False), 0.0, 0.0]),
                   ("(3,4,0)", [3.0, 4.0, 0.0]), ("(1,1,1)", [1.0, 1.0, 1.0]), ("tiny", [1e-20, 1e-20, 1e-20]), ("denormal", [FLT_TRUE_MIN, 0.0, 0.0]),
                   ("huge_1e19", [1e19, 0.0, 0.0]), ("huge_1e30", [1e30, 1e30, 1e30]), ("huge_1e154", [1e154, 0.0, 0.0]),
                   ("huge_1e155_size_squared_overflows_double", [1e155, 0.0, 0.0]), ("huge_1e200", [1e200, 1e200, 0.0]),
                   ("double_precision_(0.6,0.8,0.0000001)", [0.6, 0.8, 1e-7]), ("double_precision_unit_plus_eps", [1.0000000001, 0.0, 0.0])]:
        fn.add(E(why), Vector=v)

    # ---- ClampMagnitude012D ----
    fn = Fn(F, "UAlsVector::ClampMagnitude012D", "AlsVector.clamp_magnitude012d", "FVector2D ClampMagnitude012D(FVector2D Vector)",
            [("Vector", "vec2")], "vec2", lambda a: V.clamp_magnitude012d(a["Vector"]),
            note="Same as ClampMagnitude01 for FVector2D (double).")
    rng = rng_for("ClampMagnitude012D")
    for _ in range(100):
        fn.add("random:length_0_3", Vector=scaled(unit2(rng), U(rng, 0, 3)))
    for _ in range(40):
        fn.add("random:length_near_1", Vector=scaled(unit2(rng), U(rng, 0.999, 1.001)))
    for _ in range(20):
        fn.add("random:square", Vector=[U(rng, -1.2, 1.2), U(rng, -1.2, 1.2)])
    for why, v in [("zero", [0.0, 0.0]), ("negative_zero", [-0.0, -0.0]), ("unit_x", [1.0, 0.0]), ("(3,4)", [3.0, 4.0]), ("(1,1)", [1.0, 1.0]),
                   ("(0.7071,0.7071)", [0.7071, 0.7071]), ("size_squared_just_above_1", [1.0, 1e-7]), ("tiny", [1e-20, 1e-20]),
                   ("huge_1e30", [1e30, 1e30]), ("huge_1e155", [1e155, 0.0]), ("double_precision_unit_plus_eps", [1.0000000001, 0.0])]:
        fn.add(E(why), Vector=v)

    # ---- RadianToDirection / RadianToDirectionXY / AngleToDirection / AngleToDirectionXY ----
    two_pi = 2 * math.pi
    rad_specials = [0.0, -0.0] + [f32(k * math.pi / 2) for k in range(-4, 5) if k] + [f32(math.pi / 4), f32(-math.pi / 4), f32(two_pi), f32(-two_pi),
                                                                                    f32(3 * math.pi), f32(100 * math.pi), 1e6, -1e6, 1e-7, -1e-7, 1e-30, 1e7]
    ang_specials = [0.0, -0.0, 45.0, 90.0, 135.0, 180.0, 270.0, 360.0, 450.0, 540.0, -45.0, -90.0, -135.0, -180.0, -270.0, -360.0, -540.0,
                    30.0, 60.0, 1e-5, 89.999992, 90.000008, 179.99998, 180.00002, 1e5, -1e5, 1e7]
    for name, py, call, kind, sig in [
        ("RadianToDirection", "radian_to_direction", lambda a: V.radian_to_direction(a["Radian"]), "vec2", "FVector2D RadianToDirection(float Radian)"),
        ("RadianToDirectionXY", "radian_to_direction_xy", lambda a: V.radian_to_direction_xy(a["Radian"]), "vec3", "FVector RadianToDirectionXY(float Radian)")]:
        fn = Fn(F, "UAlsVector::" + name, "AlsVector." + py, sig, [("Radian", F32)], kind, call,
                note="{cos, sin} from FMath::SinCos(float): the engine's own range reduction + polynomial, NOT libm. The result components are float32 values stored as doubles; Z (XY variant) is 0.")
        rng = rng_for(name)
        for _ in range(150):
            fn.add("random", Radian=U(rng, -2 * two_pi, 2 * two_pi))
        for v in rad_specials:
            fn.add(E("special_radian"), Radian=v)
        fn.add(E("nan"), Radian=NAN)
        fn.add(E("inf"), Radian=INF)
    for name, py, call, kind, sig in [
        ("AngleToDirection", "angle_to_direction", lambda a: V.angle_to_direction(a["Angle"]), "vec2", "FVector2D AngleToDirection(float Angle)"),
        ("AngleToDirectionXY", "angle_to_direction_xy", lambda a: V.angle_to_direction_xy(a["Angle"]), "vec3", "FVector AngleToDirectionXY(float Angle)")]:
        fn = Fn(F, "UAlsVector::" + name, "AlsVector." + py, sig, [("Angle", F32)], kind, call,
                note="Angle is converted with FMath::DegreesToRadians(float) (float multiply by PI/180) then SinCos as above. Result {cos, sin[, 0]}.")
        rng = rng_for(name)
        for _ in range(150):
            fn.add("random", Angle=U(rng, -540, 540))
        for v in ang_specials:
            fn.add(E("special_angle"), Angle=v)
        fn.add(E("nan"), Angle=NAN)

    # ---- DirectionToAngle / DirectionToAngleXY ----
    dirs = [("+x", [1.0, 0.0]), ("-x", [-1.0, 0.0]), ("+y", [0.0, 1.0]), ("-y", [0.0, -1.0]), ("zero", [0.0, 0.0]), ("neg_zero_x_pos_zero_y", [-1.0, 0.0]),
            ("-x_neg_zero_y", [-1.0, -0.0]), ("zero_neg_zero_y", [0.0, -0.0]), ("neg_zero_zero_y", [-0.0, 0.0]), ("neg_zero_neg_zero", [-0.0, -0.0]),
            ("(1,1)", [1.0, 1.0]), ("(-1,1)", [-1.0, 1.0]), ("(-1,-1)", [-1.0, -1.0]), ("(1,-1)", [1.0, -1.0]), ("tiny_1e-30", [1e-30, 1e-30]),
            ("huge_1e30", [1e30, -1e30]), ("(1,1e-7)", [1.0, 1e-7]), ("(-1,1e-7)", [-1.0, 1e-7]), ("(-1,-1e-7)", [-1.0, -1e-7]), ("(1e-7,1)", [1e-7, 1.0]),
            ("(3,4)", [3.0, 4.0]), ("(0.5,0.8660254037844386)_double", [0.5, 0.8660254037844386]), ("(1e300,1e300)", [1e300, 1e300]),
            ("(1e-300,1e-300)", [1e-300, 1e-300]), ("(1e-300,0)", [1e-300, 0.0])]
    fn = Fn(F, "UAlsVector::DirectionToAngle", "AlsVector.direction_to_angle", "double DirectionToAngle(FVector2D Direction)",
            [("Direction", "vec2")], F64, lambda a: V.direction_to_angle(a["Direction"]),
            note="RadiansToDegrees(FMath::Atan2(Y, X)) in double. On all random inputs FMath::Atan2(double) equals the C library atan2 bit for bit, but it does NOT honour the sign of a zero: "
                 "(Y=-0, X<0) gives +180 (libm: -180), and (+-0, +-0) gives 0 (libm: +-0 or +-180).")
    rng = rng_for("DirectionToAngle")
    for _ in range(100):
        fn.add("random:unit", Direction=unit2(rng))
    for _ in range(50):
        fn.add("random:any_length", Direction=[U(rng, -10, 10), U(rng, -10, 10)])
    for n, d in dirs:
        fn.add(E(n), Direction=d)
    fn = Fn(F, "UAlsVector::DirectionToAngleXY", "AlsVector.direction_to_angle_xy", "double DirectionToAngleXY(const FVector& Direction)",
            [("Direction", "vec3")], F64, lambda a: V.direction_to_angle_xy(a["Direction"]),
            note="Same as DirectionToAngle on (X, Y); Z is ignored.")
    rng = rng_for("DirectionToAngleXY")
    for _ in range(100):
        fn.add("random:unit_xy", Direction=unit2(rng) + [0.0])
    for _ in range(50):
        fn.add("random:any", Direction=vec_range(rng, -10, 10))
    for n, d in dirs:
        fn.add(E(n), Direction=d + [0.0])
    fn.add(E("z_ignored"), Direction=[1.0, 1.0, 100.0])
    fn.add(E("z_only"), Direction=[0.0, 0.0, 5.0])

    # ---- Perpendicular XY ----
    for name, py, call, doc in [
        ("PerpendicularClockwiseXY", "perpendicular_clockwise_xy", lambda a: V.perpendicular_clockwise_xy(a["Vector"]), "(Y, -X, Z)"),
        ("PerpendicularCounterClockwiseXY", "perpendicular_counter_clockwise_xy", lambda a: V.perpendicular_counter_clockwise_xy(a["Vector"]), "(-Y, X, Z)")]:
        fn = Fn(F, "UAlsVector::" + name, "AlsVector." + py, "FVector %s(const FVector& Vector)" % name, [("Vector", "vec3")], "vec3", call,
                note="Returns %s (no normalization). Signed zeros are kept, e.g. -X of +0.0 is -0.0." % doc)
        rng = rng_for(name)
        for _ in range(100):
            fn.add("random", Vector=vec_range(rng, -100, 100))
        for why, v in [("zero", [0.0, 0.0, 0.0]), ("negative_zero", [-0.0, -0.0, -0.0]), ("unit_x", [1.0, 0.0, 0.0]), ("unit_y", [0.0, 1.0, 0.0]),
                       ("unit_z", [0.0, 0.0, 1.0]), ("neg_x", [-1.0, 0.0, 0.0]), ("huge", [1e30, -1e30, 1e30]), ("tiny", [1e-30, -1e-30, 1e-30]),
                       ("double_precision", [0.1, 0.2, 0.3]), ("mixed_zero_signs", [0.0, -0.0, -0.0])]:
            fn.add(E(why), Vector=v)

    # ---- AngleBetweenSkipNormalization ----
    fn = Fn(F, "UAlsVector::AngleBetweenSkipNormalization", "AlsVector.angle_between_skip_normalization",
            "double AngleBetweenSkipNormalization(const FVector& From, const FVector& To)", [("From", "vec3"), ("To", "vec3")], F64,
            lambda a: V.angle_between_skip_normalization(a["From"], a["To"]),
            note="RadiansToDegrees(Acos(From | To)) in double; Acos clamps its argument to [-1, 1]. The inputs are expected to be unit vectors (not normalized by the function), result in [0, 180].")
    rng = rng_for("AngleBetweenSkipNormalization")
    for _ in range(110):
        fn.add("random:unit_vectors", From=unit3(rng), To=unit3(rng))
    for _ in range(30):
        a = unit3(rng)
        fn.add("random:nearly_parallel", From=a, To=[f32(c + rng.uniform(-1e-3, 1e-3)) for c in a])
    for _ in range(20):
        a = unit3(rng)
        fn.add("random:nearly_opposite", From=a, To=[f32(-c + rng.uniform(-1e-3, 1e-3)) for c in a])
    for _ in range(10):
        fn.add("random:non_unit", From=vec_range(rng, -3, 3), To=vec_range(rng, -3, 3))
    ex, ey, ez = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]
    for why, f, t in [("same_axis_x", ex, ex), ("opposite_axis_x", ex, [-1.0, 0.0, 0.0]), ("perpendicular_xy", ex, ey), ("perpendicular_xz", ex, ez),
                      ("zero_from", [0.0, 0.0, 0.0], ex), ("zero_both", [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]), ("dot_above_one_clamped", [2.0, 0.0, 0.0], [2.0, 0.0, 0.0]),
                      ("dot_below_minus_one_clamped", [2.0, 0.0, 0.0], [-2.0, 0.0, 0.0]), ("dot_1ulp_below_one", [f32_step(1.0, False), 0.0, 0.0], ex),
                      ("dot_just_above_one_1e-7", [1.0000001192092896, 0.0, 0.0], ex), ("tiny_angle_1e-4_rad", ex, [f32(math.cos(1e-4)), f32(math.sin(1e-4)), 0.0]),
                      ("tiny_angle_1e-6_rad", ex, [f32(math.cos(1e-6)), f32(math.sin(1e-6)), 0.0]), ("45deg", ex, [f32(math.sqrt(0.5)), f32(math.sqrt(0.5)), 0.0]),
                      ("179.9deg", ex, [f32(math.cos(math.radians(179.9))), f32(math.sin(math.radians(179.9))), 0.0]),
                      ("double_precision_unit", [0.6, 0.8, 0.0], [0.8, 0.6, 0.0]), ("huge_dot_1e30", [1e15, 0.0, 0.0], [1e15, 0.0, 0.0])]:
        fn.add(E(why), From=f, To=t)

    # ---- AngleBetweenSignedXY ----
    fn = Fn(F, "UAlsVector::AngleBetweenSignedXY", "AlsVector.angle_between_signed_xy", "float AngleBetweenSignedXY(FVector3f From, FVector3f To)",
            [("From", "vec3f"), ("To", "vec3f")], F32, lambda a: V.angle_between_signed_xy(a["From"], a["To"]),
            note="Python type: unreal.Vector3f (float32 components). Both vectors are projected to XY and FVector2f::Normalize() is applied: if |v|^2 <= UE_SMALL_NUMBER (1e-8) the 2D vector "
                 "is SET TO ZERO (so a zero / tiny vector gives dot 0 and cross 0, result 0); result = RadiansToDegrees(Acos(FromXY | ToXY)) * FMath::Sign(FromXY ^ ToXY). FMath::Sign(0) is 0, so exactly parallel AND exactly opposite vectors "
                 "return 0 (antiparallel gives 0, not +-180).")
    rng = rng_for("AngleBetweenSignedXY")
    for _ in range(80):
        fn.add("random:unit_xy_random_z", From=unit2(rng) + [U(rng, -1, 1)], To=unit2(rng) + [U(rng, -1, 1)])
    for _ in range(50):
        fn.add("random:any_length", From=vec_range(rng, -10, 10), To=vec_range(rng, -10, 10))
    for _ in range(20):
        a = rng.uniform(-math.pi, math.pi)
        d = rng.uniform(-1e-2, 1e-2)
        fn.add("random:small_angle", From=[f32(math.cos(a)), f32(math.sin(a)), 0.0], To=[f32(math.cos(a + d)), f32(math.sin(a + d)), 0.0])
    for _ in range(10):
        a = rng.uniform(-math.pi, math.pi)
        d = math.pi + rng.uniform(-1e-2, 1e-2)
        fn.add("random:near_opposite", From=[f32(math.cos(a)), f32(math.sin(a)), 0.0], To=[f32(math.cos(a + d)), f32(math.sin(a + d)), 0.0])
    Z3 = [0.0, 0.0, 0.0]
    for why, f, t in [
        ("x_to_y_ccw_90", ex, ey), ("y_to_x_cw_-90", ey, ex), ("same_vector_zero", ex, ex), ("exactly_opposite_returns_0", ex, [-1.0, 0.0, 0.0]),
        ("opposite_diagonal_returns_0", [1.0, 1.0, 0.0], [-1.0, -1.0, 0.0]), ("zero_from", Z3, ey), ("zero_to", ex, Z3), ("zero_both", Z3, Z3),
        ("z_only_from", ez, ex), ("z_only_both", ez, ez), ("z_ignored", [1.0, 0.0, 5.0], [0.0, 1.0, -5.0]),
        ("tiny_from_below_normalize_tolerance_1e-5", [1e-5, 0.0, 0.0], ey), ("tiny_both_1e-5", [1e-5, 0.0, 0.0], [0.0, 1e-5, 0.0]),
        ("from_size_1e-4_boundary", [1e-4, 0.0, 0.0], ey), ("from_size_1.01e-4_just_normalized", [f32(1.01e-4), 0.0, 0.0], ey),
        ("from_size_9.9e-5_not_normalized", [f32(9.9e-5), 0.0, 0.0], ey), ("huge_1e20_size_squared_overflows_float", [1e20, 0.0, 0.0], ey),
        ("huge_1e18", [1e18, 0.0, 0.0], [0.0, 1e18, 0.0]), ("45deg", ex, [1.0, 1.0, 0.0]), ("135deg", ex, [-1.0, 1.0, 0.0]),
        ("-135deg", ex, [-1.0, -1.0, 0.0]), ("179.99deg_ccw", ex, [f32(math.cos(math.radians(179.99))), f32(math.sin(math.radians(179.99))), 0.0]),
        ("179.99deg_cw", ex, [f32(math.cos(math.radians(179.99))), f32(-math.sin(math.radians(179.99))), 0.0]),
        ("1e-4_rad_ccw", ex, [f32(math.cos(1e-4)), f32(math.sin(1e-4)), 0.0]), ("1e-4_rad_cw", ex, [f32(math.cos(1e-4)), f32(-math.sin(1e-4)), 0.0]),
        ("3e-4_rad_ccw_acos_precision", ex, [f32(math.cos(3e-4)), f32(math.sin(3e-4)), 0.0]),
        ("1e-6_rad_acos_precision", ex, [f32(math.cos(1e-6)), f32(math.sin(1e-6)), 0.0]),
        ("nan_from", [NAN, 0.0, 0.0], ey),
    ]:
        fn.add(E(why), From=f, To=t)

    # ---- SlerpSkipNormalization ----
    fn = Fn(F, "UAlsVector::SlerpSkipNormalization", "AlsVector.slerp_skip_normalization",
            "FVector SlerpSkipNormalization(const FVector& From, const FVector& To, float Ratio)", [("From", "vec3"), ("To", "vec3"), ("Ratio", F32)], "vec3",
            lambda a: V.slerp_skip_normalization(a["From"], a["To"], a["Ratio"]),
            note="Dot > 0.9995 -> GetSafeNormal(Lerp(From, To, Ratio)); else Theta = Acos(max(-1, Dot)) * Ratio (float), result = From * cos(Theta) + Normalize(To - From * Dot) * sin(Theta). "
                 "Inputs are expected to be unit vectors. For (nearly) opposite vectors the perpendicular is zero/degenerate.")
    rng = rng_for("SlerpSkipNormalization")
    for _ in range(80):
        fn.add("random:unit_vectors", From=unit3(rng), To=unit3(rng), Ratio=U(rng, 0, 1))
    for _ in range(16):
        a = unit3(rng)
        fn.add("random:nearly_parallel", From=a, To=[f32(c + rng.uniform(-1e-2, 1e-2)) for c in a], Ratio=U(rng, 0, 1))
    for _ in range(12):
        a = unit3(rng)
        fn.add("random:nearly_opposite", From=a, To=[f32(-c + rng.uniform(-1e-2, 1e-2)) for c in a], Ratio=U(rng, 0, 1))
    for _ in range(12):
        fn.add("random:ratio_outside_0_1", From=unit3(rng), To=unit3(rng), Ratio=U(rng, -0.5, 1.5))
    for _ in range(10):
        fn.add("random:non_unit", From=vec_range(rng, -3, 3), To=vec_range(rng, -3, 3), Ratio=U(rng, 0, 1))
    for why, f, t, r in [
        ("ratio_zero", ex, ey, 0.0), ("ratio_one", ex, ey, 1.0), ("ratio_half_90deg", ex, ey, 0.5), ("opposite_ratio_half", ex, [-1.0, 0.0, 0.0], 0.5),
        ("opposite_ratio_zero", ex, [-1.0, 0.0, 0.0], 0.0), ("opposite_ratio_one", ex, [-1.0, 0.0, 0.0], 1.0), ("same_vector", ex, ex, 0.5),
        ("zero_from", [0.0, 0.0, 0.0], ey, 0.5), ("zero_to", ex, [0.0, 0.0, 0.0], 0.5), ("zero_both", [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], 0.5),
        ("dot_exactly_0.9995_lerp_branch_boundary", ex, [0.9995, f32(math.sqrt(1 - 0.9995 ** 2)), 0.0], 0.5),
        ("dot_just_above_0.9995", ex, [0.99951, f32(math.sqrt(1 - 0.99951 ** 2)), 0.0], 0.5),
        ("dot_just_below_0.9995", ex, [0.99949, f32(math.sqrt(1 - 0.99949 ** 2)), 0.0], 0.5),
        ("ratio_2_extrapolate", ex, ey, 2.0), ("ratio_-1_extrapolate", ex, ey, -1.0), ("ratio_nan", ex, ey, NAN),
        ("parallel_lerp_branch_opposite_scaling", [2.0, 0.0, 0.0], [4.0, 0.0, 0.0], 0.5), ("non_unit_perpendicular", [2.0, 0.0, 0.0], [0.0, 3.0, 0.0], 0.5),
        ("dot_below_minus_one", [2.0, 0.0, 0.0], [-2.0, 0.0, 0.0], 0.5), ("tiny_vectors", [1e-20, 0.0, 0.0], [0.0, 1e-20, 0.0], 0.5),
        ("double_precision_inputs", [0.6, 0.8, 0.0], [0.0, 0.6, 0.8], 0.3),
    ]:
        fn.add(E(why), From=f, To=t, Ratio=r)

    gen_spring_vector(F)


# =====================================================================================================================
# UAlsUtility
# =====================================================================================================================
def gen_als_utility():
    F = "als_utility.json"
    fn = Fn(F, "UAlsUtility::NameToDisplayString", "AlsUtility.name_to_display_string", "FString NameToDisplayString(FName Name, bool bNameIsBool)",
            [("Name", "name"), ("bNameIsBool", "bool")], "str", lambda a: AU.name_to_display_string(a["Name"], a["bNameIsBool"]),
            note="Wraps FName::NameToDisplayString(Name.ToString(), bNameIsBool). FName is case-insensitive: no two names that differ only in case are used.")
    names = ["bIsFooBar", "bUseHandIkBones", "bHasInput", "b", "bb", "bA", "MaxWalkSpeed", "VelocityBlend", "RotationYawOffsets", "AlsGaitWalking",
             "HTTPRequest", "ViewYawAngleThreshold", "Turn180AngleThreshold", "foot_left_ik", "SpineYaw2Angle", "GaitAmount", "bStateValid", "A", "AB",
             "ABc", "aBC", "Als.Gait.Walking", "Foot_L", "None", "XMLParserBase64", "Mantle3DHigh", "ForwardHalfAngle70", "bIsXML", "bURL", "b2D",
             "Left-Right", "LeftHand_IK", "x", "ID", "UserIDs", "OneTwoThree", "ABC_DEF", "bAIControlled", "bNaNValue", "ThighAngleLimit"]
    for n in names:
        for b in (False, True):
            fn.add("fixed", Name=n, bNameIsBool=b)
    fn.add(E("empty_name"), Name="", bNameIsBool=False)
    fn.add(E("empty_name_bool"), Name="", bNameIsBool=True)
    fn.add(E("spaces"), Name="Already Spaced Name", bNameIsBool=False)

    tag_names = ["Als", "Als.ViewMode", "Als.ViewMode.FirstPerson", "Als.ViewMode.ThirdPerson", "Als.LocomotionMode", "Als.LocomotionMode.Grounded",
                 "Als.LocomotionMode.InAir", "Als.RotationMode", "Als.RotationMode.VelocityDirection", "Als.RotationMode.ViewDirection",
                 "Als.RotationMode.Aiming", "Als.Stance", "Als.Stance.Standing", "Als.Stance.Crouching", "Als.Gait", "Als.Gait.Walking",
                 "Als.Gait.Running", "Als.Gait.Sprinting", "Als.OverlayMode", "Als.OverlayMode.Default", "Als.OverlayMode.Rifle", "Als.LocomotionAction",
                 "Als.LocomotionAction.Mantling", "Als.LocomotionAction.Rolling", "Als.LocomotionAction.Ragdolling", "Als.LocomotionAction.GettingUp",
                 "Als.GroundedEntryMode", "Als.GroundedEntryMode.FromRoll"]
    fn = Fn(F, "UAlsUtility::GetSimpleTagName", "AlsUtility.get_simple_tag_name", "FName GetSimpleTagName(FGameplayTag Tag)",
            [("Tag", "tag")], "name", lambda a: AU.get_simple_tag_name(a["Tag"]),
            note="The last tag segment, or the string \"None\" for an unregistered / empty tag. The tag is built from its name with FGameplayTag::ImportText.")
    for t in tag_names:
        fn.add("fixed", Tag=t)
    for why, t in [("unregistered_tag", "Bogus.Tag"), ("unregistered_child_of_registered", "Als.Gait.Crawling"), ("prefix_only_case_differs", "als.gait.walking")]:
        fn.add(E(why), Tag=t)
    fn.add(E("empty_tag"), Tag="")

    fn = Fn(F, "UAlsUtility::GetChildTags", "AlsUtility.get_child_tags", "FGameplayTagContainer GetChildTags(FGameplayTag Tag)",
            [("Tag", "tag")], "tags", lambda a: AU.get_child_tags(a["Tag"]),
            note="All registered descendants (not the tag itself) as tag name strings, in the order UE returned them (compare as a set: the order is the tag tree's, reverse alphabetical per level in this run).")
    for t in tag_names:
        fn.add("fixed", Tag=t)
    for why, t in [("unregistered_tag", "Bogus.Tag")]:
        fn.add(E(why), Tag=t)
    fn.add(E("empty_tag"), Tag="")
    # one more: sort-stable copy for set comparison
    for c in fn.cases:
        if isinstance(c.get("out"), list):
            c["out_sorted"] = sorted(c["out"])


# =====================================================================================================================
# UAlsMontageUtility
# =====================================================================================================================
MONTAGE_PATHS = [
    "/ALS/ALS/Animations/Actions/Mantle/AM_Als_Mantle_High", "/ALS/ALS/Animations/Actions/Mantle/AM_Als_Mantle_Low",
    "/ALS/ALS/Animations/Actions/Mantle/AM_Als_Mantle_Low_BothArms", "/ALS/ALS/Animations/Actions/Mantle/AM_Als_Mantle_Low_Box",
    "/ALS/ALS/Animations/Actions/Mantle/AM_Als_Mantle_Low_Left", "/ALS/ALS/Animations/Actions/Mantle/AM_Als_Mantle_Low_Right",
    "/ALS/ALS/Animations/Actions/Roll/AM_Als_Roll", "/ALS/ALS/Animations/Actions/GetUp/AM_Als_GetUp_Back",
    "/ALS/ALS/Animations/Actions/GetUp/AM_Als_GetUp_Front",
    "/MoverExamples/Characters/Mannequins/Animations/Manny/VaultOver_Montage",
    "/MoverExamples/Characters/Mannequins/Animations/Manny/MM_Dash_Forward_Montage",
    "/MoverExamples/Characters/Mannequins/Animations/Manny/MM_Death_Front_03_ExtractedRM_Montage",
]


def _tf(t):
    return from_ue("transform", t)


def gen_als_montage():
    MU = unreal.AlsMontageUtility
    # discover (for the report): every AnimMontage the asset registry knows
    ar = unreal.AssetRegistryHelpers.get_asset_registry()
    ar.search_all_assets(True)
    flt = unreal.ARFilter(class_paths=[unreal.TopLevelAssetPath("/Script/Engine", "AnimMontage")], recursive_classes=True)
    found = sorted(str(a.package_name) for a in ar.get_assets(flt))
    montages = []
    skipped = []
    for p in MONTAGE_PATHS:
        mt = unreal.load_asset(p)
        if mt is None:
            skipped.append({"path": p, "reason": "asset not found"})
            continue
        try:
            slots = mt.get_editor_property("slot_anim_tracks")
            segs = slots[0].get_editor_property("anim_track").get_editor_property("anim_segments") if len(slots) else []
            if len(slots) == 0 or len(segs) == 0:
                skipped.append({"path": p, "reason": "montage has no slot track / no segment (ALS_ENSURE would fire)"})
                continue
        except Exception as exc:
            skipped.append({"path": p, "reason": "cannot inspect slots: %s" % exc})
            continue
        length = float(mt.get_play_length())
        entry = {"montage": p, "play_length": length, "slot_name": str(slots[0].get_editor_property("slot_name")), "segments": [],
                 "root_transform_samples": [], "last_root_transform": None, "root_motion_ranges": []}
        for s in segs:
            entry["segments"].append({"anim": s.get_editor_property("anim_reference").get_path_name(), "start_pos": float(s.get_editor_property("start_pos")),
                                      "anim_start_time": float(s.get_editor_property("anim_start_time")),
                                      "anim_end_time": float(s.get_editor_property("anim_end_time")),
                                      "play_rate": float(s.get_editor_property("anim_play_rate")), "looping_count": int(s.get_editor_property("looping_count"))})
        times = [f32(length * i / 15.0) for i in range(16)]
        times[0], times[15] = 0.0, f32(length)
        extra = [("edge:time_negative", -0.1), ("edge:time_beyond_length", f32(length + 0.5)), ("edge:time_just_below_length", f32_step(f32(length), False))]
        for i, t in enumerate(times):
            entry["root_transform_samples"].append({"Time": t, "out": _tf(MU.extract_root_transform_from_montage(mt, t)),
                                                    "tag": "sample_%d_of_15" % i})
        for tag, t in extra:
            entry["root_transform_samples"].append({"Time": t, "out": _tf(MU.extract_root_transform_from_montage(mt, t)), "tag": tag})
        entry["last_root_transform"] = _tf(MU.extract_last_root_transform_from_montage(mt))
        L = f32(length)
        ranges = [("full", 0.0, L), ("first_half", 0.0, f32(L / 2)), ("second_half", f32(L / 2), L), ("quarter_to_three_quarters", f32(L * 0.25), f32(L * 0.75)),
                  ("first_0.1_s", 0.0, f32(0.1)), ("0.1_to_0.2_s", f32(0.1), f32(0.2)), ("zero_length_range", f32(L / 2), f32(L / 2)),
                  ("edge:end_beyond_length", 0.0, f32(L + 1.0)), ("edge:start_negative", f32(-0.5), f32(L / 2)),
                  ("last_frame_step_1/30", f32(max(0.0, L - 1 / 30.0)), L)]
        for tag, a, b in ranges:
            entry["root_motion_ranges"].append({"StartTime": a, "EndTime": b, "out": _tf(MU.extract_root_motion_from_montage(mt, a, b)), "tag": tag})
        montages.append(entry)
    return {"function_list": [
        {"function": "UAlsMontageUtility::ExtractRootTransformFromMontage", "python": "AlsMontageUtility.extract_root_transform_from_montage",
         "signature": "FTransform ExtractRootTransformFromMontage(const UAnimMontage* Montage, float Time)", "params": ["Montage", "Time"],
         "param_types": {"Montage": "asset path", "Time": F32}, "returns": "transform"},
        {"function": "UAlsMontageUtility::ExtractLastRootTransformFromMontage", "python": "AlsMontageUtility.extract_last_root_transform_from_montage",
         "signature": "FTransform ExtractLastRootTransformFromMontage(const UAnimMontage* Montage)", "params": ["Montage"],
         "param_types": {"Montage": "asset path"}, "returns": "transform"},
        {"function": "UAlsMontageUtility::ExtractRootMotionFromMontage", "python": "AlsMontageUtility.extract_root_motion_from_montage",
         "signature": "FTransform ExtractRootMotionFromMontage(const UAnimMontage* Montage, float StartTime, float EndTime)", "params": ["Montage", "StartTime", "EndTime"],
         "param_types": {"Montage": "asset path", "StartTime": F32, "EndTime": F32}, "returns": "transform"}],
        "montages": montages, "skipped": skipped, "all_anim_montages_in_registry": found}


# =====================================================================================================================
# engine functions through unreal.MathLibrary (KismetMathLibrary): the ones the ALS C++ calls / the DAL builtins mirror
# =====================================================================================================================
ENGINE_NOTE = ("unreal.MathLibrary (UKismetMathLibrary) works in double precision for its 'float' pins, while the ALS C++ calls the float32 FMath overloads: "
               "compare these goldens with a tolerance (~1e-5 relative), not bit-exactly. Inputs are float32-representable values.")


# DAL built-in (dal/builtins.py) that mirrors the engine function, where there is one
DAL_BUILTIN = {
    "FRotator::NormalizeAxis": "normalize_axis", "FRotator::ClampAxis": "clamp_axis", "FMath::ClampAngle": "clamp_angle",
    "FMath::UnwindDegrees (via FVector::UnwindEuler)": "unwind_degrees", "FMath::FInterpTo": "interp_to (float)",
    "FMath::FInterpConstantTo": "interp_constant_to (float)", "FMath::VInterpTo": "interp_to (vec3)", "FMath::VInterpConstantTo": "interp_constant_to (vec3)",
    "FMath::Vector2DInterpTo": "interp_to (vec2)", "FMath::Vector2DInterpConstantTo": "interp_constant_to (vec2)", "FMath::RInterpTo": "interp_to (rotator)",
    "FMath::RInterpConstantTo": "interp_constant_to (rotator)", "FQuat::Slerp": "slerp", "FQuat::Inverse": "quat_inverse", "FQuat::Rotator": "to_rotator",
    "FRotator::Quaternion": "to_quat", "FQuat::RotateVector": "rotate (quat)", "FQuat::UnrotateVector": "unrotate (quat)", "FQuat::GetNormalized": "normalize (quat)",
    "FQuat::operator*": "compose / transform_rotation (quaternion product)", "FVector::ToOrientationRotator (Vector.Rotation())": "look_at (to - from)",
    "FRotationMatrix::MakeFromX(Target - Start).Rotator()": "look_at (compare: the DAL builtin uses ToOrientationRotator)", "FVector::ToOrientationQuat": "to_orientation_quat",
    "(A - B).GetNormalized()": "delta_rotator", "FMath::Sin": "sin", "FMath::Cos": "cos", "FMath::Tan": "tan", "FMath::Asin": "asin", "FMath::Acos": "acos",
    "FMath::Atan": "atan", "FMath::Atan2": "atan2", "FMath::Exp": "exp", "FMath::Loge": "log", "FMath::Sqrt": "sqrt", "FMath::Pow": "pow",
    "FMath::DegreesToRadians": "deg_to_rad", "FMath::RadiansToDegrees": "rad_to_deg", "FMath::Lerp": "lerp", "FMath::Clamp": "clamp",
    "FMath::GetMappedRangeValueClamped": "map_range_clamped", "FMath::GetMappedRangeValueUnclamped": "map_range", "FMath::GetRangePct": "range_pct",
    "FMath::Frac": "frac", "FMath::Sign": "sign", "FMath::Square": "square", "FMath::Abs": "abs", "FVector::GetSafeNormal": "normalize (vec3)",
    "FVector::GetClampedToMaxSize": "clamp_length", "FVector::Dist": "distance", "FVector::ProjectOnToNormal": "project_on_normal", "FVector::DotProduct": "dot",
    "FVector::CrossProduct": "cross", "FQuat::MakeFromRotationVector": "quat_from_rotation_vector",
}


def N(k):
    """Random case count of an engine function: half of the nominal number (the engine goldens are best effort and kept small)."""
    return max(12, k // 2)


def gen_ue_math():
    F = "ue_math.json"

    def F1(cname, py, sig, pname_kinds, ret, call, note=None):
        fn_ = Fn(F, cname, "MathLibrary." + py, sig, pname_kinds, ret, call, note=note)
        if cname in DAL_BUILTIN:
            fn_.extra["dal_builtin"] = DAL_BUILTIN[cname]
        return fn_

    def rnd_angle(rng, lo=-1080, hi=1080):
        return U(rng, lo, hi)

    # ---- NormalizeAxis / ClampAxis / ClampAngle / UnwindDegrees (via FVector::UnwindEuler) ----
    sp = [f32(x) for x in (0.0, -0.0, 90.0, 180.0, -180.0, 360.0, -360.0, 540.0, -540.0, 720.0, 179.99998, -179.99998, 180.00002, -180.00002, 359.99997, 360.00003, 1e7, -1e7)]
    fn = F1("FRotator::NormalizeAxis", "normalize_axis", "float NormalizeAxis(float Angle)", [("Angle", F32)], F32, lambda a: ML.normalize_axis(a["Angle"]),
            "Result in (-180, 180]: -180 maps to +180 (unlike FMath::UnwindDegrees which keeps -180).")
    rng = rng_for("NormalizeAxis")
    for _ in range(N(80)):
        fn.add("random", Angle=rnd_angle(rng))
    for v in sp:
        fn.add(E("special_angle"), Angle=v)
    fn = F1("FRotator::ClampAxis", "clamp_axis", "float ClampAxis(float Angle)", [("Angle", F32)], F32, lambda a: ML.clamp_axis(a["Angle"]), "Result in [0, 360).")
    rng = rng_for("ClampAxis")
    for _ in range(N(80)):
        fn.add("random", Angle=rnd_angle(rng))
    for v in sp:
        fn.add(E("special_angle"), Angle=v)
    fn = F1("FMath::ClampAngle", "clamp_angle", "double ClampAngle(double AngleDegrees, double MinAngleDegrees, double MaxAngleDegrees)",
            [("AngleDegrees", F64), ("MinAngleDegrees", F64), ("MaxAngleDegrees", F64)], F64,
            lambda a: ML.clamp_angle(a["AngleDegrees"], a["MinAngleDegrees"], a["MaxAngleDegrees"]))
    rng = rng_for("ClampAngle")
    for _ in range(N(60)):
        lo = U(rng, -180, 0)
        fn.add("random", AngleDegrees=rnd_angle(rng, -540, 540), MinAngleDegrees=lo, MaxAngleDegrees=f32(lo + rng.uniform(0, 180)))
    for why, a_, lo, hi in [("inside", 10.0, -45.0, 45.0), ("above", 100.0, -45.0, 45.0), ("below", -100.0, -45.0, 45.0), ("wrap_350_in_-45_45", 350.0, -45.0, 45.0),
                            ("min_equals_max", 10.0, 20.0, 20.0), ("min_above_max", 10.0, 30.0, -30.0), ("angle_540", 540.0, -45.0, 45.0)]:
        fn.add(E(why), AngleDegrees=a_, MinAngleDegrees=lo, MaxAngleDegrees=hi)
    fn = F1("FMath::UnwindDegrees (via FVector::UnwindEuler)", "vector_unwind_euler", "FVector Vector_UnwindEuler(FVector A)  // each component through FMath::UnwindDegrees",
            [("A", "vec3")], "vec3", lambda a: ML.vector_unwind_euler(a["A"]),
            "FMath::UnwindDegrees itself is not exposed to Python; this goes through FVector::UnwindEuler (same loop, double precision). For float32 inputs the result is identical to the float version. "
            "UnwindDegrees keeps -180 and 180 as they are (range [-180, 180]); it loops by +-360 and never terminates for inf or |x| > ~8.6e9 (not tested).")
    rng = rng_for("UnwindEuler")
    for _ in range(N(70)):
        fn.add("random", A=[rnd_angle(rng), rnd_angle(rng), rnd_angle(rng)])
    for v in sp:
        fn.add(E("special_angle"), A=[v, v, v])
    for b in (175.0, 180.0, -180.0, 360.0, 540.0, -540.0):
        for v in around(b, 2):
            fn.add(E("neighbours"), A=[v, f32(v + 1e-3), f32(v - 1e-3)])

    # ---- interpolation ----
    fn = F1("FMath::FInterpTo", "f_interp_to", "double FInterpTo(double Current, double Target, double DeltaTime, double InterpSpeed)",
            [("Current", F64), ("Target", F64), ("DeltaTime", F64), ("InterpSpeed", F64)], F64,
            lambda a: ML.f_interp_to(a["Current"], a["Target"], a["DeltaTime"], a["InterpSpeed"]),
            "InterpSpeed <= 0 returns Target; Dist^2 < 1e-8 returns Target; else Current + Dist * Clamp(DeltaTime * InterpSpeed, 0, 1).")
    rng = rng_for("FInterpTo")
    for _ in range(N(70)):
        fn.add("random", Current=U(rng, -100, 100), Target=U(rng, -100, 100), DeltaTime=LU(rng, 1e-4, 0.1), InterpSpeed=U(rng, 0, 30))
    for why, c, t, dt, sp_ in [("speed_zero", 0.0, 10.0, 0.016, 0.0), ("speed_negative", 0.0, 10.0, 0.016, -5.0), ("dt_zero", 0.0, 10.0, 0.0, 5.0),
                               ("dist_1e-4_boundary", 0.0, 1e-4, 0.016, 5.0), ("dist_9e-5_returns_target", 0.0, 9e-5, 0.016, 5.0), ("alpha_above_one", 0.0, 10.0, 1.0, 5.0),
                               ("dt_negative", 0.0, 10.0, -0.016, 5.0), ("equal", 3.0, 3.0, 0.016, 5.0)]:
        fn.add(E(why), Current=c, Target=t, DeltaTime=dt, InterpSpeed=sp_)
    fn = F1("FMath::FInterpConstantTo", "f_interp_to_constant", "double FInterpTo_Constant(double Current, double Target, double DeltaTime, double InterpSpeed)",
            [("Current", F64), ("Target", F64), ("DeltaTime", F64), ("InterpSpeed", F64)], F64,
            lambda a: ML.f_interp_to_constant(a["Current"], a["Target"], a["DeltaTime"], a["InterpSpeed"]),
            "Moves by at most InterpSpeed * DeltaTime; |Dist| < 1e-8 returns Target.")
    rng = rng_for("FInterpConstantTo")
    for _ in range(N(70)):
        fn.add("random", Current=U(rng, -100, 100), Target=U(rng, -100, 100), DeltaTime=LU(rng, 1e-4, 0.1), InterpSpeed=U(rng, 0, 300))
    for why, c, t, dt, sp_ in [("speed_zero", 0.0, 10.0, 0.016, 0.0), ("speed_negative", 0.0, 10.0, 0.016, -5.0), ("dt_zero", 0.0, 10.0, 0.0, 5.0),
                               ("reach_exactly", 0.0, 8.0, 0.5, 16.0), ("overshoot_clamped", 0.0, 1.0, 1.0, 100.0), ("equal", 3.0, 3.0, 0.016, 5.0)]:
        fn.add(E(why), Current=c, Target=t, DeltaTime=dt, InterpSpeed=sp_)
    fn = F1("FMath::VInterpTo", "v_interp_to", "FVector VInterpTo(FVector Current, FVector Target, double DeltaTime, double InterpSpeed)",
            [("Current", "vec3"), ("Target", "vec3"), ("DeltaTime", F64), ("InterpSpeed", F64)], "vec3",
            lambda a: ML.v_interp_to(a["Current"], a["Target"], a["DeltaTime"], a["InterpSpeed"]))
    rng = rng_for("VInterpTo")
    for _ in range(N(50)):
        fn.add("random", Current=vec_range(rng, -100, 100), Target=vec_range(rng, -100, 100), DeltaTime=LU(rng, 1e-4, 0.1), InterpSpeed=U(rng, 0, 30))
    for why, c, t, dt, sp_ in [("speed_zero", [0.0, 0.0, 0.0], [1.0, 2.0, 3.0], 0.016, 0.0), ("dist_tiny_returns_target", [0.0, 0.0, 0.0], [1e-5, 0.0, 0.0], 0.016, 5.0),
                               ("dt_zero", [0.0, 0.0, 0.0], [1.0, 2.0, 3.0], 0.0, 5.0)]:
        fn.add(E(why), Current=c, Target=t, DeltaTime=dt, InterpSpeed=sp_)
    fn = F1("FMath::VInterpConstantTo", "v_interp_to_constant", "FVector VInterpTo_Constant(FVector Current, FVector Target, double DeltaTime, double InterpSpeed)",
            [("Current", "vec3"), ("Target", "vec3"), ("DeltaTime", F64), ("InterpSpeed", F64)], "vec3",
            lambda a: ML.v_interp_to_constant(a["Current"], a["Target"], a["DeltaTime"], a["InterpSpeed"]))
    rng = rng_for("VInterpConstantTo")
    for _ in range(N(50)):
        fn.add("random", Current=vec_range(rng, -100, 100), Target=vec_range(rng, -100, 100), DeltaTime=LU(rng, 1e-4, 0.1), InterpSpeed=U(rng, 0, 300))
    for why, c, t, dt, sp_ in [("speed_zero", [0.0, 0.0, 0.0], [1.0, 2.0, 3.0], 0.016, 0.0), ("reach_exactly", [0.0, 0.0, 0.0], [3.0, 4.0, 0.0], 0.5, 10.0),
                               ("dt_zero", [0.0, 0.0, 0.0], [1.0, 2.0, 3.0], 0.0, 5.0)]:
        fn.add(E(why), Current=c, Target=t, DeltaTime=dt, InterpSpeed=sp_)
    fn = F1("FMath::Vector2DInterpTo", "vector2d_interp_to", "FVector2D Vector2DInterpTo(FVector2D Current, FVector2D Target, double DeltaTime, double InterpSpeed)",
            [("Current", "vec2"), ("Target", "vec2"), ("DeltaTime", F64), ("InterpSpeed", F64)], "vec2",
            lambda a: ML.vector2d_interp_to(a["Current"], a["Target"], a["DeltaTime"], a["InterpSpeed"]))
    rng = rng_for("Vector2DInterpTo")
    for _ in range(N(40)):
        fn.add("random", Current=[U(rng, -100, 100), U(rng, -100, 100)], Target=[U(rng, -100, 100), U(rng, -100, 100)], DeltaTime=LU(rng, 1e-4, 0.1), InterpSpeed=U(rng, 0, 30))
    fn.add(E("speed_zero"), Current=[0.0, 0.0], Target=[1.0, 2.0], DeltaTime=0.016, InterpSpeed=0.0)
    fn = F1("FMath::Vector2DInterpConstantTo", "vector2d_interp_to_constant", "FVector2D Vector2DInterpConstantTo(FVector2D Current, FVector2D Target, double DeltaTime, double InterpSpeed)",
            [("Current", "vec2"), ("Target", "vec2"), ("DeltaTime", F64), ("InterpSpeed", F64)], "vec2",
            lambda a: ML.vector2d_interp_to_constant(a["Current"], a["Target"], a["DeltaTime"], a["InterpSpeed"]))
    rng = rng_for("Vector2DInterpConstantTo")
    for _ in range(N(40)):
        fn.add("random", Current=[U(rng, -100, 100), U(rng, -100, 100)], Target=[U(rng, -100, 100), U(rng, -100, 100)], DeltaTime=LU(rng, 1e-4, 0.1), InterpSpeed=U(rng, 0, 300))
    fn.add(E("speed_zero"), Current=[0.0, 0.0], Target=[1.0, 2.0], DeltaTime=0.016, InterpSpeed=0.0)
    fn = F1("FMath::RInterpTo", "r_interp_to", "FRotator RInterpTo(FRotator Current, FRotator Target, double DeltaTime, double InterpSpeed)",
            [("Current", "rotator"), ("Target", "rotator"), ("DeltaTime", F64), ("InterpSpeed", F64)], "rotator",
            lambda a: ML.r_interp_to(a["Current"], a["Target"], a["DeltaTime"], a["InterpSpeed"]),
            "Rotators are [pitch, yaw, roll]. Delta is the normalized (Target - Current) per axis (shortest way; +-180 is ambiguous).")
    rng = rng_for("RInterpTo")
    for _ in range(N(60)):
        fn.add("random", Current=rot_random(rng), Target=rot_random(rng), DeltaTime=LU(rng, 1e-4, 0.1), InterpSpeed=U(rng, 0, 30))
    for why, c, t, dt, sp_ in [("speed_zero", [0.0, 0.0, 0.0], [10.0, 20.0, 30.0], 0.016, 0.0), ("yaw_170_to_-170", [0.0, 170.0, 0.0], [0.0, -170.0, 0.0], 0.016, 5.0),
                               ("yaw_0_to_180", [0.0, 0.0, 0.0], [0.0, 180.0, 0.0], 0.016, 5.0), ("yaw_0_to_-180", [0.0, 0.0, 0.0], [0.0, -180.0, 0.0], 0.016, 5.0),
                               ("equal", [10.0, 20.0, 30.0], [10.0, 20.0, 30.0], 0.016, 5.0), ("delta_tiny", [0.0, 0.0, 0.0], [0.0, 1e-5, 0.0], 0.016, 5.0)]:
        fn.add(E(why), Current=c, Target=t, DeltaTime=dt, InterpSpeed=sp_)
    fn = F1("FMath::RInterpConstantTo", "r_interp_to_constant", "FRotator RInterpTo_Constant(FRotator Current, FRotator Target, double DeltaTime, double InterpSpeed)",
            [("Current", "rotator"), ("Target", "rotator"), ("DeltaTime", F64), ("InterpSpeed", F64)], "rotator",
            lambda a: ML.r_interp_to_constant(a["Current"], a["Target"], a["DeltaTime"], a["InterpSpeed"]))
    rng = rng_for("RInterpConstantTo")
    for _ in range(N(60)):
        fn.add("random", Current=rot_random(rng), Target=rot_random(rng), DeltaTime=LU(rng, 1e-4, 0.1), InterpSpeed=U(rng, 0, 300))
    for why, c, t, dt, sp_ in [("speed_zero", [0.0, 0.0, 0.0], [10.0, 20.0, 30.0], 0.016, 0.0), ("yaw_170_to_-170", [0.0, 170.0, 0.0], [0.0, -170.0, 0.0], 0.016, 100.0),
                               ("yaw_0_to_180", [0.0, 0.0, 0.0], [0.0, 180.0, 0.0], 0.016, 100.0), ("equal", [10.0, 20.0, 30.0], [10.0, 20.0, 30.0], 0.016, 100.0)]:
        fn.add(E(why), Current=c, Target=t, DeltaTime=dt, InterpSpeed=sp_)

    # ---- quaternions ----
    q0 = [0.0, 0.0, 0.0, 1.0]
    fn = F1("FQuat::Slerp", "quat_slerp", "FQuat Quat_Slerp(FQuat A, FQuat B, double Alpha)", [("A", "quat"), ("B", "quat"), ("Alpha", F64)], "quat",
            lambda a: ML.quat_slerp(a["A"], a["B"], a["Alpha"]), "Shortest-path slerp, result normalized. Quats are [x,y,z,w].")
    rng = rng_for("QuatSlerp")
    for _ in range(N(70)):
        fn.add("random", A=unit_quat(rng), B=unit_quat(rng), Alpha=U(rng, 0, 1))
    for _ in range(N(15)):
        fn.add("random:alpha_outside_0_1", A=unit_quat(rng), B=unit_quat(rng), Alpha=U(rng, -0.5, 1.5))
    for why, a_, b_, al in [("alpha_zero", [0.0, 0.0, 0.5, 0.8660254037844386], [0.0, 0.5, 0.0, 0.8660254037844386], 0.0), ("alpha_one", [0.0, 0.0, 0.5, 0.8660254037844386], [0.0, 0.5, 0.0, 0.8660254037844386], 1.0),
                            ("identical", [0.0, 0.0, 0.5, 0.8660254037844386], [0.0, 0.0, 0.5, 0.8660254037844386], 0.5), ("q_vs_minus_q", [0.0, 0.0, 0.5, 0.8660254037844386], [0.0, 0.0, -0.5, -0.8660254037844386], 0.5),
                            ("180_degrees", q0, [0.0, 0.0, 1.0, 0.0], 0.5), ("nearly_identical_1e-6", q0, [1e-6, 0.0, 0.0, 1.0], 0.5), ("identity_to_90z", q0, [0.0, 0.0, 0.7071067811865476, 0.7071067811865476], 0.5)]:
        fn.add(E(why), A=a_, B=b_, Alpha=al)
    fn = F1("FQuat::Inverse", "quat_inversed", "FQuat Quat_Inversed(FQuat Q)", [("Q", "quat")], "quat", lambda a: ML.quat_inversed(a["Q"]), "(-x,-y,-z,w) for a normalized quaternion.")
    rng = rng_for("QuatInverse")
    for _ in range(N(50)):
        fn.add("random", Q=unit_quat(rng))
    fn.add(E("identity"), Q=q0)
    fn.add(E("unnormalized_2"), Q=[0.0, 0.0, 0.0, 2.0])
    fn = F1("FQuat::operator*", "multiply_quat_quat", "FQuat Multiply_QuatQuat(FQuat A, FQuat B)", [("A", "quat"), ("B", "quat")], "quat",
            lambda a: ML.multiply_quat_quat(a["A"], a["B"]), "A * B: apply B first, then A (UE convention).")
    rng = rng_for("QuatMultiply")
    for _ in range(N(70)):
        fn.add("random", A=unit_quat(rng), B=unit_quat(rng))
    fn.add(E("identity_times_q"), A=q0, B=[0.1, 0.2, 0.3, 0.9273618495495703])
    fn.add(E("q_times_inverse"), A=[0.1, 0.2, 0.3, 0.9273618495495703], B=[-0.1, -0.2, -0.3, 0.9273618495495703])
    fn = F1("FQuat::GetNormalized", "quat_normalized", "FQuat Quat_Normalized(FQuat Q, double Tolerance = 1e-4)", [("Q", "quat")], "quat",
            lambda a: ML.quat_normalized(a["Q"]), "Default tolerance 1e-4 (FQuat::GetNormalized(UE_SMALL_NUMBER) semantics: squared size below the tolerance gives identity).")
    rng = rng_for("QuatNormalized")
    for _ in range(N(40)):
        fn.add("random:non_unit", Q=[U(rng, -3, 3), U(rng, -3, 3), U(rng, -3, 3), U(rng, -3, 3)])
    for why, q in [("zero", [0.0, 0.0, 0.0, 0.0]), ("tiny_1e-5", [1e-5, 0.0, 0.0, 0.0]), ("tiny_1e-3", [1e-3, 0.0, 0.0, 0.0]), ("identity", q0)]:
        fn.add(E(why), Q=q)
    fn = F1("FRotator::Quaternion", "conv_rotator_to_quaternion", "FQuat Conv_RotatorToQuaternion(FRotator Rot)", [("Rot", "rotator")], "quat",
            lambda a: ML.conv_rotator_to_quaternion(a["Rot"]), "Rotators are [pitch, yaw, roll]; result [x,y,z,w].")
    rng = rng_for("RotatorToQuat")
    for _ in range(N(70)):
        fn.add("random:normalized", Rot=rot_random(rng))
    for _ in range(N(20)):
        fn.add("random:wide", Rot=rot_random(rng, True))
    for why, r_ in [("zero", [0.0, 0.0, 0.0]), ("yaw_90", [0.0, 90.0, 0.0]), ("yaw_180", [0.0, 180.0, 0.0]), ("pitch_90", [90.0, 0.0, 0.0]), ("pitch_-90", [-90.0, 0.0, 0.0]),
                    ("roll_180", [0.0, 0.0, 180.0]), ("yaw_360", [0.0, 360.0, 0.0]), ("all_45", [45.0, 45.0, 45.0])]:
        fn.add(E(why), Rot=r_)
    fn = F1("FQuat::Rotator", "quat_rotator", "FRotator Quat_Rotator(FQuat Q)", [("Q", "quat")], "rotator", lambda a: ML.quat_rotator(a["Q"]),
            "Result [pitch, yaw, roll]. Includes the engine's singularity handling at pitch = +-90.")
    rng = rng_for("QuatToRotator")
    for _ in range(N(70)):
        fn.add("random", Q=unit_quat(rng))
    for why, q in [("identity", q0), ("yaw_90", [0.0, 0.0, 0.7071067811865476, 0.7071067811865476]), ("pitch_90_singularity", [0.0, -0.7071067811865476, 0.0, 0.7071067811865476]),
                   ("pitch_-90_singularity", [0.0, 0.7071067811865476, 0.0, 0.7071067811865476]), ("yaw_180", [0.0, 0.0, 1.0, 0.0]), ("roll_180", [1.0, 0.0, 0.0, 0.0]),
                   ("minus_identity", [0.0, 0.0, 0.0, -1.0]), ("unnormalized_2", [0.0, 0.0, 0.0, 2.0])]:
        fn.add(E(why), Q=q)
    fn = F1("FQuat::RotateVector", "quat_rotate_vector", "FVector Quat_RotateVector(FQuat Q, FVector V)", [("Q", "quat"), ("V", "vec3")], "vec3",
            lambda a: ML.quat_rotate_vector(a["Q"], a["V"]))
    rng = rng_for("QuatRotateVector")
    for _ in range(N(70)):
        fn.add("random", Q=unit_quat(rng), V=vec_range(rng, -100, 100))
    fn.add(E("identity"), Q=q0, V=[1.0, 2.0, 3.0])
    fn.add(E("zero_vector"), Q=[0.1, 0.2, 0.3, 0.9273618495495703], V=[0.0, 0.0, 0.0])
    fn = F1("FQuat::UnrotateVector", "quat_unrotate_vector", "FVector Quat_UnrotateVector(FQuat Q, FVector V)", [("Q", "quat"), ("V", "vec3")], "vec3",
            lambda a: ML.quat_unrotate_vector(a["Q"], a["V"]))
    rng = rng_for("QuatUnrotateVector")
    for _ in range(N(70)):
        fn.add("random", Q=unit_quat(rng), V=vec_range(rng, -100, 100))
    fn.add(E("identity"), Q=q0, V=[1.0, 2.0, 3.0])

    # ---- look at / delta rotator / vectors -> rotation ----
    fn = F1("FRotationMatrix::MakeFromX(Target - Start).Rotator()", "find_look_at_rotation", "FRotator FindLookAtRotation(FVector Start, FVector Target)",
            [("Start", "vec3"), ("Target", "vec3")], "rotator", lambda a: ML.find_look_at_rotation(a["Start"], a["Target"]),
            "Roll is 0. [pitch, yaw, roll].")
    rng = rng_for("FindLookAtRotation")
    for _ in range(N(70)):
        fn.add("random", Start=vec_range(rng, -500, 500), Target=vec_range(rng, -500, 500))
    for why, s, t in [("same_point", [1.0, 2.0, 3.0], [1.0, 2.0, 3.0]), ("straight_up", [0.0, 0.0, 0.0], [0.0, 0.0, 10.0]), ("straight_down", [0.0, 0.0, 0.0], [0.0, 0.0, -10.0]),
                      ("along_x", [0.0, 0.0, 0.0], [10.0, 0.0, 0.0]), ("along_-x", [0.0, 0.0, 0.0], [-10.0, 0.0, 0.0]), ("along_y", [0.0, 0.0, 0.0], [0.0, 10.0, 0.0]),
                      ("along_-y", [0.0, 0.0, 0.0], [0.0, -10.0, 0.0]), ("diagonal", [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]), ("nearly_vertical", [0.0, 0.0, 0.0], [1e-3, 0.0, 10.0]),
                      ("tiny_distance_1e-5", [0.0, 0.0, 0.0], [1e-5, 1e-5, 0.0])]:
        fn.add(E(why), Start=s, Target=t)
    fn = F1("FVector::ToOrientationRotator (Vector.Rotation())", "conv_vector_to_rotator", "FRotator Conv_VectorToRotator(FVector InVec)", [("InVec", "vec3")], "rotator",
            lambda a: ML.conv_vector_to_rotator(a["InVec"]), "The DAL builtin look_at / ToOrientationRotator. Roll 0.")
    rng = rng_for("VectorToRotator")
    for _ in range(N(70)):
        fn.add("random", InVec=vec_range(rng, -500, 500))
    for why, v in [("zero", [0.0, 0.0, 0.0]), ("up", [0.0, 0.0, 1.0]), ("down", [0.0, 0.0, -1.0]), ("+x", [1.0, 0.0, 0.0]), ("-x", [-1.0, 0.0, 0.0]),
                   ("+y", [0.0, 1.0, 0.0]), ("-y", [0.0, -1.0, 0.0]), ("neg_zero_y", [-1.0, -0.0, 0.0]), ("tiny_1e-30", [1e-30, 1e-30, 1e-30]), ("huge_1e30", [1e30, 1e30, 1e30])]:
        fn.add(E(why), InVec=v)
    fn = F1("FVector::ToOrientationQuat", "conv_vector_to_quaternion", "FQuat Conv_VectorToQuaternion(FVector InVec)", [("InVec", "vec3")], "quat",
            lambda a: ML.conv_vector_to_quaternion(a["InVec"]), "The DAL builtin to_orientation_quat.")
    rng = rng_for("VectorToQuat")
    for _ in range(N(60)):
        fn.add("random", InVec=vec_range(rng, -500, 500))
    for why, v in [("zero", [0.0, 0.0, 0.0]), ("up", [0.0, 0.0, 1.0]), ("down", [0.0, 0.0, -1.0]), ("+x", [1.0, 0.0, 0.0]), ("-x", [-1.0, 0.0, 0.0]), ("+y", [0.0, 1.0, 0.0])]:
        fn.add(E(why), InVec=v)
    fn = F1("(A - B).GetNormalized()", "normalized_delta_rotator", "FRotator NormalizedDeltaRotator(FRotator A, FRotator B)", [("A", "rotator"), ("B", "rotator")], "rotator",
            lambda a: ML.normalized_delta_rotator(a["A"], a["B"]), "The DAL builtin delta_rotator. Per axis (A - B) normalized to (-180, 180].")
    rng = rng_for("NormalizedDeltaRotator")
    for _ in range(N(70)):
        fn.add("random", A=rot_random(rng, True), B=rot_random(rng, True))
    for why, a_, b_ in [("equal", [10.0, 20.0, 30.0], [10.0, 20.0, 30.0]), ("yaw_170_vs_-170", [0.0, 170.0, 0.0], [0.0, -170.0, 0.0]), ("yaw_180_vs_0", [0.0, 180.0, 0.0], [0.0, 0.0, 0.0]),
                        ("yaw_-180_vs_0", [0.0, -180.0, 0.0], [0.0, 0.0, 0.0]), ("yaw_0_vs_180", [0.0, 0.0, 0.0], [0.0, 180.0, 0.0])]:
        fn.add(E(why), A=a_, B=b_)

    # ---- scalar math (double precision in Kismet; ALS uses the float32 overloads) ----
    def scalar(cname, py, sig, call, gen, edges, note=None, params=(("A", F64),), n=60):
        fn_ = F1(cname, py, sig, list(params), F64, call, note)
        rng_ = rng_for(py)
        for _ in range(N(n)):
            fn_.add("random", **gen(rng_))
        for why, kw in edges:
            fn_.add(E(why), **kw)
        return fn_

    scalar("FMath::Sin", "sin", "double Sin(double A)", lambda a: ML.sin(a["A"]), lambda r: {"A": U(r, -20, 20)},
           [("zero", {"A": 0.0}), ("pi", {"A": f32(math.pi)}), ("half_pi", {"A": f32(math.pi / 2)}), ("large_1e6", {"A": 1e6}), ("neg_zero", {"A": -0.0})],
           "Kismet Sin = FMath::Sin(double) (libm). ALS calls the float overload (FMath::SinCos(float...)), see als_vector.json for those goldens.")
    scalar("FMath::Cos", "cos", "double Cos(double A)", lambda a: ML.cos(a["A"]), lambda r: {"A": U(r, -20, 20)},
           [("zero", {"A": 0.0}), ("pi", {"A": f32(math.pi)}), ("half_pi", {"A": f32(math.pi / 2)}), ("large_1e6", {"A": 1e6})])
    scalar("FMath::Tan", "tan", "double Tan(double A)", lambda a: ML.tan(a["A"]), lambda r: {"A": U(r, -1.5, 1.5)},
           [("zero", {"A": 0.0}), ("half_pi", {"A": f32(math.pi / 2)}), ("quarter_pi", {"A": f32(math.pi / 4)})])
    scalar("FMath::Asin", "asin", "double Asin(double A)", lambda a: ML.asin(a["A"]), lambda r: {"A": U(r, -1, 1)},
           [("zero", {"A": 0.0}), ("one", {"A": 1.0}), ("minus_one", {"A": -1.0}), ("above_one_clamped", {"A": 1.5}), ("below_minus_one_clamped", {"A": -1.5})],
           "FMath::Asin clamps its argument to [-1, 1].")
    scalar("FMath::Acos", "acos", "double Acos(double A)", lambda a: ML.acos(a["A"]), lambda r: {"A": U(r, -1, 1)},
           [("zero", {"A": 0.0}), ("one", {"A": 1.0}), ("minus_one", {"A": -1.0}), ("above_one_clamped", {"A": 1.5}), ("below_minus_one_clamped", {"A": -1.5}),
            ("1ulp_below_one", {"A": f32_step(1.0, False)}), ("1e-7_below_one", {"A": 1.0 - 1e-7}), ("nan_clamps_to_one_behaviour", {"A": NAN})],
           "FMath::Acos clamps its argument to [-1, 1] (a NaN argument is not < -1 and not < 1, so the clamp yields 1 and Acos returns 0).")
    scalar("FMath::Atan", "atan", "double Atan(double A)", lambda a: ML.atan(a["A"]), lambda r: {"A": U(r, -100, 100)},
           [("zero", {"A": 0.0}), ("one", {"A": 1.0}), ("large_1e30", {"A": 1e30}), ("minus_large", {"A": -1e30})])
    scalar("FMath::Atan2", "atan2", "double Atan2(double Y, double X)", lambda a: ML.atan2(a["Y"], a["X"]), lambda r: {"Y": U(r, -10, 10), "X": U(r, -10, 10)},
           [("zero_zero", {"Y": 0.0, "X": 0.0}), ("neg_zero_y_neg_x", {"Y": -0.0, "X": -1.0}), ("pos_zero_y_neg_x", {"Y": 0.0, "X": -1.0}), ("x_zero_y_pos", {"Y": 1.0, "X": 0.0}),
            ("x_zero_y_neg", {"Y": -1.0, "X": 0.0}), ("y_zero_x_pos", {"Y": 0.0, "X": 1.0}), ("1_1", {"Y": 1.0, "X": 1.0}), ("-1_-1", {"Y": -1.0, "X": -1.0}),
            ("tiny_both", {"Y": 1e-30, "X": 1e-30}), ("huge_both", {"Y": 1e30, "X": -1e30}), ("y_tiny_x_neg", {"Y": 1e-7, "X": -1.0}), ("y_neg_tiny_x_neg", {"Y": -1e-7, "X": -1.0})],
           "FMath::Atan2(double) equals the C library atan2 bit for bit on the random inputs but ignores the sign of zeros (Y=-0, X<0 gives +pi, not -pi; (+-0,+-0) gives 0). DirectionToAngle in als_vector.json is the same function (times 180/pi).",
           params=(("Y", F64), ("X", F64)), n=120)
    scalar("FMath::Exp", "exp", "double Exp(double A)", lambda a: ML.exp(a["A"]), lambda r: {"A": U(r, -10, 10)},
           [("zero", {"A": 0.0}), ("one", {"A": 1.0}), ("minus_one", {"A": -1.0}), ("large_700", {"A": 700.0}), ("overflow_800", {"A": 800.0}), ("underflow_-800", {"A": -800.0})])
    scalar("FMath::Loge", "loge", "double Loge(double A)", lambda a: ML.loge(a["A"]), lambda r: {"A": LU(r, 1e-3, 1e3)},
           [("one", {"A": 1.0}), ("zero", {"A": 0.0}), ("negative", {"A": -1.0}), ("e", {"A": f32(math.e)}), ("tiny", {"A": 1e-30})])
    scalar("FMath::Sqrt", "sqrt", "double Sqrt(double A)", lambda a: ML.sqrt(a["A"]), lambda r: {"A": U(r, 0, 1000)},
           [("zero", {"A": 0.0}), ("neg_zero", {"A": -0.0}), ("negative", {"A": -4.0}), ("one", {"A": 1.0}), ("tiny", {"A": 1e-30})],
           "Kismet Sqrt returns 0 (and logs a warning) for a negative argument, unlike FMath::Sqrt which gives NaN.")
    scalar("FMath::Pow", "multiply_multiply_float_float", "double MultiplyMultiply_FloatFloat(double Base, double Exp)  // Base ** Exp",
           lambda a: ML.multiply_multiply_float_float(a["Base"], a["Exp"]), lambda r: {"Base": U(r, 0.1, 10), "Exp": U(r, -3, 3)},
           [("zero_zero", {"Base": 0.0, "Exp": 0.0}), ("zero_negative_exp", {"Base": 0.0, "Exp": -1.0}), ("negative_base_integer_exp", {"Base": -2.0, "Exp": 3.0}),
            ("negative_base_fraction_exp_nan", {"Base": -2.0, "Exp": 0.5}), ("one_huge_exp", {"Base": 1.0, "Exp": 1e30})], params=(("Base", F64), ("Exp", F64)))
    scalar("FMath::DegreesToRadians", "degrees_to_radians", "double DegreesToRadians(double A)", lambda a: ML.degrees_to_radians(a["A"]), lambda r: {"A": U(r, -720, 720)},
           [("180", {"A": 180.0}), ("90", {"A": 90.0}), ("360", {"A": 360.0})])
    scalar("FMath::RadiansToDegrees", "radians_to_degrees", "double RadiansToDegrees(double A)", lambda a: ML.radians_to_degrees(a["A"]), lambda r: {"A": U(r, -12, 12)},
           [("pi", {"A": f32(math.pi)}), ("half_pi", {"A": f32(math.pi / 2)}), ("two_pi", {"A": f32(2 * math.pi)})])
    scalar("FMath::Lerp", "lerp", "double Lerp(double A, double B, double Alpha)", lambda a: ML.lerp(a["A"], a["B"], a["Alpha"]),
           lambda r: {"A": U(r, -100, 100), "B": U(r, -100, 100), "Alpha": U(r, -0.5, 1.5)},
           [("alpha_zero", {"A": 1.0, "B": 5.0, "Alpha": 0.0}), ("alpha_one", {"A": 1.0, "B": 5.0, "Alpha": 1.0}), ("a_equals_b", {"A": 3.0, "B": 3.0, "Alpha": 0.7})],
           "A + Alpha * (B - A)", params=(("A", F64), ("B", F64), ("Alpha", F64)))
    scalar("FMath::Clamp", "f_clamp", "double FClamp(double Value, double Min, double Max)", lambda a: ML.f_clamp(a["Value"], a["Min"], a["Max"]),
           lambda r: {"Value": U(r, -10, 10), "Min": -2.0, "Max": 3.0},
           [("min_above_max", {"Value": 0.0, "Min": 3.0, "Max": -3.0}), ("nan_value", {"Value": NAN, "Min": 0.0, "Max": 1.0}), ("at_min", {"Value": -2.0, "Min": -2.0, "Max": 3.0})],
           params=(("Value", F64), ("Min", F64), ("Max", F64)))
    scalar("FMath::GetMappedRangeValueClamped", "map_range_clamped", "double MapRangeClamped(double Value, double InRangeA, double InRangeB, double OutRangeA, double OutRangeB)",
           lambda a: ML.map_range_clamped(a["Value"], a["InRangeA"], a["InRangeB"], a["OutRangeA"], a["OutRangeB"]),
           lambda r: {"Value": U(r, -5, 15), "InRangeA": 0.0, "InRangeB": 10.0, "OutRangeA": -1.0, "OutRangeB": 1.0},
           [("degenerate_in_range", {"Value": 5.0, "InRangeA": 3.0, "InRangeB": 3.0, "OutRangeA": 0.0, "OutRangeB": 1.0}),
            ("reversed_in_range", {"Value": 2.0, "InRangeA": 10.0, "InRangeB": 0.0, "OutRangeA": 0.0, "OutRangeB": 1.0}),
            ("reversed_out_range", {"Value": 2.0, "InRangeA": 0.0, "InRangeB": 10.0, "OutRangeA": 1.0, "OutRangeB": 0.0})],
           params=(("Value", F64), ("InRangeA", F64), ("InRangeB", F64), ("OutRangeA", F64), ("OutRangeB", F64)))
    scalar("FMath::GetMappedRangeValueUnclamped", "map_range_unclamped", "double MapRangeUnclamped(double Value, double InRangeA, double InRangeB, double OutRangeA, double OutRangeB)",
           lambda a: ML.map_range_unclamped(a["Value"], a["InRangeA"], a["InRangeB"], a["OutRangeA"], a["OutRangeB"]),
           lambda r: {"Value": U(r, -5, 15), "InRangeA": 0.0, "InRangeB": 10.0, "OutRangeA": -1.0, "OutRangeB": 1.0},
           [("degenerate_in_range", {"Value": 5.0, "InRangeA": 3.0, "InRangeB": 3.0, "OutRangeA": 0.0, "OutRangeB": 1.0})],
           params=(("Value", F64), ("InRangeA", F64), ("InRangeB", F64), ("OutRangeA", F64), ("OutRangeB", F64)))
    scalar("FMath::GetRangePct", "normalize_to_range", "double NormalizeToRange(double Value, double RangeMin, double RangeMax)",
           lambda a: ML.normalize_to_range(a["Value"], a["RangeMin"], a["RangeMax"]), lambda r: {"Value": U(r, -5, 15), "RangeMin": 0.0, "RangeMax": 10.0},
           [("degenerate_range", {"Value": 5.0, "RangeMin": 3.0, "RangeMax": 3.0}), ("reversed_range", {"Value": 2.0, "RangeMin": 10.0, "RangeMax": 0.0})],
           params=(("Value", F64), ("RangeMin", F64), ("RangeMax", F64)))
    scalar("FMath::Frac", "fraction", "double Fraction(double A)", lambda a: ML.fraction(a["A"]), lambda r: {"A": U(r, -10, 10)},
           [("negative_1.25", {"A": -1.25}), ("integer", {"A": 3.0}), ("neg_zero", {"A": -0.0})])
    scalar("FMath::Sign", "sign_of_float", "double SignOfFloat(double A)", lambda a: ML.sign_of_float(a["A"]), lambda r: {"A": U(r, -10, 10)},
           [("zero", {"A": 0.0}), ("neg_zero", {"A": -0.0}), ("nan", {"A": NAN})], "FMath::Sign(0) is 0 (three-way sign).")
    scalar("FMath::Square", "square", "double Square(double A)", lambda a: ML.square(a["A"]), lambda r: {"A": U(r, -100, 100)}, [("zero", {"A": 0.0}), ("huge", {"A": 1e200})])
    scalar("FMath::Abs", "abs", "double Abs(double A)", lambda a: ML.abs(a["A"]), lambda r: {"A": U(r, -100, 100)}, [("neg_zero", {"A": -0.0}), ("neg_huge", {"A": -1e30})], n=30)

    # ---- vectors ----
    fn = F1("FVector::GetSafeNormal", "normal", "FVector Normal(FVector A, double Tolerance = 1e-4)", [("A", "vec3")], "vec3", lambda a: ML.normal(a["A"]),
            "Default tolerance 1e-4: a squared size <= tolerance gives the zero vector (DAL builtin normalize(vec3)).")
    rng = rng_for("VectorNormal")
    for _ in range(N(60)):
        fn.add("random", A=vec_range(rng, -100, 100))
    for why, v in [("zero", [0.0, 0.0, 0.0]), ("tiny_1e-3", [1e-3, 0.0, 0.0]), ("tiny_1e-5_below_tolerance", [1e-5, 0.0, 0.0]), ("size_1e-2_boundary", [1e-2, 0.0, 0.0]),
                   ("unit_x", [1.0, 0.0, 0.0]), ("huge_1e30", [1e30, 1e30, 1e30])]:
        fn.add(E(why), A=v)
    fn = F1("FVector::GetClampedToMaxSize", "vector_clamp_size_max", "FVector VectorClampSizeMax(FVector A, double Max)", [("A", "vec3"), ("Max", F64)], "vec3",
            lambda a: ML.vector_clamp_size_max(a["A"], a["Max"]), "DAL builtin clamp_length(vec3, float).")
    rng = rng_for("VectorClampSizeMax")
    for _ in range(N(50)):
        fn.add("random", A=vec_range(rng, -100, 100), Max=U(rng, 0, 100))
    for why, v, m in [("zero_vector", [0.0, 0.0, 0.0], 5.0), ("max_zero", [1.0, 2.0, 3.0], 0.0), ("max_negative", [1.0, 2.0, 3.0], -5.0), ("inside", [1.0, 0.0, 0.0], 5.0),
                      ("exact_boundary", [3.0, 4.0, 0.0], 5.0), ("tiny_vector_below_tolerance", [1e-5, 0.0, 0.0], 1.0)]:
        fn.add(E(why), A=v, Max=m)
    fn = F1("FVector::Dist", "vector_distance", "double VectorDistance(FVector V1, FVector V2)", [("V1", "vec3"), ("V2", "vec3")], F64, lambda a: ML.vector_distance(a["V1"], a["V2"]))
    rng = rng_for("VectorDistance")
    for _ in range(N(40)):
        fn.add("random", V1=vec_range(rng, -100, 100), V2=vec_range(rng, -100, 100))
    fn.add(E("same_point"), V1=[1.0, 2.0, 3.0], V2=[1.0, 2.0, 3.0])
    fn = F1("FVector::ProjectOnToNormal", "vector_project_on_to_normal", "FVector ProjectOnToNormal(FVector V, FVector Normal)", [("V", "vec3"), ("Normal", "vec3")], "vec3",
            lambda a: ML.vector_project_on_to_normal(a["V"], a["Normal"]), "Normal * (V | Normal): the DAL builtin project_on_normal.")
    rng = rng_for("ProjectOnToNormal")
    for _ in range(N(50)):
        fn.add("random", V=vec_range(rng, -100, 100), Normal=unit3(rng))
    fn.add(E("zero_normal"), V=[1.0, 2.0, 3.0], Normal=[0.0, 0.0, 0.0])
    fn.add(E("non_unit_normal"), V=[1.0, 2.0, 3.0], Normal=[0.0, 0.0, 2.0])
    fn = F1("FVector::DotProduct", "dot_vector_vector", "double Dot_VectorVector(FVector A, FVector B)", [("A", "vec3"), ("B", "vec3")], F64, lambda a: ML.dot_vector_vector(a["A"], a["B"]))
    rng = rng_for("Dot")
    for _ in range(N(40)):
        fn.add("random", A=vec_range(rng, -100, 100), B=vec_range(rng, -100, 100))
    fn = F1("FVector::CrossProduct", "cross_vector_vector", "FVector Cross_VectorVector(FVector A, FVector B)", [("A", "vec3"), ("B", "vec3")], "vec3", lambda a: ML.cross_vector_vector(a["A"], a["B"]))
    rng = rng_for("Cross")
    for _ in range(N(40)):
        fn.add("random", A=vec_range(rng, -100, 100), B=vec_range(rng, -100, 100))
    fn = F1("FQuat::FindBetweenVectors", "quat_find_between_vectors", "FQuat Quat_FindBetweenVectors(FVector Start, FVector End)", [("Start", "vec3"), ("End", "vec3")], "quat",
            lambda a: ML.quat_find_between_vectors(a["Start"], a["End"]), "Shortest rotation between two vectors (any length); opposite vectors pick an arbitrary perpendicular axis.")
    rng = rng_for("QuatFindBetweenVectors")
    for _ in range(N(50)):
        fn.add("random", Start=vec_range(rng, -10, 10), End=vec_range(rng, -10, 10))
    for why, s, e in [("same_direction", [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]), ("opposite_x", [1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]), ("opposite_z", [0.0, 0.0, 1.0], [0.0, 0.0, -1.0]),
                      ("perpendicular", [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]), ("zero_start", [0.0, 0.0, 0.0], [1.0, 0.0, 0.0]), ("zero_both", [0.0, 0.0, 0.0], [0.0, 0.0, 0.0])]:
        fn.add(E(why), Start=s, End=e)
    fn = F1("FQuat::MakeFromRotationVector", "quat_make_from_rotation_vector", "FQuat Quat_MakeFromRotationVector(FVector RotationVector)", [("V", "vec3")], "quat",
            lambda a: ML.quat_make_from_rotation_vector(a["V"]), "The rotation about V by |V| radians (DAL builtin quat_from_rotation_vector).")
    rng = rng_for("QuatMakeFromRotationVector")
    for _ in range(N(50)):
        fn.add("random", V=vec_range(rng, -4, 4))
    for why, v in [("zero", [0.0, 0.0, 0.0]), ("tiny_1e-6", [1e-6, 0.0, 0.0]), ("pi_about_x", [f32(math.pi), 0.0, 0.0]), ("two_pi_about_x", [f32(2 * math.pi), 0.0, 0.0]), ("large_100", [100.0, 0.0, 0.0])]:
        fn.add(E(why), V=v)
    fn = F1("FQuat::GetAngle", "quat_get_angle", "float Quat_GetAngle(FQuat Q)", [("Q", "quat")], F32, lambda a: ML.quat_get_angle(a["Q"]), "Rotation angle in radians (float return).")
    rng = rng_for("QuatGetAngle")
    for _ in range(N(40)):
        fn.add("random", Q=unit_quat(rng))
    for why, q in [("identity", q0), ("minus_identity", [0.0, 0.0, 0.0, -1.0]), ("180", [0.0, 0.0, 1.0, 0.0]), ("unnormalized_2", [0.0, 0.0, 0.0, 2.0])]:
        fn.add(E(why), Q=q)
    fn = F1("FQuat::AngularDistance", "quat_angular_distance", "float Quat_AngularDistance(FQuat A, FQuat B)", [("A", "quat"), ("B", "quat")], F32,
            lambda a: ML.quat_angular_distance(a["A"], a["B"]), "Radians (float return).")
    rng = rng_for("QuatAngularDistance")
    for _ in range(N(40)):
        fn.add("random", A=unit_quat(rng), B=unit_quat(rng))
    fn.add(E("same"), A=q0, B=q0)
    fn.add(E("q_vs_minus_q"), A=q0, B=[0.0, 0.0, 0.0, -1.0])
    fn = F1("FQuat::GetRotationAxis", "quat_get_rotation_axis", "FVector Quat_GetRotationAxis(FQuat Q)", [("Q", "quat")], "vec3", lambda a: ML.quat_get_rotation_axis(a["Q"]))
    rng = rng_for("QuatGetRotationAxis")
    for _ in range(N(40)):
        fn.add("random", Q=unit_quat(rng))
    for why, q in [("identity_zero_axis", q0), ("tiny_rotation_1e-5", [1e-5, 0.0, 0.0, 1.0]), ("180", [0.0, 0.0, 1.0, 0.0])]:
        fn.add(E(why), Q=q)
    fn = F1("FVector::RotateAngleAxis", "rotate_angle_axis", "FVector RotateAngleAxis(FVector V, double AngleDeg, FVector Axis)", [("V", "vec3"), ("AngleDeg", F64), ("Axis", "vec3")], "vec3",
            lambda a: ML.rotate_angle_axis(a["V"], a["AngleDeg"], a["Axis"]))
    rng = rng_for("RotateAngleAxis")
    for _ in range(N(40)):
        fn.add("random", V=vec_range(rng, -100, 100), AngleDeg=U(rng, -540, 540), Axis=unit3(rng))
    fn.add(E("axis_not_unit"), V=[1.0, 0.0, 0.0], AngleDeg=90.0, Axis=[0.0, 0.0, 2.0])
    fn.add(E("zero_axis"), V=[1.0, 0.0, 0.0], AngleDeg=90.0, Axis=[0.0, 0.0, 0.0])


# =====================================================================================================================
# engine functions used by the ALS C++ / DAL builtins and whether Python can reach them
# =====================================================================================================================
def engine_reachability():
    def has(*names):
        return [n for n in names if hasattr(ML, n)]

    rows = [
        ("FMath::UnwindDegrees", "only through FVector::UnwindEuler (MathLibrary.vector_unwind_euler)", has("vector_unwind_euler")),
        ("FRotator::NormalizeAxis", "MathLibrary.normalize_axis", has("normalize_axis")),
        ("FRotator::ClampAxis", "MathLibrary.clamp_axis", has("clamp_axis")),
        ("FMath::FInterpTo", "MathLibrary.f_interp_to (double)", has("f_interp_to")),
        ("FMath::FInterpConstantTo", "MathLibrary.f_interp_to_constant (double)", has("f_interp_to_constant")),
        ("FMath::VInterpTo / VInterpConstantTo", "MathLibrary.v_interp_to / v_interp_to_constant", has("v_interp_to", "v_interp_to_constant")),
        ("FMath::Vector2DInterpTo / ConstantTo", "MathLibrary.vector2d_interp_to / vector2d_interp_to_constant", has("vector2d_interp_to", "vector2d_interp_to_constant")),
        ("FMath::RInterpTo / RInterpConstantTo", "MathLibrary.r_interp_to / r_interp_to_constant", has("r_interp_to", "r_interp_to_constant")),
        ("FMath::QInterpTo / QInterpConstantTo", "NOT exposed (no MathLibrary function)", has("q_interp_to", "q_interp_to_constant")),
        ("FQuat::Slerp", "MathLibrary.quat_slerp", has("quat_slerp")),
        ("FQuat::FastLerp", "NOT exposed directly; only inside UAlsRotation::InterpolateQuaternionFast", has("quat_fast_lerp")),
        ("FQuat::Inverse / operator* / GetNormalized", "MathLibrary.quat_inversed / multiply_quat_quat / quat_normalized", has("quat_inversed", "multiply_quat_quat", "quat_normalized")),
        ("FRotator::Quaternion / FQuat::Rotator", "MathLibrary.conv_rotator_to_quaternion / quat_rotator", has("conv_rotator_to_quaternion", "quat_rotator")),
        ("FRotator::GetNormalized / FRotator::Normalize", "NOT exposed (only per component via normalize_axis / normalized_delta_rotator)", has("normalize_rotator")),
        ("FindLookAtRotation / FVector::ToOrientationRotator / ToOrientationQuat", "MathLibrary.find_look_at_rotation / conv_vector_to_rotator / conv_vector_to_quaternion",
         has("find_look_at_rotation", "conv_vector_to_rotator", "conv_vector_to_quaternion")),
        ("FMath::FindDeltaAngleDegrees", "NOT exposed", has("find_delta_angle_degrees", "find_delta_angle")),
        ("FMath::InvExpApprox", "NOT exposed; reachable only through UAlsMath::DamperExactAlpha and the spring dampers", has("inv_exp_approx")),
        ("FMath::InvSqrt", "NOT exposed (MathLibrary.sqrt only); reachable through UAlsVector::ClampMagnitude01", has("inv_sqrt")),
        ("FMath::SinCos(float)", "NOT exposed; reachable through UAlsVector::RadianToDirection*/AngleToDirection*", has("sin_cos")),
        ("FMath::Acos / Asin / Atan / Atan2 / Sin / Cos / Tan / Exp / Loge / Sqrt (double)", "MathLibrary.* (double overloads only; ALS uses the float overloads)", has("acos", "asin", "atan", "atan2", "sin", "cos", "tan", "exp", "loge", "sqrt")),
        ("FMath::Pow", "MathLibrary.multiply_multiply_float_float", has("multiply_multiply_float_float")),
        ("FMath::Fmod", "NOT exposed under a stable name", has("fmod")),
        ("FMath::SpringDamper", "only through UAlsMath::SpringDamperFloat / UAlsVector::SpringDamperVector (MathLibrary.float_spring_interp is a different API)", has("spring_damper")),
        ("SpringMath::CriticalSpringDamper / HalfLifeToSmoothingTime / FMath::ExponentialSmoothingApprox", "NOT exposed", has("critical_spring_damper", "half_life_to_smoothing_time", "exponential_smoothing_approx")),
        ("FQuat::GetTwistAngle / ToSwingTwist", "NOT exposed (UAlsRotation::GetTwist covers the twist part)", has("quat_get_twist_angle", "quat_to_swing_twist")),
        ("FAlphaBlend::AlphaToBlendOption (DAL blend_alpha)", "NOT exposed", has("alpha_to_blend_option")),
        ("FTransform ops (transform_position, relative_transform, ...)", "partly: MathLibrary.transform_location / inverse_transform_location / compose_transforms / convert_transform_to_relative (not covered here)", has("transform_location", "compose_transforms")),
    ]
    return [{"engine_function": n, "python": p, "reachable_names_found": found} for n, p, found in rows]


# =====================================================================================================================
# main
# =====================================================================================================================
NOT_CALLABLE = [
    {"name": "UAlsMath::DamperExact<ValueType>", "reason": "C++ template, no UFUNCTION; it is FMath::Lerp(Current, Target, DamperExactAlpha(DeltaTime, HalfLife)) - use DamperExactAlpha goldens"},
    {"name": "UAlsMath::SpringDamper<ValueType, StateType>", "reason": "C++ template, no UFUNCTION; covered through SpringDamperFloat / SpringDamperVector"},
    {"name": "UAlsRotation::RemapAngleForCounterClockwiseRotation<ValueType>", "reason": "C++ template (the float overload RemapAngleForCounterClockwiseRotation(float) IS covered)"},
    {"name": "UAlsRotation::RemapRotationForCounterClockwiseRotation(VectorRegister4Double)", "reason": "SIMD register API, not reflected; its effect is covered through LerpRotation / DamperExactRotation"},
    {"name": "UAlsVector::ClampMagnitude01(FVector3f)", "reason": "plain C++ overload without UFUNCTION (the FVector overload IS covered)"},
    {"name": "UAlsUtility::BoolToString", "reason": "constexpr C++ helper without UFUNCTION (returns \"True\" / \"False\")"},
    {"name": "UAlsUtility::TryGetMovementBaseAngularVelocity", "reason": "needs a FBasedMovementInfo (live physics body), no UFUNCTION"},
    {"name": "UAlsUtility::GetAnimationCurveValueFromCharacter", "reason": "needs a live ACharacter with an anim instance (skipped on purpose)"},
    {"name": "UAlsUtility::GetFirstPlayerPingSeconds", "reason": "needs a world with a player controller (skipped on purpose)"},
    {"name": "UAlsMontageUtility::StopMontagesWithSlot / StopMontagesWithAnySharedSlots", "reason": "need a live UAnimInstance with playing montages (side effects, no return value)"},
    {"name": "UAlsConstants::*", "reason": "constant FName getters (bone / curve / slot names), no math; not part of T1"},
    {"name": "UAlsDebugUtility::*", "reason": "debug drawing / display flags, no math"},
]


def terminate_process():
    """The UE 5.8 pythonscript commandlet crashes in Python finalization at exit in this host, and os._exit() spins in DLL detach: end the process directly."""
    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = ctypes.c_void_p
    k.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    k.TerminateProcess(k.GetCurrentProcess(), 0)


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    engine = str(unreal.SystemLibrary.get_engine_version())
    plugin_ver = ""
    try:
        up = os.path.join(os.path.dirname(unreal.Paths.get_project_file_path()), "Plugins", "ALS-Refactored", "ALS.uplugin")
        with open(up, "r", encoding="utf-8") as fh:
            plugin_ver = json.load(fh).get("VersionName", "")
    except Exception:
        pass
    meta = {"schema": SCHEMA, "engine": engine, "project": "DreamAnimTestHost", "als_plugin_version": plugin_ver, "generator": GENERATOR}

    gen_als_math()
    gen_als_rotation()
    gen_als_vector()
    gen_als_utility()
    gen_ue_math()
    montage = gen_als_montage()

    index = {"schema": SCHEMA, "engine": engine, "project": "DreamAnimTestHost", "als_plugin_version": plugin_ver, "generator": GENERATOR, "files": {}}
    total_bytes = 0
    for file in ("als_math.json", "als_rotation.json", "als_vector.json", "als_utility.json", "ue_math.json"):
        fns = REG.get(file, [])
        text = dump_doc(file, dict(meta, note=ENGINE_NOTE) if file == "ue_math.json" else meta, fns)
        write_text(os.path.join(OUT_DIR, file), text)
        errors = sum(1 for f in fns for c in f.cases if "error" in c)
        index["files"][file] = {
            "bytes": len(text.encode("utf-8")), "errors": errors,
            "functions": [{"function": f.cname, "python": f.py, "cases": len(f.cases), "sequences": len(f.sequences),
                           "sequence_steps": sum(len(s["rows"]) for s in f.sequences)} for f in fns]}
        total_bytes += len(text.encode("utf-8"))
    # montage file
    mdoc = dict(meta, file="als_montage.json")
    mtext = jline(mdoc)[:-1] + ',"functions":' + jline(montage["function_list"]) + ',"skipped":' + jline(montage["skipped"]) + \
        ',"all_anim_montages_in_registry":' + jline(montage["all_anim_montages_in_registry"]) + \
        ',"montages":[\n' + ",\n".join(jline(m) for m in montage["montages"]) + "\n]}\n"
    write_text(os.path.join(OUT_DIR, "als_montage.json"), mtext)
    index["files"]["als_montage.json"] = {
        "bytes": len(mtext.encode("utf-8")), "errors": 0,
        "functions": [{"function": f["function"], "python": f["python"],
                       "cases": sum(len(m["root_transform_samples"]) for m in montage["montages"]) if "ExtractRootTransform" in f["function"] and "Last" not in f["function"]
                       else (len(montage["montages"]) if "Last" in f["function"] else sum(len(m["root_motion_ranges"]) for m in montage["montages"])),
                       "sequences": 0, "sequence_steps": 0} for f in montage["function_list"]],
        "montages": [m["montage"] for m in montage["montages"]], "skipped_montages": montage["skipped"]}
    total_bytes += len(mtext.encode("utf-8"))
    index["total_bytes_of_data_files"] = total_bytes
    index["not_callable_from_python"] = NOT_CALLABLE
    index["engine_functions_reachability"] = engine_reachability()
    write_text(os.path.join(OUT_DIR, "index.json"), json.dumps(index, indent=1, ensure_ascii=True, allow_nan=False) + "\n")
    unreal.log("[gen_goldens] wrote %d bytes of data into %s" % (total_bytes, OUT_DIR))


try:
    main()
except Exception:
    err = traceback.format_exc()
    unreal.log_error("[gen_goldens] FAILED\n" + err)
    try:
        write_text(os.path.join(OUT_DIR, "_gen_error.txt"), err)
    except Exception:
        pass
    terminate_process()
terminate_process()
