"""The host commands the ALS character writes: set_max_walk_speed_crouched, set_max_acceleration_walking, set_ground_friction,
set_braking_friction_factor, set_input_blocked, set_actor_rotation(rotator, teleport = false), add_controller_yaw_input,
add_controller_pitch_input.  Tables, checker, mock host, generated C++ and the stand-in engine, the ALS sources."""

import os
import unittest

import _bootstrap  # noqa: F401
from _bootstrap import ROOT

import dal
from dal import harness, hostapi
from dal.cppbackend import EXEC, generate_cpp
from dal.mockhost import MockHost

try:
    import test_cpp_compile as tcc
except Exception:                                   # pragma: no cover
    tcc = None

NAMES = ("set_max_walk_speed_crouched", "set_max_acceleration_walking", "set_ground_friction", "set_braking_friction_factor",
         "set_input_blocked", "set_actor_rotation", "add_controller_yaw_input", "add_controller_pitch_input")

PROGRAM = """
logic Chr : actor;
input F: float;
input R: rotator;
input B: bool;
update(dt) {
    host.set_max_walk_speed_crouched(F);
    host.set_max_acceleration_walking(F * 2.0);
    host.set_ground_friction(F + 1.0);
    host.set_braking_friction_factor(F - 1.0);
    host.set_input_blocked(B);
    host.set_actor_rotation(R);
    host.set_actor_rotation(R, B);
    host.add_controller_yaw_input(F);
    host.add_controller_pitch_input(-F);
}
"""


def compile_ok(src, **kw):
    return dal.check_source(src, "t.dal", **kw).module


def codes(src):
    try:
        dal.check_source(src, "t.dal")
    except dal.DalError as e:
        return [d.code for d in e.diagnostics if d.severity == "error"]
    return []


class TableTests(unittest.TestCase):
    def test_every_command_is_in_every_table(self):
        with open(os.path.join(os.path.dirname(dal.__file__), "cpp", "DalHost.h"), encoding="utf-8") as f:
            host_h = f.read()
        for name in NAMES:
            with self.subTest(name=name):
                self.assertIn(name, hostapi.COMMANDS)
                self.assertIn(name, EXEC)
                self.assertTrue(callable(getattr(hostapi.HostV1, name)))
                self.assertTrue(callable(getattr(MockHost, name)))
                self.assertIn(f"inline FCommand {dal.cppbackend.cmd_cpp(name)}(", host_h)

    def test_the_actor_rotation_takes_an_optional_teleport_flag(self):
        cmd = hostapi.COMMANDS["set_actor_rotation"]
        self.assertEqual((len(cmd.params), cmd.min_args, cmd.defaults), (2, 1, (False,)))

    def test_the_markdown_lists_them(self):
        text = hostapi.protocol_markdown()
        for name in NAMES:
            self.assertIn(f"`{name}`", text)


class CheckerTests(unittest.TestCase):
    def test_the_program_is_accepted(self):
        self.assertEqual(codes(PROGRAM), [])

    def test_the_arguments_are_checked(self):
        for stmt, want in (("host.set_actor_rotation(F);", "DAL4002"), ("host.set_actor_rotation(R, B, B);", "DAL4041"),
                           ("host.set_actor_rotation();", "DAL4041"), ("host.set_input_blocked(F);", "DAL4002"),
                           ("host.set_ground_friction(B);", "DAL4002"), ("host.add_controller_yaw_input(R);", "DAL4002")):
            with self.subTest(stmt=stmt):
                self.assertEqual(codes(PROGRAM.replace("host.set_max_walk_speed_crouched(F);", stmt, 1)), [want], stmt)

    def test_an_animation_instance_may_issue_them_too(self):
        self.assertEqual(codes("logic A : anim;\nupdate(dt) { host.set_ground_friction(1.0); }"), [])


class SemanticsTests(unittest.TestCase):
    def test_the_interpreter_and_python_agree_and_the_host_gets_the_canonical_arguments(self):
        module = compile_ok(PROGRAM)
        frames = [{"dt": 0.1, "free": {"F": 3.0, "R": (10.0, 20.0, 30.0), "B": True}}, {"dt": 0.1, "free": {"F": 0.5, "R": (0.0, 0.0, 0.0), "B": False}}]
        py = harness.run_python(module, frames)
        self.assertEqual(harness.compare(py, harness.run_interpreter(module, frames)), [])
        calls = py[0]["__cmds__"]
        self.assertEqual(calls[0], ("set_max_walk_speed_crouched", (3.0,)))
        self.assertEqual(calls[1], ("set_max_acceleration_walking", (6.0,)))
        self.assertEqual(calls[4], ("set_input_blocked", (True,)))
        self.assertEqual(calls[5], ("set_actor_rotation", ((10.0, 20.0, 30.0), False)))      # the teleport flag defaults to false
        self.assertEqual(calls[6], ("set_actor_rotation", ((10.0, 20.0, 30.0), True)))
        self.assertEqual(calls[8], ("add_controller_pitch_input", (-3.0,)))

    def test_the_mock_host_echoes_the_set_commands(self):
        host = MockHost()
        host.set_ground_friction(8.0)
        host.set_input_blocked(True)
        self.assertEqual((host.state["ground_friction"], host.state["input_blocked"]), (8.0, True))

    def test_random_frames_agree(self):
        module = compile_ok(PROGRAM)
        for seed in (1, 2):
            self.assertEqual(harness.verify(module, 100, seed), [])


class CppStructureTests(unittest.TestCase):
    def setUp(self):
        self.files = generate_cpp(compile_ok(PROGRAM))
        self.cpp, self.h = self.files["Chr.cpp"], self.files["Chr.h"]

    def test_the_executors(self):
        cpp = self.cpp
        self.assertIn("DalMovement->MaxWalkSpeedCrouched = DalCommand.F;", cpp)
        self.assertIn("DalMovement->GroundFriction = DalCommand.F;", cpp)
        self.assertIn("DalMovement->BrakingFrictionFactor = DalCommand.F;", cpp)
        self.assertIn("DalPawn->AddControllerYawInput(DalCommand.F);", cpp)
        self.assertIn("DalPawn->AddControllerPitchInput(DalCommand.F);", cpp)
        self.assertIn("DalPawn->SetActorRotation(FRotator(DalCommand.R), DalCommand.B0 ? ETeleportType::TeleportPhysics : ETeleportType::None);", cpp)

    def test_the_two_that_belong_to_the_movement_component_of_dalruntime(self):
        cpp = self.cpp
        self.assertIn("if (UDalCharacterMovementComponent* DalAlsMovement = Cast<UDalCharacterMovementComponent>(DalMovement))", cpp)
        self.assertIn("DalAlsMovement->MaxAccelerationWalking = DalCommand.F;", cpp)
        self.assertIn("DalAlsMovement->bInputBlocked = DalCommand.B0;", cpp)
        self.assertIn('#include "DalCharacterMovementComponent.h"', self.cpp)
        self.assertIn("// Module dependencies: Core, CoreUObject, Engine, DalRuntime", self.h)

    def test_a_program_without_them_has_no_dalruntime_dependency(self):
        files = generate_cpp(compile_ok("logic T : actor;\nupdate(dt) { host.set_ground_friction(2.0); host.add_controller_yaw_input(1.0); }"))
        self.assertIn("// Module dependencies: Core, CoreUObject, Engine\n", files["T.h"])
        self.assertNotIn("DalCharacterMovementComponent", files["T.h"] + files["T.cpp"])


@unittest.skipIf(tcc is None or tcc.COMPILER is None, "no C++ compiler found")
class CppCompileTests(unittest.TestCase):
    def test_the_effects_match_python(self):
        module = compile_ok(PROGRAM)
        runner = tcc.CppCompileTests("test_hero_gait_matches_python")
        effects = 0
        for seed in (1, 2):
            effects += runner.run_case(module, harness.random_frames(module, 40, seed), warnings_as_errors=True)
        self.assertGreaterEqual(effects, 40 * 2 * 9)


class AlsSourceTests(unittest.TestCase):
    """The sources that needed them compile with the compiler alone (no test shim)."""

    def test_the_character_and_the_input_check(self):
        for name in ("AlsCharacter", "AlsInput"):
            with self.subTest(name=name):
                dal.check_file(os.path.join(ROOT, "als", f"{name}.dal"))          # raises DalError with the diagnostics when there are errors


if __name__ == "__main__":
    unittest.main()
