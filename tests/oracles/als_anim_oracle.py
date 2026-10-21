"""Independent Python port of UAlsAnimationInstance (ALS-Refactored, MIT, Sixze): the parts AlsAnimationInstance.dal ports in M1.

Source: Source/ALS/Private/AlsAnimationInstance.cpp (line numbers "AI:" below), Public/AlsAnimationInstance.h, State/*.h,
Utility/AlsMath.h, AlsRotation.h, AlsVector.h.

This file deliberately does not import the dal package (``als_lean_oracle`` is its only neighbour: UE math re-derived there).
It is written like the C++ -- one function per ALS function, the same state structs (as dicts of dicts), double precision,
the frame order of the engine: NativeUpdateAnimation, NativeThreadSafeUpdateAnimation, the graph's Call(Self.X) entries,
NativePostUpdateAnimation -- so it also checks the way the .dal runs the post-update of a frame at the start of the next one.

Not ported (M2, as in the .dal): foot lock, the montage playback of the queued transitions, the ground prediction sweep,
the turn-in-place selection, the final ragdoll pose snapshot.  Movement-base compensation is dead without moving bases.
"""

from __future__ import annotations

import math

from oracles.als_lean_oracle import UE_KINDA_SMALL_NUMBER, UE_LN2, UE_SMALL_NUMBER, dot, inv_exp_approx, quat_unrotate_vector, quat_yaw_degrees, unwind_degrees

ZERO_ANIMWEIGHT_THRESH = 0.00001
CCW_THRESHOLD = 5.0          # UAlsRotation::CounterClockwiseRotationAngleThreshold

TAGS = {
    "ViewMode": "Als.ViewMode.", "LocomotionMode": "Als.LocomotionMode.", "RotationMode": "Als.RotationMode.", "Stance": "Als.Stance.",
    "Gait": "Als.Gait.", "OverlayMode": "Als.OverlayMode.", "LocomotionAction": "Als.LocomotionAction.", "GroundedEntryMode": "Als.GroundedEntryMode.",
}


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


def v_len_sq(a):
    return a[0] * a[0] + a[1] * a[1] + a[2] * a[2]


def v_scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


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


# ---- the instance -------------------------------------------------------------------------------------------------------------

class AlsAnimOracle:
    """``settings`` (UAlsAnimationInstanceSettings): a dict with the members the ported code reads, curves as Python callables:

    general: moving_smooth_speed_threshold, lean_half_life, use_hand_ik, use_foot_ik
    head: pitch_hl, yaw_hl, switch_hl, fp_pitch_hl, fp_yaw_hl
    grounded: velocity_blend_hl, yaw_offset_curves (forward, backward, left, right)
    standing: animated_walk, animated_run, animated_sprint, stride_walk, stride_run, pivot_threshold
    crouching: animated_speed, stride
    in_air: lean_curve
    rotate_in_place: view_yaw_threshold, fp_view_yaw_threshold, reference_view_yaw_speed (2), play_rate (2)
    transitions: quick_stop_blend_in, quick_stop_blend_out, quick_stop_play_rate (2), quick_stop_start_time,
                 standing_left, standing_right, crouching_left, crouching_right (sequence names)
    dynamic_transitions: foot_lock_distance_threshold, blend_duration, play_rate, standing/crouching left/right
    turn_in_place: view_yaw_angle_threshold, view_yaw_speed_threshold, activation_delay (2)
    teleport_distance_threshold
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
        s["Feet"] = dict(FootPlantedAmount=0.0, FeetCrossingAmount=0.0, bValid=False)
        s["Transitions"] = dict(bTransitionsAllowed=False, QueuedTransitionSequence=None, QueuedTransitionBlendInDuration=0.0,
                                QueuedTransitionBlendOutDuration=0.0, QueuedTransitionPlayRate=1.0, QueuedTransitionStartTime=0.0,
                                bStopTransitionsQueued=False, QueuedStopTransitionsBlendOutDuration=-1.0)
        s["DynamicTransitions"] = dict(bUpdatedThisFrame=False, FrameDelay=0)
        s["RotateInPlace"] = dict(bUpdatedThisFrame=False, bRotatingLeft=False, bRotatingRight=False, PlayRate=1.0)
        s["TurnInPlace"] = dict(bUpdatedThisFrame=False, ActivationDelay=0.0, PlayRate=1.0)
        s["Ragdolling"] = dict(FlailPlayRate=1.0)
        self.s = s
        self.bPendingUpdate = True
        self.TeleportedTime = 0.0
        self.f = None          # the inputs of the frame being processed
        self.before_post = {}
        self.dt = 1.0

    # ---- the frame, in the engine's order -------------------------------------------------------------------------------
    def frame(self, f, calls=()):
        """One frame in the engine's order: NativeUpdateAnimation + NativeThreadSafeUpdateAnimation, the graph's entry calls
        ``[(name, kwargs)]``, NativePostUpdateAnimation.  Returns ``flat()`` after all of it; ``before_post`` is what the graph left."""
        self.f = f
        self.dt = f["dt"]
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
        # RefreshFeetOnGameThread: M2
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
        self.refresh_feet()
        s["Transitions"]["bTransitionsAllowed"] = is_full_weight(self.curve("AllowTransitions"))    # RefreshTransitions, AI:1675

    def native_post_update_animation(self):                 # AI:194
        # PlayQueuedTransitionAnimation, PlayQueuedTurnInPlaceAnimation, StopQueued...: the montage commands are M2; the queues are consumed
        tr = self.s["Transitions"]
        if not tr["bStopTransitionsQueued"] and tr["QueuedTransitionSequence"] is not None:
            tr.update(QueuedTransitionSequence=None, QueuedTransitionBlendInDuration=0.0, QueuedTransitionBlendOutDuration=0.0,
                      QueuedTransitionPlayRate=1.0, QueuedTransitionStartTime=0.0)
        if tr["bStopTransitionsQueued"]:
            tr["bStopTransitionsQueued"] = False
            tr["QueuedStopTransitionsBlendOutDuration"] = -1.0
        self.bPendingUpdate = not self.f["actor_ready"]

    # ---- public calls of the character -----------------------------------------------------------------------------------
    def jump(self):                                         # AI.h:402 (the .dal queues it: it arrives at the start of the next update)
        self.s["InAir"]["bJumpRequested"] = True

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
            effective_dt = self.f["dt_without_time_dilation"] if first_person else dt
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
        ia["GroundPredictionAmount"] = 0.0                  # M2: the sweep; the early outs and the stub give 0
        lean = s["Lean"]
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

    # ---- feet, transitions ---------------------------------------------------------------------------------------------------------------
    def refresh_feet(self):                                 # M2 stub: the two curve reads of AI:1304 (only while bValid in ALS)
        F = self.s["Feet"]
        F["FootPlantedAmount"] = clamp(self.curve("FootPlanted"), -1.0, 1.0)
        F["FeetCrossingAmount"] = self.curve01("FeetCrossing")

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

    def refresh_dynamic_transitions(self):                  # AI:1682 (the foot lock is M2: every LockAmount is 0, nothing is allowed)
        D = self.s["DynamicTransitions"]
        if D["bUpdatedThisFrame"]:
            return
        D["bUpdatedThisFrame"] = True
        if D["FrameDelay"] > 0:
            D["FrameDelay"] -= 1

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

    def refresh_turn_in_place(self):                        # AI:1903 up to the selection (M2)
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

    # ---- ControlRig input -------------------------------------------------------------------------------------------------------------------
    def get_control_rig_input(self):                        # AI:235
        s = self.s
        pelvis = clamp01(s["Pose"]["GroundedAmount"] + s["Pose"]["InAirAmount"] * s["InAir"]["GroundPredictionAmount"])
        return dict(bUseHandIkBones=self.S["general"]["use_hand_ik"], bUseFootIkBones=self.S["general"]["use_foot_ik"], bFootTransformsValid=s["Feet"]["bValid"],
                    VelocityBlendForwardAmount=s["Grounded"]["VelocityBlend"]["ForwardAmount"],
                    VelocityBlendBackwardAmount=s["Grounded"]["VelocityBlend"]["BackwardAmount"],
                    SpineYawAngle=s["Spine"]["FinalYawAngle"], PelvisOffsetAmount=pelvis)

    # ---- the flat view the tests compare ----------------------------------------------------------------------------------------------------------
    def flat(self):
        s = self.s
        out = dict(s["tags"])
        roots = {"LayeringState": s["Layering"], "PoseState": s["Pose"], "ViewState": s["View"], "SpineState": s["Spine"], "HeadState": s["Head"],
                 "LocomotionState": s["Loco"], "LeanState": s["Lean"], "GroundedState": s["Grounded"], "StandingState": s["Standing"],
                 "CrouchingState": s["Crouching"], "InAirState": s["InAir"], "FeetState": s["Feet"], "TransitionsState": s["Transitions"],
                 "DynamicTransitionsState": s["DynamicTransitions"], "RotateInPlaceState": s["RotateInPlace"], "TurnInPlaceState": s["TurnInPlace"],
                 "RagdollingState": s["Ragdolling"]}

        def walk(prefix, d):
            for k, v in d.items():
                if isinstance(v, dict):
                    walk(prefix + k + ".", v)
                else:
                    out[prefix + k] = v

        for name, d in roots.items():
            walk(name + ".", d)
        out["bPendingUpdate"] = self.bPendingUpdate
        out["TeleportedTime"] = self.TeleportedTime
        return out
