"""Language v1 / v2: actor logic, host commands, queries, events, montages, loops and the sugar
(timers, windows, buffers, combos) -- diagnostics, semantics and interpreter / Python consistency."""

import unittest

import _bootstrap  # noqa: F401

import dal
from dal import harness, ir
from dal import runtime as rt
from dal.mockhost import MockHost
from dal.pybackend import generate_python, load_class

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
    """Run the generated Python and the interpreter on the same frames; they must agree; return the Python result."""
    module = compile_ok(src)
    py = harness.run_python(module, frames)
    it = harness.run_interpreter(module, frames)
    assert harness.compare(py, it) == [], harness.compare(py, it)[:3]
    return py


def fr(dt=0.125, **kw):
    kw["dt"] = dt
    return kw


class DiagnosticTests(unittest.TestCase):
    def check(self, src, *expected):
        with self.subTest(src=src):
            self.assertEqual(errors(src), list(expected), src)

    def test_logic_kind(self):
        self.assertEqual(compile_ok("logic T;\nupdate(dt) { }").kind, "anim")
        self.assertEqual(compile_ok("logic T : anim;\nupdate(dt) { }").kind, "anim")
        self.assertEqual(compile_ok("logic T : actor;\nupdate(dt) { }").kind, "actor")
        self.check("logic T : thing;", ("DAL2020", 1, 11))

    def test_actor_only_features(self):
        self.check("logic T;\naction A: bool;\non input A started { }", ("DAL5040", 3, 1))
        self.check("logic T;\nstate S: float;\nupdate(dt) { let h = host.trace_line(vec3(0.0, 0.0, 0.0), vec3(1.0, 0.0, 0.0)); S = h.distance; }", ("DAL5041", 3, 22))
        # but an anim instance may queue commands and handle notifies and montage events
        compile_ok("logic T;\nmontage M;\non notify Step { host.jump(); }\non montage_ended M { host.montage_stop(M); }\nupdate(dt) { host.set_max_walk_speed(1.0); }")

    def test_host_commands(self):
        self.check(ACTOR + "update(dt) { host.jumpp(); }", ("DAL3060", 2, 19))
        self.check(ACTOR + "update(dt) { host.jump(1.0); }", ("DAL4041", 2, 19))
        self.check(ACTOR + "state S: float;\nupdate(dt) { host.montage_play(S); }", ("DAL4024", 3, 32))
        self.check(ACTOR + "montage M { A }\nmontage N { C }\nupdate(dt) { host.montage_play(M, 1.0, N.C); }", ("DAL4025", 4, 40))
        self.check(ACTOR + "fn f() -> float { host.jump(); return 1.0; }", ("DAL5042", 2, 24))
        self.check(ACTOR + "update(dt) { host.set_max_walk_speed(true); }", ("DAL4002", 2, 38))
        self.check(ACTOR + "update(dt) { host.set_movement_mode(1); }", ("DAL4002", 2, 37))

    def test_montages_and_actions(self):
        self.check(ACTOR + "montage M { playing }", ("DAL3059", 2, 13))
        self.check(ACTOR + "montage M { A, A }", ("DAL3001", 2, 16))
        self.check(ACTOR + "montage M;\nmontage M;", ("DAL3001", 3, 9))
        self.check(ACTOR + "montage M;\nstate S: float;\nupdate(dt) { S = M; }", ("DAL3030", 4, 18))
        self.check(ACTOR + "montage M { A }\nstate S: float;\nupdate(dt) { S = M.Nope; }", ("DAL3032", 4, 20))
        self.check(ACTOR + "on montage_ended Nope { }", ("DAL3061", 2, 18))
        self.check(ACTOR + "on input Nope started { }", ("DAL3062", 2, 10))
        self.check(ACTOR + "action A: int;", ("DAL4021", 2, 11))
        self.check(ACTOR + 'input X: float <- host.montage_position("M");', ("DAL3023", 2, 41))
        self.check(ACTOR + "montage M;\ninput X: float <- host.montage_playing(M);", ("DAL4020", 3, 10))
        self.check(ACTOR + "action A: bool;\naction B: vec2;\ninput X: vec2 <- host.action(A);", ("DAL4022", 4, 30))
        self.check(ACTOR + 'action A: bool;\ninput X: float <- host.action("A");', ("DAL4022", 3, 31))

    def test_hit_values(self):
        self.check(ACTOR + "state S: hit;", ("DAL4023", 2, 10))
        self.check(ACTOR + "state S: float;\nupdate(dt) { let h = host.trace_line(vec3(0.0, 0.0, 0.0), vec3(1.0, 0.0, 0.0)); S = h.point; }", ("DAL4002", 3, 85))
        self.check(ACTOR + "update(dt) { let h = host.trace_line(vec3(0.0, 0.0, 0.0), vec3(1.0, 0.0, 0.0)); let x = h.nope; }", ("DAL4030", 2, 91))
        compile_ok(ACTOR + "fn depth(h: hit) -> float { return h.distance; }\nupdate(dt) { }")
        self.check(ACTOR + "fn f() -> float { return host.target_count(); }", ("DAL5042", 2, 26))
        self.check(ACTOR + "update(dt) { let n = host.target_count(1); }", ("DAL4041", 2, 22))

    def test_loops(self):
        self.check(ACTOR + "state S: int;\nupdate(dt) { for i in 0..100 { S += i; } }", ("DAL5050", 3, 14))
        self.check(ACTOR + "state S: int;\nupdate(dt) { for i in 5..2 { S += i; } }", ("DAL5051", 3, 14))
        self.check(ACTOR + "state S: int;\nupdate(dt) { for i in 0..2.5 { S += i; } }", ("DAL4026", 3, 26))
        self.check(ACTOR + "state S: int;\nupdate(dt) { for S in 0..2 { } }", ("DAL3005", 3, 18))
        self.check(ACTOR + "state N: int;\nconst K: int = 3;\nupdate(dt) { for i in 0..K { N += i; } for i in 1..K { N += i; } }")

    def test_sugar(self):
        self.check(ACTOR + "timer T;\nupdate(dt) { start(T); }", ("DAL3057", 3, 14))
        self.check(ACTOR + "update(dt) { frobnicate(1.0); }", ("DAL2011", 2, 14))
        self.check(ACTOR + "timer T = 1.0;\nupdate(dt) { open(T); }", ("DAL3056", 3, 14))
        self.check(ACTOR + "timer T = 1.0;\nstate S: float;\nupdate(dt) { S = T.nope; }", ("DAL3058", 4, 20))
        self.check(ACTOR + "timer T = 1.0;\ntimer T = 2.0;", ("DAL3001", 3, 1))
        self.check(ACTOR + "montage M;\naction A: bool;\nbuffer B = 0.2 <- input A started;\ncombo C { start on B -> Nope; step X { play M; } }", ("DAL3055", 5, 25))
        self.check(ACTOR + "state F: float;\ncombo C { start on F -> X; step X { } }", ("DAL3053", 3, 20))
        self.check(ACTOR + "combo C { step Idle { } }", ("DAL3052", 2, 16))
        self.check(ACTOR + "action A: bool;\nbuffer B = 0.2 <- input A started;\ncombo C { start -> X; step X { } }", ("DAL2001", 4, 17))

    def test_hidden_montage_inputs(self):
        m = compile_ok(ACTOR + "montage M { A, B }\nstate S: bool;\nupdate(dt) { S = M.playing && M.section == M.B; }")
        names = [i.name for i in m.inputs]
        self.assertEqual(names, ["DalMontage_M_playing", "DalMontage_M_section"])
        self.assertEqual(m.inputs[1].host.item, "montage_section")
        self.assertEqual(m.inputs[1].ty.name, "MSection")

    def test_command_defaults_are_filled_in(self):
        m = compile_ok(ACTOR + "montage M { A }\nupdate(dt) { host.montage_play(M); host.montage_stop(M); host.montage_stop_all(); host.add_movement_input(vec3(0.0, 1.0, 0.0)); host.launch(vec3(0.0, 0.0, 1.0)); }")
        text = ir.dump(m)
        self.assertIn("host.montage_play(montage:M, 1.0, MSection.None)", text)
        self.assertIn("host.montage_stop(montage:M, -1.0)", text)
        self.assertIn("host.montage_stop_all(-1.0)", text)
        self.assertIn("host.launch(vec3[0](0.0, 0.0, 1.0), False, False)", text)

    def test_v0_programs_are_unchanged(self):
        """A program without v1 constructs produces no v1 baggage in the generated Python."""
        src = "logic Old;\ninput V: vec3 <- host.velocity;\nstate S: float;\nupdate(dt) { S = length(V); }\n"
        text = generate_python(compile_ok(src))
        for word in ("_cmd", "_flush", "_dispatch", "DAL_HANDLERS", "_pending", "DAL_KIND"):
            self.assertNotIn(word, text)


class CommandTests(unittest.TestCase):
    SRC = ACTOR + """
montage M { Windup, Strike }
state Count: int;
update(dt) {
    Count += 1;
    host.set_max_walk_speed(300.0);
    host.add_movement_input(vec3(0.0, 1.0, 0.0));
    host.launch(vec3(0.0, 0.0, 500.0), false, true);
    host.set_movement_mode(MovementMode.Falling);
    host.montage_play(M);
    host.montage_play(M, 1.5, M.Strike);
    host.montage_stop(M);
    host.montage_stop(M, 0.25);
    host.montage_stop_all();
    host.montage_jump_to_section(M, M.Windup);
    host.montage_set_next_section(M, M.Windup, M.Strike);
    host.montage_set_play_rate(M, 2);
    host.set_rotation_rate(rotator(0.0, 540.0, 0.0));
    host.camera_set_socket_offset(vec3(1.0, 2.0, 3.0));
    host.set_orient_rotation_to_movement(true);
    host.jump();
    host.crouch();
}
"""

    def test_commands_reach_the_host_in_order_with_plain_arguments(self):
        out = run_both(self.SRC, [fr(), fr()])
        calls = out[0]["__cmds__"]
        self.assertEqual([c[0] for c in calls], [
            "set_max_walk_speed", "add_movement_input", "launch", "set_movement_mode", "montage_play", "montage_play",
            "montage_stop", "montage_stop", "montage_stop_all", "montage_jump_to_section", "montage_set_next_section",
            "montage_set_play_rate", "set_rotation_rate", "camera_set_socket_offset", "set_orient_rotation_to_movement",
            "jump", "crouch"])
        by = {}
        for name, args in calls:
            by.setdefault(name, []).append(args)
        self.assertEqual(by["set_max_walk_speed"], [(300.0,)])
        self.assertEqual(by["add_movement_input"], [((0.0, 1.0, 0.0), 1.0)])
        self.assertEqual(by["launch"], [((0.0, 0.0, 500.0), False, True)])
        self.assertEqual(by["set_movement_mode"], [(3,)])
        self.assertEqual(by["montage_play"], [("M", 1.0, None), ("M", 1.5, "Strike")])
        self.assertEqual(by["montage_stop"], [("M", -1.0), ("M", 0.25)])
        self.assertEqual(by["montage_stop_all"], [(-1.0,)])
        self.assertEqual(by["montage_jump_to_section"], [("M", "Windup")])
        self.assertEqual(by["montage_set_next_section"], [("M", "Windup", "Strike")])
        self.assertEqual(by["montage_set_play_rate"], [("M", 2.0)])
        self.assertEqual(by["set_rotation_rate"], [((0.0, 540.0, 0.0),)])
        self.assertEqual(by["set_orient_rotation_to_movement"], [(True,)])
        self.assertEqual(out[1]["__cmds__"], out[0]["__cmds__"])         # every update issues them again

    def test_commands_are_not_executed_inside_the_update(self):
        """The host sees them only after update() returns: the logic cannot observe their effect in the same frame."""
        module = compile_ok(ACTOR + "state Seen: bool;\nupdate(dt) { host.set_max_walk_speed(10.0); }")
        host = MockHost()
        logic = load_class(module)(host)
        logic.update(0.1)
        self.assertEqual(host.calls, [("set_max_walk_speed", (10.0,))])
        self.assertEqual(host.state["max_walk_speed"], 10.0)

    def test_entries_flush_their_commands(self):
        module = compile_ok(ACTOR + "entry Kick(dt) { host.jump(); }\nupdate(dt) { }")
        host = MockHost()
        logic = load_class(module)(host)
        logic.update(0.1)
        self.assertEqual(host.calls, [])
        logic.call_entry("Kick")
        self.assertEqual(host.calls, [("jump", ())])

    def test_anim_logic_can_queue_commands_and_handle_events(self):
        src = "logic A;\nmontage M;\nstate N: int;\non notify Step { N += 1; host.set_gravity_scale(0.5); }\nupdate(dt) { if (N > 1) { host.montage_stop(M); } }\n"
        out = run_both(src, [fr(events=[("notify", "Step")]), fr(events=[("notify", "Step"), ("notify", "Step")]), fr()])
        self.assertEqual([o["N"] for o in out], [1, 3, 3])
        self.assertEqual(out[0]["__cmds__"], (("set_gravity_scale", (0.5,)),))
        self.assertEqual([c[0] for c in out[1]["__cmds__"]], ["set_gravity_scale", "set_gravity_scale", "montage_stop"])


class EventTests(unittest.TestCase):
    SRC = ACTOR + """
montage M { A }
action Attack: bool;
action Move: vec2;
state Log: int;
state Interrupted: bool;
state Last: vec2;
on notify Foo { Log = Log * 10 + 1; }
on notify Foo { Log = Log * 10 + 2; }
on notify_begin Win { Log = Log * 10 + 3; }
on notify_end Win { Log = Log * 10 + 4; }
on montage_ended M { Log = Log * 10 + 5; Interrupted = interrupted; }
on montage_blending_out M { Log = Log * 10 + 6; }
on input Attack started { Log = Log * 10 + 7; }
on input Attack completed { Log = Log * 10 + 8; }
on input Move triggered { Last = value; }
update(dt) { }
"""

    def test_handlers_run_in_event_order_then_declaration_order(self):
        out = run_both(self.SRC, [fr(events=[("notify", "Foo"), ("input", "Attack", "started", True), ("notify", "Nope"), ("montage_ended", "M", True)])])
        self.assertEqual(out[0]["Log"], 1275)
        self.assertIs(out[0]["Interrupted"], True)

    def test_all_event_kinds(self):
        events = [("notify_begin", "Win"), ("notify_end", "Win"), ("montage_blending_out", "M", False), ("input", "Attack", "completed", False),
                  ("input", "Move", "triggered", (0.5, -1.0)), ("input", "Attack", "ongoing", True), ("montage_ended", "M", False)]
        out = run_both(self.SRC, [fr(events=events)])
        self.assertEqual(out[0]["Log"], 34685)
        self.assertEqual(out[0]["Last"], (0.5, -1.0))
        self.assertIs(out[0]["Interrupted"], False)

    def test_events_are_delivered_once(self):
        out = run_both(self.SRC, [fr(events=[("notify", "Foo")]), fr(), fr()])
        self.assertEqual([o["Log"] for o in out], [12, 12, 12])

    def test_post_event_api(self):
        module = compile_ok(self.SRC)
        logic = load_class(module)(MockHost())
        logic.post_event("notify", "Foo")
        logic.post_event("input", "Move", "triggered", (1.0, 2.0))
        logic.update(0.1)
        self.assertEqual(logic.Log, 12)
        self.assertEqual(logic.Last, rt.Vec2(1.0, 2.0))
        logic.update(0.1)
        self.assertEqual(logic.Log, 12)

    def test_handlers_see_the_input_snapshot_and_may_issue_commands(self):
        src = ACTOR + "input G: bool <- host.is_grounded;\naction A: bool;\nstate Hits: int;\non input A started { if (G) { Hits += 1; host.jump(); } }\nupdate(dt) { }\n"
        out = run_both(src, [fr(is_grounded=True, events=[("input", "A", "started", True)]), fr(is_grounded=False, events=[("input", "A", "started", True)])])
        self.assertEqual([o["Hits"] for o in out], [1, 1])
        self.assertEqual([len(o["__cmds__"]) for o in out], [1, 0])

    def test_logic_without_update_still_handles_events(self):
        out = run_both(ACTOR + "state N: int;\non notify X { N += 1; }\n", [fr(events=[("notify", "X")]), fr(events=[("notify", "X")])])
        self.assertEqual([o["N"] for o in out], [1, 2])


class TimerTests(unittest.TestCase):
    SRC = ACTOR + """
action Go: bool;
timer Cooldown = 0.5;
state Ready: bool;
state Active: bool;
state Rem: float;
state Count: int;
on input Go started { if (Cooldown.ready) { start(Cooldown); Count += 1; } }
update(dt) { Ready = Cooldown.ready; Active = Cooldown.active; Rem = Cooldown.remaining; }
"""

    def test_cooldown(self):
        go = ("input", "Go", "started", True)
        frames = [fr(events=[go]), fr(events=[go]), fr(), fr(), fr(), fr(events=[go]), fr()]
        out = run_both(self.SRC, frames)
        # frame 0 starts the timer (timers tick before handlers, so it starts at the full 0.5); frame 1's press is refused
        self.assertEqual([o["Rem"] for o in out], [0.5, 0.375, 0.25, 0.125, 0.0, 0.5, 0.375])
        self.assertEqual([o["Count"] for o in out], [1, 1, 1, 1, 1, 2, 2])
        self.assertEqual([o["Ready"] for o in out], [False, False, False, False, True, False, False])
        self.assertEqual([o["Active"] for o in out], [True, True, True, True, False, True, True])
        # a press in the very frame the timer runs out is accepted (the tick comes first)
        out = run_both(self.SRC, [fr(events=[go])] + [fr() for _ in range(3)] + [fr(events=[go])])
        self.assertEqual([o["Count"] for o in out], [1, 1, 1, 1, 2])

    def test_start_with_a_duration_and_stop(self):
        src = ACTOR + "timer Tm;\naction A: bool;\naction B: bool;\non input A started { start(Tm, 2.0); }\non input B started { stop(Tm); }\nupdate(dt) { }\n"
        out = run_both(src, [fr(events=[("input", "A", "started", True)]), fr(), fr(events=[("input", "B", "started", True)]), fr()])
        self.assertEqual([o["Tm"] for o in out], [2.0, 1.875, 0.0, 0.0])

    def test_timer_is_a_plain_float_state_for_the_graph(self):
        m = compile_ok(self.SRC)
        self.assertEqual(m.find_state("Cooldown").ty.name, "float")


class WindowBufferTests(unittest.TestCase):
    SRC = ACTOR + """
montage M;
action Attack: bool;
window Cancel <- notify_state CancelWindow closes_on M;
window Manual;
buffer Buf = 0.375 <- input Attack started;
buffer Gated = 0.25 <- input Attack started when Cancel;
state Took: int;
update(dt) {
    if (Buf.pending && Cancel) { consume(Buf); Took += 1; }
    if (Gated.pending) { open(Manual); } else { close(Manual); }
}
"""

    def test_window_follows_the_notify_state(self):
        out = run_both(self.SRC, [fr(), fr(events=[("notify_begin", "CancelWindow")]), fr(), fr(events=[("notify_end", "CancelWindow")]), fr()])
        self.assertEqual([o["Cancel"] for o in out], [False, True, True, False, False])

    def test_window_closes_when_its_montage_ends(self):
        out = run_both(self.SRC, [fr(events=[("notify_begin", "CancelWindow")]), fr(events=[("montage_ended", "M", True)])])
        self.assertEqual([o["Cancel"] for o in out], [True, False])

    def test_buffer_keeps_a_press_until_it_can_be_used(self):
        press = ("input", "Attack", "started", True)
        open_ = ("notify_begin", "CancelWindow")
        # press in frame 0 (window closed): the buffer lasts 0.375 s = 3 frames; the window opens in frame 2 -> used there
        out = run_both(self.SRC, [fr(events=[press]), fr(), fr(events=[open_]), fr(), fr()])
        self.assertEqual([o["Took"] for o in out], [0, 0, 1, 1, 1])
        self.assertEqual([o["Buf"] for o in out], [0.375, 0.25, 0.0, 0.0, 0.0])          # consumed in frame 2
        # window opens after the buffer ran out: the press is lost
        out = run_both(self.SRC, [fr(events=[press]), fr(), fr(), fr(), fr(events=[open_])])
        self.assertEqual(out[-1]["Took"], 0)

    def test_buffer_condition_gates_filling(self):
        press = ("input", "Attack", "started", True)
        out = run_both(self.SRC, [fr(events=[press]), fr(events=[("notify_begin", "CancelWindow")]), fr(events=[press])])
        self.assertEqual([o["Gated"] for o in out], [0.0, 0.0, 0.25])
        self.assertEqual([o["Manual"] for o in out], [False, False, True])


class MontageReadTests(unittest.TestCase):
    SRC = ACTOR + """
montage M { Windup, Strike }
state P: bool;
state Pos: float;
state InStrike: bool;
state Act: bool;
state Sec: MSection;
update(dt) { P = M.playing; Pos = M.position; InStrike = M.section == M.Strike; Act = M.active; Sec = M.section; }
"""

    def test_montage_state_is_sampled_like_an_input(self):
        out = run_both(self.SRC, [
            fr(),
            fr(montages={"M": {"playing": True, "active": True, "position": 0.4, "section": "Strike"}}),
            fr(montages={"M": {"playing": False, "active": True, "position": 0.9, "section": None}}),
        ])
        self.assertEqual([o["P"] for o in out], [False, True, False])
        self.assertEqual([o["Pos"] for o in out], [0.0, 0.4, 0.9])
        self.assertEqual([o["InStrike"] for o in out], [False, True, False])
        self.assertEqual([o["Act"] for o in out], [False, True, True])
        self.assertEqual([o["Sec"] for o in out], [0, 2, 0])

    def test_unknown_section_names_read_as_none(self):
        out = run_both(self.SRC, [fr(montages={"M": {"playing": True, "section": "Mystery"}})])
        self.assertEqual(out[0]["Sec"], 0)


class QueryTests(unittest.TestCase):
    SRC = ACTOR + """
state Blocked: bool;
state D: float;
state Pt: vec3;
state N: vec3;
state SphereD: float;
state SpherePt: vec3;
state SphereN: vec3;
state Count: int;
state Id: int;
state Loc: vec3;
state Back: int;
update(dt) {
    let h = host.trace_line(vec3(500.0, 0.0, 100.0), vec3(500.0, 0.0, -100.0));
    Blocked = h.blocking; D = h.distance; Pt = h.point; N = h.normal;
    let s = host.trace_sphere(vec3(-200.0, 0.0, 50.0), vec3(200.0, 0.0, 50.0), 10.0);
    SphereD = s.distance; SpherePt = s.point; SphereN = s.normal;
    Count = host.target_count();
    if (Count > 0) { Id = host.target_id(0); Loc = host.target_location(0); Back = host.target_index(Id); }
}
"""

    def test_traces_and_targets(self):
        world = {"ground_z": 0.0, "spheres": [((0.0, 0.0, 50.0), 30.0)]}
        out = run_both(self.SRC, [fr(world=world, targets=[(9, (10.0, 20.0, 30.0)), (4, (0.0, 0.0, 0.0))]), fr()])
        o = out[0]
        self.assertIs(o["Blocked"], True)
        self.assertAlmostEqual(o["D"], 100.0)
        self.assertEqual(o["Pt"], (500.0, 0.0, 0.0))
        self.assertEqual(o["N"], (0.0, 0.0, 1.0))
        self.assertAlmostEqual(o["SphereD"], 160.0)                  # stops 30 + 10 before the obstacle's centre
        self.assertEqual(o["SphereN"], (-1.0, 0.0, 0.0))
        self.assertEqual(o["SpherePt"], (-30.0, 0.0, 50.0))
        self.assertEqual((o["Count"], o["Id"], o["Loc"], o["Back"]), (2, 9, (10.0, 20.0, 30.0), 0))
        self.assertEqual((out[1]["Blocked"], out[1]["Count"]), (False, 0))

    def test_a_clear_trace_reports_no_hit(self):
        out = run_both(self.SRC, [fr()])
        self.assertIs(out[0]["Blocked"], False)
        self.assertEqual(out[0]["D"], 0.0)


class LoopTests(unittest.TestCase):
    def test_for_loops(self):
        src = ACTOR + "state Sum: int;\nstate Count: int;\nconst K: int = 4;\nupdate(dt) { Sum = 0; for i in 0..K { Sum += i; for j in 1..3 { Count += j; } } }\n"
        out = run_both(src, [fr(), fr()])
        self.assertEqual([o["Sum"] for o in out], [6, 6])
        self.assertEqual([o["Count"] for o in out], [12, 24])

    def test_empty_range(self):
        out = run_both(ACTOR + "state N: int;\nupdate(dt) { for i in 3..3 { N += 1; } }\n", [fr()])
        self.assertEqual(out[0]["N"], 0)

    def test_lock_on_selection(self):
        src = ACTOR + """
state Best: int = -1;
state BestScore: float;
fn score(origin: vec3, forward: vec3, candidate: vec3) -> float {
    let to = candidate - origin;
    let angle = angle_between(forward, to);
    if (angle > 60.0 || length(to) > 1000.0) { return 1000000.0; }
    return angle / 60.0 + length(to) / 1000.0 * 0.25;
}
update(dt) {
    var best = -1;
    var best_score = 1000000.0;
    for i in 0..8 {
        if (i < host.target_count()) {
            let s = score(vec3(0.0, 0.0, 0.0), vec3(1.0, 0.0, 0.0), host.target_location(i));
            if (s < best_score) { best_score = s; best = i; }
        }
    }
    Best = best;
    BestScore = best_score;
}
"""
        targets = [(1, (500.0, 400.0, 0.0)), (2, (300.0, 20.0, 0.0)), (3, (-200.0, 0.0, 0.0)), (4, (900.0, 0.0, 0.0))]
        out = run_both(src, [fr(targets=targets), fr(targets=[(3, (-200.0, 0.0, 0.0))]), fr()])
        self.assertEqual([o["Best"] for o in out], [1, -1, -1])
        self.assertLess(out[0]["BestScore"], 1.0)
        self.assertEqual(out[1]["BestScore"], 1000000.0)


class RandomConsistencyTests(unittest.TestCase):
    KITCHEN_SINK = ACTOR + """
montage A { Windup, Strike, Recover }
montage B;
action Attack: bool;
action Move: vec2;
param Speed: float = 400.0;
input G: bool <- host.is_grounded;
input V: vec3 <- host.velocity;
input MoveIn: vec2 <- host.action(Move);
timer Cool = 0.25;
window Open <- notify_state Win closes_on A;
buffer Buf = 0.2 <- input Attack started when G;
state Hits: int;
state Best: int = -1;
state Aim: rotator;
state Tag: int;
combo C {
    reset when !G;
    start on Buf when Cool.ready -> One;
    step One {
        play A rate 1.1 from A.Strike;
        on Buf within Open -> Two;
        after 0.5 -> Idle;
        exit { start(Cool); }
    }
    step Two { play B; enter { Hits += 1; } }
}
on notify Hit { Hits += 1; }
on montage_blending_out A { Tag = Tag + 1; }
on input Move triggered { Aim = look_at(vec3(0.0, 0.0, 0.0), vec3(value.x, value.y, 0.0)); }
update(dt) {
    if (A.playing && A.section == A.Recover) { host.montage_stop(A, 0.1); }
    var best = -1;
    for i in 0..4 {
        if (i < host.target_count() && host.target_id(i) > 10) { best = i; }
    }
    Best = best;
    let h = host.trace_sphere(V, V + vec3(0.0, 0.0, -100.0), 5.0);
    if (h.blocking) { host.add_movement_input(h.normal, h.time); }
    host.set_control_rotation(Aim);
}
"""

    def test_interpreter_and_python_agree_on_random_frames(self):
        module = compile_ok(self.KITCHEN_SINK)
        for seed in (1, 2, 3, 4):
            frames = harness.random_frames(module, 300, seed)
            self.assertEqual(harness.compare(harness.run_interpreter(module, frames), harness.run_python(module, frames)), [], seed)

    def test_the_random_frames_exercise_commands_and_events(self):
        module = compile_ok(self.KITCHEN_SINK)
        frames = harness.random_frames(module, 300, 1)
        out = harness.run_python(module, frames)
        names = {c[0] for o in out for c in o["__cmds__"]}
        self.assertTrue({"montage_play", "montage_stop", "set_control_rotation"} <= names, names)
        self.assertTrue(any(o["Hits"] > 0 for o in out))
        self.assertTrue(any(o["C"] != 0 for o in out))

    def test_melee_example(self):
        import os
        from _bootstrap import EXAMPLES

        module = dal.check_file(os.path.join(EXAMPLES, "melee_combo.dal")).module
        for seed in (1, 2):
            frames = harness.random_frames(module, 300, seed)
            self.assertEqual(harness.compare(harness.run_interpreter(module, frames), harness.run_python(module, frames)), [], seed)


if __name__ == "__main__":
    unittest.main()
