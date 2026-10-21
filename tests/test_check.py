"""Name resolution, typing, constant folding, diagnostics and the shape of the IR."""

import unittest

import _bootstrap  # noqa: F401

import dal
from dal import ir
from dal import runtime as rt
from dal.types import BOOL, FLOAT, INT, MOVEMENT_MODE, QUAT, VEC2, VEC3

HDR = "logic Zz;\n"


def compile_ok(src, standins=None):
    return dal.check_source(src, "t.dal", standins)


def errors(src, standins=None):
    """[(code, line, col)] of the errors (warnings are not included)."""
    try:
        dal.check_source(src, "t.dal", standins)
    except dal.DalError as e:
        return [(d.code, d.pos.line, d.pos.col) for d in e.diagnostics if d.severity == "error"]
    return []


def warnings(src, standins=None):
    return [d.code for d in compile_ok(src, standins).warnings]


class DiagnosticTests(unittest.TestCase):
    def check(self, src, *expected):
        with self.subTest(src=src):
            self.assertEqual(errors(src), list(expected), src)

    def test_logic_declaration(self):
        self.check("state X: float;", ("DAL3010", 1, 1))
        self.check("logic A;\nlogic B;", ("DAL3011", 2, 1))

    def test_duplicates_and_builtin_names(self):
        self.check(HDR + "state X: float;\nstate X: int;", ("DAL3001", 3, 7))
        self.check(HDR + "enum E { A }\nstate E: float;", ("DAL3001", 3, 7))
        self.check(HDR + "state lerp: float;", ("DAL3002", 2, 7))
        self.check(HDR + "state PI: float;", ("DAL3002", 2, 7))
        self.check(HDR + "state vec3: float;", ("DAL3002", 2, 7))
        self.check(HDR + "enum E { A, A }", ("DAL3001", 2, 13))

    def test_reserved_for_backends(self):
        for name in ("lambda", "None", "delete", "class", "TRUE", "reset", "Dal_x", "AnimNotify_Foo", "bUseMultiThreadedAnimationUpdate"):
            self.check(HDR + f"state {name}: float;", ("DAL3004", 2, 7))

    def test_enum_member_names_may_be_python_keywords(self):
        compile_ok(HDR + "enum E { None, Pass, From }\nstate S: E;")
        self.check(HDR + "enum E { delete }", ("DAL3004", 2, 10))

    def test_undefined_names_suggest_a_fix(self):
        try:
            compile_ok(HDR + "state Speed: float;\nupdate(dt) { Speed = Spede; }")
        except dal.DalError as e:
            self.assertEqual(e.first.code, "DAL3040")
            self.assertIn("did you mean 'Speed'", e.first.message)
        else:
            self.fail()
        self.check(HDR + "update(dt) { let x = nosuch(1.0); }", ("DAL3040", 2, 22))
        self.check(HDR + "update(dt) { let x = Nope.Thing; }", ("DAL3040", 2, 22))

    def test_unknown_type(self):
        self.check(HDR + "state X: flaot;", ("DAL3041", 2, 10))

    def test_type_errors(self):
        self.check(HDR + "state S: float;\nupdate(dt) { S = true; }", ("DAL4002", 3, 18))
        self.check(HDR + "state N: int;\nupdate(dt) { N = 1.5; }", ("DAL4002", 3, 18))
        self.check(HDR + "update(dt) { if (1.0) { } }", ("DAL4003", 2, 18))
        self.check(HDR + "update(dt) { let x = 1 + true; }", ("DAL4001", 2, 24))
        self.check(HDR + "update(dt) { let x = !1; }", ("DAL4001", 2, 22))
        self.check(HDR + "update(dt) { let x = 1.5 % 2; }", ("DAL4001", 2, 26))
        self.check(HDR + "update(dt) { let x = true ? 1.0 : vec2(1.0, 2.0); }", ("DAL4004", 2, 22))
        self.check(HDR + 'input V: vec2 <- host.action("m");\nstate B: bool;\nupdate(dt) { B = V == V; }', ("DAL4001", 4, 20))

    def test_assignment_rules(self):
        self.check(HDR + "input I: float;\nupdate(dt) { I = 1.0; }", ("DAL5001", 3, 14))
        self.check(HDR + "param P: float = 1.0;\nupdate(dt) { P = 2.0; }", ("DAL5001", 3, 14))
        self.check(HDR + "const C: float = 1.0;\nupdate(dt) { C = 2.0; }", ("DAL5001", 3, 14))
        self.check(HDR + "update(dt) { let x = 1; x = 2; }", ("DAL5001", 2, 25))
        self.check(HDR + "update(dt) { dt = 2.0; }", ("DAL5001", 2, 14))
        self.check(HDR + "state V: vec2;\nupdate(dt) { V.x = 1.0; }")                         # a component is assignable (the vector is rebuilt)
        self.check(HDR + "state V: vec2;\nupdate(dt) { V.xy = vec2(1.0, 2.0); }", ("DAL2010", 3, 16))
        self.check(HDR + "state V: vec2;\nupdate(dt) { V.z = 1.0; }", ("DAL4030", 3, 16))
        self.check(HDR + "state N: int;\nupdate(dt) { N -= 0.5; }", ("DAL4002", 3, 19))

    def test_scoping(self):
        self.check(HDR + "state X: float;\nupdate(dt) { let X = 1.0; }", ("DAL3005", 3, 18))
        self.check(HDR + "update(dt) { let a = 1; if (true) { let a = 2; } }", ("DAL3005", 2, 41))
        # the same name in sibling blocks is fine
        compile_ok(HDR + "update(dt) { if (true) { let a = 1; } else { let a = 2; } let b = 3; }")
        self.check(HDR + "update(dt) { if (true) { let a = 1; } let c = a; }", ("DAL3040", 2, 47))
        self.check(HDR + "update(dt) { let dt = 1.0; }", ("DAL3005", 2, 18))

    def test_functions_are_pure(self):
        self.check(HDR + "state S: float;\nfn f() -> float { return S; }", ("DAL5011", 3, 26))
        self.check(HDR + "input I: float;\nfn f() -> float { return I; }", ("DAL5011", 3, 26))
        self.check(HDR + "param P: float = 1.0;\nfn f() -> float { return P; }", ("DAL5011", 3, 26))
        self.check(HDR + "state S: float;\nfn f() -> float { S = 1.0; return 0.0; }", ("DAL5010", 3, 19))
        compile_ok(HDR + "const K: float = 2.0;\nfn f(x: float) -> float { return x * K; }")

    def test_return_rules(self):
        self.check(HDR + "fn f(x: float) -> float { if (x > 0.0) { return 1.0; } }", ("DAL5021", 2, 1))
        compile_ok(HDR + "fn f(x: float) -> float { if (x > 0.0) { return 1.0; } else { return 2.0; } }")
        self.check(HDR + "fn f() -> float { return; }", ("DAL5020", 2, 19))
        self.check(HDR + "fn f() -> float { return true; }", ("DAL4002", 2, 26))
        self.check(HDR + "update(dt) { return 1.0; }", ("DAL5020", 2, 21))
        self.assertIn("DAL5090", warnings(HDR + "fn f() -> float { return 1.0; return 2.0; }\nupdate(dt) { }"))

    def test_recursion_is_rejected(self):
        self.check(HDR + "fn f(x: float) -> float { return f(x); }\nupdate(dt) { }", ("DAL5030", 2, 1))
        self.check(HDR + "fn f(x: float) -> float { return g(x); }\nfn g(x: float) -> float { return f(x); }\nupdate(dt) { }", ("DAL5030", 2, 1))

    def test_call_errors(self):
        self.check(HDR + "fn f(x: float) -> float { return x; }\nupdate(dt) { let a = f(1.0, 2.0); }", ("DAL4041", 3, 22))
        self.check(HDR + "fn f(x: float) -> float { return x; }\nupdate(dt) { let a = f(true); }", ("DAL4002", 3, 24))
        self.check(HDR + "update(dt) { let a = lerp(1.0, vec2(1.0, 2.0), 0.5); }", ("DAL4040", 2, 22))
        self.check(HDR + "update(dt) { let a = length(1.0); }", ("DAL4040", 2, 22))
        self.check(HDR + "state S: float;\nupdate(dt) { let a = S(1.0); }", ("DAL3031", 3, 22))
        self.check(HDR + "update(dt) { let a = lerp; }", ("DAL3031", 2, 22))

    def test_members(self):
        self.check(HDR + 'input V: vec2 <- host.action("m");\nstate S: vec2;\nupdate(dt) { S = V.xz; }', ("DAL4030", 4, 20))
        self.check(HDR + 'input V: vec3 <- host.velocity;\nstate S: float;\nupdate(dt) { S = V.w; }', ("DAL4030", 4, 20))
        self.check(HDR + "input R: rotator <- host.control_rotation;\nstate S: float;\nupdate(dt) { S = R.x; }", ("DAL4030", 4, 20))
        self.check(HDR + "enum G { X1 }\nstate S: G;\nupdate(dt) { S = G.Y1; }", ("DAL3032", 4, 20))
        self.check(HDR + "enum G { X1 }\nstate S: G;\nupdate(dt) { S = G; }", ("DAL3030", 4, 18))

    def test_constants(self):
        self.check(HDR + "state S: float;\nconst C: float = S;", ("DAL5002", 3, 18))
        self.check(HDR + "fn f() -> float { return 1.0; }\nconst C: float = f();", ("DAL5002", 3, 18))
        self.check(HDR + "const A: float = B;\nconst B: float = 1.0;", ("DAL3042", 2, 18))
        self.check(HDR + "const C: float = sqrt(-1.0);", ("DAL5003", 2, 18))
        self.check(HDR + "const C: int = 1.5;", ("DAL4002", 2, 16))

    def test_host_bindings(self):
        self.check(HDR + "input V: vec3 <- host.velocty;", ("DAL3020", 2, 18))
        self.check(HDR + "input V: vec2 <- host.velocity;", ("DAL4020", 2, 10))
        self.check(HDR + "input V: vec2 <- host.action;", ("DAL3021", 2, 18))
        self.check(HDR + 'input V: float <- host.time("x");', ("DAL3021", 2, 29))
        self.check(HDR + 'input V: int <- host.action("x");', ("DAL4020", 2, 10))
        self.check(HDR + 'input V: bool <- host.notify("not valid");', ("DAL3022", 2, 30))
        self.check(HDR + "input V: float <- host.movement_mode;", ("DAL4020", 2, 10))

    def test_entries(self):
        self.check(HDR + "state X: float;\nentry X(dt) { }", ("DAL3001", 3, 7))
        self.check(HDR + "update(dt) { }\nupdate(dt) { }", ("DAL3012", 3, 1))


class StandinTests(unittest.TestCase):
    SRC = HDR + "extern fn f(x: float) -> float;\nstate S: float;\nupdate(dt) { S = f(1.0); }"

    def test_missing_standins(self):
        self.assertEqual(errors(self.SRC), [("DAL6001", 2, 1)])

    def test_missing_function(self):
        self.assertEqual(errors(self.SRC, ("s.py", "def other(x): return x\n")), [("DAL6002", 2, 1)])

    def test_arity_mismatch(self):
        self.assertEqual(errors(self.SRC, ("s.py", "def f(a, b): return a\n")), [("DAL6003", 2, 1)])
        self.assertEqual(errors(self.SRC, ("s.py", "def f(): return 1.0\n")), [("DAL6003", 2, 1)])

    def test_optional_parameters_and_varargs_are_accepted(self):
        self.assertEqual(errors(self.SRC, ("s.py", "def f(a, b=1): return a\n")), [])
        self.assertEqual(errors(self.SRC, ("s.py", "def f(*a): return 1.0\n")), [])

    def test_parameter_name_mismatch_is_a_warning(self):
        r = compile_ok(self.SRC, ("s.py", "def f(y): return y\n"))
        self.assertEqual([w.code for w in r.warnings], ["DAL6004"])

    def test_syntax_error_in_standins(self):
        self.assertEqual(errors(self.SRC, ("s.py", "def f(x:\n")), [("DAL6005", 2, 1)])

    def test_standin_must_not_break_the_generated_module(self):
        future = "from __future__ import annotations\ndef f(x): return x\n"
        self.assertEqual(errors(self.SRC, ("s.py", future)), [("DAL6006", 2, 1)])
        clash = "def f(x): return x\ndef Zz(): pass\n"                    # Zz is the logic's class name
        self.assertEqual(errors(self.SRC, ("s.py", clash)), [("DAL6007", 2, 1)])
        fine = "import math\n_TABLE = (1.0, 2.0)\ndef f(x): return math.floor(x) + _TABLE[0]\n"
        self.assertEqual(errors(self.SRC, ("s.py", fine)), [])

    def test_python_name_override(self):
        src = HDR + 'extern fn f(x: float) -> float py "f_py";\nstate S: float;\nupdate(dt) { S = f(1.0); }'
        self.assertEqual(errors(src, ("s.py", "def f_py(x): return x\n")), [])
        self.assertEqual(errors(src, ("s.py", "def f(x): return x\n")), [("DAL6002", 2, 1)])

    def test_standins_are_bundled_into_the_module(self):
        r = compile_ok(self.SRC, ("s.py", "def f(x):\n    return x * 2\n"))
        self.assertIn("def f(x)", r.module.standin_source)
        self.assertEqual(r.module.standin_filename, "s.py")


class IrShapeTests(unittest.TestCase):
    def test_int_literal_widens_to_float_in_place(self):
        m = compile_ok(HDR + "state S: float;\nupdate(dt) { S = 2; S = S * 3; }").module
        first, second = m.update.body
        self.assertIsInstance(first.value, ir.Lit)
        self.assertEqual((first.value.ty, first.value.value), (FLOAT, 2.0))
        self.assertIsInstance(second.value.b, ir.Lit)
        self.assertIs(second.value.ty, FLOAT)

    def test_int_variable_widens_with_tofloat(self):
        m = compile_ok(HDR + "state S: float;\nupdate(dt) { let n = 3; S = n; }").module
        self.assertIsInstance(m.update.body[1].value, ir.ToFloat)

    def test_compound_assignment_is_expanded(self):
        m = compile_ok(HDR + "state N: int;\nupdate(dt) { N += 2; N *= 3; }").module
        a, b = m.update.body
        self.assertIsInstance(a.value, ir.Binary)
        self.assertEqual((a.value.op, a.value.a.name), ("+", "N"))
        self.assertEqual(b.value.op, "*")

    def test_constants_are_folded(self):
        m = compile_ok(HDR + "const A: float = 2 * PI;\nconst B: int = 7 / 2;\nconst C: vec2 = vec2(1.0, 2.0) * 2.0;\n"
                             "const D: bool = 3 > 2 && true;\nconst E: float = interp_to(0.0, 10.0, 1.0, 0.5);").module
        vals = {c.name: c.value for c in m.consts}
        self.assertAlmostEqual(vals["A"], 6.283185307179586)
        self.assertEqual(vals["B"], 3)
        self.assertEqual(vals["C"], rt.Vec2(2.0, 4.0))
        self.assertIs(vals["D"], True)
        self.assertAlmostEqual(vals["E"], 5.0)

    def test_functions_are_ordered_callees_first(self):
        m = compile_ok(HDR + "fn a(x: float) -> float { return b(x) + 1.0; }\nfn b(x: float) -> float { return c(x); }\n"
                             "fn c(x: float) -> float { return x; }\nupdate(dt) { }").module
        self.assertEqual([f.name for f in m.funcs], ["c", "b", "a"])

    def test_overload_selection(self):
        m = compile_ok(HDR + 'input V: vec3 <- host.velocity;\nstate L: float;\nstate I: int;\n'
                             "update(dt) { L = length(V); I = max(1, 2); L = max(1, 2.5); }").module
        a, b, c = m.update.body
        self.assertIs(a.value.ty, FLOAT)
        self.assertEqual(a.value.sig_index, 1)           # length(vec3)
        self.assertEqual(b.value.sig_index, 0)           # max(int, int)
        self.assertEqual(c.value.sig_index, 1)           # max(float, float) with 1 widened
        self.assertIs(c.value.ty, FLOAT)

    def test_operator_types(self):
        src = (HDR + 'input V: vec3 <- host.velocity;\ninput Q: quat <- host.actor_rotation;\nstate A: vec3;\nstate B: quat;\nstate C: vec2;\n'
               "update(dt) { A = V * 2; A = 2.0 * V; A = V / dt; B = Q * Q; C = V.xy; }")
        m = compile_ok(src).module
        self.assertTrue(all(isinstance(s, ir.Assign) for s in m.update.body))
        self.assertIs(m.update.body[3].value.ty, QUAT)
        self.assertIs(m.update.body[4].value.ty, VEC2)

    def test_host_binding_and_defaults(self):
        m = compile_ok(HDR + 'input V: vec3 <- host.velocity;\ninput M: MovementMode <- host.movement_mode;\n'
                             'input A: vec2 <- host.action("Move");\ninput F: float = 2.5;\ninput N: bool <- host.notify("Hit");').module
        by = {i.name: i for i in m.inputs}
        self.assertEqual((by["V"].host.item, by["V"].ty), ("velocity", VEC3))
        self.assertIs(by["M"].ty, MOVEMENT_MODE)
        self.assertEqual(by["A"].host.arg, "Move")
        self.assertIsNone(by["F"].host)
        self.assertEqual(by["F"].default, 2.5)
        self.assertEqual(by["N"].ty, BOOL)

    def test_zero_defaults(self):
        m = compile_ok(HDR + "enum E { P, Q }\nstate A: float;\nstate B: int;\nstate C: bool;\nstate D: vec3;\nstate F: quat;\nstate G: E;\nstate H: E = E.Q;").module
        d = {s.name: s.default for s in m.states}
        self.assertEqual(d["A"], 0.0)
        self.assertEqual(d["B"], 0)
        self.assertIs(d["C"], False)
        self.assertEqual(d["D"], rt.Vec3(0, 0, 0))
        self.assertEqual(d["F"], rt.Quat(0, 0, 0, 1))
        self.assertEqual((d["G"], d["H"]), (0, 1))

    def test_dump_is_stable_text(self):
        m = compile_ok(HDR + "state S: float;\nupdate(dt) { S = S + dt; }").module
        text = ir.dump(m)
        self.assertIn("state:S = (state:S + local:dt)", text)

    def test_constructor_overloads(self):
        m = compile_ok(HDR + "state A: vec3;\ninput V: vec2 <- host.action(\"m\");\nupdate(dt) { A = vec3(V, 1.0); A = vec3(1.0, 2.0, 3); }").module
        self.assertEqual([s.value.sig_index for s in m.update.body], [1, 0])


if __name__ == "__main__":
    unittest.main()
