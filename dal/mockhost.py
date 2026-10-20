"""Hosts for tests and tools: a recording ``MockHost`` and a ``SimHost`` that simulates montages.

``MockHost`` implements the whole ``HostV1`` protocol with scripted reads and recorded commands: every
command call is appended to ``calls`` as ``(name, args)`` with plain-data arguments, and ``set_*``
commands are echoed into ``state``.  Traces run against an analytic scene: a ground plane and spheres.

``SimHost`` adds the part of a real host that closes the loop for combo logic: it plays montages
(``SimMontage``: length, sections, notifies, notify windows) when the logic asks, advances them with
``advance(dt)`` and turns what happens into the events the logic polls (notify, notify window
begin / end, blending out, ended with the interrupted flag).  The simulation follows UE's observable
order: starting a montage interrupts the playing one (blending out starts at once, the interrupted
montage ends when its blend is over), a montage that runs to its end starts blending out
``blend_out`` seconds before the end.

None of this is Unreal or Blender; the real hosts implement ``HostV1`` the same way.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import runtime as rt
from .hostapi import COMMANDS, HostV1


def plain(v):
    """Command arguments as plain data (floats, bools, ints, tuples, strings, None)."""
    return rt.to_plain(v) if not isinstance(v, (str, type(None))) else v


class MockHost(HostV1):
    def __init__(self) -> None:
        self.calls: list[tuple] = []             # every command, oldest first
        self.state: dict = {}                    # last value of every set_* command
        self.events: list[tuple] = []            # delivered (and cleared) by poll_events
        self.montages: dict[str, dict] = {}      # scripted montage state: name -> playing / active / position / section
        self.targets: list[tuple[int, tuple]] = []
        self.ground_z: float | None = None
        self.spheres: list[tuple[tuple, float]] = []
        self._actions: dict = {}
        self._notifies: dict = {}

    # ---- reads ---------------------------------------------------------------------------------------------------------
    def action(self, name, ty):
        if name in self._actions:
            return self._actions[name]
        return super().action(name, ty)

    def notify(self, name):
        return bool(self._notifies.get(name, False))

    def _montage(self, name):
        return self.montages.get(name, {})

    def montage_playing(self, name):
        return bool(self._montage(name).get("playing", False))

    def montage_active(self, name):
        return bool(self._montage(name).get("active", self._montage(name).get("playing", False)))

    def montage_position(self, name):
        return float(self._montage(name).get("position", 0.0))

    def montage_section(self, name):
        return self._montage(name).get("section")

    def poll_events(self):
        events, self.events = self.events, []
        return events

    # ---- targets and traces -------------------------------------------------------------------------------------------------
    def target_count(self):
        return len(self.targets)

    def target_id(self, index):
        return self.targets[index][0] if 0 <= index < len(self.targets) else -1

    def target_location(self, index):
        return rt.as_vec3(self.targets[index][1]) if 0 <= index < len(self.targets) else rt.VEC3_ZERO

    def target_index(self, target_id):
        for i, (tid, _loc) in enumerate(self.targets):
            if tid == target_id:
                return i
        return -1

    def trace_line(self, start, end):
        return self._trace(rt.as_vec3(start), rt.as_vec3(end), 0.0)

    def trace_sphere(self, start, end, radius):
        return self._trace(rt.as_vec3(start), rt.as_vec3(end), float(radius))

    def _trace(self, a, b, radius):
        """Earliest hit of the segment a->b (a sphere of ``radius`` swept along it) with the ground plane and the spheres."""
        d = b - a
        length = rt.length(d)
        best = None                                   # (t, point, normal)
        if self.ground_z is not None and d.z != 0.0:
            gz = self.ground_z + (radius if a.z >= self.ground_z else -radius)
            t = (gz - a.z) / d.z
            if 0.0 <= t <= 1.0:
                p = a + d * t
                n = rt.Vec3(0.0, 0.0, 1.0 if a.z >= self.ground_z else -1.0)
                best = (t, rt.Vec3(p.x, p.y, self.ground_z), n)
        for center, r in self.spheres:
            c = rt.as_vec3(center)
            rr = r + radius
            f = a - c
            qa = rt.dot(d, d)
            if qa <= 0.0:
                continue
            qb = 2.0 * rt.dot(f, d)
            qc = rt.dot(f, f) - rr * rr
            disc = qb * qb - 4.0 * qa * qc
            if disc < 0.0:
                continue
            t = (-qb - math.sqrt(disc)) / (2.0 * qa)
            if 0.0 <= t <= 1.0 and (best is None or t < best[0]):
                p = a + d * t
                n = rt.normalize(p - c)
                best = (t, c + n * r, n)
        if best is None:
            return rt.Hit(False, b, rt.VEC3_ZERO, 0.0, 1.0)
        t, point, normal = best
        return rt.Hit(True, point, normal, t * length, t)

    # ---- commands -------------------------------------------------------------------------------------------------------------
    def record(self, name, args):
        args = tuple(plain(a) for a in args)
        self.calls.append((name, args))
        if name.startswith("set_") and len(args) == 1:
            self.state[name[4:]] = args[0]
        elif name.startswith("camera_set_"):
            self.state["camera_" + name[len("camera_set_"):]] = args[0]


def _install_recorders() -> None:
    for cname in COMMANDS:
        def make(n):
            def method(self, *args):
                self.record(n, args)
            method.__name__ = n
            return method
        setattr(MockHost, cname, make(cname))


_install_recorders()


# ---- montage simulation ------------------------------------------------------------------------------------------------------------

@dataclass
class SimMontage:
    name: str
    length: float
    sections: dict = field(default_factory=dict)              # section name -> start time (insertion order = time order)
    notifies: list = field(default_factory=list)              # (time, notify name)
    windows: list = field(default_factory=list)               # (start, end, notify state name)
    blend_out: float = 0.2


class _Instance:
    def __init__(self, montage: SimMontage, rate: float, position: float):
        self.m = montage
        self.rate = rate
        self.position = position
        self.state = "playing"                  # playing | blending_out | done
        self.blend_left = 0.0
        self.interrupted = False
        self.open_windows: set[str] = set()

    def section(self) -> str | None:
        name = None
        for sec, start in self.m.sections.items():
            if self.position >= start:
                name = sec
        return name


class SimHost(MockHost):
    def __init__(self, montages: list[SimMontage]) -> None:
        super().__init__()
        self.catalog = {m.name: m for m in montages}
        self.instances: list[_Instance] = []
        self.time = 0.0

    # ---- commands that change the simulation ---------------------------------------------------------------------------------
    def montage_play(self, name, rate, section):
        self.record("montage_play", (name, rate, section))
        for inst in self.instances:
            if inst.state == "playing":
                self._blend_out(inst, inst.m.blend_out, interrupted=True)
        m = self.catalog[name]
        pos = m.sections.get(section, 0.0) if section else 0.0
        inst = _Instance(m, float(rate), pos)
        self.instances.append(inst)
        self._notify_range(inst, -1.0, pos)       # things at the start position are not 'passed'

    def montage_stop(self, name, blend_out):
        self.record("montage_stop", (name, blend_out))
        for inst in self.instances:
            if inst.m.name == name and inst.state == "playing":
                self._blend_out(inst, inst.m.blend_out if blend_out < 0 else blend_out, interrupted=True)

    def montage_stop_all(self, blend_out):
        self.record("montage_stop_all", (blend_out,))
        for inst in self.instances:
            if inst.state == "playing":
                self._blend_out(inst, inst.m.blend_out if blend_out < 0 else blend_out, interrupted=True)

    def montage_jump_to_section(self, name, section):
        self.record("montage_jump_to_section", (name, section))
        for inst in self.instances:
            if inst.m.name == name and inst.state == "playing" and section in inst.m.sections:
                inst.position = inst.m.sections[section]

    def montage_set_play_rate(self, name, rate):
        self.record("montage_set_play_rate", (name, rate))
        for inst in self.instances:
            if inst.m.name == name and inst.state != "done":
                inst.rate = float(rate)

    # ---- reads from the simulation --------------------------------------------------------------------------------------------
    def _newest(self, name, states):
        for inst in reversed(self.instances):
            if inst.m.name == name and inst.state in states:
                return inst
        return None

    def montage_playing(self, name):
        return self._newest(name, ("playing",)) is not None

    def montage_active(self, name):
        return self._newest(name, ("playing", "blending_out")) is not None

    def montage_position(self, name):
        inst = self._newest(name, ("playing", "blending_out"))
        return inst.position if inst else 0.0

    def montage_section(self, name):
        inst = self._newest(name, ("playing",))
        return inst.section() if inst else None

    # ---- time -------------------------------------------------------------------------------------------------------------------
    def _blend_out(self, inst, seconds, interrupted):
        inst.state = "blending_out"
        inst.blend_left = max(seconds, 0.0)
        inst.interrupted = inst.interrupted or interrupted
        self.events.append(("montage_blending_out", inst.m.name, interrupted))

    def _notify_range(self, inst, prev, cur):
        m = inst.m
        timed = [(t, 1, ("notify", n)) for t, n in m.notifies if prev < t <= cur]
        for start, end, n in m.windows:
            if prev < start <= cur:
                timed.append((start, 0, ("notify_begin", n)))
                inst.open_windows.add(n)
            if prev < end <= cur and n in inst.open_windows:
                timed.append((end, 2, ("notify_end", n)))
                inst.open_windows.discard(n)
        for _t, _o, ev in sorted(timed, key=lambda x: (x[0], x[1])):
            self.events.append(ev)

    def _finish(self, inst):
        for n in sorted(inst.open_windows):
            self.events.append(("notify_end", n))
        inst.open_windows.clear()
        inst.state = "done"
        self.events.append(("montage_ended", inst.m.name, inst.interrupted))

    def advance(self, dt: float) -> None:
        """Advance every montage by dt seconds, queueing the events that happen."""
        self.time += dt
        for inst in list(self.instances):
            if inst.state == "playing":
                prev = inst.position
                inst.position = min(prev + dt * inst.rate, inst.m.length)
                self._notify_range(inst, prev, inst.position)
                if inst.position >= inst.m.length:
                    self._blend_out(inst, 0.0, interrupted=False)
                    self._finish(inst)
                elif inst.position >= inst.m.length - inst.m.blend_out > prev:
                    self._blend_out(inst, inst.m.length - inst.position, interrupted=False)
            elif inst.state == "blending_out":
                inst.position = min(inst.position + dt * inst.rate, inst.m.length)
                inst.blend_left -= dt
                if inst.blend_left <= 1e-9:
                    self._finish(inst)
        self.instances = [i for i in self.instances if i.state != "done"]
