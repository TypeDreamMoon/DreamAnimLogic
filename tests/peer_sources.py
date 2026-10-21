"""A three-logic system in the shape of ALS: an input logic, a character logic and an animation logic that read each other.

AlsInput (actor, before the movement) calls AlsCharacter (actor, after the movement), which calls the animation instance
(queued) and is read by it (sampled at the start of its update).
"""

LIB = """
tags AlsGait = "Als.Gait" { Walking, Running, Sprinting }
tags AlsStance = "Als.Stance" { Standing, Crouching }
enum Direction { Forward, Backward, Left, Right }

struct LocomotionState {
    bHasInput: bool;
    Speed: float;
    InputYaw: float;
    Velocity: vec3;
    bMoving: bool;
}
struct AnimLocomotion {
    Speed: float;
    Lean: vec2;
    bMoving: bool;
    Direction: Direction = Direction.Forward;
}

fn clamp01(x: float) -> float { return clamp(x, 0.0, 1.0); }
"""

CHARACTER = """
use "als_lib.dal";
peer Anim: AlsAnim;
logic AlsCharacter : actor in after_movement;

input Velocity: vec3 <- host.velocity;
input Acceleration: vec3 <- host.acceleration;
state Locomotion: LocomotionState;
state DesiredGait: AlsGait = AlsGait.Running;
state Gait: AlsGait = AlsGait.Walking;
state Stance: AlsStance = AlsStance.Standing;
state Jumps: int;
state Log: int;

view can_sprint() -> bool {
    return Locomotion.bHasInput && Stance == AlsStance.Standing && abs(Locomotion.InputYaw) < 50.0;
}

view max_allowed() -> AlsGait {
    if (DesiredGait != AlsGait.Sprinting) { return DesiredGait; }
    return can_sprint() ? AlsGait.Sprinting : AlsGait.Running;
}

pub method SetDesiredGait(g: AlsGait) { DesiredGait = g; Log += 1; }
pub method SetStance(s: AlsStance) { Stance = s; }

pub method Jump() -> bool {
    if (Stance == AlsStance.Crouching) { return false; }
    Jumps += 1;
    Anim.Notify(Jumps);
    return true;
}

method refresh_locomotion() {
    Locomotion.Velocity = Velocity;
    Locomotion.Speed = length(Velocity.xy);
    Locomotion.bHasInput = length_sq(Acceleration) > KINDA_SMALL_NUMBER;
    if (Locomotion.bHasInput) { Locomotion.InputYaw = rad_to_deg(atan2(Acceleration.y, Acceleration.x)); }
    Locomotion.bMoving = Locomotion.bHasInput && Locomotion.Speed > 50.0;
}

method refresh_gait() {
    let allowed = max_allowed();
    if (Locomotion.Speed < 180.0) { Gait = AlsGait.Walking; }
    else if (Locomotion.Speed < 400.0 || allowed != AlsGait.Sprinting) { Gait = AlsGait.Running; }
    else { Gait = AlsGait.Sprinting; }
}

on begin_play { Log = 100; }

update(dt) in after_movement {
    refresh_locomotion();
    refresh_gait();
}
"""

INPUT = """
use "als_lib.dal";
peer Character: AlsCharacter;
logic AlsInput : actor;

input Sprint: float;
input Crouch: float;
input JumpPressed: bool;
state Sent: int;
state Jumped: bool;

update(dt) in pre_movement {
    Character.SetDesiredGait(Sprint > 0.5 ? AlsGait.Sprinting : AlsGait.Running);
    Character.SetStance(Crouch > 0.5 ? AlsStance.Crouching : AlsStance.Standing);
    Sent += 1;
    if (JumpPressed) {
        let ok = Character.Jump();
        Jumped = ok;
    }
}
"""

ANIM = """
use "als_lib.dal";
peer Character: AlsCharacter;
logic AlsAnim;

input CharLocomotion: LocomotionState <- Character.Locomotion;
input Gait: AlsGait <- Character.Gait;
input Stance: AlsStance <- Character.Stance;
input CharSpeed: float <- Character.Locomotion.Speed;
input MaxAccel: float <- host.max_acceleration;
state Loco: AnimLocomotion;
state Notified: int;
state Updates: int;

pub method Notify(n: int) { Notified = n; }

view lean_target() -> vec2 {
    return CharLocomotion.bMoving ? vec2(CharLocomotion.Velocity.x, CharLocomotion.Velocity.y) / 1000.0 : VEC2_ZERO;
}

entry RefreshLoco(dt) {
    Loco.Speed = lerp(Loco.Speed, CharSpeed, clamp01(dt * 8.0));
    Loco.Lean = lerp(Loco.Lean, lean_target(), clamp01(dt * 4.0));
    Loco.bMoving = CharLocomotion.bMoving;
    Loco.Direction = CharLocomotion.Velocity.x >= 0.0 ? Direction.Forward : Direction.Backward;
}

update(dt) {
    Updates += 1;
    RefreshLoco_body(dt);
}

method RefreshLoco_body(dt: float) {
    Loco.Speed = lerp(Loco.Speed, CharSpeed, clamp01(dt * 8.0));
}
"""

FILES = {"als_lib.dal": LIB, "AlsCharacter.dal": CHARACTER, "AlsInput.dal": INPUT, "AlsAnim.dal": ANIM}
