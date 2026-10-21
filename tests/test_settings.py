"""Settings (data assets), curves, tables, lists and asset values: declarations, the loader, evaluation, diagnostics,
the interpreter / generated Python / C++ implementations, and the ALS settings of the exported pack."""

import contextlib
import io
import json
import math
import os
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401

import dal
import test_cpp_compile as tcc
from dal import curve as dcurve
from dal import harness, ir
from dal import runtime as rt
from dal import settings as dsettings
from dal.cppbackend import generate_cpp
from dal.interp import Interpreter
from dal.pybackend import generate_python, load_class
from settings_sources import DATA, DECLS, PROGRAM, curve, curve3

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ALS_LIB = os.path.join(ROOT, "als", "als_lib.dal")
PACK = os.environ.get("DAL_ALS_PACK", r"G:\DCC\AlsPack")
HAVE_PACK = os.path.isfile(os.path.join(PACK, "pack.json"))


def errors(src, name="t.dal", files=None):
    try:
        dal.check_source(src, name, files=files)
    except dal.DalError as e:
        return [(d.code, d.pos.line, d.pos.col) for d in e.diagnostics if d.severity == "error"]
    return []


def codes(src):
    return [c for c, _l, _c in errors(src)]


def compile_ok(src, name="t.dal"):
    return dal.check_source(src, name)


def frame(dt=0.1, **free):
    return {"dt": dt, "free": free}


# ---- curves -----------------------------------------------------------------------------------------------------------

def rc(*keys, pre="constant", post="constant", **kw):
    """RichCurve from (t, v) or (t, v, interp, arrive, leave) tuples."""
    out = []
    for k in keys:
        t, v = k[0], k[1]
        interp = k[2] if len(k) > 2 else "linear"
        arrive, leave = (k[3], k[4]) if len(k) > 4 else (0.0, 0.0)
        out.append(dcurve.Key(t, v, interp, arrive, leave))
    return dcurve.RichCurve(out, pre, post, **kw)


class CurveTests(unittest.TestCase):
    def test_constant_and_linear_keys(self):
        c = rc((0.0, 1.0, "constant"), (10.0, 5.0, "linear"), (20.0, 9.0))
        self.assertEqual(c.eval(-5.0), 1.0)                       # before the first key: constant extrapolation
        self.assertEqual(c.eval(5.0), 1.0)                        # a constant key holds its value until the next one
        self.assertEqual(c.eval(10.0), 5.0)
        self.assertAlmostEqual(c.eval(15.0), 7.0)
        self.assertEqual(c.eval(99.0), 9.0)

    def test_empty_curve_and_default_value(self):
        self.assertEqual(rc().eval(3.0), 0.0)
        self.assertEqual(rc(default_value=2.5).eval(3.0), 2.5)
        self.assertEqual(rc((1.0, 4.0)).eval(-10.0), 4.0)
        self.assertEqual(rc((1.0, 4.0)).eval(10.0), 4.0)

    def test_linear_extrapolation_follows_the_end_slopes(self):
        c = rc((0.0, 0.0), (10.0, 10.0), (20.0, 14.0), pre="linear", post="linear")
        self.assertAlmostEqual(c.eval(-5.0), -5.0)
        self.assertAlmostEqual(c.eval(30.0), 18.0)

    def test_cubic_uses_the_tangents_as_bezier_handles(self):
        flat = rc((0.0, 0.0, "cubic", 0.0, 0.0), (1.0, 1.0, "cubic", 0.0, 0.0))
        self.assertAlmostEqual(flat.eval(0.5), 0.5)
        self.assertAlmostEqual(flat.eval(0.25), 0.15625)          # 3a^2 - 2a^3 for zero tangents
        line = rc((0.0, 0.0, "cubic", 1.0, 1.0), (1.0, 1.0, "cubic", 1.0, 1.0))
        for t in (0.1, 0.25, 0.9):
            self.assertAlmostEqual(line.eval(t), t)               # tangents of slope 1 make the segment a straight line
        steep = rc((0.0, 0.0, "cubic", 0.0, 6.0), (2.0, 0.0, "cubic", 0.0, 0.0))
        # handles: p1 = leave * diff / 3 = 4, p2 = 0; B(.5) = 3 * .5 * .5^2 * 4 = 1.5
        self.assertAlmostEqual(steep.eval(1.0), 1.5)

    def test_cycle_extrapolation(self):
        c = rc((0.0, 0.0), (10.0, 10.0), pre="cycle", post="cycle")
        self.assertAlmostEqual(c.eval(12.0), 2.0)
        self.assertAlmostEqual(c.eval(-3.0), 7.0)
        off = rc((0.0, 0.0), (10.0, 10.0), pre="cycle_offset", post="cycle_offset")
        self.assertAlmostEqual(off.eval(12.0), 12.0)
        self.assertAlmostEqual(off.eval(25.0), 25.0)
        self.assertAlmostEqual(off.eval(-3.0), -3.0)

    def test_oscillate_extrapolation(self):
        c = rc((0.0, 0.0), (10.0, 10.0), pre="oscillate", post="oscillate")
        self.assertAlmostEqual(c.eval(12.0), 8.0)
        self.assertAlmostEqual(c.eval(22.0), 2.0)
        self.assertAlmostEqual(c.eval(-3.0), 3.0)

    def test_vector_curve_has_three_channels(self):
        c = dcurve.Curve3(rc((0.0, 0.0), (1.0, 2.0)), rc((0.0, 5.0)), rc((0.0, 1.0), (1.0, 0.0)))
        v = c.eval(0.5)
        self.assertEqual(tuple(v), (1.0, 5.0, 0.5))
        self.assertIsInstance(v, rt.Vec3)

    def test_json_formats(self):
        # the export of the pack
        c = dcurve.curve_from_json({"class": "CurveFloat", "channels": [{"keys": [
            {"t": 0.0, "v": 0.0, "interp": "cubic", "tangent": "auto", "arrive": 0.0, "leave": 0.0},
            {"t": 1.0, "v": 1.0, "interp": "linear"}], "pre": "constant", "post": "linear"}]})
        self.assertIsInstance(c, dcurve.RichCurve)
        self.assertAlmostEqual(c.eval(0.5), 0.5)
        # the layout of the spec and the engine's enum spellings
        c = dcurve.curve_from_json({"kind": "float", "channels": [{"keys": [{"time": 0, "value": 2, "interp_mode": "RCIM_Constant"}],
                                                                   "pre_infinity": "RCCE_Cycle", "post_infinity": "RCCE_CycleWithOffset"}]})
        self.assertEqual(c.eval(7.0), 2.0)
        self.assertEqual((c.pre, c.post), ("cycle", "cycle_offset"))
        v = dcurve.curve_from_json(curve3(((0.0, 1.0),), ((0.0, 2.0),), ((0.0, 3.0),)))
        self.assertIsInstance(v, dcurve.Curve3)
        self.assertEqual(tuple(v.eval(9.0)), (1.0, 2.0, 3.0))

    def test_json_errors(self):
        bad = [
            ({}, "non-empty"),
            ({"channels": []}, "non-empty"),
            ({"class": "CurveVector", "channels": [{"keys": []}]}, "3 channels"),
            ({"class": "CurveFloat", "channels": [{"keys": []}, {"keys": []}]}, "1 channel"),
            ({"channels": [{"keys": [{"t": 0, "v": 0, "interp": "wavy"}]}]}, "interpolation"),
            ({"channels": [{"keys": [{"t": "a", "v": 0}]}]}, "number"),
            ({"channels": [{"keys": [], "pre": "spiral"}]}, "extrapolation"),
        ]
        for obj, word in bad:
            with self.subTest(obj=obj):
                with self.assertRaises(dcurve.CurveError) as cm:
                    dcurve.curve_from_json(obj)
                self.assertIn(word, str(cm.exception))

    @unittest.skipUnless(HAVE_PACK, "the ALS pack is not on this machine")
    def test_a_curve_of_the_pack(self):
        with open(os.path.join(PACK, "curves", "CF_Als_StrideBlendAmount_Walk.curve.json"), encoding="utf-8") as f:
            raw = json.load(f)
        c = dcurve.curve_from_json(raw)
        keys = raw["channels"][0]["keys"]
        for k in keys:
            self.assertAlmostEqual(c.eval(k["t"]), k["v"], places=5)         # the curve passes through its keys
        self.assertAlmostEqual(c.eval(-10.0), keys[0]["v"], places=5)
        mid = c.eval((keys[0]["t"] + keys[1]["t"]) / 2)                      # cubic segment: between the two values
        self.assertTrue(min(keys[0]["v"], keys[1]["v"]) - 1e-6 <= mid <= max(keys[0]["v"], keys[1]["v"]) + 1e-6)


# ---- diagnostics ------------------------------------------------------------------------------------------------------

HDR = """tags AlsMode = "Als.Mode" { View, Velocity }
enum Pose { Stand, Crouch }
settings G { Walk: float = 1.0; Accel: curve3; Rot: curve; Clip: sequence; T: map<Pose, float>; L: array<float>; }
"""
LOGIC = "logic A;\n"


class SettingsCheckTests(unittest.TestCase):
    def check(self, body, *expected, header=HDR):
        with self.subTest(body=body):
            self.assertEqual(codes(header + body), list(expected), body)

    def test_only_settings_hold_curves_tables_and_lists(self):
        self.check("logic A; state C: curve;\nupdate(dt) {}", "DAL4058")
        self.check("logic A; state C: curve3;\nupdate(dt) {}", "DAL4058")
        self.check("logic A; state C: map<Pose, float>;\nupdate(dt) {}", "DAL4058")
        self.check("logic A; state C: array<float>;\nupdate(dt) {}", "DAL4058")
        self.check("logic A; input C: curve;\nupdate(dt) {}", "DAL4058")
        self.check("logic A; param C: array<float>;\nupdate(dt) {}", "DAL4058")
        self.check("struct S { m: map<Pose, float>; }\n" + LOGIC + "update(dt) {}", "DAL4050")
        self.check("struct S { m: curve; }\n" + LOGIC + "update(dt) {}", "DAL4050")
        self.check("struct S { m: sequence; n: montage; }\n" + LOGIC + "update(dt) {}")           # asset values are fine anywhere
        self.check("logic A; state Q: sequence; state M: montage;\nupdate(dt) {}")

    def test_a_settings_value_comes_from_a_param(self):
        self.check("logic A; state S: G;\nupdate(dt) {}", "DAL4058")
        self.check("logic A; input S: G;\nupdate(dt) {}", "DAL4058")
        self.check("const S: G = G();\n" + LOGIC + "update(dt) {}", "DAL4058", "DAL5004")
        self.check("logic A; param P: G; state X: float;\nupdate(dt) { X = P.Walk; }")
        self.check("logic A; param P: G; state X: float;\nupdate(dt) { let q = P; }", "DAL4059")
        self.check("logic A; param P: G; state X: float;\nupdate(dt) { P.Walk = 1.0; }", "DAL5001")
        self.check("logic A; state X: float;\nupdate(dt) { let g = G(Walk: 2.0); X = g.Walk; }", "DAL5004")
        self.check("logic A; state X: float;\nupdate(dt) { var g = G(); X = 1.0; }", "DAL5004")
        self.check("logic A; param P: G = G(); state X: float;\nupdate(dt) { X = P.Walk; }", "DAL5004")

    def test_member_types_and_keys(self):
        self.check("settings S { m: map<float, float>; }\n" + LOGIC + "update(dt) {}", "DAL4052")
        self.check("settings S { m: map<Pose, map<Pose, float>>; }\n" + LOGIC + "update(dt) {}", "DAL4052")
        self.check("settings S { m: array<array<float>>; }\n" + LOGIC + "update(dt) {}", "DAL4052")
        self.check("settings S { m: map<Pose, float> = 1; }\n" + LOGIC + "update(dt) {}", "DAL4052")
        self.check("settings S { m: curve = 1; }\n" + LOGIC + "update(dt) {}", "DAL4052")
        self.check("settings S { m: map<Pose, Nope>; }\n" + LOGIC + "update(dt) {}", "DAL3041")
        self.check("settings S { a: S; }\n" + LOGIC + "update(dt) {}", "DAL4053")
        self.check("settings S { a: map<Pose, S>; }\n" + LOGIC + "update(dt) {}", "DAL4053")
        self.check("settings S { a: array<T>; }\nsettings T { s: S; }\n" + LOGIC + "update(dt) {}", "DAL4053")
        self.check("struct T { s: float; }\nsettings S { t: T; u: map<Pose, T>; }\n" + LOGIC + "update(dt) {}")
        self.check("settings S { a: float; }\nstruct T { s: S; }\n" + LOGIC + "update(dt) {}", "DAL4050")

    def test_derived_members(self):
        self.check("struct S { derived m: float = 1.0; }\n" + LOGIC + "update(dt) {}", "DAL4052")
        self.check("settings S { derived m: float; }\n" + LOGIC + "update(dt) {}", "DAL4052")
        self.check("settings S { a: float = 2.0; derived m: float = a * 2.0; derived n: float = m + 1.0; }\n" + LOGIC + "update(dt) {}")
        self.check("settings S { derived m: float = Nope; }\n" + LOGIC + "update(dt) {}", "DAL3040")
        self.check("settings S { a: float; derived m: bool = a; }\n" + LOGIC + "update(dt) {}", "DAL4002")
        self.check("settings S { a: float; derived m: float = a; }\nconst K: float = 3.0;\nsettings U { derived n: float = K; }\n" + LOGIC + "update(dt) {}")

    def test_curves_tables_and_lists_in_expressions(self):
        body = "logic A; param P: G; state X: float; state V: vec3; state B: bool; state N: int;\n"
        self.check(body + "update(dt) { X = P.Rot.eval(1.0); V = P.Accel.eval(2.0); X += P.T[Pose.Stand] + P.L[0]; B = P.T.has(Pose.Crouch); N = P.L.len; }")
        self.check(body + "update(dt) { X = P.Rot.eval(1.0, 2.0); }", "DAL4041")
        self.check(body + "update(dt) { X = P.Rot.eval(true); }", "DAL4002")
        self.check(body + "update(dt) { X = P.Accel.eval(1.0); }", "DAL4002")                    # curve3 gives a vec3
        self.check(body + "update(dt) { X = P.Rot.sample(1.0); }", "DAL4030")
        self.check(body + "update(dt) { X = P.T[1.0]; }", "DAL4002")
        self.check(body + "update(dt) { X = P.T[Pose.Stand][0]; }", "DAL4044")
        self.check(body + "update(dt) { X = P.Walk[1]; }", "DAL4044")
        self.check(body + "update(dt) { X = P.L[1.0]; }", "DAL4002")
        self.check(body + "update(dt) { B = P.T.has(2.0); }", "DAL4002")
        self.check(body + "update(dt) { N = P.L.count; }", "DAL4030")
        self.check(body + "update(dt) { X = P.Nope; }", "DAL4030")
        self.check(body + "update(dt) { var t = P.T; }", "DAL5004")
        self.check(body + "update(dt) { let t = P.T; X = t[Pose.Stand]; B = t.has(Pose.Stand); }")

    def test_none_and_asset_values(self):
        body = "logic A; param P: G; state Q: sequence = none; state B: bool; state X: float;\n"
        self.check(body + "update(dt) { Q = none; Q = P.Clip; B = valid(Q) && Q != none && Q == P.Clip; }")
        self.check(body + "update(dt) { X = none; }", "DAL4002")
        self.check(body + "update(dt) { let q = none; }", "DAL4002")
        self.check(body + "update(dt) { X = P.Clip + 1.0; }", "DAL4001")
        self.check(body + "update(dt) { B = P.Clip == 1; }", "DAL4001")
        self.check(body + "update(dt) { Q = P.Rot; }", "DAL4002")                               # a curve is not a sequence
        self.check(body + "update(dt) { X = (B) ? 1.0 : none; }", "DAL4004")
        self.check(body + "update(dt) { Q = B ? none : P.Clip; }")
        self.check("logic A; state M: montage = none; state Q: sequence;\nupdate(dt) { Q = M; }", "DAL4002")

    def test_positions(self):
        self.assertEqual(errors("logic A; state C: array<float>;\nupdate(dt) {}"), [("DAL4058", 1, 19)])
        self.assertEqual(errors("settings S {\n    m: map<float, int>;\n}\nlogic A;\nupdate(dt) {}"), [("DAL4052", 2, 8)])
        self.assertEqual(errors("settings S { x: float; }\nlogic A; param P: S; state V: float;\nupdate(dt) { V = P.y; }"), [("DAL4030", 3, 20)])

    def test_a_library_root_cannot_be_held_by_a_logics_settings(self):
        lib = "settings Root { a: float; }\nsettings Holder { r: Root; }\nsettings Top { a: float; }\n"
        files = {"lib.dal": lib}
        # inside the library Root is held by Holder (a value type there), Top is a data asset
        src = 'use "lib.dal";\nsettings Mine { t: Top; }\nlogic A;\nupdate(dt) {}'
        self.assertEqual([c for c, _l, _c in errors(src, files=files)], ["DAL4052"])
        ok = 'use "lib.dal";\nsettings Mine { t: Root; }\nlogic A; param P: Top; param H: Holder;\nupdate(dt) {}'
        self.assertEqual(errors(ok, files=files), [])
        m = dal.check_source(ok, "main.dal", files=files).module
        roots = {s.name: s.asset_root for s in m.structs if s.is_settings}
        self.assertEqual(roots, {"Root": False, "Holder": True, "Top": True, "Mine": True})

    def test_the_ir_dump_shows_derived_members(self):
        text = ir.dump(compile_ok(PROGRAM).module)
        self.assertIn("settings Trace {Reach: float; Range: vec2; Angle: float; derived AngleCos: float; derived Span: float; derived Tall: bool}", text)
        self.assertIn("map<AlsMode,Stances>", text)
        self.assertIn(".eval(", text)


# ---- the loader --------------------------------------------------------------------------------------------------------

def rules():
    """(namespace with the settings classes, module)."""
    m = compile_ok(PROGRAM).module
    ns: dict = {}
    exec(compile(generate_python(m), "<gen>", "exec"), ns)
    return ns, m


class LoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns, cls.module = rules()
        cls.Rules = cls.ns["Rules"]

    def test_none_gives_the_declared_defaults(self):
        r = self.Rules.load(None)
        self.assertEqual(r.Speed, 1.0)
        self.assertEqual(tuple(r.Trace.Range), (50.0, 225.0))
        self.assertAlmostEqual(r.Trace.AngleCos, math.cos(math.radians(35.0)))
        self.assertAlmostEqual(r.Doubled, 2.0 + math.cos(math.radians(35.0)))
        self.assertEqual((r.Modes, r.Names, r.Traces, r.Clip), ({}, (), (), None))

    def test_a_dict_fills_the_members(self):
        r = self.Rules.load(DATA)
        self.assertEqual(r.Speed, 2.5)
        self.assertEqual(tuple(r.Trace.Range), (10.0, 260.0))
        self.assertEqual(r.Trace.Reach, 90.0)
        self.assertEqual(r.Trace.Span, 250.0)                        # derived members are computed
        self.assertTrue(r.Trace.Tall)
        self.assertAlmostEqual(r.Doubled, 5.0 + 0.5)
        view = r.Modes["Als.Mode.View"]
        self.assertEqual(view.Bias, 0.25)
        stand = view.ByPose[0]                                       # enum keys are the member indices
        self.assertEqual((stand.Walk, stand.Run, stand.Clip, stand.Style, stand.Facing), (150.0, 400.0, "A_Stand", 2, "Als.Mode.Aiming"))
        self.assertAlmostEqual(stand.RotSpeed.eval(50.0), 0.5)
        self.assertEqual(tuple(stand.Accel.eval(50.0)), (5.0, 5.0, 0.5))
        crouch = view.ByPose[1]
        self.assertEqual((crouch.Walk, crouch.Run, crouch.Style), (100.0, 375.0, 1))      # missing members keep their defaults
        self.assertIsNone(crouch.RotSpeed)
        self.assertEqual(r.Counts, {0: 1.5, 2: 4.0})                 # int keys
        self.assertEqual(r.Names, (3.0, 5.0, 7.0))
        self.assertEqual([t.Reach for t in r.Traces], [11.0, 22.0])
        self.assertAlmostEqual(r.Traces[1].AngleCos, 0.0, places=6)  # the derived member of an element
        self.assertEqual((r.Clip, r.Fallback), ("AM_Roll", "A_Fallback"))

    def test_member_names_may_be_snake_case_and_wrapped(self):
        wrapped = {"type": "Rules", "values": {"speed": 4.0, "trace": {"reach": 5.0, "angle": 90.0}}}
        r = self.Rules.load(wrapped)
        self.assertEqual((r.Speed, r.Trace.Reach, r.Trace.Angle), (4.0, 5.0, 90.0))
        exported = {"$object": "/Game/Data/CS.CS", "class": "/Script/Mod.URules", "props": {"Speed": 3.0}}
        self.assertEqual(self.Rules.load(exported).Speed, 3.0)
        self.assertEqual(self.Rules.load({"class": "/Script/Mod.Rules", "props": {"Speed": 3.5}}).Speed, 3.5)

    def test_wrong_class_is_an_error(self):
        with self.assertRaises(dsettings.SettingsError) as cm:
            self.Rules.load({"type": "Gait", "values": {}})
        self.assertIn("Gait", str(cm.exception))

    def test_unknown_members_are_reported_not_fatal(self):
        rep = dsettings.Report()
        r = self.Rules.load({"Speed": 2.0, "Nope": 1, "Trace": {"Colour": "red"}}, None, rep)
        self.assertEqual(r.Speed, 2.0)
        self.assertEqual(len(rep.warnings), 2)
        self.assertIn("Rules.Nope", rep.warnings[0])
        self.assertIn("Rules.Trace.Colour", rep.warnings[1])

    def test_exported_derived_values_are_ignored(self):
        r = self.Rules.load({"Trace": {"Angle": 90.0, "AngleCos": 0.123, "Span": 99999.0}})
        self.assertAlmostEqual(r.Trace.AngleCos, 0.0, places=6)
        self.assertEqual(r.Trace.Span, 175.0)

    def test_type_errors_name_the_path(self):
        bad = [
            ({"Speed": "fast"}, "Rules.Speed"),
            ({"Speed": True}, "Rules.Speed"),
            ({"Trace": 3}, "Rules.Trace"),
            ({"Trace": {"Range": [1, 2, 3]}}, "Rules.Trace.Range"),
            ({"Trace": {"Range": {"X": 1}}}, "Rules.Trace.Range"),
            ({"Names": {"a": 1}}, "Rules.Names"),
            ({"Names": [1, "x"]}, "Rules.Names[1]"),
            ({"Counts": [1]}, "Rules.Counts"),
            ({"Counts": {"two": 1.0}}, "Rules.Counts['two']"),
            ({"Modes": {"Als.Mode.View": {"ByPose": {"Sit": {}}}}}, "'Sit' is not a member of Pose"),
            ({"Modes": {"Als.Mode.View": {"ByPose": {"Stand": {"Style": 9}}}}}, "9 is not a member index"),
            ({"Modes": {"Als.Mode.View": {"ByPose": {"Stand": {"Facing": 4}}}}}, "expected a tag name"),
            ({"Modes": {"Als.Mode.View": {"ByPose": {"Stand": {"Accel": curve((0.0, 1.0))}}}}}, "expected a vector curve"),
            ({"Modes": {"Als.Mode.View": {"ByPose": {"Stand": {"RotSpeed": curve3(((0.0, 1.0),), ((0.0, 1.0),), ((0.0, 1.0),))}}}}}, "expected a float curve"),
            ({"Modes": {"Als.Mode.View": {"ByPose": {"Stand": {"RotSpeed": {"channels": [{"keys": [{"t": 0, "v": 0, "interp": "x"}]}]}}}}}}, "interpolation"),
            ("[]", "expected an object"),
        ]
        for data, word in bad:
            with self.subTest(data=data):
                with self.assertRaises(dsettings.SettingsError) as cm:
                    self.Rules.load(json.loads(data) if isinstance(data, str) else data)
                self.assertIn(word, str(cm.exception))

    def test_enum_spellings_and_vector_layouts(self):
        for spelling in ("Prone", "EPose::Prone", 2):
            r = self.Rules.load({"Modes": {"Als.Mode.View": {"ByPose": {spelling if isinstance(spelling, str) else "Stand": {"Style": spelling}}}}})
            self.assertEqual(r.Modes["Als.Mode.View"].ByPose[0 if not isinstance(spelling, str) or spelling == "Stand" else 2].Style, 2)
        for spelling in ([3.0, 4.0], {"X": 3.0, "Y": 4.0}, {"x": 3, "y": 4}):
            self.assertEqual(tuple(self.Rules.load({"Trace": {"Range": spelling}}).Trace.Range), (3.0, 4.0))

    def test_asset_names_are_taken_from_paths(self):
        for spelling in ("A_Foo", "/Game/Anim/A_Foo.A_Foo", {"$asset": "/Game/Anim/A_Foo.A_Foo", "class": "AnimSequence"}):
            self.assertEqual(self.Rules.load({"Fallback": spelling}).Fallback, "A_Foo")
        for none in (None, "None", ""):
            self.assertIsNone(self.Rules.load({"Fallback": none}).Fallback)

    def test_curves_by_name_come_from_the_resolver(self):
        work = tempfile.mkdtemp(prefix="dal_set_")
        try:
            os.makedirs(os.path.join(work, "curves"))
            with open(os.path.join(work, "curves", "CF_Rot.curve.json"), "w", encoding="utf-8") as f:
                json.dump({"class": "CurveFloat", "name": "CF_Rot", "channels": curve((0.0, 0.0), (10.0, 1.0))["channels"]}, f)
            with open(os.path.join(work, "curves", "CV_Acc.json"), "w", encoding="utf-8") as f:
                json.dump(curve3(((0.0, 1.0),), ((0.0, 2.0),), ((0.0, 3.0),)), f)
            data = {"Modes": {"Als.Mode.View": {"ByPose": {"Stand": {"RotSpeed": "/Game/Data/CF_Rot.CF_Rot", "Accel": "CV_Acc"},
                                                           "Crouch": {"RotSpeed": "CF_Missing"}}}}}
            rep = dsettings.Report()
            r = self.Rules.load(data, dsettings.PackResolver(work), rep)
            by_pose = r.Modes["Als.Mode.View"].ByPose
            self.assertAlmostEqual(by_pose[0].RotSpeed.eval(5.0), 0.5)
            self.assertEqual(tuple(by_pose[0].Accel.eval(1.0)), (1.0, 2.0, 3.0))
            self.assertIsNone(by_pose[1].RotSpeed)
            self.assertEqual(len(rep.unresolved), 1)
            self.assertIn("CF_Missing", rep.unresolved[0])
            # without a resolver no curve can be found: every named curve is reported
            rep = dsettings.Report()
            self.Rules.load(data, None, rep)
            self.assertEqual(len(rep.unresolved), 3)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_files_and_loaded_instances(self):
        work = tempfile.mkdtemp(prefix="dal_set_")
        try:
            path = os.path.join(work, "rules.json")
            with open(path, "w", encoding="utf-8-sig") as f:             # a BOM is fine
                json.dump(DATA, f)
            r = self.Rules.load(path)
            self.assertEqual(r.Speed, 2.5)
            self.assertIs(self.Rules.load(r), r)                         # an instance is used as it is
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_defaults_are_independent_per_load(self):
        a, b = self.Rules.load(None), self.Rules.load(None)
        self.assertIsNot(a.Trace, b.Trace)


# ---- semantics ---------------------------------------------------------------------------------------------------------

class SettingsSemanticsTests(unittest.TestCase):
    def run_both(self, frames, **params):
        m = compile_ok(PROGRAM).module
        a, b = harness.run_interpreter(m, frames, **params), harness.run_python(m, frames, **params)
        self.assertEqual(harness.compare(a, b), [])
        return a

    def test_reading_the_data(self):
        out = self.run_both([frame(M="Als.Mode.View", P=0, X=50.0, K=2)], Cfg=DATA)[0]
        # g = View/Stand: blend = 150 + (400 - 150) * 0.5 + rot(50) = 275.5, plus Speed
        self.assertAlmostEqual(out["Out"], 278.0)
        self.assertAlmostEqual(out["Cos"], 0.5)
        self.assertEqual((out["Span"], out["Tall"]), (250.0, True))
        self.assertAlmostEqual(out["Doubled"], 5.5)
        self.assertEqual(out["Acc"], (5.0, 5.0, 0.5))
        self.assertAlmostEqual(out["Rot"], 0.5)
        self.assertEqual((out["Has"], out["HasCount"]), (True, True))
        self.assertEqual((out["Q"], out["Mon"], out["HasQ"], out["HasMon"], out["Same"]), ("A_Stand", "AM_Roll", True, True, False))
        self.assertEqual((out["Style"], out["Facing"]), (2, "Als.Mode.Aiming"))
        self.assertEqual((out["Len"], out["TraceLen"]), (3, 2))
        self.assertAlmostEqual(out["Pick"], 7.0 + 4.0)                # Names[2] + Counts[2]
        self.assertEqual(out["Third"], 75.0)                          # Traces[2] does not exist: the default element
        self.assertEqual(out["Bias"], 0.25)
        self.assertEqual(out["Near"], 2.5)                            # Q is set

    def test_missing_entries_read_as_defaults(self):
        out = self.run_both([frame(M="Als.Mode.Aiming", P=1, X=40.0, K=-1)], Cfg=DATA)[0]
        # no table entry: the default Stances, its empty table, the default Gait
        self.assertAlmostEqual(out["Out"], 175.0 + (375.0 - 175.0) * 0.4 + 0.0 + 2.5)
        self.assertEqual((out["Has"], out["HasCount"]), (False, False))
        self.assertEqual((out["Q"], out["HasQ"], out["Style"], out["Facing"], out["Bias"]), (None, False, 1, "", 0.5))
        self.assertEqual(out["Acc"], (0.0, 0.0, 0.0))                 # an unset curve gives zero
        self.assertEqual(out["Near"], -2.5)
        self.assertEqual((out["Pick"], out["Third"]), (0.0, 75.0))
        none_tag = self.run_both([frame(M="", P=0, X=0.0, K=0)], Cfg=DATA)[0]
        self.assertFalse(none_tag["Has"])                              # the empty tag is not a key

    def test_no_data_means_declared_defaults(self):
        out = self.run_both([frame(M="Als.Mode.View", P=0, X=10.0, K=0)])[0]
        self.assertEqual((out["Out"], out["Len"], out["TraceLen"], out["Mon"]), (175.0 + 20.0 + 1.0, 0, 0, None))
        self.assertAlmostEqual(out["Cos"], math.cos(math.radians(35.0)))

    def test_params_take_paths_dicts_and_instances(self):
        m = compile_ok(PROGRAM).module
        work = tempfile.mkdtemp(prefix="dal_set_")
        try:
            path = os.path.join(work, "cfg.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(DATA, f)
            frames = [frame(M="Als.Mode.Velocity", P=0, X=5.0, K=1)]
            expect = harness.run_interpreter(m, frames, Cfg=DATA)
            self.assertEqual(harness.compare(expect, harness.run_interpreter(m, frames, Cfg=path)), [])
            self.assertEqual(harness.compare(expect, harness.run_python(m, frames, Cfg=path)), [])
            inst = load_class_ns(m)["Rules"].load(DATA)
            self.assertEqual(harness.compare(expect, harness.run_python(m, frames, Cfg=inst)), [])
            self.assertEqual(harness.compare(expect, harness.run_interpreter(m, frames, Cfg=inst)), [])         # an instance of the generated class
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_a_missing_curve_file_is_reported_by_the_resolver(self):
        m = compile_ok(PROGRAM).module
        data = {"Modes": {"Als.Mode.View": {"ByPose": {"Stand": {"RotSpeed": "CF_Nowhere"}}}}}
        rep = dsettings.Report()
        r = load_class_ns(m)["Rules"].load(data, dsettings.PackResolver(tempfile.gettempdir()), rep)
        self.assertEqual(len(rep.unresolved), 1)
        self.assertIsNone(r.Modes["Als.Mode.View"].ByPose[0].RotSpeed)

    def test_settings_in_functions_and_locals(self):
        src = DECLS + """
logic Z;
param Cfg: Rules;
input M: AlsMode;
state A: float;
state B: float;
fn pick(s: Stances, p: Pose) -> Gait { return s.ByPose[p]; }
fn walk_of(r: Rules) -> float { return 0.0; }
update(dt) {
    let st = Cfg.Modes[M];
    let g = pick(st, Pose.Stand);
    let again = pick(Cfg.Modes[M], Pose.Stand);
    A = g.Walk + again.Run;
    B = Cfg.Traces[0].Reach;
}
"""
        m = compile_ok(src).module
        a = harness.run_interpreter(m, [frame(M="Als.Mode.View")], Cfg=DATA)
        b = harness.run_python(m, [frame(M="Als.Mode.View")], Cfg=DATA)
        self.assertEqual(harness.compare(a, b), [])
        self.assertEqual((a[0]["A"], a[0]["B"]), (150.0 + 400.0, 11.0))

    def test_settings_reset_with_the_logic(self):
        m = compile_ok(PROGRAM).module
        cls = load_class(m)
        obj = cls(None, Cfg=DATA)
        self.assertEqual(obj.Cfg.Speed, 2.5)
        obj.reset()
        self.assertEqual(obj.Cfg.Speed, 2.5)                          # params are not state

    def test_the_generated_python_carries_a_schema(self):
        src = generate_python(compile_ok(PROGRAM).module)
        self.assertIn("class Rules(_rt.Struct):", src)
        self.assertIn("DAL_SCHEMA = ", src)
        self.assertIn("def _derive(self):", src)
        self.assertIn("from dal import settings as _settings", src)
        ns, _m = rules()
        self.assertEqual(ns["DAL_SCHEMA"]["Rules"]["kind"], "settings")
        self.assertEqual(set(ns["DAL_CLASSES"]), {"Trace", "Gait", "Stances", "Rules"})

    def test_programs_without_settings_do_not_import_the_loader(self):
        src = generate_python(compile_ok("logic Q; state X: float;\nupdate(dt) { X += dt; }").module)
        self.assertNotIn("settings", src)


def load_class_ns(module):
    ns: dict = {}
    exec(compile(generate_python(module), "<gen>", "exec"), ns)
    return ns


class SettingsConsistencyTests(unittest.TestCase):
    def test_interpreter_and_python_agree_on_random_data(self):
        m = compile_ok(PROGRAM).module
        ty = m.params[0].ty
        for seed in range(1, 13):
            data = harness.random_settings(ty, seed)
            with self.subTest(seed=seed):
                self.assertEqual(harness.verify(m, 80, seed, Cfg=data), [])

    def test_random_frames_with_the_data_inside_the_curves(self):
        m = compile_ok(PROGRAM).module
        ty = m.params[0].ty
        for seed in (1, 2, 3):
            data = harness.random_settings(ty, seed, scale=100.0)
            frames = harness.random_frames(m, 80, seed)
            for f in frames:
                f["free"]["X"] = (hash((seed, f["time"])) % 1400) / 10.0 - 20.0
            self.assertEqual(harness.compare(harness.run_interpreter(m, frames, Cfg=data), harness.run_python(m, frames, Cfg=data)), [])

    def test_the_command_line_checks_a_settings_program_with_a_data_file(self):
        from dal import cli

        work = tempfile.mkdtemp(prefix="dal_set_")
        try:
            src, data = os.path.join(work, "t.dal"), os.path.join(work, "cfg.json")
            with open(src, "w", encoding="utf-8") as f:
                f.write(PROGRAM)
            with open(data, "w", encoding="utf-8") as f:
                json.dump(DATA, f)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["verify", src, "--frames", "30", "--param", f"Cfg={data}"]), 0)
                self.assertEqual(cli.main(["verify", src, "--frames", "30", "--param", "Cfg"]), 1)
        finally:
            shutil.rmtree(work, ignore_errors=True)


# ---- C++ ----------------------------------------------------------------------------------------------------------------

@unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
class SettingsCppTests(unittest.TestCase):
    def frames(self, m, n, seed):
        frames = harness.random_frames(m, n, seed)
        for k, f in enumerate(frames):
            f["free"]["X"] = ((seed * 7919 + k * 104729) % 1400) / 10.0 - 20.0           # across the curves' range
        return frames

    def test_data_asset_matches_python(self):
        m = compile_ok(PROGRAM).module
        for seed in (1, 2):
            tcc.CppCompileTests.run_case(self, m, self.frames(m, 80, seed), warnings_as_errors=True, params={"Cfg": DATA})

    def test_class_defaults_match_python_without_an_asset(self):
        m = compile_ok(PROGRAM).module
        tcc.CppCompileTests.run_case(self, m, self.frames(m, 40, 3), warnings_as_errors=True)

    def test_random_data_matches_python(self):
        m = compile_ok(PROGRAM).module
        ty = m.params[0].ty
        for seed in (5, 6, 7):
            data = harness.random_settings(ty, seed, polyline=True)
            tcc.CppCompileTests.run_case(self, m, self.frames(m, 60, seed), warnings_as_errors=True, params={"Cfg": data}, default=2e-3)

    def test_nested_settings_param_is_a_value(self):
        src = DECLS + """
logic T;
param Tr: Trace;
param Speed: float = 3.0;
state Cos: float;
state Tall: bool;
update(dt) { Cos = Tr.AngleCos * Speed; Tall = Tr.Tall; }
"""
        m = compile_ok(src).module
        data = {"Tr": {"Angle": 90.0, "Range": [0.0, 300.0]}}
        tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 10, 1), warnings_as_errors=True, params=data)

    def test_generated_text(self):
        files = generate_cpp(compile_ok(PROGRAM).module)
        self.assertEqual(sorted(files), ["DalData.h", "DalMath.h", "Tuned.cpp", "Tuned.h"])
        h, cpp = files["Tuned.h"], files["Tuned.cpp"]
        self.assertIn("USTRUCT(BlueprintType)\nstruct FTrace\n{", h)
        self.assertIn("UCLASS(BlueprintType)\nclass URules : public UDataAsset\n{", h)
        self.assertIn("TMap<FGameplayTag, FStances> Modes;", h)
        self.assertIn("TMap<EPose, FGait> ByPose;", h)
        self.assertIn("TArray<FTrace> Traces;", h)
        self.assertIn("TObjectPtr<UCurveVector> Accel;", h)
        self.assertIn("TObjectPtr<UAnimSequenceBase> Clip;", h)
        self.assertIn("TObjectPtr<URules> Cfg;", h)
        self.assertIn("UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = \"DAL\", Transient)\n\tfloat AngleCos = 0.81915204f;", h)
        self.assertIn("virtual void PostLoad() override", h)
        self.assertIn("const URules& DalSettings_Cfg() const", h)
        self.assertIn("AngleCos = DalMath::Cos(DalMath::DegToRad(Angle));", h)
        self.assertIn("const FStances& st = DalData::MapGet(DalSettings_Cfg().Modes, M, DalData::DefaultOf<FStances>());", cpp)
        self.assertIn("Acc = DalData::EvalCurve3(g.Accel, X);", cpp)
        self.assertIn("HasCount = DalSettings_Cfg().Counts.Contains(K);", cpp)
        self.assertIn("Len = DalSettings_Cfg().Names.Num();", cpp)
        self.assertIn("Dal_Fn_blend(const FGait& g, float x)", cpp)
        self.assertIn('#include "DalData.h"', cpp)

    def test_programs_without_settings_get_no_data_header(self):
        files = generate_cpp(compile_ok("logic Q; state X: float; state S: sequence = none;\nupdate(dt) { X += dt; }").module)
        self.assertNotIn("DalData.h", files)
        self.assertNotIn("DalData.h", files["Q.h"] + files["Q.cpp"])
        self.assertIn("TObjectPtr<UAnimSequenceBase> S = nullptr;", files["Q.h"])
        self.assertIn('#include "Animation/AnimSequenceBase.h"', files["Q.h"])


PROBE_SRC = """use "als_lib.dal";
logic AlsProbe;
param Cs: AlsCharacterSettings;
param Ms: AlsMovementSettings;
param Ais: AlsAnimationInstanceSettings;
input Mode: AlsRotationMode;
input Stance: AlsStance;
input Speed: float;
state Cos: float;
state Walk: float;
state Sprint: float;
state Stride: float;
state Turn: sequence = none;
state Roll: montage = none;
state Accel: vec3;
update(dt) {
    let gait = Ms.RotationModes[Mode].Stances[Stance];
    Cos = Cs.Mantling.SlopeAngleThresholdCos;
    Walk = gait.WalkForwardSpeed;
    Sprint = gait.SprintSpeed;
    Stride = Ais.Standing.StrideBlendAmountWalkCurve.eval(Speed);
    Turn = Ais.TurnInPlace.StandingTurn90Left.Sequence;
    Roll = Cs.Rolling.Montage;
    Accel = gait.AccelerationAndDecelerationAndGroundFrictionCurve.eval(Speed);
}
"""

PROBE_PARAMS = {"Cs": os.path.join(PACK, "data", "CS_Als_Default.json"),
                "Ms": os.path.join(PACK, "data", "MS_Als_Normal.json"),
                "Ais": os.path.join(PACK, "data", "AIS_Als_Default.json")}
PROBE_FRAMES = ([frame(Mode="Als.RotationMode.VelocityDirection", Stance="Als.Stance.Standing", Speed=s) for s in (0.0, 50.0, 100.0, 400.0)]
                + [frame(Mode="Als.RotationMode.Aiming", Stance="Als.Stance.Crouching", Speed=s) for s in (0.0, 120.0, 800.0)])


def probe_module():
    with open(ALS_LIB, encoding="utf-8") as f:
        return dal.check_source(PROBE_SRC, "probe.dal", files={"als_lib.dal": f.read()}).module


# ---- the ALS settings of the exported pack --------------------------------------------------------------------------------

def same(test, js, value, path="", derived=("SlopeAngleThresholdCos",)):
    """Check loaded data against the JSON it came from, for what can be compared directly."""
    if isinstance(value, rt.Struct):
        test.assertIsInstance(js, dict, path)
        for k, x in js.items():
            if k.startswith("$") or k not in value._FIELDS:
                continue
            same(test, x, getattr(value, k), f"{path}.{k}")
    elif isinstance(value, bool):
        test.assertEqual(js, value, path)
    elif isinstance(value, (int, float)):
        if isinstance(js, (int, float)):
            test.assertAlmostEqual(js, value, places=5, msg=path)
    elif isinstance(value, tuple) and value and all(isinstance(c, float) for c in value) and not isinstance(js, str):
        comps = list(js.values()) if isinstance(js, dict) else list(js)
        test.assertEqual(len(comps), len(value), path)
        for a, b in zip(comps, value):
            test.assertAlmostEqual(a, b, places=5, msg=path)
    elif isinstance(value, tuple):
        test.assertEqual(len(js), len(value), path)
        for i, (a, b) in enumerate(zip(js, value)):
            same(test, a, b, f"{path}[{i}]")
    elif isinstance(value, dict):
        test.assertIsInstance(js, dict, path)
        test.assertEqual(len(js), len(value), path)
        for (jk, jv), (k, v) in zip(js.items(), value.items()):
            if isinstance(k, str):
                test.assertEqual(jk, k, path)
            same(test, jv, v, f"{path}[{jk}]")
    elif isinstance(value, str):
        test.assertEqual(dsettings.asset_name(js) if not value.startswith("Als.") else js, value, path)


@unittest.skipUnless(HAVE_PACK, "the ALS pack is not on this machine")
class AlsPackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lib = dal.check_file(ALS_LIB)
        cls.ns = load_class(lib.module)
        cls.resolver = dsettings.PackResolver(PACK)

    def data(self, name):
        with open(os.path.join(PACK, "data", name + ".json"), encoding="utf-8-sig") as f:
            return json.load(f)

    def load(self, cls, name):
        rep = dsettings.Report()
        inst = self.ns[cls].load(os.path.join(PACK, "data", name + ".json"), self.resolver, rep)
        return inst, rep

    def test_character_settings(self):
        inst, rep = self.load("AlsCharacterSettings", "CS_Als_Default")
        same(self, self.data("CS_Als_Default")["props"], inst)
        self.assertEqual(rep.unresolved, [])
        self.assertEqual(sorted(w.rsplit(".", 1)[-1].split(":")[0] for w in rep.warnings), ["MantlingTraceResponses"])     # the response container: derived from the channel list
        self.assertEqual(inst.Mantling.MantlingTraceChannel, 3)                                                          # Channel.Visibility
        self.assertEqual(inst.Mantling.MantlingTraceResponseChannels, (0, 1, 7))                                          # WorldStatic, WorldDynamic, Destructible
        self.assertAlmostEqual(inst.Mantling.SlopeAngleThresholdCos, math.cos(math.radians(35.0)), places=6)
        self.assertEqual(inst.InAirRotationMode, 1)
        self.assertEqual((inst.Rolling.Montage, inst.Ragdolling.GetUpFrontMontage), ("AM_Als_Roll", "AM_Als_GetUp_Front"))

    def test_animation_instance_settings_with_their_curves(self):
        inst, rep = self.load("AlsAnimationInstanceSettings", "AIS_Als_Default")
        same(self, self.data("AIS_Als_Default")["props"], inst)
        self.assertEqual(rep.unresolved, [])
        for curve_ in (inst.Standing.StrideBlendAmountWalkCurve, inst.Standing.StrideBlendAmountRunCurve, inst.Crouching.StrideBlendAmountCurve,
                       inst.Grounded.RotationYawOffsetForwardCurve, inst.InAir.LeanAmountCurve, inst.InAir.GroundPredictionAmountCurve):
            self.assertIsInstance(curve_, dcurve.RichCurve)
            self.assertGreaterEqual(len(curve_.keys), 2)
        self.assertEqual(inst.Transitions.StandingLeftSequence, "A_Als_Stand_Transition_Left")
        self.assertEqual(inst.TurnInPlace.StandingTurn90Left.Sequence, "A_Als_Turn_90_Left")
        self.assertAlmostEqual(inst.Standing.StrideBlendAmountWalkCurve.eval(150.0), 1.0, places=5)

    def test_movement_settings_with_their_tag_keyed_tables(self):
        for name in ("MS_Als_Normal", "MS_Als_Responsive", "MS_Als_Sluggish"):
            with self.subTest(name=name):
                inst, rep = self.load("AlsMovementSettings", name)
                same(self, self.data(name)["props"], inst)
                self.assertEqual(rep.unresolved, [])
                self.assertEqual(rep.warnings, [])
                self.assertEqual(sorted(inst.RotationModes), ["Als.RotationMode.Aiming", "Als.RotationMode.VelocityDirection", "Als.RotationMode.ViewDirection"])
                gait = inst.RotationModes["Als.RotationMode.VelocityDirection"].Stances["Als.Stance.Standing"]
                self.assertEqual(gait.WalkForwardSpeed, {"MS_Als_Normal": 175.0, "MS_Als_Responsive": 175.0, "MS_Als_Sluggish": 150.0}[name])
                accel = gait.AccelerationAndDecelerationAndGroundFrictionCurve.eval(100.0)
                self.assertIsInstance(accel, rt.Vec3)
                self.assertGreater(accel.x, 0.0)

    def test_every_mantling_settings_file(self):
        names = sorted(f[:-5] for f in os.listdir(os.path.join(PACK, "data")) if f.startswith("MS_Als_") and not f.endswith("Normal.json")
                       and not f.endswith("Responsive.json") and not f.endswith("Sluggish.json"))
        self.assertGreaterEqual(len(names), 6)
        for name in names:
            with self.subTest(name=name):
                inst, rep = self.load("AlsMantlingSettings", name)
                same(self, self.data(name)["props"], inst)
                self.assertEqual(rep.unresolved, [])
                self.assertTrue(inst.Montage)
                self.assertGreater(inst.MotionWarpingTimeRange.Max, 0.0)

    def test_a_logic_reads_the_pack_in_both_python_implementations(self):
        m = probe_module()
        params = dict(PROBE_PARAMS, resolver=self.resolver)
        a, b = harness.run_interpreter(m, PROBE_FRAMES, **params), harness.run_python(m, PROBE_FRAMES, **params)
        self.assertEqual(harness.compare(a, b), [])
        self.assertAlmostEqual(a[0]["Cos"], math.cos(math.radians(35.0)), places=6)
        self.assertEqual((a[0]["Walk"], a[0]["Sprint"]), (175.0, 650.0))
        self.assertEqual((a[0]["Turn"], a[0]["Roll"]), ("A_Als_Turn_90_Left", "AM_Als_Roll"))
        self.assertGreater(a[3]["Stride"], 0.5)                       # 400 is beyond the last key of the walk curve (150): its value, 1
        self.assertGreater(a[3]["Accel"][0], 0.0)


@unittest.skipUnless(HAVE_PACK and tcc.COMPILER is not None, "needs the ALS pack and a C++ compiler")
class AlsPackCppTests(unittest.TestCase):
    def test_the_sketch_library_and_the_pack_data_in_cpp(self):
        # the stand-in engine has no cubic curves: leave them out on both sides (no resolver) and compare everything else
        m = probe_module()
        tcc.CppCompileTests.run_case(self, m, PROBE_FRAMES, warnings_as_errors=True, params=PROBE_PARAMS)

    def test_the_library_header_declares_every_settings_type(self):
        files = generate_cpp(dal.check_file(ALS_LIB).module)
        h = files["AlsLib.h"]
        for name in ("UAlsCharacterSettings", "UAlsAnimationInstanceSettings", "UAlsMovementSettings", "UAlsMantlingSettings"):
            self.assertIn(f"class {name} : public UDataAsset", h)
        self.assertIn("struct FAlsGeneralMantlingSettings", h)
        self.assertIn("TMap<FGameplayTag, FAlsMovementStanceSettings> RotationModes;", h)
        self.assertIn("float SlopeAngleThresholdCos = ", h)
        self.assertIn("namespace DalLib_AlsLib", h)
        self.assertIn("inline float Dal_Fn_damper_exact(", h)
        self.assertEqual(h.count("UCLASS(BlueprintType)"), 4)


# ---- the sketch library of ALS, without the pack ----------------------------------------------------------------------------

MATH_PROBE = """use "als_lib.dal";
logic MathProbe;
input A: float;
input B: float;
input C: float;
input Dt: float;
input V: vec3;
state Clamp01: float;
state LerpClamped: float;
state Damper: float;
state DamperVec: vec3;
state ClampMag: vec3;
state Angle: float;
state Dir: vec3;
state LerpAngle: float;
state Remap: float;
state Constant: float;
state DamperAngle: float;
state Direction: AlsMovementDirection;
state Stance: AlsStance = AlsStance.Standing;
state Hips: AlsHipsDirection;
state Foot: AlsFootState;
state Locomotion: AlsLocomotionState;
update(dt) {
    Clamp01 = clamp01(A);
    LerpClamped = lerp_clamped(A, B, C);
    Damper = damper_exact(A, B, abs(Dt), abs(C) + 0.01);
    DamperVec = damper_exact_vec3(V, vec3(B, C, A), abs(Dt), abs(C) + 0.01);
    ClampMag = clamp_magnitude01(V);
    Angle = direction_to_angle_xy(V);
    Dir = angle_to_direction_xy(A * 30.0);
    LerpAngle = lerp_angle(A * 40.0, B * 40.0, C);
    Remap = remap_angle_for_counter_clockwise_rotation(A * 20.0);
    Constant = interpolate_angle_constant(A * 40.0, B * 40.0, abs(Dt), abs(C) * 50.0);
    DamperAngle = damper_exact_angle(A * 40.0, B * 40.0, abs(Dt), abs(C) + 0.01);
    Direction = calculate_movement_direction(A * 20.0, 70.0, 50.0);
    Stance = (A > 0.0) ? AlsStance.Crouching : AlsStance.Standing;
    Hips = AlsHipsDirection.LeftBackward;
    Foot.bLockingActive = A > 0.0;
    Foot.LockAmount = Clamp01;
    Foot.FinalLocation = V;
    Locomotion.Speed = length(V);
    Locomotion.bMoving = Locomotion.Speed > 50.0;
    Locomotion.AimingYawAngleLimit -= 1.0;
}
"""


def math_probe():
    with open(ALS_LIB, encoding="utf-8") as f:
        return dal.check_source(MATH_PROBE, "probe.dal", files={"als_lib.dal": f.read()}).module


class AlsLibTests(unittest.TestCase):
    def test_the_library_checks_alone(self):
        lib = dal.check_file(ALS_LIB).module
        self.assertTrue(lib.is_library)
        self.assertEqual(len([s for s in lib.structs if s.is_settings]), 23)
        self.assertEqual(len(lib.tagsets), 8)
        self.assertEqual([f.name for f in lib.funcs][:3], ["clamp01", "lerp_clamped", "damper_exact_alpha"])

    def test_its_states_and_helpers_run_the_same_in_both_python_implementations(self):
        m = math_probe()
        for seed in (1, 2, 3):
            self.assertEqual(harness.verify(m, 150, seed), [], seed)

    def test_helpers_by_hand(self):
        m = math_probe()
        out = harness.run_python(m, [frame(A=0.5, B=2.0, C=0.5, Dt=0.016, V=(3.0, 4.0, 0.0))])[0]
        self.assertEqual(out["Clamp01"], 0.5)
        self.assertEqual(out["LerpClamped"], 0.5 + 0.5 * 1.5)
        self.assertAlmostEqual(out["Angle"], math.degrees(math.atan2(4.0, 3.0)))
        self.assertAlmostEqual(out["ClampMag"][0] ** 2 + out["ClampMag"][1] ** 2, 1.0)
        self.assertEqual(out["Remap"], 10.0)
        self.assertEqual(out["Direction"], 0)                         # 10 degrees is within the forward half angle (70)
        for a, expected in ((5.0, 3), (-5.0, 2), (8.0, 1), (-8.0, 1)):                      # 100: right, -100: left, +-160: backward
            self.assertEqual(harness.run_python(m, [frame(A=a, B=0.0, C=0.0, Dt=0.0, V=(0.0, 0.0, 0.0))])[0]["Direction"], expected, a)
        self.assertEqual(out["Locomotion.AimingYawAngleLimit"], 179.0)
        self.assertEqual(out["Locomotion.bRotationTowardsLastInputDirectionBlocked"], True)
        self.assertEqual(out["Foot.bLockingActive"], True)

    @unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
    def test_its_helpers_in_cpp(self):
        m = math_probe()
        for seed in (1, 2):
            frames = harness.random_frames(m, 80, seed)
            tcc.CppCompileTests.run_case(self, m, frames, warnings_as_errors=True, default=2e-3)


if __name__ == "__main__":
    unittest.main()
