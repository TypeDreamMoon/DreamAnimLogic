"""The pose, mesh and skeleton reads (G30): bone transforms, the mesh transform, the reference-skeleton path, the root motion mode
and ragdoll support; two-name reads, the checker, both Python implementations and C++."""

import unittest

import _bootstrap  # noqa: F401

import dal
import test_cpp_compile as tcc
from dal import harness
from dal import runtime as rt
from dal.cppbackend import generate_cpp
from dal.hostapi import HostV1
from dal.interp import Interpreter
from dal.mockhost import MockHost
from dal.pybackend import generate_python, load_class

SRC = """
logic Bn : actor;
input Pelvis: transform <- host.bone_component_transform("pelvis");
input Foot: transform <- host.bone_world_transform("ik_foot_l");
input Mesh: transform <- host.mesh_world_transform;
input Axis: vec3 <- host.ref_path_child_translation("calf_l", "thigh_l");
input Rm: RootMotionMode <- host.root_motion_mode;
input Rag: bool <- host.ragdoll_supported;
state Out: vec3;
state M: RootMotionMode;
state R: bool;
update(dt) {
    Out = transform_position(Foot, Axis) + inverse_transform_position(Mesh, Pelvis.location);
    M = Rm;
    R = Rag && Rm == RootMotionMode.IgnoreRootMotion;
}
"""

SKELETON = {"pelvis": {"parent": None, "translation": (0.0, 0.0, 90.0)},
            "thigh_l": {"parent": "pelvis", "translation": (0.0, -8.0, 0.0)},
            "calf_l": {"parent": "thigh_l", "translation": (0.0, 0.0, -42.0)},
            "foot_l": {"parent": "calf_l", "translation": (0.0, 0.0, -40.0)}}


def tf(loc=(0.0, 0.0, 0.0), rot=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0)):
    return (tuple(loc), tuple(rot), tuple(scale))


def codes(src):
    try:
        dal.check_source(src, "t.dal")
    except dal.DalError as e:
        return [d.code for d in e.diagnostics if d.severity == "error"]
    return []


def binding(item_text, ty="vec3"):
    return "logic T;\ninput X: " + ty + " <- host." + item_text + ";\nupdate(dt) { }"


class MeshReadTests(unittest.TestCase):
    def host(self):
        h = MockHost()
        h.skeleton = {k: dict(v) for k, v in SKELETON.items()}
        h.bones = {"pelvis": {"component": tf((1.0, 2.0, 3.0)), "world": tf((11.0, 12.0, 13.0))}, "ik_foot_l": {"world": tf((100.0, 0.0, 0.0))}}
        return h

    def test_checker(self):
        self.assertEqual(codes(SRC), [])
        self.assertEqual(codes(binding('ref_path_child_translation("a")')), ["DAL3021"])
        self.assertEqual(codes(binding('bone_world_transform("a", "b")', "transform")), ["DAL3021"])
        self.assertEqual(codes(binding('bone_world_transform("a")', "vec3")), ["DAL4020"])                  # a bone gives a transform
        self.assertEqual(codes(binding("bone_world_transform", "transform")), ["DAL3021"])
        self.assertEqual(codes(binding('ref_path_child_translation("a b", "c")')), [])                    # bone names may have single spaces ("VB foot_l")
        self.assertEqual(codes(binding('ref_path_child_translation("a  b", "c")')), ["DAL3022"])          # ... but no doubled, leading or trailing ones
        self.assertEqual(codes(binding("root_motion_mode", "MovementMode")), ["DAL4020"])
        self.assertEqual(codes(binding("root_motion_mode", "RootMotionMode")), [])

    def test_a_third_name_does_not_parse(self):
        with self.assertRaises(dal.DalError) as cm:
            dal.check_source(binding('ref_path_child_translation("a", "b", "c")'), "t.dal")
        self.assertEqual(cm.exception.first.code, "DAL2001")

    def test_the_second_name_is_kept_in_the_binding(self):
        m = dal.check_source(SRC, "t.dal").module
        axis = m.find_input("Axis").host
        self.assertEqual((axis.item, axis.arg, axis.arg2), ("ref_path_child_translation", "calf_l", "thigh_l"))
        self.assertIn("host.ref_path_child_translation('calf_l', 'thigh_l')", generate_python(m))

    def test_values(self):
        m = dal.check_source(SRC, "t.dal").module
        for obj in (load_class(m)(self.host()), Interpreter(m, self.host())):
            h = obj.host
            h.mesh_world_transform = rt.Transform((0.0, 0.0, 10.0))
            h.root_motion_mode = rt.RootMotionMode.IgnoreRootMotion
            h.ragdoll_supported = True
            obj.update(0.1)
            snap = obj.snapshot()
            self.assertEqual(snap["Axis"], (0.0, 0.0, -42.0))                  # calf_l is the child of thigh_l on the way up from calf_l
            self.assertEqual(snap["Pelvis"][0], (1.0, 2.0, 3.0))
            self.assertEqual(snap["Foot"][0], (100.0, 0.0, 0.0))
            self.assertEqual(snap["Mesh"][0], (0.0, 0.0, 10.0))
            self.assertEqual((snap["M"], snap["R"]), (1, True))
            self.assertEqual(snap["Out"], (101.0, 2.0, -49.0))                  # (100, 0, -42) from the foot + (1, 2, 3) seen from the mesh at z = 10

    def test_the_path_of_the_reference_skeleton(self):
        h = self.host()
        self.assertEqual(h.ref_path_child_translation("foot_l", "thigh_l"), (0.0, 0.0, -42.0))   # foot -> calf -> thigh: the calf is the child of the thigh
        self.assertEqual(h.ref_path_child_translation("foot_l", "pelvis"), (0.0, -8.0, 0.0))
        self.assertEqual(h.ref_path_child_translation("thigh_l", "thigh_l"), (0.0, 0.0, 0.0))     # no path
        self.assertEqual(h.ref_path_child_translation("pelvis", "foot_l"), (0.0, 0.0, 0.0))       # up, not down
        self.assertEqual(h.ref_path_child_translation("nobody", "pelvis"), (0.0, 0.0, 0.0))
        self.assertEqual(h.ref_path_child_translation("foot_l", "nobody"), (0.0, 0.0, 0.0))
        self.assertEqual(HostV1().ref_path_child_translation("a", "b"), rt.VEC3_ZERO)

    def test_neutral_defaults(self):
        h = HostV1()
        self.assertEqual(h.mesh_world_transform, rt.Transform())
        self.assertEqual(h.bone_world_transform("x"), rt.Transform())
        self.assertEqual((h.root_motion_mode, h.ragdoll_supported), (rt.RootMotionMode.RootMotionFromMontagesOnly, False))

    def test_random(self):
        m = dal.check_source(SRC, "t.dal").module
        for seed in (1, 2, 3):
            self.assertEqual(harness.verify(m, 80, seed), [])


@unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
class MeshReadCppTests(unittest.TestCase):
    def test_actor_and_anim_logic_match_python(self):
        for src in (SRC, SRC.replace("logic Bn : actor;", "logic Bn;")):
            m = dal.check_source(src, "t.dal").module
            for seed in (1, 2):
                tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 50, seed), warnings_as_errors=True, default=3e-3)

    def test_a_scripted_skeleton(self):
        m = dal.check_source(SRC, "t.dal").module
        frames = [{"dt": 0.1, "free": {}, "skeleton": SKELETON,
                   "bones": {"pelvis": {"component": tf((1.0, 2.0, 3.0)), "world": tf((11.0, 12.0, 13.0))},
                             "ik_foot_l": {"component": tf(), "world": tf((100.0, 0.0, 0.0))}},
                   "mesh_world_transform": tf((0.0, 0.0, 10.0)), "root_motion_mode": 1, "ragdoll_supported": True}]
        tcc.CppCompileTests.run_case(self, m, frames, warnings_as_errors=True, default=1e-3)

    def test_generated_text(self):
        files = generate_cpp(dal.check_source(SRC, "t.dal").module)
        cpp = files["Bn.cpp"]
        self.assertIn('Pelvis = DalMesh != nullptr ? FTransform3f(DalMesh->GetSocketTransform(FName(TEXT("pelvis")), RTS_Component)) : FTransform3f();', cpp)
        self.assertIn('Axis = DalRefPathChildTranslation(FName(TEXT("calf_l")), FName(TEXT("thigh_l")));', cpp)
        self.assertIn("DalRef.GetRefBonePose()[DalChild].GetTranslation()", cpp)
        self.assertIn("DalAnim->RootMotionMode.GetValue()", cpp)
        self.assertIn("TEnumAsByte<ERootMotionMode::Type> Rm", files["Bn.h"])


if __name__ == "__main__":
    unittest.main()
