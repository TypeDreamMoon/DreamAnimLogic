"""Component assignment, the quaternion / vector / spring / easing built-ins and the `transform` type (G12-G14)."""

import math
import os
import sys
import unittest

import _bootstrap  # noqa: F401

import dal
import test_cpp_compile as tcc
from dal import harness, ir
from dal import runtime as rt
from dal import settings as dsettings
from dal.cppbackend import generate_cpp
from dal.interp import Interpreter
from dal.pybackend import generate_python, load_class

HDR = "logic Lg;\n"


def errors(src):
    try:
        dal.check_source(src, "t.dal")
    except dal.DalError as e:
        return [(d.code, d.pos.line, d.pos.col) for d in e.diagnostics if d.severity == "error"]
    return []


def compile_ok(src):
    return dal.check_source(src, "t.dal").module


def run(src, frames, **params):
    m = compile_ok(src)
    a, b = harness.run_interpreter(m, frames, **params), harness.run_python(m, frames, **params)
    assert harness.compare(a, b) == [], harness.compare(a, b)[:3]
    return a


def frame(dt=0.1, **free):
    return {"dt": dt, "free": free}


def near(test, a, b, places=5):
    test.assertEqual(len(a), len(b))
    for x, y in zip(a, b):
        test.assertAlmostEqual(x, y, places=places)


# ---- component assignment (G14) ------------------------------------------------------------------------------------------

class ComponentAssignTests(unittest.TestCase):
    def codes(self, body, header=HDR):
        return [(c, l, k) for c, l, k in errors(header + body)]

    def test_the_forms(self):
        ok = [
            "state V: vec3;\nupdate(dt) { V.z = 1.0; }",
            "state V: vec2;\nupdate(dt) { V.y += 2.0; V.x *= 3; }",
            "state R: rotator;\nupdate(dt) { R.yaw = R.yaw + 10.0; R.pitch -= 1.0; R.roll /= 2.0; }",
            "state Q: quat;\nupdate(dt) { Q.w = 0.5; }",
            "struct S { Look: rotator; Lean: vec2; }\nstate P: S;\nupdate(dt) { P.Look.yaw += 5.0; P.Lean.x = 1.0; }",
            "struct In { V: vec3; }\nstruct Out { A: In; }\nstate P: Out;\nupdate(dt) { P.A.V.z = 2.0; }",
            "update(dt) { var v = vec3(1.0, 2.0, 3.0); v.x = v.y; }",
            "state T: transform;\nupdate(dt) { T.location = vec3(1.0, 2.0, 3.0); T.rotation = quat(0.0, 0.0, 0.0, 1.0); T.scale = vec3(2.0, 2.0, 2.0); }",
            "state T: transform;\nupdate(dt) { T.location.z = 4.0; T.scale.x *= 2.0; T.rotation.w = 1.0; }",
            "struct S { T: transform; }\nstate P: S;\nupdate(dt) { P.T.location.y += 1.0; }",
        ]
        for body in ok:
            with self.subTest(body=body):
                self.assertEqual(self.codes(body), [])

    def test_errors(self):
        cases = [
            ("state V: vec2;\nupdate(dt) { V.z = 1.0; }", [("DAL4030", 3, 16)]),
            ("state V: vec3;\nupdate(dt) { V.xy = vec2(1.0, 2.0); }", [("DAL2010", 3, 16)]),
            ("state V: vec3;\nupdate(dt) { V.x.y = 1.0; }", [("DAL4030", 3, 18)]),
            ("state V: vec3;\nupdate(dt) { V.x = true; }", [("DAL4002", 3, 20)]),
            ("input V: vec3;\nupdate(dt) { V.x = 1.0; }", [("DAL5001", 3, 14)]),
            ("update(dt) { let v = vec3(1.0, 2.0, 3.0); v.x = 1.0; }", [("DAL5001", 2, 43)]),
            ("fn f(a: vec3) -> vec3 { var b = a; b.x = 1.0; return b; }\nstate V: vec3;\nupdate(dt) { V = f(V); }", []),
            ("state R: rotator;\nupdate(dt) { R.x = 1.0; }", [("DAL4030", 3, 16)]),
            ("state T: transform;\nupdate(dt) { T.nope = vec3(1.0, 1.0, 1.0); }", [("DAL4030", 3, 16)]),
            ("state T: transform;\nupdate(dt) { T.location = 1.0; }", [("DAL4002", 3, 27)]),
            ("state T: transform;\nupdate(dt) { T.rotation.yaw = 1.0; }", [("DAL4030", 3, 25)]),
            ("state T: transform;\nupdate(dt) { T.location.z.x = 1.0; }", [("DAL4030", 3, 25)]),
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual(self.codes(body), expected)

    def test_the_vector_is_rebuilt(self):
        text = ir.dump(compile_ok(HDR + "state V: vec3;\nstate L: rotator;\nupdate(dt) { V.z = 3.0; L.yaw += 1.0; }"))
        self.assertIn("state:V = vec3[0](state:V.x, state:V.y, 3.0)", text)
        self.assertIn("state:L = rotator[0](state:L.pitch, (state:L.yaw + 1.0), state:L.roll)", text)

    def test_values(self):
        src = """
struct Look { Rotation: rotator; Lean: vec2; }
logic Lg;
input Dx: float;
state V: vec3 = vec3(1.0, 2.0, 3.0);
state R: rotator;
state Q: quat = quat(0.0, 0.0, 0.0, 1.0);
state L: Look;
update(dt) {
    V.z = 10.0 + Dx;
    V.x += 1.0;
    R.yaw = R.yaw + 10.0;
    R.pitch -= 2.0;
    Q.w = 0.5;
    L.Rotation.yaw += Dx;
    L.Rotation.roll = 5;
    L.Lean.x *= 2.0;
    L.Lean.y = 1.0;
}
"""
        out = run(src, [frame(Dx=1.5), frame(Dx=2.5)])
        self.assertEqual(out[0]["V"], (2.0, 2.0, 11.5))
        self.assertEqual(out[1]["V"], (3.0, 2.0, 12.5))
        self.assertEqual(out[1]["R"], (-4.0, 20.0, 0.0))
        self.assertEqual(out[1]["Q"], (0.0, 0.0, 0.0, 0.5))
        self.assertEqual(out[1]["L.Rotation"], (0.0, 4.0, 5.0))
        self.assertEqual(out[1]["L.Lean"], (0.0, 1.0))

    def test_the_sequence_inside_one_update_sees_earlier_assignments(self):
        out = run("logic Lg;\nstate V: vec3;\nupdate(dt) { V.x = 1.0; V.y = V.x + 1.0; V.z = V.y + V.x; }", [frame()])
        self.assertEqual(out[0]["V"], (1.0, 2.0, 3.0))

    def test_transform_parts(self):
        src = """
logic Lg;
input P: vec3;
state T: transform;
update(dt) {
    T.location = P;
    T.location.z += 1.0;
    T.scale.x = 2.0;
    T.rotation.w = 0.5;
}
"""
        out = run(src, [frame(P=(1.0, 2.0, 3.0))])
        self.assertEqual(out[0]["T"], ((1.0, 2.0, 4.0), (0.0, 0.0, 0.0, 0.5), (2.0, 1.0, 1.0)))

    def test_consistency_on_random_frames(self):
        src = """
struct Look { Rotation: rotator; Lean: vec2; }
logic Lg;
input Dx: float;
input Q: quat;
state V: vec3;
state L: Look;
state T: transform;
update(dt) {
    V.z = Dx;
    V.x += V.z;
    L.Rotation.yaw += Dx;
    L.Lean.x = V.x * 0.5;
    T.location.y = Dx;
    T.rotation = Q;
    T.scale.z *= 1.5;
}
"""
        m = compile_ok(src)
        for seed in (1, 2, 3):
            self.assertEqual(harness.verify(m, 80, seed), [])

    @unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
    def test_cpp_matches_python(self):
        src = """
struct Look { Rotation: rotator; Lean: vec2; }
logic Lg : actor;
input Dx: float <- host.max_walk_speed;
input Q: quat;
state V: vec3;
state R: rotator;
state L: Look;
state T: transform;
update(dt) {
    V.z = Dx;
    V.x += V.z;
    R.yaw = R.yaw + Dx;
    L.Rotation.yaw += Dx;
    L.Lean.x = V.x * 0.5;
    T.location.y = Dx;
    T.rotation = Q;
    T.scale.z *= 1.5;
    T.location.x -= 1.0;
}
"""
        m = compile_ok(src)
        for seed in (1, 2):
            tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 60, seed), warnings_as_errors=True, default=2e-3)
        cpp = generate_cpp(m)["Lg.cpp"]
        self.assertIn("V = FVector3f(V.X, V.Y, Dx);", cpp)
        self.assertIn("T = FTransform3f(T.GetRotation(), FVector3f(T.GetLocation().X, Dx, T.GetLocation().Z), T.GetScale3D());", cpp)


# ---- quaternion and vector helpers (G13) --------------------------------------------------------------------------------------

class HelperTests(unittest.TestCase):
    def one(self, expr, decls="", **free):
        names = "".join(f"input {k}: {v[0]};\n" for k, v in free.items())
        src = f"logic Lg;\n{names}{decls}state R: {{ty}};\nupdate(dt) {{ R = {expr}; }}"
        return src

    def call(self, ty, expr, **free):
        names = "".join(f"input {k}: {t};\n" for k, (t, _v) in free.items())
        src = f"logic Lg;\n{names}state R: {ty};\nupdate(dt) {{ R = {expr}; }}"
        return run(src, [frame(**{k: v for k, (_t, v) in free.items()})])[0]["R"]

    def test_quat_from_axis_angle(self):
        q = self.call("quat", "quat_from_axis_angle(vec3(0.0, 0.0, 1.0), HALF_PI)")
        near(self, q, (0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4)))
        v = self.call("vec3", "rotate(quat_from_axis_angle(vec3(0.0, 0.0, 1.0), HALF_PI), vec3(1.0, 0.0, 0.0))")
        near(self, v, (0.0, 1.0, 0.0))

    def test_quat_from_rotation_vector(self):
        q = self.call("quat", "quat_from_rotation_vector(vec3(0.0, 0.0, HALF_PI))")
        near(self, q, (0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4)))
        near(self, self.call("quat", "quat_from_rotation_vector(vec3(0.0, 0.0, 0.0))"), (0.0, 0.0, 0.0, 1.0))
        near(self, self.call("quat", "quat_from_rotation_vector(vec3(1.0e-9, 0.0, 0.0))"), (5.0e-10, 0.0, 0.0, 1.0), places=9)

    def test_to_orientation_quat_faces_the_vector_without_roll(self):
        v = (3.0, 4.0, 5.0)
        q = self.call("quat", "to_orientation_quat(V)", V=("vec3", v))
        near(self, q, self.call("quat", "to_quat(look_at(vec3(0.0, 0.0, 0.0), V))", V=("vec3", v)))
        n = self.call("vec3", "rotate(to_orientation_quat(V), vec3(1.0, 0.0, 0.0))", V=("vec3", v))
        length = math.sqrt(sum(c * c for c in v))
        near(self, n, tuple(c / length for c in v))

    def test_fast_lerp_takes_the_short_way_and_is_not_normalized(self):
        a, b = (0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0, -1.0)
        q = self.call("quat", "fast_lerp(A, B, 0.25)", A=("quat", a), B=("quat", b))
        near(self, q, (0.0, 0.0, 0.0, 0.25 * -1.0 + 0.75 * -1.0))        # B = -A: the bias flips A, so the result is -A
        half = self.call("quat", "fast_lerp(A, B, 0.5)", A=("quat", (0.0, 0.0, 0.0, 1.0)), B=("quat", (0.0, 0.0, 1.0, 0.0)))
        near(self, half, (0.0, 0.0, 0.5, 0.5))
        self.assertAlmostEqual(sum(c * c for c in half), 0.5)                # not normalized

    def test_twist(self):
        axis = (0.0, 0.0, 1.0)
        yaw30 = self.call("quat", "to_quat(rotator(0.0, 30.0, 0.0))")
        t = self.call("quat", "get_twist(Q, A)", Q=("quat", yaw30), A=("vec3", axis))
        near(self, t, yaw30)
        self.assertAlmostEqual(self.call("float", "twist_angle(Q, A)", Q=("quat", yaw30), A=("vec3", axis)), math.radians(30.0), places=5)
        pitch = self.call("quat", "to_quat(rotator(40.0, 0.0, 0.0))")
        near(self, self.call("quat", "get_twist(Q, A)", Q=("quat", pitch), A=("vec3", axis)), (0.0, 0.0, 0.0, 1.0))      # no twist about Z
        mixed = self.call("quat", "to_quat(rotator(40.0, 30.0, 0.0))")
        self.assertAlmostEqual(self.call("float", "twist_angle(Q, A)", Q=("quat", mixed), A=("vec3", axis)),
                               2.0 * math.atan2(mixed[2], mixed[3]), places=5)
        self.assertAlmostEqual(self.call("float", "twist_angle(Q, A)", Q=("quat", (0.0, 0.0, 1.0, 0.0)), A=("vec3", axis)), math.pi, places=5)

    def test_vector_helpers(self):
        near(self, self.call("vec3", "project_on_normal(V, N)", V=("vec3", (3.0, 4.0, 5.0)), N=("vec3", (0.0, 1.0, 0.0))), (0.0, 4.0, 0.0))
        near(self, self.call("vec3", "safe_normal_2d(V)", V=("vec3", (3.0, 4.0, 5.0))), (0.6, 0.8, 0.0))
        near(self, self.call("vec3", "safe_normal_2d(V)", V=("vec3", (0.0, 0.0, 5.0))), (0.0, 0.0, 0.0))
        near(self, self.call("vec3", "safe_normal_2d(V)", V=("vec3", (1.0, 0.0, 5.0))), (1.0, 0.0, 0.0))
        self.assertAlmostEqual(self.call("float", "size_2d(V)", V=("vec3", (3.0, 4.0, 12.0))), 5.0)

    def test_half_life_to_smoothing_time(self):
        self.assertAlmostEqual(self.call("float", "half_life_to_smoothing_time(0.5)"), 0.5 / math.log(2.0))

    def test_names_cannot_be_redefined(self):
        for name in ("inverse", "compose", "blend_alpha", "critical_spring_damper", "fast_lerp", "transform"):
            with self.subTest(name=name):
                self.assertTrue(errors(f"logic Lg;\nfn {name}(x: float) -> float {{ return x; }}\nupdate(dt) {{ }}"))


# ---- springs ----------------------------------------------------------------------------------------------------------------

class SpringTests(unittest.TestCase):
    SRC = """
logic Sp;
input Target: float;
input Smooth: float;
state X: float;
state V: float;
state P: vec3;
state PV: vec3;
update(dt) {
    let r = critical_spring_damper(X, V, Target, Smooth, dt);
    X = r.value;
    V = r.velocity;
    let q = critical_spring_damper(P, PV, vec3(Target, 0.0, -Target), Smooth, dt);
    P = q.value;
    PV = q.velocity;
}
"""

    def test_converges_to_the_target_without_overshoot(self):
        frames = [frame(1 / 60, Target=10.0, Smooth=0.25) for _ in range(180)]
        out = run(self.SRC, frames)
        xs = [f["X"] for f in out]
        self.assertTrue(all(b >= a for a, b in zip(xs, xs[1:])))            # critically damped: monotone
        self.assertLess(xs[-1], 10.0 + 1e-9)
        self.assertAlmostEqual(xs[-1], 10.0, places=2)
        self.assertAlmostEqual(out[-1]["P"][0], 10.0, places=2)
        self.assertAlmostEqual(out[-1]["P"][2], -10.0, places=2)
        self.assertAlmostEqual(out[-1]["V"], 0.0, places=2)

    def test_zero_smoothing_time_snaps(self):
        out = run(self.SRC, [frame(0.1, Target=7.0, Smooth=0.0)])
        self.assertEqual((out[0]["X"], out[0]["V"], out[0]["P"], out[0]["PV"]), (7.0, 0.0, (7.0, 0.0, -7.0), (0.0, 0.0, 0.0)))

    def test_matches_the_closed_form(self):
        # critically damped spring from rest: x(t) = target - (target - x0)(1 + y t) e^{-y t}, y = 2 / smoothing time
        smooth, target, dt = 0.5, 4.0, 0.01
        out = run(self.SRC, [frame(dt, Target=target, Smooth=smooth) for _ in range(100)])
        y = 4.0 / smooth / 2.0
        t = 100 * dt
        exact = target - target * (1.0 + y * t) * math.exp(-y * t)
        self.assertAlmostEqual(out[-1]["X"], exact, delta=0.02)               # InvExpApprox is the engine's approximation of exp(-x)

    def test_spring_values_are_local_only_and_have_two_members(self):
        self.assertEqual([c for c, _l, _k in errors("logic Lg;\nstate S: spring;\nupdate(dt) { }")], ["DAL4023"])
        self.assertEqual([c for c, _l, _k in errors("logic Lg;\ninput S: spring3;\nupdate(dt) { }")], ["DAL4023"])
        self.assertEqual([c for c, _l, _k in errors("struct S { s: spring; }\nlogic Lg;\nupdate(dt) { }")], ["DAL4050"])
        self.assertEqual([c for c, _l, _k in errors("logic Lg;\nstate X: float;\nupdate(dt) { let r = critical_spring_damper(X, X, X, 1.0, dt); X = r.nope; }")], ["DAL4030"])
        self.assertEqual([c for c, _l, _k in errors("logic Lg;\nstate X: float;\nupdate(dt) { let r = critical_spring_damper(X, X, X, 1.0, dt); X = r; }")], ["DAL4002"])
        self.assertEqual([c for c, _l, _k in errors("logic Lg;\nstate X: vec3;\nupdate(dt) { let r = critical_spring_damper(X, X, 1.0, 1.0, dt); }")], ["DAL4040"])
        ok = "fn step(r: spring) -> float { return r.value + r.velocity; }\nlogic Lg;\nstate X: float;\nupdate(dt) { X = step(critical_spring_damper(X, 0.0, 1.0, 1.0, dt)); }"
        self.assertEqual(errors(ok), [])

    def test_random(self):
        m = compile_ok(self.SRC)
        for seed in (1, 2, 3):
            self.assertEqual(harness.verify(m, 100, seed), [])

    @unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
    def test_cpp_matches_python(self):
        m = compile_ok(self.SRC)
        frames = [frame(1 / 60 if k % 7 else 0.05, Target=float((k // 20) % 3) * 5.0, Smooth=0.1 + (k % 5) * 0.1) for k in range(200)]
        tcc.CppCompileTests.run_case(self, m, frames, warnings_as_errors=True, default=1e-3)
        cpp = generate_cpp(m)["Sp.cpp"]
        self.assertIn("const DalMath::FSpring r = DalMath::CriticalSpringDamper(X, V, Target, Smooth, dt);", cpp)
        self.assertIn("X = r.Value;", cpp)


# ---- easing ------------------------------------------------------------------------------------------------------------------

class BlendAlphaTests(unittest.TestCase):
    OPTIONS = ["Linear", "Cubic", "HermiteCubic", "Sinusoidal", "QuadraticInOut", "CubicInOut", "QuarticInOut", "QuinticInOut",
               "CircularIn", "CircularOut", "CircularInOut", "ExpIn", "ExpOut", "ExpInOut", "Custom"]

    def source(self):
        lines = ["settings Cfg { Curve: curve; }", "logic B;", "param C: Cfg;", "input A: float;"]
        lines += [f"state R_{o}: float;" for o in self.OPTIONS]
        lines += ["update(dt) {"] + [f"    R_{o} = blend_alpha(A, BlendOption.{o}, C.Curve);" for o in self.OPTIONS] + ["}"]
        return "\n".join(lines)

    def test_endpoints_and_the_known_values(self):
        out = run(self.source(), [frame(A=a) for a in (0.0, 0.5, 1.0, -0.5, 1.5)])
        for o in self.OPTIONS:
            self.assertAlmostEqual(out[0][f"R_{o}"], 0.0, places=6, msg=o)
            self.assertAlmostEqual(out[2][f"R_{o}"], 1.0, places=6, msg=o)
            for k in (3, 4):                                                     # out of range: clamped (the cubic formula itself is not monotone there)
                self.assertTrue(0.0 <= out[k][f"R_{o}"] <= 1.0, o)
            if o in ("Linear", "HermiteCubic", "Custom"):
                self.assertEqual(out[3][f"R_{o}"], 0.0, o)
                self.assertEqual(out[4][f"R_{o}"], 1.0, o)
        mid = out[1]
        self.assertAlmostEqual(mid["R_Linear"], 0.5)
        self.assertAlmostEqual(mid["R_Cubic"], 0.5)                           # 3a^2 - 2a^3
        self.assertAlmostEqual(mid["R_HermiteCubic"], 0.5)
        self.assertAlmostEqual(mid["R_Sinusoidal"], 0.5)
        for o in ("QuadraticInOut", "CubicInOut", "QuarticInOut", "QuinticInOut", "CircularInOut", "ExpInOut"):
            self.assertAlmostEqual(mid[f"R_{o}"], 0.5, places=6, msg=o)
        self.assertAlmostEqual(mid["R_CircularIn"], 1.0 - math.sqrt(0.75))
        self.assertAlmostEqual(mid["R_CircularOut"], math.sqrt(0.75))
        self.assertAlmostEqual(mid["R_ExpIn"], 2.0 ** (10 * (0.5 - 1.0)))
        self.assertAlmostEqual(mid["R_ExpOut"], 1.0 - 2.0 ** (-5.0))
        self.assertAlmostEqual(mid["R_Custom"], 0.5)                          # no curve: linear

    def test_quarter_values(self):
        q = run(self.source(), [frame(A=0.25)])[0]
        self.assertAlmostEqual(q["R_Cubic"], 3 * 0.0625 - 2 * 0.015625)
        self.assertAlmostEqual(q["R_QuadraticInOut"], 0.5 * (0.5 ** 2))
        self.assertAlmostEqual(q["R_Sinusoidal"], (math.sin(0.25 * math.pi - math.pi / 2) + 1.0) / 2.0)

    def test_the_ease_options_are_monotone(self):
        out = run(self.source(), [frame(A=k / 40.0) for k in range(41)])
        for o in self.OPTIONS:
            xs = [f[f"R_{o}"] for f in out]
            self.assertTrue(all(b >= a - 1e-9 for a, b in zip(xs, xs[1:])), o)

    def test_custom_reads_the_curve_over_its_time_range(self):
        from settings_sources import curve

        data = {"Curve": curve((10.0, 0.0), (20.0, 1.0), (30.0, 0.25))}
        out = run(self.source(), [frame(A=0.0), frame(A=0.25), frame(A=0.5), frame(A=1.0), frame(A=2.0)], C=data)
        vals = [f["R_Custom"] for f in out]
        near(self, vals, (0.0, 0.5, 1.0, 0.25, 0.25), places=6)                # time = 10 + 20 * alpha

    def test_an_unset_curve_makes_custom_linear(self):
        out = run(self.source(), [frame(A=0.3)])
        self.assertAlmostEqual(out[0]["R_Custom"], 0.3)

    def test_the_option_is_an_enum(self):
        self.assertEqual([c for c, _l, _k in errors("settings S { C: curve; }\nlogic Lg;\nparam P: S;\nstate X: float;\nupdate(dt) { X = blend_alpha(0.5, 3, P.C); }")], ["DAL4040"])
        self.assertEqual([c for c, _l, _k in errors("logic Lg;\nstate B: BlendOption = BlendOption.Cubic;\nupdate(dt) { B = BlendOption.Custom; }")], [])
        self.assertEqual([c for c, _l, _k in errors("logic Lg;\nupdate(dt) { let o = BlendOption.Nope; }")], ["DAL3032"])
        self.assertEqual([c for c, _l, _k in errors("enum BlendOption { A }\nlogic Lg;\nupdate(dt) { }")], ["DAL3002"])

    @unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
    def test_cpp_matches_python(self):
        from settings_sources import curve

        m = compile_ok(self.source())
        data = {"Curve": curve((0.0, 0.0), (0.5, 0.9), (1.0, 0.2))}
        frames = [frame(A=k / 20.0 - 0.1) for k in range(26)]
        tcc.CppCompileTests.run_case(self, m, frames, warnings_as_errors=True, params={"C": data}, default=2e-3)
        self.assertIn("DalMath::BlendAlpha(A, EAlphaBlendOption::HermiteCubic, C.Curve)", generate_cpp(m)["B.cpp"].replace("DalSettings_C().Curve", "C.Curve"))


# ---- transforms ----------------------------------------------------------------------------------------------------------------

def tf(loc=(0.0, 0.0, 0.0), rot=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0)):
    return (tuple(loc), tuple(rot), tuple(scale))


YAW90 = (0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4))


class TransformTests(unittest.TestCase):
    def call(self, ty, expr, **free):
        names = "".join(f"input {k}: {t};\n" for k, (t, _v) in free.items())
        src = f"logic Lg;\n{names}state R: {ty};\nupdate(dt) {{ R = {expr}; }}"
        return run(src, [frame(**{k: v for k, (_t, v) in free.items()})])[0]["R"]

    def test_construction_and_members(self):
        t = self.call("transform", "transform(vec3(1.0, 2.0, 3.0), quat(0.0, 0.0, 0.0, 1.0), vec3(2.0, 2.0, 2.0))")
        self.assertEqual(t, tf((1.0, 2.0, 3.0), scale=(2.0, 2.0, 2.0)))
        t = self.call("transform", "transform(vec3(1.0, 2.0, 3.0), quat(0.0, 0.0, 0.0, 1.0))")
        self.assertEqual(t, tf((1.0, 2.0, 3.0)))                              # the scale defaults to 1
        near(self, self.call("vec3", "T.location + T.scale", T=("transform", tf((1.0, 2.0, 3.0), scale=(2.0, 3.0, 4.0)))), (3.0, 5.0, 7.0))
        self.assertEqual(self.call("quat", "T.rotation", T=("transform", tf(rot=YAW90))), YAW90)

    def test_the_default_is_the_identity(self):
        out = run("logic Lg;\nstate X: transform;\nupdate(dt) { }", [frame()])
        self.assertEqual(out[0]["X"], tf())

    def test_transform_position(self):
        t = tf((10.0, 0.0, 0.0), YAW90, (2.0, 2.0, 2.0))
        near(self, self.call("vec3", "transform_position(T, vec3(1.0, 0.0, 0.0))", T=("transform", t)), (10.0, 2.0, 0.0))     # scale, rotate, move
        near(self, self.call("vec3", "inverse_transform_position(T, vec3(10.0, 2.0, 0.0))", T=("transform", t)), (1.0, 0.0, 0.0))
        near(self, self.call("vec3", "transform_vector_no_scale(T, vec3(1.0, 0.0, 0.0))", T=("transform", t)), (0.0, 1.0, 0.0))

    def test_rotations(self):
        t = tf(rot=YAW90)
        q = self.call("quat", "transform_rotation(T, Q)", T=("transform", t), Q=("quat", YAW90))
        near(self, q, (0.0, 0.0, 1.0, 0.0))                                        # two quarter turns
        near(self, self.call("quat", "inverse_transform_rotation(T, Q)", T=("transform", t), Q=("quat", YAW90)), (0.0, 0.0, 0.0, 1.0))

    def test_compose_applies_the_left_one_first(self):
        a = tf((1.0, 0.0, 0.0))
        b = tf(rot=YAW90)
        ab = self.call("transform", "compose(A, B)", A=("transform", a), B=("transform", b))
        near(self, self.call("vec3", "transform_position(T, vec3(0.0, 0.0, 0.0))", T=("transform", ab)), (0.0, 1.0, 0.0))      # move, then turn
        ba = self.call("transform", "compose(B, A)", A=("transform", a), B=("transform", b))
        near(self, self.call("vec3", "transform_position(T, vec3(0.0, 0.0, 0.0))", T=("transform", ba)), (1.0, 0.0, 0.0))      # turn, then move

    def test_inverse_and_relative(self):
        t = tf((3.0, -2.0, 1.0), YAW90, (2.0, 2.0, 2.0))
        inv = self.call("transform", "inverse(T)", T=("transform", t))
        round_trip = self.call("transform", "compose(T, I)", T=("transform", t), I=("transform", inv))
        near(self, round_trip[0], (0.0, 0.0, 0.0))
        near(self, round_trip[1], (0.0, 0.0, 0.0, 1.0))
        near(self, round_trip[2], (1.0, 1.0, 1.0))
        a, b = tf((5.0, 0.0, 0.0), YAW90), tf((1.0, 1.0, 0.0), rot=(0.0, 0.0, 0.0, 1.0), scale=(2.0, 2.0, 2.0))
        rel = self.call("transform", "relative_transform(A, B)", A=("transform", a), B=("transform", b))
        back = self.call("transform", "compose(Rl, B)", Rl=("transform", rel), B=("transform", b))
        near(self, back[0], a[0])                                                   # a = relative(a, b) * b
        near(self, back[1], a[1])
        rev = self.call("transform", "relative_transform_reverse(A, B)", A=("transform", a), B=("transform", b))
        again = self.call("transform", "compose(A, Rv)", A=("transform", a), Rv=("transform", rev))
        near(self, again[0], b[0])                                                  # b = a * relative_reverse(a, b)
        near(self, again[2], b[2])
        # a rotation that is not normalized gives the identity (as in the engine)
        odd = self.call("transform", "relative_transform(A, B)", A=("transform", a), B=("transform", tf(rot=(0.0, 0.0, 0.0, 3.0))))
        self.assertEqual(odd, tf())

    def test_with_scale(self):
        t = self.call("transform", "with_scale(T, vec3(2.0, 3.0, 4.0))", T=("transform", tf((1.0, 2.0, 3.0), YAW90)))
        self.assertEqual(t, tf((1.0, 2.0, 3.0), YAW90, (2.0, 3.0, 4.0)))

    def test_a_zero_scale_has_a_safe_inverse(self):
        near(self, self.call("vec3", "inverse_transform_position(T, vec3(1.0, 2.0, 3.0))", T=("transform", tf(scale=(0.0, 1.0, 1.0)))), (0.0, 2.0, 3.0))

    def test_agrees_with_the_animation_runtime_of_the_blender_host(self):
        modules = r"I:\DCC\blender-moon-engine\scripts\modules"
        if not os.path.isdir(modules):
            self.skipTest("blender-moon-engine is not on this machine")
        try:
            import numpy as np

            sys.path.insert(0, modules)
            from dreamved.anim.runtime.transform import FTransform
        except Exception as e:                                                   # noqa: BLE001
            self.skipTest(f"the animation runtime cannot be imported: {e}")
        finally:
            if modules in sys.path:
                sys.path.remove(modules)
        import random

        rng = random.Random(4)
        src = """
logic Lg;
input A: transform;
input B: transform;
input P: vec3;
state Comp: transform;
state Rel: transform;
state Rev: transform;
state Inv: transform;
state Pos: vec3;
state IPos: vec3;
state NoScale: vec3;
update(dt) {
    Comp = compose(A, B);
    Rel = relative_transform(A, B);
    Rev = relative_transform_reverse(A, B);
    Inv = inverse(A);
    Pos = transform_position(A, P);
    IPos = inverse_transform_position(A, P);
    NoScale = transform_vector_no_scale(A, P);
}
"""
        m = compile_ok(src)
        frames = [frame(A=harness._rand_value(rng, rt_type("transform"), 50.0), B=harness._rand_value(rng, rt_type("transform"), 50.0),
                        P=tuple(rng.uniform(-50, 50) for _ in range(3))) for _ in range(40)]
        out = harness.run_python(m, frames)

        def ft(v):
            return FTransform(np.array(v[1]), np.array(v[0]), np.array(v[2]))

        def flat(x):
            return np.concatenate([np.asarray(x.translation), np.asarray(x.rotation), np.asarray(x.scale3d)])

        def mine(v):
            return np.array(list(v[0]) + list(v[1]) + list(v[2]))

        for f, o in zip(frames, out):
            a, b = ft(f["free"]["A"]), ft(f["free"]["B"])
            p = np.array(f["free"]["P"])
            for name, other in (("Comp", a.compose(b)), ("Rel", a.get_relative_transform(b)), ("Rev", a.get_relative_transform_reverse(b)), ("Inv", a.inverse())):
                got, want = mine(o[name]), flat(other)
                if name == "Inv" or np.abs(want).max() > 1e-6:
                    self.assertTrue(np.allclose(got[[0, 1, 2, 7, 8, 9]], want[[0, 1, 2, 7, 8, 9]], atol=1e-3, rtol=1e-4), (name, got, want))
                    self.assertTrue(np.allclose(got[3:7], want[3:7], atol=1e-4) or np.allclose(got[3:7], -want[3:7], atol=1e-4), (name, got, want))
            self.assertTrue(np.allclose(o["Pos"], a.transform_position(p), atol=1e-3, rtol=1e-4))
            self.assertTrue(np.allclose(o["IPos"], a.inverse_transform_position(p), atol=1e-3, rtol=1e-4))
            self.assertTrue(np.allclose(o["NoScale"], a.transform_vector_no_scale(p), atol=1e-3, rtol=1e-4))

    def test_transforms_in_structs_settings_and_free_inputs(self):
        src = """
struct Foot { Target: transform; Offset: vec3; }
settings Rig { Mesh: transform; }
logic Lg;
param C: Rig;
input Pose: transform;
state F: Foot;
state W: transform;
update(dt) {
    F.Target = Pose;
    F.Offset = transform_position(C.Mesh, vec3(1.0, 0.0, 0.0));
    W = compose(F.Target, C.Mesh);
}
"""
        data = {"Mesh": {"location": [0.0, 0.0, -90.0], "rotation": [0.0, 0.0, 0.7071067811865476, 0.7071067811865476], "scale": [1.0, 1.0, 1.0]}}
        out = run(src, [frame(Pose=tf((1.0, 2.0, 3.0)))], C=data)
        near(self, out[0]["F.Offset"], (0.0, 1.0, -90.0))
        self.assertEqual(out[0]["F.Target"], tf((1.0, 2.0, 3.0)))
        near(self, out[0]["W"][0], (-2.0, 1.0, -87.0))
        with self.assertRaises(dsettings.SettingsError):
            compile_loader = load_class(compile_ok(src))
            compile_loader(None, C={"Mesh": [1, 2, 3]})
        # the engine's spelling of a transform
        m = compile_ok(src)
        out = harness.run_python(m, [frame(Pose=tf())], C={"Mesh": {"Translation": {"X": 1, "Y": 2, "Z": 3}, "Rotation": {"Pitch": 0, "Yaw": 90, "Roll": 0}, "Scale3D": {"X": 1, "Y": 1, "Z": 1}}})
        near(self, out[0]["F.Offset"], (1.0, 3.0, 3.0), places=4)

    def test_transforms_cannot_be_compared(self):
        self.assertEqual([c for c, _l, _k in errors("logic Lg;\ninput A: transform;\nstate B: bool;\nupdate(dt) { B = A == A; }")], ["DAL4001"])

    def test_wrong_constructor_calls(self):
        self.assertEqual([c for c, _l, _k in errors("logic Lg;\nstate X: transform;\nupdate(dt) { X = transform(1.0, 2.0); }")], ["DAL4040"])
        self.assertEqual([c for c, _l, _k in errors("logic Lg;\nstate X: transform;\nupdate(dt) { X = transform(vec3(0.0, 0.0, 0.0), rotator(0.0, 0.0, 0.0)); }")], ["DAL4040"])

    def test_random_frames_agree(self):
        src = """
logic Lg;
input A: transform;
input B: transform;
state C: transform;
state V: vec3;
update(dt) { C = compose(A, relative_transform(B, A)); V = transform_position(inverse(C), A.location); }
"""
        m = compile_ok(src)
        for seed in (1, 2, 3):
            self.assertEqual(harness.verify(m, 80, seed), [])

    @unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
    def test_cpp_matches_python(self):
        src = """
struct Foot { Target: transform; Offset: vec3; }
logic Lg : actor;
input A: transform;
input B: transform;
input P: vec3;
state C: transform;
state F: Foot;
state V: vec3;
state W: vec3;
update(dt) {
    C = compose(A, relative_transform(B, A));
    F.Target = with_scale(inverse(B), vec3(1.0, 2.0, 0.5));
    F.Offset = relative_transform_reverse(A, B).location + transform_vector_no_scale(A, P);
    V = transform_position(C, P) + inverse_transform_position(B, P);
    W = (transform_rotation(A, B.rotation)).w * transform_position(F.Target, P);
}
"""
        m = compile_ok(src)
        for seed in (1, 2, 3):
            tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 60, seed), warnings_as_errors=True, default=5e-3)
        cpp = generate_cpp(m)
        self.assertIn("FTransform3f A = FTransform3f(FQuat4f(0.0f, 0.0f, 0.0f, 1.0f), FVector3f(0.0f, 0.0f, 0.0f), FVector3f(1.0f, 1.0f, 1.0f));", cpp["Lg.h"])
        self.assertIn("DalMath::Compose(A, DalMath::RelativeTransform(B, A))", cpp["Lg.cpp"])
        self.assertIn("F.Offset = DalMath::RelativeTransformReverse(A, B).GetLocation() + DalMath::TransformVectorNoScale(A, P);", cpp["Lg.cpp"])


def rt_type(name):
    from dal.types import BASIC_TYPES

    return BASIC_TYPES[name]


if __name__ == "__main__":
    unittest.main()
