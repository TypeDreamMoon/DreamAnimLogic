"""The ALS character against the recordings of the real thing: teacher-forced single steps on the truth takes of the UE recorder
(``G:/DCC/AlsTruth``, ``anim-recording-format.md`` section 13), and the root motion driver of the mantling against the velocity the engine
gave the character during ``als_mantle``.

The harness is the T2 harness of the Blender side (``dreamved.anim.teacher``: for every frame it forces the state the logic had at the start
of the frame, hands it the inputs of the frame and compares the state at the end of the frame, field by field).  It and the readers of
the ``.dvrec`` takes are imported from the DreamVEd tree read-only (``DAL_BLENDER_MODULES``); the compiler is this repository's.  Everything
here skips when the takes, the ALS pack or the Blender-side modules are not on the machine.

* ``TeacherTakeTests``: all ten takes, the character logic only: not one of its 39 state fields may differ in any frame of any take.  The
  commands that push the movement settings to the movement component (``effects``) are compared too; where they differ is the known
  timing of ``RefreshGroundedMovementSettings`` inside ``PhysWalking`` (a crouch or the velocity of the root motion that the movement
  component applies before it), counted per take against ``EFFECT_LIMITS``.
* ``MantleDriverTests``: the mantling is never forced, it is recomputed: the traces run against a box scene that matches the take (a 130 cm
  box, front face at x = 180), the mantling settings and the montage come from the pack, and the root motion override of every frame is
  compared with ``Cmc.Velocity`` of the take (the engine's own result of ``FAlsRootMotionSource_Mantling::PrepareRootMotion``).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

import _bootstrap  # noqa: F401
from _bootstrap import ROOT

TRUTH = os.environ.get("DAL_ALS_TRUTH", r"G:\DCC\AlsTruth")
PACK = os.environ.get("DAL_ALS_PACK", r"G:\DCC\AlsPack")
BLENDER_MODULES = os.environ.get("DAL_BLENDER_MODULES", r"I:\DCC\blender-moon-engine\scripts\modules")
ALS_DIR = os.path.join(ROOT, "als")

TAKES = ("lane", "features", "speed_jumps", "in_air", "view_yaw_sweeps", "stance_changes", "rotation_modes", "view_mode", "roll", "mantle")
# frames in which a pushed movement setting differs from what the engine had set in the next frame: the push is made at the end of the
# character's tick from the velocity of that frame, the engine computes it inside PhysWalking of the next frame -- after UpdateCharacterStateBeforeMovement
# has crouched or stood the character up (stance changes, the start and the end of the roll) and, with root motion, after the root motion has set the velocity
EFFECT_LIMITS = {"lane": 2, "features": 2, "speed_jumps": 0, "in_air": 2, "view_yaw_sweeps": 0, "stance_changes": 18, "rotation_modes": 0, "view_mode": 0,
                 "roll": 26, "mantle": 0}

_STUB_ANIM = """
logic AlsAnimationInstance;
state Jumps: int;
state Snapshots: int;
pub method Jump() { Jumps += 1; }
pub method SnapshotFinalRagdollPose() { Snapshots += 1; }
update(dt) { Jumps += 0; }
"""


def _blender_side():
    """(teacher, dal_host, anim_pack, Recording) of the DreamVEd tree, or None."""
    if not os.path.isdir(os.path.join(BLENDER_MODULES, "dreamved", "anim")):
        return None
    sys.path.insert(0, BLENDER_MODULES)
    if ROOT in sys.path:                          # the compiler of this repository decides: it has to come first
        sys.path.remove(ROOT)
    sys.path.insert(0, ROOT)
    try:
        from dreamved.anim import anim_pack, dal_host, teacher
        from dreamved.anim.recording import Recording
    except Exception:                             # numpy missing, an older tree ...
        return None
    return teacher, dal_host, anim_pack, Recording


SIDE = _blender_side()
HAVE_TAKES = all(os.path.isfile(os.path.join(TRUTH, f"als_{t}.dvrec")) for t in TAKES)
HAVE_PACK = os.path.isfile(os.path.join(PACK, "pack.json"))
READY = SIDE is not None and HAVE_TAKES and HAVE_PACK
WHY = "the truth takes, the ALS pack or the DreamVEd animation modules are not on this machine"


def bindings():
    with open(os.path.join(ALS_DIR, "bindings.json"), encoding="utf-8") as f:
        return json.load(f)["character"]


@unittest.skipUnless(READY, WHY)
class TeacherTakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.teacher, cls.dal_host, cls.anim_pack, cls.Recording = SIDE
        cls.tmp = tempfile.mkdtemp(prefix="als_truth_")
        for name in ("als_lib.dal", "AlsCharacter.dal"):
            shutil.copy(os.path.join(ALS_DIR, name), os.path.join(cls.tmp, name))
        with open(os.path.join(cls.tmp, "AlsAnimationInstance.dal"), "w", encoding="utf-8") as f:       # the animation instance is somebody else's file here
            f.write(_STUB_ANIM)
        cls.pack = cls.anim_pack.Pack(PACK)
        cls.program = cls.dal_host.load_logic(os.path.join(cls.tmp, "AlsCharacter.dal"))
        cls.params = cls.anim_pack.logic_params(cls.pack, cls.program, overrides=bindings())
        cls.reports = {}
        cls.frames = {}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def report(self, take):
        if take not in self.reports:
            rec = self.teacher.from_truth(os.path.join(TRUTH, f"als_{take}.dvrec"), frames=range(60, 100000), logics=["AlsCharacter"])
            harness = self.teacher.TeacherHarness([self.program], params={"AlsCharacter": self.params})
            self.reports[take] = harness.run(rec)
        return self.reports[take]

    def test_the_params_of_the_pack_are_bound(self):
        for name in ("Settings", "MovementSettings", "MantlingHigh", "MantlingHighInAir", "MantlingLow", "MantlingLowBox"):
            self.assertIn(name, self.params)

    def test_no_state_field_of_the_character_differs_in_any_frame_of_any_take(self):
        out = []
        for take in TAKES:
            r = self.report(take)
            fields = r.logics["AlsCharacter"]
            compared = len(fields)
            bad = {p: f.mismatches for p, f in fields.items() if f.mismatches}
            flips = [x for x in r.flips if x[0] == "AlsCharacter"]
            print(f"[truth] {take:16s} {r.frames:5d} frames  {compared} state fields  mismatching fields {len(bad)}  flips {len(flips)}")
            self.assertEqual(bad, {}, take)
            self.assertEqual(flips, [], take)
            self.assertGreaterEqual(compared, 39, take)
            out.append(take)
        self.assertEqual(out, list(TAKES))

    def test_the_pushed_movement_settings_differ_only_where_the_engine_computes_them_inside_the_movement(self):
        for take in TAKES:
            r = self.report(take)
            frames = len(set(r.bad_frames.get("AlsCharacter", [])))
            counted = max(r.commands.get("AlsCharacter", {}).values(), default=0)
            print(f"[truth] {take:16s} frames with a different pushed setting {counted} (limit {EFFECT_LIMITS[take]})")
            self.assertLessEqual(counted, EFFECT_LIMITS[take], take)
            self.assertLessEqual(frames, EFFECT_LIMITS[take], take)

    def test_the_roll_and_the_mantle_are_in_the_takes(self):
        # the takes really contain the actions (the state fields that follow them are compared above): LocomotionAction changes in the recording
        for take, tag in (("roll", "Als.LocomotionAction.Rolling"), ("mantle", "Als.LocomotionAction.Mantling")):
            rec = self.Recording.load(os.path.join(TRUTH, f"als_{take}.dvrec"))
            self.assertIn(tag, set(rec.truth.series("final", "Char.LocomotionAction")))


# ---- the mantling driver against the velocity of the engine ---------------------------------------------------------------------------------

@unittest.skipUnless(READY, WHY)
class MantleDriverTests(unittest.TestCase):
    FIRST = 109                       # the frame in which the engine started the mantle (the take's start_mantling command)
    LAST = 185                        # the last frame of the montage's notify state

    @classmethod
    def setUpClass(cls):
        import test_als_character as base
        import als_world as W
        from dal import runtime as rt
        from dal import settings as dsettings

        teacher, dal_host, anim_pack, Recording = SIDE
        cls.base, cls.W, cls.rt = base, W, rt
        cls.rec = Recording.load(os.path.join(TRUTH, "als_mantle.dvrec"))
        pack = anim_pack.Pack(PACK)
        cls.assets = anim_pack.make_assets(pack, skeleton=pack.skeleton(), humanoid=pack.humanoid() or None)
        data = os.path.join(PACK, "data")
        cls.params = {name: os.path.join(data, asset + ".json") for name, asset in bindings().items()}
        cls.resolver = dsettings.PackResolver(PACK)
        cls.data = data

        assets = cls.assets

        def first_animation(m):
            for track in m.slots:
                if track.segments:
                    return track.segments[0].anim
            return None

        class PackWorld(W.BoxWorld):
            """The scene of the take and the montages of the pack: what the engine answers for the montage asset queries."""

            def _m(self, name):
                return assets.montage(name) if name else None

            def montage_length(self, name):
                m = self._m(name)
                return float(m.play_length) if m else 0.0

            def montage_rate_scale(self, name):
                m = self._m(name)
                return float(m.rate_scale) if m else 1.0

            def montage_sampling_rate(self, name):
                m = self._m(name)
                a = first_animation(m) if m else None
                return float(getattr(a, "frame_rate", 0.0) or 0.0)

            def montage_blend_in_time(self, name):
                m = self._m(name)
                return float(m.blend_in.time) if m else 0.25

            def montage_blend_in_option(self, name):
                m = self._m(name)
                options = {"".join("_" + c.lower() if c.isupper() else c for c in o.name).lstrip("_"): o for o in rt.BlendOption}
                return options.get(str(m.blend_in.option) if m else "linear", rt.BlendOption.Linear)

            def montage_root_transform(self, name, time):
                m = self._m(name)
                a = first_animation(m) if m else None
                if a is None:
                    return rt.Transform()
                t = min(max(float(time), 0.0), float(a.play_length))
                tr = a.root_track_transform(t)
                return rt.Transform(tuple(float(x) for x in tr.translation), tuple(float(x) for x in tr.rotation), tuple(float(x) for x in tr.scale3d))

            def montage_last_root_transform(self, name):
                m = self._m(name)
                a = first_animation(m) if m else None
                return self.montage_root_transform(name, float(a.play_length)) if a else rt.Transform()

        cls.PackWorld = PackWorld

    def v(self, channel, path, f):
        x = self.rec.truth.series(channel, path)[f]
        return x.item() if hasattr(x, "item") else x

    def rig(self, backend):
        base, W = self.base, self.W
        f = self.FIRST
        location = tuple(self.v("pre_cmc", "Actor.Location." + c, f) for c in "XYZ")
        mesh = ((location[0], location[1], location[2] - 90.0), W.yaw_quat(-90.0), (1.0, 1.0, 1.0))
        rig = base.Rig(backend, settings=os.path.join(self.data, "CS_Als_Default.json"), movement=os.path.join(self.data, "MS_Als_Normal.json"),
                       resolver=self.resolver, character_params=self.params, host_class=self.PackWorld, actor_location=location,
                       mesh_world_transform=mesh, velocity=(375.0, 0.0, 0.0), acceleration=(2000.0, 0.0, 0.0))
        for h in rig.hosts.values():
            h.boxes.append(W.Box("Box", (180.0, -100.0, 0.0), (280.0, 100.0, 130.0)))          # the 130 cm box the take put in the scene: the capsule ends at x = 195
        return rig

    def test_the_character_starts_the_mantle_the_take_started(self):
        for backend in self.base.BACKENDS:
            rig = self.rig(backend)
            for _ in range(2):
                rig.step(velocity=(375.0, 0.0, 0.0), acceleration=(2000.0, 0.0, 0.0))           # running at the wall
            self.assertTrue(rig.call("StartMantling"))
            rig.step(velocity=(375.0, 0.0, 0.0), acceleration=(2000.0, 0.0, 0.0), movement_mode=6, auto_hooks=False)       # (the engine has applied MOVE_Custom)
            self.assertEqual(rig.s["LocomotionAction"], "Als.LocomotionAction.Mantling")
            self.assertEqual(rig.s["MantlingState.RootMotionSourceId"], int(self.v("final", "Char.MantlingState.RootMotionSourceId", self.FIRST)))
            self.assertEqual(rig.s["MantlingSource.Kind"], 0)                                      # MS_Als_High: the box is 130 cm high
            self.assertEqual(rig.s["MantlingSource.Montage"], "AM_Als_Mantle_High")

    def test_the_root_motion_of_every_frame_is_the_velocity_the_engine_gave_the_character(self):
        for backend in self.base.BACKENDS:
            rig = self.rig(backend)
            for _ in range(2):
                rig.step(velocity=(375.0, 0.0, 0.0), acceleration=(2000.0, 0.0, 0.0))
            rig.call("StartMantling")
            worst, first = 0.0, None
            for f in range(self.FIRST, self.LAST + 1):
                location = tuple(self.v("pre_cmc", "Actor.Location." + c, f) for c in "XYZ")
                quat = tuple(self.v("pre_cmc", "Actor.Quat." + c, f) for c in "XYZW")
                rig.step(actor_location=location, actor_rotation=quat, velocity=(375.0, 0.0, 0.0), acceleration=(2000.0, 0.0, 0.0), dt=float(self.v("final", "Time.Delta", f)),
                         movement_mode=6, auto_hooks=False)
                commands = [c for c in rig.cmds["AlsCharacter"] if c[0] in ("root_motion_override", "root_motion_clear")]
                truth = [self.v("post_cmc", "Cmc.Velocity." + c, f) for c in "XYZ"]
                if commands and commands[-1][0] == "root_motion_override":
                    mine = commands[-1][1][0]
                else:
                    mine = (0.0, 0.0, 0.0)                                                         # no override: the movement component zeroes the velocity of a custom mode
                err = max(abs(a - b) for a, b in zip(mine, truth))
                worst = max(worst, err)
                if first is None:
                    first = mine
            print(f"[truth] mantle driver ({backend}): {self.LAST - self.FIRST + 1} frames, largest difference to Cmc.Velocity {worst:.4f} cm/s; first frame {tuple(round(x, 2) for x in first)}")
            self.assertLess(worst, 0.02, backend)                                                  # float32 in the engine, 675 cm/s at most: 3e-5 relative
            self.assertLess(max(abs(a - b) for a, b in zip(first, (236.5882, 0.0, 301.9872))), 0.01)


if __name__ == "__main__":
    unittest.main()
