"""Methods, views, call statements and the lifecycle handlers (`on begin_play` / `on end_play`)."""

import unittest

import _bootstrap  # noqa: F401

import dal
from dal import harness, ir
from dal.interp import Interpreter
from dal.pybackend import generate_python, load_class
from struct_sources import STRUCTS

HDR = "logic Zz;\n"


def errors(src, files=None, name="t.dal"):
    try:
        dal.check_source(src, name, files=files)
    except dal.DalError as e:
        return [(d.code, d.pos.line, d.pos.col) for d in e.diagnostics if d.severity == "error"]
    return []


def codes(src):
    return [c for c, _l, _c in errors(src)]


class MethodCheckTests(unittest.TestCase):
    def check(self, body_src, *expected):
        with self.subTest(src=body_src):
            self.assertEqual(codes(body_src), list(expected), body_src)

    def test_declarations_are_collected(self):
        m = dal.check_source(HDR + """
state S: float;
method bump(by: float) { S += by; }
pub method set_to(v: float) -> float { S = v; return S; }
view doubled() -> float { return S * 2.0; }
pub view GetS() -> float { return S; }
update(dt) { bump(dt); }""", "t.dal").module
        self.assertEqual([(x.name, x.pub, x.is_view) for x in m.methods],
                         [("bump", False, False), ("set_to", True, False), ("doubled", False, True), ("GetS", True, True)])
        self.assertIsNone(m.methods[0].ret)
        self.assertIn("pub method set_to(v: float) -> float", ir.dump(m))

    def test_recursion_is_rejected(self):
        self.check(HDR + "method a() { a(); }\nupdate(dt) { a(); }", "DAL5046")
        self.check(HDR + "method a() { b(); }\nmethod b() { a(); }\nupdate(dt) { a(); }", "DAL5046")
        self.check(HDR + "state S: float;\nview v() -> float { return w(); }\nview w() -> float { return v(); }\nupdate(dt) { S = v(); }", "DAL5046")

    def test_views_only_read(self):
        pre = HDR + "state S: float;\nmethod m() { S = 1.0; }\nupdate(dt) {}\n"
        self.check(pre + "view v() -> float { S = 1.0; return S; }", "DAL5044")
        self.check(pre + "view v() -> float { m(); return S; }", "DAL5044")
        self.check(pre + "view v() -> float { host.jump(); return S; }", "DAL5044")
        self.check(pre + "view v() -> float { var x = S; x += 1.0; return x; }")
        self.check(pre.replace("logic Zz;", "logic Zz : actor;") + "view v() -> float { let h = host.trace_line(vec3(0.0, 0.0, 0.0), vec3(1.0, 0.0, 0.0)); return h.distance; }", "DAL5044")

    def test_functions_stay_pure(self):
        pre = HDR + "state S: float;\nmethod m() { S = 1.0; }\nview v() -> float { return S; }\nupdate(dt) {}\n"
        self.check(pre + "fn f() -> float { return v(); }", "DAL5012")
        self.check(pre + "fn f() -> float { m(); return 1.0; }", "DAL5012")
        self.check(pre + "const C: float = v();", "DAL5002")

    def test_a_method_result_is_taken_from_a_whole_right_hand_side(self):
        pre = HDR + "state S: float;\nmethod inc() -> float { S += 1.0; return S; }\nmethod nothing() { S = 0.0; }\n"
        self.check(pre + "update(dt) { let a = inc(); var b = inc(); b = inc(); S = inc(); inc(); }")
        # inside an expression a call runs before the statement (language version 4): fine where it always runs
        self.check(pre + "update(dt) { let a = inc() + 1.0; }")
        self.check(pre + "update(dt) { S = 2.0 * inc(); }")
        self.check(pre + "update(dt) { S += inc(); }")
        self.check(pre + "update(dt) { if (inc() > 1.0) { S = 1.0; } }")
        self.check(pre + "update(dt) { let a = sqrt(inc()); }")
        # ... and an error where it might not run, or has to run every pass
        self.check(pre + "update(dt) { if (S > 0.0 && inc() > 1.0) { S = 1.0; } }", "DAL4042")
        self.check(pre + "update(dt) { if (S > 0.0 || inc() > 1.0) { S = 1.0; } }", "DAL4042")
        self.check(pre + "update(dt) { let a = S > 0.0 ? inc() : 1.0; }", "DAL4042")
        self.check(pre + "update(dt) { let a = S > 0.0 ? 2.0 : inc(); }", "DAL4042")
        self.check(pre + "update(dt) { while (inc() < 3.0) bound 3 { S += 1.0; } }", "DAL4042")
        self.check(pre + "update(dt) { if (inc() > 1.0 && S > 0.0) { S = 1.0; } }")                          # the left side always runs
        self.check(pre + "update(dt) { let a = nothing(); }", "DAL4043")
        self.check(pre + "update(dt) { nothing(); }")

    def test_calls_are_checked_like_functions(self):
        pre = HDR + "state S: float;\nmethod m(a: float, b: bool) { S = a; }\nview v(x: vec2) -> float { return x.x; }\n"
        self.check(pre + "update(dt) { m(1.0); }", "DAL4041")
        self.check(pre + "update(dt) { m(1.0, 2.0); }", "DAL4002")
        self.check(pre + "update(dt) { m(1, true); }")                       # int widens to float
        self.check(pre + "update(dt) { S = v(1.0); }", "DAL4002")
        self.check(pre + "update(dt) { v(vec2(1.0, 2.0)); }", "DAL2011")       # a view is for its result
        self.check(pre + "update(dt) { nothere(1.0); }", "DAL2011")
        self.check(pre + "update(dt) { let x = m; }", "DAL3031")

    def test_returns(self):
        self.check(HDR + "method m() { return 1.0; }\nupdate(dt) {}", "DAL5020")
        self.check(HDR + "method m() -> float { return; }\nupdate(dt) {}", "DAL5020")
        self.check(HDR + "method m() -> float { }\nupdate(dt) {}", "DAL5021")
        self.check(HDR + "method m(a: bool) -> float { if (a) { return 1.0; } }\nupdate(dt) {}", "DAL5021")
        self.check(HDR + "method m(a: bool) { if (a) { return; } }\nupdate(dt) {}")
        self.check(HDR + "view v() -> float { }\nupdate(dt) {}", "DAL5021")
        self.check(HDR + "view v() -> float { return true; }\nupdate(dt) {}", "DAL4002")

    def test_names(self):
        self.check(HDR + "state S: float;\nmethod S() {}\nupdate(dt) {}", "DAL3001")
        self.check(HDR + "method m() {}\nview m() -> float { return 1.0; }\nupdate(dt) {}", "DAL3001")
        self.check(HDR + "method m(a: float, a: float) {}\nupdate(dt) {}", "DAL3001")
        self.check(HDR + "state S: float;\nmethod m(S: float) {}\nupdate(dt) {}", "DAL3005")
        self.check(HDR + "method update_phase() {}\nupdate(dt) {}", "DAL3004")
        self.check(HDR + "method Dal_x() {}\nupdate(dt) {}", "DAL3004")
        self.check(HDR + "method delete() {}\nupdate(dt) {}", "DAL3004")
        self.check(HDR + "view v() { }\nupdate(dt) {}", "DAL2001") if False else None

    def test_view_needs_a_result_type(self):
        with self.assertRaises(dal.DalError) as cm:
            dal.check_source(HDR + "view v() { }", "t.dal")
        self.assertEqual(cm.exception.first.code, "DAL2001")

    def test_lifecycle_handlers(self):
        self.check(HDR + "state S: float;\non begin_play { S = 1.0; }\non end_play { S = 2.0; }\nupdate(dt) {}")
        self.check(HDR + "on begin_play { return 1.0; }", "DAL5020")
        self.check(HDR + "on nothing { }", "DAL2021")


PROGRAM = STRUCTS + HDR + """
state Blend: VelocityBlend;
state Total: float;
state Gate: bool;
state Phase: int;
state Trace: float;
timer Cooldown = 0.25;
input Velocity: vec3;
input Fire: bool;

view speed() -> float { return length(Velocity.xy); }
view target_for(v: vec3) -> VelocityBlend {
    return VelocityBlend(Forward: clamp(v.x, 0.0, 1.0), Backward: abs(clamp(v.x, -1.0, 0.0)));
}

method lerp_blend(a: float) {
    let t = target_for(normalize(Velocity));
    Blend.Forward = lerp(Blend.Forward, t.Forward, a);
    Blend.Backward = lerp(Blend.Backward, t.Backward, a);
}

method step() -> int {
    Phase += 1;
    if (Phase > 3) { Phase = 0; return -1; }
    return Phase;
}

pub method Reset(to: float) { Total = to; Trace = 0.0; }

method try_fire() {
    if (Fire && Cooldown.ready) {
        start(Cooldown);
        Total += 1.0;
        host.jump();
    }
}

on begin_play { Total = 50.0; Trace = 1.0; }
on end_play { Total = -1.0; }

update(dt) {
    lerp_blend(clamp(dt * 8.0, 0.0, 1.0));
    let s = step();
    Trace += s >= 0 ? 0.5 : 10.0;
    try_fire();
    Gate = speed() > 100.0;
}

entry Poke(dt) { Total += dt; try_fire(); }
"""


class MethodSemanticsTests(unittest.TestCase):
    def test_methods_views_and_lowering_inside_methods(self):
        m = dal.check_source(PROGRAM, "t.dal").module
        frames = [{"dt": 0.1, "free": {"Fire": f, "Velocity": (500.0, 0.0, 0.0)}} for f in (False, True, True, False, True)]
        a = harness.run_interpreter(m, frames)
        b = harness.run_python(m, frames)
        self.assertEqual(harness.compare(a, b), [])
        self.assertEqual(a[0]["Total"], 50.0)                    # begin_play ran before the first update
        self.assertTrue(a[0]["Gate"])
        self.assertEqual([f["Phase"] for f in a], [1, 2, 3, 0, 1])
        self.assertEqual([f["Trace"] for f in a], [1.5, 2.0, 2.5, 12.5, 13.0])
        # the cooldown timer started by a method gates the next shot: frame 1 fires, frames 2-4 are inside the 0.25 s, frame 5 fires again
        self.assertEqual([f["Total"] for f in a], [50.0, 51.0, 51.0, 51.0, 52.0])
        self.assertEqual(a[1]["__cmds__"], (("jump", ()),))
        self.assertEqual(a[2]["__cmds__"], ())

    def test_pub_methods_are_callable_from_outside(self):
        m = dal.check_source(PROGRAM, "t.dal").module
        cls = load_class(m)
        obj = cls(None)
        obj.update(0.016)
        obj.Reset(7.0)                                           # the Python name of a pub method is its DAL name
        self.assertEqual((obj.Total, obj.Trace), (7.0, 0.0))
        self.assertEqual(obj.dal_call("Reset", 3.0), None)
        self.assertEqual(obj.Total, 3.0)
        self.assertEqual(obj.DAL_METHODS, ("Reset",))
        with self.assertRaises(KeyError):
            obj.dal_call("step")                                 # private
        it = Interpreter(m)
        it.update(0.016)
        it.dal_call("Reset", 4.0)
        self.assertEqual(it.values["Total"], 4.0)
        with self.assertRaises(Exception):
            it.dal_call("step")

    def test_begin_and_end_play(self):
        m = dal.check_source(PROGRAM, "t.dal").module
        for make in (lambda: load_class(m)(None), lambda: Interpreter(m)):
            obj = make()
            self.assertEqual(obj.snapshot()["Total"], 0.0)       # nothing ran at construction
            obj.begin_play()                                     # a host may begin the play itself ...
            snap = obj.snapshot()
            self.assertEqual((snap["Total"], snap["Trace"]), (50.0, 1.0))
            obj.begin_play()                                     # ... once
            obj.update(0.016)
            self.assertEqual(obj.snapshot()["Total"], 50.0)
            obj.end_play()
            self.assertEqual(obj.snapshot()["Total"], -1.0)
            obj.reset()                                          # a reset arms begin_play again
            obj.update(0.016)
            self.assertEqual(obj.snapshot()["Total"], 50.0)

    def test_methods_in_entries_and_handlers(self):
        src = HDR + """
montage M { A }
state N: int;
state Log: float;
method bump(by: int) -> int { N += by; return N; }
on montage_ended M { let r = bump(10); Log = r; }
entry Poke(dt) { let r = bump(1); Log = r * 100; }
update(dt) { }
"""
        m = dal.check_source(src, "t.dal").module
        for run in (harness.run_interpreter, harness.run_python):
            frames = [{"dt": 0.1, "calls": ["Poke"], "events": [("montage_ended", "M", False)]}, {"dt": 0.1, "calls": ["Poke", "Poke"]}]
            out = run(m, frames)
            self.assertEqual([(f["N"], f["Log"]) for f in out], [(11, 1100.0), (13, 1300.0)])

    def test_struct_arguments_and_results_are_values(self):
        src = STRUCTS + HDR + """
state B: VelocityBlend;
state C: VelocityBlend;
method modify(v: VelocityBlend) -> VelocityBlend { var w = v; w.Forward = 9.0; return w; }
method mutate_state() { B.Left = 5.0; }
update(dt) {
    let before = B;
    mutate_state();
    C = modify(B);
    B.Right = before.Left + 1.0;
}
"""
        m = dal.check_source(src, "t.dal").module
        a = harness.run_interpreter(m, [{"dt": 0.1}] * 2)
        b = harness.run_python(m, [{"dt": 0.1}] * 2)
        self.assertEqual(harness.compare(a, b), [])
        self.assertEqual((a[0]["B.Forward"], a[0]["B.Left"], a[0]["B.Right"], a[0]["C.Forward"], a[0]["C.Left"]), (0.0, 5.0, 1.0, 9.0, 5.0))

    def test_random_frames_agree(self):
        m = dal.check_source(PROGRAM, "t.dal").module
        for seed in (1, 2, 3):
            self.assertEqual(harness.verify(m, 200, seed), [], seed)

    def test_lifecycle_only_logic_does_not_poll_for_events(self):
        m = dal.check_source(HDR + "state S: float;\non begin_play { S = 4.0; }", "t.dal").module
        src = generate_python(m)
        self.assertNotIn("_collect_events", src)
        self.assertNotIn("DAL_HANDLERS", src)
        obj = load_class(m)(None)
        obj.update(0.0)
        self.assertEqual(obj.S, 4.0)

    def test_legacy_update_keeps_its_shape(self):
        # a program without tick slots, peers or lifecycle generates the same update() as before
        src = generate_python(dal.check_source(HDR + "state S: float;\nupdate(dt) { S += dt; }", "t.dal").module)
        self.assertIn("    def update(self, dt):\n", src)
        self.assertNotIn("update_phase", src)
        self.assertNotIn("_begun", src)


if __name__ == "__main__":
    unittest.main()
