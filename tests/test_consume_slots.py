"""Consume-on-read host inputs (the pre-penetration velocity) in an actor logic with several tick slots: the slots whose block does not read them
leave them to the one that does (the value used to go to whichever slot sampled first)."""

import unittest

import _bootstrap  # noqa: F401

import dal
from dal import harness
from dal.cppbackend import generate_cpp

try:
    import test_cpp_compile as tcc
except Exception:                                   # pragma: no cover
    tcc = None

TWO_SLOTS = """
logic Cm : actor in pre_movement;
input PV: vec3 <- host.pre_penetration_velocity;
input Valid: bool <- host.pre_penetration_velocity_valid;
input Speed: float <- host.max_walk_speed;
state Seen: int;
state Last: vec3;
state Early: int;
view fresh() -> bool { return Valid; }
update(dt) in pre_movement { Early += 1; }
update(dt) in after_movement { if (fresh()) { Last = PV; Seen += 1; } }
"""
BOTH = TWO_SLOTS.replace("update(dt) in pre_movement { Early += 1; }", "update(dt) in pre_movement { if (Valid) { Early += 10; } }")
HANDLER = TWO_SLOTS.replace("view fresh()", "on jumped { Early += Valid ? 100 : 1; }\nview fresh()")


def compile_ok(src):
    return dal.check_source(src, "t.dal").module


def fr(valid, v=(1.0, 2.0, 3.0), dt=0.125, **kw):
    return dict(dt=dt, pre_penetration_velocity_valid=valid, pre_penetration_velocity=v, **kw)


FRAMES = [fr(True), fr(False), fr(True, (4.0, 5.0, 6.0)), fr(True, (7.0, 8.0, 9.0))]


class ModuleTests(unittest.TestCase):
    def test_the_slots_that_read_them(self):
        self.assertEqual(compile_ok(TWO_SLOTS).consume_slots(), ("after_movement",))               # read through a view, in the second block
        self.assertIsNone(compile_ok(BOTH).consume_slots())                                          # both blocks read it: every slot samples
        self.assertIsNone(compile_ok("logic T : actor;\ninput V: vec3 <- host.pre_penetration_velocity;\nstate S: float;\nupdate(dt) { S = V.x; }").consume_slots())
        self.assertIsNone(compile_ok("logic T : actor in pre_movement;\nstate S: int;\nupdate(dt) in pre_movement { S += 1; }\nupdate(dt) in after_movement { S += 2; }").consume_slots())

    def test_an_event_handler_reads_in_the_first_slot(self):
        self.assertEqual(compile_ok(HANDLER.replace("input Speed", "input Speed")).consume_slots(), None)       # the handler (first slot) and the second block both read it


class SemanticsTests(unittest.TestCase):
    def run_both(self, src):
        module = compile_ok(src)
        py = harness.run_python(module, FRAMES)
        self.assertEqual(harness.compare(py, harness.run_interpreter(module, FRAMES)), [])
        return py

    def test_the_block_that_reads_it_gets_it(self):
        out = self.run_both(TWO_SLOTS)
        self.assertEqual([f["Seen"] for f in out], [1, 1, 2, 3])                          # the first block's sampling does not eat it
        self.assertEqual(out[2]["Last"], (4.0, 5.0, 6.0))
        self.assertEqual(out[3]["Last"], (7.0, 8.0, 9.0))

    def test_when_both_read_it_the_first_gets_it(self):
        out = self.run_both(BOTH)
        self.assertEqual([f["Early"] for f in out], [10, 10, 20, 30])
        self.assertEqual([f["Seen"] for f in out], [0, 0, 0, 0])                          # the same as before: consumed by the first reader

    def test_random_frames_agree(self):
        self.assertEqual(harness.verify(compile_ok(TWO_SLOTS), 100, 3), [])


class CppTests(unittest.TestCase):
    def test_the_generated_gate(self):
        files = generate_cpp(compile_ok(TWO_SLOTS))
        self.assertIn("void DalSampleInputs(const int32 Phase = -1);", files["Cm.h"])
        cpp = files["Cm.cpp"]
        self.assertIn("const bool bDalConsume = Phase < 0 || Phase == 1;", cpp)
        self.assertIn("= bDalConsume && IsValid(Character) ?", cpp)
        self.assertIn("DalSampleInputs(Phase);", cpp)

    def test_a_logic_that_needs_no_gate_is_unchanged(self):
        files = generate_cpp(compile_ok(BOTH))
        self.assertIn("void DalSampleInputs();", files["Cm.h"])
        self.assertNotIn("bDalConsume", files["Cm.cpp"])

    @unittest.skipIf(tcc is None or tcc.COMPILER is None, "no C++ compiler found")
    def test_the_compiled_logic_matches_python(self):
        runner = tcc.CppCompileTests("test_hero_gait_matches_python")
        for src in (TWO_SLOTS, BOTH):
            module = compile_ok(src)
            runner.run_case(module, FRAMES, warnings_as_errors=True)
            runner.run_case(module, harness.random_frames(module, 60, 5), warnings_as_errors=True)


if __name__ == "__main__":
    unittest.main()
