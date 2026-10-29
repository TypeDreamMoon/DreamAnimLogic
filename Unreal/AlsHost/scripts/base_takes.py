# The moving-base takes: ALS standing on a sliding platform, an elevator, a turning disc, jumping off them, mantling a box that drifts, rolling and crouching on one.
#
# Loaded by g_record.py, which adds these takes to the driver of G:\DCC\AlsTruth\tools\r1_record.py the way it adds the ragdoll take (the routes of the ten
# original takes are not touched).  The same driver runs against the original ALS (R1_ORIGINAL=1, host_als.py restore) and the generated one, so both record
# the same routes on the same platforms.  Design: Plan/anim-moving-base.md section 7.
#
# The platforms are spawned in the EDITOR world before play begins (PIE copies it; nothing is ever saved) as ADalTestMover actors (DalRuntime): a static mesh
# actor whose mesh is Movable, with a UInterpToMovementComponent and a URotatingMovementComponent it can use.  Three ways of moving one:
#   driver "set"     the script moves the actor with SetActorLocation / SetActorRotation every frame to the pose `motion` (a c3 `motion.py` description: the
#                    Blender side evaluates the very same formula) has at that frame.  The component velocity of such a mover is zero.
#   driver "interp"  a UInterpToMovementComponent moves it back and forth (it reports its velocity: ComponentVelocity is its speed).
#   driver "rotate"  a URotatingMovementComponent turns it.
# Every frame the script writes down where each platform is (plat_<take>.json next to the take) - for "set" the pose it has put there, for the others the pose the
# actor has now - together with the frame the recorder is at; the closed loop in Blender drives its copy of the platforms with them, so what the movers do does not have
# to be reproducible, only recorded.  The character's own record says where the engine thought the base was (Cmc.OldBaseLocation / OldBaseQuat, Char.MovementBase.*).
#
# Time: the platform pose a frame's movement sees is motion((frame + 1) * dt), the order of the Blender session: the platforms are ticked, then the character.
import importlib
import json
import math
import os
import sys
import types

import unreal

C3 = "I:/DCC/blender-moon-engine/scripts/modules/dreamved/anim/c3"
DT = 1.0 / 60.0
MOVER_CLASS = "/Script/DalRuntime.DalTestMover"
CUBE = "/Engine/BasicShapes/Cube"
CYLINDER = "/Engine/BasicShapes/Cylinder"

_motion = {}


def motion_module():
    """c3's motion.py without importing the c3 package (its __init__ needs numpy, which the editor's Python does not have)."""
    if "mod" not in _motion:
        pkg = types.ModuleType("c3m")
        pkg.__path__ = [C3]
        sys.modules["c3m"] = pkg
        _motion["mod"] = importlib.import_module("c3m.motion")
    return _motion["mod"]


# ---- the platforms ----------------------------------------------------------------------------------------------------------------

def box(name, center, half, driver="set", motion=None, interp=None, rotate=None, mover=True):
    return {"name": name, "shape": "box", "center": list(center), "half": list(half), "driver": driver, "motion": motion, "interp": interp,
            "rotate": rotate, "mover": mover}


def disc(name, center, radius, half_height, driver="set", motion=None, rotate=None):
    return {"name": name, "shape": "cylinder", "center": list(center), "radius": radius, "half_height": half_height, "driver": driver, "motion": motion,
            "interp": None, "rotate": rotate, "mover": True}


def pingpong(offset, speed, dwell=0.0, smooth=False, t0=0.0):
    d = {"type": "pingpong", "offset": list(offset), "speed": speed, "dwell": dwell, "smooth": smooth}
    if t0:
        d["t0"] = t0
    return d


def interp_of(offset, speed):
    """What the DalTestMover needs for a back-and-forth of `speed` cm/s over `offset`."""
    length = math.sqrt(sum(c * c for c in offset))
    return {"offset": list(offset), "seconds": length / speed}


# The character starts at (0, 0, 100) facing +X on a floor at z = 0.  A platform is 7 cm thick, its bottom 1 cm above the floor (a mover that overlaps the floor is blocked by it when a
# movement component sweeps it) and its top at z = 8: the capsule (feet at z = 10) lands on it.
SLIDE_LONG = ((0, 0, 4.5), (800, 150, 3.5), (1500, 0, 0), 150.0)            # center, half extents, the way it slides (cm), speed (cm/s)

TAKES = {
    "base_probe": {
        "desc": "probe: stand on a platform sliding at 100 cm/s (moved by SetActorLocation), jump",
        "platforms": [box("R1B_Slide", (0, 0, 4.5), (800, 150, 3.5), "set", pingpong((3000, 0, 0), 100.0))]},
    "base_probe_interp": {
        "desc": "probe: the same, the platform moved by a UInterpToMovementComponent",
        "platforms": [box("R1B_Slide", (0, 0, 4.5), (800, 150, 3.5), "interp", pingpong((3000, 0, 0), 100.0), interp=interp_of((3000, 0, 0), 100.0))]},
    "base_translate": {
        "desc": "standing, walking, crouching and running off a platform that slides back and forth (moved by SetActorLocation)",
        "platforms": [box("R1B_Slide", (0, 0, 4.5), (250, 150, 3.5), "set", pingpong((900, 0, 0), 120.0, 1.5))]},
    "base_translate_interp": {
        "desc": "the same on a platform moved by a UInterpToMovementComponent",
        "platforms": [box("R1B_Slide", (0, 0, 4.5), (250, 150, 3.5), "interp", pingpong((900, 0, 0), 120.0), interp=interp_of((900, 0, 0), 120.0))]},
    "base_disc": {
        "desc": "standing, walking, jumping, crouching, rolling and running off a disc that turns at 20 degrees per second",
        "platforms": [disc("R1B_Disc", (-150, 0, 4.5), 300.0, 3.5, "set", {"type": "spin", "rate": 20.0})]},
    "base_elevator": {
        "desc": "an elevator: rising, stepping off onto a landing, dropping onto the descending lift, jumping off a rising one",
        "platforms": [box("R1B_Lift", (0, 0, 4.5), (200, 200, 3.5), "set", pingpong((0, 0, 300), 100.0, 2.0, True, 1.0)),
                      box("R1B_Landing", (350, 0, 154), (150, 150, 154), mover=False)]},
    "base_jump_off": {
        "desc": "jumping on and off a long platform that slides at 150 cm/s (moved by SetActorLocation)",
        "platforms": [box("R1B_Slide", (0, 0, 4.5), (800, 150, 3.5), "set", pingpong((1500, 0, 0), 150.0, 1.0))]},
    "base_jump_off_interp": {
        "desc": "the same on a platform moved by a UInterpToMovementComponent",
        "platforms": [box("R1B_Slide", (0, 0, 4.5), (800, 150, 3.5), "interp", pingpong((1500, 0, 0), 150.0), interp=interp_of((1500, 0, 0), 150.0))]},
    "base_roll_crouch": {
        "desc": "crouching and rolling on a platform that slides at 100 cm/s (moved by SetActorLocation)",
        "platforms": [box("R1B_Slide", (0, 0, 4.5), (800, 150, 3.5), "set", pingpong((2400, 0, 0), 100.0))]},
    "base_mantle": {
        "desc": "mantling a box that drifts at 8 cm/s (moved by SetActorLocation: its component velocity is zero)",
        "platforms": [box("R1B_MantleBox", (330, -300, 65.5), (150, 450, 64.5), "set", pingpong((0, 600, 0), 8.0))]},
    "base_mantle_fast_set": {
        "desc": "mantling a box that drifts at 60 cm/s, moved by SetActorLocation (component velocity zero)",
        "platforms": [box("R1B_MantleBox", (330, -300, 65.5), (150, 450, 64.5), "set", pingpong((0, 1200, 0), 60.0))]},
    "base_mantle_fast_interp": {
        "desc": "mantling a box that drifts at 60 cm/s, moved by a UInterpToMovementComponent (it reports 60 cm/s: above ALS's speed threshold)",
        "platforms": [box("R1B_MantleBox", (330, -300, 65.5), (150, 450, 64.5), "interp", pingpong((0, 1200, 0), 60.0), interp=interp_of((0, 1200, 0), 60.0))]},
}

# Far from the origin (ANIM-55, anim-moving-base.md 12.7): the character is put at (FAR_X, 0, 100) on a static slab and walks / stops / locks its feet there.  FAR_X = 1e6 cm
# (10 km): a float32 holds a world position there with 0.0625 cm steps, the double of FVector does not.  The slab is 70 x 30 m (top at z = 0) and reaches 10 m behind the start.
# Env R1_FAR_X (cm) overrides the distance.
FAR_X = float(os.environ.get("R1_FAR_X", "1000000"))
TAKES["far_origin"] = {
    "desc": "standing, walking, turning, running, crouching, sprinting, jumping, rolling on a slab %.0f m from the origin" % (FAR_X / 100.0),
    "start": [FAR_X, 0.0, 100.0],
    "platforms": [box("R1B_Far", (FAR_X + 2500.0, 0.0, -50.0), (3500.0, 1500.0, 50.0), mover=False)]}

# The routes (driver steps: see r1_record.py).  The character stands at the origin facing +X.
STEPS = '''
ORIGIN = [0.0, 0.0]          # where the character starts (a take with "start" moves it there); the driver's LIMIT / GUARD are distances from it

FAR_ORIGIN = [
    step("idle", 2.5),
    step("walk", 2.5, gait="Walking", move=(1, 0)),
    step("stop", 2.0, move=(0, 0)),
    step("turn_right", 2.5, yaw_delta=100.0, yaw_time=0.4),
    step("turn_left", 2.5, yaw_delta=-100.0, yaw_time=0.4),
    step("run", 2.5, gait="Running", move=(1, 0)),
    step("stop2", 2.5, move=(0, 0)),
    step("strafe", 2.0, gait="Walking", move=(0, 1)),
    step("stop3", 1.5, move=(0, 0)),
    step("crouch_idle", 2.0, stance="Crouching"),
    step("crouch_walk", 2.0, gait="Walking", move=(1, 0)),
    step("stand_stop", 2.0, stance="Standing", move=(0, 0)),
    step("sprint", 2.5, gait="Sprinting", move=(1, 0)),
    step("stop4", 2.5, move=(0, 0)),
    step("jump", 2.5, jump=True),
    step("roll", 3.0, roll=True),
    step("run_back", 2.0, gait="Running", move=(-1, 0)),
    step("end", 2.0, move=(0, 0)),
]

BASE_PROBE = [
    step("idle", 2.0),
    step("jump", 3.0, jump=True),
    step("idle_end", 2.0),
]

BASE_TRANSLATE = [
    step("idle", 3.0),
    step("walk_fwd", 1.2, gait="Walking", move=(1, 0)),
    step("stop", 1.0, move=(0, 0)),
    step("walk_back", 1.5, move=(-1, 0)),
    step("stop2", 1.0, move=(0, 0)),
    step("crouch_idle", 1.5, stance="Crouching"),
    step("crouch_walk", 1.5, gait="Walking", move=(1, 0)),
    step("stand_stop", 1.0, stance="Standing", move=(0, 0)),
    step("run_strafe_off", 2.0, gait="Running", move=(0, 1)),
    step("fall_land", 1.0, move=(0, 0)),
    step("end", 1.5),
]

BASE_DISC = [
    step("idle", 3.0),
    step("walk_in", 0.8, gait="Walking", move=(-1, 0)),
    step("stop", 1.0, move=(0, 0)),
    step("strafe", 1.5, move=(0, 1)),
    step("stop2", 1.0, move=(0, 0)),
    step("jump", 2.5, jump=True),
    step("crouch_idle", 1.5, stance="Crouching"),
    step("stand", 1.0, stance="Standing"),
    step("roll_idle", 3.0, roll=True),
    step("run_out", 1.5, gait="Running", move=(1, 0)),
    step("land", 2.0, move=(0, 0)),
]

BASE_ELEVATOR = [
    step("idle_wait", 2.0),
    step("idle_rise", 2.5),
    step("run_to_landing", 1.0, gait="Running", move=(1, 0)),
    step("stop_on_landing", 1.5, move=(0, 0)),
    step("run_back_off_edge", 1.2, move=(-1, 0)),
    step("stand_lift", 4.0, move=(0, 0)),
    step("jump_rising", 3.0, jump=True),
    step("end", 1.5),
]

BASE_JUMP_OFF = [
    step("idle", 2.0),
    step("jump_idle", 2.5, jump=True),
    step("walk", 1.0, gait="Walking", move=(1, 0)),
    step("jump_walk", 2.5, jump=True),
    step("stop", 1.0, move=(0, 0)),
    step("run_back", 1.0, gait="Running", move=(-1, 0)),
    step("jump_run_back", 2.5, jump=True),
    step("stop2", 1.0, move=(0, 0)),
    step("strafe_off_jump", 2.5, gait="Running", move=(0, 1), at=[(0.2, {"jump": True})]),
    step("land_stop", 3.0, move=(0, 0)),
]

BASE_ROLL_CROUCH = [
    step("idle", 2.0),
    step("crouch_idle", 2.0, stance="Crouching"),
    step("crouch_walk", 1.5, gait="Walking", move=(1, 0)),
    step("stand", 1.0, stance="Standing", move=(0, 0)),
    step("roll_idle", 3.0, roll=True),
    step("run", 1.5, gait="Running", move=(1, 0)),
    step("roll_run", 3.0, roll=True),
    step("stop", 1.5, move=(0, 0)),
    step("turn_back", 1.0, yaw_delta=180.0, yaw_time=0.4),
    step("run_back", 1.5, move=(1, 0)),
    step("roll_back", 3.0, roll=True),
    step("end", 1.5, move=(0, 0)),
]

BASE_MANTLE = [
    step("idle", 1.5),
    step("approach_and_mantle", 4.0, move=(1, 0), gait="Running", mantle_try=True),
    step("after_mantle", 3.0, move=(1, 0), gait="Running"),
    step("stop", 2.0, move=(0, 0)),
]

'''

ROUTES = {"far_origin": "FAR_ORIGIN", "base_probe": "BASE_PROBE", "base_probe_interp": "BASE_PROBE", "base_translate": "BASE_TRANSLATE", "base_translate_interp": "BASE_TRANSLATE",
          "base_disc": "BASE_DISC", "base_elevator": "BASE_ELEVATOR", "base_jump_off": "BASE_JUMP_OFF", "base_jump_off_interp": "BASE_JUMP_OFF",
          "base_roll_crouch": "BASE_ROLL_CROUCH", "base_mantle": "BASE_MANTLE", "base_mantle_fast_set": "BASE_MANTLE", "base_mantle_fast_interp": "BASE_MANTLE"}


# ---- the driver source ----------------------------------------------------------------------------------------------------------------

def _replace_once(src, old, new):
    if src.count(old) != 1:
        raise RuntimeError("r1_record.py has changed: anchor not found once: " + old[:70])
    return src.replace(old, new)


def add_base_takes(src):
    """The driver source with the moving-base takes, the platform hooks and a mantle attempt per step (each anchor must be there once; the ragdoll take is added first)."""
    nl = chr(10)
    entries = "".join('    "%s": (%s, %r),' % (t, r, TAKES[t]["desc"]) + nl for t, r in ROUTES.items())
    src = _replace_once(src, "SCRIPTED = {" + nl, STEPS + "SCRIPTED = {" + nl)
    anchor = '    "ragdoll": (RAGDOLL, "ragdolling from a run, at rest and in the air, with the get up"),' + nl
    src = _replace_once(src, anchor, anchor + entries)
    # the platforms are spawned in the editor world before play begins, bound to their copies in the game world, moved / logged every frame, written at the end
    src = _replace_once(src, "    ar.begin_play()" + nl + "    yield Wait(until=lambda: game_world() is not None, timeout=120)" + nl,
                        "    base_state = BASE.begin(name, INFO_T, OUT, log)" + nl + "    ar.begin_play()" + nl + "    yield Wait(until=lambda: game_world() is not None, timeout=120)" + nl)
    src = _replace_once(src, "    world = game_world()" + nl + "    ch = unreal.GameplayStatics.get_player_character(world, 0)" + nl,
                        "    world = game_world()" + nl + "    BASE.bind(base_state, world)" + nl + "    ch = unreal.GameplayStatics.get_player_character(world, 0)" + nl)
    src = _replace_once(src, '        state["frames"] += 1' + nl, '        state["frames"] += 1' + nl + "        BASE.drive(base_state, fc())" + nl)
    src = _replace_once(src, "    if box_actor is not None:" + nl + "        unreal.EditorLevelLibrary.destroy_actor(box_actor)" + nl,
                        "    if box_actor is not None:" + nl + "        unreal.EditorLevelLibrary.destroy_actor(box_actor)" + nl + "    BASE.finish(base_state, log)" + nl)
    # a take with a "start" puts the character there before the settling wait; the driver's limits (LIMIT: abort, GUARD: turn around) count from the start
    src = _replace_once(src, "    pc.set_control_rotation(unreal.Rotator(roll=0.0, pitch=0.0, yaw=0.0))" + nl + "    yield Wait(2.0)" + nl,
                        "    pc.set_control_rotation(unreal.Rotator(roll=0.0, pitch=0.0, yaw=0.0))" + nl + "    BASE.place(base_state, ch, ORIGIN, log)" + nl + "    yield Wait(2.0)" + nl)
    src = _replace_once(src, "        if abs(loc.x) > LIMIT or abs(loc.y) > LIMIT:" + nl,
                        "        if abs(loc.x - ORIGIN[0]) > LIMIT or abs(loc.y - ORIGIN[1]) > LIMIT:" + nl)
    src = _replace_once(src, '        if (abs(loc.x) > GUARD or abs(loc.y) > GUARD) and state["t"] > state["guard_until"]',
                        '        if (abs(loc.x - ORIGIN[0]) > GUARD or abs(loc.y - ORIGIN[1]) > GUARD) and state["t"] > state["guard_until"]')
    src = _replace_once(src, "            heading = math.degrees(math.atan2(-loc.y, -loc.x))" + nl,
                        "            heading = math.degrees(math.atan2(ORIGIN[1] - loc.y, ORIGIN[0] - loc.x))" + nl)
    # a mantle attempt per step (the original driver tried once per take; with one such step the behaviour is the same)
    src = _replace_once(src, '        if s["cmd"].get("mantle_try") and not state.get("mantled") and state["t"] > 0.2:' + nl,
                        '        if s["cmd"].get("mantle_try") and state.get("mantled_step") != state["i"] and state["t"] > 0.2:' + nl)
    src = _replace_once(src, '                state["mantled"] = True' + nl, '                state["mantled"] = True' + nl + '                state["mantled_step"] = state["i"]' + nl)
    return src


# ---- the platforms in the editor world and in the game --------------------------------------------------------------------------------

def _pose_at(spec, t):
    """(location, rotation (pitch, yaw, roll)) of a platform driven by `set` at time t."""
    mo = motion_module()
    base_loc = mo.V3(*spec["center"])
    if spec["motion"] is None:
        return list(spec["center"]), (0.0, 0.0, 0.0)
    m = mo.motion_from_dict(spec["motion"])
    loc, rot = m.evaluate(t, base_loc, mo.Rot())
    return [loc.x, loc.y, loc.z], rot.tuple()


def _scale_of(spec, mesh):
    b = mesh.get_bounds().box_extent
    native = (max(b.x * 2.0, 1e-3), max(b.y * 2.0, 1e-3), max(b.z * 2.0, 1e-3))
    if spec["shape"] == "box":
        size = [h * 2.0 for h in spec["half"]]
    else:
        d = spec["radius"] * 2.0
        size = [d, d, spec["half_height"] * 2.0]
    return unreal.Vector(size[0] / native[0], size[1] / native[1], size[2] / native[2])


def begin(take, info, out_dir, log):
    """Spawn the take's platforms in the editor world (None when the take has none)."""
    cfg = TAKES.get(take)
    if cfg is None:
        return None
    mover_class = unreal.load_class(None, MOVER_CLASS)
    if mover_class is None:
        raise RuntimeError("cannot load " + MOVER_CLASS + " (DalRuntime is not built into the host?)")
    st = {"take": take, "out": out_dir, "platforms": [], "started": False, "desc": cfg["desc"]}
    for spec in cfg["platforms"]:
        pose = _pose_at(spec, DT) if spec["driver"] == "set" else (list(spec["center"]), (0.0, 0.0, 0.0))
        loc, rot = pose
        mesh = unreal.load_asset(CUBE if spec["shape"] == "box" else CYLINDER)
        if spec["mover"]:
            actor = unreal.EditorLevelLibrary.spawn_actor_from_class(mover_class, unreal.Vector(*loc), unreal.Rotator(roll=rot[2], pitch=rot[0], yaw=rot[1]))
        else:
            actor = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(*loc), unreal.Rotator(roll=rot[2], pitch=rot[0], yaw=rot[1]))
        actor.set_actor_label(spec["name"])
        actor.static_mesh_component.set_static_mesh(mesh)
        actor.set_actor_scale3d(_scale_of(spec, mesh))
        if spec["mover"]:
            if spec["driver"] == "interp":
                actor.set_editor_property("move_by_interp_to", True)
                actor.set_editor_property("interp_offset", unreal.Vector(*spec["interp"]["offset"]))
                actor.set_editor_property("interp_seconds", spec["interp"]["seconds"])
            if spec["driver"] == "rotate":
                actor.set_editor_property("rotation_rate", unreal.Rotator(roll=spec["rotate"][2], pitch=spec["rotate"][0], yaw=spec["rotate"][1]))
        st["platforms"].append({"spec": spec, "editor": actor, "game": None, "rows": [], "label": spec["name"]})
    info["platforms"] = [{"name": p["spec"]["name"], "shape": p["spec"]["shape"], "driver": p["spec"]["driver"], "center": p["spec"]["center"],
                          "half": p["spec"].get("half"), "radius": p["spec"].get("radius"), "half_height": p["spec"].get("half_height"),
                          "motion": p["spec"]["motion"], "mover": p["spec"]["mover"], "interp": p["spec"].get("interp")} for p in st["platforms"]]
    log("%s: %d platform(s) spawned in the editor world" % (take, len(st["platforms"])))
    return st


def place(st, ch, origin, log):
    """A take with a "start" ([x, y, z]): teleport the character there (the driver then settles for two seconds before the recorder starts) and make it the origin of the driver's limits."""
    cfg = TAKES.get(st["take"]) if st is not None else None
    if cfg is None or not cfg.get("start"):
        return
    start = cfg["start"]
    ch.set_actor_location(unreal.Vector(*start), False, True)
    origin[0], origin[1] = start[0], start[1]
    log("%s: the character is at (%.0f, %.0f, %.0f)" % (st["take"], start[0], start[1], start[2]))


def bind(st, world):
    """Find the platforms' copies in the game world."""
    if st is None:
        return
    actors = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.StaticMeshActor)
    by_label = {a.get_actor_label(): a for a in actors}
    for p in st["platforms"]:
        p["game"] = by_label.get(p["label"])
        if p["game"] is None:
            raise RuntimeError("the platform %s is not in the game world" % p["label"])
    # R1_FOLLOW_BASE=1: the toggle -- the character's movement component does not ignore the rotation of its base (bIgnoreBaseRotation off), so the facing and the
    # view follow a turning platform.  The truth takes are recorded without it (the default of ALS), so a comparison against them uses the default.
    if os.environ.get("R1_FOLLOW_BASE", "0") == "1":
        ch = unreal.GameplayStatics.get_player_character(world, 0)
        cmc = ch.get_component_by_class(unreal.CharacterMovementComponent) if ch is not None else None
        if cmc is not None:
            cmc.set_editor_property("ignore_base_rotation", False)         # (bIgnoreBaseRotation: the editor Python drops the b)
            st["follow_base_rotation"] = True


def _activate(p):
    spec = p["spec"]
    actor = p["game"]
    if spec["driver"] == "interp":
        comp = actor.get_component_by_class(unreal.InterpToMovementComponent)
        if comp is not None:
            comp.activate(True)
    elif spec["driver"] == "rotate":
        comp = actor.get_component_by_class(unreal.RotatingMovementComponent)
        if comp is not None:
            comp.activate(True)


def drive(st, frames):
    """Called every frame after the engine ticked, `frames` = how many frames the recorder has.  Writes down where every platform is (for the frame that just ended)
    and moves the `set` platforms to the pose the next frame's movement sees."""
    if st is None:
        return
    world = st["platforms"][0]["game"].get_world() if st["platforms"] else None
    now = unreal.GameplayStatics.get_time_seconds(world) if world is not None else 0.0
    if not st["started"]:
        st["started"] = True
        st["first_frames"] = frames
        for p in st["platforms"]:
            if p["spec"]["mover"]:
                _activate(p)
    for p in st["platforms"]:
        actor = p["game"]
        loc = actor.get_actor_location()
        q = actor.get_actor_rotation().quaternion()
        p["rows"].append([frames, now, loc.x, loc.y, loc.z, q.x, q.y, q.z, q.w])
        if p["spec"]["mover"] and p["spec"]["driver"] == "set":
            pos, rot = _pose_at(p["spec"], (frames + 1) * DT)
            actor.set_actor_location_and_rotation(unreal.Vector(*pos), unreal.Rotator(roll=rot[2], pitch=rot[0], yaw=rot[1]), False, False)
        if p["spec"]["mover"]:                       # what the engine's velocity queries say about this mover (the probe reads them)
            cv = actor.static_mesh_component.get_component_velocity()
            bv = actor.get_body_velocity()
            p.setdefault("vel", []).append([frames, cv.x, cv.y, cv.z, bv.x, bv.y, bv.z])


def finish(st, log):
    """Write the platforms' poses next to the take and take the platforms out of the editor world."""
    if st is None:
        return
    doc = {"schema": "dreamved.platforms/1", "take": st["take"], "dt": DT, "desc": st["desc"], "first_frames": st.get("first_frames", 0), "platforms": [],
           "follow_base_rotation": bool(st.get("follow_base_rotation", False))}
    for p in st["platforms"]:
        spec = p["spec"]
        entry = {k: spec[k] for k in ("name", "shape", "driver", "center", "motion", "mover") if k in spec}
        for k in ("half", "radius", "half_height", "interp"):
            if spec.get(k) is not None:
                entry[k] = spec[k]
        entry["rows"] = p["rows"]                     # [frames, world seconds, x, y, z, qx, qy, qz, qw]
        entry["velocities"] = p.get("vel", [])      # [frames, component velocity x, y, z, body velocity x, y, z]
        doc["platforms"].append(entry)
    os.makedirs(st["out"], exist_ok=True)
    path = os.path.join(st["out"], "plat_%s.json" % st["take"])
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    log("platform poses written to " + path)
    for p in st["platforms"]:
        try:
            unreal.EditorLevelLibrary.destroy_actor(p["editor"])
        except Exception as exc:  # noqa: BLE001 (the editor world may be gone)
            log("could not destroy %s: %s" % (p["label"], exc))
