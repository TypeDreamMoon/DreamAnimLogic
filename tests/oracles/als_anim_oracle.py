"""Independent Python port of UAlsAnimationInstance (ALS-Refactored, MIT, Sixze): AlsAnimationInstance.dal's counterpart.

Source: Source/ALS/Private/AlsAnimationInstance.cpp (line numbers "AI:" below), Public/AlsAnimationInstance.h, State/*.h, Notifies/*,
Utility/AlsMath.h, AlsRotation.h, AlsVector.h.

This file deliberately does not import the dal package (``als_lean_oracle`` is its only neighbour: UE math re-derived there).
It is written like the C++ -- one function per ALS function, the same state structs (as dicts of dicts), double precision,
the frame order of the engine: events of the notifies, NativeUpdateAnimation, NativeThreadSafeUpdateAnimation, the graph's
Call(Self.X) entries, NativePostUpdateAnimation -- so it also checks the way the .dal runs the post-update of a frame.

The host commands the C++ issues (PlaySlotAnimationAsDynamicMontage, StopMontagesWithSlot, SnapshotPose, Montage stop of the early
blend out) are collected in ``commands`` as ``(name, args)``, the form the scripted host of the tests records them in.
Movement-base compensation is dead without moving bases (``MovementBase.bHasRelativeLocation`` is never true) and left out, as in the
.dal.  The thread rules, which the .dal gets from the language, are not modelled: every function runs where the frame puts it.
"""

from __future__ import annotations

import math
from collections import Counter

from oracles.als_lean_oracle import UE_KINDA_SMALL_NUMBER, UE_LN2, UE_SMALL_NUMBER, dot, inv_exp_approx, quat_unrotate_vector, quat_yaw_degrees, unwind_degrees

ZERO_ANIMWEIGHT_THRESH = 0.00001
CCW_THRESHOLD = 5.0          # UAlsRotation::CounterClockwiseRotationAngleThreshold

TAGS = {
    "ViewMode": "Als.ViewMode.", "LocomotionMode": "Als.LocomotionMode.", "RotationMode": "Als.RotationMode.", "Stance": "Als.Stance.",
    "Gait": "Als.Gait.", "OverlayMode": "Als.OverlayMode.", "LocomotionAction": "Als.LocomotionAction.", "GroundedEntryMode": "Als.GroundedEntryMode.",
}

SLOT_NONE, SLOT_STANDING, SLOT_CROUCHING = 0, 1, 2                      # the FName slots of the turn in place state (the .dal's enum AlsTurnInPlaceSlot)
MONTAGE_BLEND_INERTIALIZATION = 1                                       # EMontageBlendMode::Inertialization



def tag(kind, member):
    return TAGS[kind] + member if member else ""


# ---- UE math (FMath / UAlsMath / UAlsRotation / UAlsVector) re-derived -------------------------------------------------------

def clamp(x, lo, hi):              # FMath::Clamp (UE 5.8): Max(Min(X, MaxValue), MinValue), with Min = (A < B) ? A : B and Max = (B < A) ? A : B
    m = x if x < hi else hi
    return m if lo < m else lo


def clamp01(x):                    # UAlsMath::Clamp01
    return (x if x < 1.0 else 1.0) if x > 0.0 else 0.0


def lerp(a, b, t):
    return a + t * (b - a)


def lerp_clamped(a, b, t):         # UAlsMath::LerpClamped
    return a + (b - a) * clamp01(t)


def sign(x):                       # FMath::Sign
    return 1.0 if x > 0.0 else (-1.0 if x < 0.0 else 0.0)


def damper_exact_alpha(dt, half_life):                      # AlsMath.h:84
    return 1.0 - inv_exp_approx(UE_LN2 / (half_life + UE_SMALL_NUMBER) * dt)


def damper_exact(current, target, dt, half_life):          # AlsMath.h:91
    return lerp(current, target, damper_exact_alpha(dt, half_life))


def remap_angle_ccw(angle):                                 # AlsRotation.h:52
    return angle - 360.0 if angle > 180.0 - CCW_THRESHOLD else angle


def lerp_angle(frm, to, ratio):                             # AlsRotation.h:83
    delta = remap_angle_ccw(unwind_degrees(to - frm))
    return unwind_degrees(frm + delta * ratio)


def damper_exact_angle(current, target, dt, half_life):    # AlsRotation.h:144
    delta = unwind_degrees(target - current)
    if abs(delta) <= UE_KINDA_SMALL_NUMBER:
        return target
    delta = remap_angle_ccw(delta)
    return unwind_degrees(current + delta * damper_exact_alpha(dt, half_life))


def range_pct(lo, hi, x):                                   # FMath::GetRangePct
    d = hi - lo
    if abs(d) <= UE_SMALL_NUMBER:                           # IsNearlyZero(Divisor)
        return 1.0 if x >= hi else 0.0
    return (x - lo) / d


def mapped_range_clamped(in_lo, in_hi, out_lo, out_hi, x):  # FMath::GetMappedRangeValueClamped
    return lerp(out_lo, out_hi, clamp(range_pct(in_lo, in_hi, x), 0.0, 1.0))


def clamp_magnitude01(v):                                   # AlsVector.h:86
    sq = dot(v, v)
    if sq <= 1.0:
        return v
    s = 1.0 / math.sqrt(sq)
    return (v[0] * s, v[1] * s, v[2] * s)


def is_full_weight(w):
    return w >= 1.0 - ZERO_ANIMWEIGHT_THRESH


def is_relevant(w):
    return w > ZERO_ANIMWEIGHT_THRESH


def critical_spring_damper(x, v, target, smoothing_time, dt):
    """SpringMath::CriticalSpringDamper (Holden's exact critical damper, y = 2 / smoothing_time) with FMath::InvExpApprox for exp(-y dt)."""
    if smoothing_time < UE_SMALL_NUMBER:
        return target, 0.0
    y = 2.0 / max(smoothing_time, UE_SMALL_NUMBER)
    j0 = x - target
    j1 = v + j0 * y
    e = inv_exp_approx(y * dt)
    return e * (j0 + j1 * dt) + target, e * (v - j1 * y * dt)


def movement_direction(angle, forward_half_angle, threshold):    # UAlsMath::CalculateMovementDirection, AlsMath.cpp:11
    if -forward_half_angle - threshold <= angle <= forward_half_angle + threshold:
        return "Forward"
    if forward_half_angle - threshold <= angle <= 180.0 - forward_half_angle + threshold:
        return "Right"
    if angle <= -(forward_half_angle - threshold) and angle >= -(180.0 - forward_half_angle + threshold):
        return "Left"
    return "Backward"


def v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_len_sq(a):
    return a[0] * a[0] + a[1] * a[1] + a[2] * a[2]


def v_scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def v_lerp(a, b, t):                                        # FMath::Lerp(FVector, FVector, float)
    return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2]))


def v_normalize_in_place(v):                                # FVector::Normalize(): SquareSum > SMALL_NUMBER, else the vector is left as it is
    sq = v_len_sq(v)
    if sq > UE_SMALL_NUMBER:
        s = 1.0 / math.sqrt(sq)
        return (v[0] * s, v[1] * s, v[2] * s)
    return v


def rotator_of(q):
    """FQuat::Rotator for the actors ALS has: (pitch, yaw, roll) with the exact formulas."""
    x, y, z, w = q
    singularity = z * x - w * y
    yaw = quat_yaw_degrees(q)
    if singularity < -0.4999995:
        return (-90.0, yaw, unwind_degrees(-yaw - 2.0 * math.atan2(x, w) * 180.0 / math.pi))
    if singularity > 0.4999995:
        return (90.0, yaw, unwind_degrees(yaw - 2.0 * math.atan2(x, w) * 180.0 / math.pi))
    pitch = math.asin(2.0 * singularity) * 180.0 / math.pi
    roll = math.atan2(-2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y)) * 180.0 / math.pi
    return (pitch, yaw, roll)


# ---- FQuat / FTransform re-derived (a transform is (location, rotation quaternion xyzw, scale)) -------------------------------

QUAT_ID = (0.0, 0.0, 0.0, 1.0)
UP = (0.0, 0.0, 1.0)


def q_mul(a, b):                                            # FQuat::operator*: b is applied first
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def q_inverse(q):                                           # FQuat::Inverse (normalized quaternions)
    return (-q[0], -q[1], -q[2], q[3])


def q_rotate(q, v):                                         # FQuat::RotateVector: V + W * T + Q ^ T with T = 2 * (Q ^ V)
    qv = (q[0], q[1], q[2])
    t = v_scale(_cross(qv, v), 2.0)
    c = _cross(qv, t)
    return (v[0] + q[3] * t[0] + c[0], v[1] + q[3] * t[1] + c[1], v[2] + q[3] * t[2] + c[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def q_normalize(q):                                         # FQuat::Normalize(): identity when the square sum is below SMALL_NUMBER
    sq = q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]
    if sq >= UE_SMALL_NUMBER:
        s = 1.0 / math.sqrt(sq)
        return (q[0] * s, q[1] * s, q[2] * s, q[3] * s)
    return QUAT_ID


def q_from_axis_angle(axis, rad):                           # FQuat(Axis, AngleRad)
    s, c = math.sin(0.5 * rad), math.cos(0.5 * rad)
    return (s * axis[0], s * axis[1], s * axis[2], c)


def q_twist_angle(q, axis):                                 # FQuat::GetTwistAngle: UnwindRadians(2 atan2(axis . xyz, w))
    a = 2.0 * math.atan2(axis[0] * q[0] + axis[1] * q[1] + axis[2] * q[2], q[3])
    while a > math.pi:
        a -= 2.0 * math.pi
    while a < -math.pi:
        a += 2.0 * math.pi
    return a


def q_fast_lerp(a, b, alpha):                               # FQuat::FastLerp: B * Alpha + A * (Bias * (1 - Alpha)), Bias = sign of A | B (>= 0: +1)
    d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]
    bias = 1.0 if d >= 0.0 else -1.0
    return tuple(b[i] * alpha + a[i] * (bias * (1.0 - alpha)) for i in range(4))


def q_equals(a, b, tol=UE_KINDA_SMALL_NUMBER):              # FQuat::Equals: component-wise within the tolerance, or the same with the sign of B flipped
    return (all(abs(a[i] - b[i]) <= tol for i in range(4))) or (all(abs(a[i] + b[i]) <= tol for i in range(4)))


def t_position(t, v):                                       # FTransform::TransformPosition: Rotation.RotateVector(Scale3D * V) + Translation
    return v_add(q_rotate(t[1], (t[2][0] * v[0], t[2][1] * v[1], t[2][2] * v[2])), t[0])


def t_rotation(t, q):                                       # FTransform::TransformRotation: Rotation * Q
    return q_mul(t[1], q)


def t_inverse(t):                                           # FTransform::Inverse
    inv_rotation = q_inverse(t[1])
    inv_scale = tuple((1.0 / s) if abs(s) > UE_SMALL_NUMBER else 0.0 for s in t[2])           # GetSafeScaleReciprocal
    inv_translation = q_rotate(inv_rotation, tuple(inv_scale[i] * -t[0][i] for i in range(3)))
    return (inv_translation, inv_rotation, inv_scale)


def t_equals_no_scale_identity(t):                          # FTransform::EqualsNoScale(Identity): rotation and translation within KINDA_SMALL_NUMBER
    return q_equals(t[1], QUAT_ID) and all(abs(c) <= UE_KINDA_SMALL_NUMBER for c in t[0])


def angle_between_signed_xy(frm, to):                       # UAlsVector::AngleBetweenSignedXY (AlsVector.h:179): FVector2f::Normalize leaves a tiny vector alone
    def norm2(v):
        sq = v[0] * v[0] + v[1] * v[1]
        if sq > UE_SMALL_NUMBER:
            k = 1.0 / math.sqrt(sq)
            return (v[0] * k, v[1] * k)
        return (v[0], v[1])

    a, b = norm2(frm), norm2(to)
    d = a[0] * b[0] + a[1] * b[1]
    cross = a[0] * b[1] - a[1] * b[0]
    return math.degrees(math.acos(max(-1.0, min(1.0, d)))) * sign(cross)


def new_foot(thigh):
    return dict(LockAmount=0.0, ThighAxisPelvisSpace=thigh, TargetLocationWorldSpace=(0.0, 0.0, 0.0), TargetRotationWorldSpace=QUAT_ID,
                LockLocationWorldSpace=(0.0, 0.0, 0.0), LockRotationWorldSpace=QUAT_ID, LockLocationMovementBaseSpace=(0.0, 0.0, 0.0),
                LockRotationMovementBaseSpace=QUAT_ID, LockLocation=(0.0, 0.0, 0.0), LockRotation=QUAT_ID, FinalLocation=(0.0, 0.0, 0.0),
                FinalRotation=QUAT_ID)


# ---- the instance -------------------------------------------------------------------------------------------------------------

class AlsAnimOracle:
    """``settings`` (UAlsAnimationInstanceSettings): a dict with the members the ported code reads, curves as Python callables:

    general: moving_smooth_speed_threshold, lean_half_life, use_hand_ik, use_foot_ik
    head: pitch_hl, yaw_hl, switch_hl, fp_pitch_hl, fp_yaw_hl
    grounded: velocity_blend_hl, yaw_offset_curves (forward, backward, left, right)
    standing: animated_walk, animated_run, animated_sprint, stride_walk, stride_run, pivot_threshold
    crouching: animated_speed, stride
    in_air: lean_curve, ground_prediction_curve
    foot_lock: allow, thigh_angle_limit, foot_angle_limit
    rotate_in_place: view_yaw_threshold, fp_view_yaw_threshold, reference_view_yaw_speed (2), play_rate (2)
    transitions: quick_stop_blend_in, quick_stop_blend_out, quick_stop_play_rate (2), quick_stop_start_time,
                 standing_left, standing_right, crouching_left, crouching_right (sequence names)
    dynamic_transitions: foot_lock_distance_threshold, blend_duration, play_rate, standing/crouching left/right
    turn_in_place: view_yaw_angle_threshold, view_yaw_speed_threshold, view_yaw_angle_to_activation_delay (2), turn_180_angle_threshold,
                   blend_duration, turns: {"standing_90_left" ... "crouching_180_right": {sequence, play_rate, scale_by_angle, animated_turn_angle}}
    teleport_distance_threshold   (Character->GetSettings())
    """

    def __init__(self, settings):
        self.S = settings
        s = {}
        s["tags"] = {"ViewMode": tag("ViewMode", "ThirdPerson"), "LocomotionMode": tag("LocomotionMode", "Grounded"),
                     "RotationMode": tag("RotationMode", "ViewDirection"), "Stance": tag("Stance", "Standing"), "Gait": tag("Gait", "Walking"),
                     "OverlayMode": tag("OverlayMode", "Default"), "LocomotionAction": "", "GroundedEntryMode": ""}
        s["Layering"] = {}
        s["Pose"] = dict(GroundedAmount=1.0, InAirAmount=0.0, StandingAmount=1.0, CrouchingAmount=0.0, MovingAmount=0.0, GaitAmount=0.0,
                         GaitWalkingAmount=0.0, GaitRunningAmount=0.0, GaitSprintingAmount=0.0, UnweightedGaitAmount=0.0,
                         UnweightedGaitWalkingAmount=0.0, UnweightedGaitRunningAmount=0.0, UnweightedGaitSprintingAmount=0.0)
        s["View"] = dict(RotationWorldSpace=(0.0, 0.0, 0.0), YawAngle=0.0, YawSpeed=0.0, PitchAngle=0.0, PitchAmount=0.5, HeadBlendAmount=1.0)
        s["Spine"] = dict(bSpineRotationAllowed=False, SpineAmount=0.0, SpineAmountScale=1.0, SpineAmountBias=0.0, LastYawAngle=0.0,
                          LastYawAngleWorldSpace=0.0, YawAngle=0.0, FinalYawAngle=0.0)
        s["Head"] = dict(bInitializationRequired=True, bSwitchingLookSides=False, PitchAngle=0.0, YawAngle=0.0, YawVelocity=0.0, YawAmount=0.5)
        s["Loco"] = dict(bHasInput=False, InputYawAngleWorldSpace=0.0, Speed=0.0, VelocityWorldSpace=(0.0, 0.0, 0.0), VelocityYawAngleWorldSpace=0.0,
                         AccelerationWorldSpace=(0.0, 0.0, 0.0), MaxAcceleration=0.0, MaxBrakingDeceleration=0.0, WalkableFloorAngleCos=0.0,
                         bMoving=False, bMovingSmooth=False, TargetYawAngleWorldSpace=0.0, LocationWorldSpace=(0.0, 0.0, 0.0),
                         RotationWorldSpace=(0.0, 0.0, 0.0), RotationQuaternionWorldSpace=(0.0, 0.0, 0.0, 1.0), YawVelocityWorldSpace=0.0,
                         ScaleWorldSpace=1.0, CapsuleRadius=0.0, CapsuleHalfHeight=0.0)
        s["Lean"] = dict(RightAmount=0.0, ForwardAmount=0.0)
        s["Grounded"] = dict(HipsDirectionLockAmount=0.0, HipsDirection="Forward",
                             VelocityBlend=dict(bInitializationRequired=True, ForwardAmount=0.0, BackwardAmount=0.0, LeftAmount=0.0, RightAmount=0.0),
                             MovementDirection=dict(bForward=True, bBackward=False, bLeft=False, bRight=False),
                             RotationYawOffsets=dict(ForwardAngle=0.0, BackwardAngle=0.0, LeftAngle=0.0, RightAngle=0.0))
        s["Standing"] = dict(StrideBlendAmount=0.0, WalkRunBlendAmount=0.0, PlayRate=1.0, SprintBlockAmount=0.0, SprintTime=0.0,
                             SprintAccelerationAmount=0.0, bPivotActive=False)
        s["Crouching"] = dict(StrideBlendAmount=0.0, PlayRate=1.0)
        s["InAir"] = dict(VerticalVelocityWorldSpace=0.0, bJumpRequested=False, bJumped=False, JumpPlayRate=1.0, GroundPredictionAmount=1.0)
        s["Feet"] = dict(PelvisRotation=QUAT_ID, FootPlantedAmount=0.0, FeetCrossingAmount=0.0, bValid=False, bBecameValid=False,
                         Left=new_foot((0.0, 0.0, -1.0)), Right=new_foot((0.0, 0.0, 1.0)))       # AlsFeetState.h:48
        s["Transitions"] = dict(bTransitionsAllowed=False, QueuedTransitionSequence=None, QueuedTransitionBlendInDuration=0.0,
                                QueuedTransitionBlendOutDuration=0.0, QueuedTransitionPlayRate=1.0, QueuedTransitionStartTime=0.0,
                                bStopTransitionsQueued=False, QueuedStopTransitionsBlendOutDuration=-1.0)
        s["DynamicTransitions"] = dict(bUpdatedThisFrame=False, FrameDelay=0)
        s["RotateInPlace"] = dict(bUpdatedThisFrame=False, bRotatingLeft=False, bRotatingRight=False, PlayRate=1.0)
        s["TurnInPlace"] = dict(bUpdatedThisFrame=False, ActivationDelay=0.0, QueuedSettings=None, QueuedSlotName=SLOT_NONE, QueuedTurnYawAngle=0.0, PlayRate=1.0)
        s["Ragdolling"] = dict(FlailPlayRate=1.0, bFinalRagdollPoseValid=False)
        self.s = s
        self.bPendingUpdate = True
        self.TeleportedTime = 0.0
        self.DeltaTimeWithoutTimeDilation = 1.0
        self.PreviousGlobalTimeDilation = 1.0
        self.f = None          # the inputs of the frame being processed
        self.before_post = {}
        self.dt = 1.0
        self.commands = []     # the host commands of the frame so far: (name, args)
        self.hits = Counter()  # how often each interesting branch ran (the tests check that the random sequences get into them)
        self._snapshot_requested = False

    # ---- NativeInitializeAnimation (AI:29-88) ---------------------------------------------------------------------------
    def native_initialize_animation(self, thigh_left, thigh_right):
        """``thigh_*``: the reference-pose translation of the first bone below the pelvis on the way up from the foot, None without a path."""
        for foot, thigh in ((self.s["Feet"]["Left"], thigh_left), (self.s["Feet"]["Right"], thigh_right)):
            if thigh is not None:
                foot["ThighAxisPelvisSpace"] = v_normalize_in_place(thigh)         # AI:80-81

    # ---- the frame, in the engine's order -------------------------------------------------------------------------------
    def frame(self, f, calls=(), peer_calls=()):
        """One frame in the engine's order: the calls of the character (``peer_calls``, method names: they were queued during its tick), the
        notify events, NativeUpdateAnimation + NativeThreadSafeUpdateAnimation, the graph's entry calls ``[(name, kwargs)]``,
        NativePostUpdateAnimation.  Returns ``flat()`` after all of it; ``before_post`` is what the graph left."""
        self.f = f
        self.dt = f["dt"]
        self.commands = []
        for name in peer_calls:
            getattr(self, name)()
        for ev in f.get("events", ()):
            self.notify_event(*ev)
        self.native_update_animation(f["dt"])
        self.native_thread_safe_update_animation(f["dt"])
        for name, kwargs in calls:
            getattr(self, name)(**kwargs)
        self.before_post = self.flat()
        self.native_post_update_animation()
        return self.flat()

    def native_update_animation(self, dt):                  # AI:98
        s, c, f = self.s, self.f["char"], self.f
        t = s["tags"]
        time_dilation = self.PreviousGlobalTimeDilation * f["actor_time_dilation"]                     # AI:114
        self.DeltaTimeWithoutTimeDilation = dt / time_dilation if time_dilation > UE_SMALL_NUMBER else f["real_delta_time"]   # AI:115
        self.PreviousGlobalTimeDilation = f["time_dilation"]                                           # AI:116
        t["ViewMode"], t["LocomotionMode"], t["RotationMode"] = c["view_mode"], c["locomotion_mode"], c["rotation_mode"]
        t["Stance"], t["Gait"], t["OverlayMode"] = c["stance"], c["gait"], c["overlay_mode"]
        if t["LocomotionAction"] != c["locomotion_action"]:
            t["LocomotionAction"] = c["locomotion_action"]
            t["GroundedEntryMode"] = ""                       # ResetGroundedEntryMode
        previous_location = s["Loco"]["LocationWorldSpace"]
        # RefreshMovementBaseOnGameThread: nothing without moving bases
        s["View"]["RotationWorldSpace"] = tuple(c["view"]["Rotation"])      # RefreshViewOnGameThread, AI:377
        s["View"]["YawSpeed"] = c["view"]["YawSpeed"]
        self.refresh_locomotion_on_game_thread(dt)
        # RefreshInAirOnGameThread, AI:1099
        ia = s["InAir"]
        ia["bJumped"] = (not self.bPendingUpdate) and (ia["bJumped"] or ia["bJumpRequested"])
        ia["bJumpRequested"] = False
        self.refresh_feet_on_game_thread()
        if t["LocomotionAction"] == tag("LocomotionAction", "Ragdolling"):                  # RefreshRagdollingOnGameThread, AI:2048
            rv = c["ragdoll_velocity"]
            s["Ragdolling"]["FlailPlayRate"] = clamp01(math.sqrt(dot(rv, rv)) / 1000.0)
        if not self.bPendingUpdate and v_len_sq(v_sub(s["Loco"]["LocationWorldSpace"], previous_location)) > self.S["teleport_distance_threshold"] ** 2:
            self.TeleportedTime = f["time"]

    def native_thread_safe_update_animation(self, dt):      # AI:170
        s = self.s
        s["DynamicTransitions"]["bUpdatedThisFrame"] = False
        s["RotateInPlace"]["bUpdatedThisFrame"] = False
        s["TurnInPlace"]["bUpdatedThisFrame"] = False
        self.refresh_layering()
        self.refresh_pose()
        self.refresh_view(dt)
        self.refresh_feet(dt)
        s["Transitions"]["bTransitionsAllowed"] = is_full_weight(self.curve("AllowTransitions"))    # RefreshTransitions, AI:1675

    def native_post_update_animation(self):                 # AI:194
        self.play_queued_transition_animation()
        self.play_queued_turn_in_place_animation()
        self.stop_queued_transition_and_turn_in_place_animations()
        if self._snapshot_requested:                        # the SnapshotPose command of the frame has run
            self._snapshot_requested = False
            self.s["Ragdolling"]["bFinalRagdollPoseValid"] = True
        self.bPendingUpdate = not self.f["actor_ready"]

    # ---- public calls of the character and the notifies ------------------------------------------------------------------
    def jump(self):                                         # AI.h:402 (the .dal queues it: it arrives at the start of the next update)
        self.s["InAir"]["bJumpRequested"] = True

    def snapshot_final_ragdoll_pose(self):                  # AI:2064
        self.hits["snapshot"] += 1
        self.commands.append(("snapshot_pose", ("RagdollingState.FinalRagdollPose",)))
        self._snapshot_requested = True

    def set_grounded_entry_mode(self, new_mode):            # AI.h:382
        self.s["tags"]["GroundedEntryMode"] = new_mode

    def notify_event(self, kind, name, payload=None, instance=None):
        """The events the notifies of the instance raise (AlsAnimNotify_SetGroundedEntryMode.cpp:30)."""
        payload = payload or {}
        if kind == "notify" and name == "AlsAnimNotify_SetGroundedEntryMode":
            self.hits["grounded_entry_mode_notify"] += 1
            self.set_grounded_entry_mode(payload.get("GroundedEntryMode", ""))

    # ---- helpers ------------------------------------------------------------------------------------------------------------
    def curve(self, name):
        return self.f.get("curves", {}).get(name, 0.0)

    def curve01(self, name):                                # GetCurveValueClamped01
        return clamp01(self.curve(name))

    # ---- layering / pose / view / spine ------------------------------------------------------------------------------------------
    def refresh_layering(self):                             # AI:288
        L = self.s["Layering"]
        g = self.curve
        L["HeadBlendAmount"], L["HeadAdditiveBlendAmount"], L["HeadSlotBlendAmount"] = g("LayerHead"), g("LayerHeadAdditive"), g("LayerHeadSlot")
        for side in ("Left", "Right"):
            L[f"Arm{side}BlendAmount"] = g(f"LayerArm{side}")
            L[f"Arm{side}AdditiveBlendAmount"] = g(f"LayerArm{side}Additive")
            L[f"Arm{side}SlotBlendAmount"] = g(f"LayerArm{side}Slot")
            L[f"Arm{side}LocalSpaceBlendAmount"] = g(f"LayerArm{side}LocalSpace")
            L[f"Arm{side}MeshSpaceBlendAmount"] = 0.0 if is_full_weight(L[f"Arm{side}LocalSpaceBlendAmount"]) else 1.0
        L["HandLeftBlendAmount"], L["HandRightBlendAmount"] = g("LayerHandLeft"), g("LayerHandRight")
        L["SpineBlendAmount"], L["SpineAdditiveBlendAmount"], L["SpineSlotBlendAmount"] = g("LayerSpine"), g("LayerSpineAdditive"), g("LayerSpineSlot")
        L["PelvisBlendAmount"], L["PelvisSlotBlendAmount"] = g("LayerPelvis"), g("LayerPelvisSlot")
        L["LegsBlendAmount"], L["LegsSlotBlendAmount"] = g("LayerLegs"), g("LayerLegsSlot")

    def refresh_pose(self):                                 # AI:337
        P = self.s["Pose"]
        g = self.curve
        P["GroundedAmount"], P["InAirAmount"] = g("PoseGrounded"), g("PoseInAir")
        P["StandingAmount"], P["CrouchingAmount"] = g("PoseStanding"), g("PoseCrouching")
        P["MovingAmount"] = g("PoseMoving")
        P["GaitAmount"] = clamp(g("PoseGait"), 0.0, 3.0)
        P["GaitWalkingAmount"] = clamp01(P["GaitAmount"])
        P["GaitRunningAmount"] = clamp01(P["GaitAmount"] - 1.0)
        P["GaitSprintingAmount"] = clamp01(P["GaitAmount"] - 2.0)
        P["UnweightedGaitAmount"] = P["GaitAmount"] / P["GroundedAmount"] if P["GroundedAmount"] > UE_SMALL_NUMBER else P["GaitAmount"]
        P["UnweightedGaitWalkingAmount"] = clamp01(P["UnweightedGaitAmount"])
        P["UnweightedGaitRunningAmount"] = clamp01(P["UnweightedGaitAmount"] - 1.0)
        P["UnweightedGaitSprintingAmount"] = clamp01(P["UnweightedGaitAmount"] - 2.0)

    def refresh_view(self, dt):                             # AI:387
        s = self.s
        V, loco = s["View"], s["Loco"]
        if not s["tags"]["LocomotionAction"]:
            V["YawAngle"] = unwind_degrees(V["RotationWorldSpace"][1] - loco["RotationWorldSpace"][1])
            V["PitchAngle"] = unwind_degrees(V["RotationWorldSpace"][0] - loco["RotationWorldSpace"][0])
            V["PitchAmount"] = 0.5 - V["PitchAngle"] / 180.0
        view_amount = 1.0 - self.curve01("ViewBlock")
        aiming_amount = self.curve01("PoseAiming")
        V["HeadBlendAmount"] = view_amount * (1.0 - aiming_amount)
        self.refresh_spine(view_amount * aiming_amount, dt)

    def is_spine_rotation_allowed(self):                    # AI:408
        t = self.s["tags"]
        return t["RotationMode"] == tag("RotationMode", "Aiming") or t["ViewMode"] == tag("ViewMode", "FirstPerson")

    def refresh_spine(self, spine_blend_amount, dt):        # AI:413
        s = self.s
        sp, V, loco = s["Spine"], s["View"], s["Loco"]
        allowed = self.is_spine_rotation_allowed()
        if sp["bSpineRotationAllowed"] != allowed:
            sp["bSpineRotationAllowed"] = not sp["bSpineRotationAllowed"]
            if sp["bSpineRotationAllowed"]:
                if is_full_weight(sp["SpineAmount"]):
                    sp["SpineAmountScale"], sp["SpineAmountBias"] = 1.0, 0.0
                else:
                    sp["SpineAmountScale"] = 1.0 / (1.0 - sp["SpineAmount"])
                    sp["SpineAmountBias"] = -sp["SpineAmount"] * sp["SpineAmountScale"]
            else:
                sp["SpineAmountScale"] = 1.0 if not is_relevant(sp["SpineAmount"]) else 1.0 / sp["SpineAmount"]
                sp["SpineAmountBias"] = 0.0
            sp["LastYawAngle"] = sp["YawAngle"]
            sp["LastYawAngleWorldSpace"] = loco["RotationWorldSpace"][1]
        if sp["bSpineRotationAllowed"]:
            if self.bPendingUpdate or is_full_weight(sp["SpineAmount"]):
                sp["SpineAmount"] = 1.0
                sp["YawAngle"] = V["YawAngle"]
            else:
                sp["SpineAmount"] = damper_exact(sp["SpineAmount"], 1.0, dt, 0.1)
                sp["YawAngle"] = lerp_angle(sp["LastYawAngle"], V["YawAngle"], sp["SpineAmount"] * sp["SpineAmountScale"] + sp["SpineAmountBias"])
        else:
            if self.bPendingUpdate or not is_relevant(sp["SpineAmount"]):
                sp["SpineAmount"] = 0.0
                sp["YawAngle"] = 0.0
            else:
                half_life, reference_view_yaw_speed = 0.7, 40.0
                multiplier = reference_view_yaw_speed / V["YawSpeed"] if V["YawSpeed"] > reference_view_yaw_speed else 1.0
                sp["SpineAmount"] = damper_exact(sp["SpineAmount"], 0.0, dt, half_life * multiplier)
                offset = unwind_degrees(sp["LastYawAngleWorldSpace"] - loco["RotationWorldSpace"][1])
                offset = clamp(offset, -30.0, 30.0)
                sp["LastYawAngleWorldSpace"] = unwind_degrees(offset + loco["RotationWorldSpace"][1])
                sp["YawAngle"] = lerp_angle(0.0, sp["LastYawAngle"] + offset, sp["SpineAmount"] * sp["SpineAmountScale"] + sp["SpineAmountBias"])
        sp["FinalYawAngle"] = lerp_angle(0.0, sp["YawAngle"], spine_blend_amount)

    # ---- head -----------------------------------------------------------------------------------------------------------------------
    def initialize_head(self):                              # AI:523
        self.s["Head"]["bInitializationRequired"] = True

    def refresh_head(self):                                 # AI:528
        s, S = self.s, self.S["head"]
        H, loco, V = s["Head"], s["Loco"], s["View"]
        dt = self.dt
        first_person = s["tags"]["ViewMode"] == tag("ViewMode", "FirstPerson")
        H["YawAngle"] -= loco["YawVelocityWorldSpace"] * dt
        H["YawAngle"] = clamp(H["YawAngle"], -180.0, 180.0)
        ready_to_switch = False
        if s["tags"]["RotationMode"] == tag("RotationMode", "VelocityDirection"):
            target_pitch = 0.0
            target_yaw = unwind_degrees((loco["InputYawAngleWorldSpace"] if loco["bHasInput"] else loco["TargetYawAngleWorldSpace"])
                                        - loco["RotationWorldSpace"][1])
            if abs(loco["YawVelocityWorldSpace"]) > 20.0:
                target_yaw = sign(loco["YawVelocityWorldSpace"]) * abs(target_yaw)
            elif abs(target_yaw) > 180.0 - CCW_THRESHOLD:
                target_yaw = H["YawAngle"]
            H["bSwitchingLookSides"] = False
        else:
            target_pitch = V["PitchAngle"]
            target_yaw = clamp(V["YawAngle"], -180.0 + CCW_THRESHOLD, 180.0 - CCW_THRESHOLD)
            if not first_person:
                if H["YawAngle"] >= 90.0 and target_yaw <= -160.0:
                    target_yaw = 180.0 - CCW_THRESHOLD
                    ready_to_switch = not loco["bHasInput"]
                elif H["YawAngle"] <= -90.0 and target_yaw >= 160.0:
                    target_yaw = -180.0 + CCW_THRESHOLD
                    ready_to_switch = not loco["bHasInput"]
        if H["bInitializationRequired"]:
            H["bInitializationRequired"] = False
            H["PitchAngle"], H["YawAngle"], H["YawVelocity"], H["bSwitchingLookSides"] = target_pitch, target_yaw, 0.0, False
        else:
            effective_dt = self.DeltaTimeWithoutTimeDilation if first_person else dt
            H["PitchAngle"] = damper_exact_angle(H["PitchAngle"], target_pitch, effective_dt,
                                                 S["fp_pitch_hl"] if first_person else S["pitch_hl"])
            if ready_to_switch:
                H["bSwitchingLookSides"] = True
            elif abs(unwind_degrees(target_yaw - H["YawAngle"])) <= 10.0:
                H["bSwitchingLookSides"] = False
            half_life = S["switch_hl"] if H["bSwitchingLookSides"] else (S["fp_yaw_hl"] if first_person else S["yaw_hl"])
            H["YawAngle"], H["YawVelocity"] = critical_spring_damper(H["YawAngle"], H["YawVelocity"], target_yaw, half_life / UE_LN2, dt)
        H["YawAmount"] = H["YawAngle"] / 360.0 + 0.5

    # ---- locomotion -----------------------------------------------------------------------------------------------------------------
    def refresh_locomotion_on_game_thread(self, dt):        # AI:681
        s, f = self.s, self.f
        L, ch = s["Loco"], f["char"]["loc"]
        can_rate = (not self.bPendingUpdate) and dt > UE_SMALL_NUMBER
        L["bHasInput"] = ch["bHasInput"]
        L["InputYawAngleWorldSpace"] = ch["InputYawAngle"]
        previous_velocity = L["VelocityWorldSpace"]
        L["Speed"] = ch["Speed"]
        L["VelocityWorldSpace"] = tuple(ch["Velocity"])
        L["VelocityYawAngleWorldSpace"] = ch["VelocityYawAngle"]
        L["AccelerationWorldSpace"] = v_scale(v_sub(L["VelocityWorldSpace"], previous_velocity), 1.0 / dt) if can_rate else (0.0, 0.0, 0.0)
        L["MaxAcceleration"] = f["max_acceleration"]
        L["MaxBrakingDeceleration"] = f["max_braking_deceleration"]
        L["WalkableFloorAngleCos"] = f["walkable_floor_z"]
        L["bMoving"] = ch["bMoving"]
        L["bMovingSmooth"] = (ch["bHasInput"] and ch["bHasVelocity"]) or ch["Speed"] > self.S["general"]["moving_smooth_speed_threshold"]
        L["TargetYawAngleWorldSpace"] = ch["TargetYawAngle"]
        previous_yaw = L["RotationWorldSpace"][1]
        L["LocationWorldSpace"] = tuple(f["location"])
        L["RotationQuaternionWorldSpace"] = tuple(f["rotation"])
        L["RotationWorldSpace"] = rotator_of(f["rotation"])
        L["YawVelocityWorldSpace"] = unwind_degrees(L["RotationWorldSpace"][1] - previous_yaw) / dt if can_rate else 0.0
        L["ScaleWorldSpace"] = f["scale"]
        L["CapsuleRadius"] = f["capsule_radius"]
        L["CapsuleHalfHeight"] = f["capsule_half_height"]

    def initialize_lean(self):                              # AI:792
        self.s["Lean"].update(RightAmount=0.0, ForwardAmount=0.0)

    def get_velocity(self):                                 # AI:824
        L = self.s["Loco"]
        return quat_unrotate_vector(L["RotationQuaternionWorldSpace"], L["VelocityWorldSpace"])

    def get_acceleration_amount(self):                      # AI:829
        L = self.s["Loco"]
        max_acc = L["MaxAcceleration"] if dot(L["AccelerationWorldSpace"], L["VelocityWorldSpace"]) >= 0.0 else L["MaxBrakingDeceleration"]
        if max_acc <= UE_KINDA_SMALL_NUMBER:
            return (0.0, 0.0)
        a = quat_unrotate_vector(L["RotationQuaternionWorldSpace"], L["AccelerationWorldSpace"])
        c = clamp_magnitude01((a[0] / max_acc, a[1] / max_acc, a[2] / max_acc))
        return (c[0], c[1])

    # ---- grounded -------------------------------------------------------------------------------------------------------------------
    def reset_grounded_entry_mode(self):
        self.s["tags"]["GroundedEntryMode"] = ""

    def initialize_grounded(self):                          # AI:798
        self.s["Grounded"]["VelocityBlend"]["bInitializationRequired"] = True

    def refresh_grounded(self):                             # AI:803
        self.refresh_velocity_blend()
        self.refresh_grounded_lean()

    def refresh_velocity_blend(self):                       # AI:850
        vb = self.s["Grounded"]["VelocityBlend"]
        d = self.get_velocity()
        target = (0.0, 0.0, 0.0)
        sq = v_len_sq(d)
        if sq > UE_SMALL_NUMBER:                            # FVector::Normalize
            n = 1.0 / math.sqrt(sq)
            d = (d[0] * n, d[1] * n, d[2] * n)
            total = abs(d[0]) + abs(d[1]) + abs(d[2])
            target = (d[0] / total, d[1] / total, d[2] / total)
        half_life = self.S["grounded"]["velocity_blend_hl"]
        fwd, bwd = clamp01(target[0]), abs(clamp(target[0], -1.0, 0.0))
        left, right = abs(clamp(target[1], -1.0, 0.0)), clamp01(target[1])
        if vb["bInitializationRequired"] or half_life <= 0.0:
            vb["bInitializationRequired"] = False
            vb.update(ForwardAmount=fwd, BackwardAmount=bwd, LeftAmount=left, RightAmount=right)
        else:
            a = damper_exact_alpha(self.dt, half_life)
            vb["ForwardAmount"] = lerp(vb["ForwardAmount"], fwd, a)
            vb["BackwardAmount"] = lerp(vb["BackwardAmount"], bwd, a)
            vb["LeftAmount"] = lerp(vb["LeftAmount"], left, a)
            vb["RightAmount"] = lerp(vb["RightAmount"], right, a)

    def refresh_grounded_lean(self):                        # AI:903
        lean = self.s["Lean"]
        target = self.get_acceleration_amount()
        half_life = self.S["general"]["lean_half_life"]
        if self.bPendingUpdate or half_life <= 0.0:
            lean["RightAmount"], lean["ForwardAmount"] = target[1], target[0]
        else:
            a = damper_exact_alpha(self.dt, half_life)
            lean["RightAmount"] = lerp(lean["RightAmount"], target[1], a)
            lean["ForwardAmount"] = lerp(lean["ForwardAmount"], target[0], a)

    def refresh_grounded_movement(self):                    # AI:921
        s, G = self.s, self.s["Grounded"]
        G["HipsDirectionLockAmount"] = clamp(self.curve("HipsDirectionLock"), -1.0, 1.0)
        v = unwind_degrees(s["Loco"]["VelocityYawAngleWorldSpace"] - s["View"]["RotationWorldSpace"][1])
        if s["tags"]["RotationMode"] == tag("RotationMode", "VelocityDirection") or s["tags"]["Gait"] == tag("Gait", "Sprinting"):
            d = "Forward"
        else:
            d = movement_direction(v, 70.0, 5.0)
        G["MovementDirection"] = dict(bForward=d == "Forward", bBackward=d == "Backward", bLeft=d == "Left", bRight=d == "Right")
        curves = self.S["grounded"]["yaw_offset_curves"]
        G["RotationYawOffsets"] = dict(ForwardAngle=curves[0](v), BackwardAngle=curves[1](v), LeftAngle=curves[2](v), RightAngle=curves[3](v))

    def set_hips_direction(self, NewHipsDirection="Forward"):     # AI.h:392
        self.s["Grounded"]["HipsDirection"] = NewHipsDirection

    def initialize_standing_movement(self):                 # AI:979
        self.s["Standing"]["SprintTime"] = 0.0
        self.s["Standing"]["bPivotActive"] = False

    def refresh_standing_movement(self):                    # AI:985
        s, S = self.s, self.S["standing"]
        St, P, loco = s["Standing"], s["Pose"], s["Loco"]
        speed = loco["Speed"] / loco["ScaleWorldSpace"]
        St["StrideBlendAmount"] = lerp(S["stride_walk"](speed), S["stride_run"](speed), P["UnweightedGaitRunningAmount"])
        St["WalkRunBlendAmount"] = 0.0 if s["tags"]["Gait"] == tag("Gait", "Walking") else 1.0
        walk_run = lerp(speed / S["animated_walk"], speed / S["animated_run"], P["UnweightedGaitRunningAmount"])
        walk_run_sprint = lerp(walk_run, speed / S["animated_sprint"], P["UnweightedGaitSprintingAmount"])
        stride = St["StrideBlendAmount"]
        # C++: walk_run_sprint / stride clamped to [KINDA_SMALL, 3]; a zero stride divides to +inf (or NaN), which Clamp turns into the upper bound
        if stride == 0.0:
            St["PlayRate"] = 3.0
        else:
            St["PlayRate"] = clamp(walk_run_sprint / stride, UE_KINDA_SMALL_NUMBER, 3.0)
        St["SprintBlockAmount"] = self.curve01("SprintBlock")
        if s["tags"]["Gait"] != tag("Gait", "Sprinting"):
            St["SprintTime"] = 0.0
            St["SprintAccelerationAmount"] = 0.0
            return
        St["SprintTime"] = 0.5 if self.bPendingUpdate else St["SprintTime"] + self.dt
        St["SprintAccelerationAmount"] = 0.0 if St["SprintTime"] >= 0.5 else self.get_acceleration_amount()[0]

    def activate_pivot(self):                               # AI:1067
        self.s["Standing"]["bPivotActive"] = self.s["Loco"]["Speed"] < self.S["standing"]["pivot_threshold"]

    def reset_pivot(self):
        self.s["Standing"]["bPivotActive"] = False

    def refresh_crouching_movement(self):                   # AI:1072
        C, loco, S = self.s["Crouching"], self.s["Loco"], self.S["crouching"]
        speed = loco["Speed"] / loco["ScaleWorldSpace"]
        C["StrideBlendAmount"] = S["stride"](speed)
        denominator = S["animated_speed"] * C["StrideBlendAmount"]
        # 0 denominator: speed / 0 is +inf (or NaN at rest) in C++, and Clamp gives the upper bound
        C["PlayRate"] = 2.0 if denominator == 0.0 else clamp(speed / denominator, UE_KINDA_SMALL_NUMBER, 2.0)

    # ---- in air -----------------------------------------------------------------------------------------------------------------------
    def refresh_in_air(self):                               # AI:1107
        s = self.s
        ia, loco = s["InAir"], s["Loco"]
        if ia["bJumped"]:
            ia["bJumped"] = False
            ia["JumpPlayRate"] = lerp_clamped(1.2, 1.5, loco["Speed"] / 600.0)
        ia["VerticalVelocityWorldSpace"] = loco["VelocityWorldSpace"][2]
        self.refresh_ground_prediction()
        self.refresh_in_air_lean()

    def refresh_ground_prediction(self):                    # AI:1142
        s = self.s
        ia, loco = s["InAir"], s["Loco"]
        if ia["VerticalVelocityWorldSpace"] > -200.0:       # AI:1150
            ia["GroundPredictionAmount"] = 0.0
            return
        allowance_amount = 1.0 - self.curve01("GroundPredictionBlock")
        if allowance_amount <= UE_KINDA_SMALL_NUMBER:
            ia["GroundPredictionAmount"] = 0.0
            return
        start = loco["LocationWorldSpace"]
        min_vertical_velocity, max_vertical_velocity = -4000.0, -200.0
        direction = (loco["VelocityWorldSpace"][0], loco["VelocityWorldSpace"][1],
                     clamp(loco["VelocityWorldSpace"][2], min_vertical_velocity, max_vertical_velocity))
        direction = v_normalize_in_place(direction)
        distance = mapped_range_clamped(max_vertical_velocity, min_vertical_velocity, 150.0, 2000.0, ia["VerticalVelocityWorldSpace"])
        sweep_vector = v_scale(direction, distance * loco["ScaleWorldSpace"])
        hit = self.f["sweep"](start, v_add(start, sweep_vector), loco["CapsuleRadius"], loco["CapsuleHalfHeight"])
        ground_valid = hit["blocking"] and hit["normal"][2] >= loco["WalkableFloorAngleCos"]       # AI:1190
        self.hits["ground_prediction_hit" if ground_valid else ("ground_prediction_wall" if hit["blocking"] else "ground_prediction_miss")] += 1
        ia["GroundPredictionAmount"] = self.S["in_air"]["ground_prediction_curve"](hit["time"]) * allowance_amount if ground_valid else 0.0

    def refresh_in_air_lean(self):                          # AI:1219
        s = self.s
        ia, lean = s["InAir"], s["Lean"]
        v = self.get_velocity()
        k = self.S["in_air"]["lean_curve"](ia["VerticalVelocityWorldSpace"]) / 350.0
        target = (v[0] * k, v[1] * k)
        half_life = self.S["general"]["lean_half_life"]
        if self.bPendingUpdate or half_life <= 0.0:
            lean["RightAmount"], lean["ForwardAmount"] = target[1], target[0]
        else:
            a = damper_exact_alpha(self.dt, half_life)
            lean["RightAmount"] = lerp(lean["RightAmount"], target[1], a)
            lean["ForwardAmount"] = lerp(lean["ForwardAmount"], target[0], a)

    # ---- feet ------------------------------------------------------------------------------------------------------------------------
    def refresh_feet_on_game_thread(self):                  # AI:1245
        F = self.s["Feet"]
        f = self.f
        pelvis = f["pelvis"]
        if self.bPendingUpdate:
            F["bValid"] = False
            F["bBecameValid"] = False
        elif not F["bValid"]:
            if t_equals_no_scale_identity(pelvis):          # AI:1263
                return
            F["bValid"] = True
            F["bBecameValid"] = True
        else:
            F["bBecameValid"] = False
        F["PelvisRotation"] = pelvis[1]
        use_ik = self.S["general"]["use_foot_ik"]
        left = f["feet"]["ik_l" if use_ik else "vb_l"]
        right = f["feet"]["ik_r" if use_ik else "vb_r"]
        F["Left"]["TargetLocationWorldSpace"], F["Left"]["TargetRotationWorldSpace"] = left[0], left[1]
        F["Right"]["TargetLocationWorldSpace"], F["Right"]["TargetRotationWorldSpace"] = right[0], right[1]

    def refresh_feet(self, dt):                             # AI:1297
        F = self.s["Feet"]
        if not F["bValid"]:
            return
        F["FootPlantedAmount"] = clamp(self.curve("FootPlanted"), -1.0, 1.0)
        F["FeetCrossingAmount"] = self.curve01("FeetCrossing")
        component = self.f["mesh_transform"]
        for foot, ik, lock in ((F["Left"], "FootLeftIk", "FootLeftLock"), (F["Right"], "FootRightIk", "FootRightLock")):
            context = dict(ComponentTransform=component, ComponentTransformInverse=t_inverse(component), foot=foot,
                           IkAmount=self.curve01(ik), LockAmount=self.curve01(lock), DeltaTime=dt)
            self.process_foot_lock_teleport(context)
            self.process_foot_lock_base_change(context)
            self.refresh_foot_lock(context)

    def process_foot_lock_teleport(self, c):                # AI:1332
        foot = c["foot"]
        if self.s["Feet"]["bBecameValid"] or self.f["time"] - self.TeleportedTime > 0.2 or not is_relevant(c["IkAmount"] * foot["LockAmount"]):
            return
        self.hits["foot_lock_teleport"] += 1
        foot["LockLocationWorldSpace"] = t_position(c["ComponentTransform"], foot["LockLocation"])
        foot["LockRotationWorldSpace"] = t_rotation(c["ComponentTransform"], foot["LockRotation"])

    def process_foot_lock_base_change(self, c):             # AI:1360
        foot = c["foot"]
        F = self.s["Feet"]
        if not F["bBecameValid"] or not is_relevant(c["IkAmount"] * foot["LockAmount"]):        # (bBaseChanged is false without bases)
            return
        if F["bBecameValid"]:
            self.hits["foot_lock_became_valid"] += 1
            foot["LockLocationWorldSpace"] = foot["TargetLocationWorldSpace"]
            foot["LockRotationWorldSpace"] = foot["TargetRotationWorldSpace"]
        foot["LockLocationMovementBaseSpace"] = (0.0, 0.0, 0.0)
        foot["LockRotationMovementBaseSpace"] = QUAT_ID
        foot["LockLocation"] = t_position(c["ComponentTransformInverse"], foot["LockLocationWorldSpace"])
        foot["LockRotation"] = t_rotation(c["ComponentTransformInverse"], foot["LockRotationWorldSpace"])

    def refresh_foot_lock(self, c):                         # AI:1395
        foot = c["foot"]
        s = self.s
        F = s["Feet"]
        inv = c["ComponentTransformInverse"]
        new_lock_amount = c["LockAmount"]
        if s["Loco"]["bMovingSmooth"] or s["tags"]["LocomotionMode"] != tag("LocomotionMode", "Grounded"):
            moving_decrease_speed, not_grounded_decrease_speed = 5.0, 0.6
            self.hits["foot_lock_decrease_moving" if s["Loco"]["bMovingSmooth"] else "foot_lock_decrease_in_air"] += 1
            new_lock_amount = 0.0 if F["bBecameValid"] else max(0.0, min(new_lock_amount, foot["LockAmount"] - c["DeltaTime"] * (
                moving_decrease_speed if s["Loco"]["bMovingSmooth"] else not_grounded_decrease_speed)))
        if not self.S["foot_lock"]["allow"] or not is_relevant(c["IkAmount"] * new_lock_amount):
            if foot["LockAmount"] > 0.0:
                self.hits["foot_lock_released"] += 1
                foot["LockAmount"] = 0.0
                foot["LockLocationWorldSpace"] = (0.0, 0.0, 0.0)
                foot["LockRotationWorldSpace"] = QUAT_ID
                foot["LockLocationMovementBaseSpace"] = (0.0, 0.0, 0.0)
                foot["LockRotationMovementBaseSpace"] = QUAT_ID
                foot["LockLocation"] = (0.0, 0.0, 0.0)
                foot["LockRotation"] = QUAT_ID
            foot["FinalLocation"] = t_position(inv, foot["TargetLocationWorldSpace"])
            foot["FinalRotation"] = t_rotation(inv, foot["TargetRotationWorldSpace"])
            return
        new_amount_equal_one = is_full_weight(new_lock_amount)
        new_amount_greater_than_previous = new_lock_amount > foot["LockAmount"]
        if new_amount_equal_one:
            if new_amount_greater_than_previous:
                self.hits["foot_lock_new_lock"] += 1
                if F["bBecameValid"]:
                    target_location, target_rotation = foot["TargetLocationWorldSpace"], foot["TargetRotationWorldSpace"]
                else:
                    # the final transform of the previous frame, without the foot IK (AI:1459)
                    target_location = t_position(c["ComponentTransform"], foot["FinalLocation"])
                    target_rotation = t_rotation(c["ComponentTransform"], foot["FinalRotation"])
                foot["LockLocationMovementBaseSpace"] = (0.0, 0.0, 0.0)
                foot["LockRotationMovementBaseSpace"] = QUAT_ID
                if foot["LockAmount"] <= 0.9:
                    self.hits["foot_lock_new_location"] += 1
                    foot["LockLocationWorldSpace"] = target_location
                    foot["LockRotationWorldSpace"] = target_rotation
                    foot["LockLocation"] = t_position(inv, foot["LockLocationWorldSpace"])
                    foot["LockRotation"] = t_rotation(inv, foot["LockRotationWorldSpace"])
            foot["LockAmount"] = 1.0
        elif not new_amount_greater_than_previous:
            self.hits["foot_lock_blend_out"] += 1
            foot["LockAmount"] = new_lock_amount
        foot["LockLocation"] = t_position(inv, foot["LockLocationWorldSpace"])
        foot["LockRotation"] = t_rotation(inv, foot["LockRotationWorldSpace"])
        self.constrain_foot_lock(foot)
        final_location = v_lerp(foot["TargetLocationWorldSpace"], foot["LockLocationWorldSpace"], foot["LockAmount"])
        final_rotation = q_normalize(q_fast_lerp(foot["TargetRotationWorldSpace"], foot["LockRotationWorldSpace"], foot["LockAmount"]))
        foot["FinalLocation"] = t_position(inv, final_location)
        foot["FinalRotation"] = t_rotation(inv, final_rotation)

    def constrain_foot_lock(self, foot):                    # AI:1526
        F = self.s["Feet"]
        S = self.S["foot_lock"]
        thigh_axis = q_rotate(F["PelvisRotation"], foot["ThighAxisPelvisSpace"])
        thigh_delta_angle = angle_between_signed_xy(thigh_axis, foot["LockLocation"])
        if abs(thigh_delta_angle) > S["thigh_angle_limit"] + UE_KINDA_SMALL_NUMBER:
            self.hits["thigh_angle_clamped"] += 1
            clamped = clamp(thigh_delta_angle, -S["thigh_angle_limit"], S["thigh_angle_limit"])
            offset = q_from_axis_angle(UP, math.radians(clamped - thigh_delta_angle))
            foot["LockLocation"] = q_rotate(offset, foot["LockLocation"])
            foot["LockRotation"] = q_normalize(q_mul(offset, foot["LockRotation"]))
            component = self.f["mesh_transform"]
            foot["LockLocationWorldSpace"] = t_position(component, foot["LockLocation"])
            foot["LockRotationWorldSpace"] = t_rotation(component, foot["LockRotation"])
        foot_delta_angle = math.degrees(q_twist_angle(q_mul(foot["LockRotationWorldSpace"], q_inverse(foot["TargetRotationWorldSpace"])), UP))
        if abs(foot_delta_angle) > S["foot_angle_limit"] + UE_KINDA_SMALL_NUMBER:
            self.hits["foot_angle_clamped"] += 1
            clamped = clamp(foot_delta_angle, -S["foot_angle_limit"], S["foot_angle_limit"])
            offset = q_from_axis_angle(UP, math.radians(clamped - foot_delta_angle))
            foot["LockRotationWorldSpace"] = q_mul(offset, foot["LockRotationWorldSpace"])

    # ---- transitions ---------------------------------------------------------------------------------------------------------------------
    def play_transition_animation(self, Sequence, BlendInDuration=0.2, BlendOutDuration=0.2, PlayRate=1.0, StartTime=0.0, bFromStandingIdleOnly=False):
        loco, t = self.s["Loco"], self.s["Transitions"]      # AI:1614
        if bFromStandingIdleOnly and (loco["bMoving"] or self.s["tags"]["Stance"] != tag("Stance", "Standing")):
            return
        t.update(QueuedTransitionSequence=Sequence, QueuedTransitionBlendInDuration=BlendInDuration, QueuedTransitionBlendOutDuration=BlendOutDuration,
                 QueuedTransitionPlayRate=PlayRate, QueuedTransitionStartTime=StartTime)

    def play_transition_left_animation(self, BlendInDuration=0.2, BlendOutDuration=0.2, PlayRate=1.0, StartTime=0.0, bFromStandingIdleOnly=False):
        S = self.S["transitions"]                           # AI:1636
        crouching = self.s["tags"]["Stance"] == tag("Stance", "Crouching")
        self.play_transition_animation(S["crouching_left"] if crouching else S["standing_left"], BlendInDuration, BlendOutDuration, PlayRate, StartTime,
                                       bFromStandingIdleOnly)

    def play_transition_right_animation(self, BlendInDuration=0.2, BlendOutDuration=0.2, PlayRate=1.0, StartTime=0.0, bFromStandingIdleOnly=False):
        S = self.S["transitions"]                           # AI:1650
        crouching = self.s["tags"]["Stance"] == tag("Stance", "Crouching")
        self.play_transition_animation(S["crouching_right"] if crouching else S["standing_right"], BlendInDuration, BlendOutDuration, PlayRate, StartTime,
                                       bFromStandingIdleOnly)

    def stop_transition_and_turn_in_place_animations(self, BlendOutDuration=-1.0):    # AI:1664
        self.s["Transitions"]["bStopTransitionsQueued"] = True
        self.s["Transitions"]["QueuedStopTransitionsBlendOutDuration"] = BlendOutDuration

    def play_quick_stop_animation(self):                    # AI:1575
        S, s = self.S["transitions"], self.s
        if s["tags"]["RotationMode"] != tag("RotationMode", "VelocityDirection"):
            self.play_transition_left_animation(S["quick_stop_blend_in"], S["quick_stop_blend_out"], S["quick_stop_play_rate"][0], S["quick_stop_start_time"])
            return
        loco = s["Loco"]
        remaining = unwind_degrees((loco["InputYawAngleWorldSpace"] if loco["bHasInput"] else loco["TargetYawAngleWorldSpace"]) - loco["RotationWorldSpace"][1])
        remaining = remap_angle_ccw(remaining)
        rate = lerp(S["quick_stop_play_rate"][0], S["quick_stop_play_rate"][1], abs(remaining) / 180.0)
        if remaining <= 0.0:
            self.play_transition_left_animation(S["quick_stop_blend_in"], S["quick_stop_blend_out"], rate, S["quick_stop_start_time"])
        else:
            self.play_transition_right_animation(S["quick_stop_blend_in"], S["quick_stop_blend_out"], rate, S["quick_stop_start_time"])

    def refresh_dynamic_transitions(self):                  # AI:1682
        s, S = self.s, self.S["dynamic_transitions"]
        D, F, T = s["DynamicTransitions"], s["Feet"], s["Transitions"]
        if D["bUpdatedThisFrame"]:
            return
        D["bUpdatedThisFrame"] = True
        if D["FrameDelay"] > 0:
            D["FrameDelay"] -= 1
            return
        if not T["bTransitionsAllowed"]:
            return
        threshold_squared = (S["foot_lock_distance_threshold"] * s["Loco"]["ScaleWorldSpace"]) ** 2
        left_squared = v_len_sq(v_sub(F["Left"]["TargetLocationWorldSpace"], F["Left"]["LockLocationWorldSpace"]))
        right_squared = v_len_sq(v_sub(F["Right"]["TargetLocationWorldSpace"], F["Right"]["LockLocationWorldSpace"]))
        left_allowed = is_relevant(F["Left"]["LockAmount"]) and left_squared > threshold_squared
        right_allowed = is_relevant(F["Right"]["LockAmount"]) and right_squared > threshold_squared
        if not left_allowed and not right_allowed:
            return
        crouching = s["tags"]["Stance"] == tag("Stance", "Crouching")
        self.hits["dynamic_transition_both" if left_allowed and right_allowed else ("dynamic_transition_left" if left_allowed else "dynamic_transition_right")] += 1
        sequence = None
        # If both transitions are allowed, choose the one with a greater lock distance.
        if not left_allowed or (right_allowed and left_squared < right_squared):
            sequence = S["crouching_right"] if crouching else S["standing_right"]
        elif not right_allowed or left_squared >= right_squared:
            sequence = S["crouching_left"] if crouching else S["standing_left"]
        if sequence is not None:
            D["FrameDelay"] = 2
            T.update(QueuedTransitionSequence=sequence, QueuedTransitionBlendInDuration=S["blend_duration"],
                     QueuedTransitionBlendOutDuration=S["blend_duration"], QueuedTransitionPlayRate=S["play_rate"], QueuedTransitionStartTime=0.0)

    def play_queued_transition_animation(self):             # AI:1780
        T = self.s["Transitions"]
        if T["bStopTransitionsQueued"] or T["QueuedTransitionSequence"] is None:
            return
        self.commands.append(("play_slot_animation", (T["QueuedTransitionSequence"], "Transition", T["QueuedTransitionBlendInDuration"],
                                                      T["QueuedTransitionBlendOutDuration"], T["QueuedTransitionPlayRate"], 1, 0.0,
                                                      T["QueuedTransitionStartTime"], 0)))
        T.update(QueuedTransitionSequence=None, QueuedTransitionBlendInDuration=0.0, QueuedTransitionBlendOutDuration=0.0,
                 QueuedTransitionPlayRate=1.0, QueuedTransitionStartTime=0.0)

    def stop_queued_transition_and_turn_in_place_animations(self):    # AI:1800
        T = self.s["Transitions"]
        if not T["bStopTransitionsQueued"]:
            return
        for slot in ("Transition", "TurnInPlaceStanding", "TurnInPlaceCrouching"):
            self.commands.append(("stop_montages_with_slot", (slot, T["QueuedStopTransitionsBlendOutDuration"])))
        T["bStopTransitionsQueued"] = False
        T["QueuedStopTransitionsBlendOutDuration"] = -1.0

    # ---- rotate in place / turn in place -----------------------------------------------------------------------------------------------------
    def refresh_rotate_in_place(self):                      # AI:1827
        s, S = self.s, self.S["rotate_in_place"]
        R, loco, V = s["RotateInPlace"], s["Loco"], s["View"]
        if R["bUpdatedThisFrame"]:
            return
        R["bUpdatedThisFrame"] = True
        t = s["tags"]
        allowed = t["RotationMode"] == tag("RotationMode", "Aiming") or t["ViewMode"] == tag("ViewMode", "FirstPerson")
        if loco["bMoving"] or not allowed:
            R["bRotatingLeft"] = R["bRotatingRight"] = False
        else:
            first_person = t["ViewMode"] == tag("ViewMode", "FirstPerson") and t["RotationMode"] != tag("RotationMode", "Aiming")
            threshold = S["fp_view_yaw_threshold"] if first_person else S["view_yaw_threshold"]
            R["bRotatingLeft"] = V["YawAngle"] < -threshold
            R["bRotatingRight"] = V["YawAngle"] > threshold
        if not R["bRotatingLeft"] and not R["bRotatingRight"]:
            R["PlayRate"] = S["play_rate"][0] if self.bPendingUpdate else damper_exact(R["PlayRate"], S["play_rate"][0], self.dt, 0.15)
            return
        rate = mapped_range_clamped(S["reference_view_yaw_speed"][0], S["reference_view_yaw_speed"][1], S["play_rate"][0], S["play_rate"][1], V["YawSpeed"])
        R["PlayRate"] = rate if self.bPendingUpdate else damper_exact(R["PlayRate"], rate, self.dt, 0.15)

    def initialize_turn_in_place(self):                     # AI:1898
        self.s["TurnInPlace"]["ActivationDelay"] = 0.0

    def refresh_turn_in_place(self):                        # AI:1903
        s, S = self.s, self.S["turn_in_place"]
        T, V = s["TurnInPlace"], s["View"]
        if T["bUpdatedThisFrame"]:
            return
        T["bUpdatedThisFrame"] = True
        t = s["tags"]
        allowed = t["RotationMode"] == tag("RotationMode", "ViewDirection") and t["ViewMode"] != tag("ViewMode", "FirstPerson")
        if not s["Transitions"]["bTransitionsAllowed"] or not allowed:
            T["ActivationDelay"] = 0.0
            return
        if V["YawSpeed"] >= S["view_yaw_speed_threshold"] or abs(V["YawAngle"]) <= S["view_yaw_angle_threshold"]:
            T["ActivationDelay"] = 0.0
            return
        T["ActivationDelay"] += self.dt
        delay = S["view_yaw_angle_to_activation_delay"]
        activation_delay = mapped_range_clamped(S["view_yaw_angle_threshold"], 180.0, delay[0], delay[1], abs(V["YawAngle"]))
        if T["ActivationDelay"] <= activation_delay:
            return
        # Select settings based on turn angle and stance.
        turn_left = remap_angle_ccw(V["YawAngle"]) <= 0.0
        turns = S["turns"]
        chosen, slot = None, SLOT_NONE
        if t["Stance"] == tag("Stance", "Standing"):
            slot = SLOT_STANDING
            ninety = abs(V["YawAngle"]) < S["turn_180_angle_threshold"]
            chosen = turns[("standing_90_" if ninety else "standing_180_") + ("left" if turn_left else "right")]
        elif t["Stance"] == tag("Stance", "Crouching"):
            slot = SLOT_CROUCHING
            ninety = abs(V["YawAngle"]) < S["turn_180_angle_threshold"]
            chosen = turns[("crouching_90_" if ninety else "crouching_180_") + ("left" if turn_left else "right")]
        if chosen is not None and chosen["sequence"] is not None:      # IsValid(TurnInPlaceSettings) && IsValid(Sequence)
            self.hits[("turn_" + ("left" if turn_left else "right") + ("_90" if ninety else "_180") + ("_standing" if slot == SLOT_STANDING else "_crouching"))] += 1
            T["QueuedSettings"] = chosen
            T["QueuedSlotName"] = slot
            T["QueuedTurnYawAngle"] = V["YawAngle"]

    def play_queued_turn_in_place_animation(self):          # AI:2012
        s, S = self.s, self.S["turn_in_place"]
        T = s["TurnInPlace"]
        if s["Transitions"]["bStopTransitionsQueued"] or T["QueuedSlotName"] == SLOT_NONE:
            return
        q = T["QueuedSettings"]
        slot = "TurnInPlaceStanding" if T["QueuedSlotName"] == SLOT_STANDING else "TurnInPlaceCrouching"
        self.commands.append(("play_slot_animation", (q["sequence"], slot, S["blend_duration"], S["blend_duration"], q["play_rate"], 1, 0.0, 0.0,
                                                      MONTAGE_BLEND_INERTIALIZATION)))
        T["PlayRate"] = q["play_rate"]
        self.hits["turn_played"] += 1
        if q["scale_by_angle"]:
            T["PlayRate"] *= abs(T["QueuedTurnYawAngle"] / q["animated_turn_angle"]) if q["animated_turn_angle"] != 0.0 else 0.0
        T["QueuedSlotName"] = SLOT_NONE
        T["QueuedTurnYawAngle"] = 0.0

    # ---- ControlRig input -------------------------------------------------------------------------------------------------------------------
    def get_control_rig_input(self):                        # AI:235
        s = self.s
        pelvis = clamp01(s["Pose"]["GroundedAmount"] + s["Pose"]["InAirAmount"] * s["InAir"]["GroundPredictionAmount"])
        F = s["Feet"]
        return dict(bUseHandIkBones=self.S["general"]["use_hand_ik"], bUseFootIkBones=self.S["general"]["use_foot_ik"], bFootTransformsValid=F["bValid"],
                    VelocityBlendForwardAmount=s["Grounded"]["VelocityBlend"]["ForwardAmount"],
                    VelocityBlendBackwardAmount=s["Grounded"]["VelocityBlend"]["BackwardAmount"],
                    SpineYawAngle=s["Spine"]["FinalYawAngle"], PelvisOffsetAmount=pelvis,
                    FootLeftLocation=F["Left"]["FinalLocation"], FootLeftRotation=F["Left"]["FinalRotation"],
                    FootRightLocation=F["Right"]["FinalLocation"], FootRightRotation=F["Right"]["FinalRotation"])

    # ---- the flat view the tests compare ----------------------------------------------------------------------------------------------------------
    def flat(self):
        s = self.s
        out = dict(s["tags"])
        roots = {"LayeringState": s["Layering"], "PoseState": s["Pose"], "ViewState": s["View"], "SpineState": s["Spine"], "HeadState": s["Head"],
                 "LocomotionState": s["Loco"], "LeanState": s["Lean"], "GroundedState": s["Grounded"], "StandingState": s["Standing"],
                 "CrouchingState": s["Crouching"], "InAirState": s["InAir"], "FeetState": s["Feet"], "TransitionsState": s["Transitions"],
                 "DynamicTransitionsState": s["DynamicTransitions"], "RotateInPlaceState": s["RotateInPlace"],
                 "TurnInPlaceState": {k: v for k, v in s["TurnInPlace"].items() if k != "QueuedSettings"},        # QueuedSettings is a pointer in C++ and a copy in the .dal
                 "RagdollingState": {"FlailPlayRate": s["Ragdolling"]["FlailPlayRate"]}}

        def walk(prefix, d):
            for k, v in d.items():
                if isinstance(v, dict):
                    walk(prefix + k + ".", v)
                else:
                    out[prefix + k] = v

        for name, d in roots.items():
            walk(name + ".", d)
        out["RagdollingState.FinalRagdollPose"] = s["Ragdolling"]["bFinalRagdollPoseValid"]       # a snapshot: only whether it was taken
        out["bPendingUpdate"] = self.bPendingUpdate
        out["TeleportedTime"] = self.TeleportedTime
        out["DeltaTimeWithoutTimeDilation"] = self.DeltaTimeWithoutTimeDilation
        out["PreviousGlobalTimeDilation"] = self.PreviousGlobalTimeDilation
        return out
