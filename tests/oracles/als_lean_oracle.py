"""Independent Python port of the lean logic in ALS-Refactored 4.18 (MIT, Sixze).

Source: Source/ALS/Private/AlsAnimationInstance.cpp
    RefreshLocomotionOnGameThread (acceleration and yaw speed), GetAccelerationAmount,
    RefreshGroundedLean, RefreshInAirLean; UAlsMath::DamperExactAlpha; UAlsVector::ClampMagnitude01.

This file deliberately does not import the dal package: it is the oracle the compiled
``examples/als_lean.dal`` is compared against.  It is written like the C++ (state in a
"LocomotionState", the same function split), in double precision, with the UE math it needs
re-derived here from the UE definitions (UnwindDegrees, FRotator::NormalizeAxis, FQuat::Rotator,
FQuat::UnrotateVector, FMath::InvExpApprox).  Movement-base compensation is not ported (the .dal
does not have it either).
"""

from __future__ import annotations

import math

UE_SMALL_NUMBER = 1.0e-8
UE_KINDA_SMALL_NUMBER = 1.0e-4
UE_LN2 = 0.69314718056


# ---- UE math, re-derived --------------------------------------------------------------------

def inv_exp_approx(x: float) -> float:
    # FMath::InvExpApprox: 1 / (1 + A x + B x^2 + C x^3)
    return 1.0 / (1.0 + 1.00746054 * x + 0.45053901 * x * x + 0.25724632 * x * x * x)


def unwind_degrees(a: float) -> float:
    while a > 180.0:
        a -= 360.0
    while a < -180.0:
        a += 360.0
    return a


def normalize_axis(angle: float) -> float:
    # FRotator::NormalizeAxis: ClampAxis -> [0, 360), then (180, 360) -> (-180, 0)
    angle = math.fmod(angle, 360.0)
    if angle < 0.0:
        angle += 360.0
    if angle > 180.0:
        angle -= 360.0
    return angle


def quat_yaw_degrees(q) -> float:
    # FQuat::Rotator().Yaw
    x, y, z, w = q
    singularity = z * x - w * y
    threshold = 0.4999995
    if singularity < -threshold:
        return normalize_axis(-2.0 * math.atan2(x, w) * 180.0 / math.pi)
    if singularity > threshold:
        return normalize_axis(2.0 * math.atan2(x, w) * 180.0 / math.pi)
    yaw_y = 2.0 * (w * z + x * y)
    yaw_x = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(yaw_y, yaw_x) * 180.0 / math.pi


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def quat_unrotate_vector(q, v):
    # FQuat::UnrotateVector: rotate by the inverse (conjugate) quaternion
    qv = (-q[0], -q[1], -q[2])
    t = cross(qv, v)
    t = (2.0 * t[0], 2.0 * t[1], 2.0 * t[2])
    c = cross(qv, t)
    w = q[3]
    return (v[0] + w * t[0] + c[0], v[1] + w * t[1] + c[1], v[2] + w * t[2] + c[2])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def clamp_magnitude01(v):
    # UAlsVector::ClampMagnitude01
    magnitude_sq = dot(v, v)
    if magnitude_sq <= 1.0:
        return v
    scale = 1.0 / math.sqrt(magnitude_sq)
    return (v[0] * scale, v[1] * scale, v[2] * scale)


def damper_exact_alpha(delta_time: float, half_life: float) -> float:
    # UAlsMath::DamperExactAlpha
    return 1.0 - inv_exp_approx(UE_LN2 / (half_life + UE_SMALL_NUMBER) * delta_time)


def lerp(a: float, b: float, alpha: float) -> float:
    return a + alpha * (b - a)


# ---- the ALS logic ------------------------------------------------------------------------------

class AlsLeanOracle:
    """Holds FAlsLocomotionAnimationState (the fields lean needs), FAlsLeanState and bPendingUpdate."""

    def __init__(self, lean_interpolation_half_life: float = 0.2, in_air_lean_curve=lambda vertical_velocity: 0.0):
        self.lean_interpolation_half_life = lean_interpolation_half_life
        self.in_air_lean_curve = in_air_lean_curve  # UAlsInAirSettings::LeanAmountCurve->GetFloatValue
        self.pending_update = True
        # FAlsLocomotionAnimationState
        self.velocity_world = (0.0, 0.0, 0.0)
        self.acceleration_world = (0.0, 0.0, 0.0)
        self.rotation_yaw = 0.0
        self.rotation_quaternion = (0.0, 0.0, 0.0, 1.0)
        self.yaw_velocity_world = 0.0
        self.max_acceleration = 0.0
        self.max_braking_deceleration = 0.0
        # FAlsLeanState
        self.lean_right_amount = 0.0
        self.lean_forward_amount = 0.0
        # FAlsInAirState
        self.vertical_velocity_world = 0.0

    # RefreshLocomotionOnGameThread (the part about velocity, acceleration, rotation)
    def refresh_locomotion(self, delta_time, velocity, rotation, max_acceleration, max_braking_deceleration):
        can_calculate_rate_of_change = (not self.pending_update) and delta_time > UE_SMALL_NUMBER

        previous_velocity = self.velocity_world
        self.velocity_world = velocity

        if can_calculate_rate_of_change:
            self.acceleration_world = tuple((n - p) / delta_time for n, p in zip(self.velocity_world, previous_velocity))
        else:
            self.acceleration_world = (0.0, 0.0, 0.0)

        self.max_acceleration = max_acceleration
        self.max_braking_deceleration = max_braking_deceleration

        previous_yaw_angle = self.rotation_yaw
        self.rotation_quaternion = rotation
        self.rotation_yaw = quat_yaw_degrees(rotation)

        if can_calculate_rate_of_change:
            self.yaw_velocity_world = unwind_degrees(self.rotation_yaw - previous_yaw_angle) / delta_time
        else:
            self.yaw_velocity_world = 0.0

    # RefreshInAirOnGameThread (only the vertical velocity)
    def refresh_in_air(self):
        self.vertical_velocity_world = self.velocity_world[2]

    # UAlsAnimationInstance::GetVelocity
    def get_velocity(self):
        return quat_unrotate_vector(self.rotation_quaternion, self.velocity_world)

    # UAlsAnimationInstance::GetAccelerationAmount
    def get_acceleration_amount(self):
        if dot(self.acceleration_world, self.velocity_world) >= 0.0:
            max_acceleration = self.max_acceleration
        else:
            max_acceleration = self.max_braking_deceleration

        if max_acceleration <= UE_KINDA_SMALL_NUMBER:
            return (0.0, 0.0)

        acceleration = quat_unrotate_vector(self.rotation_quaternion, self.acceleration_world)
        acceleration = tuple(c / max_acceleration for c in acceleration)
        clamped = clamp_magnitude01(acceleration)
        return (clamped[0], clamped[1])

    def _apply_target(self, delta_time, target_x, target_y):
        if self.pending_update or self.lean_interpolation_half_life <= 0.0:
            self.lean_right_amount = target_y
            self.lean_forward_amount = target_x
        else:
            interpolation_amount = damper_exact_alpha(delta_time, self.lean_interpolation_half_life)
            self.lean_right_amount = lerp(self.lean_right_amount, target_y, interpolation_amount)
            self.lean_forward_amount = lerp(self.lean_forward_amount, target_x, interpolation_amount)

    # RefreshGroundedLean
    def refresh_grounded_lean(self, delta_time):
        target = self.get_acceleration_amount()
        self._apply_target(delta_time, target[0], target[1])

    # RefreshInAirLean
    def refresh_in_air_lean(self, delta_time):
        reference_speed = 350.0
        curve = self.in_air_lean_curve(self.vertical_velocity_world)
        velocity = self.get_velocity()
        target = tuple(c / reference_speed * curve for c in velocity)
        self._apply_target(delta_time, target[0], target[1])

    # NativePostUpdateAnimation
    def post_update(self):
        self.pending_update = False

    # one frame as the animation graph runs it: locomotion on the game thread, then the
    # grounded or in-air lean refresh, then the post update
    def frame(self, delta_time, velocity, rotation, max_acceleration, max_braking_deceleration, grounded):
        self.refresh_locomotion(delta_time, velocity, rotation, max_acceleration, max_braking_deceleration)
        self.refresh_in_air()
        if grounded:
            self.refresh_grounded_lean(delta_time)
        else:
            self.refresh_in_air_lean(delta_time)
        self.post_update()
        return {
            "LeanForward": self.lean_forward_amount,
            "LeanRight": self.lean_right_amount,
            "Acceleration": self.acceleration_world,
            "YawSpeed": self.yaw_velocity_world,
        }
