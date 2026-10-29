"""als/AlsAnimationInstance.dal against the recordings of the original ALS in Unreal (``G:\\DCC\\AlsTruth``, teacher forced: truth_als_anim.py).

Skipped when the recordings or the DreamVEd reader (``blender-moon-engine``, read only) are not on the machine.  The takes are large and
not in git; set ``DAL_ALS_TRUTH`` / ``DAL_DREAMVED_MODULES`` / ``DAL_ALS_PACK`` to point at other copies.

What a take contains decides what the comparison proves (``TakeReport.stats``): the lane and in-air takes lock feet, run into the thigh and
foot angle limits and fall; the view yaw sweeps start turns in place; no take makes the original play a dynamic transition (its foot lock
never holds a foot 8 cm away from the target), so that part rests on the oracle and the hand-checked scenarios.
"""

import os
import unittest

import _bootstrap  # noqa: F401

import truth_als_anim as T

HAVE_TRUTH = T.available("lane")

# float32 against double: a tie that the original's single precision decided one way. Listed so that nobody mistakes them for new mismatches.
KNOWN_FLIPS = {("view_yaw_sweeps", "TurnInPlaceState.PlayRate", 216)}       # the view is exactly 60 degrees off: the activation delay (5 frames) equals the threshold


@unittest.skipUnless(HAVE_TRUTH, "the ALS truth recordings (G:/DCC/AlsTruth) or the DreamVEd reader are not on this machine")
class TruthTests(unittest.TestCase):
    reports = {}

    @classmethod
    def report(cls, name):
        if name not in cls.reports:
            cls.reports[name] = T.compare_take(name)
        return cls.reports[name]

    def check(self, name, min_frames):
        r = self.report(name)
        self.assertGreater(r.frames, min_frames, name)
        unexplained = [(k, v.first_bad) for k, v in r.bad_fields().items() if (name, k, v.first_bad) not in KNOWN_FLIPS or v.bad > 1]
        self.assertEqual(unexplained, [], r.format())
        # the foot lock (every FeetState leaf of both feet, every frame) has no flip at all
        compared, bad = r.count(("FeetState.",))
        self.assertGreater(compared, 10000, name)
        self.assertEqual(bad, 0, r.format(("FeetState.",)))
        self.assertLess(r.max_error(("FeetState.",)), 5e-3, name)
        return r

    def test_lane(self):
        r = self.check("lane", 2000)
        self.assertGreater(r.stats["foot lock full"], 1000)
        self.assertGreater(r.stats["foot angle limit at work"], 20)             # the lock held against the foot angle limit of the settings
        self.assertGreater(r.stats["thigh angle limit at work"], 20)
        self.assertGreater(r.fields["FeetState.Left.LockAmount"].frames, 2000)
        # four turns in place: the commands are the ones the original started
        self.assertEqual(r.commands["played TurnInPlaceStanding"], 4)
        self.assertEqual(r.commands["played TurnInPlaceStanding: the original started the same sequence"], 4)
        self.assertEqual(r.commands["original started TurnInPlaceStanding"], 4)
        self.assertEqual(r.fields["FeetState.Left.ThighAxisPelvisSpace(begin_play)"].bad, 0)        # the axes out of the reference skeleton
        self.assertEqual(r.fields["FeetState.Right.ThighAxisPelvisSpace(begin_play)"].bad, 0)

    def test_in_air(self):
        r = self.check("in_air", 3000)
        air = r.fields["InAirState.GroundPredictionAmount"]
        self.assertGreater(air.frames, 800)                                     # the ground prediction amount of every frame the instance was asked in the air
        self.assertEqual(air.bad, 0)
        self.assertGreater(r.sweeps["with a recorded sweep"], 200)              # ... a good part of them with a sweep the recorder redid
        self.assertEqual(r.sweeps["no sweep asked"], 0)                         # the instance sweeps exactly when the original did
        self.assertEqual(r.sweeps["sweep the original did not make"], 0)
        for end in ("Start", "End"):                                            # ... from the same place to the same place
            self.assertEqual(r.fields[f"GroundPrediction.Sweep.{end}"].bad, 0)
            self.assertEqual(r.fields[f"GroundPrediction.Sweep.{end}"].frames, r.sweeps["with a recorded sweep"])

    def test_features(self):
        r = self.check("features", 4000)
        self.assertEqual(r.fields["InAirState.GroundPredictionAmount"].bad, 0)

    def test_view_yaw_sweeps_turn_in_place(self):
        r = self.check("view_yaw_sweeps", 3000)
        self.assertGreater(r.commands["original started TurnInPlaceStanding"], 10)
        self.assertEqual(r.commands["played TurnInPlaceStanding: the original started the same sequence"], r.commands["original started TurnInPlaceStanding"])
        self.assertEqual(r.fields["TurnInPlaceState.ActivationDelay"].bad, 0)
        self.assertEqual(r.fields["RotateInPlaceState.PlayRate"].bad, 0)

    def test_the_other_takes(self):
        for name in ("speed_jumps", "stance_changes", "rotation_modes", "view_mode"):
            with self.subTest(take=name):
                self.check(name, 2000)


@unittest.skipUnless(T.available("far_origin"), "the far-origin truth take (G:/DCC/AlsTruth/far, ANIM-55) is not on this machine")
class FarOriginTests(unittest.TestCase):
    """The original ALS recorded 10 km (1e6 cm) from the origin (`Unreal/AlsHost/scripts/base_takes.py` far_origin): a walk, turns, a run, crouching, a sprint, a jump,
    a roll, with the feet locking in between.  The foot lock runs in the compiler's float32 mode, as the Blender ALS spec and the engine's float arithmetic do: the
    world space lock and the foot targets are doubles (FVector / FQuat), everything else of the foot a float, so what is left against the original is what the original
    itself rounds to a float (a float holds a number of 1e6 in steps of 0.0625).  Before ANIM-55 the world locations were floats here: 7800 of the 70907 foot lock
    values of the take differed (FinalLocation in 2420 and 2320 of 2445 frames, up to 0.08 cm, LockLocation up to 0.3 cm)."""

    @classmethod
    def setUpClass(cls):
        old = os.environ.get("DAL_ALS_FLOAT32")
        os.environ["DAL_ALS_FLOAT32"] = "1"
        try:
            cls.report = T.compare_take("far_origin", tol=T.Tolerance(rel=1.5e-7, abs_=1e-3))        # (a relative 1.5e-7: the original's own float32 of a number of 1e6)
        finally:
            if old is None:
                del os.environ["DAL_ALS_FLOAT32"]
            else:
                os.environ["DAL_ALS_FLOAT32"] = old

    def test_the_take_is_far_from_the_origin_and_has_locked_feet(self):
        r = self.report
        self.assertGreater(r.frames, 2000)
        self.assertGreater(r.stats["foot lock full"], 500)

    def test_the_foot_lock_is_the_originals_to_the_float_it_stores(self):
        compared, bad = self.report.count(("FeetState.",))
        self.assertGreater(compared, 60000)
        self.assertLess(bad, 100, self.report.format(("FeetState.",)))              # (48: the frames of a released lock, whose component space lock is a world zero seen from 10 km)

    def test_the_final_feet_and_the_world_targets_are_exact_in_every_frame(self):
        group = self.report.group(("FeetState.",))
        for side in ("Left", "Right"):
            for name in ("FinalLocation", "TargetLocationWorldSpace"):
                stat = group[f"FeetState.{side}.{name}"]
                self.assertEqual(stat.bad, 0, name)
                self.assertLess(stat.max_error, 5e-3, f"{side}.{name}")


if __name__ == "__main__":
    unittest.main()
