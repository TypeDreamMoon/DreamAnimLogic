"""The host reads of the ALS rewrite: character / capsule items, string-argument reads (animation curves) and
consume-on-read items (the pre-penetration velocity), in the checker, both Python implementations and C++."""

import unittest

import _bootstrap  # noqa: F401

import dal
import test_cpp_compile as tcc
from dal import harness
from dal import runtime as rt
from dal.cppbackend import generate_cpp
from dal.hostapi import HOST_ITEMS, HostV0, HostV1
from dal.interp import Interpreter
from dal.mockhost import MockHost
from dal.pybackend import generate_python, load_class

SRC = """
logic Hr : actor;
input R: float <- host.capsule_radius;
input HH: float <- host.capsule_half_height;
input Sc: float <- host.actor_scale;
input Wz: float <- host.walkable_floor_z;
input Ready: bool <- host.actor_ready;
input Rm: bool <- host.has_root_motion;
input Mc: int <- host.movement_mode_custom;
input Head: float <- host.anim_curve("LayerHead");
input Legs: float <- host.anim_curve("LayerLegs");
input PV: vec3 <- host.pre_penetration_velocity;
input PVValid: bool <- host.pre_penetration_velocity_valid;
state Sum: float;
state Last: vec3;
state Got: int;
update(dt) {
    Sum = R + HH + Sc + Wz + Head + Legs + Mc;
    if (PVValid) { Last = PV; Got += 1; }
    if (Ready && Rm) { Sum += 1.0; }
}
"""

ANIM_SRC = SRC.replace("logic Hr : actor;", "logic Hr;")
PLAIN = """logic Q : actor;
input V: vec3 <- host.velocity;
state S: float;
update(dt) { S += length(V); }"""


def codes(src):
    try:
        dal.check_source(src, "t.dal")
    except dal.DalError as e:
        return [d.code for d in e.diagnostics if d.severity == "error"]
    return []


class ReadTableTests(unittest.TestCase):
    NEW = ("capsule_radius", "capsule_half_height", "actor_scale", "walkable_floor_z", "actor_ready", "has_root_motion",
           "movement_mode_custom", "anim_curve", "pre_penetration_velocity", "pre_penetration_velocity_valid")

    def test_the_items_are_in_the_table_and_on_the_base_class(self):
        for name in self.NEW:
            self.assertIn(name, HOST_ITEMS)
            self.assertTrue(hasattr(HostV1, name), name)
            self.assertTrue(HOST_ITEMS[name].ue, name)
        self.assertTrue(callable(HostV0.anim_curve))
        self.assertTrue(callable(HostV0.consume_pre_penetration_velocity))
        self.assertEqual(HOST_ITEMS["anim_curve"].arg_kind, "string")
        self.assertEqual({HOST_ITEMS[n].consume for n in ("pre_penetration_velocity", "pre_penetration_velocity_valid")}, {"consume_pre_penetration_velocity"})

    def test_neutral_defaults(self):
        h = HostV1()
        self.assertEqual((h.capsule_radius, h.actor_scale, h.actor_ready, h.has_root_motion, h.movement_mode_custom), (0.0, 1.0, True, False, 0))
        self.assertEqual(h.anim_curve("Anything"), 0.0)
        self.assertEqual(h.consume_pre_penetration_velocity(), (False, rt.VEC3_ZERO))

    def test_the_documentation_names_them(self):
        from dal.hostapi import protocol_markdown

        text = protocol_markdown()
        for name in self.NEW:
            self.assertIn(f"`{name}", text)
        self.assertIn("`anim_curve(string)`", text)
        self.assertIn("DalRuntime", text)


class ReadCheckTests(unittest.TestCase):
    def test_binding_rules(self):
        self.assertEqual(codes('logic T;\ninput C: float <- host.anim_curve("LayerHead");\nupdate(dt) { }'), [])
        self.assertEqual(codes("logic T;\ninput C: float <- host.anim_curve;\nupdate(dt) { }"), ["DAL3021"])
        self.assertEqual(codes("logic T;\ninput C: float <- host.anim_curve(LayerHead);\nupdate(dt) { }"), ["DAL3023"])
        self.assertEqual(codes('logic T;\ninput C: float <- host.anim_curve("Layer Head");\nupdate(dt) { }'), [])        # curve names may have single spaces
        self.assertEqual(codes('logic T;\ninput C: float <- host.anim_curve("Layer  Head");\nupdate(dt) { }'), ["DAL3022"])
        self.assertEqual(codes('logic T;\ninput C: int <- host.anim_curve("A");\nupdate(dt) { }'), ["DAL4020"])
        self.assertEqual(codes('logic T;\ninput C: float <- host.actor_ready;\nupdate(dt) { }'), ["DAL4020"])
        self.assertEqual(codes('logic T;\ninput C: bool <- host.capsule_radius("a");\nupdate(dt) { }'), ["DAL3021", "DAL4020"][:1])
        self.assertEqual(codes('logic T;\ninput C: float <- host.anim_curv("A");\nupdate(dt) { }'), ["DAL3020"])
        self.assertEqual(codes("logic T;\ninput V: vec3 <- host.pre_penetration_velocity;\ninput B: bool <- host.pre_penetration_velocity_valid;\nupdate(dt) { }"), [])
        self.assertEqual(codes("logic T;\ninput V: vec2 <- host.pre_penetration_velocity;\nupdate(dt) { }"), ["DAL4020"])

    def test_the_curve_name_is_kept_in_the_binding(self):
        m = dal.check_source(SRC, "t.dal").module
        head = m.find_input("Head")
        self.assertEqual((head.host.item, head.host.arg), ("anim_curve", "LayerHead"))


class ReadRunTests(unittest.TestCase):
    def objects(self, host=None, src=SRC):
        m = dal.check_source(src, "t.dal").module
        return (load_class(m)(host or MockHost()), Interpreter(m, host or MockHost()))

    def test_values_reach_the_inputs(self):
        for obj in self.objects():
            h = obj.host
            h.capsule_radius, h.capsule_half_height, h.actor_scale, h.walkable_floor_z = 30.0, 90.0, 2.0, 0.5
            h.has_root_motion, h.movement_mode_custom = True, 3
            h._curves = {"LayerHead": 0.25, "LayerLegs": 0.5}
            obj.update(0.1)
            snap = obj.snapshot()
            self.assertEqual(snap["Sum"], 30.0 + 90.0 + 2.0 + 0.5 + 0.25 + 0.5 + 3 + 1.0)
            self.assertEqual((snap["R"], snap["Ready"], snap["Rm"], snap["Mc"]), (30.0, True, True, 3))

    def test_a_curve_that_is_not_there_reads_zero(self):
        for obj in self.objects():
            obj.update(0.1)
            self.assertEqual((obj.snapshot()["Head"], obj.snapshot()["Legs"]), (0.0, 0.0))

    def test_the_pre_penetration_velocity_is_consumed_by_the_read(self):
        for obj in self.objects():
            h = obj.host
            h.pre_penetration_velocity, h.pre_penetration_velocity_valid = rt.Vec3(1.0, 2.0, 3.0), True
            obj.update(0.1)
            self.assertEqual(obj.snapshot()["Got"], 1)
            self.assertEqual(obj.snapshot()["Last"], (1.0, 2.0, 3.0))
            self.assertEqual(h.pre_penetration_velocity_valid, False)             # the host cleared it
            obj.update(0.1)
            self.assertEqual(obj.snapshot()["Got"], 1)
            self.assertEqual(obj.snapshot()["PVValid"], False)
            self.assertEqual(obj.snapshot()["PV"], (0.0, 0.0, 0.0))

    def test_one_call_serves_both_inputs_and_unread_values_stay(self):
        class Counting(MockHost):
            calls = 0

            def consume_pre_penetration_velocity(self):
                Counting.calls += 1
                return super().consume_pre_penetration_velocity()

        for obj in self.objects(Counting()):
            Counting.calls = 0
            obj.update(0.1)
            self.assertEqual(Counting.calls, 1)
        only_valid = "logic T : actor;\ninput B: bool <- host.pre_penetration_velocity_valid;\nstate N: int;\nupdate(dt) { if (B) { N += 1; } }"
        for obj in self.objects(Counting(), only_valid):
            Counting.calls = 0
            obj.update(0.1)
            self.assertEqual(Counting.calls, 1)                                     # reading the flag consumes the value too
        no_consumer = "logic T : actor;\ninput C: float <- host.anim_curve(\"A\");\nstate N: float;\nupdate(dt) { N = C; }"
        for obj in self.objects(Counting(), no_consumer):
            Counting.calls = 0
            obj.update(0.1)
            self.assertEqual(Counting.calls, 0)

    def test_an_override_does_not_consume(self):
        for obj in self.objects():
            obj.host.pre_penetration_velocity, obj.host.pre_penetration_velocity_valid = rt.Vec3(4.0, 0.0, 0.0), True
            obj.overrides["PV"] = (9.0, 0.0, 0.0)
            obj.overrides["PVValid"] = True
            obj.update(0.1)
            self.assertEqual(obj.snapshot()["Last"], (9.0, 0.0, 0.0))
            self.assertTrue(obj.host.pre_penetration_velocity_valid)                # still there for the next reader

    def test_the_generated_python_reads_the_group_lazily(self):
        src = generate_python(dal.check_source(SRC, "t.dal").module)
        self.assertIn("c_consume_pre_penetration_velocity = None", src)
        self.assertIn("c_consume_pre_penetration_velocity = h.consume_pre_penetration_velocity()", src)
        self.assertIn("h.anim_curve('LayerHead')", src)

    def test_interpreter_and_python_agree_on_random_frames(self):
        for src in (SRC, ANIM_SRC):
            m = dal.check_source(src, "t.dal").module
            for seed in (1, 2, 3):
                self.assertEqual(harness.verify(m, 100, seed), [], seed)

    def test_earlier_random_sequences_are_unchanged(self):
        # the later host items draw from their own stream: reading them does not change the other values of the frames
        old = dal.check_source(PLAIN, "q.dal").module
        new = dal.check_source(SRC, "t.dal").module
        a, b = harness.random_frames(old, 20, 5), harness.random_frames(new, 20, 5)
        for fa, fb in zip(a, b):
            self.assertEqual((fa["dt"], fa["velocity"], fa["max_walk_speed"], fa["actor_location"]), (fb["dt"], fb["velocity"], fb["max_walk_speed"], fb["actor_location"]))


@unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
class ReadCppTests(unittest.TestCase):
    def test_actor_logic_matches_python(self):
        m = dal.check_source(SRC, "t.dal").module
        for seed in (1, 2, 3):
            tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 80, seed), warnings_as_errors=True)

    def test_anim_logic_matches_python(self):
        m = dal.check_source(ANIM_SRC, "t.dal").module
        tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 80, 4), warnings_as_errors=True)

    def test_generated_text(self):
        files = generate_cpp(dal.check_source(SRC, "t.dal").module)
        cpp = files["Hr.cpp"]
        self.assertIn("// Module dependencies: Core, CoreUObject, Engine, DalRuntime", files["Hr.h"])
        self.assertIn('#include "DalCharacterMovementComponent.h"', cpp)
        self.assertIn('#include "Components/CapsuleComponent.h"', cpp)
        self.assertIn("bDalPrePenetration = DalMovement->TryConsumePrePenetrationAdjustmentVelocity(DalPrePenetration);", cpp)
        self.assertIn('Head = DalAnim != nullptr ? DalAnim->GetCurveValue(FName(TEXT("LayerHead"))) : 0.0f;', cpp)
        self.assertIn("Ready = Pawn->HasActorRegisteredAllComponents();", cpp)
        self.assertIn("Wz = Movement->GetWalkableFloorZ();", cpp)
        self.assertEqual(cpp.count("TryConsumePrePenetrationAdjustmentVelocity"), 1)         # one call serves both inputs

    def test_programs_that_do_not_use_them_are_unchanged(self):
        files = generate_cpp(dal.check_source("logic Q : actor;\ninput V: vec3 <- host.velocity;\nstate S: float;\nupdate(dt) { S += length(V); }", "q.dal").module)
        self.assertNotIn("DalRuntime", files["Q.h"])
        self.assertNotIn("Capsule", files["Q.cpp"])


if __name__ == "__main__":
    unittest.main()
