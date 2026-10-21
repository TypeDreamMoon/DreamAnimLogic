"""Small additions of language version 4: rotator arithmetic, valid(Peer), and settings members initialised by their declaration."""

import unittest

import _bootstrap  # noqa: F401

import dal
from dal import harness
from dal import runtime as rt
from dal.cppbackend import generate_cpp
from dal.pybackend import generate_python, load_class
from dal.interp import Interpreter
from dal.mockhost import MockHost

try:
    import test_cpp_compile as tcc
except Exception:                                   # pragma: no cover
    tcc = None


def compile_ok(src, **kw):
    return dal.check_source(src, "t.dal", **kw).module


def codes(src, files=None):
    try:
        dal.check_source(src, "t.dal", files=files or {})
    except dal.DalError as e:
        return [d.code for d in e.diagnostics if d.severity == "error"]
    return []


def fr(dt=0.125, **kw):
    kw["dt"] = dt
    return kw


ROT = """
logic R : actor;
input A: rotator;
input B: rotator;
input F: float;
state Sum: rotator;
state Diff: rotator;
state Scaled: rotator;
state Both: rotator;
update(dt) { Sum = A + B; Diff = A - B; Scaled = A * F; Both = F * 2.0 * B + A; }
"""


class RotatorArithmeticTests(unittest.TestCase):
    def check(self, expr, *expected):
        src = f"logic R : actor;\ninput A: rotator;\ninput B: rotator;\ninput F: float;\nstate S: rotator;\nstate X: float;\nupdate(dt) {{ {expr} }}"
        with self.subTest(expr=expr):
            self.assertEqual(codes(src), list(expected), expr)

    def test_the_operators(self):
        self.check("S = A + B;")
        self.check("S = A - B;")
        self.check("S = A * F;")
        self.check("S = F * A;")
        self.check("S = A * 2;")                       # an int widens
        self.check("S = A + F;", "DAL4001")
        self.check("S = A * B;", "DAL4001")
        self.check("S = A / F;", "DAL4001")
        self.check("S = -A;", "DAL4001")
        self.check("X = (A + B).yaw;")

    def test_component_wise_and_not_normalised(self):
        module = compile_ok(ROT)
        frames = [fr(free={"A": (10.0, 170.0, 5.0), "B": (-3.0, 20.0, 1.0), "F": 2.0})]
        py = harness.run_python(module, frames)
        self.assertEqual(harness.compare(py, harness.run_interpreter(module, frames)), [])
        f = py[0]
        self.assertEqual(f["Sum"], (7.0, 190.0, 6.0))                                   # 170 + 20 stays 190: no normalisation (FRotator)
        self.assertEqual(f["Diff"], (13.0, 150.0, 4.0))
        self.assertEqual(f["Scaled"], (20.0, 340.0, 10.0))
        self.assertEqual(f["Both"], (-2.0, 250.0, 9.0))

    def test_the_runtime_class(self):
        self.assertEqual(rt.Rotator(1, 2, 3) + rt.Rotator(1, 1, 1), rt.Rotator(2, 3, 4))
        self.assertEqual(rt.Rotator(1, 2, 3) * 2, rt.Rotator(2, 4, 6))
        self.assertEqual(2 * rt.Rotator(1, 2, 3), rt.Rotator(2, 4, 6))
        self.assertIsInstance(rt.Rotator(1, 2, 3) - rt.Rotator(1, 1, 1), rt.Rotator)

    def test_the_generated_c_plus_plus(self):
        cpp = generate_cpp(compile_ok(ROT))["R.cpp"]
        self.assertIn("Sum = A + B;", cpp)
        self.assertIn("Scaled = A * F;", cpp)
        self.assertIn("Both = (B * (F * 2.0f)) + A;", cpp)

    @unittest.skipIf(tcc is None or tcc.COMPILER is None, "no C++ compiler found")
    def test_compiled(self):
        module = compile_ok(ROT)
        runner = tcc.CppCompileTests("test_hero_gait_matches_python")
        for seed in (1, 2):
            runner.run_case(module, harness.random_frames(module, 50, seed), warnings_as_errors=True)


CHR = "logic Chr : actor;\nstate N: int;\npub method Bump() { N += 1; }\nupdate(dt) { }\n"
INP = """
peer Chr: Chr;
logic Inp : actor;
state Calls: int;
state Present: int;
view there() -> bool { return valid(Chr); }
update(dt) {
    if (valid(Chr)) { Present += 1; Chr.Bump(); }
    if (!valid(Chr) && there() == false) { Calls += 1; }
}
"""


class PeerValidTests(unittest.TestCase):
    FILES = {"Chr.dal": CHR}

    def test_diagnostics(self):
        self.assertEqual(codes(INP, self.FILES), [])
        self.assertEqual(codes("logic T : actor;\nstate X: int;\nupdate(dt) { if (valid(X)) { } }"), ["DAL4040"])               # only tags, assets and peers
        self.assertEqual(codes("logic T : actor;\nupdate(dt) { if (valid(Nope)) { } }"), ["DAL3040"])
        self.assertEqual(codes("peer Chr: Chr;\nlogic Inp : actor;\nfn f() -> bool { return valid(Chr); }\nupdate(dt) { }", self.FILES), ["DAL5042"])

    def test_the_peer_is_there_or_not(self):
        module = compile_ok(INP, files=self.FILES)
        host = MockHost()
        for backend in ("python", "interp"):
            alone = load_class(module, generate_python(module))(host) if backend == "python" else Interpreter(module, host)
            alone.update(0.1)
            values = alone.values if backend == "interp" else vars(alone)
            self.assertEqual((values["Present"], values["Calls"]), (0, 1), backend)
            chr_mod = compile_ok(CHR)
            chr_obj = load_class(chr_mod, generate_python(chr_mod))(MockHost()) if backend == "python" else Interpreter(chr_mod, MockHost())
            alone.connect("Chr", chr_obj)
            alone.update(0.1)
            values = alone.values if backend == "interp" else vars(alone)
            self.assertEqual((values["Present"], values["Calls"]), (1, 1), backend)

    def test_the_generated_c_plus_plus(self):
        cpp = generate_cpp(compile_ok(INP, files=self.FILES))["Inp.cpp"]
        self.assertIn("return Chr != nullptr;", cpp)
        self.assertIn("if (Chr != nullptr)", cpp)

    @unittest.skipIf(tcc is None or tcc.COMPILER is None, "no C++ compiler found")
    def test_compiled_system(self):
        import cpp_system
        from test_cpp_system import compare_system

        files = {"Chr.dal": CHR, "Inp.dal": INP}
        mods = {n[:-4]: dal.check_source(files[n], n, files=files).module for n in ("Chr.dal", "Inp.dal")}
        frames = [{"dt": 0.125} for _ in range(4)]
        cpp, output = cpp_system.build_and_run_system(mods, frames)
        if tcc.COMPILER[0] == "msvc":
            self.assertNotIn("warning", output.lower(), output)
        py = harness.run_system(mods, frames, "python")
        compare_system(self, mods, py, cpp)
        self.assertEqual([f["Inp"]["Present"] for f in py], [1, 2, 3, 4])


SETTINGS = """
settings Trace { Reach: float = 75.0; Angle: float = 35.0; derived Double: float = Reach * 2.0; }
settings Rules {
    Near: Trace = Trace(Reach: 10.0);
    Far: Trace = Trace(Reach: 200.0, Angle: 90.0);
    Plain: Trace;
    Speed: float = 3.0;
}
logic Sr : anim;
param Cfg: Rules;
state Near: float;
state FarAngle: float;
state Plain: float;
state NearDouble: float;
update(dt) { Near = Cfg.Near.Reach; FarAngle = Cfg.Far.Angle; Plain = Cfg.Plain.Reach; NearDouble = Cfg.Near.Double; }
"""


class SettingsInitializerTests(unittest.TestCase):
    def test_diagnostics(self):
        self.assertEqual(codes(SETTINGS), [])
        self.assertEqual(codes(SETTINGS.replace("Trace(Reach: 10.0)", "Trace(Nope: 10.0)")), ["DAL4030"])
        self.assertEqual(codes(SETTINGS.replace("Trace(Reach: 10.0)", "Trace(Reach: true)")), ["DAL4002"])
        self.assertEqual(codes(SETTINGS.replace("update(dt) {", "update(dt) { let t = Trace(Reach: 1.0);")), ["DAL5004"])       # not outside a declaration
        self.assertEqual(codes(SETTINGS.replace("state Near: float;", "state Near: float = Trace(Reach: 1.0).Reach;")), ["DAL5004"])

    def test_the_declared_values_are_the_defaults(self):
        module = compile_ok(SETTINGS)
        for backend in ("python", "interp"):
            out = (harness.run_python if backend == "python" else harness.run_interpreter)(module, [fr()], Cfg={})[0]
            self.assertEqual((out["Near"], out["FarAngle"], out["Plain"], out["NearDouble"]), (10.0, 90.0, 75.0, 20.0), backend)     # the derived one follows

    def test_the_data_wins(self):
        module = compile_ok(SETTINGS)
        data = {"Near": {"Reach": 4.0}, "Plain": {"Reach": 5.0}}
        out = harness.run_python(module, [fr()], Cfg=data)[0]
        self.assertEqual((out["Near"], out["FarAngle"], out["Plain"], out["NearDouble"]), (4.0, 90.0, 5.0, 8.0))            # a member of the data keeps the rest of its type's defaults

    def test_the_generated_c_plus_plus(self):
        files = generate_cpp(compile_ok(SETTINGS))
        self.assertIn("FTrace Near = []() { FTrace Dal_S; Dal_S.Reach = 10.0f; return Dal_S; }();", files["Sr.h"])
        self.assertIn("FTrace Plain = FTrace();", files["Sr.h"])

    @unittest.skipIf(tcc is None or tcc.COMPILER is None, "no C++ compiler found")
    def test_compiled(self):
        module = compile_ok(SETTINGS)
        runner = tcc.CppCompileTests("test_hero_gait_matches_python")
        runner.run_case(module, [fr()], params={"Cfg": {}}, warnings_as_errors=True)
        runner.run_case(module, [fr()], params={"Cfg": {"Near": {"Reach": 4.0}}}, warnings_as_errors=True)


if __name__ == "__main__":
    unittest.main()
