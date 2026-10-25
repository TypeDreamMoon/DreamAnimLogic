"""A scene and a movement component for the tests of the ALS actions (``test_als_actions.py``).

``BoxWorld`` is a ``ScriptedHost`` whose queries meet axis aligned boxes next to the ground plane of the mock: the forward capsule sweep, the
downward sphere sweep and the overlaps of ``AlsCharacter.start_mantling_with`` need a wall and a ledge to hit.  The shapes are crude on purpose
(a swept capsule or sphere meets a box grown by its radius, the vertical extent of a capsule grows the box too): they answer the questions the
port asks with the right normals, impact points and locations, which is what the tests check; they are not a collision engine.

``MoveSim`` is the movement component of the closed loop tests: it takes the commands the character hands to the host and moves a capsule the way
the C++ would (root motion override in the custom movement mode, set_actor_location / rotation, the movement mode and its lock).
"""

from __future__ import annotations

import math

import _bootstrap  # noqa: F401

from dal import harness
from dal import runtime as rt


class Box:
    def __init__(self, name, lo, hi, can_step_up=True, speed=0.0):
        self.name, self.lo, self.hi, self.can_step_up, self.speed = name, tuple(lo), tuple(hi), can_step_up, speed


class BoxWorld(harness.ScriptedHost):
    """Boxes (and what the mock has: a ground plane, spheres) as the scene of the world queries."""

    def __init__(self):
        super().__init__()
        self.boxes: list[Box] = []
        self.gone: set[str] = set()                      # primitives that were destroyed: primitive_valid says no

    def load(self, frame: dict) -> None:
        super().load(frame)
        for b in self.boxes:
            self.primitives[b.name] = {"speed": b.speed, "can_step_up": b.can_step_up}

    def primitive_valid(self, primitive):
        return primitive is not None and primitive not in self.gone

    # ---- queries ------------------------------------------------------------------------------------------------------------------
    def _grown(self, b: Box, radius: float, half_height: float):
        z = half_height if half_height > 0.0 else radius
        return (b.lo[0] - radius, b.lo[1] - radius, b.lo[2] - z), (b.hi[0] + radius, b.hi[1] + radius, b.hi[2] + z)

    def _trace(self, a, b, radius, half_height=0.0):
        best = super()._trace(a, b, radius, half_height)
        d = b - a
        length = rt.length(d)
        for box in self.boxes:
            lo, hi = self._grown(box, radius, half_height)
            t_enter, t_exit, axis, sign = 0.0, 1.0, None, 0.0
            inside = all(lo[i] < c < hi[i] for i, c in enumerate((a.x, a.y, a.z)))
            ok = True
            for i, (s, dd) in enumerate(zip((a.x, a.y, a.z), (d.x, d.y, d.z))):
                if abs(dd) < 1e-12:
                    if s < lo[i] or s > hi[i]:
                        ok = False
                        break
                    continue
                t0, t1 = (lo[i] - s) / dd, (hi[i] - s) / dd
                sg = -1.0 if dd > 0 else 1.0                      # the face the ray enters through
                if t0 > t1:
                    t0, t1 = t1, t0
                if t0 > t_enter:
                    t_enter, axis, sign = t0, i, sg
                t_exit = min(t_exit, t1)
                if t_enter > t_exit:
                    ok = False
                    break
            if not ok:
                continue
            if inside:
                # starts inside: out through the nearest face
                depth = [(min(c - lo[i], hi[i] - c), i, -1.0 if c - lo[i] < hi[i] - c else 1.0) for i, c in enumerate((a.x, a.y, a.z))]
                _, i, sg = min(depth)
                normal = [0.0, 0.0, 0.0]
                normal[i] = sg
                n = rt.Vec3(*normal)
                point = rt.Vec3(min(max(a.x, box.lo[0]), box.hi[0]), min(max(a.y, box.lo[1]), box.hi[1]), min(max(a.z, box.lo[2]), box.hi[2]))
                hit = rt.Hit(True, point, n, 0.0, 0.0, location=a, start_penetrating=True, primitive=box.name, face_normal=n)
                t = 0.0
            else:
                if axis is None or not (0.0 <= t_enter <= 1.0):
                    continue
                normal = [0.0, 0.0, 0.0]
                normal[axis] = sign
                n = rt.Vec3(*normal)
                loc = a + d * t_enter
                point = rt.Vec3(min(max(loc.x, box.lo[0]), box.hi[0]), min(max(loc.y, box.lo[1]), box.hi[1]), min(max(loc.z, box.lo[2]), box.hi[2]))
                hit = rt.Hit(True, point, n, t_enter * length, t_enter, location=loc, primitive=box.name, face_normal=n)
                t = t_enter
            if not best.blocking or t < best.time:
                best = hit
        return best

    def overlap_capsule(self, center, radius, half_height, channel, responses):
        if super().overlap_capsule(center, radius, half_height, channel, responses):
            return True
        c = rt.as_vec3(center)
        for box in self.boxes:
            lo, hi = self._grown(box, float(radius), float(half_height))
            if all(lo[i] < v < hi[i] for i, v in enumerate((c.x, c.y, c.z))):
                return True
        return False


def yaw_quat(deg):
    r = math.radians(deg) / 2.0
    return (0.0, 0.0, math.sin(r), math.cos(r))


def quat_mul(a, b):
    """Hamilton product, (x, y, z, w): a * b rotates by b first, then by a (the Unreal convention)."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def yaw_of(q):
    x, y, z, w = q
    return math.degrees(math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)))


class MoveSim:
    """The movement component of a closed loop: location, yaw, velocity, the movement mode and what the commands of the character do to them."""

    WALKING, FALLING, CUSTOM, NONE = 1, 3, 6, 0

    def __init__(self, location=(0.0, 0.0, 92.2), yaw=0.0):
        self.location = list(location)
        self.rotation = yaw_quat(yaw)
        self.velocity = [0.0, 0.0, 0.0]
        self.mode = self.WALKING
        self.locked = False
        self.override = None                      # (velocity, rotation delta) of root_motion_override
        self.input_blocked = False
        self.log: list[str] = []
        self.hooks: list[tuple] = []              # what the component raised while it moved: the events of the next after_movement slot

    # -- commands the character handed to the host
    def apply(self, cmds):
        for name, args in cmds:
            if name == "set_movement_mode":
                self.set_mode(args[0])
            elif name == "set_movement_mode_locked":
                self.locked = bool(args[0])
            elif name == "root_motion_override":
                self.override = (tuple(args[0]), tuple(args[1]))
            elif name == "root_motion_clear":
                self.override = None
            elif name == "set_actor_location":
                self.location = list(args[0])
            elif name == "set_actor_rotation":
                r = args[0]
                self.rotation = yaw_quat(r[1])
            elif name == "set_velocity":
                self.velocity = list(args[0])
            elif name == "set_input_blocked":
                self.input_blocked = bool(args[0])
            self.log.append(name)

    def set_mode(self, mode):
        mode = int(mode)
        if self.locked or mode == self.mode:
            return
        previous, self.mode = self.mode, mode
        self.hooks.append(("movement_mode_changed", previous, mode))

    # -- one tick of movement
    def move(self, dt):
        """PhysCustom of ALS (CMC:606-629): without a root motion override the velocity is zero, with one the actor moves by it."""
        if self.mode == self.CUSTOM:
            if self.override is None:
                self.velocity = [0.0, 0.0, 0.0]
            else:
                v, delta = self.override
                self.velocity = list(v)
                self.location = [c + s * dt for c, s in zip(self.location, v)]
                self.rotation = quat_mul(delta, self.rotation)

    def host(self):
        """The host values the character reads in the next tick."""
        return {"actor_location": tuple(self.location), "actor_rotation": self.rotation, "velocity": tuple(self.velocity),
                "movement_mode": self.mode, "is_grounded": self.mode == self.WALKING, "has_root_motion": self.override is not None}
