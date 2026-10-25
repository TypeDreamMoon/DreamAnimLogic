"""Teacher-forced comparison of ``als/AlsAnimationInstance.dal`` with the recordings of the original ALS in Unreal (``G:\\DCC\\AlsTruth``).

The question (als-dal-rewrite.md 7.2): does the rewrite compute, from the state the original instance started a frame with and the inputs it had
that frame, the state the original ended the frame with?  Asked frame by frame with the state *forced* to the recorded one, errors cannot
accumulate: a deviation shows up in the frame and the field where it happens.

Per recorded frame ``f`` (``anim-recording-format.md`` 13.7):

* the instance starts from ``post_char[f]``'s ``Anim.*`` (the final state of the frame before plus the calls between the frames);
* it reads ``post_char[f]``'s ``Char.*`` (the character's state: a stand-in character logic is fed with it), ``Cmc.*``, ``Actor.*``, ``Mesh.*``,
  ``Time.*``, the curves ``curve_values[f - 1]`` and the pose ``pose[f - 1]`` (the pose evaluated last: the foot lock reads the pelvis and the IK
  feet of it, the feet in world space with the mesh transform of now);
* the animation graph's ``Call``s are not recorded.  The three entries with a ``bUpdatedThisFrame`` flag are called when the original's flag
  says it ran; ``RefreshInAir`` when the original is in the air and its vertical velocity is the one of its velocity (which is what the call
  writes).  The scene is not there either: the ground prediction sweep is answered with the recorded ``Trace.GroundPrediction`` hit, and the
  start and end the instance asks for are compared with the recorded ones;
* the result is compared with ``final[f]``'s ``Anim.*``.

Not compared: what the graph's other entries write (head, lean, grounded, standing, crouching), the queues of the transition / turn montages (the
original consumes them in the same frame: the commands are compared with the recorded ``montage_play`` events instead), the moving base.

``compare_take`` returns a ``TakeReport``; the tests in test_als_anim_truth.py assert on it.  Needs the recordings (large, not in git) and
the DreamVEd recording reader (``blender-moon-engine``, read only): both optional, the tests skip without them.
"""

from __future__ import annotations

import math
import os
import sys
from collections import Counter, defaultdict

import _bootstrap  # noqa: F401

import als_sources as A
from dal import runtime as rt
from dal.harness import ScriptedHost
from dal.pybackend import generate_python, load_class
from dal.types import EnumType, StructType

TRUTH_DIR = os.environ.get("DAL_ALS_TRUTH", r"G:\DCC\AlsTruth")
BLENDER_MODULES = os.environ.get("DAL_DREAMVED_MODULES", r"I:\DCC\blender-moon-engine\scripts\modules")
INSTANCE = A.INSTANCE
CHARACTER = A.CHARACTER

TAKES = ("lane", "features", "in_air", "speed_jumps", "stance_changes", "rotation_modes", "view_yaw_sweeps", "view_mode")


def take_path(name: str) -> str:
    return os.path.join(TRUTH_DIR, f"als_{name}.dvrec")


def available(name: str = "lane") -> bool:
    return os.path.isfile(take_path(name)) and os.path.isfile(os.path.join(BLENDER_MODULES, "dreamved", "anim", "recording.py"))


def load_recording(name: str):
    if BLENDER_MODULES not in sys.path:
        sys.path.append(BLENDER_MODULES)                            # after the repository: ``dal`` is the one of this repository
    from dreamved.anim.recording import Recording
    return Recording.load(take_path(name))


# ---- values -----------------------------------------------------------------------------------------------------------------------------------

def num(v, default=0.0):
    if v is None:
        return default
    if isinstance(v, float) and v != v:
        return default
    return float(v)


def rotator_quat(pitch, yaw, roll):
    """FRotator::Quaternion (degrees)."""
    sp, cp = math.sin(math.radians(pitch) / 2), math.cos(math.radians(pitch) / 2)
    sy, cy = math.sin(math.radians(yaw) / 2), math.cos(math.radians(yaw) / 2)
    sr, cr = math.sin(math.radians(roll) / 2), math.cos(math.radians(roll) / 2)
    return (cr * sp * sy - sr * cp * cy, -cr * sp * cy - sr * cp * sy, cr * cp * sy - sr * sp * cy, cr * cp * cy + sr * sp * sy)


def transform_compose(a, b):
    """``a * b`` of FTransform (apply a first, then b): rotation b.q * a.q, location b.q (b.s * a.loc) + b.loc, scale a.s * b.s."""
    (al, aq, as_), (bl, bq, bs) = a, b
    x, y, z, w = bq
    v = (bs[0] * al[0], bs[1] * al[1], bs[2] * al[2])
    t = (2.0 * (y * v[2] - z * v[1]), 2.0 * (z * v[0] - x * v[2]), 2.0 * (x * v[1] - y * v[0]))
    r = (v[0] + w * t[0] + (y * t[2] - z * t[1]), v[1] + w * t[1] + (z * t[0] - x * t[2]), v[2] + w * t[2] + (x * t[1] - y * t[0]))
    q = (bq[3] * aq[0] + bq[0] * aq[3] + bq[1] * aq[2] - bq[2] * aq[1], bq[3] * aq[1] - bq[0] * aq[2] + bq[1] * aq[3] + bq[2] * aq[0],
         bq[3] * aq[2] + bq[0] * aq[1] - bq[1] * aq[0] + bq[2] * aq[3], bq[3] * aq[3] - bq[0] * aq[0] - bq[1] * aq[1] - bq[2] * aq[2])
    return ((r[0] + bl[0], r[1] + bl[1], r[2] + bl[2]), q, (as_[0] * bs[0], as_[1] * bs[1], as_[2] * bs[2]))


def pose_row(row):
    """A row of the recorded pose [qx qy qz qw tx ty tz sx sy sz] as a transform (location, rotation, scale)."""
    return ((float(row[4]), float(row[5]), float(row[6])), (float(row[0]), float(row[1]), float(row[2]), float(row[3])), (float(row[7]), float(row[8]), float(row[9])))


# ---- the recording ------------------------------------------------------------------------------------------------------------------------------

class Take:
    def __init__(self, name: str):
        self.name = name
        self.rec = load_recording(name)
        self.t = self.rec.truth
        self.n = self.rec.frame_count
        sk = self.rec.header["skeleton"]
        self.bone_names = list(sk["names"])
        self.parents = [int(p) for p in sk["parents"]]
        self.bone = {b: i for i, b in enumerate(self.bone_names)}
        self.pose = self.rec.arrays["pose"]
        self.ref = self.rec.arrays["skeleton_ref"]
        self.curve_names = list(self.rec.header["curves"])
        self.curve_values = self.rec.arrays["curve_values"]
        self.sampled = {c: self.t.sampled(c) for c in ("post_char", "final")}
        self.phase = self.t.anim_update_phase()
        self.events = self.rec.header["events"]

    def frame(self, channel: str, f: int) -> dict:
        return self.t.frame(channel, f)

    def curves(self, f: int) -> dict:
        """The curves of the pose evaluated last: the output curves of the frame before (a curve the pose does not have reads 0)."""
        if f < 1:
            return {}
        return {n: float(v) for n, v in zip(self.curve_names, self.curve_values[f - 1]) if v == v}

    def skeleton(self) -> dict:
        """The reference skeleton as the host read wants it: ``{bone: {"parent": bone or None, "translation": (x, y, z)}}``."""
        return {name: {"parent": self.bone_names[p] if p >= 0 else None, "translation": (float(self.ref[i][4]), float(self.ref[i][5]), float(self.ref[i][6]))}
                for i, (name, p) in enumerate(zip(self.bone_names, self.parents))}


def vec(flat, prefix, comps="XYZ"):
    return tuple(num(flat.get(f"{prefix}.{c}")) for c in comps)


# small quaternion helpers for the statistics only (the comparison itself is the instance's)
def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def quat_conj(q):
    return (-q[0], -q[1], -q[2], q[3])


def rotate_quat(q, v):
    qv = (q[0], q[1], q[2])
    t = tuple(2.0 * c for c in (qv[1] * v[2] - qv[2] * v[1], qv[2] * v[0] - qv[0] * v[2], qv[0] * v[1] - qv[1] * v[0]))
    c = (qv[1] * t[2] - qv[2] * t[1], qv[2] * t[0] - qv[0] * t[2], qv[0] * t[1] - qv[1] * t[0])
    return (v[0] + q[3] * t[0] + c[0], v[1] + q[3] * t[1] + c[1], v[2] + q[3] * t[2] + c[2])


def twist_z(q):
    """The twist angle about +Z in radians (FQuat::GetTwistAngle)."""
    a = 2.0 * math.atan2(q[2], q[3])
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def angle_between_signed_xy(a, b):
    def n2(v):
        k = math.hypot(v[0], v[1])
        return (v[0] / k, v[1] / k) if k > 1e-4 else (v[0], v[1])

    a, b = n2(a), n2(b)
    d = max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1]))
    cross = a[0] * b[1] - a[1] * b[0]
    return math.degrees(math.acos(d)) * ((cross > 0) - (cross < 0))


# ---- the instance and the stand-in character ---------------------------------------------------------------------------------------------------

def leaf_types(module) -> dict:
    out = {}

    def walk(prefix, ty):
        if isinstance(ty, StructType):
            for f in ty.fields:
                if f.ty.name in ("curve", "curve3") or getattr(f.ty, "settings_only", False) or getattr(f.ty, "is_settings", False):
                    continue
                walk(prefix + "." + f.name, f.ty)
        else:
            out[prefix] = ty

    for s in module.states:
        walk(s.name, s.ty)
    return out


COMPONENTS = {"vec2": "XY", "vec3": "XYZ", "quat": "XYZW", "rotator": ("Pitch", "Yaw", "Roll")}


def truth_leaves(module, flat: dict, prefix: str = "Anim.") -> dict:
    """The state leaves of ``module`` that the recorded ``flat`` dict (full truth paths) has, in the types of the module."""
    out = {}
    for path, ty in leaf_types(module).items():
        key = prefix + path
        name = ty.name
        if name in COMPONENTS:
            comps = COMPONENTS[name]
            vals = [flat.get(f"{key}.{c}") for c in comps]
            if any(v is None for v in vals):
                continue
            v = tuple(float(x) for x in vals)
            out[path] = {"vec2": rt.Vec2, "vec3": rt.Vec3, "quat": rt.Quat, "rotator": rt.Rotator}[name](*v)
            continue
        if name == "snapshot" or key not in flat:
            continue
        v = flat[key]
        if isinstance(ty, EnumType):
            m = str(v).split("::")[-1]
            if m in ty.members:
                out[path] = ty.members.index(m)
        elif getattr(ty, "is_tags", False):
            out[path] = "" if v in (None, "None") else str(v)
        elif name == "bool":
            out[path] = bool(v)
        elif name == "int":
            out[path] = int(v)
        elif name == "float":
            out[path] = float(v)
        elif getattr(ty, "is_asset", False):
            out[path] = None if v in (None, "") else str(v)
    return out


def force_state(instance, leaves: dict) -> None:
    for path, value in leaves.items():
        parts = path.split(".")
        obj = instance
        for p in parts[:-1]:
            obj = getattr(obj, p)
        setattr(obj, parts[-1], value)


class TruthHost(ScriptedHost):
    """The scripted host with the sweep answered from the recording: the hit of ``Trace.GroundPrediction`` (a sweep the original did not make is
    answered with a miss and noted)."""

    def __init__(self):
        super().__init__()
        self.forced_hit = None
        self.sweeps = []

    def sweep_capsule(self, start, end, radius, half_height, channel, responses):
        self.sweeps.append((tuple(start), tuple(end), radius, half_height))
        return self.forced_hit if self.forced_hit is not None else rt.Hit(False, end, rt.VEC3_ZERO, 0.0, 1.0, location=end)


class Tolerance:
    def __init__(self, rel=1e-4, abs_=1e-3):
        self.rel, self.abs = rel, abs_


def close(a, b, tol, angle=False):
    """(equal, absolute error) for a scalar / vector / quaternion recorded value and the instance's."""
    if isinstance(a, (tuple, list)):
        if len(a) == 4 and not isinstance(a, rt.Rotator):          # a quaternion: q and -q are the same rotation
            e1 = max(abs(x - y) for x, y in zip(a, b))
            e2 = max(abs(x + y) for x, y in zip(a, b))
            e = min(e1, e2)
        else:
            e = max(abs(x - y) for x, y in zip(a, b))
        return e <= max(tol.abs, tol.rel * max(abs(x) for x in list(a) + list(b))), e
    if isinstance(a, bool) or isinstance(b, bool) or isinstance(a, str) or isinstance(b, str) or a is None or b is None:
        return a == b, 0.0
    d = abs(((a - b) + 180.0) % 360.0 - 180.0) if angle else abs(a - b)
    return d <= max(tol.abs, tol.rel * max(abs(a), abs(b))), d


# ---- the report -----------------------------------------------------------------------------------------------------------------------------

class FieldStat:
    def __init__(self):
        self.frames = 0
        self.bad = 0
        self.max_error = 0.0
        self.worst_frame = None
        self.first_bad = None

    def add(self, frame, ok, error):
        self.frames += 1
        if not ok:
            self.bad += 1
            if self.first_bad is None:
                self.first_bad = frame
        if error > self.max_error:
            self.max_error, self.worst_frame = error, frame


class TakeReport:
    def __init__(self, name):
        self.name = name
        self.fields = defaultdict(FieldStat)
        self.frames = 0                                 # frames run
        self.skipped = Counter()
        self.entries = Counter()                        # how often each entry was called
        self.sweeps = Counter()                         # ground prediction: asked / answered / start-end matched / mismatches
        self.commands = Counter()
        self.stats = Counter()                          # how much of the interesting behaviour the recording has (so that "no mismatch" means something)
        self.flips = defaultdict(list)                  # frames with exactly one discrete field bad
        self.notes = []

    def group(self, prefixes):
        return {k: v for k, v in self.fields.items() if k.startswith(tuple(prefixes))}

    def bad_fields(self, prefixes=("",)):
        return {k: v for k, v in self.group(prefixes).items() if v.bad}

    def count(self, prefixes=("",)):
        g = self.group(prefixes)
        return sum(v.frames for v in g.values()), sum(v.bad for v in g.values())

    def max_error(self, prefixes=("",)):
        return max((v.max_error for v in self.group(prefixes).values()), default=0.0)

    def format(self, prefixes=("",), limit=12) -> str:
        total, bad = self.count(prefixes)
        lines = [f"{self.name}: {self.frames} frames, {total} field comparisons, {bad} outside the tolerance, max error {self.max_error(prefixes):.3g}"]
        for k, v in sorted(self.bad_fields(prefixes).items(), key=lambda kv: -kv[1].bad)[:limit]:
            lines.append(f"  {k}: {v.bad}/{v.frames} bad, max error {v.max_error:.3g} (frame {v.worst_frame}), first bad frame {v.first_bad}")
        return "\n".join(lines)


# ---- the run ------------------------------------------------------------------------------------------------------------------------------------

# the state the instance's update writes by itself (the entries of the graph do not touch it): compared in every frame
UPDATE_OWNED = ("LayeringState.", "PoseState.", "ViewState.", "SpineState.", "LocomotionState.", "FeetState.", "TransitionsState.bTransitionsAllowed",
                "bPendingUpdate", "TeleportedTime", "DeltaTimeWithoutTimeDilation", "PreviousGlobalTimeDilation", "LocomotionMode", "RotationMode", "Stance",
                "Gait", "ViewMode", "OverlayMode", "LocomotionAction")
ENTRY_FIELDS = {"RotateInPlaceState": ("RotateInPlaceState.bRotatingLeft", "RotateInPlaceState.bRotatingRight", "RotateInPlaceState.PlayRate"),
                "TurnInPlaceState": ("TurnInPlaceState.ActivationDelay", "TurnInPlaceState.PlayRate"),
                "DynamicTransitionsState": ("DynamicTransitionsState.FrameDelay",)}
ANGLE_FIELDS = ("YawAngle", "PitchAngle", "RotationWorldSpace.", "YawAngle")
DISCRETE_TYPES = ("bool", "int")


def host_frame(take: Take, f: int, post_char: dict, curves: dict, module_dt: float) -> dict:
    """The host reads of frame ``f`` out of the recording."""
    mode = str(post_char.get("Cmc.MovementMode", "MOVE_Walking"))
    walking = mode == "MOVE_Walking"
    mesh = ((num(post_char.get("Mesh.Location.X")), num(post_char.get("Mesh.Location.Y")), num(post_char.get("Mesh.Location.Z"))),
            rotator_quat(*vec(post_char, "Mesh.Rotation", ("Pitch", "Yaw", "Roll"))), vec(post_char, "Mesh.Scale"))
    bones = {}
    pelvis = take.bone["pelvis"]
    previous = take.pose[f - 1]
    bones["pelvis"] = {"component": pose_row(previous[pelvis])}
    for name, bone in (("ik_foot_l", "ik_foot_l"), ("ik_foot_r", "ik_foot_r"), ("VB_foot_l", "VB foot_l"), ("VB_foot_r", "VB foot_r")):
        bones[name] = {"world": transform_compose(pose_row(previous[take.bone[bone]]), mesh)}
    return {
        "dt": module_dt, "time": num(post_char.get("Time.Seconds")),
        "actor_location": vec(post_char, "Actor.Location"), "actor_rotation": vec(post_char, "Actor.Quat", "XYZW"),
        "mesh_scale": num(post_char.get("Mesh.Scale.Z"), 1.0),
        "capsule_radius": num(post_char.get("Capsule.ScaledRadius")), "capsule_half_height": num(post_char.get("Capsule.ScaledHalfHeight")),
        "max_acceleration": num(post_char.get("Cmc.MaxAccelerationWalking" if walking else "Cmc.MaxAcceleration")),
        "max_braking_deceleration": num(post_char.get("Cmc.BrakingDecelerationWalking" if walking else "Cmc.BrakingDecelerationFalling")),
        "walkable_floor_z": num(post_char.get("Cmc.WalkableFloorZ"), 0.71), "actor_ready": True,
        "time_dilation": num(post_char.get("Time.TimeDilation"), 1.0), "actor_time_dilation": 1.0, "real_delta_time": num(post_char.get("Time.Real")),
        "mesh_world_transform": mesh, "bones": bones, "curves": curves, "skeleton": {},
    }


def character_inputs(post_char: dict) -> dict:
    """The free inputs of the stand-in character: the recorded state of the real one."""
    def tag(key):
        v = post_char.get(key)
        return "" if v in (None, "None") else str(v)

    loc = {"bHasInput": bool(post_char.get("Char.LocomotionState.bHasInput")), "InputYawAngle": num(post_char.get("Char.LocomotionState.InputYawAngle")),
           "bHasVelocity": bool(post_char.get("Char.LocomotionState.bHasVelocity")), "Speed": num(post_char.get("Char.LocomotionState.Speed")),
           "Velocity": vec(post_char, "Char.LocomotionState.Velocity"), "VelocityYawAngle": num(post_char.get("Char.LocomotionState.VelocityYawAngle")),
           "bMoving": bool(post_char.get("Char.LocomotionState.bMoving")), "TargetYawAngle": num(post_char.get("Char.LocomotionState.TargetYawAngle"))}
    return {"InViewMode": tag("Char.ViewMode"), "InLocomotionMode": tag("Char.LocomotionMode"), "InRotationMode": tag("Char.RotationMode"),
            "InStance": tag("Char.Stance"), "InGait": tag("Char.Gait"), "InOverlayMode": tag("Char.OverlayMode"), "InLocomotionAction": tag("Char.LocomotionAction"),
            "InLocomotion": loc, "InView": {"Rotation": vec(post_char, "Char.ViewState.Rotation", ("Pitch", "Yaw", "Roll")), "YawSpeed": num(post_char.get("Char.ViewState.YawSpeed"))},
            "InRagdolling": {"Velocity": vec(post_char, "Char.RagdollingState.Velocity")}, "DoJump": False, "DoSnapshot": False}


def recorded_hit(final: dict):
    """The ground prediction sweep of the original, redone by the recorder at the end of the frame: ``None`` when it did not run."""
    if not final.get("Trace.GroundPrediction.Ran"):
        return None
    normal = vec(final, "Trace.GroundPrediction.ImpactNormal")
    location = vec(final, "Trace.GroundPrediction.Location")
    return rt.Hit(bool(final.get("Trace.GroundPrediction.bBlockingHit")), location, normal, 0.0, num(final.get("Trace.GroundPrediction.Time"), 1.0),
                  location=location, start_penetrating=bool(final.get("Trace.GroundPrediction.bStartPenetrating")), face_normal=normal)


def compare_take(name: str, frames: range | None = None, tol: Tolerance | None = None, settings=None) -> TakeReport:
    """Run ``frames`` (default all) of a take through the instance, teacher forced, and compare with the recording."""
    tol = tol or Tolerance()
    take = Take(name)
    report = TakeReport(name)
    mods = A.compile_system()
    module = mods[INSTANCE]
    leaves_of = leaf_types(module)
    cls = {n: load_class(m, generate_python(m)) for n, m in mods.items()}
    from dal import settings as dsettings
    pack = os.environ.get("DAL_ALS_PACK", r"G:\DCC\AlsPack")
    resolver = dsettings.PackResolver(pack)
    if settings is None:
        settings = {"Settings": os.path.join(pack, "data", "AIS_Als_Default.json"), "resolver": resolver}
    hosts = {n: TruthHost() for n in mods}
    skeleton = take.skeleton()
    for h in hosts.values():
        h.skeleton = skeleton
    objs = {CHARACTER: cls[CHARACTER](hosts[CHARACTER], Settings=os.path.join(pack, "data", "CS_Als_Default.json"), resolver=resolver),
            INSTANCE: cls[INSTANCE](hosts[INSTANCE], **settings)}
    for n, m in mods.items():
        for p in m.peers:
            objs[n].connect(p.name, objs[p.logic])
    inst, char = objs[INSTANCE], objs[CHARACTER]
    # NativeInitializeAnimation: the thigh axes out of the reference skeleton, compared with what the original computed
    hosts[INSTANCE].load({"skeleton": skeleton})
    inst.begin_play()
    first = take.frame("post_char", 0)
    for side in ("Left", "Right"):
        got = tuple(getattr(inst.FeetState, side).ThighAxisPelvisSpace)
        want = vec(first, f"Anim.FeetState.{side}.ThighAxisPelvisSpace")
        ok, err = close(got, want, Tolerance(1e-5, 1e-5))
        report.fields[f"FeetState.{side}.ThighAxisPelvisSpace(begin_play)"].add(0, ok, err)

    play_events = defaultdict(list)                                        # frame -> the dynamic montages the original started
    for e in take.events:
        if e.get("type") == "montage_play" and e.get("slot"):
            play_events[int(e["f"])].append(e)

    rng = frames or range(1, take.n)
    for f in rng:
        if f < 1 or f >= take.n:
            continue
        if not (take.sampled["post_char"][f] and take.sampled["final"][f]):
            report.skipped["not sampled"] += 1
            continue
        if take.phase[f] == "cmc":
            report.skipped["pose ticked in the movement component"] += 1
            continue
        post_char, final = take.frame("post_char", f), take.frame("final", f)
        dt = num(final.get("Time.Delta"))
        frame = host_frame(take, f, post_char, take.curves(f), dt)
        frame["skeleton"] = skeleton
        hosts[INSTANCE].load(frame)
        hosts[CHARACTER].load(frame)
        hit = recorded_hit(final)
        hosts[INSTANCE].forced_hit = hit
        hosts[INSTANCE].sweeps.clear()
        # the state the instance starts the frame with
        force_state(inst, truth_leaves(module, post_char))
        inst.bPendingUpdate = bool(post_char.get("Anim.bPendingUpdate"))
        inst.TeleportedTime = num(post_char.get("Anim.TeleportedTime"))
        inst.PreviousGlobalTimeDilation = num(post_char.get("Anim.PreviousGlobalTimeDilation"), 1.0)
        inst.GroundedEntryMode = "" if post_char.get("Anim.GroundedEntryMode") in (None, "None") else post_char["Anim.GroundedEntryMode"]
        for name_, value in character_inputs(post_char).items():
            char.set_input(name_, value)
        hosts[INSTANCE].calls.clear()
        char.update_phase("after_movement", dt)
        inst.update(dt)
        # the entries the original's graph called, as far as the recording says
        air = str(final.get("Anim.LocomotionMode")) == "Als.LocomotionMode.InAir"
        vertical_ran = air and abs(num(final.get("Anim.InAirState.VerticalVelocityWorldSpace")) - num(final.get("Anim.LocomotionState.VelocityWorldSpace.Z"))) < 1e-3
        if final.get("Anim.DynamicTransitionsState.bUpdatedThisFrame"):
            inst.call_entry("RefreshDynamicTransitions")
            report.entries["RefreshDynamicTransitions"] += 1
        if final.get("Anim.RotateInPlaceState.bUpdatedThisFrame"):
            inst.call_entry("RefreshRotateInPlace")
            report.entries["RefreshRotateInPlace"] += 1
        if final.get("Anim.TurnInPlaceState.bUpdatedThisFrame"):
            inst.call_entry("RefreshTurnInPlace")
            report.entries["RefreshTurnInPlace"] += 1
        if vertical_ran:
            inst.call_entry("RefreshInAir")
            report.entries["RefreshInAir"] += 1
        inst.post_update()
        report.frames += 1
        got = inst.snapshot()
        want_leaves = truth_leaves(module, final)

        def check(path):
            if path not in want_leaves or path not in got:
                return None
            ty = leaves_of[path]
            ok, err = close(got[path], want_leaves[path], tol, angle=ty.name == "float" and path.endswith(ANGLE_FIELDS))
            report.fields[path].add(f, ok, err)
            return ok, ty

        bad = []
        for path in leaves_of:
            owned = path.startswith(UPDATE_OWNED)
            entry = any(path in fields for key, fields in ENTRY_FIELDS.items() if final.get(f"Anim.{key}.bUpdatedThisFrame"))
            if not (owned or entry):
                continue
            r = check(path)
            if r is not None and not r[0]:
                bad.append((path, r[1]))
        if len(bad) == 1 and bad[0][1].name in DISCRETE_TYPES + ("enum",) or (len(bad) == 1 and isinstance(bad[0][1], EnumType)):
            report.flips[bad[0][0]].append(f)
        # what the original was doing in this frame, from its own final state: the foot lock in each of its phases, the two limits at work
        feet = "Anim.FeetState."
        if final.get(feet + "bBecameValid"):
            report.stats["became valid"] += 1
        for side in ("Left", "Right"):
            amount = num(final.get(f"{feet}{side}.LockAmount"))
            report.stats["foot lock partial" if 0.0 < amount < 1.0 else ("foot lock full" if amount >= 1.0 else "foot lock none")] += 1
            if amount > 0.0:
                axis = rotate_quat(vec(final, feet + "PelvisRotation", "XYZW"), vec(final, f"{feet}{side}.ThighAxisPelvisSpace"))
                thigh = abs(angle_between_signed_xy(axis, vec(final, f"{feet}{side}.LockLocation")))
                if abs(thigh - 90.0) < 0.01:                                          # the ThighAngleLimit of AIS_Als_Default
                    report.stats["thigh angle limit at work"] += 1
                foot = abs(math.degrees(twist_z(quat_mul(vec(final, f"{feet}{side}.LockRotationWorldSpace", "XYZW"),
                                                         quat_conj(vec(final, f"{feet}{side}.TargetRotationWorldSpace", "XYZW"))))))
                if abs(foot - 40.0) < 0.01:                                           # the FootAngleLimit
                    report.stats["foot angle limit at work"] += 1
        if num(post_char.get("Time.Seconds")) - num(post_char.get("Anim.TeleportedTime")) <= 0.2 and num(post_char.get("Time.Seconds")) > 1.0:
            report.stats["within 0.2 s of a teleport"] += 1
        # ground prediction: the sweep the instance asked for and the amount it came to
        if vertical_ran:
            amount = "InAirState.GroundPredictionAmount"
            check(amount)
            report.sweeps["frames in air"] += 1
            if num(final.get("Anim.InAirState.VerticalVelocityWorldSpace")) <= -200.0 and hit is not None:
                report.sweeps["with a recorded sweep"] += 1
                sweeps = hosts[INSTANCE].sweeps
                if len(sweeps) != 1:
                    report.sweeps["no sweep asked"] += 1
                else:
                    s, e, radius, half_height = sweeps[0]
                    ok1, e1 = close(s, vec(final, "Trace.GroundPrediction.Start"), tol)
                    ok2, e2 = close(e, vec(final, "Trace.GroundPrediction.End"), tol)
                    report.fields["GroundPrediction.Sweep.Start"].add(f, ok1, e1)
                    report.fields["GroundPrediction.Sweep.End"].add(f, ok2, e2)
            elif hosts[INSTANCE].sweeps:
                report.sweeps["sweep the original did not make"] += 1
        # the dynamic montages: what the instance played in this frame against what the original started (a frame later at most)
        played = [c for c in hosts[INSTANCE].calls if c[0] == "play_slot_animation"]
        started = [e for g in (f, f + 1) for e in play_events.get(g, [])]
        for c in played:
            slot = c[1][1]
            report.commands[f"played {slot}"] += 1
            match = [e for e in started if e.get("slot") == slot and e.get("sequence") == c[1][0]]
            report.commands[f"played {slot}: the original started the same sequence"] += 1 if match else 0
            if match:
                e = match[0]
                ok, err = close((float(c[1][4]), float(c[1][7])), (float(e["rate"]), float(e["start"])), Tolerance(1e-4, 1e-3))
                report.fields[f"play_slot_animation({slot}).rate,start"].add(f, ok, err)
        for e in play_events.get(f, []) + play_events.get(f + 1, []):
            if e.get("slot") in ("TurnInPlaceStanding", "TurnInPlaceCrouching") and f in play_events and e in play_events[f]:
                report.commands[f"original started {e['slot']}"] += 1
    return report
