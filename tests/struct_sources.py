"""Struct programs shared by the v3 tests: a small animation-state sketch in the style of ALS (nested states, defaults,
construction by name, member assignment, value semantics, struct functions and free inputs)."""

STRUCTS = """
enum Direction { Forward, Backward, Left, Right }

struct VelocityBlend {
    bInit: bool = true;
    Forward: float;
    Backward: float;
    Left: float;
    Right: float;
}
struct FootState {
    Axis: vec3 = vec3(0.0, 0.0, -1.0);
    Offset: float = 2.5;
    Heading: Direction = Direction.Left;
}
struct Feet {
    Left: FootState;
    Right: FootState = FootState(Axis: vec3(0.0, 0.0, 1.0), Heading: Direction.Right);
    Phase: int;
}
struct PoseState {
    Blend: VelocityBlend;
    Feet: Feet;
    Lean: vec2;
    Look: rotator;
    Turn: quat = quat(0.0, 0.0, 0.0, 1.0);
}
"""

PROGRAM = STRUCTS + """
logic Sketch;
const Sample: VelocityBlend = VelocityBlend(Forward: 0.25, Left: 0.75);
input Velocity: vec3;
input Target: VelocityBlend;
input Which: bool;
state Pose: PoseState;
state Previous: VelocityBlend;
state Held: Feet;
state Picked: VelocityBlend = VelocityBlend(Backward: 9.0);
state Count: int;

fn blend_of(v: vec3) -> VelocityBlend {
    let d = normalize(v);
    return VelocityBlend(bInit: false, Forward: clamp(d.x, 0.0, 1.0), Backward: abs(clamp(d.x, -1.0, 0.0)),
                         Left: abs(clamp(d.y, -1.0, 0.0)), Right: clamp(d.y, 0.0, 1.0));
}

fn mirrored(f: Feet) -> Feet {
    var out = f;
    out.Left = f.Right;
    out.Right = f.Left;
    out.Phase = f.Phase + 1;
    return out;
}

update(dt) {
    Previous = Pose.Blend;                       // a copy, later changes of Pose.Blend must not show here
    let target = blend_of(Velocity);
    if (Pose.Blend.bInit || Which) {
        Pose.Blend = target;
    } else {
        let a = clamp(dt * 8.0, 0.0, 1.0);
        Pose.Blend.Forward = lerp(Pose.Blend.Forward, target.Forward, a);
        Pose.Blend.Backward += (target.Backward - Pose.Blend.Backward) * a;
        Pose.Blend.Left = lerp(Pose.Blend.Left, target.Left, a);
        Pose.Blend.Right = lerp(Pose.Blend.Right, target.Right, a);
    }
    Pose.Lean = Velocity.xy * 0.01;
    Pose.Look = rotator(0.0, Velocity.y, 0.0);
    Pose.Feet.Phase += 1;
    Pose.Feet.Left.Offset = Velocity.z * 0.1;
    Pose.Feet.Left.Heading = Velocity.x > 0.0 ? Direction.Forward : Direction.Backward;
    Held = Which ? mirrored(Pose.Feet) : Pose.Feet;      // both branches are copies
    Pose.Feet.Right.Offset = -Pose.Feet.Left.Offset;     // Held must keep the old Right.Offset
    var scratch = Target;
    scratch.Forward += 1.0;
    Picked = Which ? scratch : Sample;
    Count += 1;
}
"""
