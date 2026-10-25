"""als/AlsAnimationInstance.dal (+ AlsLinkedAnimationInstance.dal): behaviour against ALS's semantics.

* the whole instance against the independent port ``oracles/als_anim_oracle.py`` (every state and every host command, every frame, random
  character / curve / pose / entry-call / notify sequences with the settings given as polyline curves), interpreter and generated Python;
* hand-checked scenarios of the parts that are easy to get wrong (test_als_anim_scenarios.py): curve-driven layering, lean, speed and stride,
  rotate in place, view, spine, head, in air and the ground prediction, foot lock, transitions, turn in place;
* three backends: interpreter vs generated Python on random frames of the whole system, generated C++ (MSVC /W4 /WX against the
  stand-in engine headers) vs Python.

The character is ``als_sources.STAND_IN_CHARACTER`` (see there); the real pair is compiled by test_als_contract.py when
``als/AlsCharacter.dal`` exists.
"""

import math
import random
import unittest

import _bootstrap  # noqa: F401

import als_sources as A
import test_cpp_compile as tcc
from dal import harness
from oracles.als_anim_oracle import AlsAnimOracle, q_mul, q_rotate, t_position

INSTANCE = A.INSTANCE
CHARACTER = A.CHARACTER

HIPS = ["Forward", "Backward", "LeftForward", "LeftBackward", "RightForward", "RightBackward"]

# ---- settings: polyline curves, so that the stand-in engine of the C++ test evaluates them too -------------------------------------

CURVES = {
    "yaw_forward": [(-180.0, 0.0), (-90.0, -12.0), (0.0, 0.0), (90.0, 12.0), (180.0, 0.0)],
    "yaw_backward": [(-180.0, 0.0), (-90.0, 6.0), (0.0, 0.0), (90.0, -6.0), (180.0, 0.0)],
    "yaw_left": [(-180.0, 20.0), (-60.0, 0.0), (0.0, -15.0), (60.0, -30.0), (180.0, 30.0)],
    "yaw_right": [(-180.0, -30.0), (-60.0, 30.0), (0.0, 15.0), (60.0, 0.0), (180.0, -20.0)],
    "stride_walk": [(0.0, 0.2), (58.3, 0.55), (150.0, 1.0)],
    "stride_run": [(0.0, 0.2), (166.9, 0.45), (350.0, 1.0)],
    "stride_crouch": [(0.0, 0.0), (150.0, 1.0)],          # zero at rest: exercises the zero stride of the C++ division
    "lean_air": [(-600.0, 1.0), (-200.0, 0.6), (0.0, 0.0), (200.0, -0.6), (600.0, -1.0)],
    "ground_prediction": [(0.0, 1.0), (0.4, 0.8), (1.0, 0.0)],
}


def curve_data(points):
    return {"class": "CurveFloat", "channels": [{"keys": [{"t": t, "v": v, "interp": "linear"} for t, v in points], "pre": "constant", "post": "constant"}]}


def curve_fn(points):
    def f(x):
        if x <= points[0][0]:
            return points[0][1]
        if x >= points[-1][0]:
            return points[-1][1]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if x0 <= x <= x1:
                return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
        raise AssertionError(x)
    return f


SEQ = {k: f"A_Als_{k}" for k in ("StandL", "StandR", "CrouchL", "CrouchR", "DynStandL", "DynStandR", "DynCrouchL", "DynCrouchR")}

# the eight turn in place settings: (key of the oracle, member of the settings, sequence, play rate, scale by the animated angle, animated turn angle)
TURNS = [(f"{stance}_{angle}_{side}", f"{stance.capitalize()}Turn{angle}{side.capitalize()}", f"A_Als_Turn_{stance}_{angle}_{side}", rate, scale, float(angle))
         for (stance, angle, side), rate, scale in zip(
             [(st, an, sd) for st in ("standing", "crouching") for an in (90, 180) for sd in ("left", "right")],
             (1.2, 1.0, 1.5, 0.9, 1.2, 1.1, 1.3, 1.0), (True, True, False, True, True, False, True, True))]
TURN_BLEND_DURATION = 0.15


def turn_settings_data():
    return {member: {"Sequence": seq, "PlayRate": rate, "bScalePlayRateByAnimatedTurnAngle": scale, "AnimatedTurnAngle": angle}
            for _, member, seq, rate, scale, angle in TURNS}


def settings_data(use_foot_ik=False):
    """AlsAnimationInstanceSettings, in the layout dal.settings loads (member names as in C++)."""
    return {
        "General": {"bUseHandIkBones": True, "bUseFootIkBones": use_foot_ik, "MovingSmoothSpeedThreshold": 150.0, "LeanInterpolationHalfLife": 0.2},
        "Head": {"PitchAngleInterpolationHalfLife": 0.1, "YawAngleInterpolationHalfLife": 0.1, "SwitchLookSidesYawAngleInterpolationHalfLife": 0.2,
                 "FirstPersonPitchAngleInterpolationHalfLife": 0.01, "FirstPersonYawAngleInterpolationHalfLife": 0.01},
        "Grounded": {"RotationYawOffsetForwardCurve": curve_data(CURVES["yaw_forward"]), "RotationYawOffsetBackwardCurve": curve_data(CURVES["yaw_backward"]),
                     "RotationYawOffsetLeftCurve": curve_data(CURVES["yaw_left"]), "RotationYawOffsetRightCurve": curve_data(CURVES["yaw_right"]),
                     "VelocityBlendInterpolationHalfLife": 0.1},
        "Standing": {"AnimatedWalkSpeed": 150.0, "AnimatedRunSpeed": 350.0, "AnimatedSprintSpeed": 600.0,
                     "StrideBlendAmountWalkCurve": curve_data(CURVES["stride_walk"]), "StrideBlendAmountRunCurve": curve_data(CURVES["stride_run"]),
                     "PivotActivationSpeedThreshold": 200.0},
        "Crouching": {"AnimatedCrouchSpeed": 150.0, "StrideBlendAmountCurve": curve_data(CURVES["stride_crouch"])},
        "InAir": {"LeanAmountCurve": curve_data(CURVES["lean_air"]), "GroundPredictionAmountCurve": curve_data(CURVES["ground_prediction"])},
        "FootLock": {"bAllowFootLock": True, "ThighAngleLimit": 60.0, "FootAngleLimit": 30.0},
        "Transitions": {"QuickStopBlendInDuration": 0.1, "QuickStopBlendOutDuration": 0.2, "QuickStopPlayRate": [1.75, 3.0], "QuickStopStartTime": 0.3,
                        "StandingLeftSequence": SEQ["StandL"], "StandingRightSequence": SEQ["StandR"],
                        "CrouchingLeftSequence": SEQ["CrouchL"], "CrouchingRightSequence": SEQ["CrouchR"]},
        "DynamicTransitions": {"FootLockDistanceThreshold": 8.0, "BlendDuration": 0.2, "PlayRate": 1.5,
                               "StandingLeftSequence": SEQ["DynStandL"], "StandingRightSequence": SEQ["DynStandR"],
                               "CrouchingLeftSequence": SEQ["DynCrouchL"], "CrouchingRightSequence": SEQ["DynCrouchR"]},
        "RotateInPlace": {"ViewYawAngleThreshold": 50.0, "FirstPersonViewYawAngleThreshold": 25.0, "ReferenceViewYawSpeed": [130.0, 300.0], "PlayRate": [1.15, 3.0]},
        "TurnInPlace": {"ViewYawAngleThreshold": 45.0, "ViewYawSpeedThreshold": 50.0, "ViewYawAngleToActivationDelay": [0.1, 0.5],
                        "Turn180AngleThreshold": 130.0, "BlendDuration": TURN_BLEND_DURATION, **turn_settings_data()},
    }


def character_settings_data(teleport=50.0):
    """AlsCharacterSettings of the stand-in character: the instance reads TeleportDistanceThreshold from it."""
    return {"TeleportDistanceThreshold": teleport}


def oracle_settings(use_foot_ik=False, teleport=50.0):
    c = {k: curve_fn(v) for k, v in CURVES.items()}
    return {
        "teleport_distance_threshold": teleport,
        "general": {"moving_smooth_speed_threshold": 150.0, "lean_half_life": 0.2, "use_hand_ik": True, "use_foot_ik": use_foot_ik},
        "head": {"pitch_hl": 0.1, "yaw_hl": 0.1, "switch_hl": 0.2, "fp_pitch_hl": 0.01, "fp_yaw_hl": 0.01},
        "grounded": {"velocity_blend_hl": 0.1, "yaw_offset_curves": (c["yaw_forward"], c["yaw_backward"], c["yaw_left"], c["yaw_right"])},
        "standing": {"animated_walk": 150.0, "animated_run": 350.0, "animated_sprint": 600.0, "stride_walk": c["stride_walk"], "stride_run": c["stride_run"],
                     "pivot_threshold": 200.0},
        "crouching": {"animated_speed": 150.0, "stride": c["stride_crouch"]},
        "in_air": {"lean_curve": c["lean_air"], "ground_prediction_curve": c["ground_prediction"]},
        "foot_lock": {"allow": True, "thigh_angle_limit": 60.0, "foot_angle_limit": 30.0},
        "rotate_in_place": {"view_yaw_threshold": 50.0, "fp_view_yaw_threshold": 25.0, "reference_view_yaw_speed": (130.0, 300.0), "play_rate": (1.15, 3.0)},
        "transitions": {"quick_stop_blend_in": 0.1, "quick_stop_blend_out": 0.2, "quick_stop_play_rate": (1.75, 3.0), "quick_stop_start_time": 0.3,
                        "standing_left": SEQ["StandL"], "standing_right": SEQ["StandR"], "crouching_left": SEQ["CrouchL"], "crouching_right": SEQ["CrouchR"]},
        "dynamic_transitions": {"foot_lock_distance_threshold": 8.0, "blend_duration": 0.2, "play_rate": 1.5,
                                "standing_left": SEQ["DynStandL"], "standing_right": SEQ["DynStandR"],
                                "crouching_left": SEQ["DynCrouchL"], "crouching_right": SEQ["DynCrouchR"]},
        "turn_in_place": {"view_yaw_angle_threshold": 45.0, "view_yaw_speed_threshold": 50.0, "view_yaw_angle_to_activation_delay": (0.1, 0.5),
                          "turn_180_angle_threshold": 130.0, "blend_duration": TURN_BLEND_DURATION,
                          "turns": {key: {"sequence": seq, "play_rate": rate, "scale_by_angle": scale, "animated_turn_angle": angle}
                                    for key, _, seq, rate, scale, angle in TURNS}},
    }


def sequence_options(seed):
    """What a random sequence of a seed fixes: the foot bones the instance reads, the thigh translations of the reference skeleton."""
    rng = random.Random(seed * 7919)
    return {"use_foot_ik": seed % 3 != 0,
            "thigh_left": (rng.uniform(-3, 3), rng.uniform(-14, -8), rng.uniform(-4, 4)),
            "thigh_right": None if seed % 7 == 0 else (rng.uniform(-3, 3), rng.uniform(8, 14), rng.uniform(-4, 4))}


def system_params(use_foot_ik=False, teleport=50.0):
    return {INSTANCE: {"Settings": settings_data(use_foot_ik)}, CHARACTER: {"Settings": character_settings_data(teleport)}}


# ---- frames -------------------------------------------------------------------------------------------------------------------------------

def axis_quat(yaw, pitch=0.0, roll=0.0):
    """The quaternion of the rotator (pitch, yaw, roll) in degrees (UE's FRotator::Quaternion)."""
    sp, cp = math.sin(math.radians(pitch) / 2), math.cos(math.radians(pitch) / 2)
    sy, cy = math.sin(math.radians(yaw) / 2), math.cos(math.radians(yaw) / 2)
    sr, cr = math.sin(math.radians(roll) / 2), math.cos(math.radians(roll) / 2)
    return (cr * sp * sy - sr * cp * cy, -cr * sp * cy - sr * cp * sy, cr * cp * sy - sr * sp * cy, cr * cp * cy + sr * sp * sy)


def tag(kind, member):
    return f"Als.{kind}." + member if member else ""


def ground_sweep(ground_z):
    """The scene the sweeps of the tests are asked: a horizontal ground plane (None: no ground) and a vertical capsule, which the test host
    models as three spheres (bottom, middle, top); a sphere that starts closer to the plane than its radius hits at time 0."""
    def sweep(start, end, radius, half_height):
        d = (end[0] - start[0], end[1] - start[1], end[2] - start[2])
        reach = half_height - radius
        best = None
        for off in ((0.0,) if reach <= 0.0 else (-reach, 0.0, reach)):
            z = start[2] + off
            if ground_z is None:
                continue
            above = z >= ground_z
            if abs(z - ground_z) < radius:
                hit = (0.0, 1.0 if above else -1.0)
            elif d[2] != 0.0:
                t = ((ground_z + (radius if above else -radius)) - z) / d[2]
                if not 0.0 <= t <= 1.0:
                    continue
                hit = (t, 1.0 if above else -1.0)
            else:
                continue
            if best is None or hit[0] < best[0]:
                best = hit
        if best is None:
            return {"blocking": False, "normal": (0.0, 0.0, 0.0), "time": 1.0}
        return {"blocking": True, "normal": (0.0, 0.0, best[1]), "time": best[0]}
    return sweep


# the entry calls a graph makes, with the arguments a .dab gives them (oracle method name, entry name)
ENTRIES = [
    ("initialize_head", "InitializeHead"), ("refresh_head", "RefreshHead"), ("initialize_lean", "InitializeLean"),
    ("reset_grounded_entry_mode", "ResetGroundedEntryMode"), ("initialize_grounded", "InitializeGrounded"), ("refresh_grounded", "RefreshGrounded"),
    ("refresh_grounded_movement", "RefreshGroundedMovement"), ("set_hips_direction", "SetHipsDirection"),
    ("initialize_standing_movement", "InitializeStandingMovement"), ("refresh_standing_movement", "RefreshStandingMovement"),
    ("activate_pivot", "ActivatePivot"), ("reset_pivot", "ResetPivot"), ("refresh_crouching_movement", "RefreshCrouchingMovement"),
    ("refresh_in_air", "RefreshInAir"), ("play_quick_stop_animation", "PlayQuickStopAnimation"),
    ("play_transition_animation", "PlayTransitionAnimation"), ("play_transition_left_animation", "PlayTransitionLeftAnimation"),
    ("play_transition_right_animation", "PlayTransitionRightAnimation"),
    ("stop_transition_and_turn_in_place_animations", "StopTransitionAndTurnInPlaceAnimations"),
    ("refresh_dynamic_transitions", "RefreshDynamicTransitions"), ("refresh_rotate_in_place", "RefreshRotateInPlace"),
    ("initialize_turn_in_place", "InitializeTurnInPlace"), ("refresh_turn_in_place", "RefreshTurnInPlace"),
]


def random_call(rng, entry):
    """(oracle method, entry name, kwargs of the call)."""
    kw = {}
    if entry == "SetHipsDirection" and rng.random() < 0.8:
        kw["NewHipsDirection"] = rng.choice(HIPS)
    if entry == "PlayTransitionAnimation":
        kw["Sequence"] = rng.choice(["A_Als_Stop_Right", "A_Als_Stop_Left"])
    if entry.startswith("PlayTransition"):
        for name, lo, hi in (("BlendInDuration", 0.0, 0.5), ("BlendOutDuration", 0.0, 0.5), ("PlayRate", 0.5, 2.0), ("StartTime", 0.0, 0.6)):
            if rng.random() < 0.5:
                kw[name] = rng.uniform(lo, hi)
        if rng.random() < 0.4:
            kw["bFromStandingIdleOnly"] = rng.random() < 0.5
    if entry == "StopTransitionAndTurnInPlaceAnimations" and rng.random() < 0.5:
        kw["BlendOutDuration"] = rng.uniform(0.0, 0.4)
    return kw


def graph_calls(rng, modes):
    """The entries a real graph calls every frame in the current state (locomotion / stance graphs), with the odd extra."""
    names = ["RefreshHead", "RefreshGroundedMovement", "RefreshRotateInPlace", "RefreshTurnInPlace", "RefreshDynamicTransitions"]
    names.append("RefreshGrounded" if modes["locomotion"] == "Grounded" else "RefreshInAir")
    names.append("RefreshCrouchingMovement" if modes["stance"] == "Crouching" else "RefreshStandingMovement")
    if rng.random() < 0.1:
        names.append(rng.choice(["InitializeHead", "InitializeGrounded", "InitializeLean", "InitializeStandingMovement", "InitializeTurnInPlace", "ActivatePivot",
                                 "ResetPivot", "PlayQuickStopAnimation", "ResetGroundedEntryMode", "SetHipsDirection"]))
    return names


def random_notify(rng):
    """A notify event of the instance: the grounded entry mode notify, with the tag or without it."""
    return ("notify", "AlsAnimNotify_SetGroundedEntryMode", rng.choice([{}, {"GroundedEntryMode": "Als.GroundedEntryMode.FromRoll"}]))


def make_sequence(seed, count, calls_per_frame=3, graph=None):
    """A character-like sequence: ``frames`` for the .dal system (``harness`` frames, calls per logic) and ``oframes`` + ``ocalls`` +
    ``opeer`` (the calls of the character: Jump, SnapshotFinalRagdollPose) for the oracle.  Gait, stance, rotation / view mode,
    locomotion mode and action change now and then, the velocity follows the input, the view turns (fast sometimes, and holds still while
    the actor does not follow it: turn in place), the actor turns towards where it goes, the feet step with the speed and lock when planted,
    the time dilates, curves are plausible, notifies arrive.
    ``graph`` (default: odd seeds) makes the entry calls those of a real graph, every frame; otherwise a random subset of all of them."""
    rng = random.Random(seed)
    options = sequence_options(seed)
    graph = bool(seed % 2) if graph is None else graph
    yaw, view_yaw, view_pitch = rng.uniform(-180, 180), rng.uniform(-180, 180), rng.uniform(-30, 30)
    vel = [0.0, 0.0, 0.0]
    loc = [0.0, 0.0, 100.0]
    modes = dict(view="ThirdPerson", locomotion="Grounded", rotation="ViewDirection", stance="Standing", gait="Walking", overlay="Default", action="")
    has_input, input_yaw, target_yaw = False, 0.0, yaw
    max_acc, max_brake, scale = 2000.0, 1500.0, 1.0
    tilt = rng.random() < 0.2                      # a tilted actor now and then: pitch / roll in the rotator
    ready_after = rng.choice([0, 1, 2])
    ground_z = rng.choice([-5.0, 0.0, 0.0, 12.0, None])
    pelvis_identity_frames = rng.choice([0, 0, 1, 3, 8])        # the pose of the previous evaluation is not there yet: GetSocketTransform gives the identity
    foot_phase, hold = 0.0, 0
    foot_curves = dict(FootLeftIk=1.0, FootLeftLock=1.0, FootRightIk=1.0, FootRightLock=1.0)
    allow_transitions, not_ready = 1.0, 0
    time_dilation, actor_dilation = 1.0, 1.0
    frames, oframes, ocalls, opeer = [], [], [], []
    last_view_yaw = view_yaw
    for i in range(count):
        dt = rng.choice([1 / 60, 1 / 60, 1 / 120, 1 / 30, rng.uniform(0.002, 0.05)])
        if i == 0:
            dt = 1 / 60
        elif rng.random() < 0.02:
            dt = 0.0
        if rng.random() < 0.04:
            modes["view"] = rng.choice(["ThirdPerson", "ThirdPerson", "FirstPerson"])
        if rng.random() < 0.05:
            modes["rotation"] = rng.choice(["VelocityDirection", "ViewDirection", "ViewDirection", "Aiming"])
        if rng.random() < 0.04:
            modes["stance"] = rng.choice(["Standing", "Crouching"])
        if rng.random() < 0.06:
            modes["gait"] = rng.choice(["Walking", "Running", "Sprinting"])
        if rng.random() < 0.04:
            modes["locomotion"] = rng.choice(["Grounded", "Grounded", "InAir"])
        if rng.random() < 0.03:
            modes["action"] = rng.choice(["", "", "Rolling", "Mantling", "Ragdolling", "GettingUp"])
        if rng.random() < 0.05:
            modes["overlay"] = rng.choice(["Default", "Rifle", "Bow"])
        if rng.random() < 0.08:
            has_input = not has_input
            input_yaw = rng.uniform(-180, 180)
        # velocity: towards the input direction, braking otherwise
        goal = {"Walking": 175.0, "Running": 375.0, "Sprinting": 650.0}[modes["gait"]] if has_input else 0.0
        gx, gy = goal * math.cos(math.radians(input_yaw)), goal * math.sin(math.radians(input_yaw))
        for k, g in ((0, gx), (1, gy)):
            rate = (max_acc if has_input else max_brake) * max(dt, 1e-3)
            vel[k] += max(-rate, min(rate, g - vel[k]))
        if modes["locomotion"] == "InAir":
            vel[2] = max(-1200.0, min(900.0, vel[2] + rng.uniform(-1, 0.6) * 1500.0 * dt))
        else:
            vel[2] = 0.0
        if rng.random() < 0.02:
            vel[0], vel[1] = 0.0, 0.0
        speed = math.hypot(vel[0], vel[1])
        has_velocity = speed >= 1.0
        velocity_yaw = math.degrees(math.atan2(vel[1], vel[0])) if has_velocity else 0.0
        moving = (has_input and has_velocity) or speed > 50.0
        # view and actor rotation; now and then the view is thrown to the side and the actor stays (a turn in place)
        if hold == 0:
            view_yaw += rng.choice([0.0, 0.0, 1.0, 5.0, -3.0, 40.0]) * rng.uniform(-1, 1) * (1.0 if rng.random() > 0.01 else 8.0)
        if hold == 0 and rng.random() < 0.03:
            view_yaw += rng.choice([-1.0, 1.0]) * rng.uniform(60.0, 178.0)
            hold = rng.randint(15, 60)
            modes["rotation"], modes["view"], allow_transitions = "ViewDirection", "ThirdPerson", 1.0       # what a turn in place needs; the stance is the sequence's
            modes["stance"] = rng.choice(["Standing", "Crouching"])
        view_yaw = ((view_yaw + 180.0) % 360.0) - 180.0
        view_pitch = max(-80.0, min(80.0, view_pitch + rng.uniform(-3, 3)))
        view_speed = abs(((view_yaw - last_view_yaw + 180.0) % 360.0) - 180.0) / dt if dt > 1e-6 else 0.0
        last_view_yaw = view_yaw
        if rng.random() < 0.1:
            target_yaw = rng.uniform(-180, 180)
        if hold > 0:
            hold -= 1                                                    # the actor does not follow the view for a while
        elif modes["rotation"] != "VelocityDirection" or not has_velocity:
            yaw += max(-300.0 * dt, min(300.0 * dt, ((view_yaw - yaw + 180.0) % 360.0) - 180.0)) * (1.0 if rng.random() > 0.3 else 0.0)
        else:
            yaw += max(-500.0 * dt, min(500.0 * dt, ((velocity_yaw - yaw + 180.0) % 360.0) - 180.0))
        if rng.random() < 0.01:
            yaw = rng.uniform(-180, 180)
        yaw += rng.uniform(-0.01, 0.01)                                   # the actor never lands exactly on the yaw it turns to: an exact tie (a remaining yaw of 0 against 3e-15) is a coin flip of the rounding
        yaw = ((yaw + 180.0) % 360.0) - 180.0
        if rng.random() < 0.03:
            max_acc = rng.choice([0.0, 1000.0, 2000.0, 2500.0])
            max_brake = rng.choice([0.0, 1000.0, 1500.0, 2500.0])
        if rng.random() < 0.02:
            scale = rng.choice([1.0, 1.0, 1.2, 0.8])
        loc = [loc[0] + vel[0] * dt, loc[1] + vel[1] * dt, loc[2] + vel[2] * dt]
        if ground_z is not None and loc[2] < ground_z + 92.0:
            loc[2] = ground_z + 92.0                                     # the capsule stands on the ground
        if rng.random() < 0.01:
            loc = [loc[0] + rng.uniform(100, 500), loc[1] - rng.uniform(100, 500), loc[2]]          # a teleport
        pitch, roll = (rng.uniform(-8, 8), rng.uniform(-8, 8)) if tilt else (0.0, 0.0)
        quat = axis_quat(yaw, pitch, roll)
        if not_ready == 0 and i > 10 and rng.random() < 0.01:
            not_ready = rng.randint(1, 3)                                # the actor has not registered all its components (again): the update is pending
        ready = i >= ready_after and not_ready == 0
        not_ready = max(0, not_ready - 1)
        # time dilation: mostly none; the actor's own dilation is sometimes 0 (then the real delta time is the unscaled one)
        if rng.random() < 0.03:
            time_dilation = rng.choice([1.0, 1.0, 0.5, 2.0])
        if rng.random() < 0.03:
            actor_dilation = rng.choice([1.0, 1.0, 0.0, 0.25, 1.5])
        real_delta_time = rng.uniform(0.005, 0.05)
        curves = {}
        grounded_amount = 0.0 if modes["locomotion"] == "InAir" else 1.0
        if rng.random() < 0.15:
            grounded_amount = rng.random()
        curves["PoseGrounded"] = grounded_amount
        curves["PoseInAir"] = 1.0 - grounded_amount
        crouching = 1.0 if modes["stance"] == "Crouching" else 0.0
        curves["PoseStanding"], curves["PoseCrouching"] = 1.0 - crouching, crouching
        curves["PoseMoving"] = 1.0 if moving else 0.0
        curves["PoseGait"] = {"Walking": 1.0, "Running": 2.0, "Sprinting": 3.0}[modes["gait"]] * rng.choice([1.0, 1.0, 0.5, 1.05])
        curves["PoseAiming"] = 1.0 if modes["rotation"] == "Aiming" else rng.choice([0.0, 0.0, 0.3])
        curves["ViewBlock"] = rng.choice([0.0, 0.0, 0.0, 0.5, 1.0])
        if rng.random() < 0.08:
            allow_transitions = rng.choice([1.0, 1.0, 1.0, 0.0, 0.999995, 0.5])
        curves["AllowTransitions"] = allow_transitions
        curves["SprintBlock"] = rng.choice([0.0, 0.0, 0.4, 1.3])
        curves["HipsDirectionLock"] = rng.choice([0.0, 0.5, -1.4, 1.4])
        curves["FootPlanted"] = rng.uniform(-1.3, 1.3)
        curves["FeetCrossing"] = rng.uniform(-0.2, 1.2)
        curves["GroundPredictionBlock"] = rng.choice([0.0, 0.0, 0.0, 1.0, 0.5, 0.97])
        for name in ("Head", "HeadAdditive", "HeadSlot", "ArmLeft", "ArmLeftAdditive", "ArmLeftSlot", "ArmLeftLocalSpace", "ArmRight", "ArmRightAdditive",
                     "ArmRightSlot", "ArmRightLocalSpace", "HandLeft", "HandRight", "Spine", "SpineAdditive", "SpineSlot", "Pelvis", "PelvisSlot", "Legs",
                     "LegsSlot"):
            curves["Layer" + name] = rng.choice([0.0, 1.0, 1.0, rng.random(), 0.999995])
        # feet: the IK / lock curves change now and then; the feet step with the speed
        for name in foot_curves:
            if rng.random() < (0.05 if name.endswith("Lock") else 0.03):
                foot_curves[name] = rng.choice([0.0, 0.3, 0.8, 1.0, 1.0, 1.0, 0.999995, 0.9] if name.endswith("Lock") else [0.0, 0.5, 1.0, 1.0, 1.0, 0.00001])
        curves.update(foot_curves)
        foot_phase += speed / 150.0 * dt * 6.0
        mesh_quat = q_mul(quat, axis_quat(-90.0))
        mesh_location = tuple(a + b for a, b in zip(loc, q_rotate(quat, (0.0, 0.0, -90.0 * scale))))
        mesh_transform = (mesh_location, mesh_quat, (scale, scale, scale))
        feet = {}
        for side, sgn, shift in (("l", -1.0, 0.0), ("r", 1.0, math.pi)):
            ph = foot_phase + shift
            stepping = 1.0 if speed > 1.0 else 0.0
            local_location = (22.0 * math.sin(ph) * stepping, sgn * 14.0, 4.0 + 8.0 * max(0.0, math.cos(ph)) * stepping)
            twist = 12.0 * math.sin(ph) + (rng.choice([50.0, -60.0, 25.0]) if rng.random() < 0.08 else 0.0)
            ik = (t_position(mesh_transform, local_location), q_mul(mesh_quat, axis_quat(twist)), (1.0, 1.0, 1.0))
            vb = (t_position(mesh_transform, (local_location[0] + 1.5, local_location[1], local_location[2] + 2.0)), q_mul(mesh_quat, axis_quat(twist + 5.0)),
                  (1.0, 1.0, 1.0))
            feet["ik_" + side], feet["vb_" + side] = ik, vb
        pelvis = ((0.0, 0.0, 0.0), QUAT_ID, (1.0, 1.0, 1.0)) if (i < pelvis_identity_frames or rng.random() < 0.01) else \
            ((rng.uniform(-2, 2), rng.uniform(-2, 2), 90.0 + rng.uniform(-4, 4)), axis_quat(rng.uniform(-20, 20), rng.uniform(-8, 8), rng.uniform(-8, 8)), (1.0, 1.0, 1.0))
        loc_state = {"bHasInput": has_input, "InputYawAngle": input_yaw, "bHasVelocity": has_velocity, "Speed": speed, "Velocity": tuple(vel),
                     "VelocityYawAngle": velocity_yaw, "bMoving": moving, "TargetYawAngle": target_yaw}
        view_state = {"Rotation": (view_pitch, view_yaw, 0.0), "YawSpeed": view_speed}
        jump = rng.random() < 0.03
        snapshot = rng.random() < 0.02
        ragdoll_velocity = (rng.uniform(-900, 900), rng.uniform(-900, 900), rng.uniform(-900, 300))
        free = {"InViewMode": tag("ViewMode", modes["view"]), "InLocomotionMode": tag("LocomotionMode", modes["locomotion"]),
                "InRotationMode": tag("RotationMode", modes["rotation"]), "InStance": tag("Stance", modes["stance"]), "InGait": tag("Gait", modes["gait"]),
                "InOverlayMode": tag("OverlayMode", modes["overlay"]), "InLocomotionAction": tag("LocomotionAction", modes["action"]),
                "InLocomotion": loc_state, "InView": view_state, "InRagdolling": {"Velocity": ragdoll_velocity},
                "DoJump": jump, "DoSnapshot": snapshot}
        events = [random_notify(rng)] if rng.random() < 0.08 else []
        calls = []
        if graph:
            by_entry = {entry: method for method, entry in ENTRIES}
            for entry in graph_calls(rng, modes):
                calls.append((by_entry[entry], entry, random_call(rng, entry)))
        else:
            for _ in range(rng.randint(0, calls_per_frame)):
                method, entry = rng.choice(ENTRIES)
                calls.append((method, entry, random_call(rng, entry)))
        t = (frames[-1]["time"] + dt) if frames else dt
        host = {"dt": dt, "time": t, "actor_location": tuple(loc), "actor_rotation": quat, "mesh_scale": scale,
                "capsule_radius": 30.0, "capsule_half_height": 90.0, "max_acceleration": max_acc, "max_braking_deceleration": max_brake,
                "walkable_floor_z": 0.71, "actor_ready": ready, "curves": curves,
                "time_dilation": time_dilation, "actor_time_dilation": actor_dilation, "real_delta_time": real_delta_time,
                "mesh_world_transform": mesh_transform,
                "bones": {"pelvis": {"component": pelvis}, **{name: {"world": tr} for name, tr in
                                                              (("ik_foot_l", feet["ik_l"]), ("ik_foot_r", feet["ik_r"]), ("VB foot_l", feet["vb_l"]), ("VB foot_r", feet["vb_r"]))}},
                "skeleton": skeleton(options), "world": {"ground_z": ground_z}, "events": events}
        frames.append({**host, "free": {CHARACTER: free}, "calls": {INSTANCE: [(entry, kw) for _, entry, kw in calls]}})
        oframes.append({"dt": dt, "time": t, "location": host["actor_location"], "rotation": quat, "scale": scale, "capsule_radius": 30.0,
                        "capsule_half_height": 90.0, "max_acceleration": max_acc, "max_braking_deceleration": max_brake, "walkable_floor_z": 0.71,
                        "actor_ready": ready, "curves": curves, "time_dilation": time_dilation, "actor_time_dilation": actor_dilation,
                        "real_delta_time": real_delta_time, "mesh_transform": mesh_transform, "pelvis": pelvis, "feet": feet,
                        "sweep": ground_sweep(ground_z), "events": events,
                        "char": {"view_mode": free["InViewMode"], "locomotion_mode": free["InLocomotionMode"], "rotation_mode": free["InRotationMode"],
                                 "stance": free["InStance"], "gait": free["InGait"], "overlay_mode": free["InOverlayMode"],
                                 "locomotion_action": free["InLocomotionAction"], "loc": loc_state, "view": view_state,
                                 "ragdoll_velocity": ragdoll_velocity}})
        ocalls.append([(method, kw) for method, _, kw in calls])
        opeer.append((["jump"] if jump else []) + (["snapshot_final_ragdoll_pose"] if snapshot else []))
    return frames, oframes, ocalls, opeer


QUAT_ID = (0.0, 0.0, 0.0, 1.0)


def skeleton(options):
    """The reference skeleton the thigh axes are read from (frame 0): pelvis, and a thigh / calf / foot chain for each foot that has a path."""
    sk = {"pelvis": {"parent": None, "translation": (0.0, 0.0, 90.0)}}
    for side, key in (("l", "thigh_left"), ("r", "thigh_right")):
        if options[key] is None:
            sk[f"foot_{side}"] = {"parent": None, "translation": (0.0, 0.0, 0.0)}         # a foot that does not hang below the pelvis
            continue
        sk[f"thigh_{side}"] = {"parent": "pelvis", "translation": options[key]}
        sk[f"calf_{side}"] = {"parent": f"thigh_{side}", "translation": (0.0, 0.0, -40.0)}
        sk[f"foot_{side}"] = {"parent": f"calf_{side}", "translation": (0.0, 0.0, -40.0)}
    return sk


def make_oracle(seed, teleport=50.0):
    """The oracle of a sequence's options, after NativeInitializeAnimation."""
    options = sequence_options(seed)
    o = AlsAnimOracle(oracle_settings(options["use_foot_ik"], teleport))
    o.native_initialize_animation(options["thigh_left"], options["thigh_right"])
    return o


def run_oracle(oframes, ocalls, opeer, seed, teleport=50.0, pre_post=None, oracle=None):
    """The oracle's states and host commands after every frame.  ``pre_post`` (a list) receives the states the graph left, before the post update."""
    o = oracle or make_oracle(seed, teleport)
    out, commands = [], []
    for f, calls, peer in zip(oframes, ocalls, opeer):
        out.append(o.frame(f, calls, peer))
        commands.append(tuple(o.commands))
        if pre_post is not None:
            pre_post.append(o.before_post)
    return out, commands, o


def instance_states(snapshot):
    return {k: v for k, v in snapshot.items() if not k.startswith("__")}


def close(a, b, tol):
    if isinstance(a, (tuple, list)) or isinstance(b, (tuple, list)):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    return a == b


def compare_with_oracle(test, out, expected, label, tol=1e-9, commands=None):
    """Every state the oracle has, every frame, and (with ``commands``, the oracle's per-frame lists) every host command in order; ``out`` is
    the run_system output."""
    worst = 0.0
    checked = 0
    for i, (frame, exp) in enumerate(zip(out, expected)):
        got = frame[INSTANCE]
        for name, want in exp.items():
            if name not in got:
                test.fail(f"{label}: frame {i}: the instance has no state {name}")
            value = got[name]
            if name == "GroundedState.HipsDirection":
                want = HIPS.index(want)
            if name == "RagdollingState.FinalRagdollPose":
                value = value is not None                            # a snapshot value: taken or not
            checked += 1
            if not close(value, want, tol):
                test.fail(f"{label}: frame {i} {name}: dal={value!r} oracle={want!r}")
            if isinstance(want, float):
                worst = max(worst, abs(value - want) / max(1.0, abs(want)))
        if commands is not None:
            got_cmds = list(got["__cmds__"])
            want_cmds = list(commands[i])
            if len(got_cmds) != len(want_cmds) or not all(g[0] == w[0] and close(g[1], w[1], tol) for g, w in zip(got_cmds, want_cmds)):
                test.fail(f"{label}: frame {i} commands: dal={got_cmds!r} oracle={want_cmds!r}")
            checked += len(want_cmds) + 1
    return worst, checked


class OracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mods = A.compile_system()

    def run_dal(self, frames, seed, backend="python", teleport=50.0):
        options = sequence_options(seed)
        return A.run_system(self.mods, frames, system_params(options["use_foot_ik"], teleport), backend)

    def test_matches_the_oracle_on_random_sequences(self):
        total = 0
        for seed in range(1, 25):
            frames, oframes, ocalls, opeer = make_sequence(seed, 260)
            expected, commands, _ = run_oracle(oframes, ocalls, opeer, seed)
            _, n = compare_with_oracle(self, self.run_dal(frames, seed), expected, f"python seed {seed}", commands=commands)
            total += n
        self.assertGreater(total, 150000)

    def test_the_interpreter_matches_the_oracle_too(self):
        for seed in (21, 22, 23):
            frames, oframes, ocalls, opeer = make_sequence(seed, 150)
            expected, commands, _ = run_oracle(oframes, ocalls, opeer, seed)
            compare_with_oracle(self, self.run_dal(frames, seed, "interp"), expected, f"interpreter seed {seed}", commands=commands)

    def test_a_far_teleport_threshold_and_a_near_one(self):
        """The threshold comes from the character's settings (peer read): the stand-in's Settings.TeleportDistanceThreshold."""
        for seed, teleport in ((5, 3.0), (6, 400.0)):
            frames, oframes, ocalls, opeer = make_sequence(seed, 200)
            expected, commands, _ = run_oracle(oframes, ocalls, opeer, seed, teleport)
            compare_with_oracle(self, self.run_dal(frames, seed, teleport=teleport), expected, f"teleport {teleport}", commands=commands)

    def test_the_sequences_exercise_the_hard_parts(self):
        """A random comparison that never leaves the easy branches proves nothing: count the interesting states and branches over the seeds."""
        seen = dict.fromkeys(["spine_on", "spine_off_decay", "rotating_left", "rotating_right", "jumped", "air", "sprint_accel", "crouch",
                              "teleport", "dir_left", "dir_right", "dir_backward", "pending", "mesh_space_arm", "turn_delay", "first_person", "quick_stop",
                              "stride_zero", "feet_valid", "feet_became_valid", "foot_locked", "foot_partly_locked", "time_dilated", "real_delta_time",
                              "snapshot_taken", "queued_turn", "transition_queued_by_feet"], False)
        hits = {}
        for seed in range(1, 25):
            frames, oframes, ocalls, opeer = make_sequence(seed, 260)
            pre = []
            states, _, oracle = run_oracle(oframes, ocalls, opeer, seed, pre_post=pre)
            for k, v in oracle.hits.items():
                hits[k] = hits.get(k, 0) + v
            for st, before in zip(states, pre):
                seen["spine_on"] |= st["SpineState.bSpineRotationAllowed"] and 0.0 < st["SpineState.SpineAmount"] < 1.0
                seen["spine_off_decay"] |= (not st["SpineState.bSpineRotationAllowed"]) and 0.0 < st["SpineState.SpineAmount"] < 0.9
                seen["rotating_left"] |= st["RotateInPlaceState.bRotatingLeft"]
                seen["rotating_right"] |= st["RotateInPlaceState.bRotatingRight"]
                seen["jumped"] |= st["InAirState.JumpPlayRate"] != 1.0
                seen["air"] |= st["InAirState.VerticalVelocityWorldSpace"] < -50.0
                seen["sprint_accel"] |= st["StandingState.SprintAccelerationAmount"] > 0.05
                seen["crouch"] |= st["CrouchingState.PlayRate"] not in (1.0, 2.0) and st["CrouchingState.PlayRate"] > 0.0
                seen["teleport"] |= st["TeleportedTime"] > 0.0
                seen["dir_left"] |= st["GroundedState.MovementDirection.bLeft"]
                seen["dir_right"] |= st["GroundedState.MovementDirection.bRight"]
                seen["dir_backward"] |= st["GroundedState.MovementDirection.bBackward"]
                seen["pending"] |= st["bPendingUpdate"]
                seen["mesh_space_arm"] |= st["LayeringState.ArmLeftMeshSpaceBlendAmount"] == 0.0
                seen["turn_delay"] |= st["TurnInPlaceState.ActivationDelay"] > 0.0
                seen["first_person"] |= st["ViewMode"].endswith("FirstPerson")
                seen["quick_stop"] |= before["TransitionsState.QueuedTransitionSequence"] is not None
                seen["stride_zero"] |= st["CrouchingState.StrideBlendAmount"] == 0.0 and st["CrouchingState.PlayRate"] == 2.0
                seen["feet_valid"] |= st["FeetState.bValid"]
                seen["feet_became_valid"] |= st["FeetState.bBecameValid"]
                seen["foot_locked"] |= st["FeetState.Left.LockAmount"] == 1.0
                seen["foot_partly_locked"] |= 0.0 < st["FeetState.Right.LockAmount"] < 1.0
                seen["time_dilated"] |= st["DeltaTimeWithoutTimeDilation"] != 0.0 and st["PreviousGlobalTimeDilation"] != 1.0
                seen["snapshot_taken"] |= st["RagdollingState.FinalRagdollPose"]
                seen["queued_turn"] |= before["TurnInPlaceState.QueuedSlotName"] != 0
                seen["transition_queued_by_feet"] |= st["DynamicTransitionsState.FrameDelay"] == 2
            seen["real_delta_time"] |= any(f["actor_time_dilation"] == 0.0 for f in oframes)
        self.assertEqual([k for k, v in seen.items() if not v], [])
        for branch in ("foot_lock_teleport", "foot_lock_became_valid", "foot_lock_decrease_moving", "foot_lock_decrease_in_air", "foot_lock_released",
                       "foot_lock_new_lock", "foot_lock_new_location", "foot_lock_blend_out", "thigh_angle_clamped", "foot_angle_clamped",
                       "ground_prediction_hit", "ground_prediction_miss", "dynamic_transition_left", "dynamic_transition_right",
                       "dynamic_transition_both", "turn_played", "grounded_entry_mode_notify", "snapshot",
                       *[f"turn_{side}_{angle}_{stance}" for side in ("left", "right") for angle in (90, 180) for stance in ("standing", "crouching")]):
            self.assertGreater(hits.get(branch, 0), 0, f"no sequence got into {branch}: {sorted(hits)}")


# ---- three backends --------------------------------------------------------------------------------------------------------------------

def add_random_pose(frames, seed):
    """The pelvis and the four foot bones for every frame of the random system frames: a bone the frame does not have is the identity in Python and
    the component transform in UE (dal-syntax-v3.md 8.1), which the hosts are allowed to differ in but a comparison of the two must not trip over."""
    rng = random.Random(seed)

    def transform():
        q = [rng.uniform(-1, 1) for _ in range(4)]
        n = math.sqrt(sum(c * c for c in q)) or 1.0
        return ((rng.uniform(-50, 50), rng.uniform(-50, 50), rng.uniform(-50, 50)), tuple(c / n for c in q), (1.0, 1.0, 1.0))

    for f in frames:
        f["bones"] = {name: {"component": transform(), "world": transform()}
                      for name in ("pelvis", "ik_foot_l", "ik_foot_r", "VB foot_l", "VB foot_r")}
        f["skeleton"] = skeleton(sequence_options(seed))
    return frames


def random_data_params(mods, seed, polyline):
    """Random settings data for the instance (and the character) in the layout dal.settings reads."""
    params = {}
    for n, m in mods.items():
        for p in m.params:
            if getattr(p.ty, "is_settings", False):
                params.setdefault(n, {})[p.name] = harness.random_settings(p.ty, seed=seed, scale=300.0, polyline=polyline)
    return params


class ThreeBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mods = A.compile_system(linked=True)

    def test_interpreter_and_python_agree_on_random_system_frames(self):
        for seed in (1, 2, 3):
            frames = add_random_pose(harness.random_system_frames(self.mods, 120, seed), seed)
            params = random_data_params(self.mods, seed, polyline=False)
            a = A.run_system(self.mods, frames, params, "interp")
            b = A.run_system(self.mods, frames, params, "python")
            for i, (fa, fb) in enumerate(zip(a, b)):
                for logic in fa:
                    diffs = harness.compare([fa[logic]], [fb[logic]], 1e-9)
                    self.assertEqual(diffs, [], f"seed {seed} frame {i} {logic}")

    def test_interpreter_and_python_agree_on_the_character_like_sequences(self):
        """The feet, the sweeps, the notifies and the montage commands: the random system frames rarely get into them."""
        mods = A.compile_system()
        for seed in (4, 5, 6):
            frames, _, _, _ = make_sequence(seed, 120)
            params = system_params(sequence_options(seed)["use_foot_ik"])
            a = A.run_system(mods, frames, params, "interp")
            b = A.run_system(mods, frames, params, "python")
            for i, (fa, fb) in enumerate(zip(a, b)):
                for logic in fa:
                    diffs = harness.compare([fa[logic]], [fb[logic]], 1e-9)
                    self.assertEqual(diffs, [], f"seed {seed} frame {i} {logic}")

    @unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
    def test_generated_cpp_matches_python(self):
        """MSVC /W4 /WX against the stand-in engine: the whole system (character, instance, linked instance) frame by frame."""
        import cpp_system
        for seed in (1, 2):
            frames = tcc.round_frames(add_random_pose(harness.random_system_frames(self.mods, 50, seed), seed))
            params = random_data_params(self.mods, seed, polyline=True)
            cpp, output = cpp_system.build_and_run_system(self.mods, frames, params=params)
            if tcc.COMPILER[0] == "msvc":
                self.assertNotIn("warning", output.lower(), output)
            py = A.run_system(self.mods, frames, params, "python")
            problems = []
            for i, (a, b) in enumerate(zip(py, cpp)):
                for n in self.mods:
                    for k, v in a[n].items():
                        if k.startswith("__"):
                            continue
                        if not tcc.close(v, b[n][k], 2e-3):
                            problems.append(f"frame {i} {n}.{k}: python={v!r} c++={b[n][k]!r}")
            self.assertEqual(problems[:5], [], f"{len(problems)} mismatches (seed {seed})")

    @unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
    def test_generated_cpp_matches_python_on_the_character_like_sequences(self):
        """The same with frames shaped like a character (the random ones rarely get into the spine / head / lean / foot lock branches), with
        the pose, the mesh transform, the ground, and the host commands compared too."""
        import cpp_system
        mods = A.compile_system()
        for seed in (3, 4):                          # 60 frames: the driver's main keeps every frame's temporaries on its stack (64 MB)
            frames, _, _, _ = make_sequence(seed, 60)
            frames = tcc.round_frames(frames)
            params = system_params(sequence_options(seed)["use_foot_ik"])
            cpp, output = cpp_system.build_and_run_system(mods, frames, params=params)
            if tcc.COMPILER[0] == "msvc":
                self.assertNotIn("warning", output.lower(), output)
            py = A.run_system(mods, frames, params, "python")
            problems = []
            for i, (a, b) in enumerate(zip(py, cpp)):
                for n in mods:
                    for k, v in a[n].items():
                        if not k.startswith("__") and not tcc.close(v, b[n][k], 2e-3):
                            problems.append(f"frame {i} {n}.{k}: python={v!r} c++={b[n][k]!r}")
                pc = tcc.py_effects(a[INSTANCE]["__cmds__"])                      # the commands of the frame, as the stand-in engine logs them
                cc = list(b[INSTANCE]["__cmds__"])
                if len(pc) != len(cc) or not all(len(x) == len(y) and x[0] == y[0] and all(tcc.close_token(u, v, 2e-3) for u, v in zip(x[1:], y[1:]))
                                                 for x, y in zip(pc, cc)):
                    problems.append(f"frame {i} commands: python={pc!r} c++={cc!r}")
            self.assertEqual(problems[:5], [], f"{len(problems)} mismatches (seed {seed})")

    def test_the_generated_cpp_has_the_unreal_shape(self):
        from dal.cppbackend import CppOptions, generate_cpp
        inst = generate_cpp(self.mods[INSTANCE], CppOptions(module_api="ALS_API"))
        h = inst["AlsAnimationInstance.h"]
        self.assertIn("class ALS_API UAlsAnimationInstance : public UAnimInstance", h)
        for entry in ("InitializeHead", "RefreshHead", "InitializeLean", "InitializeGrounded", "RefreshGrounded", "ResetGroundedEntryMode",
                      "RefreshGroundedMovement", "SetHipsDirection", "InitializeStandingMovement", "RefreshStandingMovement", "ResetPivot",
                      "RefreshCrouchingMovement", "RefreshInAir", "RefreshDynamicTransitions", "RefreshRotateInPlace", "InitializeTurnInPlace",
                      "RefreshTurnInPlace", "ActivatePivot", "PlayQuickStopAnimation", "PlayTransitionAnimation", "PlayTransitionLeftAnimation",
                      "PlayTransitionRightAnimation", "StopTransitionAndTurnInPlaceAnimations"):
            self.assertRegex(h, r"UFUNCTION\(BlueprintCallable, Category = \"DAL\|Entry\", Meta = \(BlueprintThreadSafe\)\)\n\tvoid " + entry + r"\(", entry)
        self.assertIn("SetHipsDirection(EAlsHipsDirection NewHipsDirection = EAlsHipsDirection::Forward)", h)
        self.assertRegex(h, r"UFUNCTION\(BlueprintPure, Category = \"DAL\|View\", Meta = \(BlueprintThreadSafe\)\)\n\tFAlsControlRigInput GetControlRigInput\(\) const")
        self.assertTrue("FAlsAnimationInstanceProxy : public FAnimInstanceProxy" in h, "the proxy has the name of ALS's (AlsAnimationInstanceProxy.h)")
        self.assertTrue("void DalPostUpdate();" in h, "the post_update block")
        self.assertIn("FGameplayTag Gait", h)
        self.assertIn("FAlsGroundedState GroundedState", h)
        self.assertIn("FAlsRagdollingAnimationState RagdollingState", h)
        link = generate_cpp(self.mods[A.LINKED], CppOptions(module_api="ALS_API"))["AlsLinkedAnimationInstance.h"]
        self.assertIn("class ALS_API UAlsLinkedAnimationInstance : public UAnimInstance", link)
        self.assertIn("TWeakObjectPtr<UAlsAnimationInstance> Parent;", link)
        self.assertIn("UAlsAnimationInstance* GetParent() const", link)
        for entry in ("InitializeHead", "RefreshHead", "InitializeLean", "InitializeGrounded", "RefreshGrounded", "ResetGroundedEntryMode",
                      "RefreshGroundedMovement", "SetHipsDirection", "InitializeStandingMovement", "RefreshStandingMovement", "ResetPivot",
                      "RefreshCrouchingMovement", "RefreshInAir", "RefreshDynamicTransitions", "RefreshRotateInPlace", "InitializeTurnInPlace",
                      "RefreshTurnInPlace"):
            self.assertIn(f"void {entry}(", link)
        self.assertNotIn("void ActivatePivot(", link)                       # called as Main.ActivatePivot(): not forwarded (LI has no such function)
        self.assertNotIn("PlayTransitionAnimation", link)

    def test_the_notifies_are_classes_of_the_ALS_names(self):
        from dal.cppbackend import CppOptions, generate_cpp
        files = generate_cpp(self.mods[INSTANCE], CppOptions(module_api="ALS_API"))
        text = "\n".join(files.values())
        self.assertIn("class ALS_API UAlsAnimNotify_SetGroundedEntryMode : public UAnimNotify", text)
        self.assertNotIn("UAlsAnimNotifyState_EarlyBlendOut", text)            # the early blend out reads the character's state: AlsCharacter.dal declares it, once


class MutationTests(unittest.TestCase):
    """The comparison with the oracle must be able to fail: a changed constant in the .dal shows up."""

    MUTATIONS = [
        ("ViewState.PitchAmount = 0.5 - ViewState.PitchAngle / 180.0;", "ViewState.PitchAmount = 0.5 - ViewState.PitchAngle / 90.0;"),
        ("let max_yaw_angle_offset = 30.0;", "let max_yaw_angle_offset = 40.0;"),
        ("let reference_speed = 600.0;", "let reference_speed = 650.0;"),
        ("let forward_half_angle = 70.0;", "let forward_half_angle = 75.0;"),
        ("let sprint_time_threshold = 0.5;", "let sprint_time_threshold = 0.6;"),
        # the foot lock, the ground prediction, the turn in place, the notifies, the time dilation
        ("let moving_decrease_speed = 5.0;", "let moving_decrease_speed = 6.0;"),
        ("let not_grounded_decrease_speed = 0.6;", "let not_grounded_decrease_speed = 0.5;"),
        ("if (foot_state.LockAmount <= 0.9) {", "if (foot_state.LockAmount <= 0.8) {"),
        ("WorldTime - TeleportedTime > 0.2", "WorldTime - TeleportedTime > 0.25"),
        ("deg_to_rad(clamped_angle - thigh_delta_angle)", "deg_to_rad(clamped_angle)"),
        ("let max_sweep_distance = 2000.0;", "let max_sweep_distance = 2100.0;"),
        ("let min_vertical_velocity = -4000.0;", "let min_vertical_velocity = -3500.0;"),
        ("GroundPredictionAmountCurve.eval(ground_hit.time)", "GroundPredictionAmountCurve.eval(1.0 - ground_hit.time)"),
        ("let turn_180 = abs(ViewState.YawAngle) >= Settings.TurnInPlace.Turn180AngleThreshold;", "let turn_180 = abs(ViewState.YawAngle) >= Settings.TurnInPlace.Turn180AngleThreshold - 50.0;"),
        ("TurnInPlaceState.PlayRate = turn_in_place_settings.PlayRate;", "TurnInPlaceState.PlayRate = Settings.TurnInPlace.BlendDuration;"),
        ("DynamicTransitionsState.FrameDelay = 2;", "DynamicTransitionsState.FrameDelay = 3;"),
        ("SetGroundedEntryMode(payload.GroundedEntryMode);", "SetGroundedEntryMode(AlsGroundedEntryMode.FromRoll);"),
        ("DeltaTimeWithoutTimeDilation = time_dilation > SMALL_NUMBER ? dt / time_dilation : RealDeltaTime;",
         "DeltaTimeWithoutTimeDilation = time_dilation > SMALL_NUMBER ? dt / time_dilation : dt;"),
    ]

    def test_every_mutation_is_caught(self):
        import dal
        for old, new in self.MUTATIONS:
            with self.subTest(mutation=old):
                files = A.virtual_files()
                text = files[A.INSTANCE + ".dal"]
                self.assertIn(old, text)
                files[A.INSTANCE + ".dal"] = text.replace(old, new)
                mods = {n: dal.check_source(files[n + ".dal"], n + ".dal", files=files).module for n in (CHARACTER, INSTANCE)}
                caught = False
                for seed in range(1, 25):
                    frames, oframes, ocalls, opeer = make_sequence(seed, 200)
                    out = A.run_system(mods, frames, system_params(sequence_options(seed)["use_foot_ik"]))
                    expected, commands, _ = run_oracle(oframes, ocalls, opeer, seed)
                    try:
                        compare_with_oracle(self, out, expected, "mutated", commands=commands)
                    except AssertionError:
                        caught = True
                        break
                self.assertTrue(caught, f"no sequence noticed: {old} -> {new}")


if __name__ == "__main__":
    unittest.main()
