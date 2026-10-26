"""Hook slots (`update(dt) in physics_walking`): a tick slot that the movement component of the host runs itself, at the start of its walking
physics, instead of the engine's tick running it in a fixed place.

The language part: the slot is accepted, comes last, is not part of `update(dt)` and takes no part in the `before` / `after` order.  The semantics: the block
sees the state of the step it runs in (the events raised so far are handled first with `slot_events`, the inputs are sampled when it starts) and hands its
commands over when it ends.  The interpreter, the generated Python and the generated C++ (against the stand-in engine) agree, the C++ runs the slot through
DalHost::FEventRouter::RunHook like the movement component of DalRuntime does."""

import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

import cpp_system
import dal
import test_cpp_compile as tcc
from dal import harness, ir
from dal.cppbackend import generate_cpp
from dal.interp import Interpreter
from dal.mockhost import MockHost
from dal.pybackend import generate_python, load_class

HK = """
logic Hk : actor slot_events in after_movement;
input Vel: vec3 <- host.velocity;
state Stance: int;
state Walked: float;
state Ticks: int;
state HookRan: bool;
on crouch_started { Stance = 1; }
on crouch_ended { Stance = 0; }
update(dt) in pre_movement { Ticks += 1; }
update(dt) in after_movement {
    if (!HookRan) {
        Walked = -1.0;
        host.set_max_walk_speed(-1.0);
    }
    HookRan = false;
}
update(dt) in physics_walking {
    Walked = Vel.x + Stance * 1000.0;
    HookRan = true;
    host.set_max_walk_speed(Walked);
}
"""
PEER = """
peer Hk: Hk;
logic Chr : actor before Hk in physics_walking;
state N: int;
update(dt) in physics_walking { N += 1; }
"""
SCHEDULE = [("Hk", "pre_movement"), ("Hk", "physics_walking"), ("Hk", "after_movement")]


def errors(src, extra=None):
    try:
        dal.check_source(src, "t.dal", files=extra or {})
    except dal.DalError as e:
        return [(d.code, d.pos.line, d.pos.col) for d in e.diagnostics if d.severity == "error"]
    return []


def warnings(src, extra=None):
    return [d.code for d in dal.check_source(src, "t.dal", files=extra or {}).warnings]


def module(src=HK):
    return dal.check_source(src, "Hk.dal").module


def with_hook(frames):
    """The frames with a crouch / stand event that arrives before the hook step of every other frame (the movement component raised it)."""
    for k, f in enumerate(frames):
        f["events"] = []                                             # (the random hooks of the harness arrive at the start of a tick, which the C++ driver does not do)
        f["step_events"] = {1: [("crouch_started",) if k % 3 == 0 else ("crouch_ended",)]} if k % 2 == 0 else {}
    return frames


class HookSlotCheckTests(unittest.TestCase):
    def test_the_slot_is_accepted_and_comes_last(self):
        m = module()
        self.assertEqual([u.phase for u in m.updates], ["pre_movement", "after_movement", "physics_walking"])
        self.assertEqual(ir.HOOK_SLOTS, ("physics_walking", "prepare_root_motion"))

    def test_written_first_it_is_still_last(self):
        src = "logic T : actor;\nstate A: int;\nupdate(dt) in physics_walking { A = 2; }\nupdate(dt) in pre_movement { A = 1; }"
        self.assertEqual([u.phase for u in module(src).updates], ["pre_movement", "physics_walking"])

    def test_an_animation_logic_has_no_slots(self):
        self.assertEqual(errors("logic M;\nupdate(dt) in physics_walking { }")[0][0], "DAL5064")

    def test_two_blocks_in_the_slot_are_refused(self):
        self.assertEqual(errors("logic T : actor;\nupdate(dt) in physics_walking { }\nupdate(dt) in physics_walking { }")[0][0], "DAL5049")

    def test_a_hook_slot_is_not_ordered_against_the_peers(self):
        # Chr has a block in the hook slot only: `before Hk` has no tick slot in common with Hk to order (the movement component calls the logics in turn)
        self.assertEqual(warnings(PEER, {"Hk.dal": HK}), ["DAL5095"])

    def test_the_python_class_lists_it(self):
        src = generate_python(module())
        self.assertIn("DAL_PHASES = ('pre_movement', 'after_movement', 'physics_walking', )", src)


class HookSlotSemanticsTests(unittest.TestCase):
    def objects(self, backend):
        m = module()
        host = MockHost()
        obj = load_class(m, generate_python(m))(host) if backend == "python" else Interpreter(m, host)
        return obj, host

    def test_update_does_not_run_it(self):
        for backend in ("python", "interp"):
            obj, host = self.objects(backend)
            obj.update(0.1)
            snap = obj.snapshot()
            self.assertFalse(snap["HookRan"], backend)
            self.assertEqual((snap["Ticks"], snap["Walked"]), (1, -1.0), backend)       # (after_movement ran without it, as on a host without the slot)

    def test_the_slot_runs_when_the_host_asks_and_flushes_its_commands(self):
        for backend in ("python", "interp"):
            obj, host = self.objects(backend)
            host.velocity = (250.0, 0.0, 0.0)
            obj.update_phase("pre_movement", 0.1)
            obj.update_phase("physics_walking", 0.1)
            self.assertEqual(host.calls[-1][0], "set_max_walk_speed", backend)
            self.assertEqual(host.calls[-1][1][0], 250.0, backend)
            obj.update_phase("after_movement", 0.1)
            snap = obj.snapshot()
            self.assertEqual((snap["Walked"], snap["HookRan"]), (250.0, False), backend)   # after_movement saw the flag the hook set, and cleared it

    def test_the_events_of_the_step_are_handled_before_the_block(self):
        for backend in ("python", "interp"):
            obj, host = self.objects(backend)
            host.velocity = (100.0, 0.0, 0.0)
            obj.update_phase("pre_movement", 0.1)
            host.events.append(("crouch_started",))                  # the movement component raised it while it moved
            obj.update_phase("physics_walking", 0.1)
            self.assertEqual(obj.snapshot()["Walked"], 1100.0, backend)    # slot_events: handled at the start of this slot, not of after_movement

    def test_all_the_implementations_agree_on_random_frames(self):
        mods = {"Hk": module()}
        for seed in (1, 2):
            frames = with_hook(harness.random_system_frames(mods, 40, seed))
            a = harness.run_system(mods, frames, "interp", SCHEDULE)
            b = harness.run_system(mods, frames, "python", SCHEDULE)
            self.assertEqual(len(a), len(b))
            for fa, fb in zip(a, b):
                self.assertEqual(sorted(k for k in fa["Hk"] if not k.startswith("__")), sorted(k for k in fb["Hk"] if not k.startswith("__")))
                self.assertEqual(fa["Hk"]["Walked"], fb["Hk"]["Walked"])
                self.assertEqual([c[0] for c in fa["Hk"]["__cmds__"]], [c[0] for c in fb["Hk"]["__cmds__"]])


@unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
class HookSlotCppTests(unittest.TestCase):
    def test_generated_text(self):
        out = generate_cpp(module())
        cpp, hdr = out["Hk.cpp"], out["Hk.h"]
        self.assertIn("public DalHost::IEventSink", hdr)
        self.assertIn("DalRunHook", hdr)
        self.assertIn('if (DalHook == FName(TEXT("physics_walking")))', cpp)
        self.assertIn("DalRunPhase(2, DeltaSeconds);", cpp)
        self.assertIn("DalPhaseTicks[2].bCanEverTick = false;", cpp)                     # no tick function: the movement component runs it
        self.assertIn("DalPhaseTicks[0].bCanEverTick = true;", cpp)
        self.assertNotIn("DalPhaseTicks[2].AddPrerequisite", cpp)
        self.assertNotIn("this, DalPhaseTicks[2])", cpp)
        root = Path(__file__).resolve().parents[1]
        for host_h in (root / "dal" / "cpp" / "DalHost.h", root / "Unreal" / "DalRuntime" / "Source" / "DalRuntime" / "Public" / "DalHost.h"):
            text = host_h.read_text(encoding="utf-8")
            self.assertIn("virtual void DalRunHook(const FName Hook, const float DeltaTime)", text)
            self.assertIn("static void RunHook(const AActor* Owner, const FName Hook, const float DeltaTime)", text)

    def test_a_logic_without_a_hook_slot_has_none_of_it(self):
        m = dal.check_source("logic T : actor;\nstate A: int;\nupdate(dt) in pre_movement { A += 1; }\nupdate(dt) in after_movement { A += 2; }", "T.dal").module
        out = generate_cpp(m)
        self.assertNotIn("DalRunHook", out["T.cpp"] + out["T.h"])

    def test_the_movement_component_runs_the_slot_like_the_python_classes_do(self):
        mods = {"Hk": module()}
        for seed in (1, 4):
            frames = tcc.round_frames(with_hook(harness.random_system_frames(mods, 12, seed)))
            cpp, output = cpp_system.build_and_run_system(mods, frames, SCHEDULE)
            if tcc.COMPILER[0] == "msvc":
                self.assertNotIn("warning", output.lower(), output)
            py = harness.run_system(mods, frames, "python", SCHEDULE)
            for i, (a, b) in enumerate(zip(py, cpp)):
                self.assertAlmostEqual(a["Hk"]["Walked"], b["Hk"]["Walked"], places=3, msg=(seed, i))
                self.assertEqual(a["Hk"]["Stance"], b["Hk"]["Stance"], (seed, i))
                self.assertEqual([e[0] for e in tcc.py_effects(a["Hk"]["__cmds__"])], [c[0] for c in b["Hk"]["__cmds__"]], (seed, i))


if __name__ == "__main__":
    unittest.main()
