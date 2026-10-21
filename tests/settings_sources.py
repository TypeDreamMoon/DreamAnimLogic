"""Settings programs shared by the v3 tests: a data-asset sketch in the shape of ALS (nested settings with a derived member,
tag- and enum-keyed tables, curves, lists, sequence / montage values) read by a logic through a `param`."""

DECLS = """
tags AlsMode = "Als.Mode" { View, Velocity, Aiming }
enum Pose { Stand, Crouch, Prone }

settings Trace {
    Reach: float = 75.0;
    Range: vec2 = vec2(50.0, 225.0);
    Angle: float = 35.0;
    derived AngleCos: float = cos(deg_to_rad(Angle));
    derived Span: float = Range.y - Range.x;
    derived Tall: bool = Range.y > 200.0;
}
settings Gait {
    Walk: float = 175.0;
    Run: float = 375.0;
    Accel: curve3;
    RotSpeed: curve;
    Clip: sequence;
    Style: Pose = Pose.Crouch;
    Facing: AlsMode;
}
settings Stances { ByPose: map<Pose, Gait>; Bias: float = 0.5; }
settings Rules {
    Speed: float = 1.0;
    Trace: Trace;
    Modes: map<AlsMode, Stances>;
    Counts: map<int, float>;
    Names: array<float>;
    Traces: array<Trace>;
    Clip: montage;
    Fallback: sequence;
    derived Doubled: float = Speed * 2.0 + Trace.AngleCos;
}
"""

PROGRAM = DECLS + """
logic Tuned;
param Cfg: Rules;
input M: AlsMode;
input P: Pose;
input X: float;
input K: int;
state Out: float;
state Cos: float;
state Span: float;
state Tall: bool;
state Doubled: float;
state Acc: vec3;
state Rot: float;
state Has: bool;
state HasCount: bool;
state Q: sequence = none;
state Mon: montage = none;
state HasQ: bool;
state HasMon: bool;
state Same: bool;
state Style: Pose;
state Facing: AlsMode;
state Len: int;
state TraceLen: int;
state Pick: float;
state Third: float;
state Bias: float;
state Near: float;

fn blend(g: Gait, x: float) -> float {
    return g.Walk + (g.Run - g.Walk) * clamp(x / 100.0, 0.0, 1.0) + g.RotSpeed.eval(x);
}

update(dt) {
    let st = Cfg.Modes[M];
    let g = st.ByPose[P];
    Out = blend(g, X) + Cfg.Speed;
    Cos = Cfg.Trace.AngleCos;
    Span = Cfg.Trace.Span;
    Tall = Cfg.Trace.Tall;
    Doubled = Cfg.Doubled;
    Acc = g.Accel.eval(X);
    Rot = g.RotSpeed.eval(X);
    Has = Cfg.Modes.has(M);
    HasCount = Cfg.Counts.has(K);
    Q = g.Clip;
    Mon = Cfg.Clip;
    HasQ = valid(Q);
    HasMon = Mon != none;
    Same = Q == Cfg.Fallback;
    Style = g.Style;
    Facing = g.Facing;
    Len = Cfg.Names.len;
    TraceLen = Cfg.Traces.len;
    Pick = Cfg.Names[K] + Cfg.Counts[K];
    Third = Cfg.Traces[K].Reach;
    Bias = st.Bias;
    Near = (Q != none) ? Cfg.Speed : -Cfg.Speed;
}
"""

# data for PROGRAM: curves inline (linear and constant keys, so that the stand-in engine can evaluate them as well)
def curve(*keys, pre="constant", post="constant"):
    return {"class": "CurveFloat", "channels": [{"keys": [{"t": t, "v": v, "interp": "linear"} for t, v in keys], "pre": pre, "post": post}]}


def curve3(kx, ky, kz):
    return {"class": "CurveVector", "channels": [curve(*k)["channels"][0] for k in (kx, ky, kz)]}


DATA = {
    "Speed": 2.5,
    "Trace": {"Reach": 90.0, "Range": [10.0, 260.0], "Angle": 60.0},
    "Modes": {
        "Als.Mode.View": {
            "Bias": 0.25,
            "ByPose": {
                "Stand": {"Walk": 150.0, "Run": 400.0, "RotSpeed": curve((0.0, 0.0), (100.0, 1.0)),
                          "Accel": curve3(((0.0, 0.0), (100.0, 10.0)), ((0.0, 5.0), (50.0, 5.0)), ((0.0, 1.0), (100.0, 0.0))),
                          "Clip": "A_Stand", "Style": "Prone", "Facing": "Als.Mode.Aiming"},
                "Crouch": {"Walk": 100.0, "Clip": "A_Crouch"},
            },
        },
        "Als.Mode.Velocity": {"ByPose": {"Stand": {"Walk": 190.0, "Clip": "A_Fallback"}}},
    },
    "Counts": {"0": 1.5, "2": 4.0},
    "Names": [3.0, 5.0, 7.0],
    "Traces": [{"Reach": 11.0}, {"Reach": 22.0, "Angle": 90.0}],
    "Clip": "AM_Roll",
    "Fallback": "A_Fallback",
}
