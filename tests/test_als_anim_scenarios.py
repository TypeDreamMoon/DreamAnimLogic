"""als/AlsAnimationInstance.dal: hand-checked scenarios of the parts that are easy to get wrong.

Each test drives the stand-in character and the instance (generated Python) with a few frames and compares the state with numbers
worked out from the ALS C++ (the line numbers are in the .dal).  The random comparison against the independent port is in
test_als_anim.py; here the expected value is written down, so a mistake the port and the .dal share cannot hide.
"""

import math
import os
import unittest

import _bootstrap  # noqa: F401

import als_sources as A
from dal import harness
from dal.pybackend import generate_python, load_class
from test_als_anim import (CHARACTER, HIPS, INSTANCE, SEQ, TURNS, axis_quat, character_settings_data, curve_data, instance_states, settings_data, system_params,
                           tag)

GAIT = {k: tag("Gait", k) for k in ("Walking", "Running", "Sprinting")}
ROT = {k: tag("RotationMode", k) for k in ("VelocityDirection", "ViewDirection", "Aiming")}
RAGDOLLING = {"LocomotionAction": tag("LocomotionAction", "Ragdolling")}


def inv_exp(x):
    return 1.0 / (1.0 + 1.00746054 * x + 0.45053901 * x * x + 0.25724632 * x ** 3)


def alpha(dt, half_life):
    """UAlsMath::DamperExactAlpha."""
    return 1.0 - inv_exp(math.log(2.0) / (half_life + 1e-8) * dt)


class Sim:
    """Frames through the stand-in character and the instance (generated Python), one at a time.  ``step`` runs a frame -- the
    character after the movement, the instance's update, then the graph's entry calls ``calls`` (names or ``(name, kwargs)``) -- and
    runs the game thread's ``post_update``, and returns the state of the instance (``post=False`` stops before the post update: the state the graph
    left).  Every frame states the character's inputs (free inputs would stay set otherwise)."""

    LOC = {"bHasInput": False, "InputYawAngle": 0.0, "bHasVelocity": False, "Speed": 0.0, "Velocity": (0.0, 0.0, 0.0), "VelocityYawAngle": 0.0,
           "bMoving": False, "TargetYawAngle": 0.0}
    TAGS = {"InViewMode": tag("ViewMode", "ThirdPerson"), "InLocomotionMode": tag("LocomotionMode", "Grounded"), "InRotationMode": tag("RotationMode", "ViewDirection"),
            "InStance": tag("Stance", "Standing"), "InGait": tag("Gait", "Walking"), "InOverlayMode": tag("OverlayMode", "Default"), "InLocomotionAction": ""}

    def __init__(self, mods, params=None, dt=1 / 60, skeleton=None, float32=None):
        self.mods, self.dt = mods, dt
        self.params = params if params is not None else {INSTANCE: {"Settings": settings_data()}}
        self.hosts = {n: harness.ScriptedHost() for n in mods}
        for host in self.hosts.values():
            host.skeleton = {k: dict(v) for k, v in (skeleton or {}).items()}               # the reference skeleton the thigh axes are read from at begin play
        self.objs = {n: load_class(m, generate_python(m, float32=float32), float32=float32)(self.hosts[n], **self.params.get(n, {})) for n, m in mods.items()}
        for n, m in mods.items():
            for p in m.peers:
                self.objs[n].connect(p.name, self.objs[p.logic])
        for n in sorted(mods, key=lambda k: mods[k].kind != "actor"):
            begin = getattr(self.objs[n], "begin_play", None)
            if begin is not None:
                begin()
        self.frames = []
        self.time = 0.0
        self.last = {}

    def step(self, calls=(), loc=None, view=None, curves=None, tags=None, yaw=0.0, location=(0.0, 0.0, 100.0), dt=None, ready=True, scale=1.0,
             max_acc=2000.0, max_brake=1500.0, jump=False, ragdoll_velocity=(0.0, 0.0, 0.0), post=True, host=None, events=(), floor_z=0.71):
        """``host``: more items of the frame (``bones``, ``mesh_world_transform``, ``world``, ``time_dilation`` ...); ``events``: notify events of the frame."""
        dt = self.dt if dt is None else dt
        self.time += dt
        loc_state = dict(self.LOC)
        if loc:
            loc_state.update(loc)
            if "Speed" not in loc and "Velocity" in loc:
                loc_state["Speed"] = math.hypot(loc["Velocity"][0], loc["Velocity"][1])
        free = {"InLocomotion": loc_state, "InView": {"Rotation": (0.0, 0.0, 0.0), "YawSpeed": 0.0, **(view or {})}, "DoJump": jump,
                "InRagdolling": {"Velocity": ragdoll_velocity}, **self.TAGS}
        for key, value in (tags or {}).items():
            free["In" + key] = value
        frame = {"dt": dt, "time": self.time, "actor_location": location, "actor_rotation": axis_quat(yaw), "mesh_scale": scale,
                 "capsule_radius": 30.0, "capsule_half_height": 90.0, "max_acceleration": max_acc, "max_braking_deceleration": max_brake,
                 "walkable_floor_z": floor_z, "actor_ready": ready, "curves": dict(curves or {}), "events": list(events), **(host or {})}
        self.frames.append(frame)
        for n in self.mods:
            self.hosts[n].load(frame)
        for name, value in free.items():
            self.objs[CHARACTER].set_input(name, value)
        self.objs[CHARACTER].update_phase("after_movement", dt)
        self.objs[INSTANCE].update(dt)
        for item in calls:
            name, kwargs = (item, {}) if isinstance(item, str) else item
            self.objs[INSTANCE].call_entry(name, **kwargs)
        self.before_post = instance_states(self.objs[INSTANCE].snapshot())          # what the graph left
        if post:
            self.post()
        else:
            self.last = self.before_post
        return self.last

    def post(self):
        """The game thread's post_update after the graph update (NativePostUpdateAnimation); ``step(..., post=False)`` leaves it to the test."""
        self.objs[INSTANCE].post_update()
        self.last = instance_states(self.objs[INSTANCE].snapshot())
        return self.last

    def live(self):
        """``(instance, character)``: the live objects, to call a pub view or to set a state."""
        return self.objs[INSTANCE], self.objs[CHARACTER]


class ScenarioBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mods = A.compile_system()

    def sim(self, **kw):
        return Sim(self.mods, **kw)


class LayeringPoseLocomotionTests(ScenarioBase):
    def test_layering_state_is_the_curves(self):
        s = self.sim().step(curves={"LayerHead": 0.3, "LayerArmLeft": 0.7, "LayerArmLeftLocalSpace": 0.99998, "LayerArmRightLocalSpace": 0.999995,
                                    "LayerSpineSlot": 0.25, "LayerLegs": 1.0})
        self.assertAlmostEqual(s["LayeringState.HeadBlendAmount"], 0.3)
        self.assertAlmostEqual(s["LayeringState.ArmLeftBlendAmount"], 0.7)
        self.assertAlmostEqual(s["LayeringState.SpineSlotBlendAmount"], 0.25)
        self.assertEqual(s["LayeringState.LegsBlendAmount"], 1.0)
        # a curve that is not there reads 0, also where the struct's default is 1 (AI:299: Curves.Find() or 0)
        self.assertEqual(s["LayeringState.HeadSlotBlendAmount"], 0.0)
        self.assertEqual(s["LayeringState.PelvisSlotBlendAmount"], 0.0)
        # the mesh space blend is 1 unless the local space blend is (nearly) 1: IsFullWeight is >= 1 - 1e-5 (AI:313)
        self.assertEqual(s["LayeringState.ArmLeftMeshSpaceBlendAmount"], 1.0)
        self.assertEqual(s["LayeringState.ArmRightMeshSpaceBlendAmount"], 0.0)
        self.assertEqual(s["LayeringState.ArmLeftLocalSpaceBlendAmount"], 0.99998)

    def test_pose_gait_amounts_and_the_unweighted_ones(self):
        sim = self.sim()
        s = sim.step(curves={"PoseGait": 2.5, "PoseGrounded": 0.5, "PoseInAir": 0.5, "PoseStanding": 0.8, "PoseCrouching": 0.2, "PoseMoving": 1.0})
        self.assertEqual((s["PoseState.GaitAmount"], s["PoseState.GaitWalkingAmount"], s["PoseState.GaitRunningAmount"], s["PoseState.GaitSprintingAmount"]),
                         (2.5, 1.0, 1.0, 0.5))
        self.assertEqual(s["PoseState.UnweightedGaitAmount"], 5.0)          # 2.5 / 0.5, the [0, 3] of the UPROPERTY is only a hint
        self.assertEqual((s["PoseState.UnweightedGaitWalkingAmount"], s["PoseState.UnweightedGaitRunningAmount"], s["PoseState.UnweightedGaitSprintingAmount"]),
                         (1.0, 1.0, 1.0))
        self.assertEqual((s["PoseState.StandingAmount"], s["PoseState.CrouchingAmount"], s["PoseState.MovingAmount"]), (0.8, 0.2, 1.0))
        s = sim.step(curves={"PoseGait": 7.0, "PoseGrounded": 0.0})
        self.assertEqual(s["PoseState.GaitAmount"], 3.0)                    # clamped to [0, 3]
        self.assertEqual(s["PoseState.UnweightedGaitAmount"], 3.0)          # no grounded weight: the gait amount itself (AI:368)
        s = sim.step(curves={"PoseGait": -1.0, "PoseGrounded": 1.0})
        self.assertEqual(s["PoseState.GaitAmount"], 0.0)

    def test_acceleration_is_the_velocity_change_over_the_frame(self):
        sim = self.sim()
        s = sim.step(loc={"Velocity": (100.0, 0.0, 0.0)})
        self.assertEqual(s["LocomotionState.AccelerationWorldSpace"], (0.0, 0.0, 0.0))        # bPendingUpdate: no rate of change yet
        s = sim.step(loc={"Velocity": (150.0, 30.0, 0.0)})
        self.assertAlmostEqual(s["LocomotionState.AccelerationWorldSpace"][0], 50.0 * 60.0)
        self.assertAlmostEqual(s["LocomotionState.AccelerationWorldSpace"][1], 30.0 * 60.0)
        s = sim.step(loc={"Velocity": (150.0, 30.0, 0.0)}, dt=0.0)
        self.assertEqual(s["LocomotionState.AccelerationWorldSpace"], (0.0, 0.0, 0.0))        # dt = 0: no division (AI:686)

    def test_lean_from_acceleration_and_braking(self):
        sim = self.sim()
        sim.step(calls=["InitializeLean"], loc={"Velocity": (0.0, 0.0, 0.0)})
        s = sim.step(calls=["RefreshGrounded"], loc={"Velocity": (200.0, 0.0, 0.0)})
        # acceleration 12000 along +X, MaxAcceleration 2000: the amount is clamped to length 1, and smoothed with the half life 0.2
        self.assertAlmostEqual(s["LeanState.ForwardAmount"], alpha(1 / 60, 0.2), places=9)
        self.assertEqual(s["LeanState.RightAmount"], 0.0)
        for _ in range(60):
            s = sim.step(calls=["RefreshGrounded"], loc={"Velocity": (200.0, 0.0, 0.0)})
        self.assertAlmostEqual(s["LeanState.ForwardAmount"], 0.0, places=2)             # the acceleration is over: the lean follows it back
        sim.step(calls=["RefreshGrounded"], loc={"Velocity": (250.0, 0.0, 0.0)})
        s = sim.step(calls=["RefreshGrounded"], loc={"Velocity": (150.0, 0.0, 0.0)})
        self.assertLess(s["LeanState.ForwardAmount"], 0.0)                              # braking leans backwards

    def test_braking_uses_the_braking_deceleration_not_the_acceleration(self):
        sim = self.sim()
        sim.step(calls=["RefreshGrounded"], loc={"Velocity": (300.0, 0.0, 0.0)})
        s = sim.step(calls=["RefreshGrounded"], loc={"Velocity": (297.0, 0.0, 0.0)}, max_acc=2000.0, max_brake=1000.0)
        # accel = -180 against the velocity: amount = -180 / 1000 (AI:834), smoothed
        self.assertAlmostEqual(s["LeanState.ForwardAmount"], alpha(1 / 60, 0.2) * -0.18, places=9)
        sim2 = self.sim()
        sim2.step(calls=["RefreshGrounded"], loc={"Velocity": (300.0, 0.0, 0.0)})
        s2 = sim2.step(calls=["RefreshGrounded"], loc={"Velocity": (297.0, 0.0, 0.0)}, max_acc=2000.0, max_brake=0.0)
        self.assertEqual(s2["LeanState.ForwardAmount"], 0.0)                            # no braking deceleration: no lean at all (AI:840)

    def test_lean_is_in_the_space_of_the_character(self):
        # the actor faces +Y (yaw 90): an acceleration along world +X is towards its left, that is local -Y
        sim = self.sim()
        sim.step(calls=["RefreshGrounded"], yaw=90.0, loc={"Velocity": (0.0, 0.0, 0.0)})
        s = sim.step(calls=["RefreshGrounded"], yaw=90.0, loc={"Velocity": (100.0, 0.0, 0.0)})
        self.assertAlmostEqual(s["LeanState.RightAmount"], alpha(1 / 60, 0.2) * -1.0, places=9)
        self.assertAlmostEqual(s["LeanState.ForwardAmount"], 0.0, places=9)

    def test_the_first_frame_is_not_smoothed(self):
        sim = self.sim()
        s = sim.step(calls=["RefreshInAir"], loc={"Velocity": (350.0, 0.0, -200.0)})
        # in the air on the very first frame the lean is the target itself: v / 350 * LeanAmountCurve(vz) = 1 * curve(-200) = 0.6 (AI:1231)
        self.assertAlmostEqual(s["LeanState.ForwardAmount"], 0.6)

    def test_moving_smooth_has_its_own_speed_threshold(self):
        sim = self.sim()
        s = sim.step(loc={"bHasInput": True, "bHasVelocity": True, "Speed": 10.0})
        self.assertTrue(s["LocomotionState.bMovingSmooth"])                                     # input and velocity
        s = sim.step(loc={"bHasInput": False, "bHasVelocity": True, "Speed": 149.0})
        self.assertFalse(s["LocomotionState.bMovingSmooth"])
        s = sim.step(loc={"bHasInput": False, "bHasVelocity": True, "Speed": 151.0})
        self.assertTrue(s["LocomotionState.bMovingSmooth"])                                     # above MovingSmoothSpeedThreshold (150)
        s = sim.step(loc={"bHasInput": True, "bHasVelocity": False, "Speed": 0.0, "bMoving": True})
        self.assertFalse(s["LocomotionState.bMovingSmooth"])
        self.assertTrue(s["LocomotionState.bMoving"])                                           # bMoving is the character's own (AI:713)

    def test_the_actor_rotation_is_what_the_instance_reports(self):
        sim = self.sim()
        sim.step(yaw=10.0)
        s = sim.step(yaw=100.0)
        self.assertAlmostEqual(s["LocomotionState.RotationWorldSpace"][1], 100.0)
        self.assertAlmostEqual(s["LocomotionState.YawVelocityWorldSpace"], 90.0 * 60.0)
        s = sim.step(yaw=-170.0)                                                                # 100 -> -170: 90 degrees across the seam, not -270
        self.assertAlmostEqual(s["LocomotionState.YawVelocityWorldSpace"], 90.0 * 60.0)
        s = sim.step(yaw=-170.0)
        self.assertAlmostEqual(s["LocomotionState.YawVelocityWorldSpace"], 0.0)

    def test_teleport_is_detected_by_distance_once_the_update_is_not_pending(self):
        sim = self.sim()
        sim.step(location=(5000.0, 0.0, 100.0))                                                  # first frame: pending, whatever the distance from the origin
        self.assertEqual(sim.last["TeleportedTime"], 0.0)
        s = sim.step(location=(5030.0, 0.0, 100.0))
        self.assertEqual(s["TeleportedTime"], 0.0)                                               # 30 cm: under TeleportDistanceThreshold (50)
        s = sim.step(location=(5100.0, 0.0, 100.0))
        self.assertAlmostEqual(s["TeleportedTime"], sim.time)                                    # 70 cm
        t = s["TeleportedTime"]
        s = sim.step(location=(5100.0, 0.0, 100.0))
        self.assertEqual(s["TeleportedTime"], t)


class GroundedTests(ScenarioBase):
    def test_velocity_blend_amounts(self):
        sim = self.sim()
        s = sim.step(calls=["InitializeGrounded", "RefreshGrounded"], loc={"Velocity": (300.0, 0.0, 0.0)})
        self.assertEqual((s["GroundedState.VelocityBlend.ForwardAmount"], s["GroundedState.VelocityBlend.BackwardAmount"],
                          s["GroundedState.VelocityBlend.LeftAmount"], s["GroundedState.VelocityBlend.RightAmount"]), (1.0, 0.0, 0.0, 0.0))
        self.assertFalse(s["GroundedState.VelocityBlend.bInitializationRequired"])
        s = sim.step(calls=["InitializeGrounded", "RefreshGrounded"], loc={"Velocity": (200.0, 200.0, 0.0)})
        self.assertAlmostEqual(s["GroundedState.VelocityBlend.ForwardAmount"], 0.5)     # diagonals are 0.5 and 0.5
        self.assertAlmostEqual(s["GroundedState.VelocityBlend.RightAmount"], 0.5)
        s = sim.step(calls=["InitializeGrounded", "RefreshGrounded"], loc={"Velocity": (-100.0, -300.0, 0.0)})
        self.assertAlmostEqual(s["GroundedState.VelocityBlend.BackwardAmount"], 0.25)   # normalised so that |x| + |y| + |z| = 1
        self.assertAlmostEqual(s["GroundedState.VelocityBlend.LeftAmount"], 0.75)
        self.assertEqual(s["GroundedState.VelocityBlend.ForwardAmount"], 0.0)

    def test_velocity_blend_is_in_the_space_of_the_character_and_is_smoothed(self):
        sim = self.sim()
        sim.step(calls=["InitializeGrounded", "RefreshGrounded"], yaw=90.0, loc={"Velocity": (0.0, 300.0, 0.0)})           # moving where it faces
        s = sim.step(calls=["RefreshGrounded"], yaw=90.0, loc={"Velocity": (0.0, 300.0, 0.0)})
        self.assertAlmostEqual(s["GroundedState.VelocityBlend.ForwardAmount"], 1.0)
        s = sim.step(calls=["RefreshGrounded"], yaw=90.0, loc={"Velocity": (300.0, 0.0, 0.0)})       # now to the left of it
        self.assertAlmostEqual(s["GroundedState.VelocityBlend.ForwardAmount"], 1.0 - alpha(1 / 60, 0.1), places=9)
        self.assertAlmostEqual(s["GroundedState.VelocityBlend.LeftAmount"], alpha(1 / 60, 0.1), places=9)
        s = sim.step(calls=["RefreshGrounded"], yaw=90.0, loc={"Velocity": (0.0, 0.0, 0.0)})         # no velocity, no direction: every target is 0
        self.assertLess(s["GroundedState.VelocityBlend.LeftAmount"], alpha(1 / 60, 0.1))

    def direction(self, velocity_yaw, tags=None, view_yaw=0.0):
        s = self.sim().step(calls=["RefreshGroundedMovement"], tags=tags or {"RotationMode": ROT["ViewDirection"]},
                            view={"Rotation": (0.0, view_yaw, 0.0)}, loc={"VelocityYawAngle": velocity_yaw})
        return tuple(k for k in ("Forward", "Backward", "Left", "Right") if s["GroundedState.MovementDirection.b" + k])

    def test_movement_direction_ranges_overlap_by_the_threshold(self):
        self.assertEqual(self.direction(0.0), ("Forward",))
        self.assertEqual(self.direction(75.0), ("Forward",))             # 70 + 5: still forward
        self.assertEqual(self.direction(75.5), ("Right",))
        self.assertEqual(self.direction(115.0), ("Right",))              # 180 - 70 + 5
        self.assertEqual(self.direction(115.5), ("Backward",))
        self.assertEqual(self.direction(180.0), ("Backward",))
        self.assertEqual(self.direction(-115.0), ("Left",))
        self.assertEqual(self.direction(-116.0), ("Backward",))
        self.assertEqual(self.direction(-100.0), ("Left",))
        self.assertEqual(self.direction(150.0, tags={"RotationMode": ROT["VelocityDirection"]}), ("Forward",))      # velocity direction: always forward
        self.assertEqual(self.direction(150.0, tags={"RotationMode": ROT["ViewDirection"], "Gait": GAIT["Sprinting"]}), ("Forward",))

    def test_movement_direction_is_relative_to_the_view(self):
        self.assertEqual(self.direction(90.0, view_yaw=90.0), ("Forward",))
        self.assertEqual(self.direction(180.0, view_yaw=90.0), ("Right",))
        self.assertEqual(self.direction(-170.0, view_yaw=170.0), ("Forward",))        # 20 degrees across the seam

    def test_rotation_yaw_offsets_are_the_curves_at_the_view_space_velocity_yaw(self):
        sim = self.sim()
        s = sim.step(calls=["RefreshGroundedMovement"], view={"Rotation": (0.0, 30.0, 0.0)}, loc={"VelocityYawAngle": -60.0})       # -90 in the view's space
        self.assertAlmostEqual(s["GroundedState.RotationYawOffsets.ForwardAngle"], -12.0)
        self.assertAlmostEqual(s["GroundedState.RotationYawOffsets.BackwardAngle"], 6.0)
        self.assertAlmostEqual(s["GroundedState.RotationYawOffsets.LeftAngle"], 20.0 * (-60.0 - -90.0) / (-60.0 - -180.0))      # (-180, 20) .. (-60, 0) at -90
        self.assertAlmostEqual(s["GroundedState.RotationYawOffsets.RightAngle"], -30.0 + 60.0 * (-90.0 - -180.0) / 120.0)       # (-180, -30) .. (-60, 30)
        s = sim.step(calls=["RefreshGroundedMovement"], view={"Rotation": (0.0, -170.0, 0.0)}, loc={"VelocityYawAngle": 175.0})  # 175 - -170 = 345, unwound to -15
        self.assertAlmostEqual(s["GroundedState.RotationYawOffsets.ForwardAngle"], -2.0, places=6)    # curve(-15) = -12 * 15 / 90 on the segment (-90, -12) .. (0, 0)

    def test_hips_direction_lock_and_the_hips_direction_call(self):
        sim = self.sim()
        s = sim.step(calls=["RefreshGroundedMovement"], curves={"HipsDirectionLock": 1.7})
        self.assertEqual(s["GroundedState.HipsDirectionLockAmount"], 1.0)
        s = sim.step(calls=["SetHipsDirection"])
        self.assertEqual(s["GroundedState.HipsDirection"], 0)                                   # no argument: the default, Forward
        s = sim.step(calls=[("SetHipsDirection", {"NewHipsDirection": "LeftBackward"})])
        self.assertEqual(s["GroundedState.HipsDirection"], HIPS.index("LeftBackward"))
        s = sim.step(calls=[("SetHipsDirection", {"NewHipsDirection": "RightForward"}), ("SetHipsDirection", {"NewHipsDirection": "Backward"})])
        self.assertEqual(s["GroundedState.HipsDirection"], HIPS.index("Backward"))               # the last call wins


class StandingCrouchingTests(ScenarioBase):
    def standing(self, speed, gait_curve, gait="Walking"):
        return self.sim().step(calls=["RefreshStandingMovement"], loc={"Velocity": (speed, 0.0, 0.0)}, tags={"Gait": GAIT[gait]},
                               curves={"PoseGait": gait_curve, "PoseGrounded": 1.0})

    def test_standing_stride_blend_and_play_rate(self):
        s = self.standing(150.0, 1.0)
        self.assertAlmostEqual(s["StandingState.StrideBlendAmount"], 1.0)
        self.assertEqual(s["StandingState.WalkRunBlendAmount"], 0.0)
        self.assertAlmostEqual(s["StandingState.PlayRate"], 1.0)                                # 150 / 150 / 1.0
        s = self.standing(100.0, 1.0)
        stride = 0.55 + 0.45 * (100.0 - 58.3) / (150.0 - 58.3)                                  # the walk curve between (58.3, 0.55) and (150, 1.0)
        self.assertAlmostEqual(s["StandingState.StrideBlendAmount"], stride)
        self.assertAlmostEqual(s["StandingState.PlayRate"], 100.0 / 150.0 / stride)
        s = self.standing(350.0, 2.0, "Running")
        self.assertAlmostEqual(s["StandingState.StrideBlendAmount"], 1.0)                       # the run curve at 350
        self.assertEqual(s["StandingState.WalkRunBlendAmount"], 1.0)
        self.assertAlmostEqual(s["StandingState.PlayRate"], 1.0)                                # 350 / 350 / 1.0
        s = self.standing(250.0, 1.5, "Running")                                                # half way between walk and run: curves and speeds blend
        run = 0.45 + 0.55 * (250.0 - 166.9) / (350.0 - 166.9)
        self.assertAlmostEqual(s["StandingState.StrideBlendAmount"], 0.5 * 1.0 + 0.5 * run, places=9)
        self.assertAlmostEqual(s["StandingState.PlayRate"], (0.5 * 250.0 / 150.0 + 0.5 * 250.0 / 350.0) / (0.5 + 0.5 * run), places=9)
        s = self.standing(600.0, 3.0, "Sprinting")
        self.assertAlmostEqual(s["StandingState.PlayRate"], 600.0 / 600.0 / 1.0)

    def test_standing_play_rate_is_clamped(self):
        s = self.standing(900.0, 1.0)
        self.assertEqual(s["StandingState.PlayRate"], 3.0)
        s = self.standing(0.0, 1.0)
        self.assertAlmostEqual(s["StandingState.PlayRate"], 1e-4)                               # KINDA_SMALL_NUMBER: never exactly 0
        self.assertAlmostEqual(s["StandingState.StrideBlendAmount"], 0.2)

    def test_standing_play_rate_is_divided_by_the_scale(self):
        s = self.sim().step(calls=["RefreshStandingMovement"], scale=2.0, loc={"Velocity": (150.0, 0.0, 0.0), "Speed": 300.0},
                            curves={"PoseGait": 1.0, "PoseGrounded": 1.0})
        self.assertAlmostEqual(s["LocomotionState.ScaleWorldSpace"], 2.0)
        self.assertAlmostEqual(s["StandingState.PlayRate"], 1.0)                                # (300 / 2) / 150 / stride(150)

    def test_a_zero_stride_blend_amount_gives_the_upper_bound_like_the_c_division(self):
        data = settings_data()
        zero = curve_data([(0.0, 0.0), (1000.0, 0.0)])
        data["Standing"]["StrideBlendAmountWalkCurve"] = zero
        data["Standing"]["StrideBlendAmountRunCurve"] = zero
        sim = self.sim(params={INSTANCE: {"Settings": data}})
        s = sim.step(calls=["RefreshStandingMovement"], loc={"Velocity": (0.0, 0.0, 0.0)}, curves={"PoseGait": 1.0, "PoseGrounded": 1.0})
        self.assertEqual(s["StandingState.StrideBlendAmount"], 0.0)
        self.assertEqual(s["StandingState.PlayRate"], 3.0)      # 0 / 0 = NaN in C++: FMath::Clamp(NaN, 1e-4, 3) is 3, and DAL's x / 0 is 0 (decision 6)
        s = sim.step(calls=["RefreshStandingMovement"], loc={"Velocity": (200.0, 0.0, 0.0)}, curves={"PoseGait": 1.0, "PoseGrounded": 1.0})
        self.assertEqual(s["StandingState.PlayRate"], 3.0)      # +inf clamps to 3

    def test_crouching_stride_and_play_rate(self):
        sim = self.sim()
        s = sim.step(calls=["RefreshCrouchingMovement"], loc={"Velocity": (75.0, 0.0, 0.0)})
        self.assertAlmostEqual(s["CrouchingState.StrideBlendAmount"], 0.5)
        self.assertAlmostEqual(s["CrouchingState.PlayRate"], 1.0)                               # 75 / (150 * 0.5)
        s = sim.step(calls=["RefreshCrouchingMovement"], loc={"Velocity": (300.0, 0.0, 0.0)})
        self.assertEqual(s["CrouchingState.PlayRate"], 2.0)                                     # 300 / (150 * 1.0) = 2: at the bound
        s = sim.step(calls=["RefreshCrouchingMovement"], loc={"Velocity": (0.0, 0.0, 0.0)})
        self.assertEqual(s["CrouchingState.StrideBlendAmount"], 0.0)
        self.assertEqual(s["CrouchingState.PlayRate"], 2.0)                                     # 0 / (150 * 0) = NaN in C++: the clamp gives the upper bound
        s = sim.step(calls=["RefreshCrouchingMovement"], loc={"Velocity": (1.0, 0.0, 0.0)})
        self.assertAlmostEqual(s["CrouchingState.PlayRate"], 1.0 / (150.0 * (1.0 / 150.0)), places=6)

    def test_sprint_acceleration_lasts_half_a_second(self):
        sim = self.sim()
        sprint = {"Gait": GAIT["Sprinting"]}
        for _ in range(2):                                                                      # not pending any more
            sim.step(calls=["InitializeStandingMovement", "RefreshStandingMovement"], loc={"Velocity": (0.0, 0.0, 0.0)}, curves={"PoseGait": 1.0})
        speed, rows = 0.0, []
        for _ in range(40):
            speed += 20.0
            s = sim.step(calls=["RefreshStandingMovement"], loc={"Velocity": (speed, 0.0, 0.0)}, tags=sprint, curves={"PoseGait": 3.0})
            rows.append((s["StandingState.SprintTime"], s["StandingState.SprintAccelerationAmount"]))
        self.assertAlmostEqual(rows[0][0], 1 / 60)
        self.assertGreater(rows[0][1], 0.0)                                                     # accelerating forward at the start of the sprint
        self.assertEqual(rows[-1][1], 0.0)                                                      # more than 0.5 s later: no sprint acceleration
        first_zero = next(i for i, (_, a) in enumerate(rows) if a == 0.0)
        self.assertGreaterEqual(rows[first_zero][0], 0.5)
        self.assertTrue(all(a > 0.0 for _, a in rows[:first_zero]))
        s = sim.step(calls=["RefreshStandingMovement"], loc={"Velocity": (800.0, 0.0, 0.0)}, tags={"Gait": GAIT["Running"]})
        self.assertEqual((s["StandingState.SprintTime"], s["StandingState.SprintAccelerationAmount"]), (0.0, 0.0))      # not sprinting: reset

    def test_sprint_time_starts_at_the_threshold_on_a_pending_update(self):
        s = self.sim().step(calls=["RefreshStandingMovement"], loc={"Velocity": (100.0, 0.0, 0.0)}, tags={"Gait": GAIT["Sprinting"]}, curves={"PoseGait": 3.0})
        self.assertEqual((s["StandingState.SprintTime"], s["StandingState.SprintAccelerationAmount"]), (0.5, 0.0))      # AI:1058

    def test_the_sprint_block_amount_and_the_pivot(self):
        sim = self.sim()
        s = sim.step(calls=["RefreshStandingMovement"], curves={"SprintBlock": 1.4})
        self.assertEqual(s["StandingState.SprintBlockAmount"], 1.0)
        s = sim.step(calls=["ActivatePivot"], loc={"Velocity": (150.0, 0.0, 0.0)})
        self.assertTrue(s["StandingState.bPivotActive"])                                        # below PivotActivationSpeedThreshold (200)
        s = sim.step(calls=["ResetPivot"], loc={"Velocity": (150.0, 0.0, 0.0)})
        self.assertFalse(s["StandingState.bPivotActive"])
        s = sim.step(calls=["ActivatePivot"], loc={"Velocity": (250.0, 0.0, 0.0)})
        self.assertFalse(s["StandingState.bPivotActive"])
        s = sim.step(calls=["ActivatePivot", "InitializeStandingMovement"], loc={"Velocity": (100.0, 0.0, 0.0)})
        self.assertFalse(s["StandingState.bPivotActive"])                                       # InitializeStandingMovement came last


class RotateInPlaceTests(ScenarioBase):
    def rotating(self, view_yaw, rotation="Aiming", view_mode="ThirdPerson", moving=False, actor_yaw=0.0):
        s = self.sim().step(calls=["RefreshRotateInPlace"], yaw=actor_yaw, view={"Rotation": (0.0, view_yaw, 0.0)}, loc={"bMoving": moving},
                            tags={"RotationMode": ROT[rotation], "ViewMode": tag("ViewMode", view_mode)})
        return s["RotateInPlaceState.bRotatingLeft"], s["RotateInPlaceState.bRotatingRight"]

    def test_rotate_in_place_yaw_thresholds(self):
        self.assertEqual(self.rotating(49.9), (False, False))
        self.assertEqual(self.rotating(50.1), (False, True))
        self.assertEqual(self.rotating(-49.9), (False, False))
        self.assertEqual(self.rotating(-50.1), (True, False))
        self.assertEqual(self.rotating(50.0), (False, False))                                   # strictly beyond the threshold
        self.assertEqual(self.rotating(120.0, actor_yaw=60.0), (False, True))                   # relative to the actor: 60 degrees
        self.assertEqual(self.rotating(100.0, actor_yaw=60.0), (False, False))                  # 40 degrees
        self.assertEqual(self.rotating(-170.0, actor_yaw=170.0), (False, False))                # 20 degrees across the +-180 seam
        self.assertEqual(self.rotating(60.0, moving=True), (False, False))                      # moving: no rotating in place
        self.assertEqual(self.rotating(60.0, rotation="ViewDirection"), (False, False))         # only aiming or first person
        self.assertEqual(self.rotating(60.0, rotation="VelocityDirection"), (False, False))

    def test_rotate_in_place_first_person_has_its_own_threshold(self):
        self.assertEqual(self.rotating(26.0, rotation="ViewDirection", view_mode="FirstPerson"), (False, True))      # 25 degrees
        self.assertEqual(self.rotating(24.0, rotation="ViewDirection", view_mode="FirstPerson"), (False, False))
        self.assertEqual(self.rotating(-26.0, rotation="VelocityDirection", view_mode="FirstPerson"), (True, False))
        self.assertEqual(self.rotating(30.0, rotation="Aiming", view_mode="FirstPerson"), (False, False))           # aiming in first person: the 50 of third person
        self.assertEqual(self.rotating(51.0, rotation="Aiming", view_mode="FirstPerson"), (False, True))

    def test_rotate_in_place_play_rate_follows_the_view_yaw_speed(self):
        def play_rate(view_speed, rotating=True):
            s = self.sim().step(calls=["RefreshRotateInPlace"], view={"Rotation": (0.0, 80.0 if rotating else 0.0, 0.0), "YawSpeed": view_speed},
                                tags={"RotationMode": ROT["Aiming"]})
            return s["RotateInPlaceState.PlayRate"]

        # the first frame is not smoothed; reference view yaw speed (130, 300) -> play rate (1.15, 3.0)
        self.assertAlmostEqual(play_rate(100.0), 1.15)
        self.assertAlmostEqual(play_rate(130.0), 1.15)
        self.assertAlmostEqual(play_rate(215.0), 1.15 + 0.5 * (3.0 - 1.15))
        self.assertAlmostEqual(play_rate(300.0), 3.0)
        self.assertAlmostEqual(play_rate(900.0), 3.0)
        self.assertAlmostEqual(play_rate(250.0, rotating=False), 1.15)                          # not rotating: the lower play rate
        # later frames approach the target with the half life 0.15 s
        sim = self.sim()
        sim.step(calls=["RefreshRotateInPlace"], view={"Rotation": (0.0, 0.0, 0.0), "YawSpeed": 0.0}, tags={"RotationMode": ROT["Aiming"]})
        s = sim.step(calls=["RefreshRotateInPlace"], view={"Rotation": (0.0, 80.0, 0.0), "YawSpeed": 300.0}, tags={"RotationMode": ROT["Aiming"]})
        self.assertAlmostEqual(s["RotateInPlaceState.PlayRate"], 1.15 + (3.0 - 1.15) * alpha(1 / 60, 0.15), places=9)

    def test_an_entry_called_several_times_in_a_frame_updates_once(self):
        args = dict(view={"Rotation": (0.0, 80.0, 0.0), "YawSpeed": 300.0}, tags={"RotationMode": ROT["Aiming"]})
        many, once = self.sim(), self.sim()
        for sim in (many, once):
            sim.step(calls=["RefreshRotateInPlace"], view={"Rotation": (0.0, 0.0, 0.0)}, tags={"RotationMode": ROT["Aiming"]})
        a = many.step(calls=["RefreshRotateInPlace", "RefreshRotateInPlace", "RefreshRotateInPlace"], **args)
        b = once.step(calls=["RefreshRotateInPlace"], **args)
        self.assertEqual(a["RotateInPlaceState.PlayRate"], b["RotateInPlaceState.PlayRate"])    # bUpdatedThisFrame (AI:1840)
        c = many.step(calls=["RefreshRotateInPlace"], **args)                                   # the next frame updates again
        self.assertNotEqual(c["RotateInPlaceState.PlayRate"], a["RotateInPlaceState.PlayRate"])


class ViewSpineHeadTests(ScenarioBase):
    def test_view_angles_pitch_amount_and_head_blend(self):
        sim = self.sim()
        s = sim.step(yaw=30.0, view={"Rotation": (45.0, 100.0, 0.0), "YawSpeed": 77.0}, curves={"ViewBlock": 0.25, "PoseAiming": 0.5})
        self.assertAlmostEqual(s["ViewState.YawAngle"], 70.0)
        self.assertAlmostEqual(s["ViewState.PitchAngle"], 45.0)
        self.assertAlmostEqual(s["ViewState.PitchAmount"], 0.25)                                # 0.5 - 45 / 180
        self.assertAlmostEqual(s["ViewState.YawSpeed"], 77.0)
        self.assertAlmostEqual(s["ViewState.HeadBlendAmount"], (1.0 - 0.25) * (1.0 - 0.5))
        s = sim.step(yaw=170.0, view={"Rotation": (-90.0, -170.0, 0.0)})
        self.assertAlmostEqual(s["ViewState.YawAngle"], 20.0)                                   # unwound across the seam
        self.assertAlmostEqual(s["ViewState.PitchAmount"], 1.0)
        self.assertEqual(s["ViewState.HeadBlendAmount"], 1.0)

    def test_the_view_angles_freeze_during_an_action(self):
        sim = self.sim()
        sim.step(view={"Rotation": (10.0, 40.0, 0.0)})
        s = sim.step(view={"Rotation": (30.0, 90.0, 0.0)}, tags={"LocomotionAction": tag("LocomotionAction", "Rolling")})
        self.assertAlmostEqual(s["ViewState.YawAngle"], 40.0)                                   # unchanged while an action plays (AI:389)
        self.assertAlmostEqual(s["ViewState.PitchAngle"], 10.0)
        self.assertAlmostEqual(s["ViewState.RotationWorldSpace"][1], 90.0)                      # the world rotation itself is still copied
        s = sim.step(view={"Rotation": (30.0, 90.0, 0.0)})
        self.assertAlmostEqual(s["ViewState.YawAngle"], 90.0)

    def test_spine_follows_the_view_while_aiming_and_decays_with_the_30_degree_clamp(self):
        sim = self.sim()
        aim = {"RotationMode": ROT["Aiming"]}
        s = sim.step(view={"Rotation": (0.0, 40.0, 0.0)}, tags=aim, curves={"PoseAiming": 1.0})
        self.assertTrue(s["SpineState.bSpineRotationAllowed"])
        self.assertEqual(s["SpineState.SpineAmount"], 1.0)                                      # the first frame: no blend
        self.assertAlmostEqual(s["SpineState.YawAngle"], 40.0)
        self.assertAlmostEqual(s["SpineState.FinalYawAngle"], 40.0)                             # blend amount = (1 - ViewBlock) * PoseAiming
        s = sim.step(view={"Rotation": (0.0, 55.0, 0.0)}, tags=aim, curves={"PoseAiming": 0.5})
        self.assertAlmostEqual(s["SpineState.YawAngle"], 55.0)                                  # full weight: the view angle itself
        self.assertAlmostEqual(s["SpineState.FinalYawAngle"], 27.5)                             # lerp_angle(0, 55, 0.5)
        # stop aiming (the actor is still at 0): the spine keeps its world rotation from now on
        view = {"Rotation": (0.0, 55.0, 0.0)}
        s = sim.step(view=view, tags={"RotationMode": ROT["ViewDirection"]}, curves={"PoseAiming": 0.0})
        self.assertFalse(s["SpineState.bSpineRotationAllowed"])
        self.assertEqual(s["SpineState.SpineAmountScale"], 1.0)                                 # 1 / SpineAmount with SpineAmount = 1
        self.assertAlmostEqual(s["SpineState.LastYawAngle"], 55.0)
        self.assertAlmostEqual(s["SpineState.LastYawAngleWorldSpace"], 0.0)
        amount = 1.0 - alpha(1 / 60, 0.7)                                                       # decay with the half life 0.7 (the view is not turning)
        self.assertAlmostEqual(s["SpineState.SpineAmount"], amount, places=9)
        self.assertAlmostEqual(s["SpineState.YawAngle"], 55.0 * amount, places=9)
        # the actor now turns 90 degrees: the spine stays where it was in the world, but at most 30 degrees away from the character
        s = sim.step(yaw=90.0, view=view, tags={"RotationMode": ROT["ViewDirection"]})
        amount *= 1.0 - alpha(1 / 60, 0.7)
        self.assertAlmostEqual(s["SpineState.SpineAmount"], amount, places=9)
        self.assertAlmostEqual(s["SpineState.LastYawAngleWorldSpace"], 60.0)                    # offset unwind(0 - 90) = -90 clamped to -30, + 90
        self.assertAlmostEqual(s["SpineState.YawAngle"], (55.0 - 30.0) * amount, places=9)      # lerp_angle(0, LastYawAngle + offset, amount)
        self.assertAlmostEqual(s["SpineState.FinalYawAngle"], 0.0)                              # nothing is aiming: the final angle is blended out
        for _ in range(600):
            s = sim.step(yaw=90.0, view=view, tags={"RotationMode": ROT["ViewDirection"]})
        self.assertLess(abs(s["SpineState.YawAngle"]), 0.05)
        self.assertLess(s["SpineState.SpineAmount"], 1e-3)

    def test_spine_decay_is_faster_when_the_view_turns_quickly(self):
        def decay(view_speed):
            sim = self.sim()
            sim.step(view={"Rotation": (0.0, 40.0, 0.0)}, tags={"RotationMode": ROT["Aiming"]}, curves={"PoseAiming": 1.0})
            return sim.step(view={"Rotation": (0.0, 40.0, 0.0), "YawSpeed": view_speed}, tags={"RotationMode": ROT["ViewDirection"]})["SpineState.SpineAmount"]

        slow, fast = decay(10.0), decay(160.0)
        self.assertAlmostEqual(slow, 1.0 - alpha(1 / 60, 0.7), places=9)
        self.assertAlmostEqual(fast, 1.0 - alpha(1 / 60, 0.7 * 40.0 / 160.0), places=9)         # the half life is multiplied by 40 / YawSpeed (AI:484)
        self.assertLess(fast, slow)

    def test_spine_in_first_person_follows_the_view_too(self):
        s = self.sim().step(view={"Rotation": (0.0, -30.0, 0.0)}, tags={"ViewMode": tag("ViewMode", "FirstPerson")}, curves={"PoseAiming": 1.0})
        self.assertTrue(s["SpineState.bSpineRotationAllowed"])
        self.assertAlmostEqual(s["SpineState.YawAngle"], -30.0)

    def test_spine_blends_in_with_the_half_life_when_it_was_not_aiming_before(self):
        sim = self.sim()
        sim.step(tags={"RotationMode": ROT["ViewDirection"]})
        sim.step(view={"Rotation": (0.0, 0.0, 0.0)})
        s = sim.step(view={"Rotation": (0.0, 60.0, 0.0)}, tags={"RotationMode": ROT["Aiming"]}, curves={"PoseAiming": 1.0})
        self.assertTrue(s["SpineState.bSpineRotationAllowed"])
        self.assertAlmostEqual(s["SpineState.SpineAmountScale"], 1.0)                           # the amount was 0: 1 / (1 - 0), bias 0
        self.assertEqual(abs(s["SpineState.SpineAmountBias"]), 0.0)
        self.assertAlmostEqual(s["SpineState.SpineAmount"], alpha(1 / 60, 0.1), places=9)      # blends in with the half life 0.1 (AI:460)
        self.assertAlmostEqual(s["SpineState.YawAngle"], 60.0 * alpha(1 / 60, 0.1), places=9)   # lerp_angle(0, 60, amount)

    def head(self, sim, view_yaw=0.0, view_pitch=0.0, yaw=0.0, rotation="ViewDirection", view_mode="ThirdPerson", has_input=False, input_yaw=0.0,
             target_yaw=0.0, calls=("RefreshHead",)):
        return sim.step(calls=list(calls), yaw=yaw, view={"Rotation": (view_pitch, view_yaw, 0.0)},
                        loc={"bHasInput": has_input, "InputYawAngle": input_yaw, "TargetYawAngle": target_yaw},
                        tags={"RotationMode": ROT[rotation], "ViewMode": tag("ViewMode", view_mode)})

    INIT = ("InitializeHead", "RefreshHead")

    def test_head_initializes_to_the_view_and_then_springs_to_it(self):
        sim = self.sim()
        s = self.head(sim, view_yaw=60.0, view_pitch=20.0, calls=self.INIT)
        self.assertAlmostEqual(s["HeadState.YawAngle"], 60.0)
        self.assertAlmostEqual(s["HeadState.PitchAngle"], 20.0)
        self.assertEqual(s["HeadState.YawVelocity"], 0.0)
        self.assertAlmostEqual(s["HeadState.YawAmount"], 60.0 / 360.0 + 0.5)
        self.assertFalse(s["HeadState.bInitializationRequired"])
        s = self.head(sim, view_yaw=-40.0, view_pitch=-10.0)
        self.assertAlmostEqual(s["HeadState.PitchAngle"], 20.0 + (-10.0 - 20.0) * alpha(1 / 60, 0.1), places=9)
        self.assertLess(s["HeadState.YawAngle"], 60.0)
        self.assertLess(s["HeadState.YawVelocity"], 0.0)                                        # the critical spring has picked up speed towards the target
        for _ in range(120):
            s = self.head(sim, view_yaw=-40.0, view_pitch=-10.0)
        self.assertAlmostEqual(s["HeadState.YawAngle"], -40.0, places=2)
        self.assertAlmostEqual(s["HeadState.PitchAngle"], -10.0, places=2)

    def test_head_target_is_kept_inside_175_degrees(self):
        self.assertAlmostEqual(self.head(self.sim(), view_yaw=179.0, calls=self.INIT)["HeadState.YawAngle"], 175.0)       # the neck does not turn to 180
        self.assertAlmostEqual(self.head(self.sim(), view_yaw=-179.0, calls=self.INIT)["HeadState.YawAngle"], -175.0)

    def test_head_is_compensated_for_the_rotation_of_the_character(self):
        sim = self.sim()
        self.head(sim, view_yaw=0.0, yaw=0.0, calls=self.INIT)
        # the actor turns by 3 degrees in 1/60 s while the view does not: the head angle is offset by -3 before it springs back towards the view
        s = self.head(sim, view_yaw=0.0, yaw=3.0)
        self.assertAlmostEqual(s["LocomotionState.YawVelocityWorldSpace"], 180.0)
        self.assertAlmostEqual(s["HeadState.YawAngle"], -3.0 * (1.0 - 0.0), delta=3.0)         # -3 first, then pulled back by the spring
        self.assertLess(s["HeadState.YawAngle"], 0.0)

    def test_head_in_velocity_direction_looks_where_the_input_goes(self):
        sim = self.sim()
        s = self.head(sim, rotation="VelocityDirection", has_input=True, input_yaw=80.0, yaw=20.0, calls=self.INIT)
        self.assertAlmostEqual(s["HeadState.YawAngle"], 60.0)
        self.assertEqual(s["HeadState.PitchAngle"], 0.0)                                        # no pitch towards the input
        s = self.head(sim, rotation="VelocityDirection", has_input=False, yaw=20.0, target_yaw=-100.0, calls=self.INIT)
        self.assertAlmostEqual(s["HeadState.YawAngle"], -120.0)                                 # no input: the target yaw of the character, relative to the actor

    def test_head_does_not_flip_through_180_while_the_character_turns_slowly(self):
        sim = self.sim()
        self.head(sim, rotation="VelocityDirection", has_input=True, input_yaw=40.0, yaw=0.0, calls=self.INIT)
        s = self.head(sim, rotation="VelocityDirection", has_input=True, input_yaw=178.0, yaw=0.0)       # |target| > 175 and no fast turn: keep the head angle
        self.assertAlmostEqual(s["HeadState.YawAngle"], 40.0, places=6)
        s = self.head(sim, rotation="VelocityDirection", has_input=True, input_yaw=170.0, yaw=0.0)       # 170 is inside: the head follows
        self.assertGreater(s["HeadState.YawAngle"], 40.0)

    def test_head_favours_the_direction_of_a_fast_turn(self):
        sim = self.sim()
        self.head(sim, rotation="VelocityDirection", has_input=True, input_yaw=0.0, yaw=0.0, calls=self.INIT)
        # the actor turns by +10 degrees in a frame (600 deg/s) and the input is behind it: the target is +160 although the shortest way is -160
        s = self.head(sim, rotation="VelocityDirection", has_input=True, input_yaw=-150.0, yaw=10.0)
        self.assertAlmostEqual(s["LocomotionState.YawVelocityWorldSpace"], 600.0)
        self.assertGreater(s["HeadState.YawVelocity"], 0.0)

    def test_head_switches_look_sides_with_the_slower_spring(self):
        sim = self.sim()
        self.head(sim, view_yaw=100.0, calls=self.INIT)                                          # looking back over the left shoulder
        s = self.head(sim, view_yaw=-170.0)                                                      # the view moves to the other side: -170 <= -160
        self.assertTrue(s["HeadState.bSwitchingLookSides"])                                     # no input: ready to switch
        self.assertGreater(s["HeadState.YawAngle"], 90.0)                                       # the spring heads for +175 first, it does not jump
        with_input = self.sim()
        self.head(with_input, view_yaw=100.0, calls=self.INIT)
        self.assertFalse(self.head(with_input, view_yaw=-170.0, has_input=True)["HeadState.bSwitchingLookSides"])      # the character is moving: not ready
        first_person = self.sim()
        self.head(first_person, view_yaw=100.0, view_mode="FirstPerson", calls=self.INIT)
        self.assertFalse(self.head(first_person, view_yaw=-170.0, view_mode="FirstPerson")["HeadState.bSwitchingLookSides"])     # never in first person


class InAirTransitionsAndMiscTests(ScenarioBase):
    def test_vertical_velocity_and_the_in_air_lean(self):
        sim = self.sim()
        air = {"LocomotionMode": tag("LocomotionMode", "InAir")}
        s = sim.step(calls=["RefreshInAir"], loc={"Velocity": (350.0, 0.0, -450.0)}, tags=air)
        self.assertEqual(s["InAirState.VerticalVelocityWorldSpace"], -450.0)
        self.assertEqual(s["InAirState.GroundPredictionAmount"], 0.0)                           # fast fall, but no ground in the scene: the sweep finds nothing
        # lean amount = local velocity / 350 * curve(vertical velocity); the first frame is not smoothed. curve(-450) = 0.6 + 0.4 * 250 / 400 (AI:1227)
        self.assertAlmostEqual(s["LeanState.ForwardAmount"], 0.6 + 0.4 * 250.0 / 400.0)
        s = sim.step(calls=["RefreshInAir"], loc={"Velocity": (0.0, 175.0, 300.0)}, tags=air)
        self.assertEqual(s["InAirState.VerticalVelocityWorldSpace"], 300.0)
        target_right = 0.5 * (-0.6 + (-0.4) * 100.0 / 400.0)                                    # curve(300): between (200, -0.6) and (600, -1.0)
        self.assertAlmostEqual(s["LeanState.RightAmount"], target_right * alpha(1 / 60, 0.2), places=9)

    def test_jump_play_rate_and_the_jumped_flag(self):
        sim = self.sim()
        sim.step(calls=["RefreshInAir"], jump=True)                                              # the very first frame: bPendingUpdate swallows the jump
        s = sim.step(calls=["RefreshInAir"])
        self.assertFalse(s["InAirState.bJumped"])
        self.assertEqual(s["InAirState.JumpPlayRate"], 1.0)
        s = sim.step(loc={"Velocity": (300.0, 0.0, 0.0)}, jump=True)                             # the character called Jump(): the update sees bJumpRequested
        self.assertTrue(s["InAirState.bJumped"])
        self.assertFalse(s["InAirState.bJumpRequested"])
        s = sim.step(calls=["RefreshInAir"], loc={"Velocity": (300.0, 0.0, 0.0)})                # bJumped stays until the graph asks (RefreshInAir)
        self.assertFalse(s["InAirState.bJumped"])
        self.assertAlmostEqual(s["InAirState.JumpPlayRate"], 1.2 + (1.5 - 1.2) * 300.0 / 600.0)
        s = sim.step(calls=["RefreshInAir"], loc={"Velocity": (1200.0, 0.0, 0.0)}, jump=True)
        self.assertEqual(s["InAirState.JumpPlayRate"], 1.5)                                      # clamped: speed above the reference
        s = sim.step(calls=["RefreshInAir"], loc={"Velocity": (0.0, 0.0, 0.0)})
        self.assertEqual(s["InAirState.JumpPlayRate"], 1.5)                                      # the play rate stays until the next jump

    def test_a_jump_not_consumed_by_the_graph_survives_the_next_frames(self):
        sim = self.sim()
        sim.step()
        sim.step(jump=True)
        self.assertTrue(sim.step()["InAirState.bJumped"])                                       # (bJumped || bJumpRequested) keeps it
        self.assertFalse(sim.step(calls=["RefreshInAir"])["InAirState.bJumped"])

    def test_pending_update_ends_with_the_post_update_of_the_first_frame_when_the_actor_is_ready(self):
        sim = self.sim()
        s = sim.step(post=False)
        self.assertTrue(s["bPendingUpdate"])                                                    # the entries of the first frame still see it
        self.assertFalse(sim.post()["bPendingUpdate"])                                          # NativePostUpdateAnimation: HasActorRegisteredAllComponents()
        sim = self.sim()
        self.assertTrue(sim.step(ready=False)["bPendingUpdate"])                                # not registered yet: still pending after the post update
        self.assertTrue(sim.step(ready=False)["bPendingUpdate"])
        self.assertFalse(sim.step(ready=True)["bPendingUpdate"])

    def test_the_entries_of_the_first_frame_see_the_pending_update(self):
        sim = self.sim()
        s = sim.step(calls=["RefreshGrounded"], loc={"Velocity": (100.0, 0.0, 0.0)}, max_acc=2000.0)
        self.assertFalse(s["bPendingUpdate"])                                                   # (after the post update)
        # the lean of that first frame was set directly (it saw bPendingUpdate): not smoothed, and with no acceleration yet it is 0
        self.assertEqual(s["LeanState.ForwardAmount"], 0.0)
        sim = self.sim()
        s = sim.step(calls=["RefreshRotateInPlace"], view={"Rotation": (0.0, 80.0, 0.0), "YawSpeed": 300.0}, tags={"RotationMode": ROT["Aiming"]})
        self.assertAlmostEqual(s["RotateInPlaceState.PlayRate"], 3.0)                           # direct, not damped from 1.0

    def test_queued_transitions_are_played_by_the_post_update_of_the_frame(self):
        sim = self.sim()
        s = sim.step(calls=[("PlayTransitionAnimation", {"Sequence": "A_Als_Stop_Right", "PlayRate": 1.5, "StartTime": 0.2})], post=False)
        self.assertEqual(s["TransitionsState.QueuedTransitionSequence"], "A_Als_Stop_Right")     # what the graph queued
        self.assertEqual((s["TransitionsState.QueuedTransitionPlayRate"], s["TransitionsState.QueuedTransitionStartTime"]), (1.5, 0.2))
        sim.hosts[INSTANCE].calls.clear()
        s = sim.post()
        self.assertIsNone(s["TransitionsState.QueuedTransitionSequence"])                       # started and cleared in the same frame (AI:1789-1797)
        self.assertEqual((s["TransitionsState.QueuedTransitionPlayRate"], s["TransitionsState.QueuedTransitionStartTime"]), (1.0, 0.0))
        # PlaySlotAnimationAsDynamicMontage(Sequence, Transition, BlendIn, BlendOut, PlayRate, 1 loop, 0 blend out trigger time, StartTime): a standard blend out
        self.assertEqual(sim.hosts[INSTANCE].calls, [("play_slot_animation", ("A_Als_Stop_Right", "Transition", 0.2, 0.2, 1.5, 1, 0.0, 0.2, 0))])

    def test_a_stop_queued_in_the_same_frame_keeps_the_transition_from_playing(self):
        # PlayQueuedTransitionAnimation returns while a stop is queued (AI:1784); the stop clears its own flag: the sequence waits for the next frame
        sim = self.sim()
        s = sim.step(calls=[("PlayTransitionAnimation", {"Sequence": "A"}), ("StopTransitionAndTurnInPlaceAnimations", {"BlendOutDuration": 0.3})], post=False)
        self.assertTrue(s["TransitionsState.bStopTransitionsQueued"])
        self.assertAlmostEqual(s["TransitionsState.QueuedStopTransitionsBlendOutDuration"], 0.3)
        sim.hosts[INSTANCE].calls.clear()
        s = sim.post()
        self.assertFalse(s["TransitionsState.bStopTransitionsQueued"])
        self.assertEqual(s["TransitionsState.QueuedStopTransitionsBlendOutDuration"], -1.0)
        self.assertEqual(s["TransitionsState.QueuedTransitionSequence"], "A")                    # not played, not dropped
        # StopMontagesWithSlot for the three slots of ALS, with the blend out time of the call (AI:1809-1816)
        self.assertEqual(sim.hosts[INSTANCE].calls, [("stop_montages_with_slot", ("Transition", 0.3)), ("stop_montages_with_slot", ("TurnInPlaceStanding", 0.3)),
                                                     ("stop_montages_with_slot", ("TurnInPlaceCrouching", 0.3))])
        sim.hosts[INSTANCE].calls.clear()
        s = sim.step()
        self.assertIsNone(s["TransitionsState.QueuedTransitionSequence"])                       # the next frame's post update plays it
        self.assertEqual([c[0] for c in sim.hosts[INSTANCE].calls], ["play_slot_animation"])

    def test_the_default_blend_out_of_a_stop_is_minus_one(self):
        s = self.sim().step(calls=["StopTransitionAndTurnInPlaceAnimations"], post=False)
        self.assertTrue(s["TransitionsState.bStopTransitionsQueued"])
        self.assertEqual(s["TransitionsState.QueuedStopTransitionsBlendOutDuration"], -1.0)

    def test_quick_stop_in_view_direction_plays_the_left_transition(self):
        s = self.sim().step(calls=["PlayQuickStopAnimation"], tags={"RotationMode": ROT["ViewDirection"]}, post=False)
        self.assertEqual(s["TransitionsState.QueuedTransitionSequence"], SEQ["StandL"])
        self.assertEqual((s["TransitionsState.QueuedTransitionBlendInDuration"], s["TransitionsState.QueuedTransitionBlendOutDuration"]), (0.1, 0.2))
        self.assertEqual((s["TransitionsState.QueuedTransitionPlayRate"], s["TransitionsState.QueuedTransitionStartTime"]), (1.75, 0.3))
        s = self.sim().step(calls=["PlayQuickStopAnimation"], tags={"RotationMode": ROT["Aiming"], "Stance": tag("Stance", "Crouching")}, post=False)
        self.assertEqual(s["TransitionsState.QueuedTransitionSequence"], SEQ["CrouchL"])

    def test_quick_stop_in_velocity_direction_turns_towards_the_input(self):
        def quick_stop(input_yaw, has_input=True, target_yaw=0.0, actor_yaw=0.0):
            s = self.sim().step(calls=["PlayQuickStopAnimation"], yaw=actor_yaw, loc={"bHasInput": has_input, "InputYawAngle": input_yaw, "TargetYawAngle": target_yaw},
                                tags={"RotationMode": ROT["VelocityDirection"]}, post=False)
            return s["TransitionsState.QueuedTransitionSequence"], s["TransitionsState.QueuedTransitionPlayRate"]

        seq, rate = quick_stop(90.0)
        self.assertEqual(seq, SEQ["StandR"])
        self.assertAlmostEqual(rate, 1.75 + (3.0 - 1.75) * 0.5)
        seq, rate = quick_stop(-45.0)
        self.assertEqual(seq, SEQ["StandL"])
        self.assertAlmostEqual(rate, 1.75 + (3.0 - 1.75) * 0.25)
        self.assertEqual(quick_stop(0.0), (SEQ["StandL"], 1.75))                                 # remaining yaw 0: left (<= 0)
        seq, rate = quick_stop(179.0)                                                            # 179 > 175 is remapped to -181: the character turns counterclockwise
        self.assertEqual(seq, SEQ["StandL"])
        self.assertAlmostEqual(rate, 1.75 + (3.0 - 1.75) * 181.0 / 180.0)                        # FMath::Lerp does not clamp
        seq, rate = quick_stop(0.0, has_input=False, target_yaw=-120.0)                          # without input: towards the target yaw
        self.assertEqual(seq, SEQ["StandL"])
        self.assertAlmostEqual(rate, 1.75 + (3.0 - 1.75) * 120.0 / 180.0)

    def test_transition_left_and_right_pick_the_sequence_by_stance(self):
        s = self.sim().step(calls=[("PlayTransitionLeftAnimation", {"PlayRate": 2.0})], post=False)
        self.assertEqual((s["TransitionsState.QueuedTransitionSequence"], s["TransitionsState.QueuedTransitionPlayRate"]), (SEQ["StandL"], 2.0))
        self.assertEqual((s["TransitionsState.QueuedTransitionBlendInDuration"], s["TransitionsState.QueuedTransitionBlendOutDuration"]), (0.2, 0.2))
        s = self.sim().step(calls=[("PlayTransitionRightAnimation", {"BlendInDuration": 0.1})], tags={"Stance": tag("Stance", "Crouching")}, post=False)
        self.assertEqual(s["TransitionsState.QueuedTransitionSequence"], SEQ["CrouchR"])
        self.assertEqual(s["TransitionsState.QueuedTransitionBlendInDuration"], 0.1)

    def test_from_standing_idle_only(self):
        call = [("PlayTransitionRightAnimation", {"bFromStandingIdleOnly": True})]
        self.assertIsNone(self.sim().step(calls=call, loc={"bMoving": True}, post=False)["TransitionsState.QueuedTransitionSequence"])
        self.assertIsNone(self.sim().step(calls=call, tags={"Stance": tag("Stance", "Crouching")}, post=False)["TransitionsState.QueuedTransitionSequence"])
        self.assertEqual(self.sim().step(calls=call, post=False)["TransitionsState.QueuedTransitionSequence"], SEQ["StandR"])

    def test_transitions_allowed_is_the_curve_at_full_weight(self):
        sim = self.sim()
        self.assertTrue(sim.step(curves={"AllowTransitions": 1.0})["TransitionsState.bTransitionsAllowed"])
        self.assertTrue(sim.step(curves={"AllowTransitions": 0.999995})["TransitionsState.bTransitionsAllowed"])
        self.assertFalse(sim.step(curves={"AllowTransitions": 0.99998})["TransitionsState.bTransitionsAllowed"])
        self.assertFalse(sim.step()["TransitionsState.bTransitionsAllowed"])

    def test_turn_in_place_activation_delay_counts_while_the_view_is_off_and_still(self):
        sim = self.sim()
        args = dict(view={"Rotation": (0.0, 90.0, 0.0), "YawSpeed": 0.0}, curves={"AllowTransitions": 1.0}, tags={"RotationMode": ROT["ViewDirection"]})
        sim.step(calls=["InitializeTurnInPlace", "RefreshTurnInPlace"], **args)
        s = sim.step(calls=["RefreshTurnInPlace"], **args)
        self.assertAlmostEqual(s["TurnInPlaceState.ActivationDelay"], 2 / 60)                    # counted on both frames
        s = sim.step(calls=["RefreshTurnInPlace"], **{**args, "view": {"Rotation": (0.0, 90.0, 0.0), "YawSpeed": 80.0}})
        self.assertEqual(s["TurnInPlaceState.ActivationDelay"], 0.0)                            # the view is turning: reset
        s = sim.step(calls=["RefreshTurnInPlace"], **{**args, "view": {"Rotation": (0.0, 30.0, 0.0), "YawSpeed": 0.0}})
        self.assertEqual(s["TurnInPlaceState.ActivationDelay"], 0.0)                            # inside the threshold: reset
        s = sim.step(calls=["RefreshTurnInPlace"], **{**args, "curves": {}})
        self.assertEqual(s["TurnInPlaceState.ActivationDelay"], 0.0)                            # transitions not allowed: reset
        s = sim.step(calls=["RefreshTurnInPlace"], **{**args, "tags": {"RotationMode": ROT["Aiming"]}})
        self.assertEqual(s["TurnInPlaceState.ActivationDelay"], 0.0)                            # only in view direction rotation mode...
        s = sim.step(calls=["RefreshTurnInPlace"], **{**args, "tags": {"RotationMode": ROT["ViewDirection"], "ViewMode": tag("ViewMode", "FirstPerson")}})
        self.assertEqual(s["TurnInPlaceState.ActivationDelay"], 0.0)                            # ... and not in first person

    def test_flail_play_rate_follows_the_ragdoll_speed(self):
        sim = self.sim()
        s = sim.step(ragdoll_velocity=(300.0, 400.0, 0.0), tags=RAGDOLLING)
        self.assertAlmostEqual(s["RagdollingState.FlailPlayRate"], 0.5)                         # |v| = 500, reference speed 1000 (AI:2059)
        s = sim.step(ragdoll_velocity=(0.0, 0.0, -3000.0), tags=RAGDOLLING)
        self.assertEqual(s["RagdollingState.FlailPlayRate"], 1.0)
        s = sim.step(ragdoll_velocity=(10.0, 0.0, 0.0), tags=RAGDOLLING)
        self.assertAlmostEqual(s["RagdollingState.FlailPlayRate"], 0.01)
        s = sim.step(ragdoll_velocity=(900.0, 0.0, 0.0))                                         # not ragdolling: nothing is updated
        self.assertAlmostEqual(s["RagdollingState.FlailPlayRate"], 0.01)

    def test_the_grounded_entry_mode_is_set_by_a_call_and_reset_when_the_action_changes(self):
        sim = self.sim()
        sim.step()
        inst, _ = sim.live()
        inst.SetGroundedEntryMode("Als.GroundedEntryMode.FromRoll")                              # the AlsAnimNotify_SetGroundedEntryMode notify
        self.assertEqual(sim.step()["GroundedEntryMode"], "Als.GroundedEntryMode.FromRoll")      # it stays until somebody resets it
        self.assertEqual(sim.step(calls=["ResetGroundedEntryMode"])["GroundedEntryMode"], "")   # Call(Self.ResetGroundedEntryMode) of the graph states
        inst.SetGroundedEntryMode("Als.GroundedEntryMode.FromRoll")
        s = sim.step(tags={"LocomotionAction": tag("LocomotionAction", "Rolling")})              # the action changed: reset (AI:147)
        self.assertEqual(s["LocomotionAction"], "Als.LocomotionAction.Rolling")
        self.assertEqual(s["GroundedEntryMode"], "")
        inst.SetGroundedEntryMode("Als.GroundedEntryMode.FromRoll")
        self.assertEqual(sim.step(tags={"LocomotionAction": tag("LocomotionAction", "Rolling")})["GroundedEntryMode"], "Als.GroundedEntryMode.FromRoll")   # same action: kept

    def test_mark_teleported_stores_the_time_of_the_update(self):
        sim = self.sim()
        sim.step()
        sim.step()
        inst, _ = sim.live()
        inst.MarkTeleported()
        self.assertAlmostEqual(inst.TeleportedTime, sim.time)

    def test_the_initial_state_is_ALS_s(self):
        obj = load_class(self.mods[INSTANCE], generate_python(self.mods[INSTANCE]))(harness.ScriptedHost())
        snap = obj.snapshot()
        self.assertEqual((snap["ViewMode"], snap["LocomotionMode"], snap["RotationMode"], snap["Stance"], snap["Gait"], snap["OverlayMode"]),
                         ("Als.ViewMode.ThirdPerson", "Als.LocomotionMode.Grounded", "Als.RotationMode.ViewDirection", "Als.Stance.Standing",
                          "Als.Gait.Walking", "Als.OverlayMode.Default"))
        self.assertEqual((snap["LocomotionAction"], snap["GroundedEntryMode"]), ("", ""))
        self.assertEqual((snap["PoseState.GroundedAmount"], snap["PoseState.StandingAmount"], snap["PoseState.InAirAmount"]), (1.0, 1.0, 0.0))
        self.assertEqual((snap["HeadState.YawAmount"], snap["ViewState.PitchAmount"], snap["ViewState.HeadBlendAmount"]), (0.5, 0.5, 1.0))
        self.assertTrue(snap["bPendingUpdate"])
        self.assertTrue(snap["HeadState.bInitializationRequired"])
        self.assertTrue(snap["GroundedState.VelocityBlend.bInitializationRequired"])
        self.assertTrue(snap["GroundedState.MovementDirection.bForward"])
        self.assertEqual(snap["InAirState.GroundPredictionAmount"], 1.0)
        self.assertEqual(snap["LayeringState.HeadSlotBlendAmount"], 1.0)
        self.assertEqual(snap["TransitionsState.QueuedStopTransitionsBlendOutDuration"], -1.0)
        self.assertEqual(snap["FeetState.Left.ThighAxisPelvisSpace"], (0.0, 0.0, -1.0))
        self.assertEqual(snap["FeetState.Right.ThighAxisPelvisSpace"], (0.0, 0.0, 1.0))
        self.assertFalse(snap["FeetState.bValid"])


class ControlRigInputTests(ScenarioBase):
    def test_control_rig_input_is_assembled_from_the_state(self):
        sim = self.sim()
        sim.step(calls=["InitializeGrounded", "RefreshGrounded", "RefreshInAir"], view={"Rotation": (0.0, 40.0, 0.0)}, tags={"RotationMode": ROT["Aiming"]},
                 loc={"Velocity": (300.0, 100.0, 0.0)}, curves={"PoseGrounded": 0.4, "PoseInAir": 0.6, "PoseAiming": 1.0})
        inst, _ = sim.live()
        rig = inst.GetControlRigInput()
        self.assertTrue(rig.bUseHandIkBones)
        self.assertFalse(rig.bUseFootIkBones)                                                    # the test settings turn it off
        self.assertFalse(rig.bFootTransformsValid)                                               # the frame has no pose: the pelvis reads as the identity, FeetState.bValid stays false
        self.assertAlmostEqual(rig.VelocityBlendForwardAmount, 0.75)                             # normalised (300, 100): 0.75 / 0.25
        self.assertAlmostEqual(rig.VelocityBlendBackwardAmount, 0.0)
        self.assertAlmostEqual(rig.SpineYawAngle, 40.0)
        self.assertAlmostEqual(rig.SpineYawAngle, inst.SpineState.FinalYawAngle)
        self.assertAlmostEqual(rig.PelvisOffsetAmount, 0.4)                                      # in the air the offset needs a ground prediction (none: no ground)
        self.assertEqual(rig.FootLeftLocation, (0.0, 0.0, 0.0))

    def test_pelvis_offset_uses_the_ground_prediction(self):
        sim = self.sim()
        sim.step(calls=["RefreshInAir"], curves={"PoseGrounded": 0.0, "PoseInAir": 1.0})
        inst, _ = sim.live()
        self.assertEqual(inst.GetControlRigInput().PelvisOffsetAmount, 0.0)                      # in the air without a predicted ground: no offset
        inst.InAirState.GroundPredictionAmount = 0.3                                             # what the sweep of RefreshInAir writes
        self.assertAlmostEqual(inst.GetControlRigInput().PelvisOffsetAmount, 0.3)
        inst.PoseState.GroundedAmount = 0.9
        self.assertEqual(inst.GetControlRigInput().PelvisOffsetAmount, 1.0)                      # clamped to 1


# ---- the foot lock, the ground prediction, the transitions and turns in place that are played, the notifies ------------------------------------

def tr(location=(0.0, 0.0, 0.0), yaw=0.0, scale=(1.0, 1.0, 1.0)):
    """A transform of a host item: (location, rotation quaternion, scale)."""
    return (tuple(location), axis_quat(yaw), scale)


def rotate_z(v, degrees):
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c, v[2])


def pose(left=(100.0, -10.0, 0.0), right=(100.0, 10.0, 0.0), left_yaw=0.0, right_yaw=0.0, pelvis=None):
    """The bones the instance reads: the pelvis (component space), the IK feet and the virtual bones (world space; 5 cm above the IK ones)."""
    pelvis = tr((0.0, 0.0, 90.0)) if pelvis is None else pelvis
    return {"pelvis": {"component": pelvis},
            "ik_foot_l": {"world": tr(left, left_yaw)}, "ik_foot_r": {"world": tr(right, right_yaw)},
            "VB foot_l": {"world": tr((left[0], left[1], left[2] + 5.0), left_yaw)}, "VB foot_r": {"world": tr((right[0], right[1], right[2] + 5.0), right_yaw)}}


def near(test, got, want, places=6, msg=""):
    test.assertEqual(len(got), len(want), msg)
    for g, w in zip(got, want):
        test.assertAlmostEqual(g, w, places=places, msg=msg)


class FeetSim(Sim):
    """The simulator with the pose of the previous evaluation: ``feet(...)`` steps a frame with the bones, the mesh transform and the foot curves."""

    def feet(self, left=(100.0, -10.0, 0.0), right=(100.0, 10.0, 0.0), left_yaw=0.0, right_yaw=0.0, mesh=(0.0, 0.0, 0.0), ik=(1.0, 1.0), lock=(1.0, 0.0),
             pelvis=None, curves=None, host=None, **kw):
        c = {"FootLeftIk": ik[0], "FootRightIk": ik[1], "FootLeftLock": lock[0], "FootRightLock": lock[1], "AllowTransitions": 1.0, **(curves or {})}
        h = {"bones": pose(left, right, left_yaw, right_yaw, pelvis), "mesh_world_transform": tr(mesh), **(host or {})}
        return self.step(curves=c, host=h, location=kw.pop("location", mesh), **kw)


class FootLockTests(ScenarioBase):
    def sim(self, use_foot_ik=True, skeleton=None, **kw):
        sim = FeetSim(self.mods, params=system_params(use_foot_ik), skeleton=skeleton, **kw)
        sim.time = 100.0                      # the world has run for a while: a TeleportedTime of 0 is long ago (the lock follows teleports of the last 0.2 s)
        return sim

    def valid_sim(self, **kw):
        """A simulator after the two frames it takes to get a valid pose: the left foot locked at (100, -10, 0), the right one not."""
        sim = self.sim(**kw)
        sim.feet()
        sim.feet()
        return sim

    def test_the_feet_are_valid_from_the_second_frame_on(self):
        sim = self.sim()
        s = sim.feet()
        self.assertEqual((s["FeetState.bValid"], s["FeetState.bBecameValid"]), (False, False))        # the first update is pending (AI:1252)
        self.assertEqual(s["FeetState.FootPlantedAmount"], 0.0)                                          # nothing is read while the feet are not valid (AI:1299)
        s = sim.feet(curves={"FootPlanted": 0.7, "FeetCrossing": 1.4})
        self.assertEqual((s["FeetState.bValid"], s["FeetState.bBecameValid"]), (True, True))
        self.assertEqual((s["FeetState.FootPlantedAmount"], s["FeetState.FeetCrossingAmount"]), (0.7, 1.0))     # the curves, FeetCrossing clamped to [0, 1]
        s = sim.feet(curves={"FootPlanted": -3.0})
        self.assertEqual((s["FeetState.bValid"], s["FeetState.bBecameValid"]), (True, False))
        self.assertEqual(s["FeetState.FootPlantedAmount"], -1.0)                                          # clamped to [-1, 1]

    def test_a_pelvis_that_reads_as_the_identity_is_not_a_pose(self):
        sim = self.sim()
        sim.feet()
        for pelvis in (tr((0.0, 0.0, 0.0)), ((0.00005, 0.0, 0.0), (0.0, 0.0, 0.0, -1.0), (1.0, 1.0, 1.0)), ((0.0, -0.00005, 0.0), (0.00005, 0.0, 0.0, 1.0), (3.0, 3.0, 3.0))):
            s = sim.feet(pelvis=pelvis)                                  # KINDA_SMALL_NUMBER, q and -q are the same rotation, the scale is not compared
            self.assertFalse(s["FeetState.bValid"], pelvis)
        self.assertTrue(sim.feet(pelvis=((0.0002, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)))["FeetState.bValid"])       # 2e-4 is a pose
        sim = self.sim()
        sim.feet()
        self.assertTrue(sim.feet(pelvis=tr((0.0, 0.0, 0.0), yaw=1.0))["FeetState.bValid"])               # a rotation is a pose too

    def test_the_targets_are_the_ik_bones_or_the_virtual_bones(self):
        pelvis = tr((0.0, 0.0, 90.0), yaw=30.0)
        sim = self.sim(use_foot_ik=True)
        sim.feet()
        s = sim.feet(pelvis=pelvis, left=(100.0, -10.0, 0.0), left_yaw=20.0)
        near(self, s["FeetState.Left.TargetLocationWorldSpace"], (100.0, -10.0, 0.0))
        near(self, s["FeetState.Left.TargetRotationWorldSpace"], axis_quat(20.0))
        near(self, s["FeetState.Right.TargetLocationWorldSpace"], (100.0, 10.0, 0.0))
        near(self, s["FeetState.PelvisRotation"], axis_quat(30.0))
        sim = self.sim(use_foot_ik=False)
        sim.feet()
        s = sim.feet(left=(100.0, -10.0, 0.0), left_yaw=20.0)
        near(self, s["FeetState.Left.TargetLocationWorldSpace"], (100.0, -10.0, 5.0))                  # the virtual bone VB foot_l (five centimetres above in the test pose)
        near(self, s["FeetState.Left.TargetRotationWorldSpace"], axis_quat(20.0))

    def test_the_thigh_axes_come_from_the_reference_skeleton(self):
        skeleton = {"pelvis": {"parent": None, "translation": (0.0, 0.0, 90.0)},
                    "thigh_l": {"parent": "pelvis", "translation": (3.0, -9.0, -4.0)}, "calf_l": {"parent": "thigh_l", "translation": (0.0, 0.0, -40.0)},
                    "foot_l": {"parent": "calf_l", "translation": (0.0, 0.0, -40.0)}}
        sim = self.sim(skeleton=skeleton)
        n = math.sqrt(9.0 + 81.0 + 16.0)
        near(self, sim.live()[0].FeetState.Left.ThighAxisPelvisSpace, (3.0 / n, -9.0 / n, -4.0 / n))   # AI:85: the direction of the thigh, normalised
        near(self, sim.live()[0].FeetState.Right.ThighAxisPelvisSpace, (0.0, 0.0, 1.0))                # no foot_r in the skeleton: the default (AI.h: ZAxis)
        near(self, self.sim().live()[0].FeetState.Left.ThighAxisPelvisSpace, (0.0, 0.0, -1.0))

    def test_a_foot_that_is_locked_stays_where_it_was_while_the_character_moves(self):
        sim = self.valid_sim()
        s = sim.last
        self.assertEqual((s["FeetState.Left.LockAmount"], s["FeetState.Right.LockAmount"]), (1.0, 0.0))
        near(self, s["FeetState.Left.LockLocationWorldSpace"], (100.0, -10.0, 0.0))                     # the lock is where the foot target was when it locked
        near(self, s["FeetState.Left.FinalLocation"], (100.0, -10.0, 0.0))
        near(self, s["FeetState.Right.FinalLocation"], (100.0, 10.0, 0.0))                              # not locked: the target in the space of the component
        s = sim.feet(left=(160.0, -10.0, 0.0), mesh=(30.0, 0.0, 0.0))                                   # the animation moved the foot on, the mesh moved 30 cm
        near(self, s["FeetState.Left.LockLocationWorldSpace"], (100.0, -10.0, 0.0))                     # ... the lock did not
        near(self, s["FeetState.Left.LockLocation"], (70.0, -10.0, 0.0))                                # in the space of the component it is 30 cm back
        near(self, s["FeetState.Left.FinalLocation"], (70.0, -10.0, 0.0))                               # full lock: the final foot is the lock (AI:1517)
        near(self, s["FeetState.Right.FinalLocation"], (70.0, 10.0, 0.0))                               # the right foot is the target (100 in the world) in the space of a mesh at 30
        for foot in ("Left", "Right"):
            near(self, s[f"FeetState.{foot}.LockLocationMovementBaseSpace"], (0.0, 0.0, 0.0))            # no moving base
            near(self, s[f"FeetState.{foot}.LockRotationMovementBaseSpace"], (0.0, 0.0, 0.0, 1.0))

    def test_the_lock_blends_out_while_the_character_moves(self):
        sim = self.valid_sim()
        moving = {"bHasInput": True, "bHasVelocity": True, "Speed": 200.0, "bMoving": True}
        s = sim.feet(left=(160.0, -10.0, 0.0), loc=moving)                                               # bMovingSmooth: 5 per second (AI:1405)
        amount = 1.0 - 5.0 / 60.0
        self.assertAlmostEqual(s["FeetState.Left.LockAmount"], amount)
        near(self, s["FeetState.Left.FinalLocation"], (160.0 + (100.0 - 160.0) * amount, -10.0, 0.0))    # lerp(target, lock, amount)
        s = sim.feet(left=(160.0, -10.0, 0.0), loc=moving, dt=1 / 30)
        self.assertAlmostEqual(s["FeetState.Left.LockAmount"], amount - 5.0 / 30.0)
        s = sim.feet(left=(160.0, -10.0, 0.0), loc=moving, dt=1.0)
        self.assertEqual(s["FeetState.Left.LockAmount"], 0.0)                                            # not below zero: released (AI:1416)
        near(self, s["FeetState.Left.LockLocationWorldSpace"], (0.0, 0.0, 0.0))
        near(self, s["FeetState.Left.FinalLocation"], (160.0, -10.0, 0.0))

    def test_in_the_air_the_lock_blends_out_slowly(self):
        sim = self.valid_sim()
        s = sim.feet(tags={"LocomotionMode": tag("LocomotionMode", "InAir")})
        self.assertAlmostEqual(s["FeetState.Left.LockAmount"], 1.0 - 0.6 / 60.0)                          # AI:1406

    def test_the_lock_is_released_when_the_curve_or_the_setting_says_so(self):
        sim = self.valid_sim()
        s = sim.feet(lock=(0.0, 0.0), left=(140.0, -10.0, 0.0))
        self.assertEqual(s["FeetState.Left.LockAmount"], 0.0)
        for name in ("LockLocationWorldSpace", "LockLocation"):
            near(self, s[f"FeetState.Left.{name}"], (0.0, 0.0, 0.0))
        near(self, s["FeetState.Left.LockRotationWorldSpace"], (0.0, 0.0, 0.0, 1.0))
        near(self, s["FeetState.Left.FinalLocation"], (140.0, -10.0, 0.0))
        sim = self.valid_sim()
        s = sim.feet(ik=(0.0, 1.0), left=(140.0, -10.0, 0.0))                                            # the IK curve at 0 means the same (IkAmount * LockAmount)
        self.assertEqual(s["FeetState.Left.LockAmount"], 0.0)
        data = settings_data(True)
        data["FootLock"]["bAllowFootLock"] = False
        sim = FeetSim(self.mods, params={INSTANCE: {"Settings": data}})
        sim.feet()
        s = sim.feet()
        self.assertEqual(s["FeetState.Left.LockAmount"], 0.0)                                            # the setting turns the lock off however the curve stands
        near(self, s["FeetState.Left.FinalLocation"], (100.0, -10.0, 0.0))

    def test_a_lock_never_blends_in(self):
        sim = self.sim()
        sim.feet(lock=(0.5, 0.0))
        s = sim.feet(lock=(0.5, 0.0), left=(140.0, -10.0, 0.0))                                           # a partial lock amount from nothing: still nothing (AI:1440)
        self.assertEqual(s["FeetState.Left.LockAmount"], 0.0)
        near(self, s["FeetState.Left.FinalLocation"], (140.0, -10.0, 0.0))
        s = sim.feet(lock=(1.0, 0.0), left=(140.0, -10.0, 0.0))                                           # ... but a full one locks where the foot is
        self.assertEqual(s["FeetState.Left.LockAmount"], 1.0)
        near(self, s["FeetState.Left.LockLocationWorldSpace"], (140.0, -10.0, 0.0))

    def test_a_relock_keeps_the_old_place_when_the_lock_was_almost_full(self):
        for previous, same_place in ((0.95, True), (0.9, False), (0.8, False)):
            sim = self.valid_sim()
            inst, _ = sim.live()
            foot = inst.FeetState.Left
            foot.LockAmount = previous                                                           # the state the previous frame left: a lock on its way out
            foot.FinalLocation = Vec3(120.0, -10.0, 0.0)
            s = sim.feet(left=(140.0, -10.0, 0.0), lock=(1.0, 0.0))
            self.assertEqual(s["FeetState.Left.LockAmount"], 1.0)
            # <= 0.9: the final location of the previous frame (not the target: it may teleport) becomes the lock (AI:1481); more: the old lock stays
            near(self, s["FeetState.Left.LockLocationWorldSpace"], (100.0, -10.0, 0.0) if same_place else (120.0, -10.0, 0.0), msg=str(previous))

    def test_the_lock_follows_a_teleport_but_not_a_walk(self):
        sim = self.valid_sim()
        s = sim.feet(left=(100.0, -10.0, 0.0), mesh=(30.0, 0.0, 0.0))                                    # 30 cm: under the threshold (50), no teleport
        near(self, s["FeetState.Left.LockLocationWorldSpace"], (100.0, -10.0, 0.0))
        sim = self.valid_sim()
        s = sim.feet(left=(100.0, -10.0, 0.0), mesh=(500.0, 0.0, 0.0))                                   # 500 cm: the update detects the teleport
        self.assertGreater(s["TeleportedTime"], 0.0)
        # the lock in the space of the component (100, -10, 0 for the mesh at the origin) goes along: it is at (600, -10, 0) now, not left at 100
        near(self, s["FeetState.Left.LockLocationWorldSpace"], (600.0, -10.0, 0.0))
        near(self, s["FeetState.Left.LockLocation"], (100.0, -10.0, 0.0))
        # a teleport that the character reports (MarkTeleported) is followed for 0.2 s: a mesh that moved 30 cm takes the lock along ...
        sim = self.valid_sim()
        sim.live()[0].MarkTeleported()
        s = sim.feet(mesh=(30.0, 0.0, 0.0))
        near(self, s["FeetState.Left.LockLocationWorldSpace"], (130.0, -10.0, 0.0))
        # ... and after it the lock stays in the world
        sim = self.valid_sim()
        sim.live()[0].MarkTeleported()
        for _ in range(12):
            sim.feet()
        s = sim.feet(mesh=(30.0, 0.0, 0.0))                                                       # 13 / 60 s after the mark: over
        near(self, s["FeetState.Left.LockLocationWorldSpace"], (100.0, -10.0, 0.0))

    def test_the_thigh_angle_is_limited(self):
        skeleton = {"pelvis": {"parent": None, "translation": (0.0, 0.0, 90.0)}, "thigh_l": {"parent": "pelvis", "translation": (0.0, -10.0, 0.0)},
                    "foot_l": {"parent": "thigh_l", "translation": (0.0, 0.0, -80.0)}}
        sim = self.sim(skeleton=skeleton)
        sim.feet(left=(70.0, 10.0, 0.0))
        s = sim.feet(left=(70.0, 10.0, 0.0))
        # the thigh points to -Y; the lock is 98.13 degrees away from it (atan2 of the cross and the dot product), the limit is 60 (the test settings)
        angle = math.degrees(math.acos(-10.0 / math.hypot(70.0, 10.0)))
        self.assertAlmostEqual(angle, 98.13, places=2)
        want = rotate_z((70.0, 10.0, 0.0), 60.0 - angle)
        near(self, s["FeetState.Left.LockLocation"], want)
        near(self, s["FeetState.Left.LockLocationWorldSpace"], want)                                     # the mesh is at the origin
        near(self, s["FeetState.Left.FinalLocation"], want)
        # within the limit nothing changes
        sim = self.sim(skeleton=skeleton)
        sim.feet(left=(30.0, -40.0, 0.0))
        s = sim.feet(left=(30.0, -40.0, 0.0))
        near(self, s["FeetState.Left.LockLocation"], (30.0, -40.0, 0.0))

    def test_the_foot_angle_is_limited(self):
        sim = self.sim()
        sim.feet(left_yaw=50.0)
        sim.feet(left_yaw=50.0)                                                                      # locks with the foot turned 50 degrees
        near(self, sim.last["FeetState.Left.LockRotationWorldSpace"], axis_quat(50.0))
        s = sim.feet(left_yaw=0.0)                                                                   # the animation turns it back; the lock may be 30 degrees off at most
        near(self, s["FeetState.Left.LockRotationWorldSpace"], axis_quat(30.0))
        near(self, s["FeetState.Left.FinalRotation"], axis_quat(30.0), msg="locked: the final rotation is the lock")
        s = sim.feet(left_yaw=-10.0)                                                                 # 40 degrees from the lock, the limit moves it to 20
        near(self, s["FeetState.Left.LockRotationWorldSpace"], axis_quat(20.0))
        s = sim.feet(left_yaw=0.0)
        near(self, s["FeetState.Left.LockRotationWorldSpace"], axis_quat(20.0))                      # inside the limit: kept

    def test_the_final_rotation_blends_with_the_shortest_route_and_stays_a_unit_quaternion(self):
        sim = self.valid_sim()
        s = sim.feet(left_yaw=20.0, loc={"bHasInput": True, "bHasVelocity": True, "Speed": 200.0}, dt=1 / 30)       # lock 1 - 5/30 = 0.8333, lock rotation 0 (yaw), target yaw 20
        q = s["FeetState.Left.FinalRotation"]
        self.assertAlmostEqual(sum(c * c for c in q), 1.0, places=6)                                 # (FQuat4f{...}: narrowed to float, so unit to a float's 6e-8)
        self.assertAlmostEqual(2.0 * math.degrees(math.atan2(q[2], q[3])), 20.0 * (1.0 - s["FeetState.Left.LockAmount"]), places=0)

    def test_a_pose_that_comes_back_after_a_pending_update_locks_where_the_foot_is(self):
        sim = self.valid_sim()
        s = sim.feet(left=(140.0, -10.0, 0.0), ready=False)                                         # the actor loses its components: the next update is pending
        self.assertTrue(s["bPendingUpdate"])
        s = sim.feet(left=(140.0, -10.0, 0.0))
        self.assertFalse(s["FeetState.bValid"])                                                      # pending: the feet are invalid for the frame (AI:1252)
        s = sim.feet(left=(140.0, -10.0, 0.0))
        self.assertEqual((s["FeetState.bValid"], s["FeetState.bBecameValid"]), (True, True))
        # became valid with a lock that was relevant: the lock starts at the target (AI:1370), without the blend out of a moving character
        near(self, s["FeetState.Left.LockLocationWorldSpace"], (140.0, -10.0, 0.0))
        self.assertEqual(s["FeetState.Left.LockAmount"], 1.0)

    def test_the_foot_lock_far_from_the_origin_is_the_foot_lock_at_the_origin(self):
        """ANIM-55: the world space lock and the foot targets are doubles (FVector / FQuat), the transforms of the component, the proxy and the bones too.  A walk,
        a stop with the feet locked and a turn at 10 km (1e6 cm) from the origin gives the same locks and final feet (the state in the space of the component) as
        at the origin: a float32 holds a world position there in steps of 0.0625 cm, which showed as a few hundredths of a centimetre before."""
        far = 1.0e6
        worst = 0.0
        runs = []
        for x0 in (0.0, far):
            sim = FeetSim(self.mods, params=system_params(True), float32=True)               # (the compiler's float32 mode: what the engine's float arithmetic does)
            sim.time = 100.0
            out = []
            for i in range(120):
                walking = i < 60
                x = x0 + 2.4137 * i if walking else x0 + 2.4137 * 60.0 + 0.31 * math.sin(0.4 * i)                # the mesh walks, stops and sways a little
                yaw = 0.0 if i < 90 else 1.7 * (i - 90)                                                         # ... then turns on the spot
                left = (x + 100.0 + 25.0 * math.sin(0.15 * i), -10.0, 0.0)
                right = (x + 100.0 - 25.0 * math.sin(0.15 * i), 10.0, 0.0)
                lock = (1.0, 0.0) if (i // 20) % 2 == 0 else (0.0, 1.0)
                s = sim.feet(left=left, right=right, left_yaw=yaw * 0.5, right_yaw=yaw * 0.5, mesh=(x, 0.0, 0.0), lock=lock,
                             loc={"bMoving": walking, "bHasVelocity": walking, "Speed": 145.0 if walking else 0.0}, yaw=yaw)
                out.append({k: s[k] for k in ("FeetState.Left.LockLocation", "FeetState.Left.FinalLocation", "FeetState.Right.LockLocation", "FeetState.Right.FinalLocation",
                                              "FeetState.Left.LockLocationMovementBaseSpace", "FeetState.Left.LockAmount", "FeetState.Right.LockAmount")})
            runs.append(out)
        for near_frame, far_frame in zip(*runs):
            for key in near_frame:
                a, b = near_frame[key], far_frame[key]
                a = a if isinstance(a, tuple) else (a,)
                b = b if isinstance(b, tuple) else (b,)
                worst = max(worst, max(abs(u - v) for u, v in zip(a, b)))
        self.assertLess(worst, 2e-3, "the foot lock at 10 km differs from the one at the origin by %.5f cm" % worst)

    def test_the_pose_goes_into_the_control_rig_input(self):
        sim = self.valid_sim()
        sim.feet(left=(160.0, -10.0, 0.0), mesh=(30.0, 0.0, 0.0))
        rig = sim.live()[0].GetControlRigInput()
        self.assertTrue(rig.bFootTransformsValid)
        near(self, rig.FootLeftLocation, (70.0, -10.0, 0.0))
        near(self, rig.FootRightLocation, (70.0, 10.0, 0.0))
        self.assertAlmostEqual(sum(c * c for c in rig.FootLeftRotation), 1.0)


class DynamicTransitionTests(ScenarioBase):
    def locked(self, **kw):
        sim = FeetSim(self.mods, params=system_params(True))
        sim.time = 100.0
        sim.feet(lock=(1.0, 1.0))
        sim.feet(lock=(1.0, 1.0), **kw)
        return sim

    def run_entry(self, sim, left, right, lock=(1.0, 1.0), **kw):
        """One frame in which the targets are ``left`` and ``right`` (the locks are at (100, -10, 0) and (100, 10, 0)) and the graph asks for the dynamic transitions."""
        sim.hosts[INSTANCE].calls.clear()
        s = sim.feet(left=left, right=right, lock=lock, calls=["RefreshDynamicTransitions"], **kw)
        return s, list(sim.hosts[INSTANCE].calls)

    def test_a_foot_that_the_lock_holds_too_far_from_its_target_plays_a_transition(self):
        sim = self.locked()
        s, calls = self.run_entry(sim, (120.0, -10.0, 0.0), (100.0, 10.0, 0.0))                       # 20 cm on the left: over the 8 of the settings
        self.assertEqual(calls, [("play_slot_animation", (SEQ["DynStandL"], "Transition", 0.2, 0.2, 1.5, 1, 0.0, 0.0, 0))])
        self.assertEqual(s["DynamicTransitionsState.FrameDelay"], 2)
        self.assertIsNone(s["TransitionsState.QueuedTransitionSequence"])                             # played and cleared by the post update
        s, calls = self.run_entry(sim, (120.0, -10.0, 0.0), (100.0, 10.0, 0.0))
        self.assertEqual((calls, s["DynamicTransitionsState.FrameDelay"]), ([], 1))                   # two frames of rest for the animation graph (AI:1761)
        s, calls = self.run_entry(sim, (120.0, -10.0, 0.0), (100.0, 10.0, 0.0))
        self.assertEqual((calls, s["DynamicTransitionsState.FrameDelay"]), ([], 0))
        s, calls = self.run_entry(sim, (120.0, -10.0, 0.0), (100.0, 10.0, 0.0))
        self.assertEqual(len(calls), 1)                                                               # and then it plays again

    def test_the_foot_with_the_greater_distance_wins(self):
        for left, right, want in (((120.0, -10.0, 0.0), (130.0, 10.0, 0.0), "DynStandR"), ((130.0, -10.0, 0.0), (120.0, 10.0, 0.0), "DynStandL"),
                                  ((120.0, -10.0, 0.0), (120.0, 10.0, 0.0), "DynStandL"), ((100.0, -10.0, 0.0), (112.0, 10.0, 0.0), "DynStandR")):
            _, calls = self.run_entry(self.locked(), left, right)
            self.assertEqual([c[1][0] for c in calls], [SEQ[want]], (left, right))

    def test_nothing_plays_inside_the_threshold_or_when_transitions_are_not_allowed(self):
        _, calls = self.run_entry(self.locked(), (107.0, -10.0, 0.0), (100.0, 17.0, 0.0))
        self.assertEqual(calls, [])                                                                   # 7 cm: under 8
        _, calls = self.run_entry(self.locked(), (120.0, -10.0, 0.0), (100.0, 10.0, 0.0), curves={"AllowTransitions": 0.0})
        self.assertEqual(calls, [])
        sim = FeetSim(self.mods, params=system_params(True))
        sim.feet(lock=(0.0, 0.0))
        sim.feet(lock=(0.0, 0.0))
        _, calls = self.run_entry(sim, (160.0, -10.0, 0.0), (160.0, 10.0, 0.0), lock=(0.0, 0.0))     # no lock at all (a lock amount of 0 is not relevant)
        self.assertEqual(calls, [])

    def test_the_threshold_scales_with_the_mesh(self):
        _, calls = self.run_entry(self.locked(), (112.0, -10.0, 0.0), (100.0, 10.0, 0.0), scale=2.0)   # 12 cm: more than 8, less than 8 * 2
        self.assertEqual(calls, [])
        _, calls = self.run_entry(self.locked(), (120.0, -10.0, 0.0), (100.0, 10.0, 0.0), scale=2.0)
        self.assertEqual(len(calls), 1)

    def test_a_crouching_character_plays_the_crouching_transitions(self):
        _, calls = self.run_entry(self.locked(), (120.0, -10.0, 0.0), (100.0, 10.0, 0.0), tags={"Stance": tag("Stance", "Crouching")})
        self.assertEqual([c[1][0] for c in calls], [SEQ["DynCrouchL"]])
        _, calls = self.run_entry(self.locked(), (100.0, -10.0, 0.0), (125.0, 10.0, 0.0), tags={"Stance": tag("Stance", "Crouching")})
        self.assertEqual([c[1][0] for c in calls], [SEQ["DynCrouchR"]])

    def test_the_entry_runs_once_per_frame(self):
        sim = self.locked()
        s, calls = self.run_entry(sim, (120.0, -10.0, 0.0), (100.0, 10.0, 0.0))
        sim.hosts[INSTANCE].calls.clear()
        s = sim.feet(left=(120.0, -10.0, 0.0), lock=(1.0, 1.0), calls=["RefreshDynamicTransitions"] * 3)
        self.assertEqual(s["DynamicTransitionsState.FrameDelay"], 1)                                  # the delay of the previous play counted down once, not three times


class GroundPredictionTests(ScenarioBase):
    AIR = {"LocomotionMode": tag("LocomotionMode", "InAir")}

    def fall(self, velocity=(300.0, 0.0, -600.0), z=300.0, ground=0.0, block=0.0, floor_z=0.71, scale=1.0, world=None, **kw):
        sim = self.sim()
        host = {"world": world if world is not None else {"ground_z": ground}}
        s = sim.step(calls=["RefreshInAir"], loc={"Velocity": velocity}, location=(0.0, 0.0, z), tags=self.AIR, floor_z=floor_z, scale=scale, host=host,
                     curves={"PoseGrounded": 0.0, "PoseInAir": 1.0, "GroundPredictionBlock": block}, **kw)
        return s, sim

    def curve(self, t):
        """CF_GroundPrediction of the test settings: (0, 1), (0.4, 0.8), (1, 0)."""
        return 1.0 + (0.8 - 1.0) * t / 0.4 if t <= 0.4 else 0.8 - 0.8 * (t - 0.4) / 0.6

    def test_the_sweep_runs_along_the_velocity_and_the_hit_time_goes_through_the_curve(self):
        s, _ = self.fall(block=0.25)
        vz = -600.0
        distance = 150.0 + (2000.0 - 150.0) * (vz - -200.0) / (-4000.0 - -200.0)                     # 344.74 cm: the faster the fall the longer the sweep
        length = math.hypot(300.0, 600.0)
        drop = distance * 600.0 / length                                                              # how far the sweep goes down
        t = (300.0 - 60.0 - 30.0) / drop                                                              # the lowest sphere of the capsule (60 below the centre) meets the ground at r = 30
        self.assertAlmostEqual(t, 0.68105, places=4)
        self.assertAlmostEqual(s["InAirState.GroundPredictionAmount"], self.curve(t) * 0.75, places=9)

    def test_a_vertical_fall_hits_a_sphere_with_its_slope(self):
        # a sphere of radius 100 at the origin, the capsule falls straight down 80 cm beside the top: the contact normal (80, 0, 102.5) / 130 has z = 0.788
        world = {"ground_z": None, "spheres": [((0.0, 0.0, 0.0), 100.0)]}
        kw = dict(velocity=(0.0, 0.0, -1000.0), world=world)
        distance = 150.0 + 1850.0 * 800.0 / 3800.0
        contact = math.sqrt(130.0 ** 2 - 80.0 ** 2)
        t = (400.0 - 60.0 - contact) / distance
        sim = self.sim()

        def amount(floor_z):
            s = sim.step(calls=["RefreshInAir"], loc={"Velocity": kw["velocity"]}, location=(80.0, 0.0, 400.0), tags=self.AIR, floor_z=floor_z,
                         host={"world": world}, curves={"PoseGrounded": 0.0, "PoseInAir": 1.0})
            return s["InAirState.GroundPredictionAmount"]

        self.assertAlmostEqual(math.sqrt(130.0 ** 2 - 80.0 ** 2) / 130.0, 0.78822, places=4)
        self.assertAlmostEqual(amount(0.71), self.curve(t), places=9)                                 # 0.788 >= 0.71: walkable ground
        self.assertAlmostEqual(amount(0.78), self.curve(t), places=9)
        self.assertEqual(amount(0.79), 0.0)                                                           # 0.788 < 0.79: a wall (AI:1190)

    def test_a_trace_that_starts_inside_the_ground_counts(self):
        s, _ = self.fall(z=80.0, block=0.0)                                                           # the lowest sphere (z = 20) is closer to the ground than its radius
        self.assertEqual(s["InAirState.GroundPredictionAmount"], 1.0)                                 # time 0: the top of the curve (AI:1188: consider the ground valid)

    def test_no_prediction_when_the_fall_is_slow_or_nothing_is_below(self):
        s, _ = self.fall(velocity=(300.0, 0.0, -199.0))
        self.assertEqual(s["InAirState.GroundPredictionAmount"], 0.0)                                 # AI:1150: > -200
        s, _ = self.fall(velocity=(0.0, 0.0, -200.0), z=130.0)
        self.assertGreater(s["InAirState.GroundPredictionAmount"], 0.0)                               # exactly -200 is predicted (a 150 cm sweep reaches the ground)
        s, _ = self.fall(z=3000.0)
        self.assertEqual(s["InAirState.GroundPredictionAmount"], 0.0)                                 # the ground is out of reach
        s, _ = self.fall(ground=None)
        self.assertEqual(s["InAirState.GroundPredictionAmount"], 0.0)

    def test_the_block_curve_allows_less_prediction(self):
        full, _ = self.fall()
        half, _ = self.fall(block=0.5)
        self.assertAlmostEqual(half["InAirState.GroundPredictionAmount"], full["InAirState.GroundPredictionAmount"] * 0.5, places=9)
        blocked, _ = self.fall(block=1.0)
        self.assertEqual(blocked["InAirState.GroundPredictionAmount"], 0.0)
        barely, _ = self.fall(block=0.99999)
        self.assertEqual(barely["InAirState.GroundPredictionAmount"], 0.0)                            # an allowance under KINDA_SMALL_NUMBER is none (AI:1157)

    def test_the_sweep_length_scales_with_the_mesh(self):
        s, _ = self.fall(velocity=(0.0, 0.0, -1000.0), z=500.0)                                       # 539.5 cm of sweep, 410 to the ground... reaches
        self.assertGreater(s["InAirState.GroundPredictionAmount"], 0.0)
        s, _ = self.fall(velocity=(0.0, 0.0, -1000.0), z=700.0)                                       # 610 to the ground
        self.assertEqual(s["InAirState.GroundPredictionAmount"], 0.0)
        s, _ = self.fall(velocity=(0.0, 0.0, -1000.0), z=700.0, scale=1.2)                            # ... 647 cm of sweep for a mesh 1.2 times as big
        self.assertGreater(s["InAirState.GroundPredictionAmount"], 0.0)

    def test_the_prediction_offsets_the_pelvis_in_the_air(self):
        s, sim = self.fall(block=0.25)
        rig = sim.live()[0].GetControlRigInput()
        self.assertAlmostEqual(rig.PelvisOffsetAmount, s["InAirState.GroundPredictionAmount"], places=9)     # InAirAmount 1 * GroundPredictionAmount

    def test_the_grounded_character_does_not_sweep(self):
        sim = self.sim()
        sim.step(calls=["RefreshGrounded"], loc={"Velocity": (0.0, 0.0, -600.0)}, host={"world": {"ground_z": 0.0}}, location=(0.0, 0.0, 300.0))
        self.assertEqual(sim.last["InAirState.GroundPredictionAmount"], 1.0)                          # untouched: RefreshInAir was not asked (the state's default is 1)


class PlayedTransitionTests(ScenarioBase):
    def test_a_quick_stop_notify_plays_the_transition_in_the_same_frame(self):
        sim = self.sim()
        sim.step()
        sim.hosts[INSTANCE].calls.clear()
        sim.step(calls=["PlayQuickStopAnimation"], tags={"RotationMode": ROT["ViewDirection"]})
        self.assertEqual(sim.hosts[INSTANCE].calls, [("play_slot_animation", (SEQ["StandL"], "Transition", 0.1, 0.2, 1.75, 1, 0.0, 0.3, 0))])

    def test_a_transition_that_is_stopped_is_not_played_and_the_stop_waits_one_frame(self):
        sim = self.sim()
        sim.step()
        sim.hosts[INSTANCE].calls.clear()
        sim.step(calls=[("PlayTransitionRightAnimation", {"PlayRate": 2.0}), "StopTransitionAndTurnInPlaceAnimations"])
        self.assertEqual([c[0] for c in sim.hosts[INSTANCE].calls], ["stop_montages_with_slot"] * 3)
        self.assertEqual([c[1] for c in sim.hosts[INSTANCE].calls], [("Transition", -1.0), ("TurnInPlaceStanding", -1.0), ("TurnInPlaceCrouching", -1.0)])


class TurnInPlaceTests(ScenarioBase):
    VIEW = ROT["ViewDirection"]

    def turn(self, view_yaw, stance="Standing", frames=40, **kw):
        """Frames with the view ``view_yaw`` degrees off the actor (still view, transitions allowed) until a turn is played; the commands that played it."""
        sim = self.sim()
        for i in range(frames):
            sim.hosts[INSTANCE].calls.clear()
            s = sim.step(calls=["RefreshTurnInPlace"], view={"Rotation": (0.0, view_yaw, 0.0), "YawSpeed": 0.0}, curves={"AllowTransitions": 1.0},
                         tags={"RotationMode": self.VIEW, "Stance": tag("Stance", stance.capitalize())}, post=False, **kw)
            before = dict(s)
            s = sim.post()
            if sim.hosts[INSTANCE].calls:
                return i, before, s, list(sim.hosts[INSTANCE].calls)
        return None, None, None, []

    def table(self, stance, angle, side):
        return next(t for t in TURNS if t[0] == f"{stance}_{angle}_{side}")

    def test_the_delay_depends_on_the_angle_and_the_turn_is_played_after_it(self):
        frame, before, after, calls = self.turn(100.0)
        # the delay at 100 degrees: the test settings map (45 .. 180) to (0.1 .. 0.5) s: 0.263; it has to be exceeded (AI:1950), a frame is 1/60 s
        delay = 0.1 + 0.4 * (100.0 - 45.0) / 135.0
        self.assertEqual(frame, int(delay * 60.0))                                                    # the first frame whose sum of dt is above it: (frame + 1) / 60 > delay
        self.assertEqual(before["TurnInPlaceState.QueuedSlotName"], 1)                                # TurnInPlaceStanding, queued by the graph, played by the post update
        self.assertAlmostEqual(before["TurnInPlaceState.QueuedTurnYawAngle"], 100.0)
        self.assertEqual(after["TurnInPlaceState.QueuedSlotName"], 0)
        self.assertEqual(after["TurnInPlaceState.QueuedTurnYawAngle"], 0.0)

    def test_a_90_degree_turn_to_the_right(self):
        _, _, after, calls = self.turn(100.0)
        _, _, sequence, rate, scale, animated = self.table("standing", 90, "right")
        # PlaySlotAnimationAsDynamicMontage_WithBlendSettings: blend in and out 0.15, the settings' play rate, one loop, inertialization when it blends out
        self.assertEqual(calls, [("play_slot_animation", (sequence, "TurnInPlaceStanding", 0.15, 0.15, rate, 1, 0.0, 0.0, 1))])
        self.assertAlmostEqual(after["TurnInPlaceState.PlayRate"], rate * abs(100.0 / animated))       # scaled by the turn angle / the animated angle (AI:2038)

    def test_the_turn_to_the_left_and_the_180_degree_turns(self):
        for view_yaw, stance, angle, side in ((-100.0, "standing", 90, "left"), (-135.0, "standing", 180, "left"), (135.0, "standing", 180, "right"),
                                              (80.0, "crouching", 90, "right"), (-80.0, "crouching", 90, "left"), (-170.0, "crouching", 180, "left"),
                                              (170.0, "crouching", 180, "right")):
            _, _, after, calls = self.turn(view_yaw, stance)
            _, _, sequence, rate, scale, animated = self.table(stance, angle, side)
            slot = "TurnInPlaceStanding" if stance == "standing" else "TurnInPlaceCrouching"
            self.assertEqual(calls, [("play_slot_animation", (sequence, slot, 0.15, 0.15, rate, 1, 0.0, 0.0, 1))], (view_yaw, stance))
            self.assertAlmostEqual(after["TurnInPlaceState.PlayRate"], rate * abs(view_yaw / animated) if scale else rate, msg=str((view_yaw, stance)))

    def test_near_180_degrees_the_character_turns_counter_clockwise(self):
        # RemapAngleForCounterClockwiseRotation: from 175 degrees on, +176 is -184 (AI:1957)
        _, _, _, calls = self.turn(176.0)
        self.assertEqual(calls[0][1][0], self.table("standing", 180, "left")[2])
        _, _, _, calls = self.turn(174.0)
        self.assertEqual(calls[0][1][0], self.table("standing", 180, "right")[2])

    def test_the_180_threshold_is_130_degrees(self):
        _, _, _, calls = self.turn(129.0)
        self.assertEqual(calls[0][1][0], self.table("standing", 90, "right")[2])
        _, _, _, calls = self.turn(130.0)
        self.assertEqual(calls[0][1][0], self.table("standing", 180, "right")[2])

    def test_no_turn_inside_the_angle_threshold(self):
        frame, _, _, calls = self.turn(44.0, frames=80)
        self.assertEqual((frame, calls), (None, []))

    def test_a_turn_that_is_stopped_waits_for_the_next_frame(self):
        sim = self.sim()
        args = dict(view={"Rotation": (0.0, 100.0, 0.0), "YawSpeed": 0.0}, curves={"AllowTransitions": 1.0}, tags={"RotationMode": self.VIEW})
        for _ in range(15):
            sim.step(calls=["RefreshTurnInPlace"], **args)
        sim.hosts[INSTANCE].calls.clear()
        s = sim.step(calls=["RefreshTurnInPlace", "StopTransitionAndTurnInPlaceAnimations"], post=False, **args)
        self.assertEqual(s["TurnInPlaceState.QueuedSlotName"], 1)
        s = sim.post()
        self.assertEqual([c[0] for c in sim.hosts[INSTANCE].calls], ["stop_montages_with_slot"] * 3)
        self.assertEqual(s["TurnInPlaceState.QueuedSlotName"], 1)                                      # still queued: PlayQueuedTurnInPlaceAnimation returned (AI:2016)
        sim.hosts[INSTANCE].calls.clear()
        sim.step(calls=["RefreshTurnInPlace"], **args)
        self.assertEqual([c[0] for c in sim.hosts[INSTANCE].calls], ["play_slot_animation"])


class NotifyTests(ScenarioBase):
    def test_the_grounded_entry_mode_notify_sets_the_tag(self):
        sim = self.sim()
        sim.step()
        s = sim.step(events=[("notify", "AlsAnimNotify_SetGroundedEntryMode", {"GroundedEntryMode": "Als.GroundedEntryMode.FromRoll"})])
        self.assertEqual(s["GroundedEntryMode"], "Als.GroundedEntryMode.FromRoll")
        s = sim.step(events=[("notify", "AlsAnimNotify_SetGroundedEntryMode", {})])                    # a notify without the property: the empty tag
        self.assertEqual(s["GroundedEntryMode"], "")

    def test_the_notify_survives_until_the_graph_resets_it(self):
        sim = self.sim()
        sim.step()
        sim.step(events=[("notify", "AlsAnimNotify_SetGroundedEntryMode", {"GroundedEntryMode": "Als.GroundedEntryMode.FromRoll"})])
        self.assertEqual(sim.step()["GroundedEntryMode"], "Als.GroundedEntryMode.FromRoll")
        self.assertEqual(sim.step(calls=["ResetGroundedEntryMode"])["GroundedEntryMode"], "")

    def test_only_the_notify_of_the_instance_is_declared_here(self):
        # SetLocomotionAction, SetRootMotionScale and EarlyBlendOut act on the character: AlsCharacter.dal declares them (a class once per module)
        notifies = {n.name: n for n in self.mods[INSTANCE].notifies}
        self.assertEqual(sorted(notifies), ["AlsAnimNotify_SetGroundedEntryMode"])
        self.assertEqual([f.name for f in notifies["AlsAnimNotify_SetGroundedEntryMode"].struct.fields], ["GroundedEntryMode"])
        self.assertFalse(notifies["AlsAnimNotify_SetGroundedEntryMode"].branching)


class TimeDilationAndSnapshotTests(ScenarioBase):
    def test_the_delta_time_without_time_dilation(self):
        sim = self.sim()
        s = sim.step(dt=0.01, host={"time_dilation": 0.5, "actor_time_dilation": 0.5, "real_delta_time": 0.02})
        self.assertAlmostEqual(s["DeltaTimeWithoutTimeDilation"], 0.02)                                # the previous global dilation (1) * the actor's (0.5): 0.01 / 0.5
        self.assertEqual(s["PreviousGlobalTimeDilation"], 0.5)                                        # ... kept for the next frame (AI:116)
        s = sim.step(dt=0.01, host={"time_dilation": 0.5, "actor_time_dilation": 0.5, "real_delta_time": 0.02})
        self.assertAlmostEqual(s["DeltaTimeWithoutTimeDilation"], 0.04)                                # 0.5 * 0.5 now: 0.01 / 0.25
        s = sim.step(dt=0.01, host={"time_dilation": 1.0, "actor_time_dilation": 0.0, "real_delta_time": 0.031})
        self.assertAlmostEqual(s["DeltaTimeWithoutTimeDilation"], 0.031)                               # a dilation of 0: the real delta time
        self.assertEqual(s["PreviousGlobalTimeDilation"], 1.0)

    def test_the_first_person_head_uses_the_time_without_dilation(self):
        def head(actor_dilation):
            sim = self.sim()
            first = {"ViewMode": tag("ViewMode", "FirstPerson"), "RotationMode": ROT["ViewDirection"]}
            sim.step(calls=["InitializeHead", "RefreshHead"], tags=first, view={"Rotation": (0.0, 0.0, 0.0)})
            return sim.step(calls=["RefreshHead"], tags=first, view={"Rotation": (30.0, 0.0, 0.0)}, host={"actor_time_dilation": actor_dilation})["HeadState.PitchAngle"]

        # the pitch follows the view with a half life of 0.01 s measured in the time the dilation does not touch: the second frame (the dilation of the frame
        # before is 1) divides the frame time by 1.0 * 0.5 = 0.5, which makes the step twice as long
        dilated, plain = head(0.5), head(1.0)
        self.assertGreater(dilated, plain)

    def test_snapshot_of_the_final_ragdoll_pose(self):
        sim = self.sim()
        sim.step()
        inst, _ = sim.live()
        self.assertIsNone(sim.last["RagdollingState.FinalRagdollPose"])
        sim.hosts[INSTANCE].calls.clear()
        inst.SnapshotFinalRagdollPose()                                                              # the character calls it when the ragdoll stops
        s = sim.step()
        self.assertEqual(sim.hosts[INSTANCE].calls, [("snapshot_pose", ("RagdollingState.FinalRagdollPose",))])
        self.assertIsNotNone(s["RagdollingState.FinalRagdollPose"])                                   # valid from now on: the graph blends out of it
        sim.hosts[INSTANCE].calls.clear()
        sim.step()
        self.assertEqual(sim.hosts[INSTANCE].calls, [])                                               # one command per call

    def test_the_teleport_distance_is_the_one_of_the_characters_settings(self):
        for threshold, moved, teleported in ((50.0, 49.0, False), (50.0, 51.0, True), (200.0, 120.0, False), (10.0, 11.0, True)):
            params = system_params(False, threshold)
            sim = Sim(self.mods, params=params)
            sim.step(location=(0.0, 0.0, 100.0))
            s = sim.step(location=(moved, 0.0, 100.0))
            self.assertEqual(s["TeleportedTime"] > 0.0, teleported, (threshold, moved))

    def test_the_scale_is_the_one_of_the_mesh(self):
        sim = self.sim()
        s = sim.step(scale=1.5, host={"actor_scale": 3.0})                                            # the capsule's scale is not what AI:784 reads
        self.assertEqual(s["LocomotionState.ScaleWorldSpace"], 1.5)


from dal.runtime import Vec3  # noqa: E402  (the foot state is set by hand in one test)



PACK = os.environ.get("DAL_ALS_PACK", r"G:\DCC\AlsPack")
HAVE_PACK = os.path.isfile(os.path.join(PACK, "data", "AIS_Als_Default.json"))


@unittest.skipUnless(HAVE_PACK, "the exported ALS pack (G:/DCC/AlsPack) is not on this machine")
class RealPackTests(ScenarioBase):
    """The instance with the data the project ships: AIS_Als_Default and its curves, exported by W1."""

    def sim_with_the_pack(self):
        from dal import settings as dsettings
        params = {INSTANCE: {"Settings": os.path.join(PACK, "data", "AIS_Als_Default.json"), "resolver": dsettings.PackResolver(PACK)}}
        return self.sim(params=params)

    def test_stride_and_play_rate_follow_the_exported_curves(self):
        sim = self.sim_with_the_pack()
        # CF_Als_StrideBlendAmount_Walk: 0.2 at speed 0, 1.0 from speed 150 on; the run curve reaches 1.0 at 350
        s = sim.step(calls=["RefreshStandingMovement"], loc={"Velocity": (150.0, 0.0, 0.0)}, curves={"PoseGait": 1.0, "PoseGrounded": 1.0})
        self.assertAlmostEqual(s["StandingState.StrideBlendAmount"], 1.0, places=5)
        self.assertAlmostEqual(s["StandingState.PlayRate"], 1.0, places=5)                       # 150 cm/s is the animated walk speed
        s = sim.step(calls=["RefreshStandingMovement"], loc={"Velocity": (350.0, 0.0, 0.0)}, tags={"Gait": GAIT["Running"]}, curves={"PoseGait": 2.0, "PoseGrounded": 1.0})
        self.assertAlmostEqual(s["StandingState.StrideBlendAmount"], 1.0, places=5)
        self.assertAlmostEqual(s["StandingState.PlayRate"], 1.0, places=5)                       # 350 cm/s is the animated run speed
        s = sim.step(calls=["RefreshStandingMovement"], loc={"Velocity": (0.0, 0.0, 0.0)}, curves={"PoseGait": 1.0, "PoseGrounded": 1.0})
        self.assertAlmostEqual(s["StandingState.StrideBlendAmount"], 0.2, places=5)
        self.assertAlmostEqual(s["StandingState.PlayRate"], 1e-4)                                # at rest: the smallest rate, never exactly 0
        inst, _ = sim.live()
        curve = inst.Settings.Crouching.StrideBlendAmountCurve                                   # the crouch walk reuses the walk curve
        s = sim.step(calls=["RefreshCrouchingMovement"], loc={"Velocity": (75.0, 0.0, 0.0)})
        self.assertAlmostEqual(s["CrouchingState.StrideBlendAmount"], curve.eval(75.0), places=9)
        self.assertGreater(curve.eval(75.0), 0.2)
        self.assertAlmostEqual(s["CrouchingState.PlayRate"], 75.0 / (150.0 * curve.eval(75.0)), places=9)

    def test_yaw_offsets_and_in_air_lean_are_the_exported_curves(self):
        sim = self.sim_with_the_pack()
        s = sim.step(calls=["RefreshGroundedMovement"], view={"Rotation": (0.0, 0.0, 0.0)}, loc={"VelocityYawAngle": 70.0})
        inst, _ = sim.live()
        g = inst.Settings.Grounded
        for state, c in (("ForwardAngle", g.RotationYawOffsetForwardCurve), ("BackwardAngle", g.RotationYawOffsetBackwardCurve),
                         ("LeftAngle", g.RotationYawOffsetLeftCurve), ("RightAngle", g.RotationYawOffsetRightCurve)):
            self.assertAlmostEqual(s["GroundedState.RotationYawOffsets." + state], c.eval(70.0), places=9)
        self.assertAlmostEqual(s["GroundedState.RotationYawOffsets.ForwardAngle"], 40.0, places=6)    # 70 degrees to the side: the forward curve turns the pelvis 40
        self.assertAlmostEqual(s["GroundedState.RotationYawOffsets.LeftAngle"], -20.0, places=6)
        s = sim.step(calls=["RefreshInAir"], loc={"Velocity": (350.0, 0.0, -300.0)}, tags={"LocomotionMode": tag("LocomotionMode", "InAir")})
        self.assertEqual(s["InAirState.VerticalVelocityWorldSpace"], -300.0)
        lean = inst.Settings.InAir.LeanAmountCurve.eval(-300.0)
        self.assertNotEqual(lean, 0.0)
        self.assertAlmostEqual(s["LeanState.ForwardAmount"], alpha(1 / 60, 0.2) * lean, places=6)            # (350 / 350) * curve(-300), smoothed from 0


if __name__ == "__main__":
    unittest.main()
