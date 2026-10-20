"""examples/als_lean.dal against the independent port of ALS's lean logic (oracles/als_lean_oracle.py)."""

import math
import os
import random
import unittest

import _bootstrap  # noqa: F401
from _bootstrap import EXAMPLES

import dal
from dal import harness, runtime as rt
from dal.interp import Interpreter
from dal.pybackend import generate_python, load_class
from oracles.als_lean_oracle import AlsLeanOracle

# Independent copy of the in-air curve (the oracle's UCurveFloat); a test below checks the
# stand-in file agrees with it.
CURVE_KEYS = ((-600.0, 1.0), (-200.0, 0.6), (0.0, 0.0), (200.0, -0.6), (600.0, -1.0))


def curve(x):
    if x <= CURVE_KEYS[0][0]:
        return CURVE_KEYS[0][1]
    if x >= CURVE_KEYS[-1][0]:
        return CURVE_KEYS[-1][1]
    for i in range(len(CURVE_KEYS) - 1):
        (x0, y0), (x1, y1) = CURVE_KEYS[i], CURVE_KEYS[i + 1]
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0)
            return y0 + (y1 - y0) * t
    raise AssertionError


def axis_angle_quat(axis, deg):
    n = math.sqrt(sum(c * c for c in axis))
    s = math.sin(math.radians(deg) / 2.0) / n
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(math.radians(deg) / 2.0))


def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def make_sequence(seed, count):
    """Frames shaped like a real character: smooth-ish velocity, turning, ground / air changes."""
    rng = random.Random(seed)
    yaw = rng.uniform(-180, 180)
    pitch = 0.0
    vel = [0.0, 0.0, 0.0]
    grounded = True
    max_accel = rng.choice([1000.0, 2048.0, 1500.0])
    max_brake = rng.choice([1500.0, 2048.0, 2500.0])
    frames = []
    for i in range(count):
        dt = rng.choice([1 / 60, 1 / 60, 1 / 120, 1 / 30, rng.uniform(0.002, 0.05)])
        if rng.random() < 0.03:
            dt = 0.0
        if i == 0:
            dt = 1 / 60
        # steer velocity
        for k in range(2):
            vel[k] = max(-700.0, min(700.0, vel[k] + rng.uniform(-1, 1) * 2000.0 * dt * rng.choice([0, 1, 1, 3])))
        if rng.random() < 0.05:
            vel[0], vel[1] = 0.0, 0.0
        if rng.random() < 0.04:
            grounded = not grounded
        vel[2] = 0.0 if grounded else max(-900.0, min(900.0, vel[2] + rng.uniform(-1, 1) * 1500.0 * dt))
        yaw += rng.uniform(-1, 1) * 300.0 * dt
        if rng.random() < 0.03:
            yaw += rng.uniform(-170, 170)  # sudden turn, also crosses +-180
        yaw = ((yaw + 180.0) % 360.0) - 180.0
        pitch = rng.uniform(-20, 20) if rng.random() < 0.1 else pitch
        q = qmul(axis_angle_quat((0, 0, 1), yaw), axis_angle_quat((0, 1, 0), pitch))
        if rng.random() < 0.02:
            max_accel = rng.choice([0.0, 1000.0, 2048.0])
            max_brake = rng.choice([0.0, 1500.0, 2048.0])
        frames.append(
            {
                "dt": dt,
                "velocity": tuple(vel),
                "actor_rotation": q,
                "max_acceleration": max_accel,
                "max_braking_deceleration": max_brake,
                "is_grounded": grounded,
            }
        )
    return frames


def run_oracle(frames, half_life):
    o = AlsLeanOracle(half_life, curve)
    out = []
    for f in frames:
        out.append(o.frame(f["dt"], f["velocity"], f["actor_rotation"], f["max_acceleration"], f["max_braking_deceleration"], f["is_grounded"]))
    return out


class AlsLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = dal.check_file(os.path.join(EXAMPLES, "als_lean.dal"))
        cls.module = cls.result.module
        cls.source = generate_python(cls.module)

    def run_all(self, frames, **params):
        host = harness.ScriptedHost()
        cls = load_class(self.module, self.source)
        gen = cls(host, **params)
        host2 = harness.ScriptedHost()
        it = Interpreter(self.module, host2, externs=harness.load_standins(self.module), **params)
        gen_out, it_out = [], []
        for f in frames:
            host.load(f)
            host2.load(f)
            gen.update(f["dt"])
            it.update(f["dt"])
            gen_out.append(gen.snapshot())
            it_out.append(it.snapshot())
        return gen_out, it_out

    def assert_matches_oracle(self, out, oracle_out, label, tol=1e-9):
        worst = 0.0
        for i, (a, b) in enumerate(zip(out, oracle_out)):
            for name in ("LeanForward", "LeanRight", "YawSpeed"):
                err = abs(a[name] - b[name]) / max(1.0, abs(b[name]))
                worst = max(worst, err)
                self.assertLessEqual(err, tol, f"{label}: frame {i} {name}: dal={a[name]!r} oracle={b[name]!r}")
            for k in range(3):
                x, y = a["Acceleration"][k], b["Acceleration"][k]
                err = abs(x - y) / max(1.0, abs(y))
                worst = max(worst, err)
                self.assertLessEqual(err, tol, f"{label}: frame {i} Acceleration[{k}]: dal={x!r} oracle={y!r}")
        return worst

    def test_matches_oracle_random_sequences(self):
        for seed in range(1, 21):
            frames = make_sequence(seed, 400)
            expect = run_oracle(frames, 0.2)
            gen_out, it_out = self.run_all(frames)
            self.assert_matches_oracle(gen_out, expect, f"python seed {seed}")
            self.assert_matches_oracle(it_out, expect, f"interpreter seed {seed}")

    def test_half_life_variants(self):
        for half_life in (0.0, 0.05, 0.5, 2.0):
            frames = make_sequence(100 + int(half_life * 10), 300)
            expect = run_oracle(frames, half_life)
            gen_out, it_out = self.run_all(frames, LeanInterpolationHalfLife=half_life)
            self.assert_matches_oracle(gen_out, expect, f"python half_life {half_life}")
            self.assert_matches_oracle(it_out, expect, f"interpreter half_life {half_life}")

    def test_sequences_are_not_vacuous(self):
        """The random sequences must actually exercise both lean branches and both signs."""
        seen_pos = seen_neg = seen_air = seen_brake = False
        peak = 0.0
        for seed in range(1, 6):
            frames = make_sequence(seed, 400)
            for f, o in zip(frames, run_oracle(frames, 0.2)):
                peak = max(peak, abs(o["LeanForward"]), abs(o["LeanRight"]))
                seen_pos |= o["LeanRight"] > 0.05
                seen_neg |= o["LeanRight"] < -0.05
                seen_air |= (not f["is_grounded"]) and abs(o["LeanForward"]) > 0.05
                seen_brake |= o["LeanForward"] < -0.05 and f["is_grounded"]
        self.assertGreater(peak, 0.2)
        self.assertTrue(seen_pos and seen_neg and seen_air and seen_brake)

    def test_first_frame_is_not_smoothed(self):
        """bPendingUpdate: the first frame assigns the target directly and reports no acceleration."""
        frames = make_sequence(3, 5)
        frames[0]["is_grounded"] = False
        frames[0]["velocity"] = (300.0, 100.0, -400.0)
        frames[0]["dt"] = 1 / 60
        gen_out, _ = self.run_all(frames)
        self.assertEqual(gen_out[0]["Acceleration"], (0.0, 0.0, 0.0))
        self.assertEqual(gen_out[0]["YawSpeed"], 0.0)
        expect = run_oracle(frames, 0.2)
        self.assertAlmostEqual(gen_out[0]["LeanForward"], expect[0]["LeanForward"], places=9)
        self.assertNotEqual(gen_out[0]["LeanForward"], 0.0)

    def test_standin_curve_matches_oracle_curve(self):
        ns = {}
        with open(os.path.join(EXAMPLES, "als_lean_standins.py"), encoding="utf-8") as f:
            exec(compile(f.read(), "als_lean_standins.py", "exec"), ns)
        for i in range(-900, 901, 7):
            self.assertAlmostEqual(ns["InAirLeanCurve"](float(i)), curve(float(i)), places=12)

    def test_python_and_interpreter_agree_exactly(self):
        for seed in (1, 2, 3):
            gen_out, it_out = self.run_all(make_sequence(seed, 300))
            self.assertEqual(harness.compare(gen_out, it_out, 0.0), [])


if __name__ == "__main__":
    unittest.main()
