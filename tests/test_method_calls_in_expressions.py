"""A method (or a peer's method) that returns a value can be used inside an expression: the calls of a statement run first, in the order written,
and the expression reads their results.  Not in the right side of && / ||, in a ?: branch or in the condition of a while."""

import unittest

import _bootstrap  # noqa: F401

import dal
from dal import harness, ir
from dal.cppbackend import generate_cpp

try:
    import test_cpp_compile as tcc
except Exception:                                   # pragma: no cover
    tcc = None

PROGRAM = """
logic Mx : actor;
input N: float;
state S: float;
state Trace: int;
state Out: float;
state Flag: bool;
method next() -> float { S += 1.0; Trace = Trace * 10 + 1; return S; }
method twice(x: float) -> float { S += x; Trace = Trace * 10 + 2; return S * 2.0; }
view peek() -> float { return S; }
update(dt) {
    Trace = 0;
    S = 0.0;
    Out = next() + twice(next()) * peek();
    if (next() > 3.0) { Flag = true; } else if (twice(1.0) > 100.0) { Flag = false; } else { Flag = N > 0.5; }
    S += next();
    var acc = 0.0;
    for i in 0..3 { acc += next() * 2.0; }
    Out += acc;
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


def fr(dt=0.125, **kw):
    kw["dt"] = dt
    return kw


class SemanticsTests(unittest.TestCase):
    def test_the_calls_run_first_in_the_order_written(self):
        module = compile_ok(PROGRAM)
        frames = [fr(free={"N": 1.0}), fr(free={"N": 0.0})]
        py = harness.run_python(module, frames)
        self.assertEqual(harness.compare(py, harness.run_interpreter(module, frames)), [])
        f = py[0]
        # Out = next() + twice(next()) * peek():  next -> S 1 (t1 = 1);  next -> S 2 (t2 = 2);  twice(2) -> S 4 (t3 = 8);  peek() reads S = 4
        #   Out = 1 + 8 * 4 = 33; if (next() > 3.0): S 5;  S += next(): S 6, then S = 6 + 6 = 12;  three times acc += next() * 2: S 13, 14, 15 -> acc = 84
        self.assertEqual((f["Out"], f["S"], f["Flag"], f["Trace"]), (117.0, 15.0, True, 11211111))

    def test_a_small_program_by_hand(self):
        src = """logic T : actor;
state S: float;
state R: float;
state Order: int;
method a() -> float { S += 1.0; Order = Order * 10 + 1; return S; }
method b(x: float) -> float { S += x; Order = Order * 10 + 2; return S; }
view peek() -> float { return S; }
update(dt) { S = 0.0; Order = 0; R = a() + b(a()) * peek(); }
"""
        out = harness.run_python(compile_ok(src), [fr()])[0]
        # a -> S = 1, t1 = 1; a -> S = 2, t2 = 2; b(2) -> S = 4, t3 = 4; peek() = 4:  R = t1 + t3 * 4 = 1 + 16 = 17
        self.assertEqual((out["R"], out["Order"], out["S"]), (17.0, 112, 4.0))

    def test_an_else_if_condition_runs_only_when_it_is_reached(self):
        src = """logic T : actor;
state N: int;
state Hits: int;
method touch() -> bool { Hits += 1; return true; }
update(dt) { N = 0; Hits = 0; if (true) { N = 1; } else if (touch()) { N = 2; } }
"""
        out = harness.run_python(compile_ok(src), [fr()])[0]
        self.assertEqual((out["N"], out["Hits"]), (1, 0))                 # the call of the else-branch's condition did not run

    def test_the_interpreter_and_python_agree_on_random_frames(self):
        for seed in (1, 2, 3):
            self.assertEqual(harness.verify(compile_ok(PROGRAM), 60, seed), [])

    def test_the_ir_has_the_temporaries(self):
        text = ir.dump(compile_ok(PROGRAM))
        self.assertIn("let Dal_t1: float =", text)
        self.assertIn("local:Dal_t1", text)


class DiagnosticTests(unittest.TestCase):
    PRE = "logic T : actor;\nstate S: float;\nmethod inc() -> float { S += 1.0; return S; }\n"

    def check(self, body, *expected):
        with self.subTest(body=body):
            self.assertEqual(codes(self.PRE + "update(dt) { " + body + " }"), list(expected), body)

    def test_where_it_is_allowed(self):
        self.check("S = inc() * 2.0 + inc();")
        self.check("host.set_gravity_scale(inc());")
        self.check("host.add_movement_input(vec3(inc(), 0.0, 0.0));")
        self.check("if (inc() > 1.0 && S > 0.0) { S = 0.0; }")
        self.check("for i in 0..2 { S = max(S, inc()); }")
        self.check("let a = inc(); let b = inc() + a;")

    def test_where_it_is_not(self):
        self.check("if (S > 0.0 && inc() > 1.0) { S = 0.0; }", "DAL4042")
        self.check("if (S > 0.0 || inc() > 1.0) { S = 0.0; }", "DAL4042")
        self.check("S = S > 0.0 ? inc() : 0.0;", "DAL4042")
        self.check("while (inc() < 3.0) bound 4 { S += 1.0; }", "DAL4042")
        self.check("while (S < 3.0) bound 4 { S = S + inc(); }")                   # in the body of a loop it is a statement like any other

    def test_a_method_without_a_result_is_still_a_statement(self):
        self.assertEqual(codes("logic T : actor;\nstate S: float;\nmethod m() { S = 1.0; }\nupdate(dt) { S = m() + 1.0; }"), ["DAL4043"])

    def test_functions_and_views_cannot_call_them(self):
        self.assertEqual(codes(self.PRE + "fn f() -> float { return inc() + 1.0; }\nupdate(dt) { }"), ["DAL5012"])
        self.assertEqual(codes(self.PRE + "view v() -> float { return inc() + 1.0; }\nupdate(dt) { }"), ["DAL5044"])


class CppTests(unittest.TestCase):
    def test_the_generated_temporaries(self):
        cpp = generate_cpp(compile_ok(PROGRAM))["Mx.cpp"]
        self.assertIn("const float Dal_t1 = DalMethod_next();", cpp)
        self.assertIn("Out = Dal_t1 + (Dal_t3 * DalView_peek());", cpp.replace("DalMethod_", "DalMethod_"))

    @unittest.skipIf(tcc is None or tcc.COMPILER is None, "no C++ compiler found")
    def test_compiled(self):
        module = compile_ok(PROGRAM)
        runner = tcc.CppCompileTests("test_hero_gait_matches_python")
        for seed in (1, 2):
            runner.run_case(module, harness.random_frames(module, 40, seed), warnings_as_errors=True)


if __name__ == "__main__":
    unittest.main()
