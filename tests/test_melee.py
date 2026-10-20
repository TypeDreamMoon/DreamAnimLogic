"""examples/melee_combo.dal driven by a simulated host (montages, notify windows, events), against the
hand-written oracle in oracles/melee_oracle.py."""

import os
import random
import unittest

import _bootstrap  # noqa: F401
from _bootstrap import EXAMPLES

import dal
from dal.interp import Interpreter
from dal.mockhost import SimHost, SimMontage
from dal.pybackend import load_class
from oracles.melee_oracle import MeleeOracle

DT = 1.0 / 60.0
ATTACK = ("input", "Attack", "started", True)


def attack_montages():
    def light(name, length):
        return SimMontage(name, length, {"Windup": 0.0, "Strike": 0.3, "Recover": 0.6}, notifies=[(0.35, "Hit")],
                          windows=[(0.5, 0.9, "CancelWindow")], blend_out=0.2)
    return [light("Light1", 1.0), light("Light2", 1.0), light("Light3", 1.2)]


def new_host(grounded=True):
    host = SimHost(attack_montages())
    host.is_grounded = grounded
    return host


class Run:
    """One simulated session: per frame the logic's visible state and the commands it issued."""

    def __init__(self, make_logic, presses=(), airborne=(), frames=300, seed=None):
        self.host = new_host()
        self.logic = make_logic(self.host)
        self.states = []
        self.commands = []
        presses, airborne = set(presses), set(airborne)
        for i in range(frames):
            self.host.is_grounded = i not in airborne
            if i in presses:
                self.host.events.append(ATTACK)
            self.logic.update(DT)
            self.host.advance(DT)
            self.states.append(self.snapshot())
            self.commands.append(tuple(self.host.calls))
            self.host.calls.clear()

    def snapshot(self):
        s = self.logic.snapshot()
        keys = ("Melee", "Hits", "AttackBuffer", "Cancel", "WasAttacking")
        return {k: s[k] for k in keys}

    def montage_plays(self):
        return [c[1][0] for frame in self.commands for c in frame if c[0] == "montage_play"]


def dal_logic(host):
    module = dal.check_file(os.path.join(EXAMPLES, "melee_combo.dal")).module
    return load_class(module)(host)


def dal_interpreter(host):
    module = dal.check_file(os.path.join(EXAMPLES, "melee_combo.dal")).module
    return Interpreter(module, host)


def oracle(host):
    return MeleeOracle(host)


class ScenarioTests(unittest.TestCase):
    """Hand-checked scenarios on the DAL program."""

    def test_idle_character_does_nothing(self):
        run = Run(dal_logic, frames=60)
        self.assertEqual(run.montage_plays(), [])
        self.assertTrue(all(s["Melee"] == 0 for s in run.states))

    def test_single_press_plays_one_hit_and_the_chain_ends(self):
        run = Run(dal_logic, presses=[3], frames=240)
        self.assertEqual(run.montage_plays(), ["Light1"])
        self.assertEqual(run.states[2]["Melee"], 0)
        self.assertEqual(run.states[3]["Melee"], 1)                  # the press arrives in frame 3 and is used in the same update
        self.assertEqual(run.states[-1]["Melee"], 0)                 # Light1 finished by itself
        self.assertEqual(run.states[-1]["Hits"], 1)                  # the Hit notify at 0.35 s
        self.assertEqual(run.commands[3][0], ("montage_play", ("Light1", 1.0, None)))
        self.assertEqual(run.commands[3][1], ("set_max_walk_speed", (120.0,)))
        last = [f for f in run.commands if any(c[0] == "set_max_walk_speed" for c in f)][-1]
        self.assertEqual(last, (("set_max_walk_speed", (600.0,)),))

    def test_three_hit_chain_through_the_cancel_windows(self):
        # press 1 starts hit 1 (frame 3); the cancel window of Light1 is 0.5 .. 0.9 s -> frames 33 .. 57
        # press 2 inside it (frame 40), press 3 inside Light2's window (Light2 starts at frame 41)
        run = Run(dal_logic, presses=[3, 40, 41 + 40], frames=400)
        self.assertEqual(run.montage_plays(), ["Light1", "Light2", "Light3"])
        self.assertEqual(max(s["Melee"] for s in run.states), 3)
        self.assertEqual(run.states[-1]["Melee"], 0)
        # one Hit notify per montage that got to 0.35 s, plus the bonus point for the third hit
        self.assertEqual(run.states[-1]["Hits"], 3 + 1)
        plays = [c for f in run.commands for c in f if c[0] == "montage_play"]
        self.assertEqual(plays[2][1], ("Light3", 1.15, None))

    def test_a_press_before_the_window_is_buffered_and_used_when_it_opens(self):
        # hit 1 starts at frame 3; its window opens at 0.5 s (frame 33). A press at frame 25 is 0.13 s early: still buffered.
        run = Run(dal_logic, presses=[3, 25], frames=200)
        self.assertEqual(run.montage_plays()[:2], ["Light1", "Light2"])

    def test_a_press_that_is_too_early_is_forgotten(self):
        # a press at frame 10 is 0.38 s before the window opens (0.3 s buffer): gone
        run = Run(dal_logic, presses=[3, 10], frames=200)
        self.assertEqual(run.montage_plays(), ["Light1"])

    def test_leaving_the_ground_drops_the_chain(self):
        run = Run(dal_logic, presses=[3], airborne=range(20, 400), frames=120)
        self.assertEqual(run.states[19]["Melee"], 1)
        self.assertEqual(run.states[20]["Melee"], 0)
        self.assertEqual(run.montage_plays(), ["Light1"])

    def test_a_press_in_the_air_does_not_start_a_chain(self):
        run = Run(dal_logic, presses=[3], airborne=range(0, 100), frames=60)
        self.assertEqual(run.montage_plays(), [])

    def test_the_interpreter_runs_the_same_scenarios(self):
        a = Run(dal_logic, presses=[3, 40, 81], frames=300)
        b = Run(dal_interpreter, presses=[3, 40, 81], frames=300)
        self.assertEqual(a.states, b.states)
        self.assertEqual(a.commands, b.commands)


class OracleComparison(unittest.TestCase):
    def compare(self, presses, airborne, frames):
        a = Run(dal_logic, presses, airborne, frames)
        b = Run(oracle, presses, airborne, frames)
        for i, (x, y) in enumerate(zip(a.states, b.states)):
            self.assertEqual(x, y, f"frame {i}")
        self.assertEqual(a.commands, b.commands)
        return a

    def test_scenarios(self):
        self.compare([3], [], 240)
        self.compare([3, 40, 81], [], 400)
        self.compare([3, 25], [], 200)
        self.compare([3, 10], [], 200)
        self.compare([3], range(20, 400), 120)

    def test_random_play(self):
        total_plays = 0
        for seed in range(1, 31):
            rng = random.Random(seed)
            presses = {i for i in range(900) if rng.random() < 0.04}
            airborne = set()
            i = 0
            while i < 900:
                if rng.random() < 0.01:
                    length = rng.randrange(3, 40)
                    airborne.update(range(i, min(i + length, 900)))
                    i += length
                i += 1
            run = self.compare(presses, airborne, 900)
            total_plays += len(run.montage_plays())
        self.assertGreater(total_plays, 100)          # the random sessions really played attacks
        # ... all the way through three-hit chains
        longest = 0
        for seed in range(1, 31):
            rng = random.Random(seed)
            presses = {i for i in range(900) if rng.random() < 0.04}
            run = Run(oracle, presses, (), 900)
            longest = max(longest, max(s["Melee"] for s in run.states))
        self.assertEqual(longest, 3)


if __name__ == "__main__":
    unittest.main()
