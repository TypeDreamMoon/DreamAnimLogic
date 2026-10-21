#!/usr/bin/env python
"""sanity_check.py -- independent plausibility checks of the ALS goldens (pure Python 3, standard library only).

    python sanity_check.py [goldens_folder]          (default: the folder of this script)

For the simple ALS functions it re-implements the C++ with float32 arithmetic (every operation rounded to float32) and compares the result
with the value UE returned; for the others it checks geometric / algebraic properties. It prints one line per check. A check that
reports "differs" is not necessarily a bug in the goldens: it marks a place where UE's real result is not what the textbook formula gives
(e.g. FMath::Atan2 / SinCos approximations) -- exactly what the DAL tests must reproduce. The exit code is 1 when a hard check fails
(invalid JSON, a float32 input that is not float32 representable, an impossible result).
"""
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
D = sys.argv[1] if len(sys.argv) > 1 else HERE
HARD_FAILS = []


def f32(x):
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0]
    except OverflowError:
        return math.inf if x > 0 else -math.inf


def dec(x):
    """JSON value -> Python (the strings "nan" / "inf" / "-inf" become floats)."""
    if isinstance(x, str):
        return {"nan": math.nan, "inf": math.inf, "-inf": -math.inf}.get(x, x)
    if isinstance(x, list):
        return [dec(i) for i in x]
    if isinstance(x, dict):
        return {k: dec(v) for k, v in x.items()}
    return x


def load(name):
    with open(os.path.join(D, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


def fns(doc):
    return {f["function"]: f for f in doc["functions"]}


def same(a, b, tol=0.0):
    if isinstance(a, list):
        return all(same(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) or math.isnan(b):
            return math.isnan(a) and math.isnan(b)
        if math.isinf(a) or math.isinf(b):
            return a == b
        return abs(a - b) <= tol
    return a == b


def diff(a, b):
    if isinstance(a, list):
        return max(diff(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return 0.0
        if math.isnan(a) or math.isnan(b):
            return math.inf
        if math.isinf(a) or math.isinf(b):
            return 0.0 if a == b else math.inf
        return abs(a - b)
    return 0.0 if a == b else math.inf


def compare(label, f, ref, tol=0.0, skip=lambda c: False, hard=False):
    n = bad = 0
    worst = (0.0, None)
    for c in f["cases"]:
        if skip(c) or c.get("out") is None:
            continue
        i = dec(c["in"])
        o = dec(c["out"])
        try:
            r = ref(i)
        except Exception as exc:  # reference itself failed (e.g. math domain error)
            r = math.nan
        n += 1
        if not same(o, r, tol):
            bad += 1
            d = diff(o, r)
            if d >= worst[0]:
                worst = (d, (c["tag"], c["in"], c["out"], r))
    status = "ok     " if bad == 0 else ("FAIL   " if hard else "differs")
    print("%s %-58s %4d/%-4d match (tol %g)%s" % (status, label, n - bad, n, tol,
                                                    "" if bad == 0 else "  worst %.3g e.g. %s" % (worst[0], json.dumps(worst[1])[:260])))
    if bad and hard:
        HARD_FAILS.append(label)
    return bad


def check(cond, label):
    print("%s %s" % ("ok     " if cond else "FAIL   ", label))
    if not cond:
        HARD_FAILS.append(label)


# ---- the float32 inputs are float32 values ----
def flatten(x):
    if isinstance(x, list):
        for i in x:
            yield from flatten(i)
    else:
        yield x


def check_f32_inputs(doc, fname):
    """float32 parameters must be float32 values; double parameters too, except in the cases tagged double_precision and for values outside
    the float32 range (they are kept as doubles on purpose)."""
    bad = bad_dbl = 0
    for f in doc["functions"]:
        kinds = f.get("param_types", {})
        for c in f["cases"]:
            for k, v in c["in"].items():
                t = kinds.get(k)
                for x in flatten(dec(v)):
                    if not isinstance(x, float) or math.isnan(x) or math.isinf(x) or f32(x) == x:
                        continue
                    if t in ("float32", "vec3f"):
                        bad += 1
                    elif t in ("float64", "vec3", "vec2", "rotator", "quat") and "double_precision" not in c["tag"] and f32(x) not in (math.inf, -math.inf, 0.0):
                        bad_dbl += 1
    check(bad == 0, "%s: all float32-typed inputs are float32-representable" % fname)
    check(bad_dbl == 0, "%s: double-typed inputs are float32-representable too (except double_precision cases / out-of-float-range values)" % fname)


def sincos_f32(x):
    """FMath::SinCos(float&, float&, float) from UnrealMathUtility.h evaluated with float32 arithmetic (a reference implementation for the DAL)."""
    if x != x:
        return math.nan, math.nan
    if math.isinf(x):
        return math.inf, math.inf  # what UE returns (not NaN): the int64 cast / polynomial overflow
    C_INV_2PI = f32(f32(0.31830988618) * f32(0.5))
    TWO_PI = f32(6.28318530717958647692)
    HALF_PI = f32(1.57079632679)
    PI = f32(3.14159265358979323846)
    quotient = f32(C_INV_2PI * x)
    quotient = float(int(f32(quotient + 0.5))) if x >= 0.0 else float(int(f32(quotient - 0.5)))
    y = f32(x - f32(TWO_PI * quotient))
    if y > HALF_PI:
        y = f32(PI - y)
        sign = -1.0
    elif y < -HALF_PI:
        y = f32(-PI - y)
        sign = -1.0
    else:
        sign = 1.0
    y2 = f32(y * y)

    def horner(coeffs):
        # ((((c0 * y2 + c1) * y2 + c2) * y2 + c3) * y2 + c4) * y2 + c5 with a float32 rounding after every operation
        acc = f32(coeffs[0])
        for c in coeffs[1:]:
            acc = f32(f32(acc * y2) + f32(c))
        return acc

    # 11-degree minimax sine and 10-degree minimax cosine (the constants of FMath::SinCos)
    sn = f32(horner([-2.3889859e-08, 2.7525562e-06, -0.00019840874, 0.0083333310, -0.16666667, 1.0]) * y)
    p_ = horner([-2.6051615e-07, 2.4760495e-05, -0.0013888378, 0.041666638, -0.5, 1.0])
    return sn, f32(sign * f32(p_))


def unwind(a):
    while a > 180.0:
        a = f32(a - 360.0)
    while a < -180.0:
        a = f32(a + 360.0)
    return a


def remap(a):
    return f32(a - 360.0) if a > 175.0 else a


def clamp01(x):
    return x if (x > 0.0 and x < 1.0) else (1.0 if x >= 1.0 else 0.0)


def inv_exp_approx(x):
    # FMath::InvExpApprox with float32 constants: 1 / (1 + A x + B x x + C x x x)
    A, B, C = f32(1.00746054), f32(0.45053901), f32(0.25724632)
    t = f32(1.0 + f32(A * x))
    t = f32(t + f32(f32(B * x) * x))
    t = f32(t + f32(f32(f32(C * x) * x) * x))
    return f32(1.0 / t) if t != 0.0 else (math.inf if True else math.nan)


def damper_alpha(dt, hl):
    LN2 = f32(0.6931471805599453)
    SMALL = f32(1e-8)
    den = f32(hl + SMALL)
    if den == 0.0:
        q = math.inf if LN2 > 0 else math.nan
        x = math.nan if dt == 0.0 or dt != dt else (math.inf if dt > 0 else -math.inf)
    else:
        x = f32(f32(LN2 / den) * dt)
    if x != x:
        return math.nan
    if x == math.inf:
        return 1.0
    if x == -math.inf:
        return math.nan
    return f32(1.0 - inv_exp_approx(x))


def main():
    m = load("als_math.json")
    r = load("als_rotation.json")
    v = load("als_vector.json")
    u = load("als_utility.json")
    q = load("als_montage.json")
    e = load("ue_math.json")
    idx = load("index.json")
    for name, doc in (("als_math.json", m), ("als_rotation.json", r), ("als_vector.json", v)):
        check_f32_inputs(doc, name)
    check(all(not c.get("error") for d in (m, r, v, u, e) for f in d["functions"] for c in f["cases"]), "no recorded call errors in any file")
    check(idx["engine"].startswith("5.8"), "index.json engine is " + idx["engine"])

    print("\n== als_math.json ==")
    F = fns(m)
    compare("Clamp01 == C++ ternary", F["UAlsMath::Clamp01"], lambda i: clamp01(i["Value"]), hard=True)
    c1 = {c["tag"]: c for c in F["UAlsMath::Clamp01"]["cases"]}
    check(dec(c1["edge:one"]["out"]) == 1.0 and dec(c1["random:grid"]["out"]) is not None, "Clamp01 spot checks")
    compare("LerpClamped == f32(From + (To - From) * Clamp01(Ratio))", F["UAlsMath::LerpClamped"],
            lambda i: f32(i["From"] + f32(f32(i["To"] - i["From"]) * clamp01(i["Ratio"]))), tol=0.0)
    compare("LerpClamped within 1e-4 relative", F["UAlsMath::LerpClamped"],
            lambda i: f32(i["From"] + f32(f32(i["To"] - i["From"]) * clamp01(i["Ratio"]))),
            tol=1e-4 * 1000, skip=lambda c: c["tag"].startswith("edge"))
    f = F["UAlsMath::DamperExactAlpha"]
    compare("DamperExactAlpha == float32 InvExpApprox formula", f, lambda i: damper_alpha(i["DeltaTime"], i["HalfLife"]), tol=0.0)
    compare("DamperExactAlpha == float32 formula within 2e-7", f, lambda i: damper_alpha(i["DeltaTime"], i["HalfLife"]), tol=2e-7)
    worst = 0.0
    for c in f["cases"]:
        dt, hl = dec(c["in"]["DeltaTime"]), dec(c["in"]["HalfLife"])
        o = dec(c["out"])
        if c["tag"].startswith("edge") or hl < 0.05 or dt <= 0:
            continue
        worst = max(worst, abs(o - (1.0 - math.exp(-math.log(2.0) / hl * dt))))
    print("info    DamperExactAlpha vs exact 1 - 2^(-dt/hl): max abs error on the random cases with hl >= 0.05 = %.3g" % worst)
    check(worst < 2e-3, "DamperExactAlpha approximates 1 - exp(-ln2/hl*dt) (error < 2e-3)")
    xs = [i / 100.0 for i in range(0, 1001)]
    print("info    InvExpApprox vs exp(-x): max abs error for x in [0,10] = %.3g, for x in [0,1] = %.3g" % (
        max(abs(inv_exp_approx(f32(x)) - math.exp(-x)) for x in xs), max(abs(inv_exp_approx(f32(x)) - math.exp(-x)) for x in xs if x <= 1.0)))

    def move_dir(i):
        a, fha, thr = i["Angle"], i["ForwardHalfAngle"], i["AngleThreshold"]
        nf = f32(-fha - thr) if False else f32(f32(-fha) - thr)
        pf = f32(fha + thr)
        if a >= nf and a <= pf:
            return 0
        if a >= f32(fha - thr) and a <= f32(f32(180.0 - fha) + thr):
            return 3
        if a <= -f32(fha - thr) and a >= -f32(f32(180.0 - fha) + thr):
            return 2
        return 1
    n = bad = 0
    for c in F["UAlsMath::CalculateMovementDirection"]["cases"]:
        n += 1
        if c["out"]["value"] != move_dir(dec(c["in"])):
            bad += 1
    check(bad == 0, "CalculateMovementDirection == C++ with float32 arithmetic (%d/%d cases)" % (n - bad, n))
    check(all(c["out"]["name"] == ["Forward", "Backward", "Left", "Right"][c["out"]["value"]] for c in F["UAlsMath::CalculateMovementDirection"]["cases"]),
          "CalculateMovementDirection enum names match values")

    # pole vector geometry
    nt = nf = 0
    ok = True
    for c in F["UAlsMath::TryCalculatePoleVector"]["cases"]:
        res, proj, pole = c["out"]
        a, b, cc = dec(c["in"]["ALocation"]), dec(c["in"]["BLocation"]), dec(c["in"]["CLocation"])
        if not res:
            nf += 1
            continue
        nt += 1
        proj, pole = dec(proj), dec(pole)
        ln = math.sqrt(sum(x * x for x in pole))
        ac = [cc[i] - a[i] for i in range(3)]
        acn = math.sqrt(sum(x * x for x in ac))
        if abs(ln - 1.0) > 1e-9:
            ok = False
        if acn > 1e-4:
            acu = [x / acn for x in ac]
            if abs(sum(pole[i] * acu[i] for i in range(3))) > 1e-6:
                ok = False  # pole direction is perpendicular to AC
            t = sum((proj[i] - a[i]) * acu[i] for i in range(3))
            off = [proj[i] - a[i] - t * acu[i] for i in range(3)]
            if math.sqrt(sum(x * x for x in off)) > 1e-6 * max(1.0, abs(t)):
                ok = False  # projection lies on the line AC
            bm = [b[i] - proj[i] for i in range(3)]
            if sum(bm[i] * pole[i] for i in range(3)) < 0:
                ok = False  # B is on the pole side of the projection
    check(ok, "TryCalculatePoleVector: %d true results are geometrically sensible (unit pole, perpendicular to AC, projection on AC, B on the pole side); %d false results" % (nt, nf))
    pc = {c["tag"]: c for c in F["UAlsMath::TryCalculatePoleVector"]["cases"]}
    check(pc["edge:b_on_ac_line_collinear"]["out"][0] is False and pc["edge:a_equals_b"]["out"][0] is False and pc["edge:a_equals_c"]["out"][0] is True,
          "TryCalculatePoleVector: collinear and A==B are false, A==C is true")
    check(pc["edge:right_angle_knee"]["out"] == [True, [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], "TryCalculatePoleVector: right angle knee gives projection (1,0,0), pole (0,1,0)")

    # springs
    sf = F["UAlsMath::SpringDamperFloat"]
    ok = True
    for c in sf["cases"]:
        i, si, o, so = dec(c["in"]), dec(c["state_in"]), dec(c["out"]), dec(c["state_out"])
        if i["DeltaTime"] <= f32(1e-8):
            ok &= (o == i["Current"] and so == si)
        elif not si["bStateValid"]:
            ok &= (o == i["Target"] and so["Velocity"] == 0.0 and so["PreviousTarget"] == i["Target"] and so["bStateValid"] is True)
        else:
            ok &= (so["PreviousTarget"] == i["Target"] and so["bStateValid"] is True) or math.isnan(i["Target"])
    check(ok, "SpringDamperFloat single steps: dt <= 1e-8 returns Current untouched; invalid state returns Target and resets; PreviousTarget = Target")
    for s in sf["sequences"]:
        cur = s["current_start"]
        state = dict(s["state_start"])
        ok = True
        for k, row in enumerate(s["rows"]):
            for ev in s["events"]:
                if ev["step"] == k:
                    state = {"Velocity": 0.0, "PreviousTarget": 0.0, "bStateValid": False}
            tgt, dt, o = dec(row[0]), dec(row[1]), dec(row[2])
            if k == 0:
                ok &= (o == tgt) and row[5] is True  # first call: invalid state returns Target
            if dt <= f32(1e-8):
                ok &= (o == cur)
            cur = o
        tail = [dec(r_[2]) for r_ in s["rows"]][-1]
        print("info    spring chain %-48s steps %-3d last out %.6g last target %.6g" % (s["tag"], len(s["rows"]), tail, dec(s["rows"][-1][0])))
        check(ok, "spring chain %s: step 0 returns Target (invalid state), dt<=1e-8 steps return Current" % s["tag"])

    print("\n== als_rotation.json ==")
    F = fns(r)
    compare("RemapAngleForCounterClockwiseRotation == (Angle > 175 ? Angle - 360 : Angle)", F["UAlsRotation::RemapAngleForCounterClockwiseRotation"], lambda i: remap(i["Angle"]), hard=True)

    def lerp_angle(i):
        d = remap(unwind(f32(i["To"] - i["From"])))
        return unwind(f32(i["From"] + f32(d * i["Ratio"])))
    compare("LerpAngle == float32 re-implementation", F["UAlsRotation::LerpAngle"], lerp_angle, tol=0.0, skip=lambda c: c["tag"].startswith("edge:huge") or c["tag"].startswith("edge:1e5"))
    compare("LerpAngle within 1e-3 of re-implementation", F["UAlsRotation::LerpAngle"], lerp_angle, tol=1e-3, skip=lambda c: c["tag"].startswith("edge:huge") or c["tag"].startswith("edge:1e5"))
    la = {c["tag"]: dec(c["out"]) for c in F["UAlsRotation::LerpAngle"]["cases"]}
    check(la["edge:pair_170_-170_ratio_0.5"] in (180.0, -180.0), "LerpAngle(170, -170, 0.5) = %r (+-180)" % la["edge:pair_170_-170_ratio_0.5"])
    check(abs(la["edge:pair_0_180_ratio_0.5"] - -90.0) < 1e-4 and abs(la["edge:pair_0_-180_ratio_0.5"] - -90.0) < 1e-4,
          "LerpAngle(0, +-180, 0.5) = -90 (the counter-clockwise remap sends both ways negative): %r %r" % (la["edge:pair_0_180_ratio_0.5"], la["edge:pair_0_-180_ratio_0.5"]))

    def iac(i):
        cur, tgt, dt, sp = i["Current"], i["Target"], i["DeltaTime"], i["Speed"]
        delta = unwind(f32(tgt - cur))
        md = f32(sp * dt)
        if sp <= 0.0 or abs(delta) <= md:
            return tgt
        delta = remap(delta)
        s = 1.0 if delta > 0 else (-1.0 if delta < 0 else 0.0)
        return unwind(f32(cur + f32(s * md)))
    compare("InterpolateAngleConstant == float32 re-implementation", F["UAlsRotation::InterpolateAngleConstant"], iac, tol=0.0,
            skip=lambda c: "nan" in c["tag"] or "1e7" in c["tag"])

    def dea(i):
        cur, tgt, dt, hl = i["Current"], i["Target"], i["DeltaTime"], i["HalfLife"]
        delta = unwind(f32(tgt - cur))
        if abs(delta) <= f32(1e-4):
            return tgt
        delta = remap(delta)
        al = damper_alpha(dt, hl)
        return unwind(f32(cur + f32(delta * al)))
    compare("DamperExactAngle == float32 re-implementation", F["UAlsRotation::DamperExactAngle"], dea, tol=0.0,
            skip=lambda c: "nan" in c["tag"] or "1e7" in c["tag"])
    compare("DamperExactAngle within 1e-3 of re-implementation", F["UAlsRotation::DamperExactAngle"], dea, tol=1e-3,
            skip=lambda c: "nan" in c["tag"] or "1e7" in c["tag"])

    def wrap_axis(a):
        a = math.fmod(a, 360.0)
        if a > 180.0:
            a -= 360.0
        elif a < -180.0:
            a += 360.0
        return a

    def lerp_rot(i):
        out = []
        for k in range(3):
            d = wrap_axis(i["To"][k] - i["From"][k])
            out.append(d)
        if all(abs(x) <= 1e-4 for x in out):
            return list(i["To"])
        res = []
        for k in range(3):
            d = out[k]
            if d >= 175.0:
                d -= 360.0
            res.append(wrap_axis(i["From"][k] + d * i["Ratio"]))
        return res
    compare("LerpRotation within 1e-4 of the per-axis double re-implementation", F["UAlsRotation::LerpRotation"], lerp_rot, tol=1e-4,
            skip=lambda c: "nan" in c["tag"])

    def damp_rot(i):
        out = []
        for k in range(3):
            out.append(wrap_axis(i["Target"][k] - i["Current"][k]))
        if all(abs(x) <= 1e-4 for x in out):
            return list(i["Target"])
        al = damper_alpha(i["DeltaTime"], i["HalfLife"])
        res = []
        for k in range(3):
            d = out[k]
            if d >= 175.0:
                d -= 360.0
            res.append(wrap_axis(i["Current"][k] + d * al))
        return res
    compare("DamperExactRotation within 1e-3 of the per-axis re-implementation", F["UAlsRotation::DamperExactRotation"], damp_rot, tol=1e-3,
            skip=lambda c: "nan" in c["tag"])

    def qnorm(x):
        n = math.sqrt(sum(c * c for c in x))
        return [c / n for c in x] if n * n >= 1e-8 else [0.0, 0.0, 0.0, 1.0]

    def iqf(i):
        c, t, dt, sp = i["Current"], i["Target"], i["DeltaTime"], i["Speed"]
        if sp <= 0.0 or all(abs(c[k] - t[k]) <= 1e-4 for k in range(4)):
            return list(t)
        a = clamp01(f32(sp * dt))
        dot = sum(c[k] * t[k] for k in range(4))
        bias = 1.0 if dot >= 0 else -1.0
        res = [t[k] * a + c[k] * (bias * (1.0 - a)) for k in range(4)]
        return qnorm(res)
    compare("InterpolateQuaternionFast within 1e-6 of FastLerp + normalize", F["UAlsRotation::InterpolateQuaternionFast"], iqf, tol=1e-6)

    def twist(i):
        qq, ax = i["Quaternion"], i["TwistAxis"]
        d = sum(ax[k] * qq[k] for k in range(3))
        p = [d * ax[k] for k in range(3)]
        return qnorm(p + [qq[3]])
    compare("GetTwist within 1e-6 of the projection formula", F["UAlsRotation::GetTwist"], twist, tol=1e-6)

    print("\n== als_vector.json ==")
    F = fns(v)

    def clamp_mag(i):
        x = i["Vector"]
        s2 = sum(c * c for c in x)
        if s2 <= 1.0:
            return list(x)
        sc = 1.0 / math.sqrt(s2)
        return [c * sc for c in x]
    compare("ClampMagnitude01(FVector) == v * 1/sqrt(|v|^2)", F["UAlsVector::ClampMagnitude01(FVector)"], clamp_mag, tol=1e-12, skip=lambda c: "overflow" in c["tag"] or "1e200" in c["tag"])
    compare("ClampMagnitude012D == v * 1/sqrt(|v|^2)", F["UAlsVector::ClampMagnitude012D"], clamp_mag, tol=1e-12, skip=lambda c: "1e155" in c["tag"])
    worst = {}
    for name, key, pick in (("RadianToDirection", "Radian", lambda a: a), ("RadianToDirectionXY", "Radian", lambda a: a),
                            ("AngleToDirection", "Angle", math.radians), ("AngleToDirectionXY", "Angle", math.radians)):
        w = 0.0
        for c in F["UAlsVector::" + name]["cases"]:
            a = dec(c["in"][key])
            o = dec(c["out"])
            if a != a or abs(a) > 1e3:
                continue
            ang = pick(a)
            w = max(w, abs(o[0] - math.cos(ang)), abs(o[1] - math.sin(ang)))
        worst[name] = w
        print("info    %-24s max |UE - libm cos/sin| over |input| <= 1e3: %.3g" % (name, w))
        # exactness of the float32 re-implementation of FMath::SinCos (radians; the degree variants first convert with DegreesToRadians(float))
        n = bad = 0
        wd = 0.0
        for c in F["UAlsVector::" + name]["cases"]:
            a = dec(c["in"][key])
            o = dec(c["out"])
            if a != a or abs(a) > 1e5:
                continue
            rad = a if key == "Radian" else f32(a * f32(math.pi / 180.0))
            sn, cs = sincos_f32(rad)
            n += 1
            d = max(abs(o[0] - cs), abs(o[1] - sn))
            wd = max(wd, d)
            bad += d > 0.0
        print("info    %-24s float32 re-implementation of FMath::SinCos: %d/%d bit-exact, max diff %.3g (|input| <= 1e5)" % (name, n - bad, n, wd))
    for name in ("DirectionToAngle", "DirectionToAngleXY"):
        w = 0.0
        wc = None
        flips = []
        for c in F["UAlsVector::" + name]["cases"]:
            d = dec(c["in"]["Direction"])
            o = dec(c["out"])
            ref = math.degrees(math.atan2(d[1], d[0]))
            if abs(o - ref) > 359.0:
                flips.append((d[:2], o, ref))  # +180 vs -180: the sign of a negative zero Y is lost in FMath::Atan2
            elif abs(o - ref) > w:
                w, wc = abs(o - ref), (d, o, ref)
        print("info    %-24s max |UE - math.degrees(math.atan2)| = %.3g degrees  worst %s; +-180 flips: %s" % (name, w, wc, flips))

    for name, fk in (("PerpendicularClockwiseXY", lambda x: [x[1], -x[0], x[2]]), ("PerpendicularCounterClockwiseXY", lambda x: [-x[1], x[0], x[2]])):
        compare(name + " == (Y,-X,Z) / (-Y,X,Z)", F["UAlsVector::" + name], lambda i, fk=fk: fk(i["Vector"]), hard=True)
    compare("AngleBetweenSkipNormalization == degrees(acos(clamp(dot)))", F["UAlsVector::AngleBetweenSkipNormalization"],
            lambda i: math.degrees(math.acos(max(-1.0, min(1.0, sum(a * b for a, b in zip(i["From"], i["To"])))))), tol=1e-9)

    def abs_xy(i):
        def nrm(x):
            # FVector2f::Normalize: |v|^2 <= 1e-8 sets the vector to zero
            x = [x[0], x[1]]
            s = f32(f32(x[0] * x[0]) + f32(x[1] * x[1]))
            if s > f32(1e-8) and math.isfinite(s):
                return [f32(c * f32(1.0 / math.sqrt(s))) for c in x]
            return [0.0, 0.0]
        a, b = nrm(i["From"]), nrm(i["To"])
        dot = a[0] * b[0] + a[1] * b[1]
        cr = a[0] * b[1] - a[1] * b[0]
        sg = 1.0 if cr > 0 else (-1.0 if cr < 0 else 0.0)
        return math.degrees(math.acos(max(-1.0, min(1.0, dot)))) * sg
    compare("AngleBetweenSignedXY within 5e-2 degrees of double acos(dot) * sign(cross) (float32 acos loses precision near 0 / 180)", F["UAlsVector::AngleBetweenSignedXY"], abs_xy, tol=5e-2,
            skip=lambda c: "nan" in c["tag"] or "huge" in c["tag"])

    def abs_xy_f32(i):
        def nrm(x):
            x = [f32(x[0]), f32(x[1])]
            s = f32(f32(x[0] * x[0]) + f32(x[1] * x[1]))
            if s > f32(1e-8):
                if math.isinf(s):
                    return [0.0, 0.0]
                sc = f32(1.0 / f32(math.sqrt(s)))
                return [f32(x[0] * sc), f32(x[1] * sc)]
            return [0.0, 0.0]
        a, b = nrm(i["From"]), nrm(i["To"])
        dot = f32(f32(a[0] * b[0]) + f32(a[1] * b[1]))
        cr = f32(f32(a[0] * b[1]) - f32(a[1] * b[0]))
        sg = 1.0 if cr > 0 else (-1.0 if cr < 0 else 0.0)
        ac = f32(math.acos(max(-1.0, min(1.0, dot))))
        return f32(f32(ac * f32(180.0 / math.pi)) * sg)
    compare("AngleBetweenSignedXY == float32 re-implementation (acosf, 1/sqrtf)", F["UAlsVector::AngleBetweenSignedXY"], abs_xy_f32, tol=1e-4,
            skip=lambda c: "nan" in c["tag"])
    ab = {c["tag"]: dec(c["out"]) for c in F["UAlsVector::AngleBetweenSignedXY"]["cases"]}
    check(ab["edge:x_to_y_ccw_90"] == 90.0 and ab["edge:y_to_x_cw_-90"] == -90.0, "AngleBetweenSignedXY(X, Y) = +90, (Y, X) = -90")
    print("info    AngleBetweenSignedXY(X, -X) = %r (exactly opposite vectors: cross product is 0 and FMath::Sign(0) = 0)" % ab["edge:exactly_opposite_returns_0"])

    ok = True
    for c in F["UAlsVector::SlerpSkipNormalization"]["cases"]:
        if c["tag"].startswith("random:unit_vectors"):
            o = dec(c["out"])
            ok &= abs(math.sqrt(sum(x * x for x in o)) - 1.0) < 1e-5
    check(ok, "SlerpSkipNormalization of unit vectors returns unit vectors (random:unit_vectors)")
    sv = F["UAlsVector::SpringDamperVector"]
    ok = True
    for s in sv["sequences"]:
        row0 = s["rows"][0]
        ok &= dec(row0[2]) == dec(row0[0]) and row0[5] is True
    check(ok, "SpringDamperVector chains: step 0 returns Target")

    print("\n== als_utility.json ==")
    F = fns(u)
    out = {c["in"]["Tag"]: c["out"] for c in F["UAlsUtility::GetSimpleTagName"]["cases"]}
    check(out["Als.Gait.Walking"] == "Walking" and out["Bogus.Tag"] == "None", "GetSimpleTagName(Als.Gait.Walking) = Walking, unregistered = None")
    ch = {c["in"]["Tag"]: c["out"] for c in F["UAlsUtility::GetChildTags"]["cases"]}
    check(sorted(ch["Als.Gait"]) == ["Als.Gait.Running", "Als.Gait.Sprinting", "Als.Gait.Walking"], "GetChildTags(Als.Gait) = Running / Sprinting / Walking")
    nd = {(c["in"]["Name"], c["in"]["bNameIsBool"]): c["out"] for c in F["UAlsUtility::NameToDisplayString"]["cases"]}
    check(nd[("bIsFooBar", True)] == "Is Foo Bar" and nd[("MaxWalkSpeed", False)] == "Max Walk Speed", "NameToDisplayString spot checks")

    print("\n== als_montage.json ==")
    for mt in q["montages"]:
        first = dec(mt["root_transform_samples"][0]["out"])
        s15 = [s for s in mt["root_transform_samples"] if s["tag"] == "sample_15_of_15"][0]
        last = dec(mt["last_root_transform"])
        full = [x for x in mt["root_motion_ranges"] if x["tag"] == "full"][0]
        fo = dec(full["out"])
        d_last = max(abs(a - b) for a, b in zip(dec(s15["out"])["translation"], last["translation"]))
        d_rm = max(abs((last["translation"][k] - first["translation"][k]) - fo["translation"][k]) for k in range(3))
        print("info    %-92s len %.4f  |sample(len)-last| %.3g  |full root motion - (last-first)| %.3g" % (mt["montage"], mt["play_length"], d_last, d_rm))

    print("\n== ue_math.json ==")
    F = fns(e)
    compare("NormalizeAxis in (-180, 180]", F["FRotator::NormalizeAxis"], lambda i: (lambda a: 180.0 if a == -180.0 else a)(wrap_axis(i["Angle"])), tol=2e-3, skip=lambda c: "1e7" in c["tag"])
    ev = F["FMath::UnwindDegrees (via FVector::UnwindEuler)"]
    compare("UnwindEuler == float32 UnwindDegrees loop", ev, lambda i: [unwind(x) for x in i["A"]], tol=0.0, skip=lambda c: "1e7" in c["tag"])
    ok = all(abs(math.sqrt(sum(x * x for x in dec(c["out"]))) - 1.0) < 1e-6 for c in F["FQuat::Slerp"]["cases"] if c["tag"] == "random")
    check(ok, "FQuat::Slerp random results are unit quaternions")

    if HARD_FAILS:
        print("\nHARD FAILURES:", HARD_FAILS)
        sys.exit(1)
    print("\nall hard checks passed")


main()
