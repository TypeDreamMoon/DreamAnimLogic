"""The actions of the ALS character in DAL (``als/AlsCharacter.dal``, ports of ``AlsCharacter_Actions.cpp`` and the root motion source of the
mantling): rolling, mantling, getting up and ragdolling, and the notify states they end on.

* behaviour against the C++ they port: the start conditions and the commands each action hands to the host, the state they keep;
* the mantling against a scene (``als_world.BoxWorld``: a wall, a ledge, a ceiling) and a movement component that obeys the commands
  (``als_world.MoveSim``), with synthetic montages whose root motion is a straight line;
* the ragdoll against the host commands of the ragdoll service (``ragdoll_*``) and the pelvis bone the host serves;
* the interpreter and the generated Python agree on every scenario; the generated C++ has the notify classes of the actions and compiles
  with the rest of the system (``test_als_character.py`` runs it against the stand-in engine on random frames, hooks and notifies included).

The truth takes of the UE recorder (``G:/DCC/AlsTruth``) are compared in ``test_als_truth.py``.
"""

from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401

import als_world as W
import test_als_character as base
from dal import runtime as rt

DT = base.DT
ROLLING = "Als.LocomotionAction.Rolling"
MANTLING = "Als.LocomotionAction.Mantling"
RAGDOLLING = "Als.LocomotionAction.Ragdolling"
GETTING_UP = "Als.LocomotionAction.GettingUp"
NOTIFY_ACTION, NOTIFY_SCALE, NOTIFY_EARLY = base.NOTIFY_ACTION, base.NOTIFY_SCALE, base.NOTIFY_EARLY

ROLL_SETTINGS = {"Montage": "AM_Roll", "bCrouchOnStart": True, "bRotateToInputOnStart": True, "RotationInterpolationHalfLife": 0.1,
                 "bStartRollingOnLand": True, "RollingOnLandSpeedThreshold": 700.0, "bInterruptRollingWhenInAir": True}
RAGDOLL_SETTINGS = {"bStartRagdollingOnLand": True, "RagdollingOnLandSpeedThreshold": 1000.0, "bLimitInitialRagdollSpeed": True,
                    "GetUpFrontMontage": "AM_GetUp_Front", "GetUpBackMontage": "AM_GetUp_Back"}
MANTLING_SETTINGS = {"bAllowMantling": True, "bAutoStartMantlingInAir": True, "TraceAngleThreshold": 110.0, "MaxReachAngle": 50.0,
                     "SlopeAngleThreshold": 35.0, "TargetPrimitiveSpeedThreshold": 10.0, "MantlingHighHeightThreshold": 125.0,
                     "GroundedTrace": {"LedgeHeight": [50.0, 225.0], "ReachDistance": 75.0, "TargetLocationOffset": 15.0, "StartLocationOffset": 55.0},
                     "InAirTrace": {"LedgeHeight": [50.0, 150.0], "ReachDistance": 70.0, "TargetLocationOffset": 15.0, "StartLocationOffset": 55.0},
                     "MantlingTraceChannel": "ECC_Visibility", "BlendOutDuration": 0.3, "bStartRagdollingOnTargetPrimitiveDestruction": True}


def character_settings(**over):
    d = dict(base.CHARACTER_SETTINGS, Rolling=dict(ROLL_SETTINGS), Ragdolling=dict(RAGDOLL_SETTINGS), Mantling=dict(MANTLING_SETTINGS))
    d.update(over)
    return d


def mantling_asset(montage, **kw):
    d = {"Montage": montage, "bAutoCalculateStartTime": False, "StartTimeReferenceHeight": [125.0, 200.0], "StartTime": [0.6, 0.0],
         "MotionWarpingTimeRange": {"Min": 0.3, "Max": 0.6}, "MotionWarpingLocationBlendOption": "Linear", "MotionWarpingRotationBlendOption": "HermiteCubic"}
    d.update(kw)
    return d


def mantling_params():
    return {"MantlingHigh": mantling_asset("AM_High"), "MantlingHighInAir": mantling_asset("AM_High", StartTime=[0.8, 0.0]),
            "MantlingLow": mantling_asset("AM_Low", StartTimeReferenceHeight=[50.0, 100.0], StartTime=[0.5, 0.0]),
            "MantlingLowBox": mantling_asset("AM_LowBox", StartTimeReferenceHeight=[50.0, 100.0], StartTime=[0.5, 0.0])}


# a montage whose root moves in a straight line: component space, so +Y is forward for a mesh turned -90 degrees (UE's mannequin) and +Z up
def montage_asset(length=2.0, forward=110.0, up=110.0, rate_scale=1.0, **kw):
    d = {"length": length, "rate_scale": rate_scale, "sampling_rate": 30.0, "blend_in_time": 0.2, "blend_in_option": 0, "root_velocity": (0.0, forward, up)}
    d.update(kw)
    return d


MONTAGES = {"AM_High": montage_asset(), "AM_Low": montage_asset(length=1.2, forward=70.0, up=50.0), "AM_LowBox": montage_asset(length=1.2, forward=70.0, up=50.0)}
MESH = ((0.0, 0.0, 2.2), W.yaw_quat(-90.0), (1.0, 1.0, 1.0))             # the mesh component of the mannequin: turned by -90 degrees, at the feet
STAND = (0.0, 0.0, 92.2)                                                  # the capsule on the ground (half height 90 + the floor distance)


def velocity(speed, yaw=0.0):
    return base.velocity(speed, yaw)


def accel(yaw, amount=2000.0):
    return base.accel(yaw, amount)


def wall(height=130.0, front=100.0, **kw):
    return W.Box("Wall", (front, -200.0, 0.0), (front + 200.0, 200.0, height), **kw)


def rig_with(backend, boxes=(), settings=None, montages=None, mantling=True, **host):
    host.setdefault("actor_location", STAND)
    host.setdefault("mesh_world_transform", MESH)
    rig = base.Rig(backend, settings=settings or character_settings(), character_params=mantling_params() if mantling else None,
                   host_class=W.BoxWorld, **host)
    for h in rig.hosts.values():
        h.boxes.extend(boxes)
    rig.host["montage_assets"] = dict(MONTAGES if montages is None else montages)
    return rig


def rigs(**kw):
    for backend in base.BACKENDS:
        yield rig_with(backend, **kw)


class Case(base.Case):
    pass


def notify(kind, name, payload=None, instance=1):
    return (kind, name, dict(payload or {}), instance)


# ---- rolling ------------------------------------------------------------------------------------------------------------------------------------

class RollingTests(Case):
    """StartRollingGrounded, StartRolling, IsRollingAllowedToStart, RefreshRolling (CA:22-139)."""

    def test_the_roll_key_plays_the_montage_and_the_character_rolls_towards_its_input(self):
        for rig in rigs():
            rig.step(velocity=velocity(300.0, 30.0), acceleration=accel(30.0))
            rig.step(events=[("Roll", "triggered", True)], velocity=velocity(300.0, 30.0), acceleration=accel(30.0))
            self.assertEqual(rig.command("montage_play"), ("AM_Roll", 1.3, None))                  # PlayRate 1.3 of the example input (EX:199)
            self.assertEqual(rig.s["LocomotionAction"], ROLLING)
            self.assertClose(rig.s["RollingState.TargetYawAngle"], 30.0)                          # the input yaw (bRotateToInputOnStart), CA:38-41
            self.assertIn("crouch", rig.names())                                                  # bCrouchOnStart: the stance of the action (C:821-824)
            self.assertClose(rig.command("set_actor_rotation")[0][1], 0.0)                        # SetRotationInstant(InitialYawAngle) turns nothing yet (CA:97)

    def test_without_input_or_without_the_setting_the_roll_goes_where_the_actor_looks(self):
        look = {"actor_rotation": W.yaw_quat(90.0), "control_rotation": (0.0, 90.0, 0.0)}                      # looking where the view looks: nothing turns the actor
        for rig in rigs(**look):
            rig.step()
            rig.step(events=[("Roll", "triggered", True)])
            self.assertClose(rig.s["RollingState.TargetYawAngle"], 90.0)
        settings = character_settings(Rolling=dict(ROLL_SETTINGS, bRotateToInputOnStart=False))
        for rig in rigs(settings=settings, **look):
            rig.step(velocity=velocity(300.0, 0.0), acceleration=accel(0.0))
            rig.step(events=[("Roll", "triggered", True)], velocity=velocity(300.0, 0.0), acceleration=accel(0.0))
            self.assertClose(rig.s["RollingState.TargetYawAngle"], 90.0)

    def test_the_roll_needs_the_ground_a_montage_and_no_other_action(self):
        for rig in rigs():
            rig.step(movement_mode=base.FALLING_MODE, is_grounded=False, velocity=(0.0, 0.0, -100.0))
            rig.step(events=[("Roll", "triggered", True)])
            self.assertEqual((rig.commands("montage_play"), rig.s["LocomotionAction"]), ([], ""))      # CA:36: StartRollingGrounded is for the ground
        for rig in rigs(settings=character_settings(Rolling={})):
            rig.step(events=[("Roll", "triggered", True)])
            self.assertEqual((rig.commands("montage_play"), rig.s["LocomotionAction"]), ([], ""))      # no montage: ALS_ENSURE(IsValid(Montage)), CA:54
        for rig in rigs():
            rig.call("SetLocomotionAction", MANTLING)
            rig.step(events=[("Roll", "triggered", True)])
            self.assertEqual(rig.commands("montage_play"), [])                                        # CA:29: only without an action ...
            self.assertEqual(rig.s["LocomotionAction"], MANTLING)

    def test_a_roll_may_be_started_again_when_its_montage_is_not_playing_any_more(self):
        for rig in rigs():
            rig.step(events=[("Roll", "triggered", True)])
            self.assertEqual(len(rig.commands("montage_play")), 1)
            rig.step(events=[("Roll", "triggered", True)], montages={"AM_Roll": {"playing": True}})
            self.assertEqual(rig.commands("montage_play"), [])                                        # still playing: CA:30-31
            rig.step(events=[("Roll", "triggered", True)], montages={"AM_Roll": {"playing": False}})
            self.assertEqual(len(rig.commands("montage_play")), 1)                                    # the montage is over, the action is not (yet): CA:30-31

    def test_the_set_locomotion_action_notify_state_ends_the_roll(self):
        for rig in rigs():
            rig.step(events=[("Roll", "triggered", True)])
            rig.step(hooks=[notify("notify_begin", NOTIFY_ACTION, {"LocomotionAction": ROLLING})])
            self.assertEqual(rig.s["LocomotionAction"], ROLLING)
            rig.step(hooks=[notify("notify_end", NOTIFY_ACTION, {"LocomotionAction": MANTLING})])
            self.assertEqual(rig.s["LocomotionAction"], ROLLING)                                      # the end of another action's notify state is not ours (Notifies/..:44)
            rig.step(hooks=[notify("notify_end", NOTIFY_ACTION, {"LocomotionAction": ROLLING})])
            self.assertEqual(rig.s["LocomotionAction"], "")
            self.assertEqual(rig.command("set_input_blocked"), (False,))                              # C:1095
            self.assertIn("uncrouch", rig.names())                                                    # the roll's crouch is given back (C:805-813)

    def test_the_actor_turns_to_the_target_yaw_while_the_roll_goes_on(self):
        settings = character_settings(Rolling=dict(ROLL_SETTINGS, RotationInterpolationHalfLife=0.1))
        for rig in rigs(settings=settings):
            rig.step(velocity=velocity(300.0, 90.0), acceleration=accel(90.0))
            rig.step(events=[("Roll", "triggered", True)], velocity=velocity(300.0, 90.0), acceleration=accel(90.0))
            self.assertClose(rig.s["RollingState.TargetYawAngle"], 90.0)
            yaw = 0.0
            for _ in range(5):
                rig.step(hooks=[("physics_rotation", DT)], actor_rotation=W.yaw_quat(yaw))               # PhysicsRotation -> RefreshRollingPhysics (C:1509-1512)
                (rotation, teleport), = rig.commands("set_actor_rotation")
                self.assertFalse(teleport)                                                            # MoveUpdatedComponent(Zero, Rotation, false)  CA:137
                self.assertClose(rotation[1], base.damper_angle(yaw, 90.0, DT, 0.1))                   # DamperExactAngle(.., TargetYawAngle, dt, half life)  CA:133-135
                yaw = rotation[1]
            self.assertGreater(yaw, 1.0)

    def test_without_a_half_life_the_actor_is_turned_at_once_by_a_teleport(self):
        settings = character_settings(Rolling=dict(ROLL_SETTINGS, RotationInterpolationHalfLife=0.0))
        for rig in rigs(settings=settings):
            rig.step(velocity=velocity(300.0, 90.0), acceleration=accel(90.0))
            rig.step(events=[("Roll", "triggered", True)], velocity=velocity(300.0, 90.0), acceleration=accel(90.0))
            rig.step(hooks=[("physics_rotation", DT)])
            (rotation, teleport), = rig.commands("set_actor_rotation")
            self.assertTrue(teleport)                                                                 # ETeleportType::TeleportPhysics  CA:129
            self.assertClose(rotation[1], 90.0)

    def test_the_rotation_is_done_in_the_tick_when_the_root_motion_of_the_animation_is_ignored(self):
        for rig in rigs():
            rig.step(velocity=velocity(300.0, 90.0), acceleration=accel(90.0))
            rig.step(events=[("Roll", "triggered", True)], velocity=velocity(300.0, 90.0), acceleration=accel(90.0))
            rig.step(root_motion_mode=3)                                                              # RootMotionFromMontagesOnly: the physics rotation hook does it
            self.assertEqual(rig.commands("set_actor_rotation"), [])
            rig.step(root_motion_mode=1)                                                              # IgnoreRootMotion: PhysicsRotation is not called, the Tick does  CA:105-111
            self.assertEqual(len(rig.commands("set_actor_rotation")), 1)

    def test_nothing_rotates_the_roll_when_no_roll_is_running(self):
        for rig in rigs():
            rig.step(hooks=[("physics_rotation", DT)])
            self.assertEqual(rig.commands("set_actor_rotation"), [])                                  # CA:118-121
            rig.step(root_motion_mode=1)
            self.assertEqual(rig.commands("set_actor_rotation"), [])

    def test_a_hard_landing_rolls_in_the_direction_of_the_velocity(self):
        for rig in rigs():
            rig.step(movement_mode=base.FALLING_MODE, is_grounded=False, velocity=(0.0, 300.0, -800.0))
            rig.step(movement_mode=base.WALKING_MODE, is_grounded=True, velocity=(0.0, 300.0, -800.0))
            self.assertEqual(rig.s["LocomotionAction"], ROLLING)                                      # C:520-525: below the ragdoll speed (1000), above the roll speed (700)
            self.assertEqual(rig.command("montage_play"), ("AM_Roll", 1.3, None))
            self.assertClose(rig.s["RollingState.TargetYawAngle"], 90.0)                              # VelocityYawAngle (the velocity has a horizontal part)
            self.assertEqual(rig.commands("set_braking_friction_factor"), [])                         # and not the friction of a soft landing

    def test_rolling_in_the_air_ends_in_a_ragdoll_when_the_settings_say_so(self):
        for rig in rigs(ragdoll_supported=True):
            rig.step(events=[("Roll", "triggered", True)])
            rig.step(movement_mode=base.FALLING_MODE, is_grounded=False, velocity=(0.0, 0.0, -100.0))
            self.assertEqual(rig.s["LocomotionAction"], RAGDOLLING)                                   # C:552-558
        settings = character_settings(Rolling=dict(ROLL_SETTINGS, bInterruptRollingWhenInAir=False))
        for rig in rigs(settings=settings, ragdoll_supported=True):
            rig.step(events=[("Roll", "triggered", True)])
            rig.step(movement_mode=base.FALLING_MODE, is_grounded=False, velocity=(0.0, 0.0, -100.0))
            self.assertEqual(rig.s["LocomotionAction"], ROLLING)


class NotifyStateTests(Case):
    """The notify states of the actions that are not the end of an action: SetRootMotionScale, EarlyBlendOut (Notifies/*)."""

    def test_the_root_motion_scale_is_set_for_the_length_of_the_notify_state(self):
        for rig in rigs():
            rig.step(hooks=[notify("notify_begin", NOTIFY_SCALE, {"TranslationScale": 0.5})])
            self.assertEqual(rig.command("set_anim_root_motion_translation_scale"), (0.5,))
            rig.step(hooks=[notify("notify_end", NOTIFY_SCALE, {"TranslationScale": 0.5})])
            self.assertEqual(rig.command("set_anim_root_motion_translation_scale"), (1.0,))
            rig.step(hooks=[notify("notify_begin", NOTIFY_SCALE, {"TranslationScale": 0.5})])
            rig.step(hooks=[notify("notify_end", NOTIFY_SCALE, {"TranslationScale": 2.0})])           # something else changed the scale: it stays (NotifyEnd:52-58)
            self.assertEqual(rig.commands("set_anim_root_motion_translation_scale"), [])
            rig.step(hooks=[notify("notify_end", NOTIFY_SCALE, {"TranslationScale": 0.5})])
            self.assertEqual(rig.command("set_anim_root_motion_translation_scale"), (1.0,))

    def early(self, **payload):
        p = {"BlendOutDuration": 0.4, "bCheckInput": True, "bCheckLocomotionMode": True, "bCheckRotationMode": True, "bCheckStance": True}
        p.update(payload)
        return notify("notify_tick", NOTIFY_EARLY, p, instance=7)

    def test_the_montage_blends_out_early_when_one_of_the_checked_things_is_so(self):
        for rig in rigs():
            rig.step()
            rig.step(hooks=[self.early()])
            self.assertEqual(rig.commands("montage_stop_source"), [])                                 # nothing is so: no input, grounded, view direction, standing
            rig.step(acceleration=accel(0.0))                                                         # (the notify state ticks while the animation is evaluated: after the Tick)
            rig.step(hooks=[self.early()], acceleration=accel(0.0))
            self.assertEqual(rig.command("montage_stop_source"), (7, 0.4))                            # input (the montage instance of the notify, the blend out time)
            rig.step(hooks=[self.early(bCheckInput=False, bCheckStance=False)], acceleration=accel(0.0))
            self.assertEqual(rig.commands("montage_stop_source"), [])                                 # the check is switched off
            rig.step(acceleration=(0.0, 0.0, 0.0), movement_mode=base.FALLING_MODE, is_grounded=False)
            rig.step(hooks=[self.early()])
            self.assertEqual(rig.command("montage_stop_source"), (7, 0.4))                            # LocomotionModeEquals = InAir
            rig.step(movement_mode=base.WALKING_MODE, is_grounded=True, is_crouching=True)
            rig.step(hooks=[self.early()])
            self.assertEqual(rig.command("montage_stop_source"), (7, 0.4))                            # StanceEquals = Crouching
            rig.step(hooks=[self.early(bCheckStance=False)])
            self.assertEqual(rig.commands("montage_stop_source"), [])


# ---- mantling -------------------------------------------------------------------------------------------------------------------------------------

def tick(rig, sim, events=(), hooks=(), **host):
    """One frame of the closed loop: the slots before the movement, the movement component (``MoveSim``), the slots after it."""
    done = [0]

    def between(r):
        for _n, _slot, cmds in r.blocks[done[0]:]:
            sim.apply(cmds)
        done[0] = len(r.blocks)
        sim.move(DT)
        raised, sim.hooks = list(sim.hooks), []
        return sim.host(), raised

    values = dict(sim.host())
    values.update(host)
    rig.step(events=events, hooks=hooks, between=between, auto_hooks=False, **values)
    for _n, _slot, cmds in rig.blocks[done[0]:]:
        sim.apply(cmds)
    return rig


def run_mantle(rig, sim, frames, jump_at=1, extra=None):
    """Press the jump key in frame ``jump_at`` and play ``frames`` frames; ``extra(i, rig, sim)`` may add hooks of the frame."""
    trace = []
    for i in range(frames):
        events = [("Jump", "triggered", True)] if i == jump_at else []
        hooks = list(extra(i, rig, sim)) if extra else []
        tick(rig, sim, events=events, hooks=hooks)
        trace.append((tuple(sim.location), sim.mode, rig.s["LocomotionAction"], list(rig.cmds["AlsCharacter"])))
    return trace


class MantlingTests(Case):
    """StartMantling and its traces, the selection of the settings, the start time, the root motion source, RefreshMantling and StopMantling
    (CA:141-713, RMS:37-128)."""

    def press_jump(self, rig, **host):
        sim = W.MoveSim(STAND)
        tick(rig, sim, **host)
        tick(rig, sim, events=[("Jump", "triggered", True)], **host)
        return sim

    def test_a_wall_the_character_can_climb_is_mantled_instead_of_jumped(self):
        for rig in rigs(boxes=[wall()]):
            self.press_jump(rig)
            self.assertEqual(rig.commands("jump"), [])
            self.assertEqual(rig.s["LocomotionAction"], MANTLING)
            self.assertEqual(rig.s["MantlingState.RootMotionSourceId"], 1)
            self.assertEqual(rig.commands("set_movement_mode"), [(6, 0)])                              # MOVE_Custom, CA:496
            self.assertEqual(rig.commands("set_movement_mode_locked"), [(True,)])                      # CA:497
            self.assertEqual(rig.command("montage_play"), ("AM_High", 1.0, None))                      # a 130 cm wall is above the high threshold of 125: CA:411-415
            names = rig.names()
            self.assertLess(names.index("set_movement_mode"), names.index("montage_play"))
            self.assertIn("root_motion_override", names)                                              # the first tick of the source runs in the same frame (before the movement)
            self.assertEqual(rig.s["LocomotionMode"], "")                                             # OnMovementModeChanged: not grounded, not in the air (C:485-488)
            self.assertTrue(rig.s["MantlingSource.bActive"])
            self.assertEqual(rig.s["MantlingSource.Kind"], 0)

    def test_the_height_of_the_wall_decides_the_kind_of_mantle(self):
        for rig in rigs(boxes=[wall(height=80.0)]):
            self.press_jump(rig)
            self.assertEqual(rig.command("montage_play"), ("AM_Low", 1.0, None))                       # 80 cm: low
            self.assertEqual(rig.s["MantlingSource.Kind"], 2)
        for rig in rigs(boxes=[wall(height=80.0)]):
            rig.call("SetOverlayMode", "Als.OverlayMode.Box")                                          # carrying a box
            self.press_jump(rig)
            self.assertEqual(rig.command("montage_play"), ("AM_LowBox", 1.0, None))
            self.assertEqual(rig.s["MantlingSource.Kind"], 3)

    def test_the_start_time_comes_from_the_height_or_from_a_search_of_the_montage(self):
        for rig in rigs(boxes=[wall()]):
            self.press_jump(rig)
            height = 129.7                                                                            # the top of the wall (130) + the floor distance (1.9) - the capsule bottom (2.2)
            want = 0.6 + (0.0 - 0.6) * (height - 125.0) / (200.0 - 125.0)                              # GetMappedRangeValueClamped((125, 200), (0.6, 0), height)  CA:587
            self.assertClose(rig.s["MantlingSource.MontageStartTime"], want, 1e-5)
            self.assertClose(rig.s["MantlingSource.Duration"], 2.0 - want, 1e-5)                       # (length - start) / rate scale  CA:510-513
        params = dict(mantling_params(), MantlingHigh=mantling_asset("AM_Auto", bAutoCalculateStartTime=True))
        montages = dict(MONTAGES, AM_Auto=montage_asset(length=2.0, forward=60.0, up=100.0))           # the root climbs 100 cm per second
        for backend in base.BACKENDS:
            rig = base.Rig(backend, settings=character_settings(), character_params=params, host_class=W.BoxWorld, actor_location=STAND, mesh_world_transform=MESH)
            for h in rig.hosts.values():
                h.boxes.append(wall())
            rig.host["montage_assets"] = montages
            self.press_jump(rig)
            start = rig.s["MantlingSource.MontageStartTime"]
            target_z = 200.0 - 129.7                                                                  # the root has climbed 200 cm at the end; 129.7 are left to do
            self.assertLessEqual(abs(start * 100.0 - target_z), 1.0)                                  # CA:612-630: within a centimetre of where the character already is

    def test_what_is_not_a_ledge_is_not_mantled(self):
        cases = {
            "too low to need it": [wall(height=40.0)],
            "out of reach": [wall(front=300.0)],
            "a platform that moves": [wall(speed=50.0)],
            "something the character cannot step up": [wall(can_step_up=False)],
            "no room for the capsule on top": [wall(), W.Box("Ceiling", (100.0, -200.0, 300.0), (300.0, 200.0, 400.0))],
        }
        for name, boxes in cases.items():
            for rig in rigs(boxes=boxes):
                self.press_jump(rig)
                self.assertEqual(rig.s["LocomotionAction"], "", name)
                self.assertEqual(rig.commands("jump"), [()], name)                                    # the key jumps instead
        off = character_settings(Mantling=dict(MANTLING_SETTINGS, bAllowMantling=False))
        for rig in rigs(boxes=[wall()], settings=off):
            self.press_jump(rig)
            self.assertEqual(rig.s["LocomotionAction"], "")

    def test_the_trace_follows_the_input_not_the_look_and_gives_up_behind_the_character(self):
        for rig in rigs(boxes=[wall()]):
            self.press_jump(rig, acceleration=accel(180.0), velocity=velocity(0.0))                    # the input points away from the wall: 180 degrees > TraceAngleThreshold
            self.assertEqual(rig.s["LocomotionAction"], "")
        for rig in rigs(boxes=[wall(front=90.0)]):
            self.press_jump(rig, acceleration=accel(20.0), velocity=velocity(300.0, 20.0))             # 20 degrees off, within MaxReachAngle: the wall is still found
            self.assertEqual(rig.s["LocomotionAction"], MANTLING)

    def test_nothing_is_mantled_during_another_action(self):
        for rig in rigs(boxes=[wall()]):
            rig.call("SetLocomotionAction", ROLLING)
            self.assertFalse(rig.call("StartMantling"))                                               # CA:162-165
        for rig in rigs(boxes=[wall()]):
            self.assertTrue(rig.call("StartMantling"))
            self.assertFalse(rig.call("StartMantling"))                                               # the first one is the action now

    def test_in_the_air_the_character_mantles_by_itself_with_the_in_air_settings(self):
        for rig in rigs(boxes=[wall()]):
            rig.step(movement_mode=base.FALLING_MODE, is_grounded=False, velocity=(300.0, 0.0, 50.0), actor_location=(0.0, 0.0, 150.0))
            self.assertEqual(rig.s["LocomotionAction"], MANTLING)                                     # AutoStartMantling: CA:156-160
            self.assertEqual(rig.command("montage_play"), ("AM_High", 1.0, None))
            self.assertEqual(rig.s["MantlingSource.Kind"], 1)                                         # MantlingType InAir -> MS_Als_High_InAir
        off = character_settings(Mantling=dict(MANTLING_SETTINGS, bAutoStartMantlingInAir=False))
        for rig in rigs(boxes=[wall()], settings=off):
            rig.step(movement_mode=base.FALLING_MODE, is_grounded=False, velocity=(300.0, 0.0, 50.0), actor_location=(0.0, 0.0, 150.0))
            self.assertEqual(rig.s["LocomotionAction"], "")

    def play(self, rig, frames=150, **kw):
        sim = W.MoveSim(STAND)
        trace = run_mantle(rig, sim, frames, **kw)
        return sim, trace

    def test_the_actor_is_moved_onto_the_ledge_by_the_root_motion_override(self):
        for rig in rigs(boxes=[wall()]):
            sim, trace = self.play(rig)
            self.assertEqual(sim.mode, W.MoveSim.CUSTOM)
            self.assertTrue(sim.locked)
            self.assertClose(sim.location, (115.0, 0.0, 221.9), 1e-5)                                 # TargetLocationOffset 15 from the wall, the top of the wall (130) + 1.9 + the half height
            self.assertClose(W.yaw_of(sim.rotation), 0.0, 1e-4)                                       # facing the wall
            self.assertEqual(sim.velocity, [0.0, 0.0, 0.0])                                           # the source has finished: no override any more, PhysCustom zeroes the velocity
            self.assertIsNone(sim.override)

    def test_the_montage_is_kept_in_step_with_the_source(self):
        for rig in rigs(boxes=[wall()]):
            sim = W.MoveSim(STAND)
            rig.step(**sim.host())
            tick(rig, sim, events=[("Jump", "triggered", True)])
            start = rig.s["MantlingSource.MontageStartTime"]
            self.assertClose(rig.command("montage_set_position")[1], start, 1e-6)                      # Max(0, MontageTime - DeltaTime), MontageTime = start + dt  RMS:49-54
            tick(rig, sim)
            self.assertClose(rig.command("montage_set_position")[1], start + DT, 1e-6)
            self.assertEqual(rig.command("montage_set_position")[0], "AM_High")

    def test_the_notify_state_at_the_end_of_the_montage_gives_the_character_back(self):
        for rig in rigs(boxes=[wall()]):
            sim = W.MoveSim(STAND)
            run_mantle(rig, sim, 40)
            self.assertEqual(rig.s["LocomotionAction"], MANTLING)
            end = notify("notify_end", NOTIFY_ACTION, {"LocomotionAction": MANTLING})
            tick(rig, sim, hooks=[end])
            names = rig.names()
            self.assertEqual(rig.s["LocomotionAction"], "")
            self.assertEqual(rig.s["MantlingState.RootMotionSourceId"], 0)                            # RefreshMantling -> StopMantling()  CA:653-657
            self.assertEqual(rig.commands("montage_stop"), [])                                        # (the montage ends by itself)
            self.assertEqual(rig.commands("set_movement_mode_locked"), [(False,)])                    # CA:707
            self.assertEqual(rig.commands("set_movement_mode"), [(1,)])                               # MOVE_Walking, CA:708
            self.assertIn("root_motion_clear", names)
            self.assertEqual(sim.mode, W.MoveSim.WALKING)
            self.assertEqual(rig.s["LocomotionMode"], base.GROUNDED)                                  # OnMovementModeChanged ran inside SetMovementMode: no wait for the hook

    def test_the_source_finishes_when_its_time_is_up_even_if_nobody_ends_the_action(self):
        for rig in rigs(boxes=[wall()]):
            sim = W.MoveSim(STAND)
            run_mantle(rig, sim, 150)
            self.assertFalse(rig.s["MantlingSource.bActive"])
            self.assertEqual(rig.s["LocomotionAction"], MANTLING)                                     # only the notify state ends the action
            self.assertIsNone(sim.override)

    def test_a_movement_mode_changed_from_outside_stops_the_mantle_and_its_montage(self):
        for rig in rigs(boxes=[wall()]):
            sim = W.MoveSim(STAND)
            run_mantle(rig, sim, 30)
            sim.locked = False
            sim.mode = W.MoveSim.FALLING                                                              # something else changed the mode
            sim.hooks = [("movement_mode_changed", W.MoveSim.CUSTOM, W.MoveSim.FALLING)]
            tick(rig, sim)
            self.assertEqual(rig.s["MantlingState.RootMotionSourceId"], 0)
            self.assertEqual(rig.command("montage_stop"), ("AM_High", 0.3))                           # Settings.Mantling.BlendOutDuration  CA:702
            self.assertEqual(rig.commands("set_movement_mode"), [(1,)])

    def test_when_the_target_is_destroyed_the_character_stops_and_ragdolls(self):
        for rig in rigs(boxes=[wall()], ragdoll_supported=True):
            sim = W.MoveSim(STAND)
            run_mantle(rig, sim, 30)
            for h in rig.hosts.values():
                h.gone.add("Wall")
            tick(rig, sim)
            self.assertEqual(rig.s["MantlingState.RootMotionSourceId"], 0)
            self.assertEqual(rig.s["LocomotionAction"], RAGDOLLING)                                   # CA:665-678
            self.assertEqual(len(rig.commands("ragdoll_begin")), 1)
        for rig in rigs(boxes=[wall()], ragdoll_supported=False):
            sim = W.MoveSim(STAND)
            run_mantle(rig, sim, 30)
            for h in rig.hosts.values():
                h.gone.add("Wall")
            tick(rig, sim)
            self.assertEqual(rig.s["MantlingState.RootMotionSourceId"], 0)
            self.assertNotEqual(rig.s["LocomotionAction"], RAGDOLLING)

    def test_the_two_python_implementations_play_the_same_mantle(self):
        out = {}
        for backend in base.BACKENDS:
            rig = rig_with(backend, boxes=[wall()])
            sim = W.MoveSim(STAND)
            out[backend] = run_mantle(rig, sim, 150)
        self.assertEqual(len(out["python"]), len(out["interp"]))
        for a, b in zip(out["python"], out["interp"]):
            self.assertClose(a[0], b[0], 1e-9)
            self.assertEqual(a[1:], b[1:])


# ---- ragdolling -----------------------------------------------------------------------------------------------------------------------------------

def pelvis(location, yaw=0.0, roll=0.0):
    """The pelvis bone the host serves (the pose evaluated last): ``bones=`` for ``Rig.step``."""
    q = rt.to_quat(rt.Rotator(0.0, yaw, roll))
    world = (tuple(location), (q.x, q.y, q.z, q.w), (1.0, 1.0, 1.0))
    return {"pelvis": {"world": world, "component": world}}


GROUND = {"ground_z": 0.0}


class RagdollTests(Case):
    """IsRagdollingAllowedToStart, StartRagdolling, RefreshRagdolling, RagdollTraceGround, StopRagdolling (CA:717-1170)."""

    def ragdolling(self, rig, velocity_now=(300.0, 0.0, 0.0), at=(0.0, 0.0, 92.2)):
        """The character runs and the ragdoll key is pressed."""
        rig.step(velocity=velocity_now, bones=pelvis(at), world=GROUND)
        rig.step(events=[("Ragdoll", "triggered", True)], velocity=velocity_now, bones=pelvis(at), world=GROUND)
        return rig

    def test_without_a_ragdoll_in_the_host_nothing_ragdolls(self):
        for rig in rigs(ragdoll_supported=False):
            rig.step()
            rig.step(events=[("Ragdoll", "triggered", True)])
            self.assertEqual((rig.s["LocomotionAction"], rig.commands("ragdoll_begin")), ("", []))     # CA:719-723: the physics asset needs the pelvis and spine_03 bodies
            rig.call("StartRagdolling")
            rig.step()
            self.assertEqual(rig.s["LocomotionAction"], "")

    def test_the_ragdoll_key_hands_the_character_to_the_ragdoll(self):
        for rig in rigs(ragdoll_supported=True):
            self.ragdolling(rig)
            self.assertEqual(rig.s["LocomotionAction"], RAGDOLLING)                                   # CA:841
            self.assertEqual(rig.commands("montage_stop_all"), [(0.2,)])                              # CA:777
            self.assertEqual(rig.commands("ragdoll_begin"), [(True, 300.0)])                          # bLimitInitialRagdollSpeed, Max(200, speed)  CA:811-821
            self.assertEqual(rig.commands("set_movement_mode"), [(0,)])                               # MOVE_None  CA:838
            self.assertEqual(rig.commands("set_movement_mode_locked"), [(True,)])                     # CA:839
            names = rig.names()
            self.assertLess(names.index("ragdoll_begin"), names.index("set_movement_mode"))
            self.assertEqual(rig.s["LocomotionMode"], "")                                             # the movement mode is none: no locomotion mode (C:485-488)
            self.assertEqual(rig.s["RagdollingState.SpeedLimit"], 300.0)
            self.assertEqual(rig.s["RagdollingState.PullForce"], 0.0)
            self.assertClose(rig.s["RagdollTargetLocation"], (0.0, 0.0, 92.2), 1e-6)                  # the pelvis (CA:833)

    def test_a_slow_character_still_gets_the_minimum_speed_limit(self):
        for rig in rigs(ragdoll_supported=True):
            self.ragdolling(rig, velocity_now=(50.0, 0.0, 0.0))
            self.assertEqual(rig.commands("ragdoll_begin"), [(True, 200.0)])                          # MinSpeedLimit = 200  CA:818

    def test_the_initial_speed_limit_holds_for_eight_ticks(self):
        for rig in rigs(ragdoll_supported=True):
            self.ragdolling(rig)
            sent = len(rig.commands("ragdoll_limit_speed"))                                           # the start frame's refresh is the first tick  CA:955-959
            self.assertEqual(rig.s["RagdollingState.SpeedLimitFrameTimeRemaining"], 7)
            for _ in range(10):
                rig.step(bones=pelvis((0.0, 0.0, 92.2)), world=GROUND)
                sent += len(rig.commands("ragdoll_limit_speed"))
                if rig.commands("ragdoll_limit_speed"):
                    self.assertEqual(rig.command("ragdoll_limit_speed"), (300.0,))
            self.assertEqual(sent, 8)
            self.assertEqual(rig.s["RagdollingState.SpeedLimitFrameTimeRemaining"], 0)
        settings = character_settings(Ragdolling=dict(RAGDOLL_SETTINGS, bLimitInitialRagdollSpeed=False))
        for rig in rigs(ragdoll_supported=True, settings=settings):
            self.ragdolling(rig)
            self.assertEqual(rig.commands("ragdoll_limit_speed"), [])
            self.assertEqual(rig.commands("ragdoll_begin")[0][0], False)

    def test_the_motor_strength_follows_the_speed_of_the_pelvis(self):
        for rig in rigs(ragdoll_supported=True):
            self.ragdolling(rig, velocity_now=(0.0, 0.0, 0.0))
            for k in range(1, 4):
                rig.step(bones=pelvis((10.0 * k, 0.0, 92.2)), world=GROUND)                           # 10 cm per tick: 600 cm/s
            self.assertClose(rig.s["RagdollingState.Velocity"], (600.0, 0.0, 0.0), 1e-4)              # [the velocity read of the pelvis body: the difference of its positions]
            self.assertClose(rig.command("ragdoll_set_motor_strength")[0], 0.6 * 25000.0, 1e-4)       # Clamp01(Speed / 1000) * Stiffness  CA:949-951
            rig.step(bones=pelvis((10.0 * 3 + 40.0, 0.0, 92.2)), world=GROUND)                        # 40 cm per tick: past the reference speed
            self.assertClose(rig.command("ragdoll_set_motor_strength")[0], 25000.0, 1e-6)

    def test_the_capsule_follows_the_pelvis_and_stands_on_the_ground_below_it(self):
        for rig in rigs(ragdoll_supported=True):
            self.ragdolling(rig, at=(0.0, 0.0, 92.2))
            rig.step(bones=pelvis((50.0, 20.0, 50.0)), world=GROUND)
            (location, sweep, teleport), = rig.commands("set_actor_location")
            self.assertEqual((sweep, teleport), (False, True))                                        # SetActorLocation(.., false, nullptr, TeleportPhysics)  CA:901
            # the sphere (radius 30) sweeps from Z + 60 to Z - 60 and meets the ground when its centre is 30 high: Z = 30 + 90 - 30 + 1.9
            self.assertClose(location, (50.0, 20.0, 91.9), 1e-6)                                      # CA:993-997
            rig.step(bones=pelvis((50.0, 20.0, 200.0)), world=GROUND)
            (location, _s, _t), = rig.commands("set_actor_location")
            self.assertClose(location, (50.0, 20.0, 200.0), 1e-6)                                     # nothing to stand on: the pelvis' height

    def stop(self, rig, facing_roll, yaw=30.0, at=(50.0, 20.0, 25.0), **host):
        self.ragdolling(rig)
        rig.step(bones=pelvis(at, yaw=yaw, roll=facing_roll), world=GROUND, **host)
        rig.step(events=[("Ragdoll", "triggered", True)], bones=pelvis(at, yaw=yaw, roll=facing_roll), world=GROUND, **host)
        return rig

    def test_the_ragdoll_key_stops_the_ragdoll_and_it_gets_up_facing_the_way_it_lay(self):
        for roll, upward, yaw, montage in ((-90.0, True, 30.0 - 180.0, "AM_GetUp_Back"), (90.0, False, 30.0, "AM_GetUp_Front"), (0.0, True, -150.0, "AM_GetUp_Back")):
            for rig in rigs(ragdoll_supported=True):
                self.stop(rig, roll)
                names = rig.names()
                self.assertEqual(rig.commands("ragdoll_end"), [()], roll)
                self.assertEqual(rig.anim.snapshot()["Snapshots"], 1)                                 # the animation instance takes the final pose (CA:1076)
                (rotation, teleport), = rig.commands("set_actor_rotation")
                self.assertTrue(teleport)
                self.assertClose(rotation[1], yaw, 1e-4)                                              # Yaw - 180 when it lay on its back  CA:1099-1102
                (location, _sweep, _teleport), = rig.commands("set_actor_location")
                self.assertClose(location, (50.0, 20.0, 91.9), 1e-6)                                  # the ground below the pelvis  CA:1094-1104
                self.assertEqual(rig.commands("set_movement_mode_locked"), [(False,)])                # CA:1143
                self.assertEqual(rig.commands("set_movement_mode"), [(1,)])                           # MOVE_Walking: on the ground  CA:1147
                self.assertEqual(rig.command("montage_play"), (montage, 1.0, None))                   # SelectGetUpMontage(bRagdollFacingUpward)  CA:1159, :1169
                self.assertEqual(rig.commands("set_input_blocked"), [(False,), (True,)])              # the empty action unblocks (C:1095), then CA:1161 blocks for the get up
                self.assertEqual(rig.s["LocomotionAction"], GETTING_UP)                               # CA:1163
                for first, second in (("ragdoll_end", "set_actor_location"), ("set_movement_mode_locked", "set_movement_mode"), ("set_movement_mode", "montage_play")):
                    self.assertLess(names.index(first), names.index(second), (roll, first, second))   # the order of the C++ calls
                self.assertEqual(rig.s["LocomotionMode"], base.GROUNDED)                              # (OnMovementModeChanged inside SetMovementMode)
                self.assertEqual(upward, roll <= 0.0)

    def test_the_notify_state_of_the_get_up_montage_gives_the_input_back(self):
        for rig in rigs(ragdoll_supported=True):
            self.stop(rig, 90.0)
            rig.step(hooks=[notify("notify_begin", NOTIFY_ACTION, {"LocomotionAction": GETTING_UP})])
            self.assertEqual(rig.s["LocomotionAction"], GETTING_UP)
            rig.step(hooks=[notify("notify_end", NOTIFY_ACTION, {"LocomotionAction": GETTING_UP})])
            self.assertEqual(rig.s["LocomotionAction"], "")
            self.assertEqual(rig.command("set_input_blocked"), (False,))                              # C:1095

    def test_a_ragdoll_that_stops_in_the_air_falls_with_its_velocity(self):
        for rig in rigs(ragdoll_supported=True):
            self.ragdolling(rig)
            rig.step(bones=pelvis((0.0, 0.0, 300.0)), world=GROUND)
            rig.step(bones=pelvis((6.0, 0.0, 294.0)), world=GROUND)
            velocity_now = rig.s["RagdollingState.Velocity"]
            rig.step(events=[("Ragdoll", "triggered", True)], bones=pelvis((6.0, 0.0, 294.0)), world=GROUND)
            self.assertEqual(rig.commands("set_movement_mode"), [(3,)])                               # MOVE_Falling  CA:1151
            self.assertEqual(rig.commands("set_velocity"), [(tuple(velocity_now),)])                  # CA:1152
            self.assertEqual(rig.commands("montage_play"), [])                                        # no get up in the air
            self.assertEqual(rig.s["LocomotionAction"], "")
            self.assertEqual(rig.s["LocomotionMode"], base.IN_AIR)

    def test_stopping_needs_a_ragdoll(self):
        for rig in rigs(ragdoll_supported=True):
            self.assertFalse(rig.call("StopRagdolling"))                                              # CA:1026-1036
            rig.step()
            self.assertEqual(rig.commands("ragdoll_end"), [])
            self.ragdolling(rig)
            self.assertTrue(rig.call("StopRagdolling"))

    def test_the_jump_key_stops_the_ragdoll_before_it_mantles_or_jumps(self):
        for rig in rigs(ragdoll_supported=True):
            self.ragdolling(rig)
            rig.step(events=[("Jump", "triggered", True)], bones=pelvis((50.0, 20.0, 25.0)), world=GROUND)      # EX:160
            self.assertEqual(rig.commands("ragdoll_end"), [()])
            self.assertEqual(rig.commands("jump"), [])

    def test_a_hard_landing_ragdolls_a_harder_one_than_it_rolls(self):
        for rig in rigs(ragdoll_supported=True):
            rig.step(movement_mode=base.FALLING_MODE, is_grounded=False, velocity=(0.0, 0.0, -1200.0), bones=pelvis(STAND))
            rig.step(movement_mode=base.WALKING_MODE, is_grounded=True, velocity=(0.0, 0.0, -1200.0), bones=pelvis(STAND))
            self.assertEqual(rig.s["LocomotionAction"], RAGDOLLING)                                   # C:512-516: before the roll
            self.assertEqual(len(rig.commands("ragdoll_begin")), 1)
            self.assertEqual(rig.commands("montage_play"), [])
        for rig in rigs(ragdoll_supported=False):
            rig.step(movement_mode=base.FALLING_MODE, is_grounded=False, velocity=(0.0, 0.0, -1200.0))
            rig.step(movement_mode=base.WALKING_MODE, is_grounded=True, velocity=(0.0, 0.0, -1200.0))
            self.assertEqual(rig.s["LocomotionAction"], "")                                           # no ragdoll in the host: nothing starts (the friction branch is skipped too)
            self.assertEqual(rig.commands("set_braking_friction_factor"), [])

    def test_the_two_python_implementations_play_the_same_fall(self):
        out = {}
        for backend in base.BACKENDS:
            rig = rig_with(backend, ragdoll_supported=True)
            trace = []
            self.ragdolling(rig)
            for k in range(1, 30):
                rig.step(bones=pelvis((7.0 * k, 0.0, max(25.0, 92.0 - 3.0 * k))), world=GROUND)
                trace.append((rig.s["RagdollingState.Velocity"], list(rig.cmds["AlsCharacter"])))
            rig.step(events=[("Ragdoll", "triggered", True)], bones=pelvis((200.0, 0.0, 25.0), yaw=70.0, roll=-80.0), world=GROUND)
            trace.append((rig.s["LocomotionAction"], list(rig.cmds["AlsCharacter"])))
            out[backend] = trace
        for a, b in zip(out["python"], out["interp"]):
            self.assertEqual(str(a), str(b))


if __name__ == "__main__":
    unittest.main()
