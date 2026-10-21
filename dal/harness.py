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
from .hostapi import HOST_ITEMS
from .mockhost import MockHost
from .interp import Interpreter
from .pybackend import generate_python, load_class
from .types import BOOL, FLOAT, INT, QUAT, ROTATOR, VEC2, VEC3, EnumType, StructType, TagType, Type


class ScriptedHost(MockHost):
    """A host that replays one frame dict at a time and records the commands it is given."""

    def load(self, frame: dict) -> None:
        for name in HOST_ITEMS:
            if name in ("action", "notify") or name.startswith("montage_"):
                continue
            if name in frame:
                setattr(self, name, frame[name])
        self._actions = frame.get("actions", {})
        self._notifies = frame.get("notifies", {})
        self.events = [tuple(e) for e in frame.get("events", [])]
        self.montages = dict(frame.get("montages", {}))
        self.targets = [(tid, tuple(loc)) for tid, loc in frame.get("targets", [])]
        world = frame.get("world", {})
        self.ground_z = world.get("ground_z")
        self.spheres = [(tuple(c), r) for c, r in world.get("spheres", [])]


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
    if isinstance(ty, EnumType):
        return rng.randrange(len(ty.members))
    if isinstance(ty, TagType):
        return ty.full_name(rng.choice(ty.members + ["None"]))
    if isinstance(ty, StructType):
        return {f.name: _rand_value(rng, f.ty, scale) for f in ty.fields if not f.derived and not f.ty.is_asset and f.ty.name not in ("curve", "curve3")}
    raise ValueError(ty)


def _rand_quat(rng: random.Random):
    while True:
        v = [rng.gauss(0, 1) for _ in range(4)]
        n = math.sqrt(sum(c * c for c in v))
        if n > 1e-3:
            return tuple(c / n for c in v)


def random_frames(module: ir.Module, count: int, seed: int = 1) -> list[dict]:
    """Random frames covering every input of the module (host-bound and free)."""
    rng = random.Random(seed)
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
        for i in module.inputs:
            if i.host is None:
                f["free"][i.name] = _rand_value(rng, i.ty)
            elif i.host.item == "action":
                f["actions"][i.host.arg] = _rand_value(rng, i.ty, 1.0)
            elif i.host.item == "notify":
                f["notifies"][i.host.arg] = rng.random() < 0.25
        if module.entries:
            f["calls"] = [e.name for e in module.entries if rng.random() < 0.6]
        if module.handlers:
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
        frames.append(f)
    return frames


def _random_events(module: ir.Module, rng: random.Random) -> list[tuple]:
    out: list[tuple] = []
    for _ in range(rng.choice([0, 0, 1, 1, 2, 3])):
        h = rng.choice(module.handlers)
        if h.event in ("notify", "notify_begin", "notify_end"):
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
        for name in f.get("calls", []):
            it.call_entry(name)
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
        for name in f.get("calls", []):
            obj.call_entry(name)
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
