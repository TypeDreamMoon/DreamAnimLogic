"""The C++ backend for language v1 / v2: golden files of the two actor examples and of an anim instance with host
traffic, plus structural invariants (threading, queues, delegates, the shape of the generated component).

Regenerate the golden files with:  UPDATE_GOLDEN=1 python -I -m unittest discover -s tests -p "test_cpp_v1.py"
(review the diff before committing).  tests/test_cpp_compile.py compiles and runs the same code against the stand-in engine.
"""

import os
import re
import unittest

import _bootstrap  # noqa: F401
from _bootstrap import EXAMPLES, TESTS

import dal
from dal.cppbackend import CppOptions, generate_cpp
from v1_sources import ALL_COMMANDS, ANIM_V1, ANIM_V4_COMMANDS, PROBE

GOLDEN = os.path.join(TESTS, "golden")
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"


def example(name):
    return dal.check_file(os.path.join(EXAMPLES, name)).module


def read(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def body(text, signature):
    start = text.index(signature)
    brace = text.index("{", start)
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[brace:i + 1]
    raise AssertionError(signature)


class GoldenTests(unittest.TestCase):
    def check_golden(self, files, wanted, prefix):
        for name in wanted:
            path = os.path.join(GOLDEN, f"{prefix}.{name}")
            if UPDATE:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(files[name])
            self.assertTrue(os.path.isfile(path), f"missing golden file {path}; run with UPDATE_GOLDEN=1")
            self.assertEqual(files[name], read(path), f"{name} differs from {path}")

    def test_melee_combo(self):
        files = generate_cpp(example("melee_combo.dal"), CppOptions(module_api="MYGAME_API"))
        self.check_golden(files, ["MeleeCombo.h", "MeleeCombo.cpp"], "melee_combo")

    def test_lockon_camera(self):
        files = generate_cpp(example("lockon_camera.dal"))
        self.check_golden(files, ["LockOnCamera.h", "LockOnCamera.cpp"], "lockon_camera")

    def test_anim_instance_with_host_traffic(self):
        files = generate_cpp(dal.check_source(ANIM_V1, "anim_v1.dal").module)
        self.check_golden(files, ["AnimV1.h", "AnimV1.cpp"], "anim_v1")

    def test_host_header_is_copied_verbatim_and_only_when_needed(self):
        here = os.path.join(os.path.dirname(dal.__file__), "cpp", "DalHost.h")
        for name in ("melee_combo.dal", "lockon_camera.dal"):
            files = generate_cpp(example(name))
            self.assertEqual(files["DalHost.h"], read(here).replace("\r\n", "\n"))
            self.assertNotIn("\r", files["DalHost.h"])
        for name, header in (("als_lean.dal", "AlsLean.h"), ("hero_gait.dal", "HeroGait.h")):
            files = generate_cpp(example(name))
            self.assertNotIn("DalHost.h", files)
            self.assertNotIn("DalHost", files[header])

    def test_output_is_lf_only_and_deterministic(self):
        for module in (example("melee_combo.dal"), example("lockon_camera.dal")):
            a, b = generate_cpp(module), generate_cpp(module)
            self.assertEqual(a, b)
            for name, text in a.items():
                self.assertNotIn("\r", text, name)
                self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"), name)


class ActorComponentTests(unittest.TestCase):
    def setUp(self):
        self.melee = generate_cpp(example("melee_combo.dal"), CppOptions(module_api="MYGAME_API"))
        self.lock = generate_cpp(example("lockon_camera.dal"))
        self.mh, self.mc = self.melee["MeleeCombo.h"], self.melee["MeleeCombo.cpp"]
        self.lh, self.lc = self.lock["LockOnCamera.h"], self.lock["LockOnCamera.cpp"]

    def test_the_class_is_a_spawnable_actor_component(self):
        self.assertIn("class MYGAME_API UMeleeCombo : public UActorComponent", self.mh)
        self.assertIn('#include "Components/ActorComponent.h"', self.mh)
        self.assertNotIn("UAnimInstance :", self.mh)
        self.assertIn("UCLASS(ClassGroup = (Dal), Meta = (BlueprintSpawnableComponent))", self.mh)
        self.assertIn("virtual void TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction) override;", self.mh)
        self.assertNotIn("NativeUpdateAnimation", self.mh)
        self.assertIn("PrimaryComponentTick.TickGroup = TG_PrePhysics;", self.mc)

    def test_one_tick_samples_updates_and_flushes_in_that_order(self):
        tick = body(self.mc, "void UMeleeCombo::TickComponent")
        order = ["DalBindHost();", "DalSampleInputs();", "DalTakeEvents();", "DalUpdate(DeltaTime);", "DalFlushCommands();"]
        positions = [tick.index(x) for x in order]
        self.assertEqual(positions, sorted(positions))

    def test_update_runs_timers_then_handlers_then_the_body(self):
        update = body(self.mc, "void UMeleeCombo::DalUpdate")
        self.assertLess(update.index("AttackBuffer = DalMath::Max(AttackBuffer - dt, 0.0f);"), update.index("DalDispatchEvents();"))
        self.assertLess(update.index("DalDispatchEvents();"), update.index("const bool attacking"))

    def test_commands_are_queued_not_executed_by_the_logic(self):
        for text in (body(self.mc, "void UMeleeCombo::DalUpdate"), *[body(self.mc, f"void UMeleeCombo::DalHandler_{k}(") for k in range(10)]):
            for banned in ("->MaxWalkSpeed", "Montage_Play", "SetMovementMode", "GetCharacterMovement"):
                self.assertNotIn(banned, text)
        update = body(self.mc, "void UMeleeCombo::DalUpdate")
        self.assertIn("DalEnqueue(DalHost::Cmd::MontagePlay(Dal_MontageId_Light1, 1.0f, static_cast<int32>(ELight1Section::None)));", update)
        self.assertIn("DalEnqueue(DalHost::Cmd::SetMaxWalkSpeed(attacking ? AttackMoveSpeed : WalkSpeed));", update)

    def test_commands_run_on_the_game_thread_from_a_swapped_queue(self):
        flush = body(self.mc, "void UMeleeCombo::DalFlushCommands")
        self.assertIn("FScopeLock DalLock(&DalQueueLock);", flush)
        self.assertIn("DalPending = MoveTemp(DalCommandQueue);", flush)
        self.assertLess(flush.index("DalPending = MoveTemp"), flush.index("DalExecuteCommand(DalCommand);"))
        execute = body(self.mc, "void UMeleeCombo::DalExecuteCommand")
        self.assertIn("DalMovement->MaxWalkSpeed = DalCommand.F;", execute)
        self.assertIn("DalAnim->Montage_Play(DalAsset, DalCommand.F);", execute)
        self.assertIn("DalAnim->Montage_JumpToSection(DalSectionName(DalCommand.Montage, DalCommand.SectionA), DalAsset);", execute)
        self.assertEqual(execute.count("case DalHost::ECommand::"), 2)       # only the commands the program uses

    def test_events_are_posted_under_the_lock_and_dispatched_in_order(self):
        self.assertIn("DalEventQueue.Add(DalEvent);", body(self.mc, "void UMeleeCombo::DalPostEvent"))
        dispatch = body(self.mc, "void UMeleeCombo::DalDispatchEvents")
        self.assertIn("DalFrameEvents.Reset();", dispatch)
        order = [dispatch.index(f"DalHandler_{k}(") for k in range(10)]
        self.assertEqual(order, sorted(order))                                # declaration order
        self.assertIn("DalHandler_5(DalEvent.Value.X != 0.0f);", dispatch)    # a bool action value
        self.assertIn("DalHandler_2(DalEvent.bFlag);", dispatch)              # montage ended: interrupted

    def test_delegates_and_input_are_bound_lazily_and_unbound_at_end_play(self):
        bind = body(self.mc, "void UMeleeCombo::DalBindAnim")
        self.assertIn("DalAnim->OnMontageEnded.AddUniqueDynamic(this, &UMeleeCombo::DalOnMontageEnded);", bind)
        self.assertIn("DalAnim->OnPlayMontageNotifyBegin.AddUniqueDynamic(this, &UMeleeCombo::DalOnPlayMontageNotifyBegin);", bind)
        self.assertIn("DalAnim->OnPlayMontageNotifyEnd.AddUniqueDynamic(this, &UMeleeCombo::DalOnPlayMontageNotifyEnd);", bind)
        self.assertNotIn("OnMontageBlendingOut", bind)                        # no handler for it
        self.assertIn("RemoveDynamic(this, &UMeleeCombo::DalOnMontageEnded)", body(self.mc, "void UMeleeCombo::DalUnbindAnim"))
        host = body(self.mc, "void UMeleeCombo::DalBindHost")
        self.assertIn("DalInput->BindAction(DalAction_Attack, ETriggerEvent::Started, this, &UMeleeCombo::DalOnInput_Attack_Started);", host)
        self.assertIn("DalUnbindAnim(DalBoundAnim.Get());", body(self.mc, "void UMeleeCombo::EndPlay"))
        for fn in ("DalOnMontageEnded", "DalOnPlayMontageNotifyBegin", "DalOnPlayMontageNotifyEnd"):
            self.assertIn(f"UFUNCTION()\n\tvoid {fn}(", self.mh)                # dynamic delegates need reflected functions

    def test_a_notify_window_is_told_from_a_point_notify_by_its_state_class(self):
        begin = body(self.mc, "void UMeleeCombo::DalOnPlayMontageNotifyBegin")
        self.assertIn("DalPayload.NotifyEvent->NotifyStateClass != nullptr", begin)
        self.assertIn("bDalWindow ? DalHost::EEventKind::NotifyBegin : DalHost::EEventKind::Notify", begin)
        self.assertIn('#include "Animation/AnimNotifies/AnimNotify.h"', self.mh)

    def test_montages_and_actions_are_assets_assigned_in_the_class_defaults(self):
        for name in ("Light1", "Light2", "Light3"):
            self.assertIn(f"TObjectPtr<UAnimMontage> DalMontage_{name};", self.mh)
            self.assertIn(f"enum class E{name}Section : uint8", self.mh)
        self.assertIn("TObjectPtr<UInputAction> DalAction_Attack;", self.mh)
        self.assertIn('FName(TEXT("Windup")),', self.mc)

    def test_traces_and_targets_are_game_thread_world_queries(self):
        trace = body(self.lc, "DalHost::FHit ULockOnCamera::DalTraceLine")
        self.assertIn("DalWorld->LineTraceSingleByChannel(DalHit, DalStart, DalEnd, DalTraceChannel, DalParams);", trace)        # (the query takes the engine's own FVector)
        self.assertIn("SCENE_QUERY_STAT(DalTrace), false, GetOwner()", trace)         # the owner never blocks its own trace
        self.assertIn("DalResult.Point = FVector3f(DalEnd);", trace)                   # a miss ends at the trace end
        self.assertIn("UGameplayStatics::GetAllActorsWithTag(DalWorld, DalTargetTag, DalFound);", self.lc)
        self.assertIn("FName DalTargetTag = FName(TEXT(\"DalTarget\"));", self.lh)
        self.assertIn("TEnumAsByte<ECollisionChannel> DalTraceChannel = ECC_Visibility;", self.lh)
        self.assertIn("const DalHost::FHit sight = DalTraceLine(FVector(eye), FVector(TargetLocation));", self.lc)       # (the positions of a trace are double: the floats are widened)
        self.assertIn("Visible = !sight.bBlocking;", self.lc)

    def test_loops_become_counted_for_statements(self):
        self.assertIn("for (int32 i = 0; i < 8; ++i)", self.lc)

    def test_camera_commands_reach_the_spring_arm_and_the_controller(self):
        execute = body(self.lc, "void ULockOnCamera::DalExecuteCommand")
        self.assertIn("DalController->SetControlRotation(FRotator(DalCommand.R));", execute)
        self.assertIn("DalArm->TargetArmLength = DalCommand.F;", execute)
        self.assertIn('#include "GameFramework/SpringArmComponent.h"', self.lc)

    def test_every_generated_name_starts_with_dal(self):
        """User names cannot start with Dal<Upper>/Dal_ (names.py), so the generated members never collide with them."""
        for text in (self.mh, self.lh):
            for m in re.finditer(r"^\t(?:UFUNCTION\(\)\n\t)?[\w:<>\*& ]+? \*?(\w+)\(", text, re.M):
                name = m.group(1)
                if name in ("BeginPlay", "EndPlay", "TickComponent") or name.startswith("U"):
                    continue
                self.assertTrue(name.startswith("Dal"), name)


class AnimInstanceHostTrafficTests(unittest.TestCase):
    def setUp(self):
        files = generate_cpp(dal.check_source(ANIM_V1, "anim_v1.dal").module)
        self.h, self.c = files["AnimV1.h"], files["AnimV1.cpp"]

    def test_still_an_anim_instance_with_the_v0_overrides(self):
        self.assertIn("class UAnimV1 : public UAnimInstance", self.h)
        self.assertIn("virtual void NativeThreadSafeUpdateAnimation(float DeltaSeconds) override;", self.h)
        self.assertNotIn("TickComponent", self.h)

    def test_commands_run_at_the_start_of_the_next_game_thread_update(self):
        native = body(self.c, "void UAnimV1::NativeUpdateAnimation")
        order = ["DalFlushCommands();", "DalSampleInputs();", "DalTakeEvents();"]
        positions = [native.index(x) for x in order]
        self.assertEqual(positions, sorted(positions))
        worker = body(self.c, "void UAnimV1::NativeThreadSafeUpdateAnimation")
        self.assertNotIn("DalFlushCommands", worker)
        self.assertNotIn("DalExecuteCommand", worker)
        self.assertIn("DalAnim->Montage_Play(DalAsset, DalCommand.F);", body(self.c, "void UAnimV1::DalExecuteCommand"))

    def test_worker_thread_code_only_queues(self):
        banned = ("TryGetPawnOwner", "GetWorld", "Cast<", "GetCharacterMovement", "Montage_Play", "Montage_Stop", "->Jump")
        texts = [body(self.c, "void UAnimV1::DalUpdate"), body(self.c, "void UAnimV1::Kick"), body(self.c, "void UAnimV1::DalDispatchEvents")]
        texts += [body(self.c, f"void UAnimV1::DalHandler_{k}(") for k in range(5)]
        for text in texts:
            for token in banned:
                self.assertNotIn(token, text)
        self.assertIn("DalEnqueue(DalHost::Cmd::MontageStop(Dal_MontageId_Slash, -1.0f));", body(self.c, "void UAnimV1::Kick"))

    def test_the_anim_instance_binds_its_own_delegates_and_has_no_input_component(self):
        self.assertIn("DalBindAnim(this);", body(self.c, "void UAnimV1::NativeInitializeAnimation"))
        self.assertNotIn("DalBindHost", self.c)
        self.assertNotIn("UEnhancedInputComponent", self.c)
        self.assertIn("UFUNCTION()\n\tvoid AnimNotify_HitNotify();", self.h)
        notify = body(self.c, "void UAnimV1::AnimNotify_HitNotify")
        self.assertIn("DalEvent.Kind = DalHost::EEventKind::Notify;", notify)

    def test_montage_state_is_read_by_the_game_thread_sampling(self):
        sample = body(self.c, "void UAnimV1::DalSampleInputs")
        self.assertIn("const UAnimInstance* DalAnim = DalGetAnimInstance();", sample)
        self.assertIn("DalAnim->Montage_IsPlaying(DalMontage_Slash)", sample)
        self.assertIn("DalSectionIndex(Dal_MontageId_Slash, DalAnim->Montage_GetCurrentSection(DalMontage_Slash))", sample)
        self.assertIn("return this;", body(self.c, "UAnimInstance* UAnimV1::DalGetAnimInstance"))


class CoverageTests(unittest.TestCase):
    def test_every_host_command_has_a_constructor_an_enum_member_and_an_executor(self):
        from dal.cppbackend import EXEC, cmd_cpp
        from dal.hostapi import COMMANDS

        host_h = read(os.path.join(os.path.dirname(dal.__file__), "cpp", "DalHost.h"))
        self.assertEqual(set(EXEC), set(COMMANDS))
        for name in COMMANDS:
            self.assertRegex(host_h, rf"\binline FCommand {cmd_cpp(name)}\(", name)
            self.assertIn(f"\t\t{cmd_cpp(name)},\n", host_h, name)

    def test_the_all_commands_program_generates_every_executor(self):
        files = generate_cpp(dal.check_source(ALL_COMMANDS, "all_commands.dal").module)
        execute = body(files["AllCommands.cpp"], "void UAllCommands::DalExecuteCommand")
        from dal.hostapi import COMMANDS
        from dal.cppbackend import cmd_cpp

        anim = generate_cpp(dal.check_source(ANIM_V4_COMMANDS, "anim_commands.dal").module)       # the commands only an animation instance issues
        execute_anim = body(anim["AnimCommands.cpp"], "void UAnimCommands::DalExecuteCommand")
        for name in COMMANDS:
            self.assertTrue(f"case DalHost::ECommand::{cmd_cpp(name)}:" in execute or f"case DalHost::ECommand::{cmd_cpp(name)}:" in execute_anim, name)

    def test_the_probe_program_generates_every_query(self):
        files = generate_cpp(dal.check_source(PROBE, "probe.dal").module)
        c = files["Probe.cpp"]
        for fn in ("DalTraceLine", "DalTraceSphere", "DalTargetCount", "DalTargetId", "DalTargetLocation", "DalTargetIndex"):
            self.assertIn(f"::{fn}(", c)
        self.assertIn("SweepSingleByChannel(DalHit, DalStart, DalEnd, FQuat::Identity, DalTraceChannel, FCollisionShape::MakeSphere(DalRadius), DalParams)", c)

    def test_the_host_header_reserves_the_pieces_the_backend_emits(self):
        host_h = read(os.path.join(os.path.dirname(dal.__file__), "cpp", "DalHost.h"))
        for name in ("Notify", "NotifyBegin", "NotifyEnd", "MontageEnded", "MontageBlendingOut", "Input"):
            self.assertIn(f"\t\t{name},\n", host_h)
        for name in ("Started", "Triggered", "Ongoing", "Completed", "Canceled"):
            self.assertIn(f"\t\t{name},\n", host_h)


if __name__ == "__main__":
    unittest.main()
