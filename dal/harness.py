"""Consistency harness: run the reference interpreter and a generated Python class
on the same input sequence and compare every input and state, frame by frame.

A *frame* is a plain dict::

    {"dt": 0.016,
     "velocity": (x, y, z), "is_grounded": True, ...,      # host items by name
     "actions": {"Move": (0.0, 1.0)},                      # host.action("Move")
     "notifies": {"Footstep": True},                       # host.notify("Footstep")
     "free": {"Aim": True},                                # inputs without a host binding
     "calls": ["RefreshLean"],                             # entries to call after update
     # v1 / v2
     "events": [("notify", "Hit"), ("montage_ended", "Light1", False), ("input", "Attack", "started", 1.0)],
     "montages": {"Light1": {"playing": True, "position": 0.4, "section": "Strike"}},
     "targets": [(7, (100.0, 0.0, 0.0))],
     "world": {"ground_z": 0.0, "spheres": [((0.0, 0.0, 100.0), 30.0)]}}

The same frame sequence drives the C++ consistency checks (``tests/cpp_driver.py``).  Besides the inputs
and states, every frame's snapshot carries ``"__cmds__"``: the host commands the logic issued that frame, so
command sequences are compared too.
"""

from __future__ import annotations

import math
import random
from typing import Callable

from . import ir
from . import runtime as rt
from .hostapi import HOOK_EVENTS, HOST_ITEMS
from .mockhost import MockHost
from .interp import Interpreter
from .pybackend import generate_python, load_class
from .types import BOOL, FLOAT, INT, QUAT, ROTATOR, TRANSFORM, VEC2, VEC3, ArrayType, AssetType, EnumType, MapType, StructType, TagType, Type


class ScriptedHost(MockHost):
    """A host that replays one frame dict at a time and records the commands it is given."""

    def load(self, frame: dict) -> None:
        for name, item in HOST_ITEMS.items():
            if name in ("action", "notify") or name.startswith("montage_") or item.arg:
                continue
            if name in frame:
                setattr(self, name, frame[name])
        self._curves = dict(frame.get("curves", {}))
        self.bones = {k: dict(v) for k, v in frame.get("bones", {}).items()}
        self.skeleton = {k: dict(v) for k, v in frame.get("skeleton", {}).items()}
        self._actions = frame.get("actions", {})
        self._notifies = frame.get("notifies", {})
        self.events = [tuple(e) for e in frame.get("events", [])]
        self.montages = dict(frame.get("montages", {}))
        self.targets = [(tid, tuple(loc)) for tid, loc in frame.get("targets", [])]
        world = frame.get("world", {})
        self.ground_z = world.get("ground_z")
        self.spheres = [(tuple(c), r) for c, r in world.get("spheres", [])]
        self.primitives = {k: dict(v) for k, v in world.get("primitives", {}).items()}


def load_standins(module: ir.Module) -> dict[str, Callable]:
    """Exec the bundled stand-in source and return the extern callables by DAL name."""
    if not module.externs:
        return {}
    ns: dict = {"__name__": "dal_standins"}
    exec(compile(module.standin_source, module.standin_filename or "<standins>", "exec"), ns)  # noqa: S102
    return {x.name: ns[x.py] for x in module.externs}


# ---- random input generation ---------------------------------------------------------------

def _rand_value(rng: random.Random, ty: Type, scale: float = 10.0):
    if ty is FLOAT:
        return rng.uniform(-scale, scale)
    if ty is INT:
        return rng.randint(-5, 5)
    if ty is BOOL:
        return rng.random() < 0.5
    if ty is VEC2:
        return (rng.uniform(-scale, scale), rng.uniform(-scale, scale))
    if ty is VEC3:
        return (rng.uniform(-scale, scale), rng.uniform(-scale, scale), rng.uniform(-scale, scale))
    if ty is ROTATOR:
        return (rng.uniform(-180, 180), rng.uniform(-180, 180), rng.uniform(-180, 180))
    if ty is QUAT:
        return _rand_quat(rng)
    if ty is TRANSFORM:
        return ((rng.uniform(-scale, scale), rng.uniform(-scale, scale), rng.uniform(-scale, scale)), _rand_quat(rng),
                tuple(rng.choice([0.5, 1.0, 1.5, 2.0]) for _ in range(3)))
    if isinstance(ty, EnumType):
        return rng.randrange(len(ty.members))
    if isinstance(ty, TagType):
        return ty.full_name(rng.choice(ty.members + ["None"]))
    if isinstance(ty, StructType):
        return {f.name: _rand_value(rng, f.ty, scale) for f in ty.fields if not f.derived and not f.ty.is_asset and f.ty.name not in ("curve", "curve3")}
    raise ValueError(ty)


def random_settings(ty: StructType, seed: int = 1, scale: float = 100.0, polyline: bool = False) -> dict:
    """Random data for a settings type, in the layout ``dal.settings`` reads: every member present (some left out at random),
    tables with a random subset of their keys, lists of 0-4 elements, inline curves and made-up asset names.

    ``polyline`` keeps curves to linear / constant keys with constant extrapolation (what the stand-in engine can evaluate)."""
    rng = random.Random(seed)
    return _rand_settings_value(rng, ty, scale, polyline)


def _rand_curve_channel(rng: random.Random, scale: float, polyline: bool) -> dict:
    times = sorted({round(rng.uniform(-0.2, 1.2) * scale, 3) for _ in range(rng.randint(0, 4))})
    kinds = ["constant", "linear"] if polyline else ["constant", "linear", "cubic"]
    keys = [{"t": t, "v": rng.uniform(-5, 5), "interp": rng.choice(kinds), "arrive": rng.uniform(-1, 1), "leave": rng.uniform(-1, 1)} for t in times]
    extrap = ["constant"] if polyline else ["constant", "linear", "cycle", "cycle_offset", "oscillate"]
    return {"keys": keys, "pre": rng.choice(extrap), "post": rng.choice(extrap)}


def _rand_settings_value(rng: random.Random, ty: Type, scale: float, polyline: bool):
    if isinstance(ty, StructType):
        out: dict = {}
        for f in ty.fields:
            if f.derived or rng.random() < 0.1:
                continue                                       # derived members are recomputed; a missing one keeps its default
            out[f.name] = _rand_settings_value(rng, f.ty, scale, polyline)
        return out
    if isinstance(ty, MapType):
        keys = [ty.key.full_name(m) for m in ty.key.members if m != "None"] if isinstance(ty.key, TagType) else             [m for m in ty.key.members] if isinstance(ty.key, EnumType) else [str(i) for i in range(-2, 4)]
        return {k: _rand_settings_value(rng, ty.value, scale, polyline) for k in keys if rng.random() < 0.7}
    if isinstance(ty, ArrayType):
        return [_rand_settings_value(rng, ty.elem, scale, polyline) for _ in range(rng.randint(0, 4))]
    if isinstance(ty, AssetType):
        if ty.name in ("curve", "curve3"):
            n = 1 if ty.name == "curve" else 3
            if rng.random() < 0.15:
                return None
            return {"class": "CurveFloat" if n == 1 else "CurveVector", "channels": [_rand_curve_channel(rng, scale, polyline) for _ in range(n)]}
        return None if rng.random() < 0.3 else f"A_{ty.name}_{rng.randint(1, 4)}"
    if isinstance(ty, EnumType):
        return ty.members[rng.randrange(len(ty.members))]
    if isinstance(ty, TagType):
        return ty.full_name(rng.choice(ty.members + ["None"])) or None
    return _rand_value(rng, ty, scale)


def _rand_quat(rng: random.Random):
    while True:
        v = [rng.gauss(0, 1) for _ in range(4)]
        n = math.sqrt(sum(c * c for c in v))
        if n > 1e-3:
            return tuple(c / n for c in v)


def random_frames(module: ir.Module, count: int, seed: int = 1) -> list[dict]:
    """Random frames covering every input of the module (host-bound and free)."""
    rng = random.Random(seed)
    rng_v2 = random.Random(seed * 7919 + 13)               # the later host items draw from their own stream (earlier sequences stay as they were)
    asks_world = module.kind != "actor" and any(isinstance(e, ir.HostQuery) for _label, body in module.bodies() for e in ir.walk_stmt_exprs(body))
    frames: list[dict] = []
    t = 0.0
    for _ in range(count):
        dt = rng.choice([0.0, 0.001, 0.008, 1 / 60, 1 / 30, rng.uniform(0.001, 0.1)])
        t += dt
        f: dict = {"dt": dt, "actions": {}, "notifies": {}, "free": {}}
        f["time"] = t
        f["velocity"] = (0.0, 0.0, 0.0) if rng.random() < 0.1 else tuple(rng.uniform(-700, 700) for _ in range(3))
        f["acceleration"] = tuple(rng.uniform(-2000, 2000) for _ in range(3))
        f["max_acceleration"] = rng.choice([0.0, 500.0, 1000.0, 2048.0, rng.uniform(100, 3000)])
        f["max_braking_deceleration"] = rng.choice([0.0, 1500.0, 2048.0, rng.uniform(100, 3000)])
        f["max_walk_speed"] = rng.uniform(100, 800)
        f["movement_mode"] = rng.randrange(7)
        f["is_grounded"] = rng.random() < 0.6
        f["is_crouching"] = rng.random() < 0.2
        f["actor_location"] = tuple(rng.uniform(-500, 500) for _ in range(3))
        f["actor_rotation"] = _rand_quat(rng)
        f["control_rotation"] = (rng.uniform(-90, 90), rng.uniform(-180, 180), 0.0)
        f["capsule_radius"] = rng_v2.uniform(20.0, 50.0)
        f["capsule_half_height"] = rng_v2.uniform(60.0, 100.0)
        f["actor_scale"] = rng_v2.choice([1.0, 1.0, 0.5, 2.0])
        f["walkable_floor_z"] = rng_v2.choice([0.71, 0.5, 0.866])
        f["actor_ready"] = rng_v2.random() < 0.8
        f["has_root_motion"] = rng_v2.random() < 0.3
        f["movement_mode_custom"] = rng_v2.randrange(4)
        f["pre_penetration_velocity_valid"] = rng_v2.random() < 0.4
        f["pre_penetration_velocity"] = tuple(rng_v2.uniform(-600, 600) for _ in range(3))
        f["curves"] = {i.host.arg: rng_v2.uniform(0.0, 1.0) for i in module.inputs if i.host is not None and i.host.item == "anim_curve"}
        f["mesh_world_transform"] = _rand_value(rng_v2, TRANSFORM, 100.0)
        f["root_motion_mode"] = rng_v2.randrange(4)
        f["ragdoll_supported"] = rng_v2.random() < 0.5
        f["bones"] = {i.host.arg: {"component": _rand_value(rng_v2, TRANSFORM, 50.0), "world": _rand_value(rng_v2, TRANSFORM, 100.0)}
                      for i in module.inputs if i.host is not None and i.host.item in ("bone_component_transform", "bone_world_transform")}
        f["skeleton"] = {}
        for i in module.inputs:
            if i.host is not None and i.host.item == "ref_path_child_translation":
                desc, anc = i.host.arg, i.host.arg2
                mid = f"{desc}_mid"
                f["skeleton"][anc] = {"parent": None, "translation": tuple(rng_v2.uniform(-5, 5) for _ in range(3))}
                f["skeleton"][mid] = {"parent": anc, "translation": tuple(rng_v2.uniform(-40, 40) for _ in range(3))}
                f["skeleton"][desc] = {"parent": mid, "translation": tuple(rng_v2.uniform(-40, 40) for _ in range(3))}
        for i in module.inputs:
            if i.peer is not None:
                continue
            if i.host is None:
                f["free"][i.name] = _rand_value(rng, i.ty)
            elif i.host.item == "action":
                f["actions"][i.host.arg] = _rand_value(rng, i.ty, 1.0)
            elif i.host.item == "notify":
                f["notifies"][i.host.arg] = rng.random() < 0.25
        if module.entries:
            f["calls"] = [_random_call(rng, e) for e in module.entries if rng.random() < 0.6]
        if any(h.event not in ("begin_play", "end_play") for h in module.handlers):
            f["events"] = _random_events(module, rng)
        if module.montages:
            f["montages"] = {}
            for md in module.montages:
                playing = rng.random() < 0.5                     # a section exists only while the montage plays; playing implies active
                f["montages"][md.name] = {"playing": playing, "active": playing or rng.random() < 0.3,
                                          "position": rng.uniform(0.0, 2.0),
                                          "section": rng.choice(md.sections) if playing and md.sections else None}
        if module.kind == "actor":
            f["targets"] = [(rng.randrange(1, 50), tuple(rng.uniform(-500, 500) for _ in range(3))) for _ in range(rng.randrange(0, 5))]
            f["world"] = {"ground_z": rng.choice([None, 0.0, -50.0]),
                          "spheres": [(tuple(rng.uniform(-300, 300) for _ in range(3)), rng.uniform(10, 80)) for _ in range(rng.randrange(0, 3))]}
        elif asks_world:                              # an animation instance that asks the scene (the thread-safe queries)
            f["world"] = {"ground_z": rng_v2.choice([None, 0.0, -50.0]),
                          "spheres": [(tuple(rng_v2.uniform(-300, 300) for _ in range(3)), rng_v2.uniform(10, 80)) for _ in range(rng_v2.randrange(0, 3))]}
        if "world" in f:
            f["world"]["primitives"] = {name: {"speed": rng_v2.choice([0.0, 0.0, rng_v2.uniform(0.0, 400.0)]), "can_step_up": rng_v2.random() < 0.7,
                                               "transform": _rand_value(rng_v2, TRANSFORM, 100.0)}
                                        for name in ["Ground"] + [f"Sphere{k}" for k in range(len(f["world"]["spheres"]))]}
        frames.append(f)
    return frames


def _rand_arg(rng: random.Random, ty: Type):
    """A loosely typed argument for an entry parameter: enums by member name or index, tags by name, assets by name."""
    if isinstance(ty, EnumType):
        return ty.members[rng.randrange(len(ty.members))] if rng.random() < 0.5 else rng.randrange(len(ty.members))
    if isinstance(ty, AssetType):
        return None if rng.random() < 0.2 else f"A_{ty.name}_{rng.randint(1, 3)}"
    return _rand_value(rng, ty, 5.0)


def _random_call(rng: random.Random, entry: ir.EntryDef):
    """An entry call of a frame: the name alone, or (name, {parameter: argument}); parameters with a default are left out at random."""
    args = {p.name: _rand_arg(rng, p.ty) for p in entry.params if not p.has_default or rng.random() < 0.5}
    return (entry.name, args) if entry.params else entry.name


def call_items(frame: dict) -> list[tuple[str, dict]]:
    """The entry calls of a frame as (name, named arguments)."""
    out = []
    for c in frame.get("calls", []):
        out.append((c, {}) if isinstance(c, str) else (c[0], dict(c[1]) if len(c) > 1 else {}))
    return out


def _random_hook(rng: random.Random, event: str) -> tuple:
    """A random event of the character: modes for movement_mode_changed, a trace result for landed (no primitive: it is a scene object)."""
    if event == "movement_mode_changed":
        return (event, rng.randrange(7), rng.randrange(7))
    if event == "landed":
        v = lambda: tuple(rng.uniform(-300, 300) for _ in range(3))
        return (event, (rng.random() < 0.8, v(), tuple(rng.choice([-1.0, 0.0, 0.5, 1.0]) for _ in range(3)), rng.uniform(0, 100),
                        rng.uniform(0, 1), v(), rng.random() < 0.2, None, v()))
    return (event,)


def _random_events(module: ir.Module, rng: random.Random) -> list[tuple]:
    out: list[tuple] = []
    handlers = [h for h in module.handlers if h.event not in ("begin_play", "end_play")]
    for _ in range(rng.choice([0, 0, 1, 1, 2, 3])):
        h = rng.choice(handlers)
        if h.event in HOOK_EVENTS:
            out.append(_random_hook(rng, h.event))
        elif h.event in ("notify", "notify_begin", "notify_end"):
            out.append((h.event, h.name))
        elif h.event in ("montage_ended", "montage_blending_out"):
            out.append((h.event, h.name, rng.random() < 0.5))
        else:
            ty = h.params[0][1]
            value = _rand_value(rng, ty, 1.0)
            out.append(("input", h.name, h.phase, value))
    return out


# ---- running -----------------------------------------------------------------------------------

def _apply_free(target, frame: dict) -> None:
    for name, value in frame.get("free", {}).items():
        target.set_input(name, value)


def _snapshot(obj, host: ScriptedHost) -> dict:
    snap = obj.snapshot()
    snap["__cmds__"] = tuple(host.calls)
    host.calls.clear()
    return snap


def run_interpreter(module: ir.Module, frames: list[dict], **params) -> list[dict]:
    host = ScriptedHost()
    it = Interpreter(module, host, externs=load_standins(module), **params)
    out = []
    for f in frames:
        host.load(f)
        _apply_free(it, f)
        it.update(f["dt"])
        for name, args in call_items(f):
            it.call_entry(name, **args)
        if f.get("end_play"):
            it.end_play()
        out.append(_snapshot(it, host))
    return out


def run_python(module: ir.Module, frames: list[dict], source: str | None = None, **params) -> list[dict]:
    host = ScriptedHost()
    cls = load_class(module, source if source is not None else generate_python(module))
    obj = cls(host, **params)
    out = []
    for f in frames:
        host.load(f)
        _apply_free(obj, f)
        obj.update(f["dt"])
        for name, args in call_items(f):
            obj.call_entry(name, **args)
        if f.get("end_play") and hasattr(obj, "end_play"):
            obj.end_play()
        out.append(_snapshot(obj, host))
    return out


# ---- comparing -----------------------------------------------------------------------------------

def _close(a, b, tol: float) -> bool:
    if isinstance(a, tuple):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
        if a == b:
            return True
        return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    return a == b


def compare(a: list[dict], b: list[dict], tol: float = 0.0) -> list[tuple[int, str, object, object]]:
    """Frame-by-frame differences: (frame, name, value_a, value_b).  ``tol`` is relative (1e-6 is typical)."""
    diffs: list[tuple[int, str, object, object]] = []
    if len(a) != len(b):
        diffs.append((-1, "<length>", len(a), len(b)))
    for i, (fa, fb) in enumerate(zip(a, b)):
        for name in fa:
            if name not in fb or not _close(fa[name], fb[name], tol):
                diffs.append((i, name, fa[name], fb.get(name)))
    return diffs


def verify(module: ir.Module, frames: int = 300, seed: int = 1, tol: float = 0.0, **params) -> list[tuple]:
    """Interpreter vs generated Python on random frames; returns the list of differences (empty = consistent)."""
    seq = random_frames(module, frames, seed)
    return compare(run_interpreter(module, seq, **params), run_python(module, seq, **params), tol)


# ---- several logics that read and call each other -----------------------------------------------------------------

def phases_of(module: ir.Module) -> list[str]:
    """The tick slots a logic runs in (an anim logic has none)."""
    if module.kind != "actor":
        return []
    return [u.phase for u in module.updates] or [module.default_phase]


def default_schedule(modules: dict[str, ir.Module]) -> list[tuple[str, str | None]]:
    """The order the engine ticks the logics of one actor: actor logics before the movement, after the movement, then the
    animation instances, then the actor logics that run after the animation.  ``(name, None)`` runs a whole ``update``."""
    steps: list[tuple[str, str | None]] = []
    for slot in ("pre_movement", "after_movement"):
        steps += [(n, slot) for n, m in modules.items() if slot in phases_of(m)]
    steps += [(n, None) for n, m in modules.items() if m.kind == "anim"]
    steps += [(n, "after_anim") for n, m in modules.items() if "after_anim" in phases_of(m)]
    return steps


def random_system_frames(modules: dict[str, ir.Module], count: int, seed: int = 1) -> list[dict]:
    """Random frames for several logics: the host values once, free inputs and entry calls per logic."""
    rng = random.Random(seed)
    first = next(iter(modules.values()))
    frames = random_frames(first, count, seed)
    for f in frames:
        f["free"] = {}
        f["calls"] = {}
        for name, m in modules.items():
            f["free"][name] = {i.name: _rand_value(rng, i.ty) for i in m.inputs if i.is_free}
            entries = [*m.entries, *(m.linked.entries if m.linked is not None else [])]
            f["calls"][name] = [_random_call(rng, e) for e in entries if rng.random() < 0.6]
    return frames


def run_system(modules: dict[str, ir.Module], frames: list[dict], backend: str = "python",
               schedule: list[tuple[str, str | None]] | None = None, sources: dict[str, str] | None = None) -> list[dict]:
    """Run several connected logics frame by frame; returns ``{logic: snapshot}`` per frame.

    ``backend`` is 'interp' (the reference interpreter) or 'python' (generated classes)."""
    hosts = {n: ScriptedHost() for n in modules}
    objs: dict = {}
    for n, m in modules.items():
        if backend == "interp":
            objs[n] = Interpreter(m, hosts[n], externs=load_standins(m))
        else:
            objs[n] = load_class(m, (sources or {}).get(n))(hosts[n])
    for n, m in modules.items():
        for p in m.peers:
            if p.logic in objs:
                objs[n].connect(p.name, objs[p.logic])
        if m.linked is not None and m.linked.parent in objs:
            objs[n].connect("Parent", objs[m.linked.parent])
    steps = schedule or default_schedule(modules)
    # the engine begins play before the first tick, once every logic exists: actor logics first, then the animation instances
    for n in sorted(modules, key=lambda k: modules[k].kind != "actor"):
        begin = getattr(objs[n], "begin_play", None)
        if begin is not None:
            hosts[n].load(frames[0] if frames else {})
            begin()
    out = []
    for f in frames:
        for n in modules:
            hosts[n].load(f)
            for name, value in f.get("free", {}).get(n, {}).items():
                objs[n].set_input(name, value)
        for n, slot in steps:
            if slot is None:
                objs[n].update(f["dt"])
            else:
                objs[n].update_phase(slot, f["dt"])
        for n in modules:
            for item in f.get("calls", {}).get(n, []):
                name, args = (item, {}) if isinstance(item, str) else (item[0], dict(item[1]) if len(item) > 1 else {})
                objs[n].call_entry(name, **args)
        snap = {}
        for n in modules:
            snap[n] = _snapshot(objs[n], hosts[n])
        out.append(snap)
    return out


def verify_system(modules: dict[str, ir.Module], frames: int = 200, seed: int = 1, tol: float = 0.0,
                  schedule: list[tuple[str, str | None]] | None = None) -> list[tuple]:
    """Interpreter vs generated Python for a connected system; (frame, logic.name, interpreter, python) differences."""
    seq = random_system_frames(modules, frames, seed)
    a, b = run_system(modules, seq, "interp", schedule), run_system(modules, seq, "python", schedule)
    diffs = []
    for i, (fa, fb) in enumerate(zip(a, b)):
        for logic in fa:
            for d in compare([fa[logic]], [fb[logic]], tol):
                diffs.append((i, f"{logic}.{d[1]}", d[2], d[3]))
    return diffs
