"""Host reads with a name that has spaces in it: ``host.bone_world_transform("VB foot_l")`` (a virtual bone of the Unreal skeleton), a curve
called ``Layer Head``.  The name is an ``FName`` in Unreal, so bone and curve names (not ``notify`` and ``action`` names, which become
identifiers) may be several words separated by single spaces; the checker, both Python implementations and the C++ backend agree."""

import unittest

import _bootstrap  # noqa: F401

import dal
import test_cpp_compile as tcc
from dal import harness
from dal import runtime as rt
from dal.cppbackend import generate_cpp
from dal.interp import Interpreter
from dal.mockhost import MockHost
from dal.pybackend import generate_python, load_class

SRC = """
logic Vb : actor;
input Left: transform <- host.bone_world_transform("VB foot_l");
input Right: transform <- host.bone_component_transform("VB foot_r");
input Axis: vec3 <- host.ref_path_child_translation("VB foot_l", "thigh l");
input Head: float <- host.anim_curve("Layer Head");
state Out: vec3;
state H: float;
update(dt) {
    Out = Left.location + Right.location + Axis;
    H = Head;
}
"""


def codes(src):
    try:
        dal.check_source(src, "t.dal")
    except dal.DalError as e:
        return [d.code for d in e.diagnostics if d.severity == "error"]
    return []


def binding(item_text, ty="transform"):
    return "logic T;\ninput X: " + ty + " <- host." + item_text + ";\nupdate(dt) { }"


def tf(loc=(0.0, 0.0, 0.0)):
    return (tuple(loc), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))


class NameCheckTests(unittest.TestCase):
    def test_single_spaces_between_words_are_fine_for_bones_and_curves(self):
        self.assertEqual(codes(SRC), [])
        for item in ('bone_world_transform("VB foot_l")', 'bone_component_transform("VB foot_l")', 'bone_world_transform("a b c")'):
            self.assertEqual(codes(binding(item)), [], item)
        self.assertEqual(codes(binding('anim_curve("Layer Head")', "float")), [])
        self.assertEqual(codes(binding('ref_path_child_translation("VB foot_l", "thigh l")', "vec3")), [])

    def test_the_name_still_has_to_be_a_name(self):
        for bad in ('" lead"', '"trail "', '"two  spaces"', '""', '" "', '"1st bone"', '"a-b"', '"a.b"', '"a/b"', '"a\\tb"', '"a:b"'):
            self.assertEqual(codes(binding("bone_world_transform(%s)" % bad)), ["DAL3022"], bad)
        self.assertEqual(codes(binding('bone_world_transform("a b1 2c")')), [])               # a word after the first may start with a digit
        self.assertEqual(codes(binding('ref_path_child_translation("a b", "c  d")', "vec3")), ["DAL3022"])      # each of the two names is checked
        self.assertEqual(codes(binding('ref_path_child_translation("a  b", "c d")', "vec3")), ["DAL3022"])

    def test_a_notify_or_an_action_name_is_an_identifier_part_of_the_generated_code(self):
        self.assertEqual(codes(binding('notify("Foot step")', "bool")), ["DAL3022"])
        self.assertEqual(codes(binding('notify("Footstep")', "bool")), [])
        self.assertEqual(codes(binding('action("Move Forward")', "vec2")), ["DAL3022"])

    def test_the_message_says_what_is_wrong(self):
        with self.assertRaises(dal.DalError) as cm:
            dal.check_source(binding('bone_world_transform("a  b")'), "t.dal")
        self.assertIn("two spaces", cm.exception.first.message)
        with self.assertRaises(dal.DalError) as cm:
            dal.check_source(binding('notify("a b")', "bool"), "t.dal")
        self.assertIn("single identifier", cm.exception.first.message)

    def test_the_item_table_says_which_items_take_free_names(self):
        from dal.hostapi import HOST_ITEMS
        free = sorted(n for n, i in HOST_ITEMS.items() if i.free_name)
        self.assertEqual(free, ["anim_curve", "bone_component_transform", "bone_world_transform", "ref_path_child_translation"])


class NameValueTests(unittest.TestCase):
    def host(self):
        h = MockHost()
        h.bones = {"VB foot_l": {"world": tf((1.0, 2.0, 3.0))}, "VB foot_r": {"component": tf((10.0, 20.0, 30.0))}}
        h.skeleton = {"thigh l": {"parent": None, "translation": (0.0, 0.0, 1.0)},
                      "mid": {"parent": "thigh l", "translation": (100.0, 200.0, 300.0)},
                      "VB foot_l": {"parent": "mid", "translation": (4.0, 5.0, 6.0)}}
        h._curves = {"Layer Head": 0.75}
        return h

    def test_the_name_reaches_the_host_unchanged_in_every_implementation(self):
        m = dal.check_source(SRC, "t.dal").module
        self.assertIn("host.bone_world_transform('VB foot_l')", generate_python(m))
        for obj in (load_class(m)(self.host()), Interpreter(m, self.host())):
            obj.update(0.1)
            snap = obj.snapshot()
            self.assertEqual(snap["Out"], (1.0 + 10.0 + 100.0, 2.0 + 20.0 + 200.0, 3.0 + 30.0 + 300.0))
            self.assertEqual(snap["H"], 0.75)

    def test_random_frames_agree(self):
        m = dal.check_source(SRC, "t.dal").module
        for seed in (1, 2, 3):
            self.assertEqual(harness.verify(m, 60, seed), [])


@unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
class NameCppTests(unittest.TestCase):
    def test_generated_text(self):
        files = generate_cpp(dal.check_source(SRC, "t.dal").module)
        cpp = files["Vb.cpp"]
        self.assertIn('Left = DalMesh != nullptr ? FTransform3f(DalMesh->GetSocketTransform(FName(TEXT("VB foot_l")), RTS_World)) : FTransform3f();', cpp)
        self.assertIn('FName(TEXT("VB foot_r")), RTS_Component', cpp)
        self.assertIn('DalRefPathChildTranslation(FName(TEXT("VB foot_l")), FName(TEXT("thigh l")))', cpp)
        self.assertIn('GetCurveValue(FName(TEXT("Layer Head")))', cpp)

    def test_actor_and_anim_logic_match_python(self):
        for src in (SRC, SRC.replace("logic Vb : actor;", "logic Vb;")):
            m = dal.check_source(src, "t.dal").module
            for seed in (1, 2):
                tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 40, seed), warnings_as_errors=True, default=3e-3)


if __name__ == "__main__":
    unittest.main()
