"""Interpreter vs generated Python, frame by frame, on random input sequences.

The interpreter walks the IR; the generated Python is source text produced by the backend.  They share
only the value types and the math library (which test_runtime.py checks against UE semantics), so a
disagreement here means one of the two evaluators (or the IR lowering) is wrong.
"""

import math
import os
import random
import unittest

import _bootstrap  # noqa: F401
from _bootstrap import EXAMPLES

import dal
from dal import harness, ir
from builtin_programs import PRELUDE, arg, input_decl, outputs, uses_curve
from dal.builtins import BUILTINS, CONSTRUCTOR_FAMILY, CONSTRUCTORS, CONVERTING
from dal.pybackend import generate_python
from dal.types import BOOL, DOUBLE_TO_SINGLE, FLOAT, INT, QUAT, ROTATOR, TRANSFORM, VEC2, VEC3

FLOAT_POOL = [0.0, -0.0, 1.0, -1.0, 0.5, -0.5, 2.5, 1e-9, -1e-9, 180.0, -180.0, 360.0, 540.0, -540.0, 1e4, 3.14159, 0.25, 100.0]


def rand_float(rng):
    r = rng.random()
    if r < 0.5:
        return rng.choice(FLOAT_POOL)
    if r < 0.8:
        return rng.uniform(-10.0, 10.0)
    return rng.uniform(-1000.0, 1000.0)


def rand_quat(rng):
    r = rng.random()
    if r < 0.1:
        return (0.0, 0.0, 0.0, 1.0)
    if r < 0.15:
        return (0.0, 0.0, 0.0, 0.0)
    v = [rng.gauss(0, 1) for _ in range(4)]
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    q = tuple(c / n for c in v)
    return q if rng.random() < 0.8 else tuple(-c for c in q)


def rand_for(rng, ty):
    ty = DOUBLE_TO_SINGLE.get(ty, ty)                  # the data of a double type is made like the data of its float counterpart
    if ty is FLOAT:
        return rand_float(rng)
    if ty is INT:
        return rng.choice([0, 1, -1, 2, -2, 7, -7, 100, 2**31 - 1, -(2**31)])
    if ty is BOOL:
        return rng.random() < 0.5
    if ty is VEC2:
        return (rand_float(rng), rand_float(rng))
    if ty is VEC3:
        return (rand_float(rng), rand_float(rng), rand_float(rng))
    if ty is ROTATOR:
        return (rand_float(rng), rand_float(rng), rand_float(rng))
    if ty is QUAT:
        return rand_quat(rng)
    if ty is TRANSFORM:
        return ((rand_float(rng), rand_float(rng), rand_float(rng)), rand_quat(rng), (rng.choice([0.5, 1.0, 2.0]),) * 3)
    if getattr(ty, 'is_enum', False):
        return rng.randrange(len(ty.members))
    raise ValueError(ty)


def free_frames(module, count, seed):
    rng = random.Random(seed)
    frames = []
    for _ in range(count):
        free = {i.name: rand_for(rng, i.ty) for i in module.inputs if i.host is None}
        frames.append({"dt": rng.choice([0.0, 0.016, 0.1]), "free": free})
    return frames


def compile_src(src):
    return dal.check_source(src, "gen.dal").module


class ExampleConsistency(unittest.TestCase):
    def test_examples_interpreter_vs_python(self):
        for name in ("als_lean.dal", "hero_gait.dal"):
            module = dal.check_file(os.path.join(EXAMPLES, name)).module
            for seed in (1, 2, 3):
                diffs = harness.verify(module, 400, seed)
                self.assertEqual(diffs, [], f"{name} seed {seed}: {diffs[:3]}")

    def test_param_overrides_are_applied_by_both(self):
        module = dal.check_file(os.path.join(EXAMPLES, "als_lean.dal")).module
        self.assertEqual(harness.verify(module, 300, 5, LeanInterpolationHalfLife=0.0), [])
        self.assertEqual(harness.verify(module, 300, 5, LeanInterpolationHalfLife=1.5), [])

    def test_the_harness_detects_a_difference(self):
        """Mutation check: a wrong generated program must be reported, otherwise the comparison proves nothing."""
        module = dal.check_file(os.path.join(EXAMPLES, "hero_gait.dal")).module
        frames = harness.random_frames(module, 200, 4)
        good = generate_python(module)
        self.assertIn("self.Steps = _rt.i32(self.Steps + 1)", good)
        bad = good.replace("self.Steps = _rt.i32(self.Steps + 1)", "self.Steps = _rt.i32(self.Steps + 2)")
        diffs = harness.compare(harness.run_interpreter(module, frames), harness.run_python(module, frames, source=bad))
        self.assertTrue(diffs)
        self.assertEqual(diffs[0][1], "Steps")

    def test_compare_tolerance(self):
        a = [{"x": 1.0, "v": (1.0, 2.0)}]
        b = [{"x": 1.0 + 1e-9, "v": (1.0, 2.0 + 1e-9)}]
        self.assertTrue(harness.compare(a, b, 0.0))
        self.assertEqual(harness.compare(a, b, 1e-6), [])
        self.assertTrue(harness.compare([{"x": 1.0}], [{"x": 1.1}], 1e-6))
        self.assertEqual(harness.compare([{"x": math.nan}], [{"x": math.nan}], 0.0), [])
        self.assertTrue(harness.compare([{"x": 1.0}], [], 0.0))


class BuiltinConsistency(unittest.TestCase):
    """One generated program per built-in signature, fed edge-case values."""

    def test_every_builtin_signature(self):
        count = 0
        for name, b in sorted(BUILTINS.items()):
            for idx, sig in enumerate(b.sigs):
                params = "".join(input_decl(sig, i, f"P{i}") + "\n" for i in range(len(sig.params)))
                args = ", ".join(arg(sig, i, f"P{i}") for i in range(len(sig.params)))
                outs = outputs(sig.ret)
                states = "".join(f"state R{suffix}: {ty};\n" for suffix, ty, _a in outs)
                body = " ".join(f"R{suffix} = {name}({args}){access};" for suffix, _t, access in outs)
                cfg = PRELUDE if uses_curve(sig) else ""
                params = ("param Cfg: BuiltinCfg;\n" if uses_curve(sig) else "") + params
                src = f"{cfg}logic T;\n{params}{states}update(dt) {{ {body} }}\n"
                module = compile_src(src)
                call = module.update.body[0].value
                if outs[0][2]:
                    call = call.x                                  # a record result: its members are stored
                self.assertIsInstance(call, ir.CallBuiltin, name)
                self.assertEqual((call.name, call.sig_index, call.ty), (name, idx, sig.ret), f"{name} overload {idx}")
                frames = free_frames(module, 120, hash((name, idx)) & 0xFFFF)
                diffs = harness.compare(harness.run_interpreter(module, frames), harness.run_python(module, frames))
                self.assertEqual(diffs, [], f"{name}{tuple(p.name for p in sig.params)}: {diffs[:2]}")
                diffs = harness.compare(harness.run_interpreter(module, frames, float32=True), harness.run_python(module, frames, float32=True))
                self.assertEqual(diffs, [], f"{name}{tuple(p.name for p in sig.params)} in the float32 mode: {diffs[:2]}")
                count += 1
        self.assertGreaterEqual(count, 80)

    def test_every_constructor_signature(self):
        for name, sigs in CONSTRUCTORS.items():
            for idx, sig in enumerate(sigs):
                params = "".join(f"input P{i}: {t.name};\n" for i, t in enumerate(sig.params))
                args = ", ".join(f"P{i}" for i in range(len(sig.params)))
                src = f"logic T;\n{params}state R: {sig.ret.name};\nupdate(dt) {{ R = {name}({args}); }}\n"
                module = compile_src(src)
                make = module.update.body[0].value
                if CONVERTING.get(name, -1) is None or CONVERTING.get(name, -1) == idx:
                    self.assertIsInstance(make, (ir.Convert, ir.ToFloat), name)           # float(x), vec3(vd) ...: a change of precision
                    self.assertIs(make.ty, sig.ret)
                else:
                    self.assertIsInstance(make, ir.Make)
                    self.assertEqual((make.ctor, make.sig_index, make.ty), (CONSTRUCTOR_FAMILY.get(name, name), idx, sig.ret))
                frames = free_frames(module, 40, 9)
                self.assertEqual(harness.compare(harness.run_interpreter(module, frames), harness.run_python(module, frames)), [])
                self.assertEqual(harness.compare(harness.run_interpreter(module, frames, float32=True), harness.run_python(module, frames, float32=True)), [])

    def test_builtin_constants(self):
        names = ["PI", "TWO_PI", "HALF_PI", "LN2", "SMALL_NUMBER", "KINDA_SMALL_NUMBER"]
        src = "logic T;\n" + "".join(f"state S{i}: float;\n" for i in range(len(names))) + "state Z2: vec2;\nstate Z3: vec3;\nstate ZR: rotator;\nstate QI: quat;\n"
        src += "update(dt) {\n" + "".join(f"  S{i} = {n};\n" for i, n in enumerate(names)) + "  Z2 = VEC2_ZERO; Z3 = VEC3_ZERO; ZR = ROTATOR_ZERO; QI = QUAT_IDENTITY;\n}\n"
        module = compile_src(src)
        frames = [{"dt": 0.1, "free": {}}]
        py = harness.run_python(module, frames)[0]
        self.assertEqual(harness.compare([py], harness.run_interpreter(module, frames)), [])
        self.assertAlmostEqual(py["S0"], math.pi)
        self.assertAlmostEqual(py["S3"], math.log(2))
        self.assertEqual(py["S4"], 1e-8)
        self.assertEqual(py["QI"], (0.0, 0.0, 0.0, 1.0))


class LanguageConsistency(unittest.TestCase):
    def verify_src(self, src, frames=150, seed=3):
        module = compile_src(src)
        seq = free_frames(module, frames, seed)
        diffs = harness.compare(harness.run_interpreter(module, seq), harness.run_python(module, seq))
        self.assertEqual(diffs, [], diffs[:3])
        return module

    def test_operators_on_every_type(self):
        self.verify_src("""logic T;
input F: float; input G: float; input I: int; input J: int; input B: bool; input C: bool;
input V: vec3; input W: vec3; input U: vec2; input X: vec2; input Q: quat; input R: quat;
state f1: float; state f2: float; state f3: float; state f4: float; state f5: float;
state i1: int; state i2: int; state i3: int; state i4: int; state i5: int;
state b1: bool; state b2: bool; state b3: bool; state b4: bool; state b5: bool; state b6: bool;
state v1: vec3; state v2: vec3; state v3: vec3; state v4: vec3; state v5: vec3;
state u1: vec2; state u2: vec2; state u3: vec2;
state q1: quat;
update(dt) {
  f1 = F + G; f2 = F - G * 2.0; f3 = F / G; f4 = -F; f5 = F * I + J;
  i1 = I + J; i2 = I - J; i3 = I * J; i4 = I / J; i5 = I % J;
  b1 = F < G; b2 = I >= J; b3 = F == I; b4 = B && !C; b5 = B || C; b6 = (I != J) == B;
  v1 = V + W; v2 = V - W; v3 = V * W; v4 = V * F; v5 = F * V / G;
  u1 = U + X; u2 = U * X * 2; u3 = -U / F;
  q1 = Q * R;
}
""")

    def test_conditionals_and_widening(self):
        self.verify_src("""logic T;
input B: bool; input I: int; input F: float;
state a: float; state b: float; state c: int; state d: float;
update(dt) {
  a = B ? I : F;
  b = B ? 1 : 2.5;
  c = B ? I : 7;
  d = I;
  if (B && F > 0.0) { d += 1; } else if (!B) { d -= 2; } else { d *= 2; }
}
""")

    def test_functions_and_locals(self):
        self.verify_src("""logic T;
input F: float; input V: vec3;
state a: float; state b: vec3; state c: float;
fn twice(x: float) -> float { return x * 2.0; }
fn norm_len(v: vec3) -> float { let n = normalize(v); return length(n); }
fn mix(x: float, v: vec3) -> vec3 {
  var r = v;
  if (x > 0.0) { r = v * twice(x); } else { r = -v; }
  return r;
}
update(dt) {
  a = twice(twice(F)) + norm_len(V);
  b = mix(F, V);
  let t = F > 0.0 ? 1.0 : -1.0;
  var acc = 0.0;
  acc += t; acc *= 3.0;
  c = acc;
}
""")

    def test_state_persists_and_accumulates(self):
        module = self.verify_src("""logic T;
input F: float;
state total: float; state steps: int; state peak: float;
update(dt) {
  total += F * dt; steps += 1;
  peak = max(peak, abs(F));
}
""", frames=60)
        self.assertEqual(len(module.states), 3)

    def test_entries_run_in_the_frame_order_given(self):
        module = compile_src("""logic T;
input F: float;
state trace: float;
update(dt) { trace = trace * 10.0 + 1.0; }
entry A(d) { trace = trace * 10.0 + 2.0 + d; }
entry B(d) { trace = trace * 10.0 + 3.0; }
""")
        frames = [{"dt": 0.5, "free": {"F": 1.0}, "calls": ["B", "A"]}, {"dt": 0.0, "free": {"F": 1.0}, "calls": ["A"]}]
        py = harness.run_python(module, frames)
        it = harness.run_interpreter(module, frames)
        self.assertEqual(harness.compare(py, it), [])
        # frame 0: ((1) * 10 + 3) * 10 + 2.5 = 132.5 ; frame 1: (132.5 * 10 + 1) * 10 + 2.0
        self.assertAlmostEqual(py[0]["trace"], 132.5)
        self.assertAlmostEqual(py[1]["trace"], 13262.0)

    def test_enum_flow(self):
        self.verify_src("""logic T;
enum Phase { Idle, Up, Down }
input F: float;
state p: Phase; state n: int;
fn next(cur: Phase, f: float) -> Phase {
  if (cur == Phase.Idle && f > 0.0) { return Phase.Up; }
  if (cur == Phase.Up && f < 0.0) { return Phase.Down; }
  if (cur == Phase.Down) { return Phase.Idle; }
  return cur;
}
update(dt) { p = next(p, F); if (p == Phase.Up) { n += 1; } }
""", frames=300)

    def test_extern_functions(self):
        module = dal.check_source(
            "logic T;\ninput F: float;\nstate S: float;\nstate V: vec2;\n"
            "extern fn Scale(x: float) -> float;\nextern fn Pair(x: float, y: float) -> vec2;\n"
            "update(dt) { S = Scale(F) + 1; V = Pair(F, 2); }\n",
            "t.dal",
            ("s.py", "def Scale(x):\n    return x * 3\n\ndef Pair(x, y):\n    return (x, y)\n"),
        ).module
        frames = free_frames(module, 60, 8)
        py = harness.run_python(module, frames)
        self.assertEqual(harness.compare(py, harness.run_interpreter(module, frames)), [])
        f0 = frames[0]["free"]["F"]
        self.assertAlmostEqual(py[0]["S"], f0 * 3 + 1)
        self.assertEqual(py[0]["V"], (f0, 2.0))


if __name__ == "__main__":
    unittest.main()
