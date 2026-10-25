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
from dal.types import (BOOL, DOUBLE, DOUBLE_TO_SINGLE, FLOAT, INT, QUAT, QUATD, ROTATOR, ROTATORD, TRANSFORMD, VEC2, VEC2D, VEC3, VEC3D)
from test_als_lean import make_sequence
from v1_sources import ALL_COMMANDS, ANIM_V1, PROBE

COMPILER = cpp_driver.find_compiler()


def f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


def close(a, b, rel):
    if isinstance(a, tuple):
        return len(a) == len(b) and all(close(x, y, rel) for x, y in zip(a, b))
    if a is None or b is None:
        return a is b
    if isinstance(a, (bool, int, str)):
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
            if name.startswith("__"):
                continue
            if not close(a[name], b[name], tolerances.get(name, default)):
                problems.append(f"frame {i} {name}: python={a[name]!r} c++={b[name]!r}")
    return problems


STUB_BLEND_OUT = 0.25            # UAnimMontage::GetDefaultBlendOutTime() of the stub


def py_effects(calls):
    """The Python host's command calls as the stub engine logs them: (name, *numbers and names)."""
    out = []

    def flat(args):
        toks = []
        for a in args:
            if isinstance(a, (tuple, list)):
                toks.extend(flat(a))
            elif isinstance(a, bool):
                toks.append(float(a))
            elif isinstance(a, (int, float)):
                toks.append(float(a))
            elif a is not None:
                toks.append(a)
        return toks

    for name, args in calls:
        if name in ("montage_play", "montage_stop", "montage_set_play_rate", "montage_set_position", "play_slot_animation") and args[0] is None:
            continue                                  # an empty montage / sequence value: the engine has nothing to act on
        if name == "montage_stop_source" and args[0] is None:
            continue                                  # no montage instance raised the notify
        if name == "stop_montages_with_slot":         # the stand-in scene has one active montage per slot: its Stop is the effect
            out.append(("montage_instance_stop", f"Slot_{args[0]}", STUB_BLEND_OUT if args[1] < 0 else float(args[1]), 0.0))
        elif name == "montage_stop_source":
            out.append(("montage_instance_stop", f"Instance_{args[0]}", STUB_BLEND_OUT if args[1] < 0 else float(args[1]), 0.0))
        elif name == "set_movement_mode":             # the sub mode is sent only when the program wrote it; the stand-in always logs it
            out.append((name, float(args[0]), float(args[1]) if len(args) > 1 else 0.0))
        elif name == "montage_play":
            out.append(("montage_play", args[0], float(args[1])))
            if args[2]:
                out.append(("montage_jump_to_section", args[0], args[2]))
        elif name == "montage_stop":
            out.append(("montage_stop", args[0], STUB_BLEND_OUT if args[1] < 0 else float(args[1])))
        elif name == "montage_stop_all":
            out.append(("montage_stop_all", 0.0 if args[0] < 0 else float(args[0])))
        else:
            out.append((name, *flat(args)))
    return out


def close_token(a, b, rel):
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    return close(a, b, rel)


def compare_effects(module, py, cpp, rel):
    """Host effects: per frame for actor logic; over the whole run for an anim instance (it runs commands one update late)."""
    problems = []
    py_frames = [py_effects(f["__cmds__"]) for f in py]
    if module.kind == "actor":
        cpp_frames = [list(f["__cmds__"]) for f in cpp]
    else:
        py_frames = [[e for fr in py_frames for e in fr]]
        cpp_frames = [[e for f in cpp for e in f["__cmds__"]]]
    for i, (a, b) in enumerate(zip(py_frames, cpp_frames)):
        if len(a) != len(b):
            problems.append(f"frame {i}: {len(a)} python effects, {len(b)} c++ effects: {a[:4]} vs {b[:4]}")
            continue
        for x, y in zip(a, b):
            if len(x) != len(y) or x[0] != y[0] or not all(close_token(u, v, rel) for u, v in zip(x[1:], y[1:])):
                problems.append(f"frame {i}: python {x} != c++ {y}")
    return problems, sum(len(f) for f in py_frames)


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
        """Compile + run the C++; compare states and host effects with the Python backend.  Returns the number of effects."""
        frames = round_frames(frames)
        try:
            cpp, output = cpp_driver.build_and_run(module, frames, **kwargs)
        except cpp_driver.CompileFailed as e:
            self.fail(f"C++ failed:\n{str(e)[:4000]}")
        if COMPILER[0] == "msvc":
            self.assertNotIn("warning", output.lower(), output)
        py = harness.run_python(module, frames, **(kwargs.get("params") or {}))
        states = [f for f in cpp if not f.get("__final__")]
        problems = compare(py, states, tolerances or {}, default)
        self.assertEqual(problems[:5], [], f"{len(problems)} mismatches")
        effects, count = compare_effects(module, py, cpp, max(default, 1e-3))
        self.assertEqual(effects[:5], [], f"{len(effects)} effect mismatches")
        return count

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

    def test_property_bag_class_compiles_next_to_the_logic(self):
        module = dal.check_file(os.path.join(EXAMPLES, "hero_gait.dal")).module
        frames = harness.random_frames(module, 30, 7)
        self.run_case(module, frames, options=CppOptions(module_api="DAL_TEST_API", property_bag=True), defines=["DAL_TEST_API="])

    def test_every_builtin_matches_python(self):
        src, count = everything_source()
        module = dal.check_source(src, "everything.dal").module
        self.assertGreaterEqual(count, 80)
        rng = random.Random(21)
        frames = []
        for _ in range(25):
            frames.append({"dt": 0.016, "free": {i.name: rand_input(rng, i.ty) for i in module.inputs}})
        self.run_case(module, frames, default=3e-3)


    # ---- v1 / v2: components, montages, events, commands, traces ---------------------------------------------------
    def test_melee_combo_matches_python(self):
        module = dal.check_file(os.path.join(EXAMPLES, "melee_combo.dal")).module
        effects = 0
        for seed in (1, 2, 3):
            effects += self.run_case(module, harness.random_frames(module, 300, seed), warnings_as_errors=True)
        self.assertGreater(effects, 20)               # the sessions really issued host commands

    def test_melee_combo_through_a_scripted_session(self):
        module = dal.check_file(os.path.join(EXAMPLES, "melee_combo.dal")).module
        press = ("input", "Attack", "started", True)
        frames = []
        for i in range(120):
            f = {"dt": 1 / 60, "is_grounded": True, "events": []}
            if i in (2, 32, 62):
                f["events"].append(press)
            if i in (30, 60):
                f["events"].append(("notify_begin", "CancelWindow"))
            if i in (38, 66):
                f["events"].append(("notify_end", "CancelWindow"))
            if i in (45,):
                f["events"].append(("notify", "Hit"))
            if i == 100:
                f["events"].append(("montage_ended", "Light3", False))
            frames.append(f)
        effects = self.run_case(module, frames, warnings_as_errors=True)
        self.assertGreaterEqual(effects, 5)           # three montage_play and the walk speed going down and up

    def test_lockon_camera_matches_python(self):
        module = dal.check_file(os.path.join(EXAMPLES, "lockon_camera.dal")).module
        for seed in (1, 2):
            self.run_case(module, harness.random_frames(module, 200, seed), warnings_as_errors=True)

    def test_lockon_camera_through_a_scripted_session(self):
        module = dal.check_file(os.path.join(EXAMPLES, "lockon_camera.dal")).module
        wall = [((250.0, 10.0, 60.0), 40.0)]
        frames = []
        for i in range(260):
            f = {"dt": 1 / 60, "actor_location": (0.0, 0.0, 0.0), "actor_rotation": (0.0, 0.0, 0.0, 1.0),
                 "control_rotation": (0.0, 0.0, 0.0),
                 "targets": [(1, (500.0, 20.0, 60.0)), (2, (300.0, 250.0, 60.0))],
                 "world": {"ground_z": None, "spheres": wall if 60 <= i < 100 else []}, "events": []}
            if i in (2, 200):
                f["events"].append(("input", "LockOn", "started", True))
            frames.append(f)
        effects = self.run_case(module, frames, default=2e-4, warnings_as_errors=True)
        self.assertGreater(effects, 100)              # control rotation and arm length every locked frame

    def test_every_host_command_matches_python(self):
        module = dal.check_source(ALL_COMMANDS, "all_commands.dal").module
        effects = self.run_case(module, harness.random_frames(module, 40, 4), warnings_as_errors=True)
        self.assertGreaterEqual(effects, 40 * 25)

    def test_traces_and_targets_match_python(self):
        module = dal.check_source(PROBE, "probe.dal").module
        for seed in (1, 2, 3):
            self.run_case(module, harness.random_frames(module, 60, seed), default=2e-3, warnings_as_errors=True)

    def test_anim_instance_with_events_commands_and_montage_reads(self):
        module = dal.check_source(ANIM_V1, "anim_v1.dal").module
        effects = 0
        for seed in (1, 2, 3):
            effects += self.run_case(module, harness.random_frames(module, 200, seed), warnings_as_errors=True)
        self.assertGreater(effects, 20)

    def test_actor_parent_class_option(self):
        module = dal.check_file(os.path.join(EXAMPLES, "melee_combo.dal")).module
        frames = harness.random_frames(module, 10, 5)
        self.run_case(module, frames, options=CppOptions(module_api="DAL_TEST_API"), defines=["DAL_TEST_API="])


from builtin_programs import CURVE_ARGS, PRELUDE, outputs  # noqa: E402
from dal.types import BLEND_OPTION, TRANSFORM  # noqa: E402

POOL = {FLOAT: ("F", 5), INT: ("I", 3), VEC2: ("U", 2), VEC3: ("V", 3), ROTATOR: ("R", 2), QUAT: ("Q", 2), BLEND_OPTION: ("B", 1), TRANSFORM: ("T", 2),
        # the versions for the double types (test_cpp_double.py)
        DOUBLE: ("DF", 5), VEC2D: ("DU", 2), VEC3D: ("DV", 3), ROTATORD: ("DR", 2), QUATD: ("DQ", 2), TRANSFORMD: ("DT", 2)}


def everything_source(derived=False):
    """One program that calls every signature of every built-in; ``derived``: the versions for the double types instead of the ones for the float types."""
    lines = [PRELUDE.rstrip(), "logic Everything;", "param Cfg: BuiltinCfg;"]
    for ty, (prefix, n) in POOL.items():
        if ty.is_double == derived or ty in (INT, BLEND_OPTION):          # (the versions for the double types read double inputs)
            lines += [f"input {prefix}{k}: {ty.name};" for k in range(n)]
    states, body, count = [], [], 0
    for name, b in sorted(BUILTINS.items()):
        for idx, sig in enumerate(b.sigs):
            if (idx >= b.n_single) != derived:
                continue
            used = {}
            args = []
            for p in sig.params:
                if p in CURVE_ARGS:
                    args.append(CURVE_ARGS[p])
                    continue
                prefix, _ = POOL[p]
                k = used.get(p, 0)
                used[p] = k + 1
                args.append(f"{prefix}{k}")
            for suffix, ty_name, access in outputs(sig.ret):
                states.append(f"state R_{name}_{idx}{suffix}: {ty_name};")
                body.append(f"  R_{name}_{idx}{suffix} = {name}({', '.join(args)}){access};")
            count += 1
    return "\n".join(lines + states) + "\nupdate(dt) {\n" + "\n".join(body) + "\n}\n", count


def rand_input(rng, ty):
    ty = DOUBLE_TO_SINGLE.get(ty, ty)                       # the data of a double type is made like the data of its float counterpart
    pool = [0.0, 1.0, -1.0, 0.5, -0.5, 2.5, 180.0, -180.0, 360.0, 3.14159, 0.25, 100.0]

    def fl():
        return rng.choice(pool) if rng.random() < 0.4 else rng.uniform(-10.0, 10.0)

    if ty is FLOAT:
        return fl()
    if ty is INT:
        return rng.choice([0, 1, -1, 2, -2, 7, -7, 100, 2**31 - 1, -(2**31)])
    if ty is BOOL:
        return rng.random() < 0.5
    if ty is BLEND_OPTION:
        return rng.randrange(len(BLEND_OPTION.members))
    if ty is TRANSFORM:
        return ((fl(), fl(), fl()), rand_input(rng, QUAT), (rng.choice([0.5, 1.0, 2.0]), rng.choice([0.5, 1.0, 2.0]), rng.choice([0.5, 1.0, 2.0])))
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
