"""The movement base in the host protocol (milestone: moving bases): the five reads of the based movement of the character, the command that stands the
character on a component, the query whether a character on a component follows it, and the hook slot `prepare_root_motion` where the movement component
prepares its root motion sources.

* the tables (`dal.hostapi`), the stand-in hosts' defaults and the `DalHost.h` version;
* the language: `update(dt) in prepare_root_motion` is an actor logic's, comes after `physics_walking` whatever the order it is written in;
* the semantics, in the interpreter, the generated Python and the generated C++ (against the stand-in engine): a program that refreshes ALS's movement base
  state from the reads (`bBaseChanged`, the rotation delta of a base the character follows), asks the query and stands on a component.  The C++ hook is
  run through DalHost::FEventRouter::RunHook like the movement component of DalRuntime does.
"""

import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

import cpp_system
import dal
import test_cpp_compile as tcc
from dal import harness, ir
from dal import runtime as rt
from dal.cppbackend import generate_cpp
from dal.hostapi import COMMANDS, HOST_ITEMS, QUERIES, HostV1
from dal.interp import Interpreter
from dal.mockhost import MockHost
from dal.pybackend import generate_python, load_class

MB = """
logic Mb : actor;
input Base: primitive <- host.movement_base;
input Loc: vec3d <- host.movement_base_location;
input Rot: quatd <- host.movement_base_rotation;
input Rel: bool <- host.movement_base_has_relative_location;
input RelRot: bool <- host.movement_base_relative_rotation;
input Go: bool;
state Prev: primitive = none;
state PrevRot: quatd = quatd(0d, 0d, 0d, 1d);
state Changed: bool;
state HasRelRot: bool;
state Delta: rotatord;
state Where: vec3d;
state Uses: bool;
state Hooked: int;
update(dt) {
    Changed = Base != Prev;
    Prev = Base;
    HasRelRot = Rel && RelRot;
    Delta = Rel && !Changed ? to_rotator(Rot * quat_inverse(PrevRot)) : rotatord(0d, 0d, 0d);
    PrevRot = Rot;
    Where = Loc;
    Uses = host.primitive_uses_relative_location(Base);
    if (Go) { host.set_movement_base(Base); }
}
update(dt) in prepare_root_motion {
    Hooked += 1;
    host.root_motion_override(vec3d(1d, 0d, 0d), quatd(0d, 0d, 0d, 1d));
}
"""
SCHEDULE = [("Mb", "pre_movement"), ("Mb", "prepare_root_motion")]


def module(src=MB):
    return dal.check_source(src, "Mb.dal").module


def yaw_quat(deg):
    import math
    h = math.radians(deg) * 0.5
    return (0.0, 0.0, math.sin(h), math.cos(h))


def script():
    """The frames: standing on the ground (static), then on a platform (a sphere of the scene that can move) that turns 10 degrees per frame, then in the
    air (no base), then on the ground again."""
    plat = {"Sphere0": {"movable": True}}
    steps = [  # (base, location, yaw of the base, has relative location, relative rotation, Go)
        (None, (0.0, 0.0, 0.0), 0.0, False, False, False),
        ("Ground", (5.0, 6.0, 0.0), 0.0, False, False, False),
        ("Ground", (5.0, 6.0, 0.0), 0.0, False, False, False),
        ("Sphere0", (100.0, 0.0, 30.0), 0.0, True, False, True),
        ("Sphere0", (110.0, 0.0, 30.0), 10.0, True, False, False),
        ("Sphere0", (120.0, 0.0, 30.0), 20.0, True, True, False),
        ("Sphere0", (130.0, 0.0, 30.0), 30.0, True, True, False),
        (None, (0.0, 0.0, 0.0), 0.0, False, False, False),
        ("Ground", (5.0, 6.0, 0.0), 0.0, False, False, False),
    ]
    frames = []
    for base, loc, yaw, rel, relrot, go in steps:
        frames.append({"movement_base": base, "movement_base_location": loc, "movement_base_rotation": yaw_quat(yaw), "movement_base_has_relative_location": rel,
                       "movement_base_relative_rotation": relrot, "world": {"ground_z": 0.0, "spheres": [((100.0, 0.0, 30.0), 50.0)], "primitives": plat},
                       "free": {"Mb": {"Go": go}}})
    return frames


def single_frames():
    """The script as frames of the single logic driver (the stand-in engine has a scene of primitives: Ground, Sphere0 ...)."""
    frames = []
    for s in script():
        f = {k: v for k, v in s.items() if k != "free"}
        f["free"] = dict(s["free"]["Mb"])
        f["dt"] = 0.1
        frames.append(f)
    return frames


class ProtocolTests(unittest.TestCase):
    def test_the_reads(self):
        want = {"movement_base": "primitive", "movement_base_location": "vec3d", "movement_base_rotation": "quatd",
                "movement_base_has_relative_location": "bool", "movement_base_relative_rotation": "bool"}
        for name, ty in want.items():
            self.assertEqual(HOST_ITEMS[name].ty.name, ty, name)
            self.assertTrue(hasattr(HostV1, name) or hasattr(MockHost, name), name)

    def test_the_command_and_the_query(self):
        self.assertEqual([p.name for p in COMMANDS["set_movement_base"].params], ["primitive"])
        q = QUERIES["primitive_uses_relative_location"]
        self.assertEqual((q.ret.name, [p.name for p in q.params], q.thread_safe), ("bool", ["primitive"], True))
        self.assertTrue(hasattr(HostV1, "set_movement_base") and hasattr(HostV1, "primitive_uses_relative_location"))
        self.assertEqual(QUERIES["primitive_transform"].ret.name, "transformd")

    def test_the_stand_in_hosts_have_no_base(self):
        h = MockHost()
        self.assertEqual((h.movement_base, h.movement_base_has_relative_location, h.movement_base_relative_rotation), (None, False, False))
        self.assertFalse(h.primitive_uses_relative_location(None))
        self.assertTrue(MockHost().primitive_uses_relative_location("Sphere0") is False)             # a scene shape that is not scripted as movable

    def test_the_header_version_and_copies(self):
        root = Path(__file__).resolve().parents[1]
        a = (root / "dal" / "cpp" / "DalHost.h").read_text(encoding="utf-8")
        b = (root / "Unreal" / "DalRuntime" / "Source" / "DalRuntime" / "Public" / "DalHost.h").read_text(encoding="utf-8")
        self.assertEqual(a, b)
        self.assertIn("#define DAL_HOST_VERSION 4", a)
        self.assertIn("SetMovementBase", a)
        self.assertIn("UPrimitiveComponent* Primitive", a)


class LanguageTests(unittest.TestCase):
    def test_the_slot_is_a_hook_slot_after_physics_walking(self):
        self.assertEqual(ir.HOOK_SLOTS, ("physics_walking", "prepare_root_motion"))
        src = ("logic T : actor;\nstate A: int;\nupdate(dt) in prepare_root_motion { A = 3; }\nupdate(dt) in physics_walking { A = 2; }\n"
               "update(dt) in after_movement { A = 1; }")
        self.assertEqual([u.phase for u in module(src).updates], ["after_movement", "physics_walking", "prepare_root_motion"])

    def test_an_animation_logic_has_no_slots(self):
        with self.assertRaises(dal.DalError) as cm:
            dal.check_source("logic M;\nupdate(dt) in prepare_root_motion { }", "t.dal")
        self.assertEqual(cm.exception.diagnostics[0].code, "DAL5064")

    def test_the_program_compiles_and_lists_the_slot(self):
        m = module()
        self.assertEqual([u.phase for u in m.updates], ["pre_movement", "prepare_root_motion"])
        self.assertIn("'prepare_root_motion'", generate_python(m))


class SemanticsTests(unittest.TestCase):
    def objects(self, backend):
        m = module()
        host = harness.ScriptedHost()
        obj = load_class(m, generate_python(m))(host) if backend == "python" else Interpreter(m, host)
        return obj, host

    def play(self, backend):
        obj, host = self.objects(backend)
        out = []
        for f in script():
            host.load(f)
            obj.set_input("Go", f["free"]["Mb"]["Go"])
            obj.update_phase("pre_movement", 0.1)
            obj.update_phase("prepare_root_motion", 0.1)
            snap = obj.snapshot()
            out.append((snap["Changed"], snap["HasRelRot"], snap["Delta"], snap["Uses"], snap["Hooked"], [c[0] for c in host.calls]))
            host.calls.clear()
        return out

    def test_the_base_state_follows_the_reads(self):
        for backend in ("python", "interp"):
            out = self.play(backend)
            self.assertEqual([o[0] for o in out], [False, True, False, True, False, False, False, True, True], backend)       # (the first frame: none -> none)
            self.assertEqual([o[1] for o in out], [False, False, False, False, False, True, True, False, False], backend)
            yaw = [round(o[2].yaw if hasattr(o[2], "yaw") else o[2][1], 4) for o in out]
            self.assertEqual(yaw[:4], [0.0, 0.0, 0.0, 0.0], backend)                  # (no delta for a base that changed, or that is not followed)
            self.assertAlmostEqual(yaw[4], 10.0, places=4, msg=backend)
            self.assertAlmostEqual(yaw[5], 10.0, places=4, msg=backend)
            self.assertAlmostEqual(yaw[6], 10.0, places=4, msg=backend)
            self.assertEqual(yaw[7:], [0.0, 0.0], backend)

    def test_the_query_and_the_command(self):
        for backend in ("python", "interp"):
            out = self.play(backend)
            self.assertEqual([o[3] for o in out], [False, False, False, True, True, True, True, False, False], backend)
            cmds = [o[5] for o in out]
            self.assertEqual([c.count("set_movement_base") for c in cmds], [0, 0, 0, 1, 0, 0, 0, 0, 0], backend)

    def test_the_hook_slot_runs_when_the_host_asks(self):
        for backend in ("python", "interp"):
            out = self.play(backend)
            self.assertEqual([o[4] for o in out], list(range(1, 10)), backend)
            self.assertTrue(all("root_motion_override" in o[5] for o in out), backend)

    def test_interpreter_and_python_agree(self):
        a, b = self.play("python"), self.play("interp")
        self.assertEqual([(x[0], x[1], x[3], x[4], x[5]) for x in a], [(x[0], x[1], x[3], x[4], x[5]) for x in b])


@unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
class CppTests(unittest.TestCase):
    def test_generated_text(self):
        out = generate_cpp(module())
        cpp, hdr = out["Mb.cpp"], out["Mb.h"]
        self.assertIn('if (DalHook == FName(TEXT("prepare_root_motion")))', cpp)
        self.assertIn("Cast<UPrimitiveComponent>(Movement->GetMovementBaseObject())", cpp)
        self.assertIn("MovementBaseUtility::GetMovementBaseTransform(&DalBasedMovement.MovementBaseInterfaceData", cpp)
        self.assertIn("GetBasedMovement().HasRelativeLocation()", cpp)
        self.assertIn("GetBasedMovement().bRelativeRotation", cpp)
        self.assertIn("FMovementBaseInterfaceData DalBaseData(DalCommand.Primitive);", cpp)
        self.assertIn("DalMovement->SetBase(&DalBaseData);", cpp)
        self.assertIn("MovementBaseUtility::UseRelativeLocation(&DalBaseData)", cpp)
        self.assertNotIn("GetMovementBase()", cpp)                                       # (deprecated in 5.8: the interface data it is)

    def test_the_stand_in_engine_agrees_with_python(self):
        # (the stand-in driver runs every slot of the logic in C++, the Python run of the harness does not run the hook slot: the program without it)
        tcc.CppCompileTests.run_case(self, module(MB.split("update(dt) in prepare_root_motion")[0]), single_frames(), warnings_as_errors=True, default=2e-3)


if __name__ == "__main__":
    unittest.main()
