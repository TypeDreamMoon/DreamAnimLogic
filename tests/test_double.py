"""The double types (double, vec2d, vec3d, rotatord, quatd, transformd) and the float32 mode of the interpreter and the Python backend.

The C++ side of the same features is tested in test_cpp_double.py; here: the lexer, the checker's conversion rules, the IR, the interpreter against the generated Python
in both modes, the arithmetic of the float32 mode, the host reads at either precision, settings, and the compile-time folding of literals."""

import json
import math
import unittest

import _bootstrap  # noqa: F401

import dal
from dal import harness, ir
from dal import runtime as rt
from dal.hostapi import HOST_ITEMS, protocol_markdown
from dal.interp import Interpreter
from dal.lexer import tokenize
from dal.errors import SourceFile
from dal.pybackend import generate_python, load_class
from dal.types import BOOL, DOUBLE, FLOAT, INT, QUATD, ROTATORD, TRANSFORMD, VEC2, VEC2D, VEC3, VEC3D

HDR = "logic Zz;\n"


def compile_ok(src):
    return dal.check_source(src, "t.dal").module


def errors(src):
    try:
        dal.check_source(src, "t.dal")
    except dal.DalError as e:
        return [d.code for d in e.diagnostics if d.severity == "error"]
    return []


def f32(x):
    return rt.f32(x)


def run(src, frames, float32=False, backend="python", **params):
    """The snapshots of a program for the given frames (dicts as the harness reads them) on one backend."""
    module = compile_ok(src)
    if backend == "python":
        return harness.run_python(module, frames, float32=float32, **params)
    return harness.run_interpreter(module, frames, float32=float32, **params)


def ticks(n, dt=1 / 60, t0=0.0):
    out = []
    t = t0
    for _ in range(n):
        t += dt
        out.append({"dt": dt, "time": t})
    return out


class LiteralTests(unittest.TestCase):
    def test_the_d_suffix_makes_a_double_literal(self):
        toks = [(t.kind, t.value) for t in tokenize(SourceFile("0.2d 60d 1e-3d 2.5 7", "t.dal"))][:-1]
        self.assertEqual(toks, [("DOUBLE", 0.2), ("DOUBLE", 60.0), ("DOUBLE", 1e-3), ("FLOAT", 2.5), ("INT", 7)])

    def test_other_suffixes_are_errors(self):
        self.assertEqual(errors(HDR + "state X: float = 1f;"), ["DAL1004"])
        self.assertEqual(errors(HDR + "state X: float = 1.5dd;"), ["DAL1004"])

    def test_a_double_literal_is_a_double(self):
        m = compile_ok(HDR + "state X: double = 0.1d;\nstate Y: double = 0.1;\nstate Z: double = 3;")
        self.assertEqual([s.ty for s in m.states], [DOUBLE] * 3)
        x, y, z = (s.default for s in m.states)
        self.assertEqual(x, 0.1)
        self.assertEqual(y, f32(0.1))                     # a float literal widened to double keeps its float32 value: 0.1d is the double
        self.assertNotEqual(y, 0.1)
        self.assertEqual(z, 3.0)


class ConversionTests(unittest.TestCase):
    def test_widening_is_implicit(self):
        compile_ok(HDR + "state D: double;\nstate F: float;\nstate V: vec3d;\nstate W: vec3;\nstate I: int;\n"
                         "update(dt) { D = F; D = I; V = W; D = dt; D = 2.0 * dt; }")

    def test_narrowing_is_an_error_with_a_hint(self):
        for src in ("state D: double;\nstate F: float;\nupdate(dt) { F = D; }",
                    "state V: vec3d;\nstate W: vec3;\nupdate(dt) { W = V; }",
                    "state Q: quatd;\nstate R: quat;\nupdate(dt) { R = Q; }",
                    "state D: double;\nstate I: int;\nupdate(dt) { I = D; }"):
            self.assertEqual(errors(HDR + src), ["DAL4002"], src)
        try:
            compile_ok(HDR + "state D: double;\nstate F: float;\nupdate(dt) { F = D; }")
        except dal.DalError as e:
            self.assertIn("float(x)", e.diagnostics[0].message)

    def test_explicit_conversions(self):
        compile_ok(HDR + "state D: double;\nstate F: float;\nstate V: vec3d;\nstate W: vec3;\nstate Q: quatd;\nstate R: quat;\n"
                         "state T: transformd;\nstate U: transform;\nstate A: rotatord;\nstate B: rotator;\nstate P: vec2d;\nstate S: vec2;\n"
                         "update(dt) { F = float(D); D = double(F); D = double(3); F = float(D); W = vec3(V); V = vec3d(W); R = quat(Q); Q = quatd(R);\n"
                         "  U = transform(T); T = transformd(U); B = rotator(A); A = rotatord(B); S = vec2(P); P = vec2d(S); }")

    def test_a_conversion_to_the_type_it_has_is_the_value(self):
        m = compile_ok(HDR + "state F: float;\nstate G: float;\nupdate(dt) { G = float(F); }")
        assign = m.update.body[0]
        self.assertIsInstance(assign.value, ir.StateRef)

    def test_mixed_arithmetic_is_double(self):
        m = compile_ok(HDR + "state D: double;\nstate F: float;\nstate V: vec3d;\nstate W: vec3;\nstate Q: quatd;\nstate R: quat;\n"
                             "state O: vec3d; state P: quatd; state N: bool;\n"
                             "update(dt) { D = F + D; D = F * 2; O = W + V; O = V * F; O = W * D; O = V / F; P = R * Q; N = F < D; N = D == 1; }")
        types = [s.value.ty for s in m.update.body]
        self.assertEqual(types, [DOUBLE, DOUBLE, VEC3D, VEC3D, VEC3D, VEC3D, QUATD, BOOL, BOOL])

    def test_float_arithmetic_stays_float(self):
        m = compile_ok(HDR + "state F: float;\nstate W: vec3;\nupdate(dt) { F = F + 1; W = W * F; }")
        self.assertEqual([s.value.ty for s in m.update.body], [FLOAT, VEC3])

    def test_the_conditional_operator_unifies_precision(self):
        m = compile_ok(HDR + "state D: double;\nstate F: float;\nstate B: bool;\nupdate(dt) { D = B ? F : D; D = B ? 1 : D; }")
        self.assertEqual([s.value.ty for s in m.update.body], [DOUBLE, DOUBLE])
        self.assertEqual(errors(HDR + "state D: double;\nstate V: vec3;\nstate B: bool;\nupdate(dt) { D = B ? V : D; }"), ["DAL4004"])

    def test_vectors_of_different_kinds_still_do_not_mix(self):
        self.assertEqual(errors(HDR + "state V: vec3d;\nstate Q: quatd;\nupdate(dt) { V = V + Q; }"), ["DAL4001"])
        self.assertEqual(errors(HDR + "state V: vec3d;\nstate F: vec2;\nupdate(dt) { V = V + F; }"), ["DAL4001"])

    def test_a_float_literal_widened_keeps_its_float32_value(self):
        sims = run(HDR + "state X: double;\nstate Y: double;\nupdate(dt) { X = 0.1; Y = 0.1d; }", ticks(1))
        self.assertEqual(sims[0]["X"], f32(0.1))
        self.assertEqual(sims[0]["Y"], 0.1)


class MembersTests(unittest.TestCase):
    SRC = HDR + """
state V: vec3d;
state P: vec2d;
state R: rotatord;
state Q: quatd;
state T: transformd;
state X: double;
state L: vec3d;
update(dt) {
    X = V.x + R.yaw + Q.w + T.scale.z;
    P = V.xy;
    L = T.location;
    V.z = 5;
    R.pitch += 1;
    T.location.x = 2.5d;
    T.rotation = Q;
}
"""

    def test_member_types(self):
        m = compile_ok(self.SRC)
        body = m.update.body
        self.assertEqual(body[0].value.ty, DOUBLE)
        self.assertEqual(body[1].value.ty, VEC2D)
        self.assertEqual(body[2].value.ty, VEC3D)

    def test_the_interpreter_and_the_python_class_agree(self):
        m = compile_ok(self.SRC)
        for f32_mode in (False, True):
            self.assertEqual(harness.verify(m, 40, 3, 0.0, float32=f32_mode), [])

    def test_member_assignment_works_on_the_double_types(self):
        snaps = run(self.SRC, ticks(2))
        self.assertEqual(snaps[1]["V.z" if "V.z" in snaps[1] else "V"][2] if "V" in snaps[1] else snaps[1]["V.z"], 5.0)
        self.assertEqual(snaps[1]["T.location"][0] if "T.location" in snaps[1] else snaps[1]["T"][0][0], 2.5)

    def test_a_missing_component_is_an_error(self):
        self.assertEqual(errors(HDR + "state P: vec2d;\nstate X: double;\nupdate(dt) { X = P.z; }"), ["DAL4030"])
        self.assertEqual(errors(HDR + "state V: vec3d;\nupdate(dt) { V.xy = V.yz; }"), ["DAL2010"])


class BuiltinTests(unittest.TestCase):
    def test_the_double_versions(self):
        m = compile_ok(HDR + "state D: double;\nstate V: vec3d;\nstate I: int;\nstate Q: quatd;\nstate F: float;\n"
                             "update(dt) { D = length(V) + dot(V, V) + min(D, 2.0) + abs(D) + sqrt(D) + lerp(D, 2.0, 0.5);\n"
                             "  I = to_int(D) + floor_to_int(D); V = cross(V, V) + normalize(V) + rotate(Q, V); Q = to_quat(rotatord(1d, 2d, 3d)) * quat_inverse(Q);\n"
                             "  F = length(vec3(V)); }")
        self.assertEqual(m.update.body[0].value.ty, DOUBLE)
        self.assertEqual(m.update.body[2].value.ty, VEC3D)

    def test_a_float_argument_among_doubles_is_widened(self):
        m = compile_ok(HDR + "state D: double;\nstate F: float;\nupdate(dt) { D = max(F, D); D = clamp(D, 0, 1); }")
        self.assertEqual([s.value.ty for s in m.update.body], [DOUBLE, DOUBLE])

    def test_float_only_built_ins_do_not_take_doubles(self):
        self.assertEqual(errors(HDR + "state D: double;\nupdate(dt) { D = interp_to(D, 1.0, dt, 2.0); }"), ["DAL4040"])
        self.assertEqual(errors(HDR + "state D: double;\nupdate(dt) { D = inv_exp_approx(D); }"), ["DAL4040"])

    def test_the_error_lists_the_double_versions_only_when_asked_with_one(self):
        try:
            compile_ok(HDR + "state D: double;\nupdate(dt) { D = length(D); }")
        except dal.DalError as e:
            self.assertIn("length(vec3d) -> double", e.diagnostics[0].message)
        try:
            compile_ok(HDR + "state D: float;\nupdate(dt) { D = length(D); }")
        except dal.DalError as e:
            self.assertNotIn("double", e.diagnostics[0].message)


class HostReadTests(unittest.TestCase):
    def test_an_item_can_be_read_at_either_precision(self):
        compile_ok(HDR + "input T: double <- host.time;\ninput U: float <- host.time;\ninput A: vec3d <- host.actor_location;\ninput B: vec3 <- host.actor_location;\n"
                         "input C: quatd <- host.actor_rotation;\ninput D: quat <- host.actor_rotation;\ninput E: float <- host.max_walk_speed;\ninput F: double <- host.max_walk_speed;\n"
                         "input G: transformd <- host.mesh_world_transform;\ninput H: transform <- host.mesh_world_transform;\ninput I: rotatord <- host.control_rotation;\n")

    def test_the_wrong_kind_is_an_error(self):
        self.assertEqual(errors(HDR + "input T: int <- host.time;"), ["DAL4020"])
        self.assertEqual(errors(HDR + "input T: vec3d <- host.max_walk_speed;"), ["DAL4020"])
        self.assertEqual(errors(HDR + "input T: quatd <- host.actor_location;"), ["DAL4020"])

    def test_a_double_read_keeps_what_the_host_gave_and_a_float_read_rounds(self):
        src = HDR + "input T: double <- host.time;\ninput U: float <- host.time;\ninput A: vec3d <- host.actor_location;\ninput B: vec3 <- host.actor_location;\nstate X: double;\nupdate(dt) { X = T; }"
        frame = {"dt": 1 / 60, "time": 123456.789012345, "actor_location": (1234.5678901234, 5.0, -0.1)}
        for float32 in (False, True):
            for backend in ("python", "interp"):
                snap = run(src, [frame], float32, backend)[0]
                self.assertEqual(snap["T"], 123456.789012345)
                self.assertEqual(snap["A"], (1234.5678901234, 5.0, -0.1))
                if float32:
                    self.assertEqual(snap["U"], f32(123456.789012345))
                    self.assertEqual(snap["B"], (f32(1234.5678901234), 5.0, f32(-0.1)))

    def test_the_protocol_table_names_the_engine_precision(self):
        self.assertIs(HOST_ITEMS["time"].ty, DOUBLE)
        self.assertIs(HOST_ITEMS["actor_location"].ty, VEC3D)
        self.assertIs(HOST_ITEMS["actor_rotation"].ty, QUATD)
        self.assertIs(HOST_ITEMS["control_rotation"].ty, ROTATORD)
        self.assertIs(HOST_ITEMS["mesh_world_transform"].ty, TRANSFORMD)
        self.assertIs(HOST_ITEMS["max_walk_speed"].ty, FLOAT)
        self.assertIn("| `time` | double (s) |", protocol_markdown())


class CommandAndQueryTests(unittest.TestCase):
    ACTOR = "logic Zz : actor;\n"

    def test_the_commands_take_doubles_and_widen_floats(self):
        m = compile_ok(self.ACTOR + "state V: vec3;\nstate W: vec3d;\nstate Q: quat;\nupdate(dt) { host.root_motion_override(V, Q); host.root_motion_override(W, quatd(Q));"
                                    " host.set_actor_location(W); host.set_actor_location(V); }")
        for cmd in m.update.body:
            self.assertTrue(all(a.ty.is_double or a.ty is BOOL for a in cmd.args))

    def test_a_double_does_not_narrow_into_a_float_command(self):
        self.assertEqual(errors(self.ACTOR + "state W: vec3d;\nupdate(dt) { host.set_velocity(W); }"), ["DAL4002"])

    def test_a_trace_takes_double_positions_and_the_hit_has_them_at_double_precision(self):
        src = (self.ACTOR + "state P: vec3d;\nstate F: vec3;\nstate L: vec3d;\nupdate(dt) {\n let h = host.trace_line(P, P + vec3d(0d, 0d, -100d));\n"
               " P = h.point_d; F = h.point; L = h.location_d; }")
        module = compile_ok(src)
        exact = (10.123456789012, 20.0, 30.5)
        host = harness.ScriptedHost()
        host.trace_line = lambda start, end: rt.Hit(True, exact, (0.0, 0.0, 1.0), 1.0, 0.5, location=exact)
        for float32 in (False, True):
            obj = load_class(module, float32=float32)(host)
            obj.update(1 / 60)
            self.assertEqual(obj.P, exact)
            self.assertEqual(obj.L, exact)
            if float32:
                self.assertEqual(obj.F, tuple(f32(c) for c in exact))


class Float32ModeTests(unittest.TestCase):
    THRESHOLD = HDR + ("setting_free_placeholder" if False else "") + """
const Delay: float = 0.11666667;
state Acc: float;
state Hit: bool;
update(dt) {
    Acc += dt;
    Hit = Acc >= Delay;
}
"""

    def first_hit(self, float32, backend="python"):
        snaps = run(self.THRESHOLD, ticks(10, 0.016666668), float32, backend)
        return next(i for i, s in enumerate(snaps) if s["Hit"])

    def test_seven_float32_additions_equal_the_float32_threshold(self):
        """The case that started it: 7 * float32(1/60) summed in float32 is exactly float32(0.11666667); summed in double it is a hair above."""
        self.assertEqual(self.first_hit(True), 6)                 # the seventh tick reaches it (condition >=)
        self.assertEqual(self.first_hit(True, "interp"), 6)
        snaps = run(self.THRESHOLD, ticks(7, 0.016666668), True)
        self.assertEqual(snaps[6]["Acc"], f32(0.11666667))

    def test_the_plain_mode_sums_in_double(self):
        snaps = run(self.THRESHOLD, ticks(7, f32(1 / 60)), False)
        self.assertNotEqual(snaps[6]["Acc"], f32(0.11666667))

    def test_a_strict_comparison_is_the_other_side_of_the_edge(self):
        src = self.THRESHOLD.replace("Acc >= Delay", "Acc > Delay")
        snaps32 = run(src, ticks(10, f32(1 / 60)), True)
        snapsd = run(src, ticks(10, f32(1 / 60)), False)
        self.assertEqual(next(i for i, s in enumerate(snaps32) if s["Hit"]), 7)       # exactly equal: not greater yet
        self.assertEqual(next(i for i, s in enumerate(snapsd) if s["Hit"]), 6)        # the double sum is a hair above: one frame early (residual 3 of the ALS)

    def test_a_double_clock_with_float_deltas_ends_the_window_a_frame_earlier(self):
        """TimeSince(Teleported) > 0.2f in the engine: the world time is a double, 12 float deltas summed in double are above 0.2f, in float32 exactly equal."""
        src = HDR + """
input T: double <- host.time;
state Start: double;
state Age: double;
state InDouble: bool;
state Acc: float;
state InFloat: bool;
update(dt) {
    Age = T - Start;
    InDouble = Age > 0.2;
    Acc += dt;
    InFloat = Acc > 0.2;
}
"""
        dt = f32(1 / 60)
        t, frames = 0.0, []
        for _ in range(16):
            t += dt                                            # the engine's clock: a double that adds the float delta
            frames.append({"dt": dt, "time": t})
        for float32 in (False, True):
            snaps = run(src, frames, float32)
            self.assertEqual(next(i for i, s in enumerate(snaps) if s["InDouble"]), 11)          # frame 12: 12 deltas as doubles are above float32(0.2)
            self.assertEqual(next(i for i, s in enumerate(snaps) if s["InFloat"]), 12 if float32 else 11)

    def test_every_float_is_a_float32(self):
        src = HDR + "state F: float;\nstate G: float;\nstate V: vec3;\nstate D: double;\nupdate(dt) { F = dt * 1.1; G = F / 3; V = vec3(F, G, 0.1) * 1.37; D = dt * 1.1d; }"
        for backend in ("python", "interp"):
            snap = run(src, ticks(3, 0.0123), True, backend)[2]
            for name in ("F", "G"):
                self.assertEqual(snap[name], f32(snap[name]))
            self.assertTrue(all(c == f32(c) for c in snap["V"]))
            self.assertNotEqual(snap["D"], f32(snap["D"]))             # the double is not

    def test_float_arithmetic_is_the_correctly_rounded_float_result(self):
        """a op b in a double holds the float result exactly (24 + 24 bits < 53), so rounding once is the C++ float operation: check on many pairs."""
        import random
        rnd = random.Random(7)
        src = HDR + "input A: float <- host.max_walk_speed;\ninput B: float <- host.max_acceleration;\nstate S: float;\nstate D: float;\nstate P: float;\nstate Q: float;\n" \
                    "update(dt) { S = A + B; D = A - B; P = A * B; Q = A / B; }"
        frames = [{"dt": 0.01, "max_walk_speed": rnd.uniform(-1000, 1000), "max_acceleration": rnd.uniform(1, 1000)} for _ in range(400)]
        snaps = run(src, frames, True)
        for fr, sn in zip(frames, snaps):
            a, b = f32(fr["max_walk_speed"]), f32(fr["max_acceleration"])
            self.assertEqual(sn["S"], f32(a + b))
            self.assertEqual(sn["P"], f32(a * b))
            self.assertEqual(sn["Q"], f32(a / b))

    def test_the_interpreter_and_the_class_agree_in_both_modes_on_the_example_programs(self):
        for src in (MembersTests.SRC, self.THRESHOLD):
            m = compile_ok(src)
            for float32 in (False, True):
                self.assertEqual(harness.verify(m, 60, 5, 0.0, float32=float32), [])

    def test_vector_division_multiplies_by_the_reciprocal(self):
        src = HDR + "state V: vec3;\nstate W: vec3d;\nstate S: float;\nstate T: double;\nupdate(dt) { V = vec3(1.0, 2.0, 3.0) / 3.0; W = vec3d(1d, 2d, 3d) / 3d; }"
        for backend in ("python", "interp"):
            snap = run(src, ticks(1), True, backend)[0]
            r = f32(1 / 3.0)
            self.assertEqual(snap["V"], (f32(1.0 * r), f32(2.0 * r), f32(3.0 * r)))
            r = 1 / 3.0
            self.assertEqual(snap["W"], (1.0 * r, 2.0 * r, 3.0 * r))
        self.assertEqual(run(src, ticks(1), False)[0]["V"], (1 / 3.0, 2 / 3.0, 1.0))      # the plain float vector keeps the component division

    def test_params_and_settings_are_rounded(self):
        src = HDR + """
settings Cfg {
    Speed: float = 0.1;
    Pos: vec3 = vec3(0.1, 0.2, 0.3);
    Exact: double = 0.1d;
    Where: vec3d = vec3d(0.1d, 0.2d, 0.3d);
}
param Config: Cfg;
param Gain: float = 0.7;
state S: float;
state E: double;
state W: vec3d;
update(dt) { S = Config.Speed + Gain; E = Config.Exact; W = Config.Where; }
"""
        module = compile_ok(src)
        data = {"Speed": 0.3, "Pos": [0.1, 0.2, 0.3], "Exact": 0.3, "Where": [0.1, 0.2, 0.3]}
        for backend in ("python", "interp"):
            for float32 in (False, True):
                host = harness.ScriptedHost()
                if backend == "python":
                    obj = load_class(module, float32=float32)(host, Config=data, Gain=0.7)
                else:
                    obj = Interpreter(module, host, float32=float32, Config=data, Gain=0.7)
                obj.update(0.01)
                snap = obj.snapshot()
                self.assertEqual(snap["E"], 0.3)                                      # a double member of a settings value is read as it is
                self.assertEqual(snap["W"], (0.1, 0.2, 0.3))
                if float32:
                    self.assertEqual(snap["S"], f32(f32(0.3) + f32(0.7)))
                else:
                    self.assertEqual(snap["S"], 0.3 + 0.7)

    def test_the_generated_float32_class_is_marked(self):
        module = compile_ok(HDR + "state F: float;\nupdate(dt) { F = F + dt; }")
        self.assertIn("DAL_FLOAT32 = True", generate_python(module, float32=True))
        self.assertNotIn("DAL_FLOAT32", generate_python(module))
        self.assertNotIn("_f32", generate_python(module))                       # the plain output is what it always was
        self.assertTrue(load_class(module, float32=True).DAL_FLOAT32)
        self.assertFalse(load_class(module).DAL_FLOAT32)

    def test_dt_is_rounded_to_float32(self):
        snaps = run(HDR + "state F: double;\nupdate(dt) { F = dt; }", ticks(1, 1 / 60), True)
        self.assertEqual(snaps[0]["F"], f32(1 / 60))
        snaps = run(HDR + "state F: double;\nupdate(dt) { F = dt; }", ticks(1, 1 / 60), False)
        self.assertEqual(snaps[0]["F"], 1 / 60)                   # (the plain mode keeps a float the Python float it is when it widens)


class CommandLineTests(unittest.TestCase):
    def test_compile_and_verify_take_float32(self):
        import io
        import os
        import tempfile
        from contextlib import redirect_stdout

        from dal import cli

        src = HDR + "state F: float;\nstate D: double;\nupdate(dt) { F += dt; D += dt; }\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Zz.dal")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(src)
            for flag, marked in (("--float32", True), ("--no-float32", False)):
                out = os.path.join(tmp, flag.strip("-"))
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["compile", path, "--py", out, flag]), 0)
                with open(os.path.join(out, "Zz.py"), encoding="utf-8") as fh:
                    self.assertEqual("DAL_FLOAT32 = True" in fh.read(), marked)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["compile", path, "--py", os.path.join(tmp, "plain")]), 0)
                self.assertEqual(cli.main(["verify", path, "--frames", "30", "--float32"]), 0)
            with open(os.path.join(tmp, "plain", "Zz.py"), encoding="utf-8") as fh:
                self.assertNotIn("DAL_FLOAT32", fh.read())                     # (not given: off, unless DAL_FLOAT32=1 is in the environment)


class FoldingTests(unittest.TestCase):
    def test_constants_of_both_precisions(self):
        m = compile_ok(HDR + "const A: double = 1d / 3d;\nconst B: float = 1.0 / 3.0;\nconst C: vec3d = vec3d(0.1d, 0.2d, 0.3d) * 2d;\nconst D: double = double(1.0 / 3.0);")
        a, b, c, d = (x.value for x in m.consts)
        self.assertEqual(a, 1 / 3)
        self.assertEqual(c, (0.2, 0.4, 0.6))
        self.assertEqual(d, f32(b))

    def test_a_double_constant_is_emitted_exactly(self):
        m = compile_ok(HDR + "const A: double = 0.1d;\nconst B: float = 0.1;\nstate X: double = A;\nstate Y: float = B;")
        text = generate_python(m)
        self.assertIn("_K_A = 0.1\n", text)
        self.assertIn("_K_B = 0.1\n", text)                                  # (the plain mode keeps the float as written)
        text32 = generate_python(m, float32=True)
        self.assertIn("_K_B = 0.10000000149011612\n", text32)
        self.assertIn("_K_A = 0.1\n", text32)


if __name__ == "__main__":
    unittest.main()
