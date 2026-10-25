"""`flush_peers` (actor logic): the host commands that a peer queued in the calls of a block run when the calling logic hands its own over, in the tick
slot of the caller, not at the end of the peer's own next block.  Without the option nothing changes (a peer's commands wait for its own block).

The interpreter, the generated Python and the generated C++ (against the stand-in engine) agree."""

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

# The character has a block only after the movement; the input calls it before the movement.  Where the character's commands run is the question.
CHR = """
logic Chr : actor in after_movement;
state N: int;
pub method Jump() { host.jump(); N += 1; }
pub method Walk(v: float) { host.set_max_walk_speed(v); }
pub method Leave() -> int { N += 10; return N; }
update(dt) in after_movement { host.crouch(); }
"""
INP = """
peer Chr: Chr;
logic Inp : actor before Chr{options} in pre_movement;
state M: int;
state Got: int;
update(dt) {
    host.stop_jumping();
    Chr.Jump();
    host.uncrouch();
    Chr.Walk(300.0);
    Got = Chr.Leave();
    Chr.Jump();
    M += 1;
}
"""
ALONE = """
peer Chr: Chr;
logic Inp : actor flush_peers in pre_movement;
state M: int;
update(dt) { Chr.Jump(); M += 1; }
"""


def files(options=""):
    return {"Chr.dal": CHR, "Inp.dal": INP.replace("{options}", options)}


def system(options="", names=("Chr.dal", "Inp.dal")):
    f = files(options)
    return {n[:-4]: dal.check_source(f[n], n, files=f).module for n in names}


def errors(src, extra=None):
    try:
        dal.check_source(src, "t.dal", files=extra or {})
    except dal.DalError as e:
        return [(d.code, d.pos.line, d.pos.col) for d in e.diagnostics if d.severity == "error"]
    return []


def warnings(src, extra=None):
    return [d.code for d in dal.check_source(src, "t.dal", files=extra or {}).warnings]


def names(calls):
    return [c[0] for c in calls]


class FlushPeersCheckTests(unittest.TestCase):
    def test_the_option_belongs_to_actor_logic(self):
        self.assertEqual(errors("logic T : anim flush_peers;\nupdate(dt) { }")[0][0], "DAL2026")
        self.assertEqual(errors("logic T : actor flush_peers in pre_movement;\nupdate(dt) { }"), [])

    def test_it_goes_with_the_other_options_in_any_order(self):
        base = {"Chr.dal": CHR}
        for opts in (" flush_peers", " flush_peers slot_events", " slot_events flush_peers", " before Chr flush_peers"):
            src = "peer Chr: Chr;\nlogic Inp : actor%s in pre_movement;\nupdate(dt) { Chr.Jump(); }" % opts
            self.assertEqual(errors(src, base), [], opts)

    def test_without_an_actor_peer_it_does_nothing_and_says_so(self):
        self.assertEqual(warnings("logic T : actor flush_peers in pre_movement;\nupdate(dt) { }"), ["DAL5096"])
        self.assertEqual(warnings("peer Chr: Chr;\nlogic T : actor flush_peers in pre_movement;\nupdate(dt) { Chr.Jump(); }", {"Chr.dal": CHR}), [])

    def test_the_module_records_it(self):
        mods = system(" flush_peers")
        self.assertTrue(mods["Inp"].flush_peers)
        self.assertFalse(system()["Inp"].flush_peers)
        self.assertIn("flush_peers", ir.dump(mods["Inp"]))
        self.assertIn("DAL_FLUSH_PEERS = True", generate_python(mods["Inp"]))
        self.assertNotIn("DAL_FLUSH_PEERS", generate_python(system()["Inp"]))

    def test_a_logic_without_commands_of_its_own_still_hands_over(self):
        mods = {"Chr": dal.check_source(CHR, "Chr.dal").module, "Inp": dal.check_source(ALONE, "Inp.dal", files={"Chr.dal": CHR}).module}
        self.assertTrue(mods["Inp"].uses_commands)


class FlushPeersSemanticsTests(unittest.TestCase):
    def drive(self, mods, backend):
        """One tick on a shared host: the commands after the pre_movement slot, then the ones after the after_movement slot."""
        host = MockHost()
        objs = {n: (load_class(m, generate_python(m))(host) if backend == "python" else Interpreter(m, host)) for n, m in mods.items()}
        for n, m in mods.items():
            for p in m.peers:
                objs[n].connect(p.name, objs[p.logic])
        seen = []
        for n, slot in harness.default_schedule(mods):
            objs[n].update_phase(slot, 0.1)
            seen.append((n, slot, list(host.calls)))
        return seen, objs

    def test_without_the_option_the_peers_commands_wait_for_its_own_block(self):
        for backend in ("python", "interp"):
            seen, objs = self.drive(system(), backend)
            (_i, _s, after_input), (_c, _s2, after_character) = seen
            self.assertEqual(names(after_input), ["stop_jumping", "uncrouch"], backend)                       # the input's own commands, nothing of the character's
            self.assertEqual(names(after_character), ["stop_jumping", "uncrouch", "jump", "set_max_walk_speed", "jump", "crouch"], backend)

    def test_with_the_option_they_run_in_the_slot_of_the_caller_after_its_own(self):
        for backend in ("python", "interp"):
            seen, objs = self.drive(system(" flush_peers"), backend)
            (_i, _s, after_input), (_c, _s2, after_character) = seen
            self.assertEqual(names(after_input), ["stop_jumping", "uncrouch", "jump", "set_max_walk_speed", "jump"], backend)    # own commands, then the peer's in call order
            self.assertEqual(names(after_character), names(after_input) + ["crouch"], backend)                                 # the character's own block adds only its own
            self.assertEqual(objs["Inp"].snapshot()["Got"], 11, backend)                                                       # (a returned value is the same as ever)

    def test_the_option_changes_when_not_what(self):
        for backend in ("python", "interp"):
            plain = self.drive(system(), backend)[0][-1][2]
            flushed = self.drive(system(" flush_peers"), backend)[0][-1][2]
            self.assertEqual(sorted(map(repr, plain)), sorted(map(repr, flushed)), backend)

    def test_each_peer_is_handed_over_once_and_the_queue_is_empty_afterwards(self):
        for backend in ("python", "interp"):
            mods = system(" flush_peers")
            host = MockHost()
            objs = {n: (load_class(m, generate_python(m))(host) if backend == "python" else Interpreter(m, host)) for n, m in mods.items()}
            objs["Inp"].connect("Chr", objs["Chr"])
            objs["Inp"].update_phase("pre_movement", 0.1)
            first = len(host.calls)
            objs["Inp"].update_phase("pre_movement", 0.1)
            self.assertEqual(len(host.calls), 2 * first, backend)                                                                # no command repeated, none lost
            objs["Chr"].update_phase("after_movement", 0.1)
            self.assertEqual(names(host.calls)[-1], "crouch", backend)

    def test_take_commands_of_the_caller_includes_the_peers(self):
        mods = system(" flush_peers")
        host = MockHost()
        objs = {n: load_class(m, generate_python(m))(host) for n, m in mods.items()}
        objs["Inp"].connect("Chr", objs["Chr"])
        objs["Inp"].update_phase("pre_movement", 0.1)
        taken = objs["Inp"].take_commands()                          # a host that executes the commands itself takes them instead of the generated flush
        self.assertEqual(names(taken), [])                           # (the block's flush handed them over already)
        objs["Inp"]._peer_call("Chr", "Jump", (), None, False)
        taken = objs["Inp"].take_commands()
        self.assertEqual(names(taken), ["jump"])
        self.assertEqual(objs["Chr"].take_commands(), [])            # ... and the peer has nothing left

    def test_all_the_implementations_agree_on_random_frames(self):
        for options in ("", " flush_peers"):
            mods = system(options)
            for seed in (1, 2):
                frames = harness.random_system_frames(mods, 40, seed)
                a = harness.run_system(mods, frames, "interp")
                b = harness.run_system(mods, frames, "python")
                for fa, fb in zip(a, b):
                    for n in mods:
                        self.assertEqual(sorted(k for k in fa[n] if not k.startswith("__")), sorted(k for k in fb[n] if not k.startswith("__")))
                        self.assertEqual(names(fa[n].get("__cmds__", [])), names(fb[n].get("__cmds__", [])), (options, n))


@unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
class FlushPeersCppTests(unittest.TestCase):
    def test_generated_text(self):
        inp = generate_cpp(system(" flush_peers")["Inp"])
        chr_ = generate_cpp(system()["Chr"])
        self.assertIn("DalHost::FlushPeerCommands(Chr.Get(), 0);", inp["Inp.cpp"])
        self.assertIn("void DalFlushCommands();", chr_["Chr.h"].split("public:")[1])                       # the peer's flush is public (it has pub methods)
        self.assertNotIn("FlushPeerCommands", generate_cpp(system()["Inp"])["Inp.cpp"])
        self.assertIn("FlushPeerCommands", (Path(__file__).resolve().parents[1] / "dal" / "cpp" / "DalHost.h").read_text(encoding="utf-8"))

    def test_the_commands_of_the_called_peer_run_in_the_step_of_the_caller(self):
        for options in ("", " flush_peers"):
            mods = system(options)
            schedule = harness.default_schedule(mods)
            frames = tcc.round_frames(harness.random_system_frames(mods, 6, 3))
            cpp, output = cpp_system.build_and_run_system(mods, frames, schedule)
            if tcc.COMPILER[0] == "msvc":
                self.assertNotIn("warning", output.lower(), output)
            py = harness.run_system(mods, frames, "python", schedule)
            for i, (a, b) in enumerate(zip(py, cpp)):
                in_py = sorted(e[0] for n in mods for e in tcc.py_effects(a[n]["__cmds__"]))
                in_cpp = sorted(e[0] for n in mods for e in b[n]["__cmds__"])
                self.assertEqual(in_py, in_cpp, (options, i))                                                       # the same commands in the frame, whoever ran them
                inp_step = [x[0] for x in b["Inp"]["__cmds__"]]
                if options:
                    self.assertEqual(inp_step, ["stop_jumping", "uncrouch", "jump", "set_max_walk_speed", "jump"], (options, i))   # the peer's commands ran in the input's step, after its own
                else:
                    self.assertEqual(inp_step, ["stop_jumping", "uncrouch"], (options, i))

    def test_a_caller_without_commands_of_its_own_hands_the_peers_over_too(self):
        files_ = {"Chr.dal": CHR, "Inp.dal": ALONE}
        mods = {n[:-4]: dal.check_source(files_[n], n, files=files_).module for n in ("Chr.dal", "Inp.dal")}
        schedule = harness.default_schedule(mods)
        frames = tcc.round_frames(harness.random_system_frames(mods, 4, 1))
        cpp, output = cpp_system.build_and_run_system(mods, frames, schedule)
        if tcc.COMPILER[0] == "msvc":
            self.assertNotIn("warning", output.lower(), output)
        self.assertEqual([c[0] for c in cpp[0]["Inp"]["__cmds__"]], ["jump"])


if __name__ == "__main__":
    unittest.main()
