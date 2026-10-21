"""Animation notifies a program declares (`notify`, `notify_state`): the payload of their handlers, `notify_tick`, the montage simulation
that raises them, and the generated notify classes (properties, entry points, routing to the logics of the actor)."""

import re
import unittest

import _bootstrap  # noqa: F401

import dal
from dal import harness, ir
from dal.cppbackend import generate_cpp
from dal.mockhost import SimHost, SimMontage

ACTOR = "logic T : actor;\n"

DECLS = '''
tags AlsAction = "Als.Action" { Rolling, Mantling }
enum Hips { Fwd, Back }
struct Pair { A: float = 1.0; B: float = 2.0; }
notify SetHips cpp "AlsAnimNotify_SetHips" { H: Hips; Weight: float = 0.5; }
notify_state SetAction cpp "AlsAnimNotifyState_SetAction" { Action: AlsAction; Offset: vec3; Pair: Pair; Loud: bool; }
notify_state EarlyOut branching { Duration: float = 0.25; Check: bool = true; }
'''

PROGRAM = DECLS + '''
logic T;
state Hip: Hips;
state W: float;
state Act: AlsAction;
state Off: vec3;
state PairSum: float;
state Loud: bool;
state Began: int;
state Ended: int;
state Ticks: int;
state Out: float;
on notify SetHips { Hip = payload.H; W += payload.Weight; }
on notify_begin SetAction { Act = payload.Action; Off = payload.Offset; PairSum = payload.Pair.A + payload.Pair.B; Loud = payload.Loud; Began += 1; }
on notify_end SetAction { if (Act == payload.Action) { Act = AlsAction.None; } Ended += 1; }
on notify_tick EarlyOut { Ticks += 1; if (payload.Check) { Out += payload.Duration; } }
on notify Hit { W += 100.0; }
'''


def compile_ok(src, **kw):
    return dal.check_source(src, "t.dal", **kw).module


def errors(src, **kw):
    try:
        dal.check_source(src, "t.dal", **kw)
    except dal.DalError as e:
        return [(d.code, d.pos.line, d.pos.col) for d in e.diagnostics if d.severity == "error"]
    return []


def run_both(src, frames):
    module = compile_ok(src)
    py = harness.run_python(module, frames)
    it = harness.run_interpreter(module, frames)
    assert harness.compare(py, it) == [], harness.compare(py, it)[:3]
    return py


def fr(dt=0.1, **kw):
    kw["dt"] = dt
    return kw


class DiagnosticTests(unittest.TestCase):
    def check(self, src, *expected):
        with self.subTest(src=src):
            self.assertEqual(errors(src), list(expected), src)

    def test_the_kind_of_the_notify_decides_the_events(self):
        self.check("notify P { }\nlogic T;\non notify_begin P { }", ("DAL3064", 3, 17))
        self.check("notify_state S { }\nlogic T;\non notify S { }", ("DAL3064", 3, 11))
        self.check("notify_state S { }\nlogic T;\non notify_tick S { }\non notify_end S { }\non notify_begin S { }")
        self.check("notify P { }\nlogic T;\non notify P { }")
        self.check("logic T;\non notify_tick Nope { }", ("DAL3065", 2, 16))
        self.check("logic T;\nstate Nope: int;\non notify_tick Nope { }", ("DAL3065", 3, 16))
        self.check("logic T;\non notify Nope { }\non notify_begin Nope { }\non notify_end Nope { }")           # undeclared names keep working

    def test_the_payload_is_read_through_its_members(self):
        self.check("notify P { A: float; }\nlogic T;\nstate S: float;\non notify P { S = payload.A; }")
        self.check("notify P { A: float; }\nlogic T;\nstate S: float;\non notify P { let q = payload; S = 1.0; }", ("DAL4060", 4, 23))
        self.check("notify P { A: float; }\nlogic T;\nfn f(a: float) -> float { return a; }\nstate S: float;\non notify P { S = f(payload); }", ("DAL4060", 5, 21))
        self.check("notify P { A: float; }\nlogic T;\non notify P { payload.A = 1.0; }", ("DAL5001", 3, 15))
        self.check("notify P { A: float; }\nlogic T;\nstate S: float;\non notify P { S = payload.B; }", ("DAL4030", 4, 27))
        self.check("notify P { A: float; }\nlogic T;\nstate payload: int;\non notify P { }", ("DAL3005", 4, 11))

    def test_declarations(self):
        self.check("notify P { A: float; A: float; }", ("DAL3001", 1, 22))
        self.check("notify P { A: hit; }", ("DAL4050", 1, 15))
        self.check("notify P { Notify: float; }", ("DAL3004", 1, 12))
        self.check('notify P cpp "Bad Name" { }', ("DAL3066", 1, 14))
        self.check("notify P { }\nnotify P { }", ("DAL3001", 2, 8))
        self.check("notify P { A: float; }\nlogic T;\nstate S: P;", ("DAL3041", 3, 10))                       # the notify is not a type
        self.check("notify P { A: float; }\nlogic T;\nstate S: float;\nupdate(dt) { S = P; }", ("DAL3030", 4, 18))
        self.check("notify P { A: float = nope; }", ("DAL3040", 1, 23))
        self.check("notify P { A: float = 1; B: bool = 2.5; }", ("DAL4002", 1, 36))
        self.check("tags G = \"G\" { X }\nnotify P { A: G = G.X; }\nlogic T;\non notify P { }")

    def test_a_library_may_declare_notifies(self):
        files = {"lib.dal": 'notify P { A: float = 4.0; }\nnotify_state S { }',
                 "t.dal": 'use "lib.dal";\nlogic T;\nstate N: float;\non notify P { N += payload.A; }\non notify_begin S { N += 1.0; }'}
        module = compile_ok(files["t.dal"], files=files)
        self.assertEqual([n.name for n in module.notifies], ["P", "S"])
        self.assertEqual({n.origin is not None for n in module.notifies}, {True})
        lib = dal.check_source(files["lib.dal"], "lib.dal", files=files).module
        self.assertTrue(lib.is_library)
        self.assertEqual([n.name for n in lib.notifies], ["P", "S"])


class SemanticsTests(unittest.TestCase):
    def test_the_payload_carries_the_properties_and_the_declared_defaults(self):
        out = run_both(PROGRAM, [
            fr(events=[("notify", "SetHips", {"H": "Back", "Weight": 2.0}, 4)]),
            fr(events=[("notify", "SetHips", {"H": 0})]),                                    # an index; the weight keeps its default 0.5
            fr(events=[("notify", "SetHips")]),                                              # no properties at all: every default
            fr(events=[("notify", "Hit")]),                                                  # an undeclared notify: no payload
        ])
        self.assertEqual([f["Hip"] for f in out], [1, 0, 0, 0])
        self.assertEqual([f["W"] for f in out], [2.0, 2.5, 3.0, 103.0])

    def test_states_begin_tick_and_end(self):
        pair = {"A": 3.0, "B": 4.0}
        out = run_both(PROGRAM, [
            fr(events=[("notify_begin", "SetAction", {"Action": "Als.Action.Mantling", "Offset": (1.0, 2.0, 3.0), "Pair": pair, "Loud": True})]),
            fr(events=[("notify_tick", "EarlyOut", {"Duration": 0.5}, 9), ("notify_tick", "EarlyOut", {"Check": False}, 9)]),
            fr(events=[("notify_end", "SetAction", {"Action": "Als.Action.Mantling"})]),
            fr(events=[("notify_end", "SetAction", {"Action": "Als.Action.Rolling"})]),
        ])
        self.assertEqual((out[0]["Act"], out[0]["Off"], out[0]["PairSum"], out[0]["Loud"], out[0]["Began"]), ("Als.Action.Mantling", (1.0, 2.0, 3.0), 7.0, True, 1))
        self.assertEqual((out[1]["Ticks"], out[1]["Out"]), (2, 0.5))
        self.assertEqual((out[2]["Act"], out[2]["Ended"]), ("", 1))
        self.assertEqual(out[3]["Ended"], 2)

    def test_a_payload_without_the_nested_values_uses_the_declared_defaults(self):
        out = run_both(PROGRAM, [fr(events=[("notify_begin", "SetAction", {})])])
        self.assertEqual(out[0]["PairSum"], 3.0)

    def test_a_window_sugar_follows_a_declared_notify_state(self):
        src = DECLS + "logic T : actor;\nwindow Open <- notify_state SetAction;\nstate N: int;\nupdate(dt) { if (Open) { N += 1; } }"
        out = run_both(src, [fr(events=[("notify_begin", "SetAction", {})]), fr(), fr(events=[("notify_end", "SetAction", {})]), fr()])
        self.assertEqual([f["N"] for f in out], [1, 2, 2, 2])

    def test_the_montage_simulation_raises_them_in_time_order(self):
        module = compile_ok(PROGRAM)
        host = SimHost([SimMontage("M", 1.0, {}, notifies=[(0.25, "SetHips", {"H": "Back", "Weight": 1.0}), (0.5, "Hit")],
                                   windows=[(0.2, 0.6, "SetAction", {"Action": "Als.Action.Rolling", "Loud": True}), (0.3, 0.9, "EarlyOut", {"Duration": 0.25})],
                                   blend_out=0.1)])
        for cls in (dal.pybackend.load_class(module),):
            logic = cls(host)
            host.montage_play("M", 1.0, None)
            for _ in range(24):
                host.advance(0.05)
                logic.update(0.05)
        self.assertEqual((logic.Hip, logic.W, logic.Began, logic.Ended), (1, 101.0, 1, 1))
        self.assertGreaterEqual(logic.Ticks, 10)
        self.assertEqual(logic.Act, "")

    def test_declared_notifies_work_in_an_actor_logic_too(self):
        src = DECLS + "logic T : actor;\nstate W: float;\non notify SetHips { W += payload.Weight; }"
        out = run_both(src, [fr(events=[("notify", "SetHips", {"Weight": 3.0})])])
        self.assertEqual(out[0]["W"], 3.0)

    def test_interpreter_and_python_agree_on_random_frames(self):
        module = compile_ok(PROGRAM)
        for seed in (1, 2, 3):
            self.assertEqual(harness.verify(module, 250, seed), [])
        events = [e for f in harness.random_frames(module, 250, 1) for e in f.get("events", [])]
        self.assertTrue({"notify_begin", "notify_end", "notify_tick", "notify"} <= {e[0] for e in events})
        self.assertTrue(any(len(e) > 3 for e in events))                      # a branching notify raised by a montage instance


class CppStructureTests(unittest.TestCase):
    def setUp(self):
        self.files = generate_cpp(compile_ok(PROGRAM))
        self.h, self.cpp = self.files["T.h"], self.files["T.cpp"]

    def test_the_notify_classes_are_declared_with_their_properties_and_entry_points(self):
        h = self.h
        self.assertIn("class UAlsAnimNotify_SetHips : public UAnimNotify", h)
        self.assertIn("class UAlsAnimNotifyState_SetAction : public UAnimNotifyState", h)
        self.assertIn("class UDalNotifyState_EarlyOut : public UAnimNotifyState", h)                     # no `cpp "..."`: the default class name
        self.assertIn("EHips H = EHips::Fwd;", h)
        self.assertIn("float Weight = 0.5f;", h)
        self.assertIn("FGameplayTag Action = FGameplayTag::EmptyTag;", h)
        self.assertIn("FPair Pair = FPair();", h)
        self.assertIn("virtual void Notify(USkeletalMeshComponent* MeshComp, UAnimSequenceBase* Animation, const FAnimNotifyEventReference& EventReference) override;", h)
        self.assertIn("virtual void NotifyTick(USkeletalMeshComponent* MeshComp, UAnimSequenceBase* Animation, float FrameDeltaTime, const FAnimNotifyEventReference& EventReference) override;", h)
        self.assertIn("virtual void BranchingPointNotifyTick(FBranchingPointNotifyPayload& BranchingPointPayload, float FrameDeltaTime) override;", h)
        self.assertEqual(h.count("BranchingPointNotifyTick("), 1)                                          # only the branching one has them
        self.assertEqual(h.count("UAlsAnimNotify_SetHips();"), 0)                                         # a constructor only when it sets the branching flag
        self.assertIn("UDalNotifyState_EarlyOut();", h)
        self.assertIn("bIsNativeBranchingPoint = true;", self.cpp)

    def test_the_notify_posts_to_the_router_with_its_declared_name(self):
        self.assertIn("DalHost::FEventRouter::PostNotify(MeshComp != nullptr ? MeshComp->GetOwner() : nullptr, DalHost::EEventKind::Notify, FName(TEXT(\"SetHips\")), this, INDEX_NONE);", self.cpp)
        self.assertIn("DalHost::EEventKind::NotifyTick, FName(TEXT(\"EarlyOut\")), this, BranchingPointPayload.MontageInstanceID);", self.cpp)
        self.assertIn("UAnimNotifyState::NotifyBegin(MeshComp, Animation, TotalDuration, EventReference);", self.cpp)       # (the stub has no Super for several classes)

    def test_the_logic_receives_the_events_through_the_router_and_reads_the_payload_through_the_object(self):
        self.assertIn("class UT : public UAnimInstance, public DalHost::IEventSink", self.h)
        self.assertIn("DalQueued.Id = DalFindNotifyId(DalQueued.Name);", self.cpp)
        self.assertIn("if (const UAlsAnimNotifyState_SetAction* DalPayload = Cast<UAlsAnimNotifyState_SetAction>(DalEvent.Source))", self.cpp)
        self.assertIn("void DalHandler_0(const UAlsAnimNotify_SetHips* payload);", self.h)
        self.assertIn("Hip = static_cast<EHips>(payload->H);", self.cpp.replace("Hip = payload->H;", "Hip = static_cast<EHips>(payload->H);"))
        self.assertIn("Off = payload->Offset;", self.cpp)
        self.assertIn("PairSum = payload->Pair.A + payload->Pair.B;", self.cpp)

    def test_declared_notifies_do_not_subscribe_delegates_or_make_anim_notify_functions(self):
        files = generate_cpp(compile_ok(DECLS + "logic T;\nstate W: float;\non notify SetHips { W += payload.Weight; }"))
        self.assertNotIn("OnPlayMontageNotifyBegin", files["T.cpp"])
        self.assertNotIn("void AnimNotify_SetHips()", files["T.h"])
        files = generate_cpp(compile_ok(PROGRAM))
        self.assertIn("void AnimNotify_Hit();", files["T.h"])               # the undeclared one still does
        self.assertNotIn("void AnimNotify_SetHips()", files["T.h"])

    def test_notifies_of_a_library_are_generated_once_into_the_library(self):
        files = {"lib.dal": 'tags G = "G" { X }\nnotify P cpp "LibNotify_P" { A: G; }\nnotify_state S { Amount: float = 2.0; }',
                 "t.dal": 'use "lib.dal";\nlogic T : actor;\nstate N: float;\non notify P { N += 1.0; }\non notify_end S { N += payload.Amount; }'}
        out = generate_cpp(compile_ok(files["t.dal"], files=files))
        self.assertIn("class ULibNotify_P : public UAnimNotify", out["Lib.h"])
        self.assertIn("class UDalNotifyState_S : public UAnimNotifyState", out["Lib.h"])
        self.assertIn("ULibNotify_P::Notify(", out["Lib.cpp"])
        self.assertIn('#include "DalHost.h"', out["Lib.h"])
        self.assertNotIn("ULibNotify_P", out["T.h"].replace("const ULibNotify_P* payload", ""))
        self.assertNotIn("UCLASS()\nclass UDalNotifyState_S", out["T.h"])
        self.assertIn("DalHost.h", out)


try:
    import test_cpp_compile as tcc
except Exception:                                   # pragma: no cover
    tcc = None


@unittest.skipIf(tcc is None or tcc.COMPILER is None, "no C++ compiler found")
class CppCompileTests(unittest.TestCase):
    """The generated notify classes and the logic against the stand-in engine; the notifies are raised by calling the entry points the engine calls."""

    def run_case(self, module, frames, **kw):
        return tcc.CppCompileTests("test_hero_gait_matches_python").run_case(module, frames, **kw)

    def test_an_animation_instance_matches_python(self):
        module = compile_ok(PROGRAM)
        for seed in (1, 2, 3):
            self.run_case(module, harness.random_frames(module, 150, seed), warnings_as_errors=True)

    def test_an_actor_logic_matches_python(self):
        module = compile_ok(DECLS + "logic T : actor;\nstate W: float;\nstate Hip: Hips;\nstate Act: AlsAction;\n"
                                    "on notify SetHips { W += payload.Weight; Hip = payload.H; }\non notify_begin SetAction { Act = payload.Action; }\n"
                                    "on notify_tick EarlyOut { W += payload.Duration; }")
        for seed in (1, 2):
            self.run_case(module, harness.random_frames(module, 120, seed), warnings_as_errors=True)

    def test_a_scripted_session(self):
        module = compile_ok(PROGRAM)
        frames = [fr(1 / 60, events=[("notify", "SetHips", {"H": "Back", "Weight": 2.0})]),
                  fr(1 / 60, events=[("notify_begin", "SetAction", {"Action": "Als.Action.Rolling", "Pair": {"A": 5.0}}), ("notify_tick", "EarlyOut", {"Duration": 0.75}, 3)]),
                  fr(1 / 60, events=[("notify_end", "SetAction", {"Action": "Als.Action.Rolling"}), ("notify", "Hit")]), fr(1 / 60)]
        self.run_case(module, frames, warnings_as_errors=True)
        out = harness.run_python(module, frames)
        self.assertEqual((out[-1]["Hip"], out[-1]["W"], out[-1]["Act"], out[-1]["Out"]), (1, 102.0, "", 0.75))

    def test_notifies_declared_in_a_library_compile_next_to_the_logic(self):
        files = {"lib.dal": 'tags G = "G" { X, Y }\nnotify P cpp "LibNotify_P" { A: G; Gain: float = 1.5; }\nnotify_state S { Amount: float = 2.0; }',
                 "t.dal": 'use "lib.dal";\nlogic T : actor;\nstate N: float;\nstate Last: G;\non notify P { N += payload.Gain; Last = payload.A; }\n'
                          'on notify_tick S { N += payload.Amount; }'}
        module = compile_ok(files["t.dal"], files=files)
        for seed in (1, 2):
            self.run_case(module, harness.random_frames(module, 100, seed), warnings_as_errors=True)


if __name__ == "__main__":
    unittest.main()
