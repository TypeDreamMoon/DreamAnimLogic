"""`break` and the bounded `while (cond) bound N { ... }`: syntax, diagnostics, the interpreter / Python agreement, the generated C++."""

import unittest

import _bootstrap  # noqa: F401

import dal
from dal import harness, ir
from dal.cppbackend import generate_cpp

try:
    import test_cpp_compile as tcc
except Exception:                                   # pragma: no cover
    tcc = None


def compile_ok(src, **kw):
    return dal.check_source(src, "t.dal", **kw).module


def errors(src, **kw):
    try:
        dal.check_source(src, "t.dal", **kw)
    except dal.DalError as e:
        return [(d.code, d.pos.line, d.pos.col) for d in e.diagnostics if d.severity == "error"]
    return []


def codes(src):
    return [c for c, _l, _k in errors(src)]


def fr(dt=0.125, **kw):
    kw["dt"] = dt
    return kw


def body(stmts, extra=""):
    return f"logic T : actor;\ninput N: float;\nstate S: float;\nstate K: int;\n{extra}\nupdate(dt) {{\n    {stmts}\n}}\n"


SEARCH = """
logic Search : actor;
input Target: float;
state Rounds: int;
state Result: float;
state Pairs: int;
state Clipped: int;
fn first_over(limit: float) -> int {
    var i = 0;
    var x = 1.0;
    while (x < limit) bound 20 {
        x = x * 2.0;
        i += 1;
    }
    return i;
}
method halve(target: float) -> float {
    var lo = 0.0;
    var hi = 100.0;
    for i in 0..32 {
        let mid = (lo + hi) * 0.5;
        Rounds += 1;
        if (abs(mid - target) < 0.01) { return mid; }
        if (mid < target) { lo = mid; } else { hi = mid; }
    }
    return lo;
}
update(dt) {
    Rounds = 0;
    Result = halve(Target);
    Pairs = 0;
    for a in 0..5 {
        for b in 0..5 {
            if (b > a) { break; }
            Pairs += 1;
        }
    }
    Clipped = 0;
    while (true) bound 7 {
        Clipped += 1;
    }
    var spin = 0;
    while (spin < 100) bound 10 {
        spin += 3;
        if (spin > 20) { break; }
    }
    Clipped = Clipped * 100 + spin + first_over(Target);
}
"""


class SyntaxAndDiagnosticTests(unittest.TestCase):
    def check(self, stmts, *expected, extra=""):
        with self.subTest(stmts=stmts):
            self.assertEqual(codes(body(stmts, extra)), list(expected), stmts)

    def test_the_forms(self):
        self.check("while (S < 10.0) bound 8 { S += 1.0; }")
        self.check("for i in 0..4 { if (i > 2) { break; } S += 1.0; }")
        self.check("while (S < 10.0) bound 2 + 2 { S += 1.0; break; }")                      # a constant expression
        self.check("while (S < 10.0) bound 64 { S += 1.0; }")

    def test_the_bound_is_required_and_small_and_constant(self):
        self.check("while (S < 10.0) { S += 1.0; }", "DAL2001")
        self.check("while (S < 10.0) bound 65 { S += 1.0; }", "DAL5050")
        self.check("while (S < 10.0) bound 0 { S += 1.0; }", "DAL5051")
        self.check("while (S < 10.0) bound 2.5 { S += 1.0; }", "DAL4026")
        self.assertTrue(codes(body("while (S < 10.0) bound K { S += 1.0; }")))                    # a state is not a constant

    def test_the_condition_is_a_bool(self):
        self.check("while (S) bound 4 { S += 1.0; }", "DAL4003")
        self.check("while (K + 1) bound 4 { S += 1.0; }", "DAL4003")

    def test_break_needs_a_loop(self):
        self.check("break;", "DAL5053")
        self.check("if (S > 1.0) { break; }", "DAL5053")
        self.check("for i in 0..2 { if (S > 1.0) { break; } }")
        self.check("while (S < 3.0) bound 3 { if (S > 1.0) { break; } S += 1.0; }")
        self.assertEqual(codes("logic T : actor;\nfn f(x: float) -> float { break; return x; }\nupdate(dt) { }"), ["DAL5053"])

    def test_a_loop_may_not_hide_a_name(self):
        self.check("let a = 1.0; for a in 0..2 { }", "DAL3005")

    def test_the_words_are_keywords_now(self):
        self.assertEqual(codes("logic T;\nstate while: float;\nupdate(dt) { }"), ["DAL2003"])
        self.assertEqual(codes("logic T;\nstate break: float;\nupdate(dt) { }"), ["DAL2003"])
        self.check("var bound = 3.0; while (bound > 0.0) bound 4 { bound -= 1.0; }")             # `bound` stays a name

    def test_loops_in_functions_methods_and_views(self):
        src = ("logic T : actor;\nstate S: float;\nfn f(x: float) -> float { var y = x; while (y > 1.0) bound 8 { y = y * 0.5; } return y; }\n"
               "view v(x: float) -> float { var y = x; for i in 0..4 { if (y < 1.0) { break; } y -= 1.0; } return y; }\n"
               "method m() { while (S < 3.0) bound 3 { S += f(8.0); } }\nupdate(dt) { m(); S += v(S); }")
        self.assertEqual(codes(src), [])

    def test_a_loop_may_issue_commands(self):
        self.check("for i in 0..3 { host.add_movement_input(vec3(1.0, 0.0, 0.0)); }")

    def test_unreachable_code_after_break_is_a_warning(self):
        result = dal.check_source(body("for i in 0..3 { break; S = 1.0; }"), "t.dal")
        self.assertEqual([d.code for d in result.warnings], ["DAL5090"])
        self.assertIn("break", result.warnings[0].message)

    def test_the_ir_dump_shows_the_loops(self):
        text = ir.dump(compile_ok(body("while (S < 3.0) bound 5 { S += 1.0; break; }")))
        self.assertIn("while", text)
        self.assertIn("bound 5", text)
        self.assertIn("break", text)


class SemanticsTests(unittest.TestCase):
    def run_both(self, src, frames, **params):
        module = compile_ok(src)
        py = harness.run_python(module, frames, **params)
        it = harness.run_interpreter(module, frames, **params)
        self.assertEqual(harness.compare(py, it), [])
        return py

    def test_the_search(self):
        out = self.run_both(SEARCH, [fr(free={"Target": t}) for t in (0.0, 37.5, 99.9, 1000.0)])
        self.assertAlmostEqual(out[1]["Result"], 37.5, delta=0.01)
        self.assertEqual(out[1]["Pairs"], 15)                                     # 1 + 2 + 3 + 4 + 5: break leaves the inner loop only
        self.assertEqual(out[1]["Clipped"] // 100, 7)                              # while (true) bound 7 runs exactly 7 times
        self.assertEqual(out[1]["Clipped"] % 100, 21 + 6)                          # spin 3, 6 .. 21 (breaks when above 20), first_over(37.5) = 6
        self.assertEqual(out[3]["Clipped"] % 100, 21 + 10)                         # first_over(1000) = 10

    def test_zero_runs_when_the_condition_is_false(self):
        src = body("while (false) bound 5 { K += 1; }\nwhile (S > 100.0) bound 5 { K += 10; }")
        out = self.run_both(src, [fr(free={"N": 0.0})])
        self.assertEqual(out[0]["K"], 0)

    def test_the_bound_stops_a_loop_that_would_never_end(self):
        src = body("while (true) bound 64 { K += 1; }")
        out = self.run_both(src, [fr()])
        self.assertEqual(out[0]["K"], 64)

    def test_the_condition_is_evaluated_before_every_run(self):
        src = body("var guard = 0;\nwhile (guard < 3) bound 10 { guard += 1; K += guard; }")
        out = self.run_both(src, [fr()])
        self.assertEqual(out[0]["K"], 6)

    def test_break_in_a_loop_in_an_if_in_a_loop(self):
        src = body("for a in 0..4 { for b in 0..4 { if (a == 1) { break; } if (b == 2) { break; } K += 1; } K += 100; }")
        out = self.run_both(src, [fr()])
        self.assertEqual(out[0]["K"], 4 * 100 + 2 + 0 + 2 + 2)                     # a = 0: b 0, 1 (2 runs); a = 1: none; a = 2, 3: 2 runs each

    def test_random_frames_agree(self):
        for seed in (1, 2, 3):
            self.assertEqual(harness.verify(compile_ok(SEARCH), 80, seed), [])


class CppTests(unittest.TestCase):
    def test_the_generated_loops(self):
        cpp = generate_cpp(compile_ok(SEARCH))["Search.cpp"]
        self.assertIn("for (int32 DalLoop1 = 0; DalLoop1 < 20 && (x < limit); ++DalLoop1)", cpp)
        self.assertIn("for (int32 DalLoop2 = 0; DalLoop2 < 7 && true; ++DalLoop2)", cpp)
        self.assertIn("for (int32 DalLoop3 = 0; DalLoop3 < 10 && (spin < 100); ++DalLoop3)", cpp)
        self.assertEqual(cpp.count("break;"), 2)

    def test_programs_without_loops_are_unchanged(self):
        cpp = generate_cpp(compile_ok("logic T : actor;\nstate S: float;\nupdate(dt) { for i in 0..3 { S += 1.0; } }"))["T.cpp"]
        self.assertNotIn("DalLoop", cpp)
        self.assertIn("for (int32 i = 0; i < 3; ++i)", cpp)

    @unittest.skipIf(tcc is None or tcc.COMPILER is None, "no C++ compiler found")
    def test_the_compiled_loops_match_python(self):
        module = compile_ok(SEARCH)
        runner = tcc.CppCompileTests("test_hero_gait_matches_python")
        runner.run_case(module, [fr(free={"Target": t}) for t in (0.0, 37.5, 99.9, 1000.0)], warnings_as_errors=True)
        for seed in (1, 2):
            runner.run_case(module, harness.random_frames(module, 60, seed), warnings_as_errors=True)


if __name__ == "__main__":
    unittest.main()
