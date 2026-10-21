"""`on timer` and the hook events of the character (movement_mode_changed, jumped, landed, crouch_started / ended, teleported):
diagnostics, semantics, interpreter / Python consistency and the generated C++ (event sink, dispatch)."""

import unittest

import _bootstrap  # noqa: F401

import dal
from dal import harness, ir
from dal import runtime as rt
from dal.cppbackend import generate_cpp
from dal.hostapi import EVENTS, HOOK_EVENTS
from dal.parser import HANDLER_EVENTS

ACTOR = "logic T : actor;\n"


def compile_ok(src):
    return dal.check_source(src, "t.dal").module


def errors(src):
    try:
        dal.check_source(src, "t.dal")
    except dal.DalError as e:
        return [(d.code, d.pos.line, d.pos.col) for d in e.diagnostics if d.severity == "error"]
    return []


def run_both(src, frames):
    """Interpreter and generated Python on the same frames: they must agree; the Python result is returned."""
    module = compile_ok(src)
    py = harness.run_python(module, frames)
    it = harness.run_interpreter(module, frames)
    assert harness.compare(py, it) == [], harness.compare(py, it)[:3]
    return py


def fr(dt=0.1, **kw):
    kw["dt"] = dt
    return kw


TIMER = ACTOR + """
timer Reset = 0.25;
state Fired: int;
state Phase: int;
state Armed: bool;
on timer Reset { Fired += 1; host.set_braking_deceleration_walking(7.0); return; }
on timer Reset { Phase += 10; start(Reset, 0.3); }
update(dt) { if (!Armed) { Armed = true; start(Reset); } }
"""


class TimerTests(unittest.TestCase):
    def test_the_handler_runs_once_when_the_countdown_reaches_zero(self):
        out = run_both(TIMER, [fr() for _ in range(12)])
        self.assertEqual([f["Fired"] for f in out], [0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3])
        # the second handler re-armed the timer with 0.3 s: three updates of 0.1 s per round
        self.assertEqual([f["Phase"] for f in out][3], 10)
        self.assertEqual([len(f["__cmds__"]) for f in out], [0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0])

    def test_handlers_run_in_declaration_order_and_return_only_ends_their_own_body(self):
        out = run_both(TIMER, [fr() for _ in range(4)])
        self.assertEqual((out[3]["Fired"], out[3]["Phase"]), (1, 10))          # `return;` in the first did not skip the second

    def test_stop_does_not_fire_and_a_zero_duration_never_fires(self):
        src = ACTOR + "timer Tm = 0.15;\nstate N: int;\nstate Go: int;\non timer Tm { N += 1; }\nupdate(dt) { if (Go == 0) { start(Tm); } if (Go == 1) { stop(Tm); } Go += 1; }"
        out = run_both(src, [fr() for _ in range(6)])
        self.assertEqual([f["N"] for f in out], [0] * 6)
        src = ACTOR + "timer Tm;\nstate N: int;\non timer Tm { N += 1; }\nupdate(dt) { start(Tm, 0.0); }"
        self.assertEqual([f["N"] for f in run_both(src, [fr() for _ in range(3)])], [0, 0, 0])

    def test_the_timer_state_is_zero_while_the_handler_runs(self):
        src = ACTOR + "timer Tm = 0.1;\nstate Seen: float;\nstate Go: bool;\non timer Tm { Seen = Tm.remaining + 5.0; }\nupdate(dt) { if (!Go) { Go = true; start(Tm); } }"
        out = run_both(src, [fr(0.1), fr(0.1), fr(0.1)])
        self.assertEqual(out[1]["Seen"], 5.0)

    def test_an_animation_instance_has_timers_too(self):
        src = "logic T;\ntimer T1 = 0.2;\nstate N: int;\nstate Go: bool;\non timer T1 { N += 1; }\nupdate(dt) { if (!Go) { Go = true; start(T1); } }"
        out = run_both(src, [fr(0.1) for _ in range(5)])
        self.assertEqual([f["N"] for f in out], [0, 0, 1, 1, 1])

    def test_the_timer_runs_before_the_events_of_the_update(self):
        src = (ACTOR + "timer Tm = 0.1;\nstate Order: int;\nstate Go: bool;\n"
               "on timer Tm { Order = Order * 10 + 1; }\non notify X { Order = Order * 10 + 2; }\n"
               "update(dt) { if (!Go) { Go = true; start(Tm); } }")
        out = run_both(src, [fr(0.1), fr(0.1, events=[("notify", "X")])])
        self.assertEqual(out[1]["Order"], 12)

    def test_diagnostics(self):
        self.assertEqual(errors(ACTOR + "on timer Nope { }"), [("DAL3063", 2, 10)])
        self.assertEqual(errors(ACTOR + "window W;\non timer W { }"), [("DAL3063", 3, 10)])
        self.assertEqual(errors(ACTOR + "action A: bool;\nbuffer B = 0.2 <- input A started;\non timer B { }"), [("DAL3063", 4, 10)])
        self.assertEqual(errors(ACTOR + "timer Tm = 1.0;\non timer { }"), [("DAL2001", 3, 10)])
        self.assertEqual(errors(ACTOR + "timer Tm = 1.0;\nstate OnTm: int;\non timer Tm { }")[0:1], [("DAL3001", 4, 10)])   # the generated method name is taken

    def test_a_program_with_only_timer_handlers_compiles_without_an_update(self):
        m = compile_ok(ACTOR + "timer Tm = 1.0;\nstate N: int;\non timer Tm { N += 1; }")
        self.assertEqual(len(m.updates), 1)
        self.assertEqual([x.name for x in m.methods], ["OnTm"])

    def test_timers_of_programs_without_handlers_lower_exactly_as_before(self):
        m = compile_ok(ACTOR + "timer Tm = 1.0;\nstate N: int;\nupdate(dt) { start(Tm); }")
        self.assertEqual(m.methods, [])
        self.assertNotIn("method", ir.dump(m))


HOOKS = ACTOR + """
state Mode: MovementMode;
state Prev: MovementMode;
state J: int;
state Z: float;
state Crouches: int;
on movement_mode_changed(prev, cur) { Prev = prev; Mode = cur; if (cur == MovementMode.Falling) { host.jump(); } }
on jumped { J += 1; }
on landed(h) { Z = h.point.z + h.distance; }
on crouch_started { Crouches += 1; }
on crouch_ended { Crouches += 100; }
on teleported { J += 1000; }
"""


class HookTests(unittest.TestCase):
    def test_the_payload_reaches_the_handler(self):
        hit = rt.Hit(True, (1.0, 2.0, 3.0), (0.0, 0.0, 1.0), 5.0, 0.5)
        out = run_both(HOOKS, [
            fr(events=[("movement_mode_changed", 1, 3), ("jumped",)]),
            fr(events=[("landed", hit)]),
            fr(events=[("crouch_started",), ("teleported",), ("crouch_ended",)]),
            fr(events=[("movement_mode_changed", 3)]),                    # a host that leaves the last value out: it reads as zero
        ])
        self.assertEqual((out[0]["Mode"], out[0]["Prev"], out[0]["J"]), (3, 1, 1))
        self.assertEqual(out[0]["__cmds__"], (("jump", ()),))
        self.assertEqual(out[1]["Z"], 8.0)
        self.assertEqual((out[2]["J"], out[2]["Crouches"]), (1001, 101))
        self.assertEqual((out[3]["Mode"], out[3]["Prev"]), (0, 3))

    def test_physics_rotation_carries_the_delta_time(self):
        src = ACTOR + "state Tm: float;\nstate N: int;\non physics_rotation(d) { Tm += d; N += 1; }"
        out = run_both(src, [fr(events=[("physics_rotation", 0.25)]), fr(events=[("physics_rotation", 0.5), ("physics_rotation", 0.125)]), fr()])
        self.assertEqual([f["Tm"] for f in out], [0.25, 0.875, 0.875])
        self.assertEqual([f["N"] for f in out], [1, 3, 3])
        out = run_both(ACTOR + "state N: int;\non physics_rotation { N += 1; }", [fr(events=[("physics_rotation",)])])      # a host that leaves the value out
        self.assertEqual(out[0]["N"], 1)
        self.assertEqual(errors(ACTOR + "on physics_rotation(a, b) { }"), [("DAL4041", 2, 21)])

    def test_a_handler_without_a_parameter_list_ignores_the_payload(self):
        out = run_both(ACTOR + "state N: int;\non movement_mode_changed { N += 1; }\non landed { N += 10; }",
                       [fr(events=[("movement_mode_changed", 1, 3), ("landed", rt.Hit(True))])])
        self.assertEqual(out[0]["N"], 11)

    def test_events_and_hooks_keep_their_order(self):
        src = ACTOR + "state Log: int;\non notify A { Log = Log * 10 + 1; }\non jumped { Log = Log * 10 + 2; }\non notify B { Log = Log * 10 + 3; }"
        out = run_both(src, [fr(events=[("notify", "A"), ("jumped",), ("notify", "B"), ("jumped",)])])
        self.assertEqual(out[0]["Log"], 1232)                              # A, jumped, B, jumped

    def test_an_animation_instance_receives_hooks_too(self):
        out = run_both("logic T;\nstate Crouches: int;\non crouch_started { Crouches += 1; }", [fr(events=[("crouch_started",)]), fr(events=[("crouch_started",)])])
        self.assertEqual([f["Crouches"] for f in out], [1, 2])

    def test_hooks_run_in_the_slot_that_takes_the_events(self):
        src = ACTOR + "state A: int;\nstate B: int;\non jumped { A += 1; }\nupdate(dt) in pre_movement { B = A; }\nupdate(dt) in after_movement { B += 100; }"
        out = run_both(src, [fr(events=[("jumped",)])])
        self.assertEqual((out[0]["A"], out[0]["B"]), (1, 101))

    def test_diagnostics(self):
        self.assertEqual(errors(ACTOR + "on jumped(x) { }"), [("DAL4041", 2, 11)])
        self.assertEqual(errors(ACTOR + "on movement_mode_changed(a) { }"), [("DAL4041", 2, 26)])
        self.assertEqual(errors(ACTOR + "on movement_mode_changed(a, a) { }"), [("DAL3001", 2, 29)])
        self.assertEqual(errors(ACTOR + "state S: float;\non landed(S) { }"), [("DAL3005", 3, 11)])
        self.assertEqual(errors(ACTOR + "on movement_mode_changed(a, b) { let c: float = a; }"), [("DAL4002", 2, 49)])
        self.assertEqual(errors(ACTOR + "on jumpd { }"), [("DAL2021", 2, 4)])
        self.assertEqual(errors(ACTOR + "on jumped { return 1; }"), [("DAL5020", 2, 20)])
        self.assertEqual(errors(ACTOR + "state H: hit;\non landed(h) { }"), [("DAL4023", 2, 10)])

    def test_the_tables_agree(self):
        self.assertTrue(set(HOOK_EVENTS) <= set(EVENTS))
        self.assertTrue(set(HOOK_EVENTS) <= set(HANDLER_EVENTS))
        self.assertEqual(set(HOOK_EVENTS), set(rt.HOOK_ARGS))
        self.assertEqual({k: len(v) for k, v in HOOK_EVENTS.items()}, {k: len(v) for k, v in rt.HOOK_ARGS.items()})

    def test_interpreter_and_python_agree_on_random_frames(self):
        module = compile_ok(HOOKS + "timer Tm = 0.3;\nstate N: int;\non timer Tm { N += 1; }\nupdate(dt) { if (N < 100 && Tm <= 0.0) { start(Tm); } }")
        for seed in (1, 2, 3):
            self.assertEqual(harness.verify(module, 200, seed), [])
        events = [e for f in harness.random_frames(module, 200, 1) for e in f.get("events", [])]
        self.assertGreater(len({e[0] for e in events}), 4)          # the random frames really carry the hook events


class CppTests(unittest.TestCase):
    def files(self, src):
        return generate_cpp(compile_ok(src))

    def test_a_logic_with_hooks_is_an_event_sink(self):
        files = self.files(HOOKS)
        h, cpp = files["T.h"], files["T.cpp"]
        self.assertIn("class UT : public UActorComponent, public DalHost::IEventSink", h)
        self.assertIn("virtual const AActor* DalSinkOwner() const override;", h)
        self.assertIn("DalHost::FEventRouter::Register(this);", cpp)
        self.assertIn("DalHost::FEventRouter::Unregister(this);", cpp)
        self.assertIn("DalEvent.Kind == DalHost::EEventKind::MovementModeChanged", cpp)
        self.assertIn("DalHandler_0(static_cast<EMovementMode>(DalEvent.ModeA), static_cast<EMovementMode>(DalEvent.ModeB));", cpp)
        self.assertIn("DalHandler_2(DalEvent.Hit);", cpp)

    def test_an_animation_instance_registers_when_it_initialises(self):
        files = self.files("logic T;\nstate N: int;\non jumped { N += 1; }")
        self.assertIn("class UT : public UAnimInstance, public DalHost::IEventSink", files["T.h"])
        self.assertIn("return GetOwningActor();", files["T.cpp"])
        self.assertIn("virtual void NativeUninitializeAnimation() override;", files["T.h"])
        self.assertIn("DalHost::FEventRouter::Unregister(this);", files["T.cpp"])

    def test_programs_without_hooks_do_not_know_the_router(self):
        for src in (ACTOR + "state N: int;\non notify X { N += 1; }", "logic T;\nstate N: int;\nupdate(dt) { N += 1; }",
                    ACTOR + "timer Tm = 1.0;\nstate N: int;\non timer Tm { N += 1; }"):
            files = self.files(src)
            self.assertNotIn("IEventSink", files["T.h"])
            self.assertNotIn("FEventRouter", files["T.cpp"])


try:
    import test_cpp_compile as tcc
except Exception:                                   # pragma: no cover
    tcc = None


@unittest.skipIf(tcc is None or tcc.COMPILER is None, "no C++ compiler found")
class CppCompileTests(unittest.TestCase):
    """The generated C++ against the stand-in engine, events posted through the router like DalRuntime does."""

    def run_case(self, module, frames, **kw):
        return tcc.CppCompileTests("test_hero_gait_matches_python").run_case(module, frames, **kw)

    def test_hooks_match_python(self):
        module = compile_ok(HOOKS)
        effects = 0
        for seed in (1, 2, 3):
            effects += self.run_case(module, harness.random_frames(module, 150, seed), warnings_as_errors=True)
        self.assertGreater(effects, 0)

    def test_physics_rotation_matches_python(self):
        module = compile_ok(ACTOR + "state Tm: float;\nstate N: int;\non physics_rotation(d) { Tm += d; N += 1; if (N > 3) { host.jump(); } }")
        for seed in (1, 2):
            self.run_case(module, harness.random_frames(module, 80, seed), warnings_as_errors=True)
        frames = [fr(0.125, events=[("physics_rotation", 0.25)]), fr(0.125, events=[("physics_rotation", 0.5)] * 4)]
        self.assertEqual(self.run_case(module, frames, warnings_as_errors=True), 2)                  # N passes 3 twice

    def test_hooks_through_a_scripted_session(self):
        module = compile_ok(HOOKS)
        hit = rt.Hit(True, (1.0, 2.0, 3.0), (0.0, 0.0, 1.0), 5.0, 0.5)
        frames = [fr(1 / 60, events=[("movement_mode_changed", 1, 3), ("jumped",)]), fr(1 / 60, events=[("landed", hit)]),
                  fr(1 / 60, events=[("crouch_started",), ("teleported",), ("crouch_ended",)]), fr(1 / 60)]
        self.assertEqual(self.run_case(module, frames, warnings_as_errors=True), 1)       # the jump the fall asked for

    def test_an_animation_instance_with_hooks_and_timers_matches_python(self):
        module = compile_ok("logic T;\ntimer T1 = 0.05;\nstate N: int;\nstate M: MovementMode;\nstate G: bool;\n"
                            "on timer T1 { N += 1; host.crouch(); }\non movement_mode_changed(a, b) { M = b; }\non landed { N += 100; }\n"
                            "update(dt) { if (!G) { G = true; start(T1); } }")
        for seed in (1, 2):
            self.run_case(module, harness.random_frames(module, 120, seed), warnings_as_errors=True)

    def test_timer_handlers_of_an_actor_match_python(self):
        module = compile_ok(TIMER.replace("start(Reset, 0.3)", "start(Reset, 0.375)"))          # binary fractions: float and double count down alike
        effects = self.run_case(module, [fr(0.125) for _ in range(40)], warnings_as_errors=True)
        self.assertGreaterEqual(effects, 5)


if __name__ == "__main__":
    unittest.main()
