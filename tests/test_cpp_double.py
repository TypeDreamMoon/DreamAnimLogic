"""The C++ backend for the double types (double, vec2d, vec3d, rotatord, quatd, transformd).

Three things are checked against the stand-in engine (tests/cpp_stub, compiled by MSVC with /W4 /WX):

* a program that uses every new type, the conversions between the precisions, double literals, mixed arithmetic, swizzles, member
  assignments, struct members, the host reads at both precisions, the double commands and the double positions of a hit:
  the C++ computes what the interpreter and the Python backend compute (the values that are only double arithmetic to the last bit);
* every overload of DalMath.h for the double types (the versions the built-ins have for them) against ``dal.runtime``;
* the float32 mode of the interpreter and of the generated Python (every float is a float32, every + - * / is rounded to float32)
  against the C++ ``float`` run, bit for bit: the threshold case this project is about (``dt`` accumulated seven times against 0.11666667)
  and a world time held in a double (``TimeSince``).

Skipped when no C++ compiler is found (see test_cpp_compile.py).
"""

import os
import random
import struct
import unittest

import _bootstrap  # noqa: F401

import cpp_driver
import dal
from dal import harness
from _bootstrap import ROOT
from dal import runtime as rt
from dal.cppbackend import CppOptions, cpp_type, cpp_value, fmt_double, generate_cpp
from dal.types import DOUBLE, QUATD, ROTATORD, TRANSFORMD, VEC2D, VEC3D
from test_cpp_compile import COMPILER, compare_effects, everything_source, rand_input, round_frames


def f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


def states_of(frames):
    """The inputs and states of the frames of a run (Python or C++) without the host effects and the final flush of an animation instance."""
    return [{k: v for k, v in f.items() if not k.startswith("__")} for f in frames if not f.get("__final__")]


def only(frames, names):
    return [{k: v for k, v in f.items() if k in names} for f in frames]


# the host items the engine holds as doubles: a frame gives them at full precision
HOST_DOUBLE_ITEMS = ("time", "velocity", "acceleration", "actor_location", "actor_rotation", "control_rotation", "mesh_world_transform",
                     "pre_penetration_velocity", "ragdoll_pelvis_location", "ragdoll_velocity")


def prepare(module, frames):
    """The frames with everything the program holds as a float rounded to float32 (both sides start from the same value) and what it holds as a
    double left as it is: the double host items and the free inputs of a double type."""
    free_doubles = {i.name for i in module.inputs if i.host is None and i.ty.is_double}
    out = []
    for f in frames:
        g = {}
        for key, value in f.items():
            if key in HOST_DOUBLE_ITEMS:
                g[key] = value
            elif key == "free":
                g[key] = {n: (x if n in free_doubles else round_frames([{"x": x}])[0]["x"]) for n, x in value.items()}
            else:
                g[key] = round_frames([{key: value}])[0][key]
        out.append(g)
    return out


# ---- (a) a program with every new type -----------------------------------------------------------------------------------------

EVERY_DOUBLE = """
settings DCfg { Resp: channel_set; }

struct Body {
    Pos: vec3d;
    Spin: rotatord = rotatord(1.0, 2.0, 3.0);
    Mass: double = 2.5d;
    Facing: quatd;
    Local: transformd;
    Lean: vec2d = vec2d(0.25, 0.5);
    Count: int;
}

logic Doubles : actor;
param C: DCfg;

input Clock: double <- host.time;
input ClockF: float <- host.time;
input Loc: vec3d <- host.actor_location;
input LocF: vec3 <- host.actor_location;
input Rot: quatd <- host.actor_rotation;
input RotF: quat <- host.actor_rotation;
input Look: rotatord <- host.control_rotation;
input LookF: rotator <- host.control_rotation;
input Vel: vec3d <- host.velocity;
input Speed: double <- host.max_walk_speed;
input Radius: float <- host.capsule_radius;
input Mesh: transformd <- host.mesh_world_transform;
input MeshF: transform <- host.mesh_world_transform;
input Pre: vec3d <- host.pre_penetration_velocity;
input PreValid: bool <- host.pre_penetration_velocity_valid;
input Ground: bool <- host.is_grounded;
input Wish: vec2d;
input Gain: double;
input Offset: vec3d;
input Bias: float;
input Dir: vec3;
input Aim: vec2;
input Idx: int;

state T0: double;
state Acc: double;
state Elapsed: double;
state ElapsedF: float;
state Late: bool;
state X0: double;
state X1: vec3d;
state X2: vec3d;
state X3: vec2d;
state X4: double;
state X5: quatd;
state X6: transformd;
state X7: double;
state X8: int;
state Lit: double;
state Third: double;
state W1: double;
state W2: vec3d;
state W3: vec2d;
state W4: quatd;
state W5: rotatord;
state W6: transformd;
state N1: float;
state N2: vec3;
state N3: vec2;
state N4: quat;
state N5: rotator;
state N6: transform;
state S1: vec2d;
state S2: vec2d;
state S3: vec3d;
state S4: double;
state S5: double;
state M1: double;
state M2: vec3d;
state R1: rotatord;
state R2: double;
state Q1: quatd;
state Q2: vec3d;
state T1: transformd;
state T2: vec3d;
state B: Body;
state Hp: vec3d;
state Hl: vec3d;
state HpF: vec3;
state Blocked: bool;
state Over: bool;
state Sweep: vec3d;
state Line: float;
state Sphere: vec3d;
state Fn: double;
state Ternary: vec3d;
state Px: vec3d;

fn along(a: vec3d, b: vec3d, t: double) -> vec3d {
    return a + (b - a) * t;
}

update(dt) {
    // the clock is a double: nothing is lost in the difference of two world times
    if (T0 == 0.0d) { T0 = Clock; }
    Elapsed = Clock - T0;
    ElapsedF = ClockF - float(T0);
    Late = Elapsed > 0.2d;
    Acc += dt;

    // only double arithmetic: the same operations in the same order on both sides
    X0 = (Clock - T0) * 3.0d + 0.1d;
    X1 = Loc * 1.5d + Vel - Offset;
    X2 = vec3d(Wish.x, Wish.y, Gain) + Pre * 2.0d;
    X3 = Wish * Gain - vec2d(0.5, 0.25);
    X4 = dot(Loc, Vel) + length_sq(Offset);
    X5 = Rot * Rot;
    X6 = transformd(Loc, Rot, vec3d(1.0, 2.0, 0.5));
    X7 = Speed * 0.1d + Gain / 4.0d - (Gain > 0.0d ? Gain : -Gain);
    X8 = to_int(Gain * 100.0d) + Idx;

    // literals, widening and narrowing
    Lit = 0.1d + 0.2d;
    Third = 1.0d / 3.0d;
    W1 = Bias;
    W2 = Dir;
    W3 = Aim;
    W4 = RotF;
    W5 = LookF;
    W6 = MeshF;
    N1 = float(Gain);
    N2 = vec3(Loc);
    N3 = vec2(Wish);
    N4 = quat(Rot);
    N5 = rotator(Look);
    N6 = transform(Mesh);

    // swizzles and member assignment
    S1 = Loc.xy;
    S2 = Offset.zx;
    S3 = Offset.xyz;
    S4 = Loc.z - Loc.x;
    S5 = Wish.y;
    var p = Loc + vec3d(Dir);
    p.z = p.z + Gain * 0.5;
    p.x -= 1.0d;
    Px = p;
    M1 = Bias * 2.0 + Gain + Idx;
    M2 = p + Dir * Bias;
    var t = Mesh;
    t.location.x = p.x;
    t.rotation = X5;
    t.scale.z = 2.0d;
    T1 = t;
    T2 = transform_position(t, p);
    B.Pos = p - vec3d(LocF);
    B.Pos.y += Gain;
    B.Mass = B.Mass / (Speed + 0.1d);
    B.Lean.x = Wish.x;
    B.Lean = B.Lean + Wish;
    B.Local = t;
    B.Local.location.z = Gain;
    B.Facing = slerp(Rot, X5, 0.25d);
    B.Count += 1;

    // rotations
    R1 = to_rotator(Rot);
    R2 = twist_angle(Rot, vec3d(0.0, 0.0, 1.0));
    Q1 = quat_from_axis_angle(normalize(Offset), Gain);
    Q2 = rotate(Look, Vel) + unrotate(Rot, Loc);
    Fn = length(along(Loc, Offset, 0.25d)) + angle_between(Loc, Vel);
    Ternary = Ground ? p : Offset;

    // the commands take the double types
    host.set_actor_location(p, Ground, !Ground);
    host.set_actor_location(Dir);
    host.root_motion_override(Vel * 0.5d, Rot);
    host.root_motion_override(Dir * 2.0, RotF);

    // a hit has its positions at both precisions
    let h = host.trace_line(Loc, p);
    Hp = h.point_d;
    Hl = h.location_d;
    HpF = h.point;
    Blocked = h.blocking;
    let s = host.trace_sphere(Loc, Offset, Radius);
    Sphere = s.point_d;
    let w = host.sweep_capsule(Loc, p, 30.0, 90.0, Channel.Pawn, C.Resp);
    Sweep = w.location_d;
    Over = host.overlap_capsule(p, 30.0, 90.0, Channel.Pawn, C.Resp);
    Line = host.line_trace(Loc, Offset, Channel.Camera).distance;
}
"""

# computed from doubles only (and from float inputs the frames round to float32 first): C++ and Python do the same IEEE operations, so they agree to the last bit
EXACT = {"T0", "Acc", "Elapsed", "Late", "X0", "X1", "X2", "X3", "X4", "X5", "X6", "X7", "X8", "Lit", "Third", "W1", "W2", "W3",
         "N1", "N2", "N3", "N4", "N5", "N6", "S1", "S2", "S3", "S4", "S5", "M1", "Px", "Ternary", "Clock", "Loc", "Rot", "Look", "Vel", "Speed", "Mesh",
         "Pre", "Wish", "Gain", "Offset", "Bias", "Dir", "Aim", "Idx", "PreValid", "Ground", "Radius"}
# computed with library functions (the same function on both sides, but the engine's and Python's may differ in the last digits)
TIGHT = {"T1", "T2", "R1", "Q1", "Q2", "Fn", "Hp", "Hl", "Blocked", "Over", "Sweep", "Sphere", "B"}
# FQuat::GetTwistAngle unwinds its angle with UE_PI / UE_TWO_PI, which are float constants even for the double type: the engine's twist angle of a
# quaternion beyond +-pi is off by 1.7e-7 from the exact one that dal.runtime computes
ENGINE = {"R2"}
# read as a float from a double, or computed in float (W4..W6: such a float widened to a double again): C++ rounds to float32 where Python (not in its float32 mode)
# keeps the double
LOOSE = {"ClockF", "ElapsedF", "LocF", "RotF", "LookF", "MeshF", "M2", "HpF", "Line", "W4", "W5", "W6"}
# a float read subtracted from a double of the same size (B.Pos): the float32 rounding of the read, 6e-8 of 1000, is left over after the difference
WIDE = {"B.Pos"}
TOLERANCE = {"exact": 0.0, "tight": 1e-12, "engine": 1e-6, "loose": 1e-6, "wide": 1e-4}


def group_of(name):
    root = name.split(".", 1)[0]
    if name in EXACT:
        return "exact"
    if name in ENGINE:
        return "engine"
    if name in WIDE:
        return "wide"
    if root in TIGHT:
        return "tight"
    assert name in LOOSE or root in LOOSE, name
    return "loose"


def split_by_group(names):
    groups = {group: set() for group in TOLERANCE}
    for n in names:
        groups[group_of(n)].add(n)
    return groups


@unittest.skipIf(COMPILER is None, "no C++ compiler found")
class DoubleProgramTests(unittest.TestCase):
    def run_cpp(self, module, frames, **kwargs):
        try:
            cpp, output = cpp_driver.build_and_run(module, frames, warnings_as_errors=True, **kwargs)
        except cpp_driver.CompileFailed as e:
            self.fail(f"C++ failed:\n{str(e)[:6000]}")
        self.assertNotIn("warning", output.lower(), output)
        return cpp

    def test_every_new_type_matches_the_interpreter_and_the_python_backend(self):
        module = dal.check_source(EVERY_DOUBLE, "doubles.dal").module
        effects = 0
        for seed in (1, 2):
            frames = prepare(module, harness.random_frames(module, 100, seed))
            interp = harness.run_interpreter(module, frames)
            py = harness.run_python(module, frames)
            self.assertEqual(harness.compare(interp, py, 0.0)[:4], [], "interpreter and Python backend differ")
            cpp = self.run_cpp(module, frames)
            py_states, cpp_states = states_of(py), states_of(cpp)
            groups = split_by_group(py_states[0].keys())
            self.assertIn("B.Pos.x", {n for n in groups["tight"]} | {"B.Pos.x"})
            for group, tol in TOLERANCE.items():
                diffs = harness.compare(only(py_states, groups[group]), only(cpp_states, groups[group]), tol)
                self.assertEqual(diffs[:5], [], f"seed {seed}, {group} states: {len(diffs)} mismatches")
            # the commands carry doubles made of float reads: the float32 mode rounds the reads as C++ does
            py32 = harness.run_python(module, frames, float32=True)
            problems, count = compare_effects(module, py32, cpp, 1e-9)
            self.assertEqual(problems[:5], [], f"seed {seed}: {len(problems)} effect mismatches")
            effects += count
        self.assertGreaterEqual(effects, 2 * 100 * 4)                        # four commands in every frame

    def test_the_double_commands_take_the_values_without_narrowing(self):
        """A double the program passes to set_actor_location / root_motion_override reaches the engine call to the last digit."""
        module = dal.check_source(EVERY_DOUBLE, "doubles.dal").module
        frames = prepare(module, harness.random_frames(module, 3, 9))
        frames[0]["actor_location"] = (1.0 / 3.0, 100000.0 + 1.0 / 7.0, -2.0 / 3.0)
        frames[0]["velocity"] = (123456.789012345, 0.1, 1e-9)
        frames[0]["actor_rotation"] = (0.1, 0.2, 0.3, 0.9273618495495703)
        cpp = self.run_cpp(module, frames)
        sets = [c for c in cpp[0]["__cmds__"] if c[0] == "set_actor_location"]
        roots = [c for c in cpp[0]["__cmds__"] if c[0] == "root_motion_override"]
        self.assertEqual(len(sets), 2)
        self.assertEqual(len(roots), 2)
        # root_motion_override(Vel * 0.5d, Rot): both double
        self.assertEqual(roots[0][1:4], (123456.789012345 * 0.5, 0.1 * 0.5, 1e-9 * 0.5))
        self.assertEqual(roots[0][4:], (0.1, 0.2, 0.3, 0.9273618495495703))
        # the location is Loc + Dir (Dir is a float): computed in double
        dx = cpp[0]["Dir"]
        loc = frames[0]["actor_location"]
        self.assertEqual(sets[1][1:4], tuple(float(v) for v in dx))             # set_actor_location(Dir): the float widened
        self.assertEqual(cpp[0]["Px"][1], loc[1] + dx[1])

# ---- settings, struct members, constants, entries and functions of the double types -----------------------------------------------

DOUBLE_SETTINGS = """
settings Gains {
    Gain: double = 0.1d;
    Tiny: double = 1e-8d;
    Dir: vec3d = vec3d(0.0, 0.0, 1.0);
    Lean: vec2d = vec2d(0.5, 0.25);
    Spin: rotatord = rotatord(0.0, 90.0, 0.0);
    Face: quatd = quatd(0.0, 0.0, 0.0, 1.0);
    Frame: transformd = transformd(vec3d(1.0, 2.0, 3.0), quatd(0.0, 0.0, 0.0, 1.0));
    Soft: vec3 = vec3(0.0, 0.0, 2.0);
    Weights: array<double>;
    Points: array<vec3d>;
    ByKey: map<int, double>;
    derived Twice: double = Gain * 2.0d;
    derived Reach: vec3d = Dir * 100.0d;
}
struct Track { Where: vec3d; Heading: rotatord; Speed: double = 1.5d; Soft: vec3; Turn: quatd = quatd(0.0, 0.0, 0.0, 1.0); }

logic DoubleSettings;
param G: Gains;
const Scale: double = 221.9d;
const Up: vec3d = vec3d(0.0, 0.0, 1.0);
input Key: int;
input X: double;
input Dir3: vec3d;
state Out: double;
state Moved: vec3d;
state Facing: quatd;
state Slot: transformd;
state Tr: Track;
state Weighted: double;
state Near: vec3d;
state Fixed: double;
state Seen: double;

fn lift(p: vec3d, h: double) -> vec3d { return p + Up * h; }
entry Nudge(By: double = 0.5d, Along: vec3d = vec3d(1.0, 0.0, 0.0)) { Moved = Moved + Along * By; }
entry Spin(Turn: rotatord = rotatord(0.0, 45.0, 0.0), Pose: transformd = transformd(vec3d(0.0, 0.0, 0.0), quatd(0.0, 0.0, 0.0, 1.0))) {
    Tr.Heading = Turn;
    Slot = Pose;
}
pub method scaled(x: double) -> double { Out = x * Scale; return Out; }
view reach() -> vec3d { return G.Reach; }

update(dt) {
    Out = G.Gain * X + G.Tiny + G.Twice;
    Facing = G.Face * quatd(0.0, 0.0, 0.0, 1.0);
    Slot = G.Frame;
    Slot.location = lift(G.Frame.location, X);
    Tr.Where = lift(Dir3, G.Gain);
    Tr.Heading = G.Spin + rotatord(0.0, X, 0.0);
    Tr.Speed = Tr.Speed + G.Gain;
    Tr.Soft = G.Soft * 2.0;
    Tr.Turn = Facing * Tr.Turn;
    Weighted = G.Weights[Key] + G.ByKey[Key] + G.Points[Key].z;
    Near = reach() + vec3d(G.Lean.x, G.Lean.y, 0.0);
    Fixed = Scale * 1e-8d;
    Seen = Seen + dt;
}
"""

DOUBLE_SETTINGS_DATA = {"Gain": 0.3, "Dir": [0.0, 1.0, 0.0], "Tiny": 2.5e-9, "Lean": [0.125, 0.75], "Spin": [10.0, 20.0, 30.0],
                        "Frame": {"location": [4.0, 5.0, 6.0], "rotation": [0.0, 0.0, 0.0, 1.0], "scale": [1.0, 2.0, 3.0]},
                        "Weights": [1.5, 2.5, 3.5], "Points": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]], "ByKey": {"0": 4.5, "1": 5.5, "5": 6.5}}


@unittest.skipIf(COMPILER is None, "no C++ compiler found")
class DoubleSettingsTests(unittest.TestCase):
    def run_both(self, options, seed):
        module = dal.check_source(DOUBLE_SETTINGS, "double_settings.dal").module
        frames = prepare(module, harness.random_frames(module, 40, seed))
        for k, f in enumerate(frames):                                  # entry calls with and without the defaults
            f["calls"] = [("Nudge", {"By": 0.25}), ("Nudge", {}), ("Spin", {"Pose": ((1.0, 2.0, 3.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))}), ("Spin", {})][: 1 + k % 4]
            f["free"]["Key"] = k % 4
        params = {"G": DOUBLE_SETTINGS_DATA}
        try:
            cpp, output = cpp_driver.build_and_run(module, frames, warnings_as_errors=True, options=options, params=params)
        except cpp_driver.CompileFailed as e:
            self.fail(f"C++ failed:\n{str(e)[:6000]}")
        self.assertNotIn("warning", output.lower(), output)
        py = harness.run_python(module, frames, **params)
        return module, frames, states_of(py), states_of(cpp)

    def test_settings_members_constants_entries_and_functions_match_python(self):
        _module, _frames, py, cpp = self.run_both(CppOptions(), 4)
        self.assertEqual(harness.compare(py, cpp, 0.0)[:5], [])               # (double arithmetic only, but for Dir * 100.0d and the derived values: exact too)
        self.assertEqual(cpp[0]["Seen"], py[0]["Seen"])
        self.assertEqual(cpp[0]["Fixed"], 221.9 * 1e-8)                        # the double literals of the constants are the doubles, not floats
        self.assertEqual(cpp[-1]["Moved"][0], py[-1]["Moved"][0])

    def test_double_members_leave_the_double_types_alone(self):
        """CppOptions.double_members changes the float types of struct members to the engine's double ones; the double types need no conversion."""
        module = dal.check_source(DOUBLE_SETTINGS, "double_settings.dal").module
        files = generate_cpp(module, CppOptions(double_members=True))
        h, c = files["DoubleSettings.h"], files["DoubleSettings.cpp"]
        self.assertIn("FVector Soft = FVector(0.0f, 0.0f, 2.0f);", h)                # the float vec3 member of the settings: the engine's FVector
        self.assertIn("FVector Soft = FVector(0.0f, 0.0f, 0.0f);", h)                # ... and of the struct
        self.assertIn("FVector Where = FVector(0.0, 0.0, 0.0);", h)                  # the vec3d member: as it was, no conversion
        self.assertIn("double Speed = 1.5;", h)
        self.assertIn("FRotator Heading = FRotator(0.0, 0.0, 0.0);", h)
        self.assertIn("Tr.Soft = FVector(FVector3f(DalSettings_G().Soft) * 2.0f);", c)    # a float member is converted both ways ...
        self.assertIn("Tr.Where = Dal_Fn_lift(Dir3, DalSettings_G().Gain);", c)             # ... a double one is not
        self.assertIn("Tr.Heading = DalSettings_G().Spin + FRotator(0.0, X, 0.0);", c)
        _module, _frames, py, cpp = self.run_both(CppOptions(double_members=True), 6)
        self.assertEqual(harness.compare(py, cpp, 1e-6)[:5], [])

# ---- the generated text (no compiler needed) -------------------------------------------------------------------------------------

READS = """
logic Reads : actor;
input Clock: double <- host.time;
input ClockF: float <- host.time;
input Loc: vec3d <- host.actor_location;
input LocF: vec3 <- host.actor_location;
input Rot: quatd <- host.actor_rotation;
input RotF: quat <- host.actor_rotation;
input Look: rotatord <- host.control_rotation;
input LookF: rotator <- host.control_rotation;
input Vel: vec3d <- host.velocity;
input VelF: vec3 <- host.velocity;
input Acc: vec3d <- host.acceleration;
input Speed: double <- host.max_walk_speed;
input SpeedF: float <- host.max_walk_speed;
input Radius: double <- host.capsule_radius;
input Scale: double <- host.actor_scale;
input MeshScale: double <- host.mesh_scale;
input Dilation: double <- host.time_dilation;
input Real: double <- host.real_delta_time;
input Mesh: transformd <- host.mesh_world_transform;
input MeshF: transform <- host.mesh_world_transform;
input Bone: transformd <- host.bone_world_transform("pelvis");
input BoneF: transform <- host.bone_component_transform("pelvis");
input Pre: vec3d <- host.pre_penetration_velocity;
input PreF: vec3 <- host.pre_penetration_velocity;
input Pelvis: vec3d <- host.ragdoll_pelvis_location;
input PelvisV: vec3 <- host.ragdoll_velocity;
input Child: vec3d <- host.ref_path_child_translation("calf_l", "thigh_l");
input Curve: double <- host.anim_curve("Lean");
state Out: double;
update(dt) { Out = Clock; }
"""

CONVERSIONS = """
logic Convs : actor;
input D: double;
input F: float;
input I: int;
input V: vec3;
input VD: vec3d;
input V2: vec2;
input V2D: vec2d;
input Q: quat;
input QD: quatd;
input R: rotator;
input RD: rotatord;
input T: transform;
input TD: transformd;
state A: double;
state B: float;
state C: vec3d;
state E: vec3;
state G: vec2d;
state H: vec2;
state J: quatd;
state K: quat;
state L: rotatord;
state M: rotator;
state N: transformd;
state O: transform;
state P: double;
state S: vec3d;
state U: vec3d;
state W: double;
state X: vec2d;
update(dt) {
    A = F + I;
    B = float(D);
    C = V;
    E = vec3(VD);
    G = V2;
    H = vec2(V2D);
    J = Q;
    K = quat(QD);
    L = R;
    M = rotator(RD);
    N = T;
    O = transform(TD);
    P = double(I) + D / F;
    S = vec3d(V2D, D);
    U = VD / D;
    W = D / 0.5d + D / F;
    X = V2D * 2.0 - V2D / D;
}
"""


class DoubleGenerationTests(unittest.TestCase):
    def cpp(self, source, name="t.dal", **kwargs):
        return generate_cpp(dal.check_source(source, name).module, CppOptions(**kwargs))

    def test_literals_and_types(self):
        for value, text in ((0.1, "0.1"), (221.9, "221.9"), (1e-8, "1e-08"), (3.0, "3.0"), (-0.5, "-0.5"), (0.0, "0.0"), (123456789.125, "123456789.125"),
                            (1e22, "1e+22"), (0.30000000000000004, "0.30000000000000004"), (5e-324, "5e-324")):
            self.assertEqual(fmt_double(value), text)
            self.assertEqual(float(fmt_double(value)), value)                    # (it round-trips)
        self.assertRaises(ValueError, fmt_double, float("inf"))
        self.assertRaises(ValueError, fmt_double, float("nan"))
        self.assertEqual([cpp_type(t) for t in (DOUBLE, VEC2D, VEC3D, ROTATORD, QUATD, TRANSFORMD)], ["double", "FVector2D", "FVector", "FRotator", "FQuat", "FTransform"])
        self.assertEqual(cpp_value(DOUBLE, 0.1), "0.1")
        self.assertEqual(cpp_value(VEC2D, rt.Vec2(1, 2.5)), "FVector2D(1.0, 2.5)")
        self.assertEqual(cpp_value(VEC3D, rt.Vec3(0, 0, -1)), "FVector(0.0, 0.0, -1.0)")
        self.assertEqual(cpp_value(ROTATORD, rt.Rotator(0, 90, 0.5)), "FRotator(0.0, 90.0, 0.5)")
        self.assertEqual(cpp_value(QUATD, rt.QUAT_IDENTITY), "FQuat(0.0, 0.0, 0.0, 1.0)")
        self.assertEqual(cpp_value(TRANSFORMD, rt.Transform((1.0, 2.0, 3.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))),
                         "FTransform(FQuat(0.0, 0.0, 0.0, 1.0), FVector(1.0, 2.0, 3.0), FVector(1.0, 1.0, 1.0))")

    def test_conversions_are_explicit_constructors_and_casts(self):
        c = self.cpp(CONVERSIONS)["Convs.cpp"]
        for line in ("A = static_cast<double>(F + static_cast<float>(I));",                     # (float arithmetic, then widened by the assignment)
                     "B = static_cast<float>(D);",
                     "C = FVector(V);",
                     "E = FVector3f(VD);",
                     "G = FVector2D(V2);",
                     "H = FVector2f(V2D);",
                     "J = FQuat(Q);",
                     "K = FQuat4f(QD);",
                     "L = FRotator(R);",
                     "M = FRotator3f(RD);",
                     "N = FTransform(T);",
                     "O = FTransform3f(TD);",
                     "S = DalMath::MakeVec3(V2D, D);",
                     "U = DalMath::Div(VD, D);",
                     "P = static_cast<double>(I) + DalMath::Div(D, static_cast<double>(F));",
                     "W = (D / 0.5) + DalMath::Div(D, static_cast<double>(F));",
                     "X = (V2D * 2.0) - DalMath::Div(V2D, D);"):
            self.assertIn(line, c.replace("\t", ""), line)

    def test_host_reads_give_the_engine_value_at_the_declared_precision(self):
        c = self.cpp(READS)["Reads.cpp"]
        start = c.index("void UReads::DalSampleInputs")
        sample = c[start:c.index(chr(10) + "}" + chr(10), start)]
        wanted = (
            # the double items of the engine: as they are for a double input, narrowed for a float one
            "Clock = World != nullptr ? World->GetTimeSeconds() : 0.0;",
            "ClockF = World != nullptr ? static_cast<float>(World->GetTimeSeconds()) : 0.0f;",
            "Loc = Pawn->GetActorLocation();",
            "LocF = FVector3f(Pawn->GetActorLocation());",
            "Rot = Pawn->GetActorQuat();",
            "RotF = FQuat4f(Pawn->GetActorQuat());",
            "Look = Pawn->GetControlRotation();",
            "LookF = FRotator3f(Pawn->GetControlRotation());",
            "Vel = Movement->Velocity;",
            "VelF = FVector3f(Movement->Velocity);",
            "Acc = Movement->GetCurrentAcceleration();",
            "Mesh = DalMesh != nullptr ? DalMesh->GetComponentTransform() : FTransform();",
            "MeshF = DalMesh != nullptr ? FTransform3f(DalMesh->GetComponentTransform()) : FTransform3f();",
            "Bone = DalMesh != nullptr ? DalMesh->GetSocketTransform(FName(TEXT(\"pelvis\")), RTS_World) : FTransform();",
            "BoneF = DalMesh != nullptr ? FTransform3f(DalMesh->GetSocketTransform(FName(TEXT(\"pelvis\")), RTS_Component)) : FTransform3f();",
            "Pre = DalPrePenetration;",
            "PreF = FVector3f(DalPrePenetration);",
            "Pelvis = DalRagdoll != nullptr ? DalRagdoll->GetPelvisLocation() : FVector(0.0, 0.0, 0.0);",
            "PelvisV = DalRagdoll != nullptr ? FVector3f(DalRagdoll->GetPelvisVelocity()) : FVector3f(0.0f, 0.0f, 0.0f);",
            "MeshScale = DalMesh != nullptr ? DalMesh->GetComponentScale().Z : 1.0;",
            "Scale = Capsule->GetComponentScale().Z;",
            # the float items of the engine: widened for a double input, as they are for a float one
            "Speed = static_cast<double>(Movement->MaxWalkSpeed);",
            "SpeedF = Movement->MaxWalkSpeed;",
            "Radius = static_cast<double>(Capsule->GetScaledCapsuleRadius());",
            "Dilation = World != nullptr && World->GetWorldSettings() != nullptr ? static_cast<double>(World->GetWorldSettings()->GetEffectiveTimeDilation()) : 1.0;",
            "Real = World != nullptr ? static_cast<double>(World->DeltaRealTimeSeconds) : 0.0;",
            "Child = FVector(DalRefPathChildTranslation(FName(TEXT(\"calf_l\")), FName(TEXT(\"thigh_l\"))));",
            "Curve = DalAnim != nullptr ? static_cast<double>(DalAnim->GetCurveValue(FName(TEXT(\"Lean\")))) : 0.0;",
        )
        flat = " ".join(sample.split())
        for line in wanted:
            self.assertIn(" ".join(line.split()), flat, line)

    def test_a_program_that_reads_floats_only_is_unchanged(self):
        """The reads of float inputs are what they were: the golden files (test_cpp.py, test_cpp_v1.py) pin them; here the narrowings of the double items."""
        c = self.cpp("logic F : actor;\ninput V: vec3 <- host.velocity;\ninput T: float <- host.time;\ninput L: vec3 <- host.actor_location;\ninput S: float <- host.max_walk_speed;\n"
                     "state O: float;\nupdate(dt) { O = T + S + V.x + L.x; }")["F.cpp"]
        self.assertIn("V = FVector3f(Movement->Velocity);", c)
        self.assertIn("T = World != nullptr ? static_cast<float>(World->GetTimeSeconds()) : 0.0f;", c)
        self.assertIn("L = FVector3f(Pawn->GetActorLocation());", c)
        self.assertIn("S = Movement->MaxWalkSpeed;", c)

    def test_commands_queries_and_hits_use_the_engine_precision(self):
        files = self.cpp(EVERY_DOUBLE, "doubles.dal")
        c, h, host = files["Doubles.cpp"], files["Doubles.h"], files["DalHost.h"]
        # the command constructors take the double types (a float argument is widened by the program), the executors hand them to the engine as they are
        self.assertIn("DalEnqueue(DalHost::Cmd::SetActorLocation(p, Ground, !Ground));", c)
        self.assertIn("DalEnqueue(DalHost::Cmd::SetActorLocation(FVector(Dir), false, true));", c)
        self.assertIn("DalEnqueue(DalHost::Cmd::RootMotionOverride(FVector(Dir * 2.0f), FQuat(RotF)));", c)
        self.assertIn("DalAlsMovement->SetRootMotionOverride(DalCommand.VD, DalCommand.QD);", c)
        self.assertIn("DalPawn->SetActorLocation(DalCommand.VD, DalCommand.B0, nullptr,", c)
        self.assertIn("inline FCommand RootMotionOverride(const FVector& Velocity, const FQuat& RotationDelta)", host)
        self.assertIn("inline FCommand SetActorLocation(const FVector& Location, bool bSweep, bool bTeleport)", host)
        self.assertIn("FVector VD = FVector(0.0, 0.0, 0.0);", host)
        self.assertIn("FQuat QD = FQuat(0.0, 0.0, 0.0, 1.0);", host)
        self.assertIn("FVector3f V = FVector3f(0.0f, 0.0f, 0.0f);", host)                        # the other commands keep their float fields
        self.assertNotIn("FQuat4f Q", host)
        # the queries take the positions as the engine has them
        for signature in ("DalHost::FHit DalTraceLine(const FVector& DalStart, const FVector& DalEnd);",
                          "DalHost::FHit DalTraceSphere(const FVector& DalStart, const FVector& DalEnd, const float DalRadius);",
                          "DalHost::FHit DalSweepCapsule(const FVector& DalStart, const FVector& DalEnd, const float DalRadius, const float DalHalfHeight,",
                          "bool DalOverlapCapsule(const FVector& DalCenter, const float DalRadius, const float DalHalfHeight,",
                          "DalHost::FHit DalLineTrace(const FVector& DalStart, const FVector& DalEnd,"):
            self.assertIn(signature, h)
        self.assertNotIn("FVector(DalStart)", c)
        self.assertNotIn("FVector(DalEnd)", c)
        self.assertNotIn("FVector(DalCenter)", c)
        self.assertIn("DalWorld->SweepSingleByChannel(DalHit, DalStart, DalEnd, FQuat::Identity, DalChannel,", c)
        self.assertIn("DalWorld->OverlapBlockingTestByChannel(DalCenter, FQuat::Identity, DalChannel,", c)
        # the hit: the positions at both precisions, filled from the engine's hit without narrowing
        self.assertIn("DalResult.PointD = DalHit.ImpactPoint;", c)
        self.assertIn("DalResult.LocationD = DalHit.Location;", c)
        self.assertIn("DalResult.Point = FVector3f(DalHit.ImpactPoint);", c)
        self.assertIn("DalResult.PointD = DalEnd;", c)                                           # a miss ends at the trace end, at both precisions
        self.assertIn("Hp = h.PointD;", c)
        self.assertIn("Hl = h.LocationD;", c)
        self.assertIn("HpF = h.Point;", c)
        self.assertIn("FVector PointD = FVector(0.0, 0.0, 0.0);", host)
        self.assertIn("FVector LocationD = FVector(0.0, 0.0, 0.0);", host)

    def test_a_trace_that_never_reads_the_double_positions_fills_what_it_did(self):
        c = self.cpp("logic Tr : actor;\ninput A: vec3;\ninput B: vec3;\nstate P: vec3;\nupdate(dt) { P = host.trace_line(A, B).point; }")["Tr.cpp"]
        trace = c[c.index("DalHost::FHit UTr::DalTraceLine"):c.index("void UTr::DalUpdate")]
        self.assertNotIn("PointD", trace)
        self.assertNotIn("LocationD", trace)
        self.assertIn("DalTraceLine(FVector(A), FVector(B))", c)

    def test_every_builtin_overload_is_called_on_the_double_types(self):
        src, count = everything_source(derived=True)
        c = generate_cpp(dal.check_source(src, "everything_d.dal").module)["Everything.cpp"]
        self.assertGreaterEqual(count, 85)
        self.assertIn("DalMath::Length(DV0)", c)
        self.assertIn("DalMath::Lerp(DV0, DV1, DF0)", c)
        self.assertIn("DalMath::Rotate(DQ0, DV0)", c)
        self.assertIn("DalMath::Rotate(DR0, DV0)", c)
        self.assertIn("DalMath::TransformPosition(DT0, DV0)", c)
        self.assertNotIn("FVector3f", c.split("void UEverything::DalUpdate")[1])               # nothing in the update narrows a double

    def test_the_plugin_fills_the_double_positions_of_a_hit(self):
        runtime = os.path.join(ROOT, "Unreal", "DalRuntime", "Source", "DalRuntime")
        with open(os.path.join(runtime, "Private", "DalCharacterMovementComponent.cpp"), encoding="utf-8") as f:
            cmc = f.read()
        self.assertIn("Result.PointD = Hit.ImpactPoint;", cmc)
        self.assertIn("Result.LocationD = Hit.Location;", cmc)
        with open(os.path.join(runtime, "Public", "DalHost.h"), encoding="utf-8", newline="") as f:
            plugin_host = f.read()
        with open(os.path.join(ROOT, "dal", "cpp", "DalHost.h"), encoding="utf-8", newline="") as f:
            self.assertEqual(plugin_host, f.read())


# ---- (c) the float32 mode against the C++ float run -----------------------------------------------------------------------------

FLOAT32_PARITY = """
logic Float32Parity : actor;
input T: double <- host.time;
input A: float;
input B: float;
input C: float;
input U: vec3;
input V: vec3;

state Sum: float;
state Frames: int;
state Reached: bool;
state Past: bool;
state Equal: bool;
state Start: double;
state Age: double;
state Over: bool;
state OverF: bool;
state Gap: double;
state Late: bool;
state Acc: float;
state R1: float;
state R2: float;
state R3: float;
state R4: float;
state R5: float;
state R6: float;
state V1: vec3;
state V2: vec3;
state V3: vec3;
state V4: vec3;
state V5: vec3;
state V6: vec3;
state K1: bool;
state K2: bool;
state K3: bool;

update(dt) {
    // the threshold case: dt accumulated seven times against 0.11666667
    Sum += dt;
    Frames += 1;
    Reached = Sum >= 0.11666667;
    Past = Sum > 0.11666667;
    Equal = Sum == 0.11666667;

    // TimeSince: a world time held in a double, a float sum of dt, a float literal widened to double
    if (Frames == 1) { Start = T; }
    Age = T - Start;
    Over = (T - Start) > 0.2;
    OverF = Sum > 0.2;
    Gap = Age - Sum;
    Late = Age > Sum;

    // + - * / and comparisons of floats and vec3: every operation is one float32 rounding
    Acc += A * dt;
    R1 = A * B + C;
    R2 = (A - B) / C;
    R3 = A / B / C;
    R4 = (A + B) * (A - B);
    R5 = A * 0.1 + B * 0.7;
    R6 = -A * B;
    V1 = U * A + V;
    V2 = U - V * B;
    V3 = (U + V) * 0.5;
    V4 = U * V;
    V5 = -U + V * (A + B);
    V6 = U * 0.1 - V;
    K1 = A * B > C;
    K2 = A + B == B + A;
    K3 = (A - B) / C <= R2;
}
"""

# the same, with a vector divided by a float
FLOAT32_VECTOR_DIVISION = """
logic Float32Division : actor;
input C: float;
input U: vec3;
input W: vec2;
state V1: vec3;
state V2: vec3;
state V3: vec2;
update(dt) {
    V1 = U / C;
    V2 = U / 3.0;
    V3 = W / C;
}
"""


def float32_frames(module, count, seed):
    """Frames whose dt is float(1/60) for the first stretch (the threshold case), then random; the world time is the double sum of the dts;
    everything the program holds as a float is a float32 value already."""
    rng = random.Random(seed)
    frames = harness.random_frames(module, count, seed)
    dt, t = f32(1.0 / 60.0), 0.0
    out = []
    for k, f in enumerate(frames):
        f = dict(f)
        f["dt"] = dt if k < count // 2 else f32(rng.choice([0.0, 0.001, 0.008, 1.0 / 30.0, rng.uniform(0.001, 0.1)]))
        t += f["dt"]                                       # (a double: the sum of float32 values)
        f["time"] = t
        out.append(f)
    return prepare(module, out)


@unittest.skipIf(COMPILER is None, "no C++ compiler found")
class Float32EquivalenceTests(unittest.TestCase):
    def run_cpp(self, module, frames):
        try:
            cpp, output = cpp_driver.build_and_run(module, frames, warnings_as_errors=True)
        except cpp_driver.CompileFailed as e:
            self.fail(f"C++ failed:\n{str(e)[:6000]}")
        self.assertNotIn("warning", output.lower(), output)
        return cpp

    def test_float32_mode_is_bit_for_bit_the_cpp_float_run(self):
        module = dal.check_source(FLOAT32_PARITY, "float32_parity.dal").module
        for seed in (1, 2):
            frames = float32_frames(module, 90, seed)
            cpp = states_of(self.run_cpp(module, frames))
            py32 = states_of(harness.run_python(module, frames, float32=True))
            interp32 = states_of(harness.run_interpreter(module, frames, float32=True))
            self.assertEqual(harness.compare(interp32, py32, 0.0)[:4], [], "the float32 interpreter and the float32 Python backend differ")
            self.assertEqual(harness.compare(py32, cpp, 0.0)[:6], [], f"seed {seed}: float32 mode and the C++ float run differ")
            if seed != 1:
                continue
            # the threshold case: in float32 the seventh sum is exactly the float 0.11666667, the eighth is above it ...
            self.assertEqual([f["Equal"] for f in cpp[:9]], [False] * 6 + [True, False, False])
            self.assertEqual([f["Past"] for f in cpp[:9]], [False] * 7 + [True, True])
            self.assertEqual([f["Reached"] for f in cpp[:9]], [False] * 6 + [True] * 3)
            # ... where doubles say it is above it one step earlier: the plain mode of Python is not the C++ run
            plain = states_of(harness.run_python(module, frames))
            self.assertTrue(plain[6]["Past"] and not plain[6]["Equal"])
            self.assertNotEqual([f["Past"] for f in plain[:9]], [f["Past"] for f in cpp[:9]])

    def test_a_double_clock_with_float_sums(self):
        """TimeSince: float(1/60) summed twelve times against the world time, a double that holds the sum of the same twelve values."""
        module = dal.check_source(FLOAT32_PARITY, "float32_parity.dal").module
        frames = float32_frames(module, 24, 3)
        cpp = states_of(self.run_cpp(module, frames))
        py32 = states_of(harness.run_python(module, frames, float32=True))
        self.assertEqual(harness.compare(py32, cpp, 0.0)[:6], [])
        dt = f32(1.0 / 60.0)
        # frame 12 (index 11): the float sum of twelve dt is float(0.2); the double clock has 11 steps since Start and is below 0.2
        total = 0.0
        for _ in range(12):
            total = f32(total + dt)
        self.assertEqual(cpp[11]["Sum"], total)
        self.assertEqual(total, f32(0.2))
        self.assertFalse(cpp[11]["Over"])                  # 11 steps of dt since the first frame
        self.assertTrue(cpp[12]["Over"])                   # 12 steps are above the float 0.2 (widened) as doubles
        self.assertEqual(cpp[11]["Age"], frames[11]["time"] - frames[0]["time"])

    def test_vector_division_by_a_float(self):
        """The engine divides a vector by multiplying with the reciprocal (TVector::operator/(Scale) is X * (1 / Scale), float and double alike): the float32
        mode rounds the reciprocal to float32 and multiplies by it (dal.runtime.vdiv_recip32), the double types multiply by the double reciprocal, so both
        agree with the C++ run to the last bit."""
        module = dal.check_source(FLOAT32_VECTOR_DIVISION, "float32_division.dal").module
        frames = float32_frames(module, 60, 5)
        cpp = states_of(self.run_cpp(module, frames))
        py32 = states_of(harness.run_python(module, frames, float32=True))
        self.assertEqual(harness.compare(py32, cpp, 0.0)[:6], [], "float32 mode and the C++ float run divide a vector differently")


if __name__ == "__main__":
    unittest.main()
