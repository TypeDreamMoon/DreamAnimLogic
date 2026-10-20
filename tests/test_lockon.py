"""examples/lockon_camera.dal (host queries, loops, camera commands) against a hand-written oracle."""

import math
import os
import random
import unittest

import _bootstrap  # noqa: F401
from _bootstrap import EXAMPLES

import dal
from dal.interp import Interpreter
from dal.mockhost import MockHost
from dal.pybackend import load_class
from oracles.lockon_oracle import LockOnOracle

DT = 1.0 / 60.0
PRESS = ("input", "LockOn", "started", True)
KEYS = ("Locked", "TargetId", "TargetLocation", "Visible", "Aim", "ArmLength", "Distance", "Candidates", "Counting", "LostSight")
MODULE = dal.check_file(os.path.join(EXAMPLES, "lockon_camera.dal")).module


def make_dal(host):
    return load_class(MODULE)(host)


def make_interp(host):
    return Interpreter(MODULE, host)


def make_oracle(host):
    return LockOnOracle(host)


def close(a, b, tol=1e-9):
    if isinstance(a, (tuple, list)):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool) or isinstance(a, int) and isinstance(b, int):
        return a == b
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


class Session:
    """Drive a logic with per-frame scene scripts; keep per-frame states and commands."""

    def __init__(self, make, scenes, presses=()):
        self.host = MockHost()
        self.logic = make(self.host)
        self.states, self.commands = [], []
        for i, scene in enumerate(scenes):
            scene(self.host)
            if i in presses:
                self.host.events.append(PRESS)
            self.logic.update(DT)
            snap = self.logic.snapshot()
            self.states.append({k: snap[k] for k in KEYS})
            self.commands.append(tuple(self.host.calls))
            self.host.calls.clear()


def scene(targets=(), spheres=(), ground=None, location=(0.0, 0.0, 0.0), yaw=0.0, control=(0.0, 0.0, 0.0)):
    def apply(host):
        host.targets = [(tid, tuple(loc)) for tid, loc in targets]
        host.spheres = [(tuple(c), r) for c, r in spheres]
        host.ground_z = ground
        host.actor_location = location
        h = math.radians(yaw) / 2.0
        host.actor_rotation = (0.0, 0.0, math.sin(h), math.cos(h))
        host.control_rotation = control
    return apply


AHEAD = [(1, (500.0, 20.0, 60.0))]


class ScenarioTests(unittest.TestCase):
    def test_pressing_lock_on_locks_the_target_in_front(self):
        s = Session(make_dal, [scene(AHEAD)] * 30, presses=[2])
        self.assertFalse(s.states[1]["Locked"])
        self.assertTrue(s.states[2]["Locked"])
        self.assertEqual(s.states[2]["TargetId"], 1)
        self.assertEqual(s.states[2]["Candidates"], 1)
        names = [c[0] for c in s.commands[2]]
        self.assertEqual(names, ["set_control_rotation", "camera_set_arm_length"])
        # the control rotation turns towards the target: yaw = atan2(20, 500) deg; it arrives in a few frames
        target_yaw = math.degrees(math.atan2(20.0, 500.0))
        yaws = [c[1][0][1] for f in s.commands for c in f if c[0] == "set_control_rotation"]
        self.assertLess(yaws[0], target_yaw)
        self.assertAlmostEqual(yaws[-1], target_yaw, places=1)
        self.assertEqual(yaws, sorted(yaws))

    def test_the_arm_grows_with_the_distance_to_the_target(self):
        near = Session(make_dal, [scene([(1, (200.0, 0.0, 60.0))])] * 240, presses=[1])
        far = Session(make_dal, [scene([(1, (1100.0, 0.0, 60.0))])] * 240, presses=[1])
        self.assertAlmostEqual(near.states[-1]["ArmLength"], 350.0 + math.hypot(200.0, 60.0) * 0.15, places=1)
        self.assertAlmostEqual(far.states[-1]["ArmLength"], 350.0 + math.hypot(1100.0, 60.0) * 0.15, places=1)

    def test_pressing_again_lets_go(self):
        s = Session(make_dal, [scene(AHEAD)] * 20, presses=[2, 10])
        self.assertTrue(s.states[9]["Locked"])
        self.assertFalse(s.states[10]["Locked"])
        self.assertEqual(s.states[10]["TargetId"], -1)
        self.assertEqual(s.commands[10], ())

    def test_the_best_candidate_wins(self):
        # dead ahead but far (angle 0, 1000 cm) beats a near one at 40 degrees; one behind the player and one too far are ignored
        targets = [(1, (1000.0, 0.0, 60.0)), (2, (300.0, 250.0, 60.0)), (3, (-300.0, 0.0, 60.0)), (4, (2000.0, 0.0, 60.0))]
        s = Session(make_dal, [scene(targets)] * 4, presses=[1])
        self.assertEqual(s.states[1]["TargetId"], 1)
        self.assertEqual(s.states[1]["Candidates"], 4)
        # closer targets win among equal angles
        s = Session(make_dal, [scene([(5, (800.0, 0.0, 60.0)), (6, (400.0, 0.0, 60.0))])] * 4, presses=[1])
        self.assertEqual(s.states[1]["TargetId"], 6)

    def test_the_search_follows_the_players_facing(self):
        targets = [(1, (500.0, 0.0, 60.0)), (2, (0.0, 500.0, 60.0))]
        facing_x = Session(make_dal, [scene(targets, yaw=0.0)] * 4, presses=[1])
        facing_y = Session(make_dal, [scene(targets, yaw=90.0)] * 4, presses=[1])
        self.assertEqual(facing_x.states[1]["TargetId"], 1)
        self.assertEqual(facing_y.states[1]["TargetId"], 2)

    def test_nothing_to_lock(self):
        s = Session(make_dal, [scene([(1, (-500.0, 0.0, 60.0)), (2, (3000.0, 0.0, 60.0))])] * 4, presses=[1])
        self.assertFalse(s.states[1]["Locked"])
        self.assertEqual(s.states[1]["Candidates"], 2)
        self.assertEqual(s.commands[1], ())

    def test_the_lock_is_dropped_when_the_target_disappears(self):
        scenes = [scene(AHEAD)] * 10 + [scene([])] * 5
        s = Session(make_dal, scenes, presses=[1])
        self.assertTrue(s.states[9]["Locked"])
        self.assertFalse(s.states[10]["Locked"])

    def test_losing_sight_for_too_long_drops_the_lock(self):
        wall = [((250.0, 10.0, 60.0), 40.0)]            # a sphere between the player's eye and the target
        scenes = [scene(AHEAD)] * 10 + [scene(AHEAD, spheres=wall)] * 120
        s = Session(make_dal, scenes, presses=[1])
        self.assertTrue(s.states[10]["Locked"] and not s.states[10]["Visible"])      # hidden, but still locked
        first_dropped = next(i for i, st in enumerate(s.states) if i > 10 and not st["Locked"])
        self.assertAlmostEqual((first_dropped - 10) * DT, 1.5, delta=0.05)
        # a short interruption does not drop it
        scenes = [scene(AHEAD)] * 10 + [scene(AHEAD, spheres=wall)] * 30 + [scene(AHEAD)] * 60
        s = Session(make_dal, scenes, presses=[1])
        self.assertTrue(s.states[-1]["Locked"] and s.states[-1]["Visible"])

    def test_interpreter_and_python_agree(self):
        scenes = [scene(AHEAD)] * 20 + [scene([])] * 5
        a = Session(make_dal, scenes, presses=[2])
        b = Session(make_interp, scenes, presses=[2])
        self.assertEqual(a.states, b.states)
        self.assertEqual(a.commands, b.commands)


class OracleComparison(unittest.TestCase):
    def random_scenes(self, rng, frames):
        targets = {tid: [rng.uniform(-200, 1400), rng.uniform(-600, 600), rng.uniform(0, 120)] for tid in range(1, rng.randrange(1, 6))}
        scenes = []
        yaw = rng.uniform(-180, 180)
        for i in range(frames):
            if rng.random() < 0.01 and targets:
                targets.pop(rng.choice(list(targets)))
            if rng.random() < 0.01:
                targets[rng.randrange(1, 12)] = [rng.uniform(-200, 1400), rng.uniform(-600, 600), rng.uniform(0, 120)]
            for loc in targets.values():
                loc[0] += rng.uniform(-3, 3)
                loc[1] += rng.uniform(-3, 3)
            yaw += rng.uniform(-2, 2)
            spheres = [((rng.uniform(100, 900), rng.uniform(-300, 300), rng.uniform(0, 120)), rng.uniform(20, 120)) for _ in range(rng.randrange(0, 3))]
            scenes.append(scene([(tid, tuple(loc)) for tid, loc in sorted(targets.items())], spheres, rng.choice([None, -10.0]),
                                (rng.uniform(-50, 50), rng.uniform(-50, 50), 0.0), yaw, (rng.uniform(-20, 20), rng.uniform(-180, 180), 0.0)))
        return scenes

    def test_random_sessions(self):
        locked_frames = dropped = 0
        for seed in range(1, 25):
            rng = random.Random(seed)
            frames = 500
            scenes = self.random_scenes(rng, frames)
            presses = {i for i in range(frames) if rng.random() < 0.02}
            a = Session(make_dal, scenes, presses)
            b = Session(make_oracle, scenes, presses)
            for i, (x, y) in enumerate(zip(a.states, b.states)):
                for k in KEYS:
                    self.assertTrue(close(x[k], y[k]), f"seed {seed} frame {i} {k}: dal={x[k]!r} oracle={y[k]!r}")
            self.assertEqual(len(a.commands), len(b.commands))
            for i, (x, y) in enumerate(zip(a.commands, b.commands)):
                self.assertEqual([c[0] for c in x], [c[0] for c in y], f"seed {seed} frame {i}")
                self.assertTrue(close([c[1] for c in x], [c[1] for c in y]), f"seed {seed} frame {i}: {x} vs {y}")
            locked_frames += sum(1 for st in a.states if st["Locked"])
            dropped += sum(1 for p, n in zip(a.states, a.states[1:]) if p["Locked"] and not n["Locked"])
        self.assertGreater(locked_frames, 1000)       # the sessions really locked on, for a long time
        self.assertGreater(dropped, 20)               # ... and let go again


if __name__ == "__main__":
    unittest.main()
