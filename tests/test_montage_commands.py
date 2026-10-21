"""Dynamic montages, slots, notify sources and pose snapshots: ``montage`` values as command arguments, ``montage_set_position``,
``montage_stop_source``, ``play_slot_animation``, ``stop_montages_with_slot``, the asset query ``montage_is_playing`` and
``host.snapshot_pose(State)`` with the ``snapshot`` value.  Diagnostics, the interpreter / Python agreement, the simulated host,
the generated C++ and the stand-in engine compile."""

import unittest

import _bootstrap  # noqa: F401
from _bootstrap import EXAMPLES  # noqa: F401

import dal
from dal import harness, hostapi
from dal.cppbackend import generate_cpp
from dal.mockhost import SimHost, SimMontage
from dal.pybackend import generate_python, load_class

try:
    import test_cpp_compile as tcc
except Exception:                                   # pragma: no cover
    tcc = None

SETTINGS = "settings Moves {\n    Attack: montage;\n    Turn: sequence;\n    Blend: float = 0.2;\n}\n"
DATA = {"Attack": "M", "Turn": "Seq"}

NOTIFY = "notify_state EarlyOut branching { Duration: float = 0.25; }\n"

PROGRAM = SETTINGS + NOTIFY + """
logic Mc : anim post_flush;
param Cfg: Moves;
input Want: bool;
input Go: float;
state Played: int;
state Pose: snapshot;

on notify_begin EarlyOut {
    if (Want) {
        host.montage_stop_source(payload.Duration);
    }
}

update(dt) {
    if (Go > 0.5) {
        host.montage_play(Cfg.Attack, 1.5);
        host.montage_set_play_rate(Cfg.Attack, 2.0);
        host.montage_set_position(Cfg.Attack, 0.25);
        host.montage_stop(Cfg.Attack, 0.1);
        host.play_slot_animation(Cfg.Turn, "Transition", 0.1, 0.2, 1.0, 1, -1.0, 0.0, MontageBlendMode.Inertialization);
        host.play_slot_animation(Cfg.Turn, "Plain");
        host.stop_montages_with_slot("Transition", 0.2);
        host.stop_montages_with_slot("Plain");
        host.snapshot_pose(Pose);
    }
    if (host.montage_is_playing(Cfg.Attack)) {
        Played += 1;
    }
}
"""


def compile_ok(src, **kw):
    return dal.check_source(src, "t.dal", **kw).module


def errors(src, **kw):
    try:
        dal.check_source(src, "t.dal", **kw)
    except dal.DalError as e:
        return [(d.code, d.pos.line, d.pos.col) for d in e.diagnostics if d.severity == "error"]
    return []


def codes(src):
    return [c for c, _l, _k in errors(src)]


def fr(dt=0.125, **kw):
    kw["dt"] = dt
    return kw


def body(stmts, kind="anim", extra=""):
    return f"{SETTINGS}{NOTIFY}logic T : {kind};\nparam Cfg: Moves;\nstate Q: float;\n{extra}\nupdate(dt) {{\n    {stmts}\n}}\n"


class DiagnosticTests(unittest.TestCase):
    def check(self, stmts, *expected, **kw):
        with self.subTest(stmts=stmts):
            self.assertEqual(codes(body(stmts, **kw)), list(expected), stmts)

    def test_a_montage_value_is_a_montage_argument(self):
        self.check("host.montage_play(Cfg.Attack, 1.5);")
        self.check("host.montage_stop(Cfg.Attack);")
        self.check("host.montage_set_position(Cfg.Attack, 0.5);")
        self.check("host.montage_play(Cfg.Turn);", "DAL4024")                              # a sequence is not a montage
        self.check("host.montage_play(Q);", "DAL4024")

    def test_a_montage_value_has_no_sections(self):
        self.check("host.montage_jump_to_section(Cfg.Attack, Cfg.Attack);", "DAL4027")
        self.check("host.montage_play(Cfg.Attack, 1.0, Cfg.Attack);", "DAL4027")

    def test_the_slot_animation_takes_a_sequence_and_a_slot_name(self):
        self.check('host.play_slot_animation(Cfg.Turn, "Transition");')
        self.check('host.play_slot_animation(Cfg.Turn, "T", 0.1, 0.2, 1.0, 2, -1.0, 0.0, MontageBlendMode.Standard);')
        self.check('host.play_slot_animation(Cfg.Attack, "T");', "DAL4002")                  # a montage is not a sequence
        self.check("host.play_slot_animation(Cfg.Turn, Q);", "DAL4061")
        self.check('host.play_slot_animation(Cfg.Turn, "");', "DAL3067")
        self.check('host.play_slot_animation(Cfg.Turn, "T", 0.1, 0.2, 1.0, 1, -1.0, 0.0, 2.0);', "DAL4002")
        self.check('host.play_slot_animation(Cfg.Turn);', "DAL4041")
        self.check('host.stop_montages_with_slot("A", 0.1, 3.0);', "DAL4041")
        self.check('host.stop_montages_with_slot("A");')

    def test_a_string_is_only_a_slot_name(self):
        self.check('host.set_max_walk_speed("x");', "DAL4061")
        self.assertEqual(errors(body('let s = "x";'))[0][0], "DAL2004")

    def test_the_source_command_belongs_in_the_handler_of_a_declared_notify(self):
        self.check("host.montage_stop_source();", "DAL5066")
        src = NOTIFY + "logic T : anim;\non notify_begin EarlyOut { host.montage_stop_source(); }\nupdate(dt) { host.montage_stop_source(0.1); }"
        self.assertEqual(codes(src), ["DAL5066"])                                           # the handler is fine, the update is not
        undeclared = "logic T : anim;\non notify Hit { host.montage_stop_source(); }"
        self.assertEqual(codes(undeclared), ["DAL5066"])                                   # a notify the program does not declare names no instance
        plain = "notify Hit { }\nlogic T : anim;\non notify Hit { host.montage_stop_source(0.5); }"
        self.assertEqual(codes(plain), ["DAL5066"])                                        # a declared notify that is not a branching point does not either
        declared = "notify Hit branching { }\nlogic T : anim;\non notify Hit { host.montage_stop_source(0.5); }"
        self.assertEqual(codes(declared), [])

    def test_the_asset_query_takes_a_montage_value(self):
        self.check("if (host.montage_is_playing(Cfg.Attack)) { Q = 1.0; }")
        self.check("if (host.montage_is_playing(Q)) { Q = 1.0; }", "DAL4002")
        self.check("if (host.montage_is_playing(Cfg.Turn)) { Q = 1.0; }", "DAL4002")
        self.check("if (host.montage_is_playing(Cfg.Attack)) { Q = 1.0; }", kind="actor")      # asked from the game thread as well

    def test_a_snapshot_is_a_state_that_a_command_fills(self):
        self.check("host.snapshot_pose(Pose);", extra="state Pose: snapshot;")
        self.check("host.snapshot_pose(Rag.Pose);", extra="struct R { A: float; Pose: snapshot; }\nstate Rag: R;")
        self.check("host.snapshot_pose(Pose);", "DAL5067", kind="actor", extra="state Pose: snapshot;")
        self.check("host.snapshot_pose(Q);", "DAL4062")                                         # not a snapshot
        self.check("let p = Pose; host.snapshot_pose(p);", "DAL4062", extra="state Pose: snapshot;")
        self.check("host.snapshot_pose(none);", "DAL4062")
        self.check('host.snapshot_pose("Pose");', "DAL4061")

    def test_where_a_snapshot_may_live(self):
        for src in ("logic T;\nparam P: snapshot;\nupdate(dt) { }", "logic T;\ninput P: snapshot;\nupdate(dt) { }",
                    "logic T;\nentry E(dt, P: snapshot) { }",
                    "settings S { P: snapshot; }\nlogic T;\nupdate(dt) { }", "const C: snapshot = none;\nlogic T;\nupdate(dt) { }"):
            with self.subTest(src=src):
                self.assertTrue(codes(src), src)
        self.assertEqual(codes("struct R { Pose: snapshot; }\nlogic T : anim;\nstate Rag: R;\nupdate(dt) { }"), [])
        self.assertEqual(codes("notify N { P: snapshot; }\nlogic T : anim;\nupdate(dt) { }") != [], True)       # a payload is not state data

    def test_a_snapshot_has_valid_and_none_but_no_comparison(self):
        extra = "state Pose: snapshot;\nstate Other: snapshot;"
        self.check("if (valid(Pose)) { Q = 1.0; }", extra=extra)
        self.check("Pose = none;", extra=extra)
        self.check("Pose = Other;", extra=extra)
        self.check("if (Pose == Other) { Q = 1.0; }", "DAL4001", extra=extra)
        self.check("if (Pose != none) { Q = 1.0; }", "DAL4001", extra=extra)
        self.check("Pose = Q;", "DAL4002", extra=extra)

    def test_the_command_tables_describe_the_new_commands(self):
        for name in ("montage_set_position", "montage_stop_source", "play_slot_animation", "stop_montages_with_slot", "snapshot_pose"):
            self.assertIn(name, hostapi.COMMANDS)
        self.assertEqual(hostapi.COMMANDS["montage_stop_source"].implicit, ("source",))
        self.assertEqual(hostapi.COMMANDS["play_slot_animation"].min_args, 2)
        self.assertEqual(hostapi.COMMANDS["snapshot_pose"].params, ("place",))
        self.assertIn("montage_is_playing", hostapi.QUERIES)

    def test_old_programs_are_unchanged(self):
        self.check("host.montage_stop_all();")                                           # (no montage section helpers appear for it)
        self.assertNotIn("DalResolveMontage", generate_cpp(compile_ok("logic T : anim;\nupdate(dt) { host.montage_stop_all(); }"))["T.cpp"])


class SemanticsTests(unittest.TestCase):
    def run_both(self, src, frames, **params):
        module = compile_ok(src)
        py = harness.run_python(module, frames, **params)
        it = harness.run_interpreter(module, frames, **params)
        self.assertEqual(harness.compare(py, it), [])
        return py

    def test_a_montage_value_reaches_the_host_by_its_asset_name(self):
        out = self.run_both(PROGRAM, [fr(free={"Go": 1.0})], Cfg=DATA)
        calls = [c for c in out[0]["__cmds__"]]
        self.assertEqual(calls[0], ("montage_play", ("M", 1.5, None)))                    # a montage value has no sections: none is sent
        self.assertEqual(calls[1], ("montage_set_play_rate", ("M", 2.0)))
        self.assertEqual(calls[2], ("montage_set_position", ("M", 0.25)))
        self.assertEqual(calls[3], ("montage_stop", ("M", 0.1)))

    def test_an_empty_montage_value_is_sent_as_none(self):
        out = self.run_both(PROGRAM, [fr(free={"Go": 1.0})], Cfg={"Turn": "Seq"})
        self.assertEqual(out[0]["__cmds__"][0], ("montage_play", (None, 1.5, None)))

    def test_the_slot_animation_has_its_defaults_and_its_blend_mode(self):
        out = self.run_both(PROGRAM, [fr(free={"Go": 1.0})], Cfg=DATA)
        calls = {i: c for i, c in enumerate(out[0]["__cmds__"])}
        self.assertEqual(calls[4], ("play_slot_animation", ("Seq", "Transition", 0.1, 0.2, 1.0, 1, -1.0, 0.0, 1)))
        self.assertEqual(calls[5], ("play_slot_animation", ("Seq", "Plain", 0.25, 0.25, 1.0, 1, -1.0, 0.0, 0)))
        self.assertEqual(calls[6], ("stop_montages_with_slot", ("Transition", 0.2)))
        self.assertEqual(calls[7], ("stop_montages_with_slot", ("Plain", -1.0)))

    def test_a_snapshot_is_valid_once_the_host_took_it(self):
        src = SETTINGS + "logic T : anim post_flush;\nstate Pose: snapshot;\nstate Seen: int;\ninput Go: bool;\n" \
                         "update(dt) { if (Go && !valid(Pose)) { host.snapshot_pose(Pose); } if (valid(Pose)) { Seen += 1; } }"
        out = self.run_both(src, [fr(free={"Go": False}), fr(free={"Go": True}), fr(free={"Go": True}), fr(free={"Go": False})])
        self.assertEqual([f["Pose"] for f in out], [None, "Pose", "Pose", "Pose"])          # the state holds the name the host took it under
        self.assertEqual([f["Seen"] for f in out], [0, 0, 1, 2])                           # valid() reads true from the next update on
        self.assertEqual([len(f["__cmds__"]) for f in out], [0, 1, 0, 0])
        self.assertEqual(out[1]["__cmds__"][0], ("snapshot_pose", ("Pose",)))

    def test_a_snapshot_member_of_a_struct_state_is_named_by_its_path(self):
        src = "struct Rag { Flail: float; Pose: snapshot; }\nlogic T : anim post_flush;\nstate Rg: Rag;\ninput Go: bool;\n" \
              "update(dt) { if (Go) { host.snapshot_pose(Rg.Pose); } }"
        out = self.run_both(src, [fr(free={"Go": False}), fr(free={"Go": True})])
        self.assertEqual(out[1]["Rg.Pose"], "Rg.Pose")
        self.assertIsNone(out[0]["Rg.Pose"])
        self.assertEqual(out[1]["__cmds__"], (("snapshot_pose", ("Rg.Pose",)),))

    def test_a_snapshot_can_be_cleared(self):
        src = "logic T : anim post_flush;\nstate Pose: snapshot;\ninput Go: bool;\nupdate(dt) { if (Go) { host.snapshot_pose(Pose); } else { Pose = none; } }"
        out = self.run_both(src, [fr(free={"Go": True}), fr(free={"Go": False})])
        self.assertEqual([f["Pose"] for f in out], ["Pose", None])

    def test_the_asset_query_asks_the_host_with_the_asset_name(self):
        frames = [fr(free={"Go": 0.0}, montages={"M": {"playing": True}}), fr(free={"Go": 0.0}, montages={"M": {"playing": False}}),
                  fr(free={"Go": 0.0}, montages={"M": {"playing": True}})]
        out = self.run_both(PROGRAM, frames, Cfg=DATA)
        self.assertEqual([f["Played"] for f in out], [1, 1, 2])
        out = self.run_both(PROGRAM, frames, Cfg={"Turn": "Seq"})                          # no montage: not playing
        self.assertEqual([f["Played"] for f in out], [0, 0, 0])

    def test_the_stop_source_command_gets_the_instance_of_the_notify(self):
        frames = [fr(free={"Want": True}, events=[("notify_begin", "EarlyOut", {"Duration": 0.5}, 7)]),
                  fr(free={"Want": False}, events=[("notify_begin", "EarlyOut", {"Duration": 0.5}, 8)]),
                  fr(free={"Want": True}, events=[("notify_begin", "EarlyOut", {}, 9)])]
        out = self.run_both(PROGRAM, frames, Cfg=DATA)
        self.assertEqual(out[0]["__cmds__"], (("montage_stop_source", (7, 0.5)),))
        self.assertEqual(out[1]["__cmds__"], ())
        self.assertEqual(out[2]["__cmds__"], (("montage_stop_source", (9, 0.25)),))         # the declared default of the property
        # a notify raised without an instance (not from a montage) names none: the command still goes out, with no instance
        out = self.run_both(PROGRAM, [fr(free={"Want": True}, events=[("notify_begin", "EarlyOut", {"Duration": 0.1})])], Cfg=DATA)
        self.assertEqual(out[0]["__cmds__"][0][0], "montage_stop_source")

    def test_the_interpreter_and_python_agree_on_random_frames(self):
        module = compile_ok(PROGRAM)
        for seed in (1, 2, 3):
            self.assertEqual(harness.verify(module, 200, seed, Cfg=DATA), [])
        events = [e for f in harness.random_frames(module, 200, 1) for e in f.get("events", [])]
        self.assertTrue(any(len(e) > 3 for e in events))

    def test_without_post_flush_the_state_follows_the_flush_of_the_update(self):
        src = "logic T : anim;\nstate Pose: snapshot;\nupdate(dt) { host.snapshot_pose(Pose); }"
        out = self.run_both(src, [fr(), fr()])
        self.assertEqual([f["Pose"] for f in out], ["Pose", "Pose"])


class SimHostTests(unittest.TestCase):
    """The simulated host plays what the commands ask for: the whole loop in Python."""

    def logic(self, src, host, **params):
        module = compile_ok(src)
        return load_class(module, generate_python(module))(host, **params)

    def montages(self):
        return [SimMontage("M", 1.0, {}, windows=[(0.2, 0.6, "EarlyOut", {"Duration": 0.25})], blend_out=0.5, slot="Attack"),
                SimMontage("N", 2.0, {}, slot="Other")]

    def test_the_notify_state_stops_its_own_montage_instance(self):
        host = SimHost(self.montages())
        logic = self.logic(PROGRAM, host, Cfg=DATA)
        logic.Want = True
        host.montage_play("M", 1.0, None)
        host.montage_play("N", 1.0, None)             # N takes the track: M blends out; the notify window of M is open already
        host.advance(0.0)
        # play M again so it is the instance that raises the notify; the window event carries its instance id
        host.instances.clear()
        host.events.clear()
        host.montage_play("M", 1.0, None)
        ident = host.instances[0].id
        stopped_at = None
        for step in range(12):
            host.advance(0.05)
            logic.update(0.05)
            logic.post_update()
            if stopped_at is None and any(c[0] == "montage_stop_source" for c in host.calls):
                stopped_at = step
        self.assertIsNotNone(stopped_at)
        stop = [c for c in host.calls if c[0] == "montage_stop_source"][0]
        self.assertEqual(stop, ("montage_stop_source", (ident, 0.25)))
        # the instance was blended out by the stop: its montage ended as interrupted, long before its length
        self.assertTrue(any(e[0] == "montage_ended" and e[2] for e in host.events) or not host.instances)

    def test_stop_montages_with_slot_blends_out_what_plays_in_the_slot(self):
        host = SimHost(self.montages())
        host.montage_play("M", 1.0, None)
        host.advance(0.1)
        host.stop_montages_with_slot("Other", -1.0)           # another slot: nothing happens
        self.assertTrue(host.montage_playing("M"))
        host.stop_montages_with_slot("Attack", 0.1)
        self.assertFalse(host.montage_playing("M"))
        self.assertTrue(host.montage_active("M"))              # blending out
        host.advance(0.2)
        self.assertFalse(host.montage_active("M"))

    def test_a_slot_animation_is_a_dynamic_montage_and_takes_its_slot(self):
        host = SimHost(self.montages(), sequences={"Seq": 0.8})
        host.montage_play("M", 1.0, None)
        host.play_slot_animation("Seq", "Attack", 0.1, 0.2, 2.0, 1, -1.0, 0.1, 0)
        self.assertFalse(host.montage_playing("M"))              # the dynamic montage took the track of the slot
        self.assertTrue(host.montage_playing("Seq"))
        self.assertAlmostEqual(host.montage_position("Seq"), 0.1)
        host.advance(0.25)
        self.assertAlmostEqual(host.montage_position("Seq"), 0.6)                         # rate 2
        host.play_slot_animation("Missing", "Attack", 0.1, 0.2, 1.0, 1, -1.0, 0.0, 0)      # an unknown asset: nothing to play
        self.assertTrue(host.montage_playing("Seq"))

    def test_the_position_of_a_playing_montage_can_be_set(self):
        host = SimHost(self.montages())
        host.montage_play("N", 1.0, None)
        host.montage_set_position("N", 1.5)
        self.assertAlmostEqual(host.montage_position("N"), 1.5)
        host.montage_set_position("N", 9.0)
        self.assertAlmostEqual(host.montage_position("N"), 2.0)
        host.montage_set_position("Unknown", 0.5)
        host.montage_set_position(None, 0.5)

    def test_the_snapshots_are_recorded(self):
        host = SimHost([])
        src = "logic T : anim post_flush;\nstate Pose: snapshot;\nupdate(dt) { host.snapshot_pose(Pose); }"
        logic = self.logic(src, host)
        logic.update(0.1)
        logic.post_update()
        self.assertEqual(host.snapshots, ["Pose"])
        self.assertEqual(logic.Pose, "Pose")

    def test_the_asset_query_follows_the_simulation(self):
        host = SimHost(self.montages())
        logic = self.logic(PROGRAM, host, Cfg=DATA)
        for _ in range(3):
            logic.update(0.05)
        self.assertEqual(logic.Played, 0)
        host.montage_play("M", 1.0, None)
        for _ in range(3):
            host.advance(0.05)
            logic.update(0.05)
        self.assertEqual(logic.Played, 3)


class CppStructureTests(unittest.TestCase):
    def setUp(self):
        self.files = generate_cpp(compile_ok(PROGRAM))
        self.h, self.cpp = self.files["Mc.h"], self.files["Mc.cpp"]

    def test_the_commands_carry_the_assets_and_names(self):
        cpp = self.cpp
        self.assertIn("DalEnqueue(DalHost::Cmd::MontagePlay(DalSettings_Cfg().Attack, 1.5f));", cpp)
        self.assertIn("DalEnqueue(DalHost::Cmd::MontageSetPosition(DalSettings_Cfg().Attack, 0.25f));", cpp)
        self.assertIn('DalHost::Cmd::PlaySlotAnimation(DalSettings_Cfg().Turn, FName(TEXT("Transition")), 0.1f, 0.2f, 1.0f, 1, -1.0f, 0.0f, '
                      "static_cast<int32>(EMontageBlendMode::Inertialization))", cpp)
        self.assertIn('DalHost::Cmd::StopMontagesWithSlot(FName(TEXT("Plain")), -1.0f)', cpp)
        self.assertIn("DalHost::Cmd::MontageStopSource(DalCurrentInstance, payload->Duration)", cpp)

    def test_the_executors_use_the_engine_calls(self):
        cpp = self.cpp
        self.assertIn("UAnimMontage* DalAsset = DalResolveMontage(DalCommand)", cpp)
        self.assertIn("DalAnim->Montage_SetPosition(DalAsset, DalCommand.F);", cpp)
        self.assertIn("DalAnim->GetMontageInstanceForID(DalCommand.Instance)", cpp)
        self.assertIn("DalAnim->PlaySlotAnimationAsDynamicMontage_WithBlendSettings(DalSequence, DalCommand.Name", cpp)
        self.assertIn("DalAnim->PlaySlotAnimationAsDynamicMontage(DalSequence, DalCommand.Name", cpp)
        self.assertIn("DalTrack.SlotName != DalCommand.Name", cpp)
        self.assertIn("DalAnim->Montage_IsPlaying(DalMontage)", cpp)

    def test_the_handler_of_a_notify_knows_its_montage_instance(self):
        self.assertIn("DalCurrentInstance = DalEvent.MontageInstance;", self.cpp)
        self.assertIn("int32 DalCurrentInstance", self.h)

    def test_the_snapshot_is_a_value_of_the_state_filled_by_the_command(self):
        self.assertIn("FPoseSnapshot Pose = FPoseSnapshot();", self.h)
        self.assertIn('#include "Animation/PoseSnapshot.h"', self.h)
        self.assertIn('if (DalCommand.Name == FName(TEXT("Pose")))', self.cpp)
        self.assertIn("DalAnim->SnapshotPose(Pose);", self.cpp)
        self.assertNotIn("UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = \"DAL|Snapshot\"", self.h)

    def test_a_snapshot_member_of_a_struct_state_is_filled_in_place(self):
        src = "struct Rag { Flail: float; Pose: snapshot; }\nlogic T : anim post_flush;\nstate Rg: Rag;\nstate Seen: bool;\n" \
              "update(dt) { host.snapshot_pose(Rg.Pose); Seen = valid(Rg.Pose); }"
        files = generate_cpp(compile_ok(src))
        self.assertIn("FPoseSnapshot Pose = FPoseSnapshot();", files["T.h"])
        self.assertIn('if (DalCommand.Name == FName(TEXT("Rg.Pose")))', files["T.cpp"])
        self.assertIn("DalAnim->SnapshotPose(Rg.Pose);", files["T.cpp"])
        self.assertIn("Seen = Rg.Pose.bIsValid;", files["T.cpp"])

    def test_programs_that_do_not_use_them_have_no_trace_of_them(self):
        files = generate_cpp(compile_ok("logic T : anim;\nstate X: float;\nupdate(dt) { X += dt; host.montage_stop_all(); }"))
        text = files["T.h"] + files["T.cpp"]
        for word in ("DalResolveMontage", "DalCurrentInstance", "SnapshotPose", "DalMontageIsPlaying", "PlaySlotAnimation"):
            self.assertNotIn(word, text)

    def test_the_host_header_has_the_commands(self):
        header = self.files["DalHost.h"]
        for word in ("MontageSetPosition", "MontageStopSource", "PlaySlotAnimation", "StopMontagesWithSlot", "SnapshotPose"):
            self.assertIn(f"{word}(", header)


@unittest.skipIf(tcc is None or tcc.COMPILER is None, "no C++ compiler found")
class CppCompileTests(unittest.TestCase):
    def run_case(self, module, frames, **kw):
        return tcc.CppCompileTests("test_hero_gait_matches_python").run_case(module, frames, **kw)

    def test_the_commands_match_python_on_random_sessions(self):
        module = compile_ok(PROGRAM)
        effects = 0
        for seed in (1, 2, 3):
            frames = harness.random_frames(module, 80, seed)
            effects += self.run_case(module, frames, params={"Cfg": DATA}, warnings_as_errors=True)
        self.assertGreater(effects, 100)

    def test_a_montage_value_that_is_empty_does_nothing(self):
        module = compile_ok(PROGRAM)
        frames = [fr(0.125, free={"Go": 1.0, "Want": False}, montages={"M": {"playing": True}}), fr(0.125, free={"Go": 0.0, "Want": False})]
        self.run_case(module, frames, params={"Cfg": {"Turn": "Seq"}}, warnings_as_errors=True)

    def test_the_asset_query_matches_python(self):
        module = compile_ok(PROGRAM)
        frames = [fr(0.125, free={"Go": 0.0, "Want": False}, montages={"M": {"playing": p, "active": p}}) for p in (True, False, True, True, False)]
        self.run_case(module, frames, params={"Cfg": DATA}, warnings_as_errors=True)

    def test_the_snapshot_state_matches_python(self):
        src = "struct Rag { Flail: float; Pose: snapshot; }\nfn Taken(p: snapshot) -> bool { return valid(p); }\nlogic T : anim post_flush;\nstate Rg: Rag;\n" \
              "state Direct: snapshot;\nstate Seen: int;\ninput Go: bool;\n" \
              "update(dt) { if (Go && !valid(Rg.Pose)) { host.snapshot_pose(Rg.Pose); } if (Go) { host.snapshot_pose(Direct); }\n" \
              "    if (Taken(Rg.Pose) && valid(Direct)) { Seen += 1; } if (!Go) { Direct = none; } }"
        module = compile_ok(src)
        frames = [fr(0.125, free={"Go": g}) for g in (False, True, True, False, False, True, False)]
        self.assertEqual(self.run_case(module, frames, warnings_as_errors=True), 4)
        for seed in (1, 2):
            self.run_case(module, harness.random_frames(module, 60, seed), warnings_as_errors=True)


if __name__ == "__main__":
    unittest.main()
