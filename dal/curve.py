"""Float and vector curves with the evaluation rules of Unreal's ``FRichCurve`` (``UCurveFloat`` / ``UCurveVector``).

A curve is a list of keys (time, value, interpolation mode, arrive / leave tangent) plus the extrapolation before the first
and after the last key.  ``eval`` follows ``FRichCurve::Eval``: constant / linear / cubic (Bezier from the tangents)
interpolation between keys, constant / linear / cycle / cycle-with-offset / oscillate extrapolation.  The tangents are
taken from the keys as exported (Unreal stores the computed ones); ``auto_tangents`` is only a fallback for keys that
come without any.

Pure Python, no dependencies.  The values are doubles (Unreal evaluates in float).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import runtime as rt

CONSTANT, LINEAR, CUBIC = "constant", "linear", "cubic"
EXTRAP = ("constant", "linear", "cycle", "cycle_offset", "oscillate")

_KINDA_SMALL = 1.0e-4
_INTERP_ALIASES = {"rcim_constant": CONSTANT, "rcim_linear": LINEAR, "rcim_cubic": CUBIC, "none": CONSTANT, "constant": CONSTANT,
                   "linear": LINEAR, "cubic": CUBIC}
_EXTRAP_ALIASES = {"rcce_constant": "constant", "rcce_linear": "linear", "rcce_cycle": "cycle", "rcce_cycle_with_offset": "cycle_offset",
                   "rcce_cyclewithoffset": "cycle_offset", "rcce_oscillate": "oscillate", "cycle_with_offset": "cycle_offset",
                   "constant": "constant", "linear": "linear", "cycle": "cycle", "cycle_offset": "cycle_offset",
                   "cyclewithoffset": "cycle_offset", "oscillate": "oscillate"}


@dataclass
class Key:
    time: float
    value: float
    interp: str = LINEAR
    arrive: float = 0.0
    leave: float = 0.0
    tangent_mode: str = "auto"


def _bezier(p0: float, p1: float, p2: float, p3: float, a: float) -> float:
    """FRichCurve's BezierInterp: de Casteljau with Lerp = A + Alpha * (B - A)."""
    p01 = p0 + a * (p1 - p0)
    p12 = p1 + a * (p2 - p1)
    p23 = p2 + a * (p3 - p2)
    p012 = p01 + a * (p12 - p01)
    p123 = p12 + a * (p23 - p12)
    return p012 + a * (p123 - p012)


def _cycle_time(min_time: float, max_time: float, t: float) -> tuple[float, int]:
    """UE's CycleTime: (time folded into [min, max], number of cycles skipped)."""
    init = t
    duration = max_time - min_time
    count = 0
    if t > max_time:
        count = math.floor((max_time - t) / duration)
        t = t + duration * count
    elif t < min_time:
        count = math.floor((t - min_time) / duration)
        t = t - duration * count
    if t == max_time and init < min_time:
        t = min_time
    if t == min_time and init > max_time:
        t = max_time
    return t, abs(count)


class RichCurve:
    """A float curve.  ``default_value`` is returned for a curve without keys (None: the caller's default, 0)."""

    def __init__(self, keys: list[Key] | None = None, pre: str = "constant", post: str = "constant", default_value: float | None = None,
                 name: str = ""):
        self.keys = sorted(keys or [], key=lambda k: k.time)
        self.pre = pre
        self.post = post
        self.default_value = default_value
        self.name = name

    def __repr__(self):
        return f"RichCurve({self.name or len(self.keys)} keys)"

    def _remap(self, t: float) -> tuple[float, float]:
        """(time, value offset) after the extrapolation modes that fold the time (cycle, cycle with offset, oscillate)."""
        keys = self.keys
        n = len(keys)
        if n < 2:
            return t, 0.0
        offset = 0.0
        if t <= keys[0].time:
            if self.pre not in ("linear", "constant"):
                lo, hi = keys[0].time, keys[-1].time
                t2, count = _cycle_time(lo, hi, t)
                if self.pre == "cycle_offset":
                    offset = (keys[0].value - keys[-1].value) * count
                elif self.pre == "oscillate" and count % 2 == 1:
                    t2 = lo + (hi - t2)
                t = t2
        elif t >= keys[-1].time:
            if self.post not in ("linear", "constant"):
                lo, hi = keys[0].time, keys[-1].time
                t2, count = _cycle_time(lo, hi, t)
                if self.post == "cycle_offset":
                    offset = (keys[-1].value - keys[0].value) * count
                elif self.post == "oscillate" and count % 2 == 1:
                    t2 = lo + (hi - t2)
                t = t2
        return t, offset

    def eval(self, t: float, default: float = 0.0) -> float:
        """FRichCurve::Eval."""
        t = float(t)
        keys = self.keys
        t, offset = self._remap(t)
        n = len(keys)
        value = self.default_value if self.default_value is not None else default
        if n == 0:
            pass
        elif n < 2 or t <= keys[0].time:
            if self.pre == "linear" and n > 1:
                dt = keys[1].time - keys[0].time
                if abs(dt) <= rt.SMALL_NUMBER:
                    value = keys[0].value
                else:
                    slope = (keys[1].value - keys[0].value) / dt
                    value = slope * (t - keys[0].time) + keys[0].value
            else:
                value = keys[0].value
        elif t < keys[-1].time:
            lo, hi = 1, n - 1               # lower bound for the second node of the interval
            count = hi - lo
            first = lo
            while count > 0:
                step = count // 2
                middle = first + step
                if t >= keys[middle].time:
                    first = middle + 1
                    count -= step + 1
                else:
                    count = step
            node = first
            a, b = keys[node - 1], keys[node]
            diff = b.time - a.time
            if diff > 0.0 and a.interp != CONSTANT:
                alpha = (t - a.time) / diff
                if a.interp == LINEAR:
                    value = a.value + alpha * (b.value - a.value)
                else:
                    third = 1.0 / 3.0
                    p1 = a.value + a.leave * diff * third
                    p2 = b.value - b.arrive * diff * third
                    value = _bezier(a.value, p1, p2, b.value, alpha)
            else:
                value = a.value
        else:
            if self.post == "linear":
                dt = keys[-2].time - keys[-1].time
                if abs(dt) <= rt.SMALL_NUMBER:
                    value = keys[-1].value
                else:
                    slope = (keys[-2].value - keys[-1].value) / dt
                    value = slope * (t - keys[-1].time) + keys[-1].value
            else:
                value = keys[-1].value
        return value + offset


class Curve3:
    """A vector curve: three float curves (x, y, z); ``eval`` gives a vec3 (UCurveVector::GetVectorValue)."""

    def __init__(self, x: RichCurve, y: RichCurve, z: RichCurve, name: str = ""):
        self.channels = (x, y, z)
        self.name = name

    def __repr__(self):
        return f"Curve3({self.name})"

    def eval(self, t: float):
        return rt.Vec3(self.channels[0].eval(t), self.channels[1].eval(t), self.channels[2].eval(t))


# ---- JSON ------------------------------------------------------------------------------------------------------

class CurveError(ValueError):
    pass


def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise CurveError(f"{what} must be a number, not {v!r}")
    return float(v)


def _first(d: dict, *names, default=None):
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def auto_tangents(keys: list[Key]) -> None:
    """A fallback for keys that carry no tangents: Catmull-Rom style slopes (end keys flat)."""
    for i, k in enumerate(keys):
        if k.interp != CUBIC or k.tangent_mode != "auto":
            continue
        if 0 < i < len(keys) - 1:
            span = max(_KINDA_SMALL, keys[i + 1].time - keys[i - 1].time)
            k.arrive = k.leave = (keys[i + 1].value - keys[i - 1].value) / span
        else:
            k.arrive = k.leave = 0.0


def channel_from_json(obj: dict, name: str = "") -> RichCurve:
    """One channel: ``{"keys": [...], "pre": ..., "post": ..., "default": ...}`` (the export) or the same with
    ``pre_infinity`` / ``post_infinity`` / ``default_value`` and keys named time / value / arrive_tangent / leave_tangent (the spec)."""
    keys: list[Key] = []
    needs_auto = False
    for i, k in enumerate(obj.get("keys", [])):
        if not isinstance(k, dict):
            raise CurveError(f"{name}: key {i} must be an object")
        interp = str(_first(k, "interp", "interp_mode", default="linear")).lower()
        interp = _INTERP_ALIASES.get(interp, interp)
        if interp not in (CONSTANT, LINEAR, CUBIC):
            raise CurveError(f"{name}: key {i} has the unknown interpolation {interp!r}")
        arrive = _first(k, "arrive", "arrive_tangent")
        leave = _first(k, "leave", "leave_tangent")
        mode = str(_first(k, "tangent", "tangent_mode", default="auto")).lower()
        if interp == CUBIC and arrive is None and leave is None:
            needs_auto = True
        keys.append(Key(_num(_first(k, "t", "time"), f"{name}: key {i} time"), _num(_first(k, "v", "value"), f"{name}: key {i} value"),
                        interp, 0.0 if arrive is None else _num(arrive, "arrive tangent"), 0.0 if leave is None else _num(leave, "leave tangent"), mode))
    pre = str(_first(obj, "pre", "pre_infinity", default="constant")).lower()
    post = str(_first(obj, "post", "post_infinity", default="constant")).lower()
    pre, post = _EXTRAP_ALIASES.get(pre, pre), _EXTRAP_ALIASES.get(post, post)
    for which, mode in (("pre", pre), ("post", post)):
        if mode not in EXTRAP:
            raise CurveError(f"{name}: unknown extrapolation {mode!r} ({which})")
    dv = _first(obj, "default", "default_value")
    c = RichCurve(keys, pre, post, None if dv is None else _num(dv, "default value"), name)
    if needs_auto:
        auto_tangents(c.keys)
    return c


def curve_from_json(obj: dict, name: str = ""):
    """A whole curve object: ``{"class": "CurveFloat" | "CurveVector", "channels": [...]}`` (also ``kind`` float / vector).
    Returns a ``RichCurve`` or a ``Curve3``."""
    name = name or str(obj.get("name", ""))
    channels = obj.get("channels")
    if channels is None and "keys" in obj:
        channels = [obj]
    if not isinstance(channels, list) or not channels:
        raise CurveError(f"{name}: a curve needs a non-empty 'channels' list")
    kind = str(_first(obj, "class", "kind", default="")).lower()
    is_vector = kind in ("curvevector", "vector") or (not kind and len(channels) == 3)
    if is_vector:
        if len(channels) != 3:
            raise CurveError(f"{name}: a vector curve has 3 channels, not {len(channels)}")
        return Curve3(*(channel_from_json(c, f"{name}.{'xyz'[i]}") for i, c in enumerate(channels)), name=name)
    if len(channels) != 1:
        raise CurveError(f"{name}: a float curve has 1 channel, not {len(channels)}")
    return channel_from_json(channels[0], name)
