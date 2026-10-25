"""C++ generation for methods, views, lifecycle handlers, peers and tick-slot update blocks, against the stand-in engine."""

import unittest

import _bootstrap  # noqa: F401

import cpp_system
import dal
import test_cpp_compile as tcc
from dal import harness
from dal.cppbackend import CppOptions, generate_cpp
from peer_sources import FILES
from test_methods import PROGRAM as METHODS_PROGRAM
from test_peers import TickSlotTests


def system():
    return {n[:-4]: dal.check_source(FILES[n], n, files=FILES).module for n in ("AlsCharacter.dal", "AlsInput.dal", "AlsAnim.dal")}


def compare_system(test, mods, py, cpp, tol=1e-3):
    problems = []
    test.assertEqual(len(py), len(cpp))
    for i, (a, b) in enumerate(zip(py, cpp)):
        for n in mods:
            for k, v in a[n].items():
                if k.startswith("__"):
                    ea, eb = tcc.py_effects(v), list(b[n][k])
                    if len(ea) != len(eb) or any(x[0] != y[0] for x, y in zip(ea, eb)):
                        problems.append(f"frame {i} {n} commands: {ea} != {eb}")
                elif not tcc.close(v, b[n][k], tol):
                    problems.append(f"frame {i} {n}.{k}: python={v!r} c++={b[n][k]!r}")
    test.assertEqual(problems[:5], [], f"{len(problems)} mismatches")


@unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
class SystemCppTests(unittest.TestCase):
    def test_three_logic_system_matches_python(self):
        mods = system()
        for seed in (1, 2, 3):
            frames = tcc.round_frames(harness.random_system_frames(mods, 80, seed))
            cpp, output = cpp_system.build_and_run_system(mods, frames)
            if tcc.COMPILER[0] == "msvc":
                self.assertNotIn("warning", output.lower(), output)
            compare_system(self, mods, harness.run_system(mods, frames, "python"), cpp)

    def test_a_different_schedule_matches_too(self):
        mods = system()
        schedule = [("AlsInput", "pre_movement"), ("AlsAnim", None), ("AlsCharacter", "after_movement")]
        frames = tcc.round_frames(harness.random_system_frames(mods, 60, 7))
        cpp, _ = cpp_system.build_and_run_system(mods, frames, schedule)
        compare_system(self, mods, harness.run_system(mods, frames, "python", schedule), cpp)

    def test_the_queued_call_arrives_in_the_animation_update_that_follows(self):
        mods = system()
        frames = tcc.round_frames(harness.random_system_frames(mods, 30, 5))
        cpp, _ = cpp_system.build_and_run_system(mods, frames)
        notified = [f["AlsAnim"]["Notified"] for f in cpp]
        jumps = [f["AlsCharacter"]["Jumps"] for f in cpp]
        self.assertGreater(max(jumps), 0)
        self.assertEqual(notified, jumps)                     # same frame: the anim logic ticks after the character

    def test_generated_text_of_the_peers_and_the_queue(self):
        mods = system()
        char, inp, anim = (generate_cpp(mods[n]) for n in ("AlsCharacter", "AlsInput", "AlsAnim"))
        # the character: a peer to the animation instance, a tick function per update block, begin play in BeginPlay
        self.assertIn("TObjectPtr<UAlsAnim> Anim;", char["AlsCharacter.h"])
        self.assertIn("struct FAlsCharacterPhaseTick : public FActorComponentTickFunction", char["AlsCharacter.h"])
        self.assertIn("void DalRunPhase(int32 Phase, float DeltaTime);", char["AlsCharacter.h"])
        cpp = char["AlsCharacter.cpp"]
        self.assertIn("DalPhaseTicks[0].TickGroup = TG_PrePhysics;", cpp)
        self.assertIn("DalPhaseTicks[0].AddPrerequisite(DalMovement, DalMovement->PrimaryComponentTick);", cpp)
        self.assertIn("DalMesh->PrimaryComponentTick.AddPrerequisite(this, DalPhaseTicks[0]);", cpp)
        self.assertIn("\tDalHandler_0();\n", cpp)
        self.assertIn("if (Anim != nullptr)\n\t{\n\t\tAnim->DalQueue_Notify(Jumps);\n\t}", cpp)
        self.assertNotIn("TickComponent", cpp)
        # the input logic calls the character at once
        self.assertIn("Character->SetDesiredGait(", inp["AlsInput.cpp"])
        self.assertIn("const bool ok = Character != nullptr ? Character->Jump() : false;", inp["AlsInput.cpp"])
        self.assertIn('#include "AlsCharacter.h"', inp["AlsInput.cpp"])
        # the animation instance: a queue for the calls of actor logic, drained after sampling
        self.assertIn("TArray<TFunction<void()>> DalPeerQueue;", anim["AlsAnim.h"])
        self.assertIn("void DalQueue_Notify(int32 n);", anim["AlsAnim.h"])
        a = anim["AlsAnim.cpp"]
        self.assertLess(a.index("DalSampleInputs();\n\tDalDrainPeerCalls();"), a.index("NativeThreadSafeUpdateAnimation"))
        # ... and again at the start of the graph's update: a call the movement component's tick or the actor logic's second slot made after NativeUpdateAnimation
        # is in effect in the update of the same frame (the original's direct call), not of the next one
        tsu = a[a.index("::NativeThreadSafeUpdateAnimation"):]
        self.assertLess(tsu.index("DalDrainPeerCalls();"), tsu.index("DalUpdate(DeltaSeconds);"))
        self.assertIn("CharLocomotion = Character != nullptr ? Character->Locomotion : FLocomotionState();", a)
        self.assertIn("CharSpeed = Character != nullptr ? Character->Locomotion.Speed : 0.0f;", a)


@unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
class MethodsCppTests(unittest.TestCase):
    def test_methods_views_and_lifecycle_on_an_animation_instance(self):
        m = dal.check_source(METHODS_PROGRAM, "t.dal").module
        for seed in (1, 2):
            frames = harness.random_frames(m, 80, seed)
            frames[-1]["end_play"] = True
            tcc.CppCompileTests.run_case(self, m, frames, warnings_as_errors=True)

    def test_tick_slots_on_an_actor_logic(self):
        m = dal.check_source(TickSlotTests.SRC, "t.dal").module
        for seed in (1, 2):
            tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 80, seed), warnings_as_errors=True)

    def test_actor_logic_with_methods_commands_and_lifecycle(self):
        src = """
logic Gun : actor;
input Fire: bool;
input Speed: float;
state Ammo: int = 5;
state Shots: int;
state Armed: bool;
timer Cooldown = 0.2;

view can_fire() -> bool { return Ammo > 0 && Cooldown.ready; }
pub view Remaining() -> int { return Ammo; }
pub method Reload(n: int) { Ammo = n; host.set_max_walk_speed(Speed * 0.5); }
method shoot() -> bool {
    if (!can_fire()) { return false; }
    Ammo -= 1;
    Shots += 1;
    start(Cooldown);
    host.jump();
    return true;
}
on begin_play { Armed = true; host.set_max_walk_speed(100.0); }
on end_play { Armed = false; host.stop_jumping(); }
update(dt) {
    if (Fire) { let ok = shoot(); if (!ok) { Reload(3); } }
}
"""
        m = dal.check_source(src, "gun.dal").module
        for seed in (1, 2, 3):
            frames = harness.random_frames(m, 120, seed)
            frames[-1]["end_play"] = True
            tcc.CppCompileTests.run_case(self, m, frames, warnings_as_errors=True)
        files = generate_cpp(m)
        self.assertIn("UFUNCTION(BlueprintPure, Category = \"DAL|View\")\n\tint32 Remaining() const;", files["Gun.h"])
        self.assertIn("UFUNCTION(BlueprintCallable, Category = \"DAL|Method\")\n\tvoid Reload(int32 n);", files["Gun.h"])
        self.assertIn("bool DalMethod_shoot();", files["Gun.h"])
        self.assertIn("virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;", files["Gun.h"])

    def test_pub_view_of_an_animation_instance_is_thread_safe(self):
        src = "logic Pv;\nstate S: float;\npub view GetS() -> float { return S; }\nupdate(dt) { S += dt; }"
        files = generate_cpp(dal.check_source(src, "pv.dal").module)
        self.assertIn('UFUNCTION(BlueprintPure, Category = "DAL|View", Meta = (BlueprintThreadSafe))\n\tfloat GetS() const;', files["Pv.h"])

    def test_a_peer_can_be_called_like_a_sample_local(self):
        # `Character` is also the name of a local of the generated sampling code: the local is renamed
        files = dict(FILES)
        files["Driver.dal"] = """
use "als_lib.dal";
peer Character: AlsCharacter;
logic Driver : actor;
input Velocity: vec3 <- host.velocity;
input Crouching: bool <- host.is_crouching;
input Yaw: rotator <- host.control_rotation;
state Seen: float;
state Y: float;
state Mode: AlsStance;
input CharStance: AlsStance <- Character.Stance;
update(dt) { Seen = length(Velocity); Y = Yaw.yaw; Mode = CharStance; if (Crouching) { Character.SetStance(AlsStance.Crouching); } }
"""
        mods = {n: dal.check_source(files[n + ".dal"], n + ".dal", files=files).module for n in ("AlsCharacter", "Driver", "AlsAnim")}
        text = generate_cpp(mods["Driver"])["Driver.cpp"]
        self.assertIn("const APawn* Pawn = Cast<APawn>(GetOwner());", text)
        self.assertIn("const ACharacter* DalCharacter = Cast<ACharacter>(Pawn);", text)
        self.assertIn("CharStance = Character != nullptr ? Character->Stance : FGameplayTag::EmptyTag;", text)
        frames = tcc.round_frames(harness.random_system_frames(mods, 40, 2))
        cpp, _ = cpp_system.build_and_run_system(mods, frames)
        compare_system(self, mods, harness.run_system(mods, frames, "python"), cpp)


if __name__ == "__main__":
    unittest.main()
