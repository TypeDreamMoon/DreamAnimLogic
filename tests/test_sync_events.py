"""`sync_events` (`logic X : actor sync_events;`): the hooks and declared notifies the host raises while it works are handled when it raises them, not
at the start of the next tick slot / update.  The handler samples the inputs, runs, and hands its commands over before the host's call returns;
an event raised by the commands of a handler waits until that handler is done.

The host pushes the event: `receive_event(event)` (interpreter, generated Python), `DalHost::FEventRouter::Post` (generated C++, here against the
stand-in engine).  A host that polls (`poll_events`) still gets the events handled at the start of the slot, so the option adds a way, not a change."""

import unittest

import _bootstrap  # noqa: F401

import cpp_system
import dal
import test_cpp_compile as tcc
from dal import harness, ir
from dal.cppbackend import generate_cpp
from dal.interp import Interpreter
from dal.mockhost import MockHost
from dal.pybackend import generate_python, load_class

SY = """
logic Sy : actor slot_events sync_events in after_movement;
input Vel: vec3 <- host.velocity;
input Pre: vec3 <- host.pre_penetration_velocity;
state Stance: int;
state Seen: float;
state Count: int;
state Block: int;
on crouch_started { Stance = 1; Seen = Vel.x; Count += 1; host.set_max_walk_speed(Seen + 1000.0); }
on crouch_ended { Stance = 0; Count += 1; }
update(dt) in pre_movement { Block += 1; }
update(dt) in after_movement { Block += 10; host.set_max_walk_speed(Pre.x); }
"""
AN = """
logic An : anim sync_events;
state Jumps: int;
on jumped { Jumps += 1; }
update(dt) { }
"""


def module(src=SY, name="Sy.dal"):
    return dal.check_source(src, name).module


def warnings(src):
    return [d.code for d in dal.check_source(src, "t.dal").warnings]


class SyncEventsCheckTests(unittest.TestCase):
    def test_the_module_records_it(self):
        m = module()
        self.assertTrue(m.sync_events)
        self.assertIn("sync_events", ir.dump(m))
        self.assertIn("DAL_SYNC_EVENTS = True", generate_python(m))
        self.assertFalse(module("logic T : actor;\nstate A: int;\non jumped { A = 1; }\nupdate(dt) { }", "T.dal").sync_events)

    def test_an_animation_logic_may_have_it(self):
        self.assertTrue(module(AN, "An.dal").sync_events)

    def test_without_a_handler_it_does_nothing_and_says_so(self):
        self.assertEqual(warnings("logic T : actor sync_events;\nstate A: int;\nupdate(dt) { A += 1; }"), ["DAL5097"])
        self.assertEqual(warnings("logic T : actor sync_events;\nstate A: int;\non jumped { A += 1; }\nupdate(dt) { }"), [])

    def test_consume_on_read_inputs_are_left_to_the_update_blocks(self):
        # the handlers sample with a slot that consumes nothing: the blocks that read the pre-penetration velocity get it
        self.assertEqual(module().consume_slots(), ("after_movement",))


class SyncEventsSemanticsTests(unittest.TestCase):
    def objects(self, backend, src=SY, name="Sy.dal"):
        m = module(src, name)
        host = MockHost()
        obj = load_class(m, generate_python(m))(host) if backend == "python" else Interpreter(m, host)
        return obj, host

    def test_the_event_is_handled_when_it_arrives(self):
        for backend in ("python", "interp"):
            obj, host = self.objects(backend)
            host.velocity = (300.0, 0.0, 0.0)
            obj.update_phase("pre_movement", 0.1)
            host.velocity = (120.0, 0.0, 0.0)                      # the movement component moved the character in the meantime
            obj.receive_event(("crouch_started",))
            snap = obj.snapshot()
            self.assertEqual((snap["Stance"], snap["Seen"], snap["Count"]), (1, 120.0, 1), backend)   # handled now, with the velocity of now
            self.assertEqual(host.calls[-1][0], "set_max_walk_speed", backend)
            self.assertEqual(host.calls[-1][1][0], 1120.0, backend)                                  # and its command is out already
            obj.update_phase("after_movement", 0.1)
            self.assertEqual(obj.snapshot()["Count"], 1, backend)                                    # (not handled a second time at the slot)

    def test_a_host_that_polls_still_gets_its_events_handled_at_the_slot(self):
        for backend in ("python", "interp"):
            obj, host = self.objects(backend)
            host.velocity = (50.0, 0.0, 0.0)
            host.events.append(("crouch_started",))
            obj.update_phase("pre_movement", 0.1)
            self.assertEqual(obj.snapshot()["Stance"], 1, backend)

    def test_the_update_blocks_still_get_the_consume_on_read_inputs(self):
        for backend in ("python", "interp"):
            obj, host = self.objects(backend)
            host.pre_penetration_velocity = (77.0, 0.0, 0.0)
            host.pre_penetration_velocity_valid = True
            obj.update_phase("pre_movement", 0.1)
            obj.receive_event(("crouch_ended",))                  # an event between the slots must not take what only after_movement reads
            obj.update_phase("after_movement", 0.1)
            self.assertEqual(host.calls[-1][1][0], 77.0, backend)

    def test_the_animation_logic_handles_it_too(self):
        for backend in ("python", "interp"):
            obj, host = self.objects(backend, AN, "An.dal")
            obj.receive_event(("jumped",))
            self.assertEqual(obj.snapshot()["Jumps"], 1, backend)

    def test_all_the_implementations_agree_on_random_frames(self):
        mods = {"Sy": module()}
        schedule = [("Sy", "pre_movement"), ("Sy", "after_movement")]
        for seed in (1, 2):
            frames = harness.random_system_frames(mods, 30, seed)
            for k, f in enumerate(frames):
                f["events"] = []
                f["step_events"] = {1: [("crouch_started",) if k % 3 == 0 else ("crouch_ended",)]} if k % 2 == 0 else {}
            a = harness.run_system(mods, frames, "interp", schedule)
            b = harness.run_system(mods, frames, "python", schedule)
            for fa, fb in zip(a, b):
                for key in ("Stance", "Seen", "Count", "Block"):
                    self.assertEqual(fa["Sy"][key], fb["Sy"][key], (seed, key))
                self.assertEqual([c[0] for c in fa["Sy"]["__cmds__"]], [c[0] for c in fb["Sy"]["__cmds__"]])


@unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
class SyncEventsCppTests(unittest.TestCase):
    def test_generated_text(self):
        out = generate_cpp(module())
        cpp, hdr = out["Sy.cpp"], out["Sy.h"]
        self.assertIn("void USy::DalHandleEventNow(const DalHost::FEvent& DalEvent)", cpp)
        self.assertIn("DalHandleEventNow(DalEvent);", cpp.split("void USy::DalReceiveEvent")[1].split("\n}\n")[0])
        self.assertIn("bool bDalInEvent = false;", hdr)
        self.assertIn("DalSampleInputs(MAX_int32);", cpp)
        plain = generate_cpp(module("logic T : actor;\nstate A: int;\non jumped { A = 1; }\nupdate(dt) { }", "T.dal"))
        self.assertNotIn("DalHandleEventNow", plain["T.cpp"] + plain["T.h"])

    def test_the_router_hands_the_event_over_and_the_handler_runs_at_once(self):
        mods = {"Sy": module()}
        schedule = [("Sy", "pre_movement"), ("Sy", "after_movement")]
        for seed in (1, 3):
            frames = tcc.round_frames(harness.random_system_frames(mods, 10, seed))
            for k, f in enumerate(frames):
                f["events"] = []
                f["step_events"] = {1: [("crouch_started",) if k % 3 == 0 else ("crouch_ended",)]} if k % 2 == 0 else {}
            cpp, output = cpp_system.build_and_run_system(mods, frames, schedule)
            if tcc.COMPILER[0] == "msvc":
                self.assertNotIn("warning", output.lower(), output)
            py = harness.run_system(mods, frames, "python", schedule)
            for i, (a, b) in enumerate(zip(py, cpp)):
                for key in ("Stance", "Count", "Block"):
                    self.assertEqual(a["Sy"][key], b["Sy"][key], (seed, i, key))
                self.assertAlmostEqual(a["Sy"]["Seen"], b["Sy"]["Seen"], places=3, msg=(seed, i))
                self.assertEqual([e[0] for e in tcc.py_effects(a["Sy"]["__cmds__"])], [c[0] for c in b["Sy"]["__cmds__"]], (seed, i))


if __name__ == "__main__":
    unittest.main()
