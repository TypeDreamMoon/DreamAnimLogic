"""DalRuntime (Unreal/DalRuntime): the pieces of the plugin that the compiler's output depends on stay in step with the compiler.

The plugin itself is built and run in the Unreal sandbox host (Unreal/AlsHost); these tests only catch the drift that needs no engine.
"""
import re
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from dal import cppbackend

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "Unreal" / "DalRuntime" / "Source" / "DalRuntime"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class DalRuntimeTests(unittest.TestCase):
    def test_the_plugin_carries_the_compilers_host_header(self):
        # DalHost.h is copied next to the generated code and into DalRuntime; the event router must be one object, so both have to be the same text
        self.assertEqual(text(REPO / "dal" / "cpp" / "DalHost.h"), text(RUNTIME / "Public" / "DalHost.h"))

    def test_the_router_is_defined_once_in_the_plugin(self):
        header = text(RUNTIME / "Public" / "DalHost.h")
        self.assertIn("class DALRUNTIME_API FEventRouter", header)
        source = text(RUNTIME / "Private" / "DalEventRouter.cpp")
        for fn in ("Register", "Unregister", "Post", "PostNotify"):
            self.assertIn(f"void DalHost::FEventRouter::{fn}(", source)
        self.assertIn("DAL_ROUTER_EXTERNAL=1", text(RUNTIME / "DalRuntime.Build.cs"))

    def test_every_event_kind_of_the_header_the_movement_component_posts_is_posted(self):
        cmc = text(RUNTIME / "Private" / "DalCharacterMovementComponent.cpp")
        for kind in ("MovementModeChanged", "Jumped", "Landed", "CrouchStarted", "CrouchEnded", "Teleported", "PhysicsRotation"):
            self.assertIn(f"EEventKind::{kind}", cmc, kind)

    def test_what_the_executors_call_on_the_runtime_classes_exists(self):
        # the generated code calls these members of DalRuntime's classes (cppbackend.EXEC); each one has to be declared there
        cmc = text(RUNTIME / "Public" / "DalCharacterMovementComponent.h")
        rag = text(RUNTIME / "Public" / "DalRagdollService.h")
        used = set()
        for guards, body in cppbackend.EXEC.values():
            if any("DalAlsMovement" in g for g in guards) or any("DalRagdoll" in g for g in guards):
                used.update(re.findall(r"Dal(?:AlsMovement|Ragdoll)->(\w+)", " ".join(body)))
        used.add("TryConsumePrePenetrationAdjustmentVelocity")
        self.assertTrue(used >= {"MaxAccelerationWalking", "bInputBlocked", "bMovementModeLocked", "SetRootMotionOverride", "ClearRootMotionOverride",
                                 "Begin", "End", "LimitSpeed", "SetMotorStrength", "AddForce"}, used)
        for name in used:
            self.assertTrue(re.search(r"\b%s\b" % name, cmc) or re.search(r"\b%s\b" % name, rag), name)

    def test_the_test_character_has_what_the_c3_scenarios_call(self):
        header = text(RUNTIME / "Public" / "DalTestCharacter.h")
        for fn in ("SetMovementModeLocked", "SetInputBlocked", "RootMotionOverride", "RootMotionClear"):
            self.assertIn(f" {fn}(", header)


if __name__ == "__main__":
    unittest.main()
