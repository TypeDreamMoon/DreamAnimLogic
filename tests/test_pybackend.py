"""The Python backend: generated class API, semantics of the generated code, bundling of stand-ins."""

import ast
import os
import unittest

import _bootstrap  # noqa: F401
from _bootstrap import EXAMPLES

import dal
from dal import harness
from dal import runtime as rt
from dal.hostapi import HostV0
from dal.pybackend import generate_python, load_class


def build(src, standins=None):
    module = dal.check_source(src, "t.dal", standins).module
    text = generate_python(module)
    return module, text, load_class(module, text)


class GeneratedModuleTests(unittest.TestCase):
    def test_examples_generate_valid_python(self):
        for name in ("als_lean.dal", "hero_gait.dal"):
            result = dal.check_file(os.path.join(EXAMPLES, name))
            text = generate_python(result.module)
            ast.parse(text)                                   # must at least parse
            self.assertIn(f"class {result.module.name}(_rt.LogicBase):", text)

    def test_output_is_deterministic_and_has_no_timestamps(self):
        result = dal.check_file(os.path.join(EXAMPLES, "hero_gait.dal"))
        a = generate_python(result.module)
        b = generate_python(dal.check_file(os.path.join(EXAMPLES, "hero_gait.dal")).module)
        self.assertEqual(a, b)
        self.assertNotIn("\r", a)
        self.assertTrue(a.endswith("\n") and not a.endswith("\n\n"))

    def test_standins_are_bundled_and_used(self):
        result = dal.check_file(os.path.join(EXAMPLES, "als_lean.dal"))
        text = generate_python(result.module)
        self.assertIn("def InAirLeanCurve(VerticalVelocity):", text)
        self.assertIn("extern stand-ins (bundled from als_lean_standins.py)", text)
        cls = load_class(result.module, text)
        host = HostV0()
        host.is_grounded = False
        host.velocity = rt.Vec3(300, 0, -400)
        host.max_acceleration = 2048.0
        host.max_braking_deceleration = 2048.0
        c = cls(host)
        c.update(1 / 60)
        # curve(-400) = 0.6 + (1.0 - 0.6) * (-400 - -200) / (-600 - -200) = 0.8; velocity / 350 * 0.8
        self.assertAlmostEqual(c.LeanForward, 300 / 350 * 0.8, places=12)

    def test_division_by_a_nonzero_literal_is_a_plain_division(self):
        _, text, _ = build("logic T;\nstate S: float;\ninput I: float;\nupdate(dt) { S = I / 2.0; S = S / I; }")
        self.assertIn("self.S = self.I / 2.0", text)
        self.assertIn("_rt.fdiv(self.S, self.I)", text)

    def test_keyword_like_enum_members_are_mangled(self):
        _, text, cls = build("logic T;\nenum E { None, Some }\nstate S: E = E.Some;\nupdate(dt) { if (S == E.Some) { S = E.None; } }")
        self.assertIn("None_ = 0", text)
        c = cls()
        c.update(0.1)
        self.assertEqual(int(c.S), 0)

    def test_early_return_in_update(self):
        _, _, cls = build("logic T;\nstate A: int;\nstate B: int;\nupdate(dt) { A += 1; if (A > 2) { return; } B += 1; }")
        c = cls()
        for _ in range(5):
            c.update(0.1)
        self.assertEqual((c.A, c.B), (5, 2))


class ClassApiTests(unittest.TestCase):
    SRC = """logic T;
param Rate: float = 2.0;
input V: vec3 <- host.velocity;
input Mode: MovementMode <- host.movement_mode;
input Aim: bool;
input Gain: float = 1.5;
state X: float;
state Count: int = 3;
update(dt) { X = X + length(V) * Rate * Gain * dt; Count += 1; }
entry Bump(d) { X += d * 100.0; }
"""

    def setUp(self):
        self.module, self.text, self.cls = build(self.SRC)

    def test_constructor_defaults_params_and_reset(self):
        c = self.cls()
        self.assertEqual((c.Rate, c.Gain, c.Count, c.X, c.Aim), (2.0, 1.5, 3, 0.0, False))
        c2 = self.cls(None, Rate=4)
        self.assertEqual(c2.Rate, 4.0)
        self.assertIsInstance(c2.Rate, float)
        c.update(0.5)
        c.reset()
        self.assertEqual((c.X, c.Count), (0.0, 3))

    def test_unknown_param_is_an_error(self):
        with self.assertRaises(TypeError):
            self.cls(None, Nope=1)

    def test_host_sampling_and_free_inputs(self):
        host = HostV0()
        host.velocity = (3.0, 4.0, 0.0)
        host.movement_mode = 3
        c = self.cls(host)
        c.set_input("Aim", 1)
        c.set_input("Gain", 2)
        self.assertIs(c.Aim, True)
        self.assertEqual(c.Gain, 2.0)
        c.update(0.1)
        self.assertEqual(c.V, rt.Vec3(3, 4, 0))
        self.assertEqual(c.Mode, rt.MovementMode.Falling)
        self.assertAlmostEqual(c.X, 5.0 * 2.0 * 2.0 * 0.1)
        self.assertEqual(c.Count, 4)

    def test_host_bound_inputs_cannot_be_set_but_can_be_overridden(self):
        host = HostV0()
        host.velocity = (3.0, 4.0, 0.0)
        c = self.cls(host)
        with self.assertRaises(ValueError):
            c.set_input("V", (1, 1, 1))
        with self.assertRaises(KeyError):
            c.set_input("Nope", 1)
        c.overrides["V"] = (0, 0, 10)
        c.update(1.0)
        self.assertEqual(c.V, rt.Vec3(0, 0, 10))
        del c.overrides["V"]
        c.update(1.0)
        self.assertEqual(c.V, rt.Vec3(3, 4, 0))

    def test_entries_use_the_last_delta_time(self):
        c = self.cls()
        c.update(0.25)
        c.call_entry("Bump")
        self.assertAlmostEqual(c.X, 25.0)
        c.Bump()
        self.assertAlmostEqual(c.X, 50.0)
        with self.assertRaises(KeyError):
            c.call_entry("Nope")

    def test_snapshot_lists_inputs_then_states_as_plain_data(self):
        c = self.cls()
        snap = c.snapshot()
        self.assertEqual(list(snap), ["V", "Mode", "Aim", "Gain", "X", "Count"])
        self.assertEqual(snap["V"], (0.0, 0.0, 0.0))
        self.assertEqual(snap["Mode"], 0)
        self.assertIs(type(snap["Mode"]), int)

    def test_metadata_tables_for_a_variable_panel(self):
        self.assertEqual(self.cls.DAL_PARAMS, (("Rate", "float", 2.0),))
        self.assertEqual([(n, t, h) for n, t, h in self.cls.DAL_INPUTS],
                         [("V", "vec3", "host.velocity"), ("Mode", "MovementMode", "host.movement_mode"), ("Aim", "bool", None), ("Gain", "float", None)])
        self.assertEqual(self.cls.DAL_STATES, (("X", "float"), ("Count", "int")))
        self.assertEqual(self.cls.DAL_ENTRIES, ("Bump",))


class ScriptedBehaviourTests(unittest.TestCase):
    def test_hero_gait_behaviour(self):
        result = dal.check_file(os.path.join(EXAMPLES, "hero_gait.dal"))
        cls = load_class(result.module)
        host = harness.ScriptedHost()
        c = cls(host)

        def step(**frame):
            frame.setdefault("dt", 0.1)
            host.load(frame)
            c.update(frame["dt"])

        step(velocity=(600.0, 0, 0), notifies={"Footstep": True})
        self.assertEqual(c.Steps, 1)
        step(velocity=(600.0, 0, 0), notifies={"Footstep": True})
        step(velocity=(600.0, 0, 0))
        self.assertEqual(c.Steps, 2)
        for _ in range(60):
            step(velocity=(600.0, 0, 0))
        self.assertAlmostEqual(c.Speed, 600.0, places=2)
        self.assertEqual(int(c.CurrentGait), 1)                   # Run: faster than 1.5 * WalkSpeed
        step(velocity=(600.0, 0, 0), actions={"Sprint": True})
        self.assertEqual(int(c.CurrentGait), 2)                   # Sprint
        step(velocity=(600.0, 0, 0), actions={"Sprint": True}, is_crouching=True)
        self.assertEqual(int(c.CurrentGait), 0)                   # crouching always walks
        step(movement_mode=3)
        self.assertTrue(c.InAir)
        step(movement_mode=1)
        self.assertFalse(c.InAir)
        c.ResetSteps()
        self.assertEqual(c.Steps, 0)

    def test_hero_gait_aim_offset_and_move_direction(self):
        result = dal.check_file(os.path.join(EXAMPLES, "hero_gait.dal"))
        cls = load_class(result.module)
        host = harness.ScriptedHost()
        c = cls(host)
        host.load({"dt": 0.1, "control_rotation": (-80.0, 170.0, 0.0), "actions": {"Move": (3.0, 4.0)}})
        c.update(0.1)
        self.assertEqual(tuple(c.AimOffset), (0.0, 0.0))          # Aiming is false
        self.assertAlmostEqual(c.MoveDirection.x, 0.6)             # length > 1 -> normalised
        self.assertAlmostEqual(c.MoveDirection.y, 0.8)
        c.set_input("Aiming", True)
        c.update(0.1)
        self.assertAlmostEqual(c.AimOffset.x, rt.delta_angle(0.0, 170.0))   # 170
        self.assertAlmostEqual(c.AimOffset.y, -60.0)               # clamp_angle(-80, -60, 60)
        host.load({"dt": 0.1, "actions": {"Move": (0.3, 0.4)}})
        c.update(0.1)
        self.assertAlmostEqual(c.MoveDirection.x, 0.3)              # length <= 1 -> unchanged

    def test_integer_semantics_in_generated_code(self):
        src = """logic T;
state Wrap: int;
state Q: int;
state R: int;
state Z: int;
update(dt) {
    var big = 2147483647;
    big += 1;
    Wrap = big;
    Q = -7 / 2;
    R = -7 % 2;
    let zero = 0;
    Z = 5 / zero + 5 % zero;
}
"""
        _, _, cls = build(src)
        c = cls()
        c.update(0.1)
        self.assertEqual((c.Wrap, c.Q, c.R, c.Z), (-2147483648, -3, -1, 0))

    def test_float_division_by_zero_is_zero(self):
        _, _, cls = build("logic T;\nstate S: float;\nstate V: vec2;\nupdate(dt) { let zero = 0.0; S = 1.0 / zero; V = vec2(1.0, 2.0) / zero; }")
        c = cls()
        c.update(0.1)
        self.assertEqual(c.S, 0.0)
        self.assertEqual(tuple(c.V), (0.0, 0.0))

    def test_locals_and_branches(self):
        src = """logic T;
input X: float;
state Out: float;
fn pick(a: float) -> float {
    if (a < 0.0) { return -1.0; }
    else if (a < 10.0) { return 0.0; }
    var r = 1.0;
    r *= a;
    return r;
}
update(dt) {
    Out = pick(X);
}
"""
        _, _, cls = build(src)
        c = cls()
        for x, expect in ((-3.0, -1.0), (5.0, 0.0), (20.0, 20.0)):
            c.set_input("X", x)
            c.update(0.1)
            self.assertEqual(c.Out, expect)

    def test_short_circuit_does_not_change_results(self):
        _, _, cls = build("logic T;\ninput A: bool;\ninput B: bool;\nstate Y: bool;\nstate N: bool;\nupdate(dt) { Y = A && B || !A; N = !(A || B); }")
        c = cls()
        for a in (False, True):
            for b in (False, True):
                c.set_input("A", a)
                c.set_input("B", b)
                c.update(0.1)
                self.assertEqual((c.Y, c.N), ((a and b) or not a, not (a or b)))


if __name__ == "__main__":
    unittest.main()
