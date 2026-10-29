"""`flush_calls` (actor logic): the host commands that a `pub` method queues run when a call from outside (game code, Blueprint, a Python driver) ends, not at the
end of the logic's next tick slot.  A call from a block of the logic (an update, a handler, an entry, another method) does not flush: it is part of the block.

Why: AAlsCharacter::StartRagdolling / StopRagdolling act at once -- the mesh turns to physics between two frames, before the frame's tick.  Run in the next tick
(the generated logic's default) the ragdoll started a frame part later in the tick and Chaos ended in another pose (anim-ragdoll.md section 13).

Without the option nothing changes (the generated Python and C++ of every program are the same as before).  The option is a C++ one: the Python backend and the
interpreter hand the commands to the host when the caller takes them, so they have no 'later'."""

import unittest

import _bootstrap  # noqa: F401

import cpp_driver
import dal
from dal import ir
from dal.cppbackend import generate_cpp
from dal.pybackend import generate_python

CHR = """
logic Chr : actor{options} in after_movement;
state N: int;
pub method Jump() { host.jump(); N += 1; }
pub method Walk(v: float) { host.set_max_walk_speed(v); }
pub method Both() { Jump(); Walk(7.0); }
pub view Count() -> int { return N; }
update(dt) in after_movement { host.crouch(); Jump(); }
"""

PHASED = """
logic Chr : actor{options} in after_movement;
state N: int;
pub method Jump() { host.jump(); N += 1; }
update(dt) in pre_movement { host.stop_jumping(); }
update(dt) in after_movement { host.crouch(); Jump(); }
"""


def module(src=CHR, options=""):
    return dal.check_source(src.replace("{options}", options), "Chr.dal").module


def errors(src):
    try:
        dal.check_source(src, "t.dal")
    except dal.DalError as e:
        return [d.code for d in e.diagnostics if d.severity == "error"]
    return []


def names(cmds):
    return [c[0] for c in cmds]


class FlushCallsCheckTests(unittest.TestCase):
    def test_the_option_belongs_to_actor_logic(self):
        self.assertEqual(errors("logic T : anim flush_calls Jump;\npub method Jump() { }\nupdate(dt) { }"), ["DAL2026"])
        self.assertEqual(errors(CHR.replace("{options}", " flush_calls Jump")), [])

    def test_it_names_pub_methods_only(self):
        self.assertEqual(errors(CHR.replace("{options}", " flush_calls Nothing")), ["DAL3080"])
        self.assertEqual(errors(CHR.replace("{options}", " flush_calls Count")), ["DAL3080"])                   # a view
        self.assertEqual(errors(CHR.replace("{options}", " flush_calls Jump, Walk, Both")), [])
        src = "logic T : actor flush_calls Hidden in after_movement;\nmethod Hidden() { host.jump(); }\nupdate(dt) { Hidden(); }"
        self.assertEqual(errors(src), ["DAL3080"])                                                              # not pub: nobody calls it from outside

    def test_it_goes_with_the_other_options_in_any_order(self):
        for options in (" flush_calls Jump slot_events", " slot_events flush_calls Jump, Walk", " sync_events flush_calls Jump slot_events"):
            self.assertEqual(errors(CHR.replace("{options}", options)), [], options)

    def test_the_module_records_it(self):
        mod = module(options=" flush_calls Jump, Walk")
        self.assertEqual(mod.flush_calls, ["Jump", "Walk"])
        self.assertTrue(mod.uses_commands)
        self.assertIn("flush_calls Jump, Walk", ir.dump(mod))
        self.assertEqual(module().flush_calls, [])
        self.assertNotIn("flush_calls", ir.dump(module()))

    def test_the_python_backend_ignores_it(self):
        def body(text):                                                       # (the header carries a hash of the source)
            return [line for line in text.splitlines() if "sha256" not in line.lower()]

        self.assertEqual(body(generate_python(module(options=" flush_calls Jump"))), body(generate_python(module())))


class FlushCallsTextTests(unittest.TestCase):
    def test_without_the_option_the_output_is_the_old_one(self):
        out = generate_cpp(module())
        for name, text in out.items():
            self.assertNotIn("DalCallScope", text, name)
            self.assertNotIn("DalCallDepth", text, name)

    def test_the_blocks_and_the_listed_methods_are_scoped(self):
        out = generate_cpp(module(options=" flush_calls Jump"))
        h, cpp = out["Chr.h"], out["Chr.cpp"]
        self.assertIn("struct FDalCallScope", h)
        self.assertIn("int32 DalCallDepth = 0;", h)
        body = cpp.split("void UChr::Jump()")[1].split("\n}\n")[0]
        self.assertIn("FDalCallScope DalCallScope(this, true);", body)                                         # a call from outside ends with the flush
        walk = cpp.split("void UChr::Walk(")[1].split("\n}\n")[0]
        self.assertNotIn("DalCallScope", walk)                                                                  # not listed: as before
        run = cpp.split("void UChr::DalRunPhase(")[1].split("\n}\n")[0] if "DalRunPhase" in cpp else cpp.split("void UChr::TickComponent(")[1].split("\n}\n")[0]
        self.assertIn("FDalCallScope DalCallScope(this, false);", run)                                          # a block counts as running
        self.assertEqual(cpp.count("DalCallScope(this, true)"), 1)

    def test_the_als_character_lists_the_ragdoll_calls(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "als" / "AlsCharacter.dal").read_text(encoding="utf-8")
        self.assertIn("flush_calls StartRagdolling, StopRagdolling", src.split("\n")[55])


@unittest.skipIf(cpp_driver.find_compiler() is None, "no C++ compiler found")
class FlushCallsCppTests(unittest.TestCase):
    def run_frames(self, mod, frames):
        states, output = cpp_driver.build_and_run(mod, frames, warnings_as_errors=True)
        return [names(s["__cmds__"]) for s in states], output

    def test_the_commands_of_a_call_from_outside_run_when_the_call_ends(self):
        frames = [{"dt": 0.1, "mark": True, "calls": ["Jump", ("Walk", {"v": 5.0})]}, {"dt": 0.1}]
        plain, _ = self.run_frames(module(), frames)
        self.assertEqual(plain[0], ["crouch", "jump", "mark_updated"])                                       # the update's own commands; the calls' wait for the next tick
        self.assertEqual(plain[1], ["jump", "set_max_walk_speed", "crouch", "jump"])
        flushed, output = self.run_frames(module(options=" flush_calls Jump"), frames)
        self.assertEqual(flushed[0], ["crouch", "jump", "mark_updated", "jump"])                              # Jump: at the end of the call
        self.assertEqual(flushed[1], ["set_max_walk_speed", "crouch", "jump"])                                # Walk is not listed: as before
        self.assertNotIn("warning", output.lower(), output)

    def test_a_call_from_a_block_does_not_flush_in_the_middle(self):
        """The update calls Jump() after host.crouch(): the commands of the block stay in the order the block gave them, and run once at its end."""
        frames = [{"dt": 0.1}, {"dt": 0.1}]
        plain, _ = self.run_frames(module(), frames)
        flushed, _ = self.run_frames(module(options=" flush_calls Jump"), frames)
        self.assertEqual(flushed, plain)
        self.assertEqual(flushed[0], ["crouch", "jump"])

    def test_nested_calls_hand_over_once_at_the_end_of_the_outer_call(self):
        frames = [{"dt": 0.1, "mark": True, "calls": ["Both"]}]
        flushed, _ = self.run_frames(module(options=" flush_calls Jump, Both"), frames)
        self.assertEqual(flushed[0], ["crouch", "jump", "mark_updated", "jump", "set_max_walk_speed"])       # Both() -> Jump() (nested: no flush) + Walk(): one flush, in order

    def test_tick_slots(self):
        """A logic with two tick slots: the slots are blocks, the call from outside is not."""
        frames = [{"dt": 0.1, "mark": True, "calls": ["Jump"]}]
        flushed, _ = self.run_frames(module(PHASED, " flush_calls Jump"), frames)
        plain, _ = self.run_frames(module(PHASED), frames)
        self.assertEqual(plain[0], ["stop_jumping", "crouch", "jump", "mark_updated"])
        self.assertEqual(flushed[0], ["stop_jumping", "crouch", "jump", "mark_updated", "jump"])


if __name__ == "__main__":
    unittest.main()
