"""Compile the generated C++ against the stand-in engine headers and compare its results with the Python backend.

Skipped when no C++ compiler is found (MSVC `cl`, or g++/clang++; set DAL_CL to a cl.exe to choose one).

What this proves: the generated .h/.cpp and DalMath.h are well-formed C++20, free of warnings at /W4 on MSVC,
and compute (in float precision) what the Python backend computes. What it cannot prove: that the code builds
inside Unreal itself -- the engine classes here are stand-ins written from the UE 5.8 headers, see
tests/cpp_stub/CoreMinimal.h.
"""

import math
import os
import random
import struct
import unittest

import _bootstrap  # noqa: F401
from _bootstrap import EXAMPLES

import cpp_driver
import dal
from dal import harness
from dal.builtins import BUILTINS
from dal.cppbackend import CppOptions
from dal.types import BOOL, FLOAT, INT, QUAT, ROTATOR, VEC2, VEC3
from test_als_lean import make_sequence

COMPILER = cpp_driver.find_compiler()


def f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


def close(a, b, rel):
    if isinstance(a, tuple):
        return len(a) == len(b) and all(close(x, y, rel) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(a, int):
        return a == b
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    if math.isinf(a) or math.isinf(b):
        # float32 overflows where double does not: C++ inf stands for any |x| beyond the float range
        return a == b or (math.isinf(b) and abs(a) > 3.0e38 and (a > 0) == (b > 0)) or (math.isinf(a) and abs(b) > 3.0e38 and (a > 0) == (b > 0))
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


def compare(py, cpp, tolerances, default):
    problems = []
    if len(py) != len(cpp):
        return [f"frame count {len(py)} != {len(cpp)}"]
    for i, (a, b) in enumerate(zip(py, cpp)):
        for name in a:
            if not close(a[name], b[name], tolerances.get(name, default)):
                problems.append(f"frame {i} {name}: python={a[name]!r} c++={b[name]!r}")
    return problems


def round_frames(frames):
    """Round every float in the frames to float32 so both sides start from identical inputs."""
    def r(v):
        if isinstance(v, float):
            return f32(v)
        if isinstance(v, tuple):
            return tuple(r(c) for c in v)
        if isinstance(v, dict):
            return {k: r(x) for k, x in v.items()}
        if isinstance(v, list):
            return [r(x) for x in v]
        return v
    return [r(f) for f in frames]


@unittest.skipIf(COMPILER is None, "no C++ compiler found")
class CppCompileTests(unittest.TestCase):
    def run_case(self, module, frames, tolerances=None, default=1e-4, **kwargs):
        frames = round_frames(frames)
        try:
            cpp, output = cpp_driver.build_and_run(module, frames, **kwargs)
        except cpp_driver.CompileFailed as e:
            self.fail(f"C++ failed:\n{str(e)[:4000]}")
        if COMPILER[0] == "msvc":
            self.assertNotIn("warning", output.lower(), output)
        py = harness.run_python(module, frames)
        problems = compare(py, cpp, tolerances or {}, default)
        self.assertEqual(problems[:5], [], f"{len(problems)} mismatches")

    def test_als_lean_matches_python(self):
        module = dal.check_file(os.path.join(EXAMPLES, "als_lean.dal")).module
        # derivatives of float32 differences (acceleration, yaw speed) amplify rounding: looser tolerance there
        loose = {"Acceleration": 5e-3, "YawSpeed": 5e-3}
        for seed in (1, 2, 3):
            self.run_case(module, make_sequence(seed, 250), loose, extra_include=EXAMPLES, warnings_as_errors=True)

    def test_hero_gait_matches_python(self):
        module = dal.check_file(os.path.join(EXAMPLES, "hero_gait.dal")).module
        for seed in (1, 2):
            self.run_case(module, harness.random_frames(module, 250, seed), warnings_as_errors=True)

    def test_api_macro_and_parent_class_options_compile(self):
        module = dal.check_file(os.path.join(EXAMPLES, "hero_gait.dal")).module
        # a macro that expands to nothing stands in for MYGAME_API
        frames = harness.random_frames(module, 20, 5)
        self.run_case(module, frames, options=CppOptions(module_api="DAL_TEST_API"), defines=["DAL_TEST_API="])

    def test_every_builtin_matches_python(self):
        src, count = everything_source()
        module = dal.check_source(src, "everything.dal").module
        self.assertGreaterEqual(count, 80)
        rng = random.Random(21)
        frames = []
        for _ in range(25):
            frames.append({"dt": 0.016, "free": {i.name: rand_input(rng, i.ty) for i in module.inputs}})
        self.run_case(module, frames, default=3e-3)


POOL = {FLOAT: ("F", 5), INT: ("I", 3), VEC2: ("U", 2), VEC3: ("V", 2), ROTATOR: ("R", 2), QUAT: ("Q", 2)}


def everything_source():
    lines = ["logic Everything;"]
    for ty, (prefix, n) in POOL.items():
        lines += [f"input {prefix}{k}: {ty.name};" for k in range(n)]
    states, body, count = [], [], 0
    for name, b in sorted(BUILTINS.items()):
        for idx, sig in enumerate(b.sigs):
            used = {}
            args = []
            for p in sig.params:
                prefix, _ = POOL[p]
                k = used.get(p, 0)
                used[p] = k + 1
                args.append(f"{prefix}{k}")
            states.append(f"state R_{name}_{idx}: {sig.ret.name};")
            body.append(f"  R_{name}_{idx} = {name}({', '.join(args)});")
            count += 1
    return "\n".join(lines + states) + "\nupdate(dt) {\n" + "\n".join(body) + "\n}\n", count


def rand_input(rng, ty):
    pool = [0.0, 1.0, -1.0, 0.5, -0.5, 2.5, 180.0, -180.0, 360.0, 3.14159, 0.25, 100.0]

    def fl():
        return rng.choice(pool) if rng.random() < 0.4 else rng.uniform(-10.0, 10.0)

    if ty is FLOAT:
        return fl()
    if ty is INT:
        return rng.choice([0, 1, -1, 2, -2, 7, -7, 100, 2**31 - 1, -(2**31)])
    if ty is BOOL:
        return rng.random() < 0.5
    if ty is VEC2:
        return (fl(), fl())
    if ty is VEC3:
        return (fl(), fl(), fl())
    if ty is ROTATOR:
        return (fl() * 10, fl() * 10, fl() * 10)
    if ty is QUAT:
        if rng.random() < 0.1:
            return (0.0, 0.0, 0.0, 1.0)
        v = [rng.gauss(0, 1) for _ in range(4)]
        n = math.sqrt(sum(c * c for c in v))
        return tuple(c / n for c in v)
    raise ValueError(ty)


if __name__ == "__main__":
    unittest.main()
