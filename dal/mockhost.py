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
        self._curves: dict = {}                  # scripted animation curve values: name -> value
        self.primitives: dict[str, dict] = {}    # scripted properties of the scene's shapes: name -> {speed, can_step_up, transform}
        self.bones: dict[str, dict] = {}         # scripted pose: bone name -> {"component": transform, "world": transform}
        self.skeleton: dict[str, dict] = {}      # scripted reference skeleton: bone name -> {"parent": name or None, "translation": (x, y, z)}
        self.snapshots: list[str] = []           # the names of the pose snapshots taken (snapshot_pose), oldest first

    # ---- reads ---------------------------------------------------------------------------------------------------------
    def action(self, name, ty):
        if name in self._actions:
            return self._actions[name]
        return super().action(name, ty)

    def notify(self, name):
        return bool(self._notifies.get(name, False))

    def bone_component_transform(self, name):
        return rt.as_transform(self.bones.get(name, {}).get("component", rt.Transform()))

    def bone_world_transform(self, name):
        return rt.as_transform(self.bones.get(name, {}).get("world", rt.Transform()))

    def ref_path_child_translation(self, descendant, ancestor):
        """Walk up from ``descendant`` to ``ancestor``: the translation of the last bone before it (its direct child on the path)."""
        bone, child = descendant, None
        while bone is not None and bone != ancestor:
            child = bone
            bone = self.skeleton.get(bone, {}).get("parent")
        if bone != ancestor or child is None or child not in self.skeleton:
            return rt.VEC3_ZERO
        return rt.as_vec3(self.skeleton[child].get("translation", rt.VEC3_ZERO))

    def anim_curve(self, name):
        return float(self._curves.get(name, 0.0))

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

    def line_trace(self, start, end, channel):
        return self._trace(rt.as_vec3(start), rt.as_vec3(end), 0.0)

    def sweep_sphere(self, start, end, radius, channel, responses):
        return self._trace(rt.as_vec3(start), rt.as_vec3(end), float(radius))

    def sweep_capsule(self, start, end, radius, half_height, channel, responses):
        return self._trace(rt.as_vec3(start), rt.as_vec3(end), float(radius), float(half_height))

    def overlap_capsule(self, center, radius, half_height, channel, responses):
        c = rt.as_vec3(center)
        for off in self._axis_offsets(float(radius), float(half_height)):
            s = rt.Vec3(c.x, c.y, c.z + off)
            if self.ground_z is not None and abs(s.z - self.ground_z) < radius:
                return True
            for sc, sr in self.spheres:
                f = s - rt.as_vec3(sc)
                if rt.dot(f, f) < (sr + radius) ** 2:
                    return True
        return False

    # the scene's shapes have names: the ground plane is "Ground", the spheres "Sphere0", "Sphere1", ...; self.primitives scripts their properties
    def primitive_valid(self, primitive):
        return primitive is not None

    def primitive_speed(self, primitive):
        return float(self.primitives.get(primitive, {}).get("speed", 0.0))

    def primitive_can_step_up(self, primitive):
        return primitive is not None and bool(self.primitives.get(primitive, {}).get("can_step_up", True))

    def primitive_transform(self, primitive):
        return rt.as_transform(self.primitives.get(primitive, {}).get("transform", rt.Transform()))

    @staticmethod
    def _axis_offsets(radius, half_height):
        """Heights of the spheres that stand for the shape: one for a sphere or a line, bottom / middle / top cap for a capsule."""
        reach = half_height - radius
        return (0.0,) if reach <= 0.0 else (-reach, 0.0, reach)

    def _trace(self, a, b, radius, half_height=0.0):
        """Earliest hit of the segment a->b with the scene: a shape (a sphere of ``radius``, or a capsule standing for three of them) swept
        along it against the ground plane and the spheres.  A shape that starts inside something hits at time 0 (start_penetrating)."""
        d = b - a
        length = rt.length(d)
        best = None                                   # (t, point, normal, primitive, penetrating)
        for off in self._axis_offsets(radius, half_height):
            hit = self._trace_one(rt.Vec3(a.x, a.y, a.z + off), d, radius)
            if hit is not None and (best is None or hit[0] < best[0]):
                best = hit
        if best is None:
            return rt.Hit(False, b, rt.VEC3_ZERO, 0.0, 1.0, location=b)
        t, point, normal, primitive, penetrating = best
        return rt.Hit(True, point, normal, t * length, t, location=a + d * t, start_penetrating=penetrating, primitive=primitive, face_normal=normal)

    def _trace_one(self, s, d, radius):
        best = None
        if self.ground_z is not None:
            above = s.z >= self.ground_z
            if abs(s.z - self.ground_z) < radius:
                best = (0.0, rt.Vec3(s.x, s.y, self.ground_z), rt.Vec3(0.0, 0.0, 1.0 if above else -1.0), "Ground", True)
            elif d.z != 0.0:
                gz = self.ground_z + (radius if above else -radius)
                t = (gz - s.z) / d.z
                if 0.0 <= t <= 1.0:
                    p = s + d * t
                    best = (t, rt.Vec3(p.x, p.y, self.ground_z), rt.Vec3(0.0, 0.0, 1.0 if above else -1.0), "Ground", False)
        for i, (center, r) in enumerate(self.spheres):
            c = rt.as_vec3(center)
            rr = r + radius
            f = s - c
            if rt.dot(f, f) < rr * rr:                # starts inside
                n = rt.normalize(f)
                if n == rt.VEC3_ZERO:
                    n = rt.Vec3(0.0, 0.0, 1.0)
                if best is None or 0.0 < best[0]:
                    best = (0.0, c + n * r, n, f"Sphere{i}", True)
                continue
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
                p = s + d * t
                n = rt.normalize(p - c)
                best = (t, c + n * r, n, f"Sphere{i}", False)
        return best

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
    """A montage of the simulation.  ``notifies`` are ``(time, name)`` or ``(time, name, payload)``, ``windows`` ``(start, end, name)`` or
    ``(start, end, name, payload)``: with a payload (a dict of the properties of a declared notify class) the events carry it and the
    montage instance (``("notify_begin", name, payload, instance)``) and a window also ticks (``notify_tick``) while it is open."""

    name: str
    length: float
    sections: dict = field(default_factory=dict)              # section name -> start time (insertion order = time order)
    notifies: list = field(default_factory=list)              # (time, notify name[, payload])
    windows: list = field(default_factory=list)               # (start, end, notify state name[, payload])
    blend_out: float = 0.2
    slot: str = "DefaultSlot"                                 # the slot of its track (stop_montages_with_slot)


class _Instance:
    def __init__(self, montage: SimMontage, rate: float, position: float, ident: int = 0):
        self.m = montage
        self.id = ident                         # the montage instance id a notify event carries
        self.rate = rate
        self.position = position
        self.state = "playing"                  # playing | blending_out | done
        self.blend_left = 0.0
        self.interrupted = False
        self.open_windows: dict[str, object] = {}       # notify state name -> its payload (None: a plain window without one)

    def section(self) -> str | None:
        name = None
        for sec, start in self.m.sections.items():
            if self.position >= start:
                name = sec
        return name


class SimHost(MockHost):
    def __init__(self, montages: list[SimMontage], sequences: dict | None = None) -> None:
        """``sequences`` maps the name of an animation sequence to its length: play_slot_animation makes a dynamic montage of it."""
        super().__init__()
        self.sequences = dict(sequences or {})
        self.catalog = {m.name: m for m in montages}
        self.instances: list[_Instance] = []
        self.time = 0.0
        self._next_id = 1

    # ---- commands that change the simulation ---------------------------------------------------------------------------------
    def montage_play(self, name, rate, section):
        self.record("montage_play", (name, rate, section))
        if name is None or name not in self.catalog:                # an empty montage value, or an asset the simulation does not have
            return
        for inst in self.instances:
            if inst.state == "playing":
                self._blend_out(inst, inst.m.blend_out, interrupted=True)
        m = self.catalog[name]
        pos = m.sections.get(section, 0.0) if section else 0.0
        inst = _Instance(m, float(rate), pos, self._next_id)
        self._next_id += 1
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

    def montage_set_position(self, name, position):
        self.record("montage_set_position", (name, position))
        inst = self._newest(name, ("playing",))
        if inst is not None:
            inst.position = min(max(float(position), 0.0), inst.m.length)

    def montage_stop_source(self, source, blend_out):
        self.record("montage_stop_source", (source, blend_out))
        for inst in self.instances:
            if inst.id == source and inst.state == "playing":
                self._blend_out(inst, inst.m.blend_out if blend_out < 0 else blend_out, interrupted=True)

    def play_slot_animation(self, sequence, slot, blend_in, blend_out, rate, loops, blend_out_trigger, start_time, blend_out_mode):
        self.record("play_slot_animation", (sequence, slot, blend_in, blend_out, rate, loops, blend_out_trigger, start_time, blend_out_mode))
        if sequence is None or sequence not in self.sequences:
            return
        for inst in self.instances:                                 # a new montage in a slot takes the slot: the one playing there blends out
            if inst.state == "playing" and inst.m.slot == slot:
                self._blend_out(inst, inst.m.blend_out, interrupted=True)
        m = self.catalog.get(f"{sequence}@{slot}")
        if m is None:
            m = self.catalog[f"{sequence}@{slot}"] = SimMontage(sequence, float(self.sequences[sequence]), blend_out=float(blend_out), slot=slot)
        inst = _Instance(m, float(rate), float(start_time), self._next_id)
        self._next_id += 1
        self.instances.append(inst)

    def stop_montages_with_slot(self, slot, blend_out):
        self.record("stop_montages_with_slot", (slot, blend_out))
        for inst in self.instances:
            if inst.state == "playing" and inst.m.slot == slot:
                self._blend_out(inst, inst.m.blend_out if blend_out < 0 else blend_out, interrupted=True)

    def snapshot_pose(self, name):
        self.record("snapshot_pose", (name,))
        self.snapshots.append(name)

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

    @staticmethod
    def _event(kind, name, payload, inst):
        """The event of a notify: with a payload (a declared notify class) it carries the properties and the montage instance."""
        return (kind, name) if payload is None else (kind, name, payload, inst.id)

    def _notify_range(self, inst, prev, cur):
        m = inst.m
        timed = [(n[0], 1, self._event("notify", n[1], n[2] if len(n) > 2 else None, inst)) for n in m.notifies if prev < n[0] <= cur]
        for w in m.windows:
            start, end, n = w[0], w[1], w[2]
            payload = w[3] if len(w) > 3 else None
            if prev < start <= cur:
                timed.append((start, 0, self._event("notify_begin", n, payload, inst)))
                inst.open_windows[n] = payload
            if prev < end <= cur and n in inst.open_windows:
                timed.append((end, 2, self._event("notify_end", n, inst.open_windows[n], inst)))
                del inst.open_windows[n]
        for _t, _o, ev in sorted(timed, key=lambda x: (x[0], x[1])):
            self.events.append(ev)

    def _finish(self, inst):
        for n in sorted(inst.open_windows):
            self.events.append(self._event("notify_end", n, inst.open_windows[n], inst))
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
                for n, payload in inst.open_windows.items():            # the notify states of declared classes tick while the montage plays them
                    if payload is not None:
                        self.events.append(("notify_tick", n, payload, inst.id))
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
