"""Tag sets: declaration, values, comparison, valid(), open sets, struct members, library sharing, C++ generation."""

import unittest

import _bootstrap  # noqa: F401

import dal
import test_cpp_compile as tcc
from dal import harness, ir
from dal.cppbackend import generate_cpp
from dal.interp import Interpreter
from dal.pybackend import generate_python, load_class

TAGS = """
tags AlsGait = "Als.Gait" { Walking, Running, Sprinting }
tags AlsStance = "Als.Stance" { Standing, Crouching }
tags AlsOverlay = "Als.OverlayMode" { Default, Rifle, Bow }
"""

PROGRAM = TAGS + """
struct Mode { Gait: AlsGait = AlsGait.Running; Stance: AlsStance; Held: AlsOverlay = AlsOverlay.Default; }
logic Tg;
const Fast: AlsGait = AlsGait.Sprinting;
input Speed: float;
input Want: AlsGait;
input Overlay: AlsOverlay;
state Gait: AlsGait = AlsGait.Walking;
state Stance: AlsStance;
state M: Mode;
state Changed: bool;
state Wanted: bool;
state Custom: bool;

fn pick(speed: float) -> AlsGait {
    if (speed > 400.0) { return Fast; }
    if (speed > 200.0) { return AlsGait.Running; }
    return AlsGait.Walking;
}

update(dt) {
    let prev = Gait;
    Gait = pick(Speed);
    Wanted = valid(Want);
    if (valid(Want)) { Gait = Want; }
    Changed = prev != Gait;
    Stance = Gait == Fast ? AlsStance.Standing : AlsStance.None;
    M.Gait = Gait;
    M.Stance = valid(Stance) ? Stance : AlsStance.Crouching;
    M.Held = Overlay == AlsOverlay.Rifle ? Overlay : M.Held;
    Custom = Overlay != AlsOverlay.Default && Overlay != AlsOverlay.Rifle && Overlay != AlsOverlay.Bow && valid(Overlay);
}
"""

HDR = "logic Zz;\n"


def errors(src, files=None, name="t.dal"):
    try:
        dal.check_source(src, name, files=files)
    except dal.DalError as e:
        return [(d.code, d.pos.line, d.pos.col) for d in e.diagnostics if d.severity == "error"]
    return []


class TagCheckTests(unittest.TestCase):
    def check(self, src, *expected):
        with self.subTest(src=src):
            self.assertEqual(errors(src), list(expected), src)

    def test_the_ir_has_the_tag_sets(self):
        m = dal.check_source(PROGRAM, "t.dal").module
        self.assertEqual([t.name for t in m.tagsets], ["AlsGait", "AlsStance", "AlsOverlay"])
        gait = m.tagsets[0]
        self.assertEqual(gait.full_name("Walking"), "Als.Gait.Walking")
        self.assertEqual(gait.full_name("None"), "")
        self.assertEqual(gait.member_of("Als.Gait.Running"), "Running")
        self.assertEqual(gait.member_of(""), "None")
        self.assertIsNone(gait.member_of("Als.Gait.Crawling"))
        self.assertEqual(m.find_state("Gait").default, "Als.Gait.Walking")
        self.assertEqual(m.find_state("Stance").default, "")
        self.assertIn("tags AlsGait = 'Als.Gait' {Walking, Running, Sprinting}", ir.dump(m))

    def test_declaration_errors(self):
        self.check('tags A = "x.y" { P, Q }\ntags B = "x.y" { R }\n' + HDR, ("DAL4051", 2, 10))
        self.check('tags A = "not a tag" { P }\n' + HDR, ("DAL4057", 1, 10))
        self.check('tags A = "" { P }\n' + HDR, ("DAL4057", 1, 10))
        self.check('tags A = "x.1y" { P }\n' + HDR, ("DAL4057", 1, 10))
        self.check('tags A = "x" { None }\n' + HDR, ("DAL3059", 1, 16))
        self.check('tags A = "x" { P, P }\n' + HDR, ("DAL3001", 1, 19))
        self.check('tags A = "x" { delete }\n' + HDR, ("DAL3004", 1, 16))
        self.check('tags A = "x" { P }\nenum A { Q }\n' + HDR, ("DAL3001", 1, 6))
        self.check('tags A = "x" { P }\nstruct A { v: float; }\n' + HDR, ("DAL3001", 2, 8))
        self.check('tags float = "x" { P }\n' + HDR, ("DAL3002", 1, 6))

    def test_use_errors(self):
        pre = TAGS + HDR + "state G: AlsGait;\nstate S: AlsStance;\nstate F: float;\nupdate(dt) { "
        for body, code in (("G = AlsGait.Crawling;", "DAL3032"), ("G = AlsGait;", "DAL3030"), ("G = AlsStance.Standing;", "DAL4002"),
                           ("let b = G == S;", "DAL4001"), ("let b = G < G;", "DAL4001"), ("G = 1.0;", "DAL4002"),
                           ("let b = valid(F);", "DAL4040"), ("let b = valid();", "DAL4040"), ("let b = valid(G, S);", "DAL4040"),
                           ("G = G ? G : G;", "DAL4003"), ("F = G;", "DAL4002"), ("let x = -G;", "DAL4001")):
            with self.subTest(body):
                self.assertEqual(errors(pre + body + " }")[0][0], code)

    def test_a_program_may_still_define_its_own_valid(self):
        src = HDR + "fn valid(x: float) -> bool { return x > 0.0; }\nstate B: bool;\nupdate(dt) { B = valid(1.0); }"
        dal.check_source(src, "t.dal")

    def test_tag_set_names_are_not_values_but_types(self):
        self.assertEqual(errors(TAGS + HDR + "state G: AlsGait;\nupdate(dt) { let x = AlsGait; }")[0][0], "DAL3030")
        self.assertEqual(errors(TAGS + HDR + "state G: AlsGait;\nupdate(dt) { let AlsGait = 1.0; }")[0][0], "DAL3005")


class TagSemanticsTests(unittest.TestCase):
    def test_tags_are_the_full_names_and_the_open_set_compares_exactly(self):
        m = dal.check_source(PROGRAM, "t.dal").module
        it = Interpreter(m)
        it.set_input("Overlay", "Als.OverlayMode.Katana")             # not declared: the set is open
        it.update(0.016)
        self.assertTrue(it.values["Custom"])
        self.assertEqual(it.snapshot()["Overlay"], "Als.OverlayMode.Katana")
        it.set_input("Overlay", "Als.OverlayMode.Rifle")
        it.update(0.016)
        self.assertFalse(it.values["Custom"])
        self.assertEqual(it.values["M"].Held, "Als.OverlayMode.Rifle")
        it.set_input("Overlay", "")
        it.update(0.016)
        self.assertFalse(it.values["Custom"])                         # an empty tag is not valid

    def test_generated_python_classes_expose_the_tags(self):
        m = dal.check_source(PROGRAM, "t.dal").module
        src = generate_python(m)
        self.assertIn("class AlsGait:", src)
        self.assertIn("Walking = 'Als.Gait.Walking'", src)
        obj = load_class(m)(None)
        self.assertEqual(obj.Gait, "Als.Gait.Walking")
        self.assertEqual(obj.M.Gait, "Als.Gait.Running")
        obj.set_input("Want", "Als.Gait.Sprinting")
        obj.update(0.016)
        self.assertEqual(obj.Gait, "Als.Gait.Sprinting")
        self.assertEqual(obj.Stance, "Als.Stance.Standing")
        self.assertEqual(obj.snapshot()["M.Stance"], "Als.Stance.Standing")

    def test_random_frames_agree_between_interpreter_and_python(self):
        m = dal.check_source(PROGRAM, "t.dal").module
        for seed in (1, 2, 3):
            self.assertEqual(harness.verify(m, 200, seed), [], seed)

    def test_constant_folding_of_tags(self):
        m = dal.check_source(TAGS + HDR + 'const A: AlsGait = AlsGait.Running;\nconst B: bool = valid(A);\nconst C: bool = valid(AlsGait.None);\nconst D: bool = A == AlsGait.Running;\nstate S: bool = B && !C && D;\nupdate(dt) {}', "t.dal").module
        self.assertTrue(m.find_state("S").default)


class TagLibraryTests(unittest.TestCase):
    FILES = {
        "als_tags.dal": TAGS + "struct Mode { Gait: AlsGait = AlsGait.Running; }\n",
        "a.dal": 'use "als_tags.dal";\nlogic A;\ninput Want: AlsGait;\nstate M: Mode;\nupdate(dt) { if (valid(Want)) { M.Gait = Want; } }',
        "b.dal": 'use "als_tags.dal";\nlogic B : actor;\nstate S: AlsStance = AlsStance.Crouching;\nupdate(dt) { S = AlsStance.Standing; }',
    }

    def test_library_tags_live_in_the_library_header_and_source(self):
        m = dal.check_source(self.FILES["a.dal"], "a.dal", files=self.FILES).module
        files = generate_cpp(m)
        self.assertIn("UE_DECLARE_GAMEPLAY_TAG_EXTERN(Walking)", files["AlsTags.h"])
        self.assertIn('UE_DEFINE_GAMEPLAY_TAG(Walking, TEXT("Als.Gait.Walking"))', files["AlsTags.cpp"])
        self.assertNotIn("UE_DEFINE_GAMEPLAY_TAG", files["A.cpp"])
        self.assertNotIn("namespace AlsGaitTags", files["A.h"])
        self.assertIn("GameplayTags", files["A.h"].split("\n")[2])

    def test_api_macro_goes_on_the_tag_declarations(self):
        from dal.cppbackend import CppOptions
        m = dal.check_source(TAGS + HDR + "update(dt) {}", "t.dal").module
        files = generate_cpp(m, CppOptions(module_api="MYGAME_API"))
        self.assertIn("\tMYGAME_API UE_DECLARE_GAMEPLAY_TAG_EXTERN(Walking)", files["Zz.h"])


@unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
class TagCppTests(unittest.TestCase):
    def test_program_matches_python(self):
        m = dal.check_source(PROGRAM, "t.dal").module
        for seed in (1, 2, 3):
            tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 100, seed), warnings_as_errors=True)

    def test_library_tags_link_into_both_logics(self):
        for name in ("a.dal", "b.dal"):
            m = dal.check_source(TagLibraryTests.FILES[name], name, files=TagLibraryTests.FILES).module
            tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 40, 5), warnings_as_errors=True)


if __name__ == "__main__":
    unittest.main()
