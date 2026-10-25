"""`host.is_game_thread()` and `host.proxy_component_transform()`: two thread-safe queries an animation logic asks while the graph updates.

ALS asks the thread twice over (`IsInGameThread()` decides whether a queued montage plays at once or waits for the post update) and reads the transform
the animation proxy cached (`Proxy.GetComponentTransform()`, which is not the mesh's transform of the moment while root motion moves the character).

A host without threads answers false and the mesh's transform, so the logic behaves as it did.  The generated C++ asks the engine, and an
animation logic with `post_flush` that asks the thread runs the commands of an update or an entry at once when it ran on the game thread."""

import unittest

import _bootstrap  # noqa: F401

import cpp_driver
import dal
import test_cpp_compile as tcc
from dal import harness, hostapi
from dal import runtime as rt
from dal.cppbackend import generate_cpp
from dal.mockhost import MockHost

PROXY = """
logic Px : anim;
input MeshT: transform <- host.mesh_world_transform;
state Proxy: vec3;
state Mesh: vec3;
state Game: bool;
update(dt) {
    Proxy = host.proxy_component_transform().location;
    Mesh = MeshT.location;
    Game = host.is_game_thread();
}
"""
GAME = """
logic Gt : anim post_flush;
state N: int;
update(dt) {
    if (host.is_game_thread()) {
        host.stop_montages_with_slot("InUpdate", 0.1);
    }
}
entry Stop() {
    host.stop_montages_with_slot("InEntry", 0.2);
    N += 1;
}
"""
PLAIN = """
logic Pl : anim post_flush;
state N: int;
update(dt) { host.stop_montages_with_slot("InUpdate", 0.1); }
entry Stop() { host.stop_montages_with_slot("InEntry", 0.2); N += 1; }
"""
ACTOR = """
logic Ac : actor;
state Where: vec3;
state Game: bool;
update(dt) { Where = host.proxy_component_transform().location; Game = host.is_game_thread(); }
"""


def module(src, name="T.dal"):
    return dal.check_source(src, name).module


def codes(src):
    try:
        dal.check_source(src, "t.dal")
    except dal.DalError as e:
        return [d.code for d in e.diagnostics if d.severity == "error"]
    return []


def frame(**kw):
    kw.setdefault("dt", 0.0166)
    return kw


class ProtocolTests(unittest.TestCase):
    def test_the_queries_are_in_the_protocol(self):
        for name, ret in (("is_game_thread", "bool"), ("proxy_component_transform", "transform")):
            q = hostapi.QUERIES[name]
            self.assertEqual((q.params, q.ret.name, q.thread_safe), ((), ret, True), name)
        self.assertIn("is_game_thread", hostapi.protocol_markdown())

    def test_a_host_without_threads_answers_false_and_the_mesh_transform(self):
        h = MockHost()
        h.mesh_world_transform = rt.Transform((1.0, 2.0, 3.0))
        self.assertFalse(h.is_game_thread())
        self.assertEqual(h.proxy_component_transform(), h.mesh_world_transform)

    def test_an_anim_logic_may_ask_and_so_may_an_actor_logic(self):
        self.assertEqual(codes(PROXY), [])
        self.assertEqual(codes(ACTOR), [])
        self.assertEqual(codes("logic T : anim;\nstate S: bool;\nupdate(dt) { S = host.is_game_thread(1.0); }"), ["DAL4041"])


class PythonTests(unittest.TestCase):
    def frames(self):
        mesh = ((1.0, 2.0, 3.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))
        return [frame(mesh_world_transform=mesh),                                                          # nothing else: the proxy has the mesh's transform
                frame(mesh_world_transform=mesh, proxy_component_transform=((10.0, 20.0, 30.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)), game_thread=True)]

    def test_the_interpreter_and_the_generated_python_agree(self):
        m = module(PROXY, "Px.dal")
        a, b = harness.run_interpreter(m, self.frames()), harness.run_python(m, self.frames())
        self.assertEqual(harness.compare(a, b), [])
        self.assertEqual((a[0]["Proxy"], a[0]["Mesh"], a[0]["Game"]), ((1.0, 2.0, 3.0), (1.0, 2.0, 3.0), False))
        self.assertEqual((a[1]["Proxy"], a[1]["Mesh"], a[1]["Game"]), ((10.0, 20.0, 30.0), (1.0, 2.0, 3.0), True))

    def test_the_commands_wait_for_the_post_update_without_the_c_plus_plus_thread(self):
        # (the interpreter and the generated Python know no threads: the commands of an update and an entry run at the post update, as before)
        m = module(GAME, "Gt.dal")
        fr = [frame(game_thread=True, calls=["Stop"])]
        for run in (harness.run_interpreter, harness.run_python):
            out = run(m, fr)
            self.assertEqual([c[0] for c in out[0]["__cmds__"]], ["stop_montages_with_slot", "stop_montages_with_slot"], run.__name__)


@unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
class CppTests(unittest.TestCase):
    def test_generated_text(self):
        cpp, hdr = (lambda f: (f["Px.cpp"], f["Px.h"]))(generate_cpp(module(PROXY, "Px.dal")))
        self.assertIn("return IsInGameThread();", cpp)
        self.assertIn("return FTransform3f(GetProxyOnAnyThread<FAnimInstanceProxy>().GetComponentTransform());", cpp)
        self.assertIn('#include "CoreGlobals.h"', cpp)
        self.assertIn('#include "Animation/AnimInstanceProxy.h"', cpp)
        self.assertIn("bool DalIsGameThread()", hdr)
        self.assertNotIn("if (IsInGameThread())\n\t{\n\t\tDalFlushCommands();", cpp)                  # (no commands, nothing to flush)

    def test_the_actor_logic_reads_the_mesh_component(self):
        cpp = generate_cpp(module(ACTOR, "Ac.dal"))["Ac.cpp"]
        self.assertIn("FindComponentByClass<USkeletalMeshComponent>()", cpp)
        self.assertNotIn("GetProxyOnAnyThread", cpp)

    def test_entries_and_the_update_flush_on_the_game_thread(self):
        cpp = generate_cpp(module(GAME, "Gt.dal"))["Gt.cpp"]
        entry = cpp.split("void UGt::Stop()")[1].split("\n}\n")[0]
        self.assertIn("if (IsInGameThread())", entry)
        self.assertIn("DalFlushCommands();", entry.split("if (IsInGameThread())")[1])
        update = cpp.split("void UGt::NativeThreadSafeUpdateAnimation")[1].split("\n}\n")[0]
        self.assertIn("if (IsInGameThread())", update)

    def test_a_logic_that_does_not_ask_keeps_its_commands_for_the_post_update(self):
        cpp = generate_cpp(module(PLAIN, "Pl.dal"))["Pl.cpp"]
        self.assertNotIn("IsInGameThread", cpp)

    def test_the_proxy_transform_is_what_the_engine_cached(self):
        m = module(PROXY, "Px.dal")
        mesh = ((1.0, 2.0, 3.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))
        proxy = ((10.0, 20.0, 30.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))
        frames = [frame(mesh_world_transform=mesh), frame(mesh_world_transform=mesh, proxy_component_transform=proxy, game_thread=True)]
        states, _out = cpp_driver.build_and_run(m, frames)
        self.assertEqual((states[0]["Proxy"], states[0]["Game"]), ((1.0, 2.0, 3.0), False))
        self.assertEqual((states[1]["Proxy"], states[1]["Mesh"], states[1]["Game"]), ((10.0, 20.0, 30.0), (1.0, 2.0, 3.0), True))

    def test_the_commands_run_where_the_thread_says(self):
        m = module(GAME, "Gt.dal")
        on = [frame(game_thread=True, mark=True, calls=["Stop"])]
        off = [frame(game_thread=False, mark=True, calls=["Stop"])]
        names = lambda states: [c[0] for c in states[0]["__cmds__"]]  # noqa: E731
        got_on = names(cpp_driver.build_and_run(m, on)[0])
        got_off = names(cpp_driver.build_and_run(m, off)[0])
        marks = [n for n in got_on if n.startswith("mark_")]
        self.assertEqual(marks, ["mark_updated", "mark_calls_done"])
        commands_on = [n for n in got_on if not n.startswith("mark_")]
        self.assertEqual(len(commands_on), 2)                                                         # the update's (game thread: asked) and the entry's
        # on the game thread both ran before the entry's mark; off it, none did before the post update
        self.assertEqual(got_on.index("mark_calls_done"), 3, got_on)
        self.assertEqual(got_off[:2], ["mark_updated", "mark_calls_done"], got_off)
        self.assertEqual(len([n for n in got_off if not n.startswith("mark_")]), 1, got_off)          # (the update asked while off the game thread: it issued nothing)

    def test_random_frames_agree_with_python(self):
        for src, name in ((PROXY, "Px.dal"), (GAME, "Gt.dal")):
            m = module(src, name)
            tcc.CppCompileTests("test_hero_gait_matches_python").run_case(m, harness.random_frames(m, 40, 3), warnings_as_errors=True)


if __name__ == "__main__":
    unittest.main()
