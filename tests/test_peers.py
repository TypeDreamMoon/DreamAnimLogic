"""Peers (`peer X: Logic;`, `input v <- X.State`, `X.Method()`) and update blocks in tick slots."""

import os
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401

import dal
from dal import harness, ir
from dal.interp import Interpreter
from dal.pybackend import generate_python, load_class
from peer_sources import ANIM, CHARACTER, FILES, INPUT, LIB


def check(name, files=None):
    f = files if files is not None else FILES
    return dal.check_source(f[name], name, files=f).module


def errors(src, files, name="main.dal"):
    fs = dict(files)
    fs[name] = src
    try:
        dal.check_source(src, name, files=fs)
    except dal.DalError as e:
        return [(d.code, d.filename, d.pos.line, d.pos.col) for d in e.diagnostics if d.severity == "error"]
    return []


def system():
    return {n[:-4]: check(n) for n in ("AlsCharacter.dal", "AlsInput.dal", "AlsAnim.dal")}


class PeerCheckTests(unittest.TestCase):
    def test_ir_of_the_system(self):
        mods = system()
        char, inp, anim = mods["AlsCharacter"], mods["AlsInput"], mods["AlsAnim"]
        self.assertEqual([(p.name, p.logic, p.kind) for p in char.peers], [("Anim", "AlsAnim", "anim")])
        self.assertEqual([(p.name, p.logic, p.kind) for p in inp.peers], [("Character", "AlsCharacter", "actor")])
        self.assertEqual(sorted(inp.peers[0].methods), ["Jump", "SetDesiredGait", "SetStance"])
        loco = anim.find_input("CharLocomotion")
        self.assertEqual((loco.peer.peer, loco.peer.path, loco.host), ("Character", ("Locomotion",), None))
        self.assertEqual(anim.find_input("CharSpeed").peer.path, ("Locomotion", "Speed"))
        self.assertFalse(anim.find_input("CharSpeed").is_free)
        self.assertTrue(anim.find_input("MaxAccel").host is not None)
        # the call on the animation instance is queued, the one between actor logics is not
        calls = [e for _l, body in char.bodies() for e in ir.walk_stmt_exprs(body) if isinstance(e, ir.CallPeer)]
        self.assertEqual([(c.peer, c.method, c.queued) for c in calls], [("Anim", "Notify", True)])
        calls = [e for _l, body in inp.bodies() for e in ir.walk_stmt_exprs(body) if isinstance(e, ir.CallPeer)]
        self.assertEqual([(c.method, c.queued) for c in calls], [("SetDesiredGait", False), ("SetStance", False), ("Jump", False)])

    def test_tick_slots(self):
        char, inp, anim = system()["AlsCharacter"], system()["AlsInput"], system()["AlsAnim"]
        self.assertEqual([u.phase for u in char.updates], ["after_movement"])
        self.assertTrue(char.phases_explicit)
        self.assertEqual([u.phase for u in inp.updates], ["pre_movement"])
        self.assertEqual([u.phase for u in anim.updates], ["pre_movement"])
        self.assertFalse(anim.phases_explicit)
        self.assertIn("update(dt) in after_movement", ir.dump(char))

    def test_missing_and_wrong_peers(self):
        base = {"als_lib.dal": LIB}
        self.assertEqual(errors('use "als_lib.dal";\npeer X: Nowhere;\nlogic M : actor;\nupdate(dt) {}', base)[0][:2], ("DAL3072", "main.dal"))
        wrong = dict(base, **{"Other.dal": "logic Different;\nupdate(dt) {}"})
        self.assertEqual(errors('peer X: Other;\nlogic M : actor;\nupdate(dt) {}', wrong)[0][0], "DAL3072")
        # an error in the peer's declarations is reported in the peer's file
        broken = dict(base, **{"Broken.dal": "logic Broken;\nstate S: Missing;\nupdate(dt) {}"})
        e = errors('peer B: Broken;\nlogic M : actor;\nupdate(dt) {}', broken)
        self.assertEqual(e[0][:2], ("DAL3041", "Broken.dal"))

    def test_peer_bindings(self):
        pre = 'use "als_lib.dal";\npeer Character: AlsCharacter;\nlogic M;\n'
        for line, code in (("input A: float <- Character.Nope;", "DAL3075"),
                           ("input A: float <- Character.Locomotion.Nope;", "DAL4030"),
                           ("input A: float <- Character.Locomotion.Speed.x;", "DAL4030"),
                           ("input A: bool <- Character.Locomotion.Speed;", "DAL4020"),
                           ("input A: float <- Elsewhere.State;", "DAL3040"),
                           ("input A: float <- Character;", "DAL2001"),
                           ("input A: float <- Character.Locomotion.Speed;\nstate B: float;\nupdate(dt) { Character = 1.0; }", "DAL5001")):
            with self.subTest(line):
                self.assertEqual(errors(pre + line, FILES)[0][0], code)
        self.assertEqual(errors(pre + "state S: float;\ninput A: float <- S.x;", FILES)[0][0], "DAL3076")

    def test_calls_on_peers(self):
        pre = 'use "als_lib.dal";\npeer Character: AlsCharacter;\nlogic M : actor;\nstate R: bool;\n'
        for body, code in (("Character.Nope();", "DAL3077"),
                           ("Character.SetDesiredGait();", "DAL4041"),
                           ("Character.SetDesiredGait(1.0);", "DAL4002"),
                           ("Character.SetDesiredGait(AlsGait.Running, 2);", "DAL4041"),
                           ("let x = Character.Jump() && true;", "DAL4042"),
                           ("Character.Jump(1);", "DAL4041"),
                           ("let x = Character.SetStance(AlsStance.Standing);", "DAL4043"),
                           ("R = Character.can_sprint();", "DAL3077")):       # a view is read through an input
            with self.subTest(body):
                self.assertEqual(errors(pre + "update(dt) { " + body + " }", FILES)[0][0], code)
        ok = errors(pre + "update(dt) { Character.SetStance(AlsStance.Standing); let j = Character.Jump(); R = j; var k = false; k = Character.Jump(); }", FILES)
        self.assertEqual(ok, [])

    def test_threading_rules(self):
        anim_calls_actor = 'use "als_lib.dal";\npeer Character: AlsCharacter;\nlogic A;\nupdate(dt) { Character.SetStance(AlsStance.Standing); }'
        self.assertEqual(errors(anim_calls_actor, FILES)[0][0], "DAL5045")
        actor_reads_anim = 'use "als_lib.dal";\npeer Anim: AlsAnim;\nlogic A : actor;\ninput N: int <- Anim.Notified;\nupdate(dt) {}'
        self.assertEqual(errors(actor_reads_anim, FILES)[0][0], "DAL5062")
        anim_with_anim = 'peer Other: AlsAnim;\nlogic A;\nupdate(dt) {}'
        self.assertEqual(errors(anim_with_anim, FILES)[0][0], "DAL5045")
        queued_value = dict(FILES, **{"AnimWithResult.dal": "logic AnimWithResult;\nstate N: int;\npub method Count() -> int { N += 1; return N; }\nupdate(dt) {}"})
        src = 'peer A: AnimWithResult;\nlogic M : actor;\nstate R: int;\nupdate(dt) { R = A.Count(); }'
        self.assertEqual(errors(src, queued_value)[0][0], "DAL5063")
        self.assertEqual(errors('peer A: AnimWithResult;\nlogic M : actor;\nupdate(dt) { A.Count(); }', queued_value), [])

    def test_views_and_functions_do_not_reach_peers(self):
        pre = 'use "als_lib.dal";\npeer Character: AlsCharacter;\nlogic M : actor;\n'
        self.assertEqual(errors(pre + "view v() -> bool { return Character.Jump(); }\nupdate(dt) {}", FILES)[0][0], "DAL5044")
        self.assertEqual(errors(pre + "fn f() -> bool { return Character.Jump(); }\nupdate(dt) {}", FILES)[0][0], "DAL5012")

    def test_tick_slot_errors(self):
        pre = "logic M : actor;\nstate S: float;\n"
        self.assertEqual(errors(pre + "update(dt) in after_movement { S = 1.0; }\nupdate(dt) in after_movement { S = 2.0; }", {})[0][0], "DAL5049")
        self.assertEqual(errors("logic M : actor in after_movement;\nupdate(dt) { }\nupdate(dt) in after_movement { }", {})[0][0], "DAL5049")
        self.assertEqual(errors("logic M : actor;\nupdate(dt) { }\nupdate(dt) { }", {})[0][0], "DAL3012")          # unchanged for plain programs
        self.assertEqual(errors("logic M;\nupdate(dt) in after_anim { }", {})[0][0], "DAL5064")
        self.assertEqual(errors("logic M in after_anim;\nupdate(dt) { }", {})[0][0], "DAL5064")
        with self.assertRaises(dal.DalError) as cm:
            dal.check_source("logic M : actor;\nupdate(dt) in later { }", "t.dal")
        self.assertEqual(cm.exception.first.code, "DAL2023")
        self.assertEqual(errors(pre + "update(dt) in pre_movement { S = 1.0; }\nupdate(dt) in after_anim { S = 2.0; }\nupdate(dt) in after_movement { S = 3.0; }", {}), [])
        m = dal.check_source(pre + "update(dt) in after_anim { S = 2.0; }\nupdate(dt) in pre_movement { S = 1.0; }", "t.dal").module
        self.assertEqual([u.phase for u in m.updates], ["pre_movement", "after_anim"])        # kept in slot order


class PeerRuntimeTests(unittest.TestCase):
    def build(self, backend):
        mods = system()
        hosts = {n: harness.ScriptedHost() for n in mods}
        objs = {}
        for n, m in mods.items():
            objs[n] = Interpreter(m, hosts[n]) if backend == "interp" else load_class(m)(hosts[n])
        for n, m in mods.items():
            for p in m.peers:
                objs[n].connect(p.name, objs[p.logic])
        return mods, hosts, objs

    def test_sync_calls_between_actor_logics_and_queued_calls_to_the_animation(self):
        for backend in ("interp", "python"):
            with self.subTest(backend):
                mods, hosts, objs = self.build(backend)
                inp, char, anim = objs["AlsInput"], objs["AlsCharacter"], objs["AlsAnim"]
                inp.set_input("Sprint", 1.0)
                inp.set_input("JumpPressed", True)
                for h in hosts.values():
                    h.load({"velocity": (0.0, 0.0, 0.0), "acceleration": (0.0, 0.0, 0.0)})
                inp.update_phase("pre_movement", 0.016)
                # the input logic called the character at once
                self.assertEqual(char.dal_get("DesiredGait"), "Als.Gait.Sprinting")
                self.assertEqual(char.dal_get("Jumps"), 1)
                self.assertTrue(inp.dal_get("Jumped"))
                # and the character queued Notify for the animation instance: not run yet
                self.assertEqual(anim.dal_get("Notified"), 0)
                char.update_phase("after_movement", 0.016)
                self.assertEqual(anim.dal_get("Notified"), 0)
                anim.update(0.016)                         # the queue is drained at the start of the animation update
                self.assertEqual(anim.dal_get("Notified"), 1)
                self.assertEqual(anim.dal_get("Updates"), 1)

    def test_inputs_follow_the_peer_at_the_start_of_each_block(self):
        for backend in ("interp", "python"):
            with self.subTest(backend):
                mods, hosts, objs = self.build(backend)
                char, anim = objs["AlsCharacter"], objs["AlsAnim"]
                for h in hosts.values():
                    h.load({"velocity": (300.0, 0.0, 0.0), "acceleration": (100.0, 0.0, 0.0)})
                anim.update(0.016)                         # before the character has run: its declared defaults
                self.assertEqual(anim.dal_get("CharSpeed"), 0.0)
                self.assertEqual(anim.dal_get("Gait"), "Als.Gait.Walking")      # the character's declared default
                char.update_phase("after_movement", 0.016)
                anim.update(0.016)
                self.assertEqual(anim.dal_get("CharSpeed"), 300.0)
                self.assertEqual(anim.dal_get("Gait"), "Als.Gait.Running")
                snap = anim.snapshot()
                self.assertEqual(snap["CharLocomotion.Speed"], 300.0)
                self.assertTrue(snap["CharLocomotion.bMoving"])

    def test_a_peer_that_is_not_connected_reads_as_zero_and_calls_do_nothing(self):
        for backend in ("interp", "python"):
            with self.subTest(backend):
                mods = system()
                host = harness.ScriptedHost()
                host.load({})
                obj = Interpreter(mods["AlsInput"], host) if backend == "interp" else load_class(mods["AlsInput"])(host)
                obj.set_input("JumpPressed", True)
                obj.update(0.016)                          # no Character connected
                self.assertEqual(obj.unresolved, {"Character"})
                self.assertFalse(obj.dal_get("Jumped"))
                self.assertEqual(obj.dal_get("Sent"), 1)
                anim = Interpreter(mods["AlsAnim"], host) if backend == "interp" else load_class(mods["AlsAnim"])(host)
                anim.update(0.016)
                self.assertEqual(anim.snapshot()["CharSpeed"], 0.0)
                self.assertEqual(anim.unresolved, {"Character"})

    def test_struct_values_cross_between_logics_as_copies(self):
        mods, hosts, objs = self.build("python")
        char, anim = objs["AlsCharacter"], objs["AlsAnim"]
        for h in hosts.values():
            h.load({"velocity": (300.0, 0.0, 0.0), "acceleration": (100.0, 0.0, 0.0)})
        char.update_phase("after_movement", 0.016)
        anim.update(0.016)
        seen = anim.CharLocomotion
        self.assertIsNot(seen, char.Locomotion)
        char.Locomotion.Speed = 12345.0
        self.assertEqual(seen.Speed, 300.0)
        # a generated class and an interpreter can be mixed: the struct classes differ but the values convert
        it_anim = Interpreter(mods["AlsAnim"], hosts["AlsAnim"])
        it_anim.connect("Character", char)
        it_anim.update(0.016)
        self.assertEqual(it_anim.snapshot()["CharLocomotion.Speed"], 12345.0)

    def test_overrides_win_over_peer_values(self):
        mods, hosts, objs = self.build("python")
        anim = objs["AlsAnim"]
        for h in hosts.values():
            h.load({})
        anim.overrides["CharSpeed"] = 77.0
        anim.update(0.016)
        self.assertEqual(anim.CharSpeed, 77.0)
        with self.assertRaises(ValueError):
            anim.set_input("CharSpeed", 1.0)               # bound to a peer: not a free input

    def test_metadata_for_hosts(self):
        mods = system()
        cls = load_class(mods["AlsCharacter"])
        self.assertEqual(cls.DAL_PEERS, (("Anim", "AlsAnim"),))
        self.assertEqual(cls.DAL_METHODS, ("SetDesiredGait", "SetStance", "Jump"))
        self.assertEqual(cls.DAL_PHASES, ("after_movement",))
        self.assertEqual(load_class(mods["AlsAnim"]).DAL_PHASES, ())
        self.assertIn(("CharSpeed", "float", "peer.Character.Locomotion.Speed"), load_class(mods["AlsAnim"]).DAL_INPUTS)


class TickSlotTests(unittest.TestCase):
    SRC = """
logic Slots : actor;
input Velocity: vec3 <- host.velocity;
state Pre: float;
state After: float;
state Late: float;
state Order: int;
state Pre2: int;
state Events: int;
timer T = 1.0;
on notify Ping { Events += 1; }

update(dt) in after_anim { Late = Velocity.x; Order = Order * 10 + 3; }
update(dt) in pre_movement {
    Pre = Velocity.x;
    Order = Order * 10 + 1;
    start(T);
}
update(dt) in after_movement { After = Velocity.x; Order = Order * 10 + 2; }
"""

    def test_update_runs_the_slots_in_order_and_each_samples_the_host_again(self):
        m = dal.check_source(self.SRC, "t.dal").module
        self.assertEqual([u.phase for u in m.updates], ["pre_movement", "after_movement", "after_anim"])
        for backend in ("interp", "python"):
            host = harness.ScriptedHost()
            obj = Interpreter(m, host) if backend == "interp" else load_class(m)(host)
            host.load({"velocity": (5.0, 0.0, 0.0)})
            obj.update_phase("pre_movement", 0.1)
            host.load({"velocity": (6.0, 0.0, 0.0)})          # the movement changed the world
            obj.update_phase("after_movement", 0.1)
            host.load({"velocity": (7.0, 0.0, 0.0)})
            obj.update_phase("after_anim", 0.1)
            self.assertEqual((obj.dal_get("Pre"), obj.dal_get("After"), obj.dal_get("Late"), obj.dal_get("Order")), (5.0, 6.0, 7.0, 123), backend)
            obj.update_phase("no_such_slot", 0.1)             # nothing happens
            self.assertEqual(obj.dal_get("Order"), 123)
            host.load({"velocity": (1.0, 0.0, 0.0)})
            obj.update(0.1)                                    # all slots in order
            self.assertEqual((obj.dal_get("Pre"), obj.dal_get("After"), obj.dal_get("Late"), obj.dal_get("Order")), (1.0, 1.0, 1.0, 123123), backend)

    def test_timers_and_events_belong_to_the_first_slot(self):
        m = dal.check_source(self.SRC, "t.dal").module
        self.assertTrue(m.updates[0].pre)                      # the timer tick is in the pre_movement block
        self.assertFalse(m.updates[1].pre or m.updates[2].pre)
        for backend in ("interp", "python"):
            host = harness.ScriptedHost()
            obj = Interpreter(m, host) if backend == "interp" else load_class(m)(host)
            host.load({"events": [("notify", "Ping")]})
            obj.update_phase("after_movement", 0.1)            # the event is not taken here
            self.assertEqual(obj.dal_get("Events"), 0)
            obj.update_phase("pre_movement", 0.1)
            self.assertEqual(obj.dal_get("Events"), 1)

    def test_a_logic_with_only_handlers_has_a_default_slot(self):
        m = dal.check_source("logic H : actor in after_movement;\nstate N: int;\non notify Ping { N += 1; }", "t.dal").module
        self.assertEqual(m.updates, [])
        self.assertEqual(m.default_phase, "pre_movement")      # no update block: the default is the slot of the first block
        host = harness.ScriptedHost()
        host.load({"events": [("notify", "Ping")]})
        obj = load_class(m)(host)
        obj.update_phase("pre_movement", 0.1)
        self.assertEqual(obj.N, 1)

    def test_random_frames_agree(self):
        m = dal.check_source(self.SRC, "t.dal").module
        for seed in (1, 2):
            self.assertEqual(harness.verify(m, 150, seed), [], seed)


class SystemConsistencyTests(unittest.TestCase):
    def test_interpreter_and_python_agree_on_the_system(self):
        mods = system()
        for seed in (1, 2, 3, 4):
            self.assertEqual(harness.verify_system(mods, 150, seed), [], seed)

    def test_the_default_schedule_follows_the_engine_order(self):
        mods = system()
        self.assertEqual(harness.default_schedule(mods), [("AlsInput", "pre_movement"), ("AlsCharacter", "after_movement"), ("AlsAnim", None)])

    def test_a_different_schedule_changes_the_result_in_the_expected_way(self):
        # the animation instance before the character sees last frame's character (one frame of lag)
        mods = system()
        frames = harness.random_system_frames(mods, 40, 9)
        good = harness.run_system(mods, frames, "python")
        lagged = harness.run_system(mods, frames, "python", [("AlsInput", "pre_movement"), ("AlsAnim", None), ("AlsCharacter", "after_movement")])
        self.assertNotEqual([f["AlsAnim"]["CharSpeed"] for f in good], [f["AlsAnim"]["CharSpeed"] for f in lagged])
        self.assertEqual([f["AlsAnim"]["CharSpeed"] for f in lagged][1:], [f["AlsCharacter"]["Locomotion.Speed"] for f in lagged][:-1])


class PeersOnDiskTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dal3_peers_")
        for name, text in FILES.items():
            with open(os.path.join(self.dir, name), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_check_file_finds_the_peer_next_to_the_file(self):
        r = dal.check_file(os.path.join(self.dir, "AlsInput.dal"))
        self.assertEqual([p.logic for p in r.module.peers], ["AlsCharacter"])
        self.assertEqual(r.module.peers[0].filename, "AlsCharacter.dal")

    def test_generated_python_of_a_peer_logic_runs_standalone(self):
        r = dal.check_file(os.path.join(self.dir, "AlsAnim.dal"))
        src = generate_python(r.module)
        self.assertIn("peers=None", src)
        self.assertIn("DAL_PEERS = (('Character', 'AlsCharacter'), )", src)


if __name__ == "__main__":
    unittest.main()
