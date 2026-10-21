"""`post_update { ... }` and `logic X : anim post_flush;`: the block that runs after the animation graph updated, the moment the
commands of a frame reach the host, diagnostics, interpreter / Python consistency and the generated proxy (C++)."""

import unittest

import _bootstrap  # noqa: F401

import dal
from dal import harness, ir
from dal.cppbackend import generate_cpp

PLAIN = """
logic T;
state Q: int;
state Played: int;
state Dt: float;
entry Queue(dt) { Q += 1; host.set_gravity_scale(2.0); }
update(dt) { host.set_max_walk_speed(Q * 1.0); }
post_update(dt) { Played += 1; Dt = dt; if (Q > 0) { host.montage_stop_all(); } }
"""

FLUSH = PLAIN.replace("logic T;", "logic T : anim post_flush;")


def compile_ok(src, **kw):
    return dal.check_source(src, "t.dal", **kw).module


def errors(src, **kw):
    try:
        dal.check_source(src, "t.dal", **kw)
    except dal.DalError as e:
        return [(d.code, d.pos.line, d.pos.col) for d in e.diagnostics if d.severity == "error"]
    return []


def fr(dt=0.125, **kw):
    kw["dt"] = dt
    return kw


class DiagnosticTests(unittest.TestCase):
    def check(self, src, *expected):
        with self.subTest(src=src):
            self.assertEqual(errors(src), list(expected), src)

    def test_post_update_belongs_to_animation_logic(self):
        self.check("logic T : actor;\npost_update { }", ("DAL5065", 2, 1))
        self.check("logic T : actor post_flush;", ("DAL2025", 1, 17))
        self.check("logic T;\npost_update { }\npost_update { }", ("DAL3079", 3, 1))
        self.check("logic T;\nstate Q: int;\npost_update { Q = 1; }\npost_update(dt) { }", ("DAL3079", 4, 1))

    def test_names_that_collide_with_the_generated_method(self):
        self.check("logic T;\nstate post_update: int;\npost_update { }", ("DAL3004", 2, 7))
        compile_ok("logic T;\nstate post_update: int;\nupdate(dt) { post_update = 1; }")           # without the block there is no such method

    def test_the_body_is_checked_like_an_update(self):
        self.check("logic T;\nstate Q: int;\npost_update { Q = 1.5; }", ("DAL4002", 3, 19))
        self.check("logic T;\npost_update { host.nope(); }", ("DAL3060", 2, 20))
        self.check("logic T;\nstate Q: float;\npost_update(Q) { }", ("DAL3005", 3, 13))
        self.check("logic T;\nstate Q: float;\npost_update { let h = host.trace_line(vec3(0.0, 0.0, 0.0), vec3(1.0, 0.0, 0.0)); Q = h.distance; }", ("DAL5041", 3, 23))

    def test_the_options_of_the_logic_line(self):
        self.assertTrue(compile_ok("logic T : anim post_flush;").post_flush)
        self.assertFalse(compile_ok("logic T : anim;").post_flush)
        self.assertFalse(compile_ok("logic T;").post_flush)
        files = {"Main.dal": "logic Main;\nentry Go(dt) linked { }\nupdate(dt) { }", "Sub.dal": "logic Sub : anim linked(Main) post_flush;\nupdate(dt) { }"}
        self.assertTrue(dal.check_source(files["Sub.dal"], "Sub.dal", files=files).module.post_flush)
        files["Sub.dal"] = "logic Sub : anim post_flush linked(Main);\nupdate(dt) { }"
        sub = dal.check_source(files["Sub.dal"], "Sub.dal", files=files).module
        self.assertTrue(sub.post_flush and sub.linked is not None)

    def test_old_programs_are_unchanged(self):
        m = compile_ok(PLAIN.replace("post_update(dt) { Played += 1; Dt = dt; if (Q > 0) { host.montage_stop_all(); } }", ""))
        self.assertIsNone(m.post_update)
        self.assertFalse(m.post_flush)


class SemanticsTests(unittest.TestCase):
    def run_py(self, src, frames):
        module = compile_ok(src)
        py = harness.run_python(module, frames)
        it = harness.run_interpreter(module, frames)
        self.assertEqual(harness.compare(py, it), [])
        return py

    def test_without_post_flush_the_update_flushes_itself_and_post_update_flushes_its_own_commands(self):
        out = self.run_py(PLAIN, [fr(calls=["Queue"]), fr(), fr(calls=["Queue"])])
        # the update's command and the entry's command were flushed by the update / the entry; the post_update's one by itself
        self.assertEqual([c[0] for c in out[0]["__cmds__"]], ["set_max_walk_speed", "set_gravity_scale", "montage_stop_all"])
        self.assertEqual([f["Played"] for f in out], [1, 2, 3])
        self.assertEqual(out[0]["Dt"], 0.125)

    def test_with_post_flush_everything_of_the_frame_waits_for_the_post_update(self):
        module = compile_ok(FLUSH)
        from dal.pybackend import load_class
        from dal.mockhost import MockHost

        host = MockHost()
        logic = load_class(module)(host)
        logic.update(0.125)
        logic.call_entry("Queue")
        self.assertEqual(host.calls, [])                                      # nothing ran yet: not after the update, not after the entry
        logic.post_update()
        self.assertEqual([c[0] for c in host.calls], ["set_max_walk_speed", "set_gravity_scale", "montage_stop_all"])   # in the order they were queued

    def test_both_implementations_order_the_commands_alike(self):
        out = self.run_py(FLUSH, [fr(calls=["Queue"]), fr(), fr(calls=["Queue"])])
        self.assertEqual([[c[0] for c in f["__cmds__"]] for f in out],
                         [["set_max_walk_speed", "set_gravity_scale", "montage_stop_all"], ["set_max_walk_speed", "montage_stop_all"],
                          ["set_max_walk_speed", "set_gravity_scale", "montage_stop_all"]])

    def test_post_flush_without_a_block_still_flushes_in_post_update(self):
        src = "logic T : anim post_flush;\nentry Go(dt) { host.jump(); }\nupdate(dt) { host.crouch(); }"
        module = compile_ok(src)
        from dal.pybackend import load_class
        from dal.mockhost import MockHost

        host = MockHost()
        logic = load_class(module)(host)
        logic.update(0.1)
        logic.Go()
        self.assertEqual(host.calls, [])
        logic.post_update()
        self.assertEqual([c[0] for c in host.calls], ["crouch", "jump"])
        self.assertEqual(logic.DAL_POST_FLUSH, True)

    def test_begin_and_end_play_still_flush_at_once(self):
        src = "logic T : anim post_flush;\non begin_play { host.jump(); }\non end_play { host.crouch(); }\nupdate(dt) { }"
        module = compile_ok(src)
        from dal.pybackend import load_class
        from dal.mockhost import MockHost

        host = MockHost()
        logic = load_class(module)(host)
        logic.begin_play()
        self.assertEqual([c[0] for c in host.calls], ["jump"])
        logic.end_play()
        self.assertEqual([c[0] for c in host.calls], ["jump", "crouch"])

    def test_the_post_update_reads_what_the_frame_left_in_the_state_and_the_inputs(self):
        src = ("logic T : anim post_flush;\ninput Speed: float <- host.max_walk_speed;\nstate Seen: float;\nstate Copy: float;\n"
               "entry Mark(dt) { Seen = Speed; }\nupdate(dt) { Copy = Speed; }\npost_update { Seen += Copy * 10.0; }")
        out = self.run_py(src, [fr(max_walk_speed=2.0, calls=["Mark"]), fr(max_walk_speed=5.0)])
        self.assertEqual(out[0]["Seen"], 22.0)                       # Mark stored 2.0, the post_update added 20
        self.assertEqual(out[1]["Seen"], 72.0)                       # the inputs are those the update sampled this frame

    def test_the_interpreter_and_python_agree_on_random_frames(self):
        for src in (PLAIN, FLUSH, FLUSH.replace("post_update(dt) {", "post_update {").replace("Dt = dt;", "")):
            module = compile_ok(src)
            for seed in (1, 2, 3):
                self.assertEqual(harness.verify(module, 150, seed), [])

    def test_a_system_runs_the_post_update_of_the_animation_logic_after_its_entries(self):
        files = {"Char.dal": "logic Char : actor;\npeer Anim: T;\nstate J: int;\nupdate(dt) { J += 1; Anim.Mark(J); }",
                 "T.dal": "logic T : anim post_flush;\nstate Last: int;\npub method Mark(n: int) { Last = n; }\npost_update { host.set_gravity_scale(Last * 1.0); }\nupdate(dt) { }"}
        mods = {n[:-4]: dal.check_source(t, n, files=files).module for n, t in files.items()}
        frames = harness.random_system_frames(mods, 20, 3)
        a, b = harness.run_system(mods, frames, "interp"), harness.run_system(mods, frames, "python")
        for fa, fb in zip(a, b):
            self.assertEqual(harness.compare([fa["T"]], [fb["T"]]), [])
        self.assertTrue(any(f["T"]["__cmds__"] for f in a))


class CppStructureTests(unittest.TestCase):
    def setUp(self):
        self.files = generate_cpp(compile_ok(FLUSH))
        self.h, self.cpp = self.files["T.h"], self.files["T.cpp"]

    def test_the_proxy_runs_the_post_update_on_the_game_thread(self):
        self.assertIn("struct FTProxy : public FAnimInstanceProxy", self.h)
        self.assertIn("virtual void PostUpdate(UAnimInstance* AnimationInstance) const override;", self.h)
        self.assertIn("virtual FAnimInstanceProxy* CreateAnimInstanceProxy() override;", self.h)
        self.assertIn('#include "Animation/AnimInstanceProxy.h"', self.h)
        self.assertIn("DalLogic->DalPostUpdate();", self.cpp)
        self.assertIn("return new FTProxy(this);", self.cpp)

    def test_post_flush_moves_the_flush_behind_the_post_update(self):
        update = self.cpp[self.cpp.index("void UT::NativeUpdateAnimation"):self.cpp.index("void UT::NativeThreadSafeUpdateAnimation")]
        self.assertNotIn("DalFlushCommands", update)
        post = self.cpp[self.cpp.index("void UT::DalPostUpdate()"):]
        post = post[:post.index("\n}\n")]
        self.assertIn("Played = Played + 1;", post)
        self.assertTrue(post.rstrip().endswith("DalFlushCommands();"))
        # the entries only queue: the flush is the post_update's
        entry = self.cpp[self.cpp.index("void UT::Queue()"):]
        self.assertNotIn("DalFlushCommands", entry[:entry.index("\n}\n")])

    def test_without_post_flush_the_next_update_still_flushes_and_the_block_gets_its_proxy(self):
        files = generate_cpp(compile_ok(PLAIN))
        cpp = files["T.cpp"]
        update = cpp[cpp.index("void UT::NativeUpdateAnimation"):cpp.index("void UT::NativeThreadSafeUpdateAnimation")]
        self.assertIn("DalFlushCommands();", update)
        post = cpp[cpp.index("void UT::DalPostUpdate()"):]
        self.assertNotIn("DalFlushCommands", post[:post.index("\n}\n")])
        self.assertIn("FTProxy", files["T.h"])

    def test_a_logic_with_neither_has_no_proxy(self):
        files = generate_cpp(compile_ok("logic T;\nstate Q: int;\nentry Go(dt) { host.jump(); Q += 1; }\nupdate(dt) { host.crouch(); }"))
        self.assertNotIn("Proxy", files["T.h"])
        self.assertNotIn("DalPostUpdate", files["T.cpp"])


try:
    import test_cpp_compile as tcc
except Exception:                                   # pragma: no cover
    tcc = None


@unittest.skipIf(tcc is None or tcc.COMPILER is None, "no C++ compiler found")
class CppCompileTests(unittest.TestCase):
    def run_case(self, module, frames, **kw):
        return tcc.CppCompileTests("test_hero_gait_matches_python").run_case(module, frames, **kw)

    def test_post_flush_matches_python(self):
        module = compile_ok(FLUSH)
        effects = 0
        for seed in (1, 2, 3):
            effects += self.run_case(module, harness.random_frames(module, 80, seed), warnings_as_errors=True)
        self.assertGreater(effects, 100)

    def test_the_post_update_of_an_animation_instance_without_post_flush_matches_python(self):
        module = compile_ok(PLAIN)
        for seed in (1, 2):
            self.run_case(module, harness.random_frames(module, 80, seed), warnings_as_errors=True)

    def test_post_update_cooperates_with_events_and_timers(self):
        module = compile_ok("logic T : anim post_flush;\ntimer Tm = 0.25;\nstate N: int;\nstate Go: bool;\non timer Tm { N += 1; host.jump(); }\n"
                            "on notify Hit { host.crouch(); }\nupdate(dt) { if (!Go) { Go = true; start(Tm); } }\npost_update { N += 100; host.uncrouch(); }")
        self.run_case(module, harness.random_frames(module, 100, 4), warnings_as_errors=True)


if __name__ == "__main__":
    unittest.main()
