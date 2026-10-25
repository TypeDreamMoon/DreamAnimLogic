"""Structs: declarations, construction, member assignment, value semantics, diagnostics and the three implementations."""

import unittest

import _bootstrap  # noqa: F401

import dal
import test_cpp_compile as tcc
from dal import harness, ir
from dal.cppbackend import CppOptions, generate_cpp
from dal.interp import Interpreter
from dal.pybackend import generate_python, load_class
from struct_sources import PROGRAM, STRUCTS

HDR = "logic Zz;\n"


def errors(src, files=None, name="t.dal"):
    try:
        dal.check_source(src, name, files=files)
    except dal.DalError as e:
        return [(d.code, d.pos.line, d.pos.col) for d in e.diagnostics if d.severity == "error"]
    return []


def compile_ok(src):
    return dal.check_source(src, "t.dal")


class StructCheckTests(unittest.TestCase):
    def check(self, src, *expected):
        with self.subTest(src=src):
            self.assertEqual(errors(src), list(expected), src)

    def test_types_defaults_and_order(self):
        m = compile_ok(PROGRAM).module
        # dependencies first: Feet needs FootState, PoseState needs both
        names = [s.name for s in m.structs]
        self.assertLess(names.index("FootState"), names.index("Feet"))
        self.assertLess(names.index("Feet"), names.index("PoseState"))
        feet = next(s for s in m.structs if s.name == "Feet")
        right = feet.field("Right")
        self.assertEqual(right.default.Heading, 3)                 # Direction.Right
        self.assertEqual(tuple(right.default.Axis), (0.0, 0.0, 1.0))
        left = feet.field("Left")
        self.assertEqual(tuple(left.default.Axis), (0.0, 0.0, -1.0))    # the declared default of FootState.Axis
        pose = next(s for s in m.structs if s.name == "PoseState")
        self.assertEqual(tuple(pose.field("Turn").default), (0.0, 0.0, 0.0, 1.0))

    def test_flat_snapshot_names_are_dotted_paths(self):
        m = compile_ok(PROGRAM).module
        it = Interpreter(m)
        snap = it.snapshot()
        self.assertIn("Pose.Blend.bInit", snap)
        self.assertIn("Pose.Feet.Right.Heading", snap)
        self.assertEqual(snap["Pose.Feet.Right.Heading"], 3)
        self.assertEqual(snap["Pose.Feet.Right.Axis"], (0.0, 0.0, 1.0))
        self.assertEqual(snap["Picked.Backward"], 9.0)
        self.assertNotIn("Pose", snap)

    def test_struct_names_are_types_not_values(self):
        src = STRUCTS + HDR + "state X: VelocityBlend;\nupdate(dt) { let y = VelocityBlend; }"
        self.assertEqual(errors(src)[0][0], "DAL3030")

    def test_declaration_errors(self):
        self.check("struct A { x: float; x: int; }\n" + HDR, ("DAL3001", 1, 22))
        self.check("struct A { x: hit; }\n" + HDR, ("DAL4050", 1, 15))
        self.check("struct A { x: Nope; }\n" + HDR, ("DAL3041", 1, 15))
        self.check("struct A { copy: float; }\n" + HDR, ("DAL3004", 1, 12))
        self.check("struct A { None: float; }\n" + HDR, ("DAL3004", 1, 12))
        self.check("struct A { Dal_X: float; }\n" + HDR, ("DAL3004", 1, 12))
        self.check("struct float { x: float; }\n" + HDR, ("DAL3002", 1, 8))
        self.check("struct A { x: float; }\nstruct A { y: float; }\n" + HDR, ("DAL3001", 2, 8))
        self.check("struct A { x: float = nope; }\n" + HDR, ("DAL3040", 1, 23))
        self.check("struct A { x: float = 1; y: int = 1.5; }\n" + HDR, ("DAL4002", 1, 35))

    def test_a_struct_cannot_contain_itself(self):
        c = errors("struct A { b: B; }\nstruct B { a: A; }\n" + HDR)
        self.assertEqual([x[0] for x in c], ["DAL4053"])
        self.assertEqual(errors("struct A { a: A; }\n" + HDR)[0][0], "DAL4053")

    def test_construction_errors(self):
        pre = STRUCTS + HDR + "state B: VelocityBlend;\nupdate(dt) { B = "
        n = STRUCTS.count("\n") + 2
        self.assertEqual(errors(pre + "VelocityBlend(1.0); }")[0][0], "DAL4054")
        self.assertEqual(errors(pre + "VelocityBlend(Nope: 1.0); }")[0][0], "DAL4030")
        self.assertEqual(errors(pre + "VelocityBlend(Forward: 1.0, Forward: 2.0); }")[0][0], "DAL4056")
        self.assertEqual(errors(pre + "VelocityBlend(Forward: true); }")[0][0], "DAL4002")
        self.assertEqual(errors(pre + "FootState(); }")[0][0], "DAL4002")
        compile_ok(pre + "VelocityBlend(); }")

    def test_member_access_and_assignment_errors(self):
        pre = STRUCTS + HDR + "input I: VelocityBlend;\nparam P: float;\nconst K: VelocityBlend = VelocityBlend();\nstate S: PoseState;\nupdate(dt) { "
        for body, code in (("let a = S.Nope;", "DAL4030"), ("S.Nope = 1.0;", "DAL4030"), ("S.Blend.Forward.x = 1.0;", "DAL4030"),
                           ("I.Forward = 1.0;", "DAL5001"), ("K.Forward = 1.0;", "DAL5001"), ("P.x = 1.0;", "DAL5001"),
                           ("let l = S.Blend; l.Forward = 1.0;", "DAL5001"), ("S.Blend.Forward = true;", "DAL4002"),
                           ("S.Lean.z = 1.0;", "DAL4030"), ("S.Lean.xy = vec2(1.0, 2.0);", "DAL2010"), ("S.Blend = S.Feet;", "DAL4002"),
                           ("let q = S.Blend == S.Blend;", "DAL4001"), ("S.Feet.Phase = 1.5;", "DAL4002")):
            with self.subTest(body):
                self.assertEqual(errors(pre + body + " }")[0][0], code)

    def test_functions_are_pure_but_may_work_on_struct_values(self):
        compile_ok(STRUCTS + HDR + "fn f(b: VelocityBlend) -> float { var c = b; c.Forward += 1.0; return c.Forward; }\nupdate(dt) {}")
        self.assertEqual(errors(STRUCTS + HDR + "state S: VelocityBlend;\nfn f() -> float { S.Forward = 1.0; return 0.0; }\nupdate(dt) {}")[0][0], "DAL5010")
        self.assertEqual(errors(STRUCTS + HDR + "state S: VelocityBlend;\nfn f() -> float { return S.Forward; }\nupdate(dt) {}")[0][0], "DAL5011")

    def test_shadowing_a_struct_name(self):
        self.assertEqual(errors(STRUCTS + HDR + "update(dt) { let Feet = 1.0; }")[0][0], "DAL3005")


class StructSemanticsTests(unittest.TestCase):
    def run_both(self, src, frames):
        m = compile_ok(src).module
        return m, harness.run_interpreter(m, frames), harness.run_python(m, frames)

    def test_value_semantics_no_aliasing(self):
        src = STRUCTS + HDR + """
state A: VelocityBlend;
state B: VelocityBlend;
state Log: float;
fn bump(v: VelocityBlend) -> VelocityBlend { var w = v; w.Forward += 1.0; return w; }
update(dt) {
    B = A;                      // copy
    A.Forward += 1.0;
    let keep = A;
    A.Forward += 10.0;
    var again = keep;
    again.Forward += 100.0;
    Log = B.Forward * 10000.0 + keep.Forward * 100.0 + again.Forward + bump(A).Forward * 0.001;
    let c = bump(A);
    A.Left = c.Forward;         // A.Forward unchanged by bump
}
"""
        frames = [{"dt": 0.1}] * 3
        m, a, b = self.run_both(src, frames)
        self.assertEqual(harness.compare(a, b), [])
        # frame 0: A.Forward 0 -> B.Forward 0; keep 1; again 101; bump(A=11) = 12
        self.assertAlmostEqual(a[0]["Log"], 0 * 10000 + 1 * 100 + 101 + 12 * 0.001)
        self.assertEqual(a[0]["A.Forward"], 11.0)
        self.assertEqual(a[0]["A.Left"], 12.0)
        self.assertEqual(a[1]["B.Forward"], 11.0)

    def test_defaults_are_fresh_on_reset(self):
        m = compile_ok(PROGRAM).module
        cls = load_class(m)
        obj = cls(None)
        obj.Pose.Feet.Right.Offset = 99.0
        obj.reset()
        self.assertEqual(obj.Pose.Feet.Right.Offset, 2.5)
        other = cls(None)
        self.assertIsNot(obj.Pose, other.Pose)
        self.assertIsNot(obj.Pose.Feet.Left, obj.Pose.Feet.Right)

    def test_free_struct_inputs_accept_dicts_and_other_instances(self):
        m = compile_ok(PROGRAM).module
        cls = load_class(m)
        obj = cls(None)
        obj.set_input("Target", {"Forward": 3.0, "Left": 4.0})
        self.assertEqual((obj.Target.Forward, obj.Target.Left, obj.Target.bInit), (3.0, 4.0, True))
        it = Interpreter(m)
        it.set_input("Target", obj.Target)               # an instance of the generated class goes into the interpreter
        self.assertEqual((it.values["Target"].Forward, it.values["Target"].Left), (3.0, 4.0))
        obj2 = cls(None)
        obj2.set_input("Target", it.values["Target"])    # and back
        self.assertEqual(obj2.Target.Left, 4.0)
        with self.assertRaises(TypeError):
            obj.set_input("Target", 3.0)

    def test_member_defaults_and_nested_defaults_in_generated_python(self):
        m = compile_ok(PROGRAM).module
        ns = load_class(m)
        self.assertEqual(ns.__name__, "Sketch")
        src = generate_python(m)
        self.assertIn("class VelocityBlend(_rt.Struct):", src)
        self.assertIn("_new_Left = staticmethod(lambda:", src)

    def test_compound_assignment_and_conditionals_on_members(self):
        src = STRUCTS + HDR + """
state P: PoseState;
update(dt) {
    P.Blend.Forward += 1.5;
    P.Blend.Forward *= 2.0;
    P.Feet.Phase -= 1;
    P.Lean = P.Lean + vec2(1.0, 2.0);
    P.Feet.Left = P.Blend.Forward > 5.0 ? P.Feet.Right : P.Feet.Left;
}
"""
        m, a, b = self.run_both(src, [{"dt": 0.1}] * 4)
        self.assertEqual(harness.compare(a, b), [])
        self.assertEqual([f["P.Blend.Forward"] for f in a], [3.0, 9.0, 21.0, 45.0])
        self.assertEqual([f["P.Feet.Phase"] for f in a], [-1, -2, -3, -4])
        self.assertEqual(a[0]["P.Feet.Left.Heading"], 2)      # still the default Left
        self.assertEqual(a[1]["P.Feet.Left.Heading"], 3)      # copied from Right once Forward > 5

    def test_ir_dump_lists_the_structs(self):
        text = ir.dump(compile_ok(PROGRAM).module)
        self.assertIn("struct Feet {Left: FootState; Right: FootState; Phase: int}", text)
        self.assertIn("state:Pose.Blend.Forward", text)


class StructConsistencyTests(unittest.TestCase):
    def test_interpreter_and_python_agree_on_random_frames(self):
        m = compile_ok(PROGRAM).module
        for seed in (1, 2, 3, 4):
            self.assertEqual(harness.verify(m, 250, seed), [], seed)

    def test_struct_state_is_flat_in_both_snapshots(self):
        m = compile_ok(PROGRAM).module
        frames = harness.random_frames(m, 5, 9)
        a, b = harness.run_interpreter(m, frames), harness.run_python(m, frames)
        self.assertEqual(sorted(a[0]), sorted(b[0]))


class StructLibraryTests(unittest.TestCase):
    FILES = {"lib.dal": STRUCTS, "a.dal": 'use "lib.dal";\nlogic A;\nstate P: PoseState;\nupdate(dt) { P.Blend.Forward += dt; }',
             "b.dal": 'use "lib.dal";\nlogic B : actor;\nstate P: PoseState;\nupdate(dt) { P.Feet.Phase += 1; }'}

    def test_library_structs_are_shared_types(self):
        a = dal.check_source(self.FILES["a.dal"], "a.dal", files=self.FILES).module
        b = dal.check_source(self.FILES["b.dal"], "b.dal", files=self.FILES).module
        self.assertEqual([s.origin for s in a.structs], ["lib.dal"] * 4)
        files = generate_cpp(a)
        self.assertIn("struct FPoseState", files["Lib.h"])
        self.assertNotIn("struct FPoseState", files["A.h"])
        self.assertEqual(generate_cpp(b)["Lib.h"], files["Lib.h"])

    def test_the_library_alone_defines_its_classes_in_python(self):
        lib = dal.check_source(STRUCTS, "lib.dal").module
        ns = load_class(lib)
        pose = ns["PoseState"]()
        self.assertEqual(pose.Feet.Right.Heading, 3)
        self.assertIs(ns["PoseState"].from_value(pose).Feet is pose.Feet, False)


@unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
class StructCppTests(unittest.TestCase):
    def test_sketch_matches_python(self):
        m = compile_ok(PROGRAM).module
        for seed in (1, 2, 3):
            tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 100, seed), warnings_as_errors=True)

    def test_double_members_match_python(self):
        # the members of structs have the double-precision engine types, the logic still computes in single precision
        m = compile_ok(PROGRAM).module
        opts = CppOptions(double_members=True)
        for seed in (1, 2):
            tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 100, seed), options=opts, warnings_as_errors=True)

    def test_double_members_text(self):
        h = generate_cpp(compile_ok(PROGRAM).module, CppOptions(double_members=True))["Sketch.h"]
        self.assertIn("	FVector Axis = FVector(0.0f, 0.0f, -1.0f);", h)           # a member of a struct
        self.assertIn("	FVector2D Lean = FVector2D(0.0f, 0.0f);", h)
        self.assertIn("	FRotator Look = FRotator(0.0f, 0.0f, 0.0f);", h)
        self.assertIn("	FQuat Turn = FQuat(0.0f, 0.0f, 0.0f, 1.0f);", h)
        self.assertIn("	FVector3f Velocity = FVector3f(0.0f, 0.0f, 0.0f);", h)      # an input of the logic keeps the single-precision type
        plain = generate_cpp(compile_ok(PROGRAM).module)["Sketch.h"]
        self.assertIn("	FVector3f Axis = FVector3f(0.0f, 0.0f, -1.0f);", plain)     # off by default

    def test_actor_logic_with_a_library_struct(self):
        files = StructLibraryTests.FILES
        m = dal.check_source(files["b.dal"], "b.dal", files=files).module
        tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 30, 2), warnings_as_errors=True)

    def test_generated_text(self):
        files = generate_cpp(compile_ok(PROGRAM).module)
        h, cpp = files["Sketch.h"], files["Sketch.cpp"]
        self.assertIn("USTRUCT(BlueprintType)\nstruct FVelocityBlend\n{\n\tGENERATED_BODY()", h)
        # UHT cannot read a lambda as a default member initializer: the member is set in a constructor instead
        self.assertNotIn("FFootState Right = []()", h)
        self.assertIn("\tFFeet()\n\t{\n\t\tRight = []() { FFootState Dal_S; Dal_S.Axis = FVector3f(0.0f, 0.0f, 1.0f);", h)
        self.assertIn("Pose.Feet.Left.Heading = (Velocity.X > 0.0f) ? EDirection::Forward : EDirection::Backward;", cpp)
        self.assertIn("FVelocityBlend Dal_Fn_blend_of", cpp)


if __name__ == "__main__":
    unittest.main()
