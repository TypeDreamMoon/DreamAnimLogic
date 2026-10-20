"""Hand-written oracle for examples/lockon_camera.dal (no dal imports; its own small vector math).

Intended behaviour, in the order one update applies it: the lost-sight timer counts down; the LockOn
press (if any) toggles the lock -- picking the target with the smallest score (angle to the facing
direction over the allowed angle, plus a quarter of the distance over the allowed distance; nothing
beyond the allowed angle or distance); then, while locked, the target is looked up by id (gone ->
unlock), the line of sight is traced from the eye to the target, losing sight starts the timer and
unlocks when it runs out, and otherwise the control rotation turns towards the target, the arm length
follows the distance, and both are sent to the host.
"""

import math

SMALL_NUMBER = 1.0e-8


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a):
    return math.sqrt(dot(a, a))


def unit(a):
    sq = dot(a, a)
    if sq < SMALL_NUMBER:
        return (0.0, 0.0, 0.0)
    s = 1.0 / math.sqrt(sq)
    return (a[0] * s, a[1] * s, a[2] * s)


def angle_between(a, b):
    na, nb = unit(a), unit(b)
    if na == (0.0, 0.0, 0.0) or nb == (0.0, 0.0, 0.0):
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, dot(na, nb)))))


def look_at(src, dst):
    d = sub(dst, src)
    return (math.degrees(math.atan2(d[2], math.sqrt(d[0] * d[0] + d[1] * d[1]))), math.degrees(math.atan2(d[1], d[0])), 0.0)


def normalize_axis(angle):
    angle = math.fmod(angle, 360.0)
    if angle < 0.0:
        angle += 360.0
    if angle > 180.0:
        angle -= 360.0
    return angle


def rotator_interp_to(cur, target, dt, speed):
    """Move ``cur`` towards ``target`` along the short way, by dt * speed of the remaining angle (at most all of it)."""
    if dt == 0.0 or tuple(cur) == tuple(target):
        return tuple(cur)
    if speed <= 0.0:
        return tuple(target)
    delta = [normalize_axis(t - c) for c, t in zip(cur, target)]
    if all(abs(normalize_axis(d)) <= 1.0e-4 for d in delta):
        return tuple(target)
    k = max(0.0, min(dt * speed, 1.0))
    return tuple(normalize_axis(c + d * k) for c, d in zip(cur, delta))


def float_interp_to(cur, target, dt, speed):
    if speed <= 0.0:
        return target
    dist = target - cur
    if dist * dist < SMALL_NUMBER:
        return target
    return cur + dist * max(0.0, min(dt * speed, 1.0))


def rotate_by_quat(q, v):
    x, y, z, w = q
    qv = (x, y, z)
    t = cross(qv, v)
    t = (2.0 * t[0], 2.0 * t[1], 2.0 * t[2])
    c = cross(qv, t)
    return (v[0] + w * t[0] + c[0], v[1] + w * t[1] + c[1], v[2] + w * t[2] + c[2])


class LockOnOracle:
    def __init__(self, host, max_distance=1200.0, max_angle=55.0, eye_height=60.0, turn_speed=8.0, arm_base=350.0,
                 arm_per_cm=0.15, arm_max=650.0, arm_speed=4.0, lose_sight_after=1.5):
        self.host = host
        self.p = dict(max_distance=max_distance, max_angle=max_angle, eye_height=eye_height, turn_speed=turn_speed,
                      arm_base=arm_base, arm_per_cm=arm_per_cm, arm_max=arm_max, arm_speed=arm_speed, lose_sight_after=lose_sight_after)
        self.locked = False
        self.target_id = -1
        self.target_location = (0.0, 0.0, 0.0)
        self.visible = False
        self.aim = (0.0, 0.0, 0.0)
        self.arm_length = 350.0
        self.distance = 0.0
        self.candidates = 0
        self.lost_sight = 0.0
        self.counting = False

    def score(self, origin, forward, candidate):
        to = sub(candidate, origin)
        dist = length(to)
        angle = angle_between(forward, to)
        if dist > self.p["max_distance"] or angle > self.p["max_angle"]:
            return 1000000.0
        return angle / self.p["max_angle"] + 0.25 * dist / self.p["max_distance"]

    def update(self, dt):
        h, p = self.host, self.p
        location = tuple(h.actor_location)
        rotation = tuple(h.actor_rotation)
        control = tuple(h.control_rotation)
        events = h.poll_events()
        commands = []

        if self.lost_sight > 0.0:
            self.lost_sight = max(self.lost_sight - dt, 0.0)

        for ev in events:
            if not (ev[0] == "input" and ev[1] == "LockOn" and ev[2] == "started"):
                continue
            if self.locked:
                self.locked = False
                self.target_id = -1
                self.counting = False
                self.lost_sight = 0.0
            else:
                eye = add(location, (0.0, 0.0, p["eye_height"]))
                forward = rotate_by_quat(rotation, (1.0, 0.0, 0.0))
                best, best_score, considered = -1, 1000000.0, 0
                for i in range(8):
                    if i < h.target_count():
                        considered += 1
                        s = self.score(eye, forward, tuple(h.target_location(i)))
                        if s < best_score:
                            best_score, best = s, i
                self.candidates = considered
                if best >= 0:
                    self.locked = True
                    self.target_id = h.target_id(best)
                    self.aim = control

        if self.locked:
            index = h.target_index(self.target_id)
            if index < 0:
                self.locked = False
                self.target_id = -1
                self.counting = False
                self.lost_sight = 0.0
            else:
                eye = add(location, (0.0, 0.0, p["eye_height"]))
                self.target_location = tuple(h.target_location(index))
                self.distance = length(sub(location, self.target_location))
                sight = h.trace_line(eye, self.target_location)
                self.visible = not sight.blocking
                if self.visible:
                    self.counting = False
                    self.lost_sight = 0.0
                elif not self.counting:
                    self.counting = True
                    self.lost_sight = p["lose_sight_after"]
                if self.counting and self.lost_sight <= 0.0:
                    self.locked = False
                    self.target_id = -1
                    self.counting = False
                else:
                    self.aim = rotator_interp_to(self.aim, look_at(eye, self.target_location), dt, p["turn_speed"])
                    commands.append(("set_control_rotation", (self.aim,)))
                    goal = max(p["arm_base"], min(p["arm_base"] + self.distance * p["arm_per_cm"], p["arm_max"]))
                    self.arm_length = float_interp_to(self.arm_length, goal, dt, p["arm_speed"])
                    commands.append(("camera_set_arm_length", (self.arm_length,)))

        for name, args in commands:
            getattr(h, name)(*args)

    def snapshot(self):
        return {"Locked": self.locked, "TargetId": self.target_id, "TargetLocation": self.target_location, "Visible": self.visible,
                "Aim": self.aim, "ArmLength": self.arm_length, "Distance": self.distance, "Candidates": self.candidates,
                "Counting": self.counting, "LostSight": self.lost_sight}
