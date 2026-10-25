"""ALS's character logic and its example input rewritten in DAL: ``als/AlsCharacter.dal``, ``als/AlsInput.dal`` and the library
``als/als_lib.dal``.

* the files follow the C++: every function cites the ALS line it ports, the state structs and settings types have the members and the
  defaults of the C++ headers (read from the ALS sources when they are on the machine), the exported ALS data loads without a warning;
* the interpreter, the generated Python and the generated C++ (MSVC ``/W4 /WX`` against the stand-in engine) agree on random frames;
* the tricky parts behave like the C++ they port: gait selection with its speed thresholds and curves, the rotation modes and the
  interpolation, the view yaw and the aiming constraint, stance changes and the commands they cause, desired against actual states,
  landing, jumping, and the mapping of the input actions.

The animation instance is a stand-in here (``ANIM_STUB``): the logics under test only call ``Jump()`` on it.  The host commands that
``dal/hostapi.py`` does not know yet are registered by ``als_host_v2`` (see there).
"""

from __future__ import annotations

import math
import os
import random
import re
import unittest

import _bootstrap  # noqa: F401
from _bootstrap import ROOT

import als_host_v2
import cpp_driver
import dal
import test_cpp_compile as tcc
from dal import harness, hostapi
from dal import runtime as rt
from dal import settings as dsettings
from dal.interp import Interpreter
from dal.pybackend import load_class
from dal.types import TRANSFORM



def setUpModule():
    als_host_v2.install()                      # the commands the compiler does not list yet, for the tests of this module only


def tearDownModule():
    als_host_v2.uninstall()
    _MODULES.clear()


ALS_DIR = os.path.join(ROOT, "als")
PACK = os.environ.get("DAL_ALS_PACK", r"G:\DCC\AlsPack")
HAVE_PACK = os.path.isfile(os.path.join(PACK, "pack.json"))
ALS_SOURCE = os.environ.get("DAL_ALS_SOURCE", r"I:\UnrealProject_Moon\DEV_58\DevTest\Plugins\ALS-Refactored\Source\ALS")
HAVE_SOURCE = os.path.isdir(os.path.join(ALS_SOURCE, "Public", "State"))

DT = 1.0 / 60.0

ANIM_STUB = """
// stand-in for als/AlsAnimationInstance.dal: the character only calls Jump() and SnapshotFinalRagdollPose() on it
logic AlsAnimationInstance;
state Jumps: int;
state Snapshots: int;
pub method Jump() { Jumps += 1; }
pub method SnapshotFinalRagdollPose() { Snapshots += 1; }
update(dt) { Jumps += 0; }
"""


def read(name: str) -> str:
    with open(os.path.join(ALS_DIR, name), encoding="utf-8") as f:
        return f.read()


def source_files(real_anim: bool = False) -> dict[str, str]:
    files = {n: read(n) for n in ("als_lib.dal", "AlsCharacter.dal", "AlsInput.dal")}
    files["AlsAnimationInstance.dal"] = read("AlsAnimationInstance.dal") if real_anim else ANIM_STUB
    return files


_MODULES: dict = {}


def modules(real_anim: bool = False) -> dict:
    """The three logics of the system, checked from the files of the folder (the animation instance is a stand-in)."""
    if real_anim not in _MODULES:
        files = source_files(real_anim)
        _MODULES[real_anim] = {n: dal.check_source(files[n + ".dal"], n + ".dal", files=files).module
                               for n in ("AlsInput", "AlsCharacter", "AlsAnimationInstance")}
    return _MODULES[real_anim]


# ---- data: settings written by hand, so the behaviour tests do not depend on the exported pack ------------------------------------------

def curve(*points):
    """A polyline of (time, value) points, constant outside."""
    return {"class": "CurveFloat", "channels": [{"keys": [{"t": t, "v": v, "interp": "linear"} for t, v in points], "pre": "constant", "post": "constant"}]}


def curve3(x, y, z):
    return {"class": "CurveVector", "channels": [curve(*c)["channels"][0] for c in (x, y, z)]}


def gait(walk=175.0, run=375.0, sprint=650.0, **extra):
    d = {"bAllowDirectionDependentMovementSpeed": False, "WalkForwardSpeed": walk, "WalkBackwardSpeed": walk, "RunForwardSpeed": run,
         "RunBackwardSpeed": run, "SprintSpeed": sprint,
         "AccelerationAndDecelerationAndGroundFrictionCurve": curve3([(0.0, 1000.0), (3.0, 2500.0)], [(0.0, 400.0), (3.0, 1600.0)], [(0.0, 8.0), (3.0, 2.0)]),
         "RotationInterpolationSpeedCurve": curve((0.0, 0.2))}
    d.update(extra)
    return d


def movement_settings(standing=None, crouching=None, **extra):
    standing = standing or gait()
    crouching = crouching or gait(150.0, 200.0, 300.0)
    modes = {f"Als.RotationMode.{m}": {"Stances": {"Als.Stance.Standing": standing, "Als.Stance.Crouching": crouching}}
             for m in ("VelocityDirection", "ViewDirection", "Aiming")}
    d = {"VelocityAngleToSpeedInterpolationRange": {"Min": 100.0, "Max": 125.0}, "RotationModes": modes}
    d.update(extra)
    return d


# the defaults of UAlsCharacterSettings, written out (AlsCharacterSettings.h:14-60)
CHARACTER_SETTINGS = {"TeleportDistanceThreshold": 50.0, "MovingSpeedThreshold": 50.0, "InAirRotationMode": "RotateToVelocity",
                      "bAllowAimingWhenInAir": True, "bSprintHasPriorityOverAiming": False, "bRotateToVelocityWhenSprinting": False,
                      "bRotateTowardsDesiredVelocityInVelocityDirectionRotationMode": True,
                      "bAutoRotateOnAnyInputWhileNotMovingInViewDirectionRotationMode": True, "AimingYawAngleLimit": 70.0}

STAND, CROUCH = "Als.Stance.Standing", "Als.Stance.Crouching"
WALKING, RUNNING, SPRINTING = "Als.Gait.Walking", "Als.Gait.Running", "Als.Gait.Sprinting"
GROUNDED, IN_AIR = "Als.LocomotionMode.Grounded", "Als.LocomotionMode.InAir"
VIEW_DIRECTION, VELOCITY_DIRECTION, AIMING = "Als.RotationMode.ViewDirection", "Als.RotationMode.VelocityDirection", "Als.RotationMode.Aiming"
FIRST_PERSON, THIRD_PERSON = "Als.ViewMode.FirstPerson", "Als.ViewMode.ThirdPerson"
WALKING_MODE, FALLING_MODE = 1, 3                                  # MovementMode.Walking, MovementMode.Falling


def yaw_quat(deg):
    r = math.radians(deg) / 2.0
    return (0.0, 0.0, math.sin(r), math.cos(r))


def unwind(a):
    while a > 180.0:
        a -= 360.0
    while a < -180.0:
        a += 360.0
    return a


def damper_alpha(dt, half_life):
    return 1.0 - rt.inv_exp_approx(math.log(2.0) / (half_life + 1e-8) * dt)


def damper_angle(current, target, dt, half_life):
    """UAlsRotation::DamperExactAngle (AlsRotation.h:144), written out for the expectations."""
    delta = unwind(target - current)
    if abs(delta) <= 1e-4:
        return target
    if delta > 175.0:
        delta -= 360.0
    return unwind(current + delta * damper_alpha(dt, half_life))


def interpolate_constant(current, target, dt, speed):
    """UAlsRotation::InterpolateAngleConstant (AlsRotation.h:130)."""
    delta = unwind(target - current)
    max_delta = speed * dt
    if speed <= 0.0 or abs(delta) <= max_delta:
        return target
    if delta > 175.0:
        delta -= 360.0
    return unwind(current + math.copysign(1.0, delta) * max_delta)


# ---- a rig: the three logics wired like the engine, driven one frame at a time ----------------------------------------------------------

class Rig:
    """AlsInput + AlsCharacter + the animation stand-in on one actor, with scripted host values that persist from frame to frame."""

    def __init__(self, backend="python", settings=None, movement=None, resolver=None, character_params=None, host_class=None, **host):
        self.mods = modules()
        self.hosts = {n: (host_class or harness.ScriptedHost)() for n in self.mods}
        params = {"AlsCharacter": {"Settings": CHARACTER_SETTINGS if settings is None else settings,
                                   "MovementSettings": movement_settings() if movement is None else movement}}
        if resolver is not None:
            params["AlsCharacter"]["resolver"] = resolver
        params["AlsCharacter"].update(character_params or {})
        self.objs = {}
        for n, m in self.mods.items():
            if backend == "interp":
                self.objs[n] = Interpreter(m, self.hosts[n], **params.get(n, {}))
            else:
                self.objs[n] = load_class(m)(self.hosts[n], **params.get(n, {}))
        for n, m in self.mods.items():
            for p in m.peers:
                self.objs[n].connect(p.name, self.objs[p.logic])
        self.schedule = harness.default_schedule(self.mods)
        self.host = {"dt": DT, "velocity": (0.0, 0.0, 0.0), "acceleration": (0.0, 0.0, 0.0), "max_acceleration": 2000.0,
                     "max_braking_deceleration": 1500.0, "max_walk_speed": 375.0, "movement_mode": WALKING_MODE, "is_grounded": True,
                     "is_crouching": False, "actor_location": (0.0, 0.0, 0.0), "actor_rotation": yaw_quat(0.0), "control_rotation": (0.0, 0.0, 0.0),
                     "has_root_motion": False, "actor_ready": True, "pre_penetration_velocity": (0.0, 0.0, 0.0), "pre_penetration_velocity_valid": False,
                     "capsule_radius": 30.0, "capsule_half_height": 90.0, "actor_scale": 1.0, "ragdoll_supported": False}
        self.host.update(host)
        self.last: dict = {}
        self.trace: list = []
        self.cmds: dict = {}
        for n in sorted(self.mods, key=lambda k: self.mods[k].kind != "actor"):         # the engine begins play before the first tick
            begin = getattr(self.objs[n], "begin_play", None)
            if begin is not None:
                self.hosts[n].load(self._frame())
                begin()
                self.hosts[n].calls.clear()

    def _frame(self, events=(), curves=None):
        f = dict(self.host)
        f["events"] = list(events)
        f["curves"] = dict(curves or {})
        return f

    @property
    def c(self):
        return self.objs["AlsCharacter"]

    @property
    def s(self) -> dict:
        """The state of the character as the flat snapshot (struct members as ``LocomotionState.Speed``, tags as full names)."""
        return self.c.snapshot()

    @property
    def inp(self):
        return self.objs["AlsInput"]

    @property
    def anim(self):
        return self.objs["AlsAnimationInstance"]

    def step(self, events=(), curves=None, dt=None, hooks=(), auto_hooks=True, between=None, **host):
        """One frame: ``host`` values replace the scripted ones from now on, ``events`` reach AlsInput (``(action, phase, value)``).

        ``hooks`` are the events the movement component raises while it moves the character, between the pre_movement and the after_movement
        slot (``("jumped",)``, ``("landed", hit)``, ``("notify_begin", name, payload, instance)`` ...); with ``auto_hooks`` a change of the host
        values ``movement_mode`` / ``is_crouching`` raises ``movement_mode_changed`` / ``crouch_started`` / ``crouch_ended`` by itself.

        ``between(rig)`` stands in for the movement component: it is called when the slots before the movement are done (``rig.blocks`` has the
        commands of each block so far) and returns ``(host values the later slots read, hooks)``."""
        if dt is not None:
            host["dt"] = dt
        previous = (self.host.get("movement_mode"), self.host.get("is_crouching"))
        self.host.update(host)
        raised = list(hooks)
        if auto_hooks:
            if self.host.get("movement_mode") != previous[0]:
                raised.append(("movement_mode_changed", previous[0], self.host.get("movement_mode")))
            if self.host.get("is_crouching") != previous[1]:
                raised.append(("crouch_started",) if self.host.get("is_crouching") else ("crouch_ended",))
        f = self._frame([("input", a, ph, v) for a, ph, v in events], curves)
        for n in self.mods:
            self.hosts[n].load(f)
        armed = False
        self.trace = []
        self.blocks = []
        for n, slot in self.schedule:
            if slot == "after_movement" and not armed:
                armed = True
                if between is not None:                   # the movement component runs: what it did is what the later slots read
                    updates, extra = between(self)
                    self.host.update(updates)
                    raised.extend(extra)
                    f = self._frame([], curves)
                    for h in self.hosts.values():
                        h.load(f)
                for h in self.hosts.values():             # the movement component records the pre-penetration velocity between the two slots:
                    h.pre_penetration_velocity = f.get("pre_penetration_velocity", rt.VEC3_ZERO)      # the reads of the blocks before it consumed the
                    h.pre_penetration_velocity_valid = f.get("pre_penetration_velocity_valid", False)  # scripted one, so arm it again
                for h in self.hosts.values():             # and the hooks it raised while it moved reach every logic that listens
                    h.events.extend(tuple(e) for e in raised)
            before = len(self.hosts[n].calls)
            if slot is None:
                self.objs[n].update(f["dt"])
            else:
                self.objs[n].update_phase(slot, f["dt"])
            self.trace.append((n, slot, [c[0] for c in self.hosts[n].calls[before:]]))        # which block handed which commands to the host
            self.blocks.append((n, slot, list(self.hosts[n].calls[before:])))
        self.cmds = {n: list(h.calls) for n, h in self.hosts.items()}
        for h in self.hosts.values():
            h.calls.clear()
        return self

    def call(self, method, *args, logic="AlsCharacter"):
        """A ``pub method`` called from outside (what a Blueprint or another logic does)."""
        return self.objs[logic].dal_call(method, *args)

    def run(self, frames, **host):
        for _ in range(frames):
            self.step(**host)
        return self

    # -- what the last frame did
    def commands(self, name, logic="AlsCharacter"):
        return [args for n, args in self.cmds.get(logic, []) if n == name]

    def command(self, name, logic="AlsCharacter"):
        found = self.commands(name, logic)
        if len(found) != 1:
            raise AssertionError(f"{name}: {len(found)} commands in the frame {[n for n, _ in self.cmds.get(logic, [])]}")
        return found[0]

    def names(self, logic="AlsCharacter"):
        return [n for n, _ in self.cmds.get(logic, [])]


def accel(yaw_deg, amount=2000.0):
    r = math.radians(yaw_deg)
    return (math.cos(r) * amount, math.sin(r) * amount, 0.0)


def velocity(speed, yaw_deg=0.0):
    r = math.radians(yaw_deg)
    return (math.cos(r) * speed, math.sin(r) * speed, 0.0)


class Case(unittest.TestCase):
    def assertClose(self, a, b, tol=1e-4, msg=None):
        if isinstance(b, (tuple, list)):
            self.assertEqual(len(a), len(b), msg)
            for x, y in zip(a, b):
                self.assertAlmostEqual(x, y, delta=tol * max(1.0, abs(y)), msg=msg)
        else:
            self.assertAlmostEqual(a, b, delta=tol * max(1.0, abs(b)), msg=msg)


# ---- the files follow the C++ ---------------------------------------------------------------------------------------------------------

CITATION = re.compile(r"(\w+\.(?:h|cpp):\d+|\b(?:C|CA|CMC|EX|AI|RMS):\d+|//\s*:\d+)")
DECLARATION = re.compile(r"^(?:pub\s+)?(?:method|view)\s+(\w+)|^fn\s+(\w+)|^update\(dt\)|^on begin_play|^on timer\s+(\w+)|^on input\s+(\w+)"
                         r"|^on (?:movement_mode_changed|jumped|crouch_started|crouch_ended|physics_rotation|notify\w*)|^notify(?:_state)?\s+(\w+)")


class FilesTests(Case):
    def test_every_function_cites_the_als_line_it_ports(self):
        missing = []
        for name in ("als_lib.dal", "AlsCharacter.dal", "AlsInput.dal"):
            lines = read(name).split("\n")
            for i, line in enumerate(lines):
                if not DECLARATION.match(line):
                    continue
                above = []
                k = i - 1
                while k >= 0 and lines[k].lstrip().startswith("//"):
                    above.append(lines[k])
                    k -= 1
                # a helper of the animation instance lives in its own marked section of the library
                if not CITATION.search(line) and not any(CITATION.search(t) for t in above):
                    missing.append(f"{name}:{i + 1}: {line.strip()[:70]}")
        self.assertEqual(missing, [])

    def test_sizes(self):
        for name, low in (("als_lib.dal", 500), ("AlsCharacter.dal", 700), ("AlsInput.dal", 120)):
            self.assertGreaterEqual(len(read(name).split("\n")), low, name)

    def test_the_three_logics_check_and_have_the_expected_shape(self):
        m = modules()
        char, inp = m["AlsCharacter"], m["AlsInput"]
        self.assertEqual((char.kind, inp.kind), ("actor", "actor"))
        self.assertEqual([u.phase for u in char.updates], ["pre_movement", "after_movement"])
        self.assertEqual(harness.phases_of(inp), ["pre_movement"])
        self.assertEqual([(p.name, p.logic) for p in char.peers], [("Anim", "AlsAnimationInstance")])
        self.assertEqual([(p.name, p.logic) for p in inp.peers], [("Character", "AlsCharacter")])
        self.assertTrue(char.slot_events)                                                           # the hooks of the movement component reach the Tick of the same tick
        self.assertEqual([(o.relation, o.peer, o.slots) for o in inp.order], [("before", "Character", ("pre_movement",))])      # its calls are flushed by the character's block
        self.assertEqual(sorted(h.event for h in char.handlers if h.event in ("movement_mode_changed", "jumped", "crouch_started", "crouch_ended", "physics_rotation")),
                         ["crouch_ended", "crouch_started", "jumped", "movement_mode_changed", "physics_rotation"])
        self.assertEqual(sorted(n.name for n in char.notifies),
                         ["AlsAnimNotifyState_EarlyBlendOut", "AlsAnimNotifyState_SetLocomotionAction", "AlsAnimNotifyState_SetRootMotionScale"])
        pub = sorted(x.name for x in char.methods if x.pub)
        for name in ("Jump", "SetDesiredGait", "SetDesiredStance", "SetDesiredAiming", "SetDesiredRotationMode", "SetViewMode", "SetOverlayMode",
                     "SetLocomotionAction", "StartMantling", "StartRagdolling", "StopRagdolling", "StartRolling", "StartRollingGrounded"):
            self.assertIn(name, pub)
        # the names the animation instance reads (Character.<name>) are ALS's
        for name in ("ViewMode", "LocomotionMode", "RotationMode", "Stance", "Gait", "OverlayMode", "LocomotionAction", "LocomotionState",
                     "ViewState", "RagdollingState", "DesiredGait", "DesiredStance", "DesiredRotationMode", "InputDirection", "MantlingState", "RollingState"):
            self.assertIsNotNone(next((s for s in char.states if s.name == name), None), name)

    def test_the_commands_the_logics_issue(self):
        text = read("AlsCharacter.dal") + read("AlsInput.dal")
        used = set(re.findall(r"host\.(\w+)\(", text))
        self.assertEqual(sorted(used - set(hostapi.COMMANDS) - set(hostapi.HOST_ITEMS) - set(hostapi.QUERIES)), [])
        self.assertTrue({"set_actor_rotation", "set_ground_friction", "add_controller_yaw_input", "set_max_acceleration_walking"} <= used)


# the members of the C++ structs, read from the ALS headers ----------------------------------------------------------------------------

def parse_header_structs(path):
    """{struct name: [(member, declared default text or None)]} of the UPROPERTY members of every struct / class of a header."""
    with open(path, encoding="utf-8-sig") as f:
        lines = f.read().split("\n")
    out, current, i = {}, None, 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(?:struct|class)\s+(?:\w+_API\s+)?([FU]\w+)", line)
        if m and not line.rstrip().endswith(";"):
            current = m.group(1)
            out[current] = []
        elif current and line.strip().startswith("UPROPERTY"):
            depth = line.count("(") - line.count(")")
            while depth > 0:                                  # a multi-line specifier list
                i += 1
                depth += lines[i].count("(") - lines[i].count(")")
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i].strip().startswith("///")):
                i += 1
            decl = lines[i].strip()
            d = re.match(r"^[\w:<>,\*& ]+?\s+(\w+)\s*(?::\s*1\s*)?(?:\{|;|$)", decl)
            if d:
                init = re.search(r"\{(.*)\}\s*;", decl)
                out[current].append((d.group(1), init.group(1) if init else None))
        i += 1
    return out


def default_value(text):
    """A C++ member initializer as a Python value (floats, bools, pairs); None when it is not a plain literal."""
    if text is None:
        return None
    text = text.strip()
    if text in ("true", "false"):
        return text == "true"
    if re.fullmatch(r"-?\d+(\.\d+)?f?", text):
        return float(text.rstrip("f"))
    pair = re.fullmatch(r"(-?[\d.]+)f?\s*,\s*(-?[\d.]+)f?", text)
    if pair:
        return (float(pair.group(1)), float(pair.group(2)))
    return None


@unittest.skipUnless(HAVE_SOURCE, "the ALS sources are not on this machine")
class HeaderContractTests(Case):
    """The library's structs and settings types against the C++ headers: member names, their order and the plain defaults."""

    # members the DAL types do not carry (and why): no networking, no moving bases.  The queued turn in place settings (a copy of the settings value
    # and the slot as an enum) and the final ragdoll pose (the snapshot value type) are in the library since the animation instance plays turns
    # and takes the snapshot.
    SKIPPED = {"AlsViewNetworkSmoothingState": None, "AlsMovementBaseState": {"MovementBaseInterfaceData", "BoneName"},
               "AlsViewState": {"NetworkSmoothing"}}
    RENAMED = {"UAlsTurnInPlaceSettings": "AlsTurnInPlaceSettings", "FAlsGeneralTurnInPlaceSettings": "AlsGeneralTurnInPlaceSettings"}

    @classmethod
    def setUpClass(cls):
        cls.lib = dal.check_file(os.path.join(ALS_DIR, "als_lib.dal")).module
        cls.types = {s.name: s for s in cls.lib.structs}
        cls.cpp = {}
        for sub in ("State", "Settings"):
            for fn in sorted(os.listdir(os.path.join(ALS_SOURCE, "Public", sub))):
                if fn.endswith(".h"):
                    cls.cpp.update(parse_header_structs(os.path.join(ALS_SOURCE, "Public", sub, fn)))

    def dal_name(self, cpp_name):
        return self.RENAMED.get(cpp_name, cpp_name[1:])

    def test_every_state_struct_and_settings_type_has_the_members_of_the_header_in_order(self):
        checked, problems = 0, []
        for cpp_name, members in self.cpp.items():
            name = self.dal_name(cpp_name)
            if name in ("AlsViewNetworkSmoothingState", "AlsFootUpdateContext", "AlsMovementBaseState") or name not in self.types:
                continue
            skip = self.SKIPPED.get(name) or set()
            want = [m for m, _d in members if m not in skip]
            have = [f.name for f in self.types[name].fields]
            if want != have:
                problems.append(f"{name}: header {want} / library {have}")
            checked += 1
        self.assertEqual(problems, [])
        self.assertGreaterEqual(checked, 40)

    def test_the_plain_defaults_are_the_headers(self):
        problems, compared = [], 0
        for cpp_name, members in self.cpp.items():
            name = self.dal_name(cpp_name)
            if name not in self.types:
                continue
            fields = {f.name: f for f in self.types[name].fields}
            for member, text in members:
                want = default_value(text)
                if want is None or member not in fields or fields[member].default is None:
                    continue
                have = fields[member].default
                value = have.value if hasattr(have, "value") else have
                if hasattr(value, "Min") and hasattr(value, "Max"):
                    value = (value.Min, value.Max)                # an FFloatInterval
                value = tuple(value) if isinstance(value, (list, tuple)) else value
                if isinstance(want, tuple):
                    ok = isinstance(value, tuple) and len(value) == len(want) and all(abs(a - b) < 1e-6 for a, b in zip(value, want))
                elif isinstance(want, bool):
                    ok = bool(value) == want
                else:
                    ok = isinstance(value, (int, float)) and abs(value - want) < 1e-6
                compared += 1
                if not ok:
                    problems.append(f"{name}.{member}: header {want!r}, library {value!r}")
        self.assertEqual(problems, [])
        self.assertGreater(compared, 80)

    def test_the_tags_are_the_gameplay_tags_of_the_plugin(self):
        with open(os.path.join(ALS_SOURCE, "Private", "Utility", "AlsGameplayTags.cpp"), encoding="utf-8-sig") as f:
            cpp = set(re.findall(r'ANSITEXTVIEW\("([\w.]+)"\)', f.read()))
        names = {t.full_name(m) for t in self.lib.tagsets for m in t.members}
        self.assertEqual(names, cpp)


# ---- the exported ALS data loads without a warning ------------------------------------------------------------------------------------

@unittest.skipUnless(HAVE_PACK, "the ALS pack is not on this machine")
class PackTests(Case):
    def test_every_settings_file_of_the_pack_loads_into_the_library_without_a_warning(self):
        import json

        ns = load_class(dal.check_file(os.path.join(ALS_DIR, "als_lib.dal")).module)
        resolver = dsettings.PackResolver(PACK)
        loaded = {}
        for fn in sorted(os.listdir(os.path.join(PACK, "data"))):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(PACK, "data", fn), encoding="utf-8-sig") as f:
                data = json.load(f)
            cls = str(data.get("class", "")).rsplit(".", 1)[-1] if isinstance(data, dict) else ""
            if cls not in ns:
                continue                                         # input actions / mapping contexts, the camera settings
            rep = dsettings.Report()
            ns[cls].load(os.path.join(PACK, "data", fn), resolver, rep)
            self.assertEqual((rep.warnings, rep.unresolved), ([], []), fn)
            loaded.setdefault(cls, []).append(fn)
        self.assertEqual(sorted(loaded), ["AlsAnimationInstanceSettings", "AlsCharacterSettings", "AlsMantlingSettings", "AlsMovementSettings"])
        self.assertEqual((len(loaded["AlsMovementSettings"]), len(loaded["AlsMantlingSettings"])), (3, 7))

    def test_the_character_logic_runs_on_the_exported_settings(self):
        resolver = dsettings.PackResolver(PACK)
        rig = Rig(settings=os.path.join(PACK, "data", "CS_Als_Default.json"), movement=os.path.join(PACK, "data", "MS_Als_Normal.json"), resolver=resolver)
        rig.step(velocity=velocity(100.0), acceleration=accel(0.0))
        self.assertEqual(rig.s["Gait"], WALKING)
        self.assertEqual(rig.command("set_max_walk_speed"), (375.0,))
        rig.step(velocity=velocity(300.0), acceleration=accel(0.0))
        self.assertEqual(rig.s["Gait"], RUNNING)
        self.assertGreater(rig.command("set_max_acceleration_walking")[0], 0.0)


# ---- behaviour: the tricky parts, against what the C++ does ----------------------------------------------------------------------------------

BACKENDS = ("python", "interp")


def rigs(**kw):
    """A fresh rig for each implementation (generated Python, reference interpreter): the test body runs on both."""
    for backend in BACKENDS:
        yield Rig(backend, **kw)


class GaitTests(Case):
    """AAlsCharacter::RefreshGait, CalculateMaxAllowedGait, CalculateActualGait, CanSprint (C:942-1027) and RefreshGroundedMovementSettings (CMC:936-1020)."""

    def test_the_actual_gait_follows_the_speed_with_ten_cm_per_second_of_margin(self):
        # standing: walk 175, run 375: Walking below 185; with the desired gait Running nothing is faster than Running
        for rig in rigs():
            self.assertEqual(rig.s["Gait"], WALKING)
        for speed, expected in ((0.0, WALKING), (100.0, WALKING), (184.9, WALKING), (185.0, RUNNING), (300.0, RUNNING), (700.0, RUNNING)):
            for rig in rigs():
                rig.step(velocity=velocity(speed), acceleration=accel(0.0))
                self.assertEqual(rig.s["Gait"], expected, speed)

    def test_sprinting_needs_the_speed_and_the_permission(self):
        aligned = accel(0.0)
        for speed, expected in ((384.9, RUNNING), (385.0, SPRINTING), (650.0, SPRINTING)):
            for rig in rigs():
                rig.step(events=[("Sprint", "triggered", True)], velocity=velocity(speed), acceleration=aligned)
                self.assertEqual(rig.s["DesiredGait"], SPRINTING)
                self.assertEqual((rig.s["MaxAllowedGait"], rig.s["Gait"]), (SPRINTING, expected), speed)
        # no input: no sprint (C:1006); input 60 degrees off the view direction: no sprint (C:1020), 49 degrees: sprint
        for accel_yaw, has_input, expected in ((None, False, RUNNING), (60.0, True, RUNNING), (49.0, True, SPRINTING)):
            for rig in rigs():
                rig.step(events=[("Sprint", "triggered", True)], velocity=velocity(700.0),
                         acceleration=(0.0, 0.0, 0.0) if accel_yaw is None else accel(accel_yaw))
                self.assertEqual((rig.s["LocomotionState.bHasInput"], rig.s["Gait"], rig.s["MaxAllowedGait"]),
                                 (has_input, expected, expected), accel_yaw)
        # releasing the key asks for Running again; the actual gait follows the speed down
        for rig in rigs():
            rig.step(events=[("Sprint", "triggered", True)], velocity=velocity(700.0), acceleration=aligned)
            rig.step(events=[("Sprint", "triggered", False)], velocity=velocity(700.0), acceleration=aligned)
            self.assertEqual((rig.s["DesiredGait"], rig.s["Gait"]), (RUNNING, RUNNING))

    def test_sprinting_is_for_the_standing_character_and_the_sprint_priority_settings(self):
        for rig in rigs():
            rig.step(events=[("Sprint", "triggered", True)], velocity=velocity(700.0), acceleration=accel(0.0))
            rig.step(velocity=velocity(700.0), acceleration=accel(0.0), is_crouching=True)
            self.assertEqual((rig.s["Stance"], rig.s["MaxAllowedGait"]), (CROUCH, RUNNING))          # C:1006 Stance != Standing
        # bRotateToVelocityWhenSprinting: in third person the input direction does not matter (C:1012-1016) ...
        flags = dict(CHARACTER_SETTINGS, bRotateToVelocityWhenSprinting=True)
        for rig in rigs(settings=flags):
            rig.step(events=[("Sprint", "triggered", True)], velocity=velocity(700.0), acceleration=accel(120.0))
            self.assertEqual(rig.s["Gait"], SPRINTING)
        # ... but in first person it does
        for rig in rigs(settings=flags):
            rig.call("SetViewMode", FIRST_PERSON)
            rig.step(events=[("Sprint", "triggered", True)], velocity=velocity(700.0), acceleration=accel(120.0))
            self.assertEqual(rig.s["Gait"], RUNNING)
        # aiming takes the sprint away unless sprinting has priority (C:1007-1010)
        for priority, expected in ((False, RUNNING), (True, SPRINTING)):
            for rig in rigs(settings=dict(CHARACTER_SETTINGS, bSprintHasPriorityOverAiming=priority)):
                rig.step(events=[("Aim", "triggered", True), ("Sprint", "triggered", True)], velocity=velocity(700.0), acceleration=accel(0.0))
                self.assertEqual(rig.s["Gait"], expected, priority)

    def test_the_movement_component_gets_the_speed_of_the_max_allowed_gait(self):
        for rig in rigs():
            rig.step(velocity=velocity(100.0), acceleration=accel(0.0))
            self.assertEqual((rig.command("set_max_walk_speed"), rig.command("set_max_walk_speed_crouched")), ((375.0,), (375.0,)))     # Running
            rig.step(events=[("Walk", "triggered", True)], velocity=velocity(100.0), acceleration=accel(0.0))
            self.assertEqual((rig.s["DesiredGait"], rig.command("set_max_walk_speed")), (WALKING, (175.0,)))
            rig.step(events=[("Walk", "triggered", True)], velocity=velocity(100.0), acceleration=accel(0.0))
            self.assertEqual((rig.s["DesiredGait"], rig.command("set_max_walk_speed")), (RUNNING, (375.0,)))
            rig.step(events=[("Sprint", "triggered", True)], velocity=velocity(100.0), acceleration=accel(0.0))
            self.assertEqual(rig.command("set_max_walk_speed"), (650.0,))
            rig.step(events=[("Sprint", "triggered", False)], velocity=velocity(100.0), acceleration=accel(0.0), is_crouching=True)
            self.assertEqual(rig.command("set_max_walk_speed"), (200.0,))                          # crouching run speed

    def test_the_gait_amount_maps_the_speed_to_zero_to_three(self):
        for speed, expected in ((0.0, 0.0), (87.5, 0.5), (175.0, 1.0), (275.0, 1.5), (375.0, 2.0), (512.5, 2.5), (650.0, 3.0), (900.0, 3.0)):
            for rig in rigs():
                rig.step(velocity=velocity(speed), acceleration=accel(0.0))
                self.assertClose(rig.s["GaitAmount"], expected, msg=speed)

    def test_acceleration_deceleration_and_friction_come_from_the_curve_of_the_gait_amount(self):
        # the curves of gait(): x 1000 -> 2500, y 400 -> 1600, z 8 -> 2 over the gait amount 0 .. 3
        for rig in rigs():
            rig.step(velocity=velocity(275.0), acceleration=accel(0.0))                     # gait amount 1.5
            self.assertClose(rig.command("set_max_acceleration_walking")[0], 1750.0)
            self.assertClose(rig.command("set_braking_deceleration_walking")[0], 1000.0)
            self.assertClose(rig.command("set_ground_friction")[0], 5.0)

    def test_nothing_is_pushed_in_the_air_and_without_the_curve(self):
        for rig in rigs():
            rig.step(velocity=velocity(275.0), acceleration=accel(0.0), is_grounded=False, movement_mode=FALLING_MODE)
            self.assertEqual([n for n in rig.names() if n.startswith("set_max") or n == "set_ground_friction"], [])
        no_curve = movement_settings(standing=gait(AccelerationAndDecelerationAndGroundFrictionCurve=None))
        for rig in rigs(movement=no_curve):
            rig.step(velocity=velocity(275.0), acceleration=accel(0.0))
            self.assertIn("set_max_walk_speed", rig.names())
            self.assertNotIn("set_max_acceleration_walking", rig.names())                  # ALS_ENSURE(IsValid(curve)): nothing set

    def test_direction_dependent_speeds_interpolate_over_the_view_angle(self):
        directional = gait(175.0, 375.0, 650.0, bAllowDirectionDependentMovementSpeed=True, WalkBackwardSpeed=100.0, RunBackwardSpeed=200.0)
        movement = movement_settings(standing=directional)
        # the angle between the velocity and the view: 0 -> forward speeds, 180 -> backward speeds, in between over 100 .. 125 degrees
        for angle, run in ((0.0, 375.0), (100.0, 375.0), (112.5, 287.5), (125.0, 200.0), (180.0, 200.0)):
            for rig in rigs(movement=movement):
                rig.step(velocity=velocity(100.0, angle), acceleration=accel(angle))
                self.assertClose(rig.command("set_max_walk_speed")[0], run, msg=angle)
        # the actual gait compares with the larger of the two speeds (AlsMovementSettings.h:85)
        for rig in rigs(movement=movement):
            rig.step(velocity=velocity(180.0, 180.0), acceleration=accel(180.0))
            self.assertEqual(rig.s["Gait"], WALKING)                                       # 180 < max(175, 100) + 10: Walking, whatever the direction


def expected_can_sprint(has_input, stance, aiming, desired_mode, view, priority, rotate_to_velocity, input_yaw, view_yaw):
    """AAlsCharacter::CanSprint (C:1000-1027), written out for the expectations."""
    if not has_input or stance != STAND or (aiming and not priority):
        return False
    if view != FIRST_PERSON and (desired_mode == VELOCITY_DIRECTION or rotate_to_velocity):
        return True
    return abs(unwind(input_yaw - view_yaw)) < 50.0


def expected_rotation_mode(view, loco, aiming, desired_mode, sprinting, allow_in_air, priority, rotate_to_velocity):
    """AAlsCharacter::RefreshRotationMode (C:675-758), written out for the expectations."""
    if view == FIRST_PERSON:
        if loco == IN_AIR:
            return AIMING if aiming and allow_in_air else VIEW_DIRECTION
        return AIMING if aiming and (not sprinting or not priority) else VIEW_DIRECTION
    if loco == IN_AIR:
        if aiming and allow_in_air:
            return AIMING
        return VIEW_DIRECTION if aiming else desired_mode
    if sprinting:
        if aiming and not priority:
            return AIMING
        return VELOCITY_DIRECTION if rotate_to_velocity else desired_mode
    return AIMING if aiming else desired_mode


class RotationModeTests(Case):
    def test_the_rotation_mode_table_of_the_c_plus_plus(self):
        checked = 0
        for allow_in_air in (False, True):
            for priority in (False, True):
                for rotate_to_velocity in (False, True):
                    settings = dict(CHARACTER_SETTINGS, bAllowAimingWhenInAir=allow_in_air, bSprintHasPriorityOverAiming=priority,
                                    bRotateToVelocityWhenSprinting=rotate_to_velocity)
                    for rig in rigs(settings=settings):
                        for view in (THIRD_PERSON, FIRST_PERSON):
                            for loco in (GROUNDED, IN_AIR):
                                for aiming_flag in (False, True):
                                    for desired_mode in (VELOCITY_DIRECTION, VIEW_DIRECTION, AIMING):
                                        for desired_gait in (RUNNING, SPRINTING):
                                            rig.call("SetViewMode", view)
                                            rig.call("SetDesiredAiming", aiming_flag)
                                            rig.call("SetDesiredRotationMode", desired_mode)
                                            rig.call("SetDesiredGait", desired_gait)
                                            grounded = loco == GROUNDED
                                            rig.step(movement_mode=WALKING_MODE if grounded else FALLING_MODE, is_grounded=grounded,
                                                     velocity=velocity(400.0), acceleration=accel(0.0))
                                            s = rig.s
                                            aiming = aiming_flag or desired_mode == AIMING
                                            sprinting = (desired_gait == SPRINTING and expected_can_sprint(
                                                True, STAND, aiming, desired_mode, view, priority, rotate_to_velocity, 0.0, 0.0)) if grounded else False
                                            want = expected_rotation_mode(view, loco, aiming, desired_mode, sprinting, allow_in_air, priority, rotate_to_velocity)
                                            self.assertEqual(s["RotationMode"], want, (view, loco, aiming_flag, desired_mode, desired_gait, allow_in_air, priority, rotate_to_velocity))
                                            self.assertEqual(s["LocomotionMode"], loco)
                                            checked += 1
        self.assertEqual(checked, 2 * 8 * 48)

    def test_a_new_rotation_mode_blocks_the_rotation_towards_the_last_input_direction(self):
        for rig in rigs():
            rig.step(velocity=velocity(300.0), acceleration=accel(0.0))                  # moving: the block is lifted (C:1639)
            self.assertFalse(rig.s["LocomotionState.bRotationTowardsLastInputDirectionBlocked"])
            rig.step(events=[("RotationMode", "triggered", True)], velocity=velocity(300.0), acceleration=accel(0.0))
            self.assertEqual(rig.s["RotationMode"], VELOCITY_DIRECTION)                  # the mode change blocks it (C:670), the moving character lifts it again (C:1620)
            self.assertFalse(rig.s["LocomotionState.bRotationTowardsLastInputDirectionBlocked"])
        for rig in rigs():
            rig.step(velocity=velocity(300.0), acceleration=accel(0.0))
            rig.step(events=[("RotationMode", "triggered", True)], velocity=(0.0, 0.0, 0.0), acceleration=(0.0, 0.0, 0.0))   # standing still after the change: stays blocked
            self.assertEqual(rig.s["RotationMode"], VELOCITY_DIRECTION)
            self.assertTrue(rig.s["LocomotionState.bRotationTowardsLastInputDirectionBlocked"])

    def test_the_toggles_of_the_input_flip_the_desired_modes(self):
        for rig in rigs():
            rig.step()
            self.assertEqual((rig.s["DesiredRotationMode"], rig.s["ViewMode"]), (VIEW_DIRECTION, THIRD_PERSON))
            rig.step(events=[("RotationMode", "triggered", True), ("ViewMode", "triggered", True)])
            self.assertEqual((rig.s["DesiredRotationMode"], rig.s["ViewMode"]), (VELOCITY_DIRECTION, FIRST_PERSON))
            rig.step(events=[("RotationMode", "triggered", True), ("ViewMode", "triggered", True)])
            self.assertEqual((rig.s["DesiredRotationMode"], rig.s["ViewMode"]), (VIEW_DIRECTION, THIRD_PERSON))


class ViewAndInputTests(Case):
    def test_the_view_rotation_is_the_normalized_control_rotation(self):
        for rig in rigs(control_rotation=(0.0, 200.0, 0.0)):
            self.assertClose(rig.s["ViewState.Rotation"], (0.0, -160.0, 0.0))             # begin play: C:101 / C:107
            rig.step(control_rotation=(400.0, 10.0, 0.0))
            self.assertClose(rig.s["ViewState.Rotation"], (40.0, 10.0, 0.0))              # GetNormalized()

    def test_the_view_yaw_speed_is_the_absolute_yaw_change_per_second(self):
        for rig in rigs():
            rig.step(control_rotation=(0.0, 10.0, 0.0))
            self.assertClose(rig.s["ViewState.YawSpeed"], 600.0)                          # |10 - 0| / (1/60)
            self.assertClose(rig.s["ViewState.PreviousYawAngle"], 0.0)
            rig.step(control_rotation=(0.0, 5.0, 0.0))
            self.assertClose(rig.s["ViewState.YawSpeed"], 300.0)
            self.assertClose(rig.s["ViewState.PreviousYawAngle"], 10.0)
            rig.step(control_rotation=(0.0, 5.0, 0.0), dt=0.0)                            # no time, no speed update (C:1263)
            self.assertClose(rig.s["ViewState.YawSpeed"], 300.0)

    def test_the_wrap_around_of_the_yaw_is_a_speed_spike_as_in_the_c_plus_plus(self):
        for rig in rigs(control_rotation=(0.0, 179.0, 0.0)):
            rig.step(control_rotation=(0.0, -179.0, 0.0))
            self.assertClose(rig.s["ViewState.YawSpeed"], 358.0 * 60.0)                   # C:1265 does not unwind the difference

    def test_a_view_change_below_the_float_tolerance_is_not_taken(self):
        for rig in rigs():
            rig.step(control_rotation=(0.0, 0.00005, 0.0))                                # FRotator::Equals (C:1132): within 1e-4
            self.assertEqual(rig.s["ViewState.Rotation"], (0.0, 0.0, 0.0))
            self.assertEqual(rig.s["ViewState.YawSpeed"], 0.0)

    def test_the_input_direction_is_the_normalized_acceleration(self):
        for rig in rigs():
            rig.step(acceleration=(0.0, 500.0, 0.0))
            s = rig.s
            self.assertClose(s["InputDirection"], (0.0, 1.0, 0.0))
            self.assertTrue(s["LocomotionState.bHasInput"])
            self.assertClose(s["LocomotionState.InputYawAngle"], 90.0)
            rig.step(acceleration=(0.0, 0.0, 0.0))
            s = rig.s
            self.assertFalse(s["LocomotionState.bHasInput"])
            self.assertClose(s["LocomotionState.InputYawAngle"], 90.0)                    # kept: C:1124 only writes it while there is input
            rig.step(acceleration=(0.001, 0.0, 0.0))                                      # 0.001 / 2000: below GetSafeNormal's tolerance
            self.assertFalse(rig.s["LocomotionState.bHasInput"])
            rig.step(acceleration=(100.0, 0.0, 0.0), max_acceleration=0.0)                # a zero maximum is no input either (the C++ divides to NaN)
            self.assertFalse(rig.s["LocomotionState.bHasInput"])


class LocomotionTests(Case):
    def test_speed_is_horizontal_and_moving_needs_input_and_velocity_or_a_high_speed(self):
        cases = ((velocity(0.5), True, False, False), (velocity(30.0), True, True, True), (velocity(30.0), False, True, False),
                 (velocity(50.0), False, True, False), (velocity(50.1), False, True, True), (velocity(300.0), False, True, True))
        for vel, has_input, has_velocity, moving in cases:
            for rig in rigs():
                v = (vel[0], vel[1], -400.0)                                              # the vertical part is not in the speed
                rig.step(velocity=v, acceleration=accel(0.0) if has_input else (0.0, 0.0, 0.0))
                s = rig.s
                self.assertClose(s["LocomotionState.Speed"], math.hypot(vel[0], vel[1]))
                self.assertEqual((s["LocomotionState.bHasVelocity"], s["LocomotionState.bMoving"]), (has_velocity or math.hypot(*vel[:2]) >= 1.0, moving), vel)

    def test_the_velocity_yaw_is_kept_while_the_character_has_no_velocity(self):
        for rig in rigs():
            rig.step(velocity=velocity(100.0, 90.0))
            self.assertClose(rig.s["LocomotionState.VelocityYawAngle"], 90.0)
            rig.step(velocity=(0.0, 0.0, 0.0))
            self.assertClose(rig.s["LocomotionState.VelocityYawAngle"], 90.0)             # C:1389: only updated with a velocity

    def test_the_desired_velocity_is_the_velocity_before_the_depenetration(self):
        for rig in rigs():
            rig.step(velocity=velocity(100.0, 0.0), pre_penetration_velocity=velocity(200.0, 90.0), pre_penetration_velocity_valid=True)
            s = rig.s
            # the character starts to move: the initial velocity yaw angle is the desired one, for the velocity yaw as well (C:1425, C:1458-1459)
            self.assertClose((s["LocomotionState.VelocityYawAngle"], s["DesiredVelocityYawAngle"]), (90.0, 90.0))
            self.assertTrue(s["bHasDesiredVelocity"])
            rig.step(velocity=velocity(100.0, 0.0), pre_penetration_velocity=velocity(200.0, 90.0), pre_penetration_velocity_valid=True)
            s = rig.s                                                                     # moving already: the velocity yaw is the velocity's, the desired one the recorded
            self.assertClose((s["LocomotionState.VelocityYawAngle"], s["DesiredVelocityYawAngle"]), (0.0, 90.0))
            rig.step(velocity=velocity(100.0, 0.0), pre_penetration_velocity=velocity(200.0, 90.0), pre_penetration_velocity_valid=False)
            self.assertClose(rig.s["DesiredVelocityYawAngle"], 0.0)                       # nothing recorded: the velocity itself (C:1415)
            rig.step(velocity=velocity(100.0, 0.0), pre_penetration_velocity=velocity(0.5, 90.0), pre_penetration_velocity_valid=True)
            self.assertClose(rig.s["DesiredVelocityYawAngle"], 0.0)                       # below the speed threshold: not used

    def test_a_character_that_starts_to_move_may_rotate_towards_the_last_input_direction_again(self):
        # AlsCharacter.cpp:1425-1431 + 1449-1462 run on the authority too: the recorded takes of the UE character show it (a roll starts a
        # velocity without any input and the rotation block is lifted in the same frame)
        for rig in rigs():
            rig.step()
            self.assertTrue(rig.s["LocomotionState.bRotationTowardsLastInputDirectionBlocked"])
            rig.step(velocity=velocity(30.0, 45.0))
            self.assertFalse(rig.s["LocomotionState.bRotationTowardsLastInputDirectionBlocked"])
            self.assertClose(rig.s["LocomotionState.VelocityYawAngle"], 45.0)
            rig.step(velocity=velocity(30.0, 45.0), hooks=[("movement_mode_changed", WALKING_MODE, FALLING_MODE)], movement_mode=FALLING_MODE, auto_hooks=False)
            rig.step(movement_mode=WALKING_MODE, hooks=[("movement_mode_changed", FALLING_MODE, WALKING_MODE)], velocity=(0.0, 0.0, -500.0), auto_hooks=False)
            self.assertTrue(rig.s["LocomotionState.bRotationTowardsLastInputDirectionBlocked"])         # the landing blocks it (C:546-549) ...
            rig.step(velocity=velocity(30.0, 45.0))
            self.assertFalse(rig.s["LocomotionState.bRotationTowardsLastInputDirectionBlocked"])        # ... and moving again lifts it
        off = dict(CHARACTER_SETTINGS, bRotateTowardsDesiredVelocityInVelocityDirectionRotationMode=False)
        for rig in rigs(settings=off):
            rig.step(velocity=velocity(100.0, 0.0), pre_penetration_velocity=velocity(200.0, 90.0), pre_penetration_velocity_valid=True)
            self.assertClose(rig.s["DesiredVelocityYawAngle"], 0.0)                       # the setting is off: C:1399


class RotationTests(Case):
    """AAlsCharacter::RefreshGroundedRotation, RefreshInAirRotation, ConstrainAimingRotation, SetRotation* (C:1514-1956)."""

    def moving_view_step(self, rig, view_yaw=90.0, **kw):
        rig.step(velocity=velocity(300.0), acceleration=accel(0.0))                              # moving along the actor, view and actor aligned
        rig.step(control_rotation=(0.0, view_yaw, 0.0), velocity=velocity(300.0), acceleration=accel(0.0), **kw)

    def test_moving_in_view_direction_rotates_towards_the_view_yaw_with_the_extra_smooth_target(self):
        for rig in rigs():
            self.moving_view_step(rig)
            s = rig.s
            # the view yaw jumped 90 degrees in one frame: the half life shrinks to a third (C:1805-1809, the curve is 0.2 everywhere)
            half_life = 0.2 * 0.333333
            smooth = interpolate_constant(0.0, 90.0, DT, 500.0)                                   # 500 degrees per second
            self.assertClose(s["LocomotionState.TargetYawAngle"], 90.0)
            self.assertClose(s["LocomotionState.SmoothTargetYawAngle"], smooth)
            self.assertClose(s["LocomotionState.TargetYawAngleViewSpace"], 0.0)
            pitch, yaw, roll = rig.command("set_actor_rotation")[0]
            self.assertClose(yaw, damper_angle(0.0, smooth, DT, half_life), 1e-3)
            self.assertEqual(rig.command("set_actor_rotation")[1], False)
            self.assertFalse(s["LocomotionState.bRotationTowardsLastInputDirectionBlocked"])

    def test_the_rotation_yaw_offset_curve_of_the_animation_is_added_to_the_view_yaw(self):
        for rig in rigs():
            rig.step(velocity=velocity(300.0), acceleration=accel(0.0))
            rig.step(velocity=velocity(300.0), acceleration=accel(0.0), curves={"RotationYawOffset": 20.0})
            self.assertClose(rig.s["LocomotionState.TargetYawAngle"], 20.0)                      # C:1649-1650
        for rig in rigs():                                                                       # not when sprinting: the velocity yaw (C:1643-1645)
            rig.step(events=[("Sprint", "triggered", True)], velocity=velocity(500.0, 30.0), acceleration=accel(30.0), curves={"RotationYawOffset": 20.0})
            self.assertEqual(rig.s["Gait"], SPRINTING)
            self.assertClose(rig.s["LocomotionState.TargetYawAngle"], 30.0)

    def test_velocity_direction_rotates_towards_the_velocity_the_character_wanted(self):
        for rig in rigs():
            rig.step(events=[("RotationMode", "triggered", True)], velocity=velocity(300.0, 0.0), acceleration=accel(0.0))
            self.assertEqual(rig.s["RotationMode"], VELOCITY_DIRECTION)
            rig.step(velocity=velocity(300.0, 0.0), acceleration=accel(0.0), pre_penetration_velocity=velocity(300.0, 90.0), pre_penetration_velocity_valid=True)
            self.assertClose(rig.s["LocomotionState.TargetYawAngle"], 90.0)                      # DesiredVelocityYawAngle, not VelocityYawAngle (C:1622-1626)
            self.assertClose(rig.s["LocomotionState.VelocityYawAngle"], 0.0)
        off = dict(CHARACTER_SETTINGS, bRotateTowardsDesiredVelocityInVelocityDirectionRotationMode=False)
        for rig in rigs(settings=off):
            rig.step(events=[("RotationMode", "triggered", True)], velocity=velocity(300.0, 0.0), acceleration=accel(0.0))
            rig.step(velocity=velocity(300.0, 0.0), acceleration=accel(0.0), pre_penetration_velocity=velocity(300.0, 90.0), pre_penetration_velocity_valid=True)
            self.assertClose(rig.s["LocomotionState.TargetYawAngle"], 0.0)

    def test_standing_still_keeps_the_rotation_until_there_is_something_to_rotate_to(self):
        for rig in rigs():
            rig.step(control_rotation=(0.0, 90.0, 0.0))                                           # view direction, no input, blocked: nothing is commanded (C:1579-1583)
            self.assertNotIn("set_actor_rotation", rig.names())
            self.assertClose(rig.s["LocomotionState.TargetYawAngle"], 0.0)
        for rig in rigs():                                                                        # input but no velocity yet: turn to the view (C:1588-1596)
            rig.step(control_rotation=(0.0, 90.0, 0.0), acceleration=accel(0.0))
            self.assertEqual(rig.s["LocomotionState.bMoving"], False)
            self.assertClose(rig.s["LocomotionState.TargetYawAngle"], 90.0)
            self.assertIn("set_actor_rotation", rig.names())
        off = dict(CHARACTER_SETTINGS, bAutoRotateOnAnyInputWhileNotMovingInViewDirectionRotationMode=False)
        for rig in rigs(settings=off):
            rig.step(control_rotation=(0.0, 90.0, 0.0), acceleration=accel(0.0))
            self.assertNotIn("set_actor_rotation", rig.names())
        for rig in rigs():                                                                        # velocity direction, blocked: rotate to the last target yaw (C:1548-1552)
            rig.step(events=[("RotationMode", "triggered", True)])
            self.assertEqual(rig.s["RotationMode"], VELOCITY_DIRECTION)
            self.assertIn("set_actor_rotation", rig.names())

    def test_the_rotation_yaw_speed_of_the_animation_turns_the_actor(self):
        for rig in rigs():
            rig.step(curves={"RotationYawSpeed": 90.0})                                           # 90 deg/s * dt = 1.5 degrees (C:1814)
            pitch, yaw, roll = rig.command("set_actor_rotation")[0]
            self.assertClose(yaw, 1.5)
            self.assertClose(rig.s["LocomotionState.TargetYawAngle"], 1.5)                        # the target follows the new actor rotation (C:1822)
        for rig in rigs():
            rig.step(curves={"RotationYawSpeed": 0.0})
            self.assertNotIn("set_actor_rotation", rig.names())

    def test_aiming_limits_how_far_the_actor_may_lag_behind_the_view(self):
        for rig in rigs():
            rig.step(events=[("Aim", "triggered", True)], control_rotation=(0.0, 150.0, 0.0))
            self.assertEqual(rig.s["RotationMode"], AIMING)
            alpha = damper_alpha(DT, 0.1)
            view_space = unwind(150.0 + (70.0 - 150.0) * alpha)                                   # the secondary constraint (C:1745-1763)
            pitch, yaw, roll = rig.command("set_actor_rotation")[0]
            self.assertClose(yaw, 80.0 * alpha, 1e-3)
            self.assertClose(yaw, unwind(150.0 - view_space), 1e-3)
            self.assertClose(rig.s["LocomotionState.AimingYawAngleLimit"], view_space, 1e-3)
            self.assertTrue(rig.s["LocomotionState.bAimingLimitAppliedThisFrame"])
            self.assertClose(rig.s["LocomotionState.TargetYawAngle"], 150.0)
            # feed the rotation back: the actor ends 70 degrees behind the view and the limit settles at the minimum (C:1776)
            for _ in range(60):
                rig.step(control_rotation=(0.0, 150.0, 0.0), actor_rotation=yaw_quat(rig.command("set_actor_rotation")[0][1]))
            self.assertClose(rig.command("set_actor_rotation")[0][1], 80.0, 1e-3)
            self.assertClose(rig.s["LocomotionState.AimingYawAngleLimit"], 70.0, 1e-3)
        for rig in rigs():                                                                        # within the limit nothing is commanded and the limit is the setting (C:1734-1738)
            rig.step(events=[("Aim", "triggered", True)], control_rotation=(0.0, 60.0, 0.0))
            self.assertNotIn("set_actor_rotation", rig.names())
            self.assertClose(rig.s["LocomotionState.AimingYawAngleLimit"], 70.0)

    def test_aiming_on_the_move_follows_the_view_with_the_extra_smooth_target(self):
        for rig in rigs():
            rig.step(events=[("Aim", "triggered", True)], velocity=velocity(300.0), acceleration=accel(0.0))
            rig.step(control_rotation=(0.0, 30.0, 0.0), velocity=velocity(300.0), acceleration=accel(0.0))
            smooth = interpolate_constant(0.0, 30.0, DT, 1000.0)                                  # C:1699-1702
            self.assertClose(rig.s["LocomotionState.SmoothTargetYawAngle"], smooth)
            self.assertClose(rig.command("set_actor_rotation")[0][1], damper_angle(0.0, smooth, DT, 0.1), 1e-3)

    def test_in_the_air_the_rotation_setting_decides(self):
        air = dict(movement_mode=FALLING_MODE, is_grounded=False)
        for mode, expected in (("RotateToVelocity", 90.0), ("KeepWorldRotation", 0.0)):
            for rig in rigs(settings=dict(CHARACTER_SETTINGS, InAirRotationMode=mode)):
                rig.step(velocity=velocity(200.0, 90.0), acceleration=accel(90.0), **air)
                self.assertEqual(rig.s["LocomotionMode"], IN_AIR)
                self.assertClose(rig.s["LocomotionState.TargetYawAngle"], expected, msg=mode)
                if mode == "RotateToVelocity":
                    self.assertClose(rig.command("set_actor_rotation")[0][1], damper_angle(0.0, 90.0, DT, 0.2), 1e-3)
                else:
                    self.assertNotIn("set_actor_rotation", rig.names())
        # RotateToVelocity without moving falls through to keeping the rotation, like the C++ switch (C:1855-1863)
        for rig in rigs(settings=dict(CHARACTER_SETTINGS, InAirRotationMode="RotateToVelocity")):
            rig.step(velocity=(0.0, 0.0, -300.0), **air)
            self.assertNotIn("set_actor_rotation", rig.names())
        # KeepViewSpaceRotation: the actor keeps its angle to the view while the view turns
        for rig in rigs(settings=dict(CHARACTER_SETTINGS, InAirRotationMode="KeepViewSpaceRotation")):
            rig.step(velocity=(0.0, 0.0, -300.0), **air)
            view_space = rig.s["LocomotionState.TargetYawAngleViewSpace"]
            rig.step(control_rotation=(0.0, 40.0, 0.0), velocity=(0.0, 0.0, -300.0), **air)
            self.assertClose(rig.s["LocomotionState.TargetYawAngle"], 40.0 - view_space)

    def test_nothing_rotates_during_an_action_or_root_motion(self):
        for rig in rigs():
            rig.call("SetLocomotionAction", "Als.LocomotionAction.Rolling")
            rig.step(control_rotation=(0.0, 90.0, 0.0), velocity=velocity(300.0), acceleration=accel(0.0))
            self.assertNotIn("set_actor_rotation", rig.names())
            self.assertClose(rig.s["LocomotionState.TargetYawAngle"], 0.0)                        # the late refresh follows the actor (C:1441)
        for rig in rigs():
            rig.step(control_rotation=(0.0, 90.0, 0.0), velocity=velocity(300.0), acceleration=accel(0.0), has_root_motion=True)
            self.assertNotIn("set_actor_rotation", rig.names())


class StanceTests(Case):
    """Desired and actual stance: C:760-887 (SetDesiredStance, ApplyDesiredStance, OnStartCrouch / OnEndCrouch)."""

    def test_the_toggle_asks_the_movement_component_and_the_stance_follows_the_crouch(self):
        for rig in rigs():
            rig.step()
            self.assertEqual((rig.s["DesiredStance"], rig.s["Stance"]), (STAND, STAND))
            rig.step(events=[("Crouch", "triggered", True)])
            self.assertEqual(rig.s["DesiredStance"], CROUCH)
            self.assertIn("crouch", rig.names())
            self.assertEqual(rig.s["Stance"], STAND)                                              # the movement component has not crouched yet
            rig.step(is_crouching=True)                                                           # OnStartCrouch
            self.assertEqual(rig.s["Stance"], CROUCH)
            rig.step(events=[("Crouch", "triggered", True)])
            self.assertEqual(rig.s["DesiredStance"], STAND)
            self.assertIn("uncrouch", rig.names())
            self.assertEqual(rig.s["Stance"], CROUCH)
            rig.step(is_crouching=False)                                                          # OnEndCrouch
            self.assertEqual(rig.s["Stance"], STAND)

    def test_the_jump_key_of_a_crouching_character_stands_up_instead(self):
        for rig in rigs():
            rig.step(events=[("Crouch", "triggered", True)])
            rig.step(is_crouching=True)
            rig.step(events=[("Jump", "triggered", True)])
            self.assertEqual(rig.s["DesiredStance"], STAND)
            self.assertIn("uncrouch", rig.names())
            self.assertNotIn("jump", rig.names())

    def test_the_air_uncrouches_and_the_landing_crouches_again(self):
        for rig in rigs():
            rig.step(events=[("Crouch", "triggered", True)])
            rig.step(is_crouching=True)
            rig.step(movement_mode=FALLING_MODE, is_grounded=False, velocity=(0.0, 0.0, -100.0))       # OnMovementModeChanged -> ApplyDesiredStance (C:507, C:816-818)
            self.assertEqual(rig.s["LocomotionMode"], IN_AIR)
            self.assertIn("uncrouch", rig.names())
            self.assertEqual(rig.s["DesiredStance"], CROUCH)
            rig.step(movement_mode=WALKING_MODE, is_grounded=True, velocity=(0.0, 0.0, 0.0))
            self.assertIn("crouch", rig.names())

    def test_an_action_clears_the_input_block_and_a_roll_crouches(self):
        rolling = dict(CHARACTER_SETTINGS, Rolling={"bCrouchOnStart": True})
        for rig in rigs(settings=rolling):
            rig.call("SetLocomotionAction", "Als.LocomotionAction.Rolling")
            rig.step()
            self.assertEqual(rig.s["LocomotionAction"], "Als.LocomotionAction.Rolling")
            self.assertIn("crouch", rig.names())                                                  # C:821-824
            rig.call("SetLocomotionAction", "")
            rig.step()
            self.assertEqual(rig.command("set_input_blocked"), (False,))                          # C:1095
            self.assertIn("uncrouch", rig.names())


class LandingAndJumpTests(Case):
    """NotifyLocomotionModeChanged (C:505-562) and Jump / OnJumped (C:1464-1502)."""

    def land(self, rig, fall_speed, with_input):
        accel_now = accel(0.0) if with_input else (0.0, 0.0, 0.0)
        rig.step(movement_mode=FALLING_MODE, is_grounded=False, velocity=(0.0, 0.0, -fall_speed), acceleration=accel_now)
        rig.step(movement_mode=WALKING_MODE, is_grounded=True, velocity=(0.0, 0.0, 0.0), acceleration=accel_now)

    def test_a_soft_landing_raises_the_friction_for_half_a_second(self):
        for with_input, factor in ((True, 0.5), (False, 3.0)):
            for rig in rigs():
                self.land(rig, 500.0, with_input)
                self.assertEqual(rig.commands("set_braking_friction_factor"), [(factor,)], with_input)         # C:531-536
                self.assertTrue(rig.s["LocomotionState.bRotationTowardsLastInputDirectionBlocked"])
                for _ in range(29):                                                                           # 29 frames: 0.4833 s
                    rig.step()
                    self.assertEqual(rig.commands("set_braking_friction_factor"), [])
                for _ in range(2):
                    rig.step()
        for rig in rigs():                                                                                    # the reset happens once, at 0.5 s
            self.land(rig, 500.0, True)
            resets = []
            for _ in range(40):
                rig.step()
                resets += rig.commands("set_braking_friction_factor")
            self.assertEqual(resets, [(0.0,)])

    def test_a_hard_landing_starts_the_actions_instead(self):
        # at 700 cm/s the roll starts, at 1000 the ragdoll: both are later milestones, so the friction branch is skipped and nothing else happens
        for speed in (700.0, 900.0, 1000.0, 1500.0):
            for rig in rigs(settings=dict(CHARACTER_SETTINGS, Ragdolling={"bStartRagdollingOnLand": True, "RagdollingOnLandSpeedThreshold": 1000.0},
                                          Rolling={"bStartRollingOnLand": True, "RollingOnLandSpeedThreshold": 700.0})):
                self.land(rig, speed, True)
                self.assertEqual(rig.commands("set_braking_friction_factor"), [], speed)
        for rig in rigs(settings=dict(CHARACTER_SETTINGS, Ragdolling={"bStartRagdollingOnLand": False}, Rolling={"bStartRollingOnLand": False})):
            self.land(rig, 1500.0, True)                                                                      # both switched off: the friction branch
            self.assertEqual(rig.commands("set_braking_friction_factor"), [(0.5,)])

    def test_the_jump_key_jumps_a_standing_grounded_character_and_the_animation_hears_of_it(self):
        for rig in rigs():
            rig.step(events=[("Jump", "triggered", True)])
            self.assertEqual(rig.commands("jump"), [()])
            self.assertEqual(rig.anim.snapshot()["Jumps"], 0)                                                 # not yet: the movement component has not started it
            rig.step(movement_mode=FALLING_MODE, is_grounded=False, velocity=(0.0, 0.0, 420.0), hooks=[("jumped",)])      # OnJumped, then the mode changes
            self.assertEqual(rig.anim.snapshot()["Jumps"], 1)                                                 # `on jumped` -> Anim.Jump()
            self.assertEqual(rig.s["LocomotionMode"], IN_AIR)
            rig.step(events=[("Jump", "triggered", False)])
            self.assertEqual(rig.commands("stop_jumping", "AlsInput"), [()])                                  # StopJumping() (EX:180)
            rig.step(movement_mode=WALKING_MODE, is_grounded=True, velocity=(0.0, 0.0, 0.0))
            rig.step(movement_mode=FALLING_MODE, is_grounded=False, velocity=(0.0, 0.0, -10.0))               # walking off a ledge is not a jump: no `jumped` event
            self.assertEqual(rig.anim.snapshot()["Jumps"], 1)

    def test_a_jump_key_without_a_jump_of_the_movement_component_tells_the_animation_nothing(self):
        # the old workaround counted two frames after the key; the hook says what really happened
        for rig in rigs():
            rig.step(events=[("Jump", "triggered", True)])
            rig.run(3)
            rig.step(movement_mode=FALLING_MODE, is_grounded=False, velocity=(0.0, 0.0, -10.0))
            self.assertEqual(rig.anim.snapshot()["Jumps"], 0)

    def test_the_hooks_are_handled_in_the_slot_after_the_movement_of_the_same_tick(self):
        for rig in rigs():
            rig.step(movement_mode=FALLING_MODE, is_grounded=False, velocity=(0.0, 0.0, -100.0))
            self.assertEqual(rig.s["LocomotionMode"], IN_AIR)                                                 # the tick that raised the hook has seen its effect
            rig.step(is_crouching=True)
            self.assertEqual(rig.s["Stance"], CROUCH)

    def test_no_jump_while_an_action_runs_or_in_the_air(self):
        for rig in rigs():
            rig.call("SetLocomotionAction", "Als.LocomotionAction.Mantling")
            rig.call("Jump")
            rig.step()
            self.assertNotIn("jump", rig.names())
        for rig in rigs():
            rig.step(movement_mode=FALLING_MODE, is_grounded=False, velocity=(0.0, 0.0, -10.0))
            rig.call("Jump")
            rig.step()
            self.assertNotIn("jump", rig.names())

    def test_the_command_of_the_jump_reaches_the_host_before_the_movement_component(self):
        # AlsInput (pre_movement) calls Jump(); the command is the character's: its pre_movement block hands it to the host
        for rig in rigs():
            rig.step(events=[("Jump", "triggered", True)])
            handed = {(n, slot): names for n, slot, names in rig.trace}
            self.assertIn("jump", handed[("AlsCharacter", "pre_movement")])
            self.assertNotIn("jump", handed[("AlsCharacter", "after_movement")])
            self.assertEqual(handed[("AlsInput", "pre_movement")], [])
            rig.step(events=[("Crouch", "triggered", True)])                                                   # the same for the crouch of a desired stance
            handed = {(n, slot): names for n, slot, names in rig.trace}
            self.assertIn("crouch", handed[("AlsCharacter", "pre_movement")])


class InputMappingTests(Case):
    """AlsInput.dal against AlsCharacterExample.cpp (EX:91-214): which action does what."""

    def test_move_is_relative_to_the_view_yaw(self):
        for yaw, value, direction in ((0.0, (0.0, 1.0), (1.0, 0.0, 0.0)), (0.0, (1.0, 0.0), (0.0, 1.0, 0.0)), (0.0, (0.0, -1.0), (-1.0, 0.0, 0.0)),
                                      (90.0, (0.0, 1.0), (0.0, 1.0, 0.0)), (90.0, (1.0, 0.0), (-1.0, 0.0, 0.0)),
                                      (0.0, (1.0, 1.0), (math.sqrt(0.5), math.sqrt(0.5), 0.0)), (0.0, (3.0, 4.0), (0.8, 0.6, 0.0))):
            for rig in rigs():
                rig.step(events=[("Move", "triggered", value)], control_rotation=(0.0, yaw, 0.0))
                (got, scale), = rig.commands("add_movement_input", "AlsInput")
                self.assertClose(got, direction, msg=(yaw, value))
                self.assertEqual(scale, 1.0)

    def test_look_scales_with_the_rates_and_the_mouse_sensitivities(self):
        for rig in rigs():
            rig.step(events=[("Look", "triggered", (2.0, 3.0))])
            self.assertClose(rig.command("add_controller_pitch_input", "AlsInput")[0], 3.0 * 90.0)             # LookUpRate
            self.assertClose(rig.command("add_controller_yaw_input", "AlsInput")[0], 2.0 * 240.0)              # LookRightRate
            rig.step(events=[("LookMouse", "triggered", (2.0, 3.0))])
            self.assertClose(rig.command("add_controller_pitch_input", "AlsInput")[0], 3.0)
            self.assertClose(rig.command("add_controller_yaw_input", "AlsInput")[0], 2.0)

    def test_the_other_actions_set_the_desired_states_of_the_character(self):
        for rig in rigs():
            rig.step(events=[("Aim", "triggered", True)])
            self.assertTrue(rig.s["bDesiredAiming"])
            rig.step(events=[("Aim", "canceled", False)])
            self.assertFalse(rig.s["bDesiredAiming"])
            rig.step(events=[("Sprint", "triggered", True)])
            self.assertEqual(rig.s["DesiredGait"], SPRINTING)
            rig.step(events=[("Sprint", "canceled", False)])
            self.assertEqual(rig.s["DesiredGait"], RUNNING)
            rig.step(events=[("Walk", "triggered", True)])
            self.assertEqual(rig.s["DesiredGait"], WALKING)
            rig.step(events=[("Walk", "triggered", True)])
            self.assertEqual(rig.s["DesiredGait"], RUNNING)
            rig.step(events=[("Sprint", "triggered", True)])
            rig.step(events=[("Walk", "triggered", True)])
            self.assertEqual(rig.s["DesiredGait"], SPRINTING)                                                  # the walk key only toggles Walking <-> Running (EX:134-141)

    def test_switching_the_shoulder_flips_the_camera_flag(self):
        for rig in rigs():
            self.assertTrue(rig.inp.snapshot()["bRightShoulder"])
            rig.step(events=[("SwitchShoulder", "triggered", True)])
            self.assertFalse(rig.inp.snapshot()["bRightShoulder"])

    def test_the_actions_that_start_an_action_need_what_they_act_with(self):
        # no roll montage in the settings, no ragdoll in the host: the keys do nothing (the actions themselves: test_als_actions.py)
        for action in ("Ragdoll", "Roll"):
            for rig in rigs():
                rig.step(events=[(action, "triggered", True)])
                self.assertEqual(rig.s["LocomotionAction"], "")
                self.assertEqual(rig.commands("montage_play"), [])

    def test_only_the_bound_phases_are_handled(self):
        for rig in rigs():
            rig.step(events=[("Walk", "canceled", True), ("Crouch", "started", True), ("Roll", "started", True)])
            self.assertEqual((rig.s["DesiredGait"], rig.s["DesiredStance"]), (RUNNING, STAND))


class ClosedLoopTests(Case):
    """The character with a movement component that obeys the commands: a few seconds of play."""

    class Sim:
        def __init__(self):
            self.v = [0.0, 0.0]
            self.yaw = 0.0
            self.crouch = False
            self.max_walk, self.accel, self.brake = 375.0, 2000.0, 1500.0
            self.inp = [0.0, 0.0]

        def apply(self, cmds):
            for name, args in cmds:
                if name == "set_max_walk_speed":
                    self.max_walk = args[0]
                elif name == "set_max_acceleration_walking":
                    self.accel = args[0]
                elif name == "set_braking_deceleration_walking":
                    self.brake = args[0]
                elif name == "add_movement_input":
                    self.inp[0] += args[0][0] * args[1]
                    self.inp[1] += args[0][1] * args[1]
                elif name == "set_actor_rotation":
                    self.yaw = args[0][1]
                elif name == "crouch":
                    self.crouch = True
                elif name == "uncrouch":
                    self.crouch = False

        def move(self, dt):
            ax, ay = self.inp
            self.inp = [0.0, 0.0]
            if ax or ay:
                n = math.hypot(ax, ay)
                self.v[0] += ax / n * self.accel * dt
                self.v[1] += ay / n * self.accel * dt
                speed = math.hypot(*self.v)
                if speed > self.max_walk:
                    self.v = [c * self.max_walk / speed for c in self.v]
                return (ax / n * self.accel, ay / n * self.accel, 0.0)
            speed = math.hypot(*self.v)
            new = max(0.0, speed - self.brake * dt)
            self.v = [c * new / speed for c in self.v] if speed > 0.0 else [0.0, 0.0]
            return (0.0, 0.0, 0.0)

    def play(self, rig, sim, frames, events=(), **host):
        for i in range(frames):
            acc = sim.move(DT)
            rig.step(events=events(i) if callable(events) else events, velocity=(sim.v[0], sim.v[1], 0.0), acceleration=acc, max_acceleration=sim.accel,
                     is_crouching=sim.crouch, actor_rotation=yaw_quat(sim.yaw), **host)
            for logic in ("AlsInput", "AlsCharacter"):
                sim.apply(rig.cmds[logic])

    def test_holding_forward_runs_and_sprinting_takes_over_with_the_key(self):
        for rig in rigs():
            sim = self.Sim()
            hold = lambda i: [("Move", "triggered", (0.0, 1.0))]
            self.play(rig, sim, 120, hold)
            self.assertEqual((rig.s["Gait"], rig.s["MaxAllowedGait"]), (RUNNING, RUNNING))
            self.assertClose(math.hypot(*sim.v), 375.0)
            self.assertClose(sim.yaw, 0.0)
            self.play(rig, sim, 1, lambda i: [("Move", "triggered", (0.0, 1.0)), ("Sprint", "triggered", True)])
            self.play(rig, sim, 120, hold)
            self.assertEqual(rig.s["Gait"], SPRINTING)
            self.assertClose(math.hypot(*sim.v), 650.0)
            self.assertClose(rig.s["GaitAmount"], 3.0)
            self.play(rig, sim, 1, lambda i: [("Sprint", "triggered", False)])                                  # let go: back to running, the speed limit follows
            self.play(rig, sim, 120, hold)
            self.assertEqual(rig.s["Gait"], RUNNING)
            self.assertClose(math.hypot(*sim.v), 375.0)
            self.play(rig, sim, 200, lambda i: [])                                                              # no input: the character stops
            self.assertClose(math.hypot(*sim.v), 0.0)
            self.assertEqual(rig.s["Gait"], WALKING)

    def test_turning_the_view_turns_a_character_that_runs_in_view_direction(self):
        for rig in rigs():
            sim = self.Sim()
            hold = lambda i: [("Move", "triggered", (0.0, 1.0))]
            self.play(rig, sim, 30, hold)
            self.play(rig, sim, 150, hold, control_rotation=(0.0, 90.0, 0.0))
            self.assertClose(sim.yaw, 90.0, 1e-2)                                                               # the actor follows the camera
            self.assertClose(math.degrees(math.atan2(sim.v[1], sim.v[0])), 90.0, 1e-2)                          # and the movement input turned with it (EX:121)


# ---- the three implementations agree --------------------------------------------------------------------------------------------------

def system_params(seed, polyline=False):
    """Random settings data for the character: every settings member, tables with random subsets of their keys, inline curves."""
    types = {p.name: p.ty for p in modules()["AlsCharacter"].params}
    return {"AlsCharacter": {"Settings": harness.random_settings(types["Settings"], seed, polyline=polyline),
                             "MovementSettings": harness.random_settings(types["MovementSettings"], seed + 1, polyline=polyline)}}


NOTIFY_ACTION = "AlsAnimNotifyState_SetLocomotionAction"
NOTIFY_SCALE = "AlsAnimNotifyState_SetRootMotionScale"
NOTIFY_EARLY = "AlsAnimNotifyState_EarlyBlendOut"
ACTION_TAGS = ("", "Als.LocomotionAction.Rolling", "Als.LocomotionAction.Mantling", "Als.LocomotionAction.Ragdolling", "Als.LocomotionAction.GettingUp")


def system_frames(count, seed, real_anim=False, notifies=False, ragdoll=True):
    """Random frames for the system: host values, input events of AlsInput, the animation curves the character reads, and the hooks the movement
    component raises between the two slots (a change of the movement mode, of the crouch, a jump, the physics rotation); with ``notifies`` also
    the events of the three notify states of the actions (the generated C++ gets them from the driver patch only where it can: not here)."""
    mods = modules(real_anim)
    frames = harness.random_system_frames(mods, count, seed)
    rng = random.Random(seed * 31 + 7)
    step = next(i for i, (n, slot) in enumerate(harness.default_schedule(mods)) if (n, slot) == ("AlsCharacter", "after_movement"))
    mode, crouching = 1, False
    for f in frames:
        if rng.random() < 0.5:                                                 # mostly on the ground: the actions start there
            f["movement_mode"], f["is_grounded"] = 1, True
        f["curves"] = {"RotationYawSpeed": rng.choice([0.0, rng.uniform(-120.0, 120.0)]), "RotationYawOffset": rng.choice([0.0, rng.uniform(-40.0, 40.0)])}
        f["control_rotation"] = (rng.uniform(-60.0, 60.0), rng.uniform(-180.0, 180.0), 0.0)
        f["bones"] = {"pelvis": {"component": harness._rand_value(rng, TRANSFORM, 50.0), "world": harness._rand_value(rng, TRANSFORM, 100.0)}}     # the pelvis the ragdoll reads
        if not ragdoll:
            f["ragdoll_supported"] = False                                     # a host without a ragdoll: the roll is the action the keys start
        hooks = []
        if f["movement_mode"] != mode:
            hooks.append(("movement_mode_changed", mode, f["movement_mode"]))
            mode = f["movement_mode"]
        if f["is_crouching"] != crouching:
            hooks.append(("crouch_started",) if f["is_crouching"] else ("crouch_ended",))
            crouching = f["is_crouching"]
        if rng.random() < 0.08:
            hooks.append(("jumped",))
        if rng.random() < 0.5:
            hooks.append(("physics_rotation", f["dt"]))
        if notifies:
            action = {"LocomotionAction": rng.choice(ACTION_TAGS)}
            if rng.random() < 0.15:
                hooks.append(("notify_begin", NOTIFY_ACTION, action, rng.randint(1, 3)))
            if rng.random() < 0.15:
                hooks.append(("notify_end", NOTIFY_ACTION, action, rng.randint(1, 3)))
            if rng.random() < 0.1:
                hooks.append(("notify_begin", NOTIFY_SCALE, {"TranslationScale": rng.choice([0.5, 1.0, 2.0])}, 1))
            if rng.random() < 0.1:
                hooks.append(("notify_end", NOTIFY_SCALE, {"TranslationScale": rng.choice([0.5, 1.0, 2.0])}, 1))
            if rng.random() < 0.2:
                hooks.append(("notify_tick", NOTIFY_EARLY, {"BlendOutDuration": rng.choice([0.1, 0.4]), "bCheckInput": rng.random() < 0.5,
                                                            "bCheckLocomotionMode": rng.random() < 0.5, "bCheckRotationMode": rng.random() < 0.5,
                                                            "bCheckStance": rng.random() < 0.5}, rng.randint(1, 3)))
        f["step_events"] = {step: hooks} if hooks else {}
    return frames


class EchoHost(harness.ScriptedHost):
    """The scripted host of the system runs, with the commands the stand-in engine applies to what the logics read next: SetActorLocation
    SetVelocity and SetMovementMode (all logics share one engine, so the change reaches every host)."""

    def __init__(self):
        super().__init__()
        self.peers: list = [self]

    def record(self, name, args):
        super().record(name, args)
        if name == "set_actor_location":
            for h in self.peers:
                h.actor_location = tuple(args[0])
        elif name == "set_velocity":
            for h in self.peers:
                h.velocity = tuple(args[0])
        elif name == "set_movement_mode":
            for h in self.peers:
                h.movement_mode = int(args[0])
                h.movement_mode_custom = int(args[1]) if len(args) > 1 else 0


def run_system(mods, frames, backend, params, schedule=None):
    """``harness.run_system`` with the params of the logics (settings data)."""
    hosts = {n: EchoHost() for n in mods}
    for h in hosts.values():
        h.peers = list(hosts.values())
    objs = {}
    for n, m in mods.items():
        p = params.get(n, {})
        objs[n] = Interpreter(m, hosts[n], **p) if backend == "interp" else load_class(m)(hosts[n], **p)
    for n, m in mods.items():
        for peer in m.peers:
            if peer.logic in objs:
                objs[n].connect(peer.name, objs[peer.logic])
    steps = schedule or harness.default_schedule(mods)
    for n in sorted(mods, key=lambda k: mods[k].kind != "actor"):
        begin = getattr(objs[n], "begin_play", None)
        if begin is not None:
            hosts[n].load({})                                  # BeginPlay runs before the first frame: the host still has its neutral values (as in the C++ driver)
            begin()
            hosts[n].calls.clear()
    out = []
    for f in frames:
        for n in mods:
            hosts[n].load(f)
        for si, (n, slot) in enumerate(steps):
            for ev in f.get("step_events", {}).get(si, []):          # the hooks the movement component raised between the slots
                for h in hosts.values():
                    h.events.append(tuple(ev))
            if slot is None:
                objs[n].update(f["dt"])
            else:
                objs[n].update_phase(slot, f["dt"])
        snap = {}
        for n in mods:
            snap[n] = objs[n].snapshot()
            snap[n]["__cmds__"] = tuple(hosts[n].calls)
            hosts[n].calls.clear()
        out.append(snap)
    return out


def compare_systems(test, mods, a, b, tol, logics=("AlsInput", "AlsCharacter"), effects=False):
    problems = []
    test.assertEqual(len(a), len(b))
    for i, (fa, fb) in enumerate(zip(a, b)):
        for n in logics:
            for k, v in fa[n].items():
                if k.startswith("__"):
                    if effects:
                        ea, eb = tcc.py_effects(v), list(fb[n][k])
                        if len(ea) != len(eb) or any(x[0] != y[0] or not all(tcc.close_token(p, q, max(tol, 1e-3)) for p, q in zip(x[1:], y[1:])) for x, y in zip(ea, eb)):
                            problems.append(f"frame {i} {n} commands: {ea} != {eb}")
                    elif tuple(fa[n][k]) != tuple(fb[n][k]):
                        problems.append(f"frame {i} {n} commands: {fa[n][k]} != {fb[n][k]}")
                elif not tcc.close(v, fb[n][k], tol):
                    problems.append(f"frame {i} {n}.{k}: {v!r} != {fb[n][k]!r}")
    test.assertEqual(problems[:6], [], f"{len(problems)} mismatches")


class GeneratedCppShapeTests(Case):
    def has(self, text, needle):
        self.assertTrue(needle in text, f"the generated C++ has no {needle!r}")

    def test_the_character_and_the_input_are_components_with_the_expected_ticks_and_peers(self):
        from dal.cppbackend import generate_cpp

        mods = modules()
        char, inp = generate_cpp(mods["AlsCharacter"]), generate_cpp(mods["AlsInput"])
        h, c = char["AlsCharacter.h"], char["AlsCharacter.cpp"]
        self.has(h, "class UAlsCharacter : public UActorComponent")
        self.has(h, "TObjectPtr<UAlsAnimationInstance> Anim;")
        self.has(h, "FGameplayTag Gait = AlsGaitTags::Walking;")
        self.has(h, "FAlsLocomotionState LocomotionState = FAlsLocomotionState();")
        self.has(h, "TObjectPtr<UAlsCharacterSettings> Settings;")
        self.has(h, "TObjectPtr<UAlsMovementSettings> MovementSettings;")
        self.has(h, "void Jump();")
        self.has(h, "FAlsCharacterPhaseTick DalPhaseTicks[2];")                             # pre_movement and after_movement
        self.has(c, "DalPhaseTicks[0].TickGroup = TG_PrePhysics;")
        self.has(c, "DalPhaseTicks[1].AddPrerequisite(DalMovement, DalMovement->PrimaryComponentTick);")    # after the movement component
        self.has(c, "Anim->DalQueue_Jump();")                                               # the animation instance is called through its queue
        for notify_class in ("UAlsAnimNotifyState_SetLocomotionAction", "UAlsAnimNotifyState_SetRootMotionScale", "UAlsAnimNotifyState_EarlyBlendOut"):
            self.has(h + c, notify_class)                                                   # the classes of the montages' notify states are the ALS ones
        self.has(h, "MantlingHigh;")                                                        # one UAlsMantlingSettings per kind of mantle
        for command in ("SetActorRotation", "SetMaxWalkSpeed", "SetGroundFriction", "SetBrakingFrictionFactor", "SetInputBlocked"):
            self.has(c, f"DalHost::Cmd::{command}(")
        self.assertEqual(len(re.findall(r"BindAction\(", inp["AlsInput.cpp"])), 19)         # the 19 bindings of SetupPlayerInputComponent (EX:69-87)
        self.has(inp["AlsInput.cpp"], "Character->Jump();")
        self.has(inp["AlsInput.h"], '#include "AlsLib.h"')                                # one shared header for the library


class BackendAgreementTests(Case):
    def test_interpreter_and_generated_python_agree_on_random_frames(self):
        mods = modules()
        for seed in (1, 2, 3, 4):
            params = system_params(seed)
            frames = system_frames(200, seed)
            compare_systems(self, mods, run_system(mods, frames, "interp", params), run_system(mods, frames, "python", params), 0.0, logics=tuple(mods))

    def test_the_random_frames_exercise_the_logic(self):
        mods = modules()
        frames = system_frames(300, 5)
        out = run_system(mods, frames, "python", system_params(5))
        names = {c[0] for f in out for logic in ("AlsInput", "AlsCharacter") for c in f[logic]["__cmds__"]}
        for expected in ("set_actor_rotation", "set_max_walk_speed", "add_movement_input", "crouch", "uncrouch", "jump", "add_controller_yaw_input"):
            self.assertIn(expected, names)
        char = [f["AlsCharacter"] for f in out]
        self.assertGreater(len({c["Gait"] for c in char}), 1)
        self.assertGreater(len({c["RotationMode"] for c in char}), 1)
        self.assertGreater(len({c["LocomotionMode"] for c in char}), 1)
        self.assertGreater(len({c["Stance"] for c in char}), 1)

    @unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
    def test_the_generated_cpp_agrees_with_python_against_the_stand_in_engine(self):
        mods = modules()

        def patch(text, modules_, frames):
            return input_events_patch(text, modules_, frames)

        for seed, ragdoll in ((1, True), (2, True), (3, False)):
            params = system_params(seed, polyline=True)                       # the stand-in engine evaluates polylines
            if not ragdoll:
                params["AlsCharacter"]["Settings"].setdefault("Rolling", {})["Montage"] = "A_Roll"      # the keys roll: a montage that exists
            frames = tcc.round_frames(system_frames(60, seed, ragdoll=ragdoll))
            with als_host_v2.cpp_environment(settings={"AlsCharacter": params["AlsCharacter"]}, driver_patch=patch) as env:
                try:
                    cpp, output = env.run(mods, frames)
                except Exception as e:                                         # a failed build shows the compiler's output
                    self.fail(f"C++ failed:\n{str(e)[:4000]}")
            if tcc.COMPILER[0] == "msvc":
                self.assertNotIn("warning", output.lower(), output)
            py = run_system(mods, frames, "python", params)
            compare_systems(self, mods, py, cpp, 3e-3, effects=True)
            self.assertGreater(sum(len(f["AlsCharacter"]["__cmds__"]) for f in cpp), 100)
            seen = {c[0] for f in cpp for logic in ("AlsInput", "AlsCharacter") for c in f[logic]["__cmds__"]}
            self.assertTrue({"add_movement_input", "add_controller_yaw_input", "set_actor_rotation", "set_max_walk_speed"} <= seen, seen)   # the input events reached AlsInput
            if not ragdoll:
                self.assertTrue({"montage_play", "crouch"} <= seen, seen)                                # the roll: the montage and the crouch of its stance


def input_events_patch(text, modules_, frames):
    """The system driver does not deliver input actions: give the pawn an Enhanced Input component, the actions of AlsInput, and fire the
    events of each frame before the logics tick."""
    actions = [a.name for a in modules_["AlsInput"].actions]
    anchor = "\tPawn.StubController = &Controller;\n"
    if anchor not in text or "\tL_AlsInput.StubWorld = &World;\n" not in text:
        raise RuntimeError("the system driver changed: no anchor for the input component")
    text = text.replace(anchor, anchor + "\tUEnhancedInputComponent InputComp;\n\tPawn.InputComponent = &InputComp;\n"
                        + "".join(f"\tUInputAction Action_{a};\n" for a in actions), 1)
    text = text.replace("\tL_AlsInput.StubWorld = &World;\n", "\tL_AlsInput.StubWorld = &World;\n"
                        + "".join(f"\tL_AlsInput.DalAction_{a} = &Action_{a};\n" for a in actions), 1)
    text = text.replace("\tMesh.StubAsset = &MeshAsset; Mesh.StubOwner = &Pawn;\n",
                        "\tUPhysicsAsset Phys;\n\tMesh.StubPhysics = &Phys;\n\tMesh.StubAsset = &MeshAsset; Mesh.StubOwner = &Pawn;\n", 1)
    marker = "\t\tStubLog::Lines().clear();\n"
    chunks = text.split(marker)
    out = [chunks[0]]
    for k, chunk in enumerate(chunks[1:]):
        lines = ""
        fr = frames[k]
        if "root_motion_mode" in fr:                                           # what the actions read of the mesh: the root motion mode of its animation instance, its physics asset
            lines += f"\t\tL_AlsAnimationInstance.RootMotionMode = static_cast<ERootMotionMode::Type>({int(fr['root_motion_mode'])});\n"
        if "ragdoll_supported" in fr:
            lines += '\t\tPhys.StubBodies = {"pelvis", "spine_03"};\n' if fr["ragdoll_supported"] else '\t\tPhys.StubBodies = {"pelvis"};\n'
        if "StubCurves[" not in text:                                          # the animation curves the character reads through the mesh's animation instance
            for cname, cv in frames[k].get("curves", {}).items():
                lines += f'\t\tL_AlsAnimationInstance.StubCurves["{cname}"] = {cpp_driver._num(cv)}f;\n'
        for ev in frames[k].get("events", []):
            if ev[0] == "input":
                c = cpp_driver._comps(cpp_driver._action_type(modules_["AlsInput"], ev[1]), ev[3])
                lines += (f"\t\tInputComp.Fire(&Action_{ev[1]}, ETriggerEvent::{cpp_driver._TRIGGER[ev[2]]}, "
                          f"FInputActionValue({cpp_driver._num(c[0])}, {cpp_driver._num(c[1])}, {cpp_driver._num(c[2])}));\n")
        out.append(marker + lines + chunk)
    return "".join(out)


# ---- the library's math against the results of the compiled ALS in Unreal 5.8 --------------------------------------------------------------

GOLDEN_DIR = os.path.join(ALS_DIR, "goldens")
GOLDEN_TYPES = {"float32": "float", "float64": "float", "vec3": "vec3", "vec3f": "vec3", "vec2": "vec2", "rotator": "rotator", "quat": "quat",
                "enum_move_dir": "AlsMovementDirection"}
GOLDEN_RENAMED = {"clamp_magnitude012d": "clamp_magnitude01_2d"}
GOLDEN_STATEFUL = ("spring_damper_float", "spring_damper_vector", "try_calculate_pole_vector")      # not part of the library (rig side)
# differences that are not the port's: float32 overflow, NaN / inf inputs (x / 0 is 0 in DAL), the C library's atan2 of a negative zero,
# sin / cos of huge float32 radians, the half life of exactly -SMALL_NUMBER (a division by zero)
GOLDEN_EDGES = ("huge", "flt_max", "special_radian", "special_angle", "neg_zero", "minus_small_number", "nan", "inf", "overflow")


def golden_decode(x):
    if isinstance(x, str):
        return {"nan": float("nan"), "inf": float("inf"), "-inf": float("-inf")}.get(x, x)
    if isinstance(x, list):
        return [golden_decode(i) for i in x]
    if isinstance(x, dict):
        return {k: golden_decode(v) for k, v in x.items()}
    return x


def has_non_finite(x):
    if isinstance(x, (list, tuple)):
        return any(has_non_finite(i) for i in x)
    return isinstance(x, float) and not math.isfinite(x)


@unittest.skipUnless(os.path.isfile(os.path.join(GOLDEN_DIR, "als_math.json")), "the UE goldens (als/goldens) are not there")
class LibGoldenTests(Case):
    """Every pure function of ``als_lib.dal`` that has a golden of the compiled UAlsMath / UAlsRotation / UAlsVector (float32 inputs and
    results of the Unreal editor): the cases of the goldens go through the generated Python of the library."""

    def probe(self, name, f):
        decls = "\n".join(f"input P{i}: {GOLDEN_TYPES[f['param_types'][p]]};" for i, p in enumerate(f["params"]))
        args = ", ".join(f"P{i}" for i in range(len(f["params"])))
        src = f'use "als_lib.dal";\nlogic Probe;\n{decls}\nstate Out: {GOLDEN_TYPES[f["returns"]]};\nupdate(dt) {{ Out = {name}({args}); }}\n'
        return dal.check_source(src, "probe.dal", files={"als_lib.dal": read("als_lib.dal")}).module

    def test_the_library_functions_give_what_unreal_gives(self):
        import json

        compared, functions = 0, []
        for fn in ("als_math.json", "als_rotation.json", "als_vector.json"):
            with open(os.path.join(GOLDEN_DIR, fn), encoding="utf-8") as fh:
                data = json.load(fh)
            for f in data["functions"]:
                name = f["python"].split(".")[1]
                name = GOLDEN_RENAMED.get(name, name)
                if name in GOLDEN_STATEFUL:
                    continue
                module = self.probe(name, f)
                cases = [c for c in f["cases"] if not any(t in c["tag"] for t in GOLDEN_EDGES)]
                cases = [c for c in cases if not has_non_finite(golden_decode(c["in"])) and not has_non_finite(golden_decode(c["out"]))]
                self.assertGreaterEqual(len(cases), 0.8 * len(f["cases"]), name)
                frames = [{"dt": 0.0, "free": {f"P{i}": golden_decode(c["in"][p]) for i, p in enumerate(f["params"])}} for c in cases]
                out = harness.run_python(module, frames)
                tol, absolute = 2e-4, (0.03 if name == "angle_between_signed_xy" else None)       # float32 acos of a dot product near 1
                bad = []
                for c, o in zip(cases, out):
                    want = golden_decode(c["out"])
                    want = want["value"] if isinstance(want, dict) else want
                    if absolute is not None and abs(o["Out"] - want) <= absolute:
                        continue
                    if absolute is None and tcc.close(o["Out"], tuple(want) if isinstance(want, list) else want, tol):
                        continue
                    bad.append((c["tag"], c["in"], want, o["Out"]))
                self.assertEqual(bad[:3], [], f"{name}: {len(bad)} of {len(cases)}")
                compared += len(cases)
                functions.append(name)
        self.assertGreaterEqual(len(functions), 22)
        self.assertGreater(compared, 4400)
        for name in ("clamp01", "damper_exact_alpha", "lerp_angle", "damper_exact_rotation", "calculate_movement_direction", "interpolate_angle_constant"):
            self.assertIn(name, functions)


# ---- the real animation instance next to these files ------------------------------------------------------------------------------------

@unittest.skipUnless(os.path.isfile(os.path.join(ALS_DIR, "AlsAnimationInstance.dal")), "als/AlsAnimationInstance.dal is not there")
class RealAnimationInstanceTests(Case):
    def test_the_three_files_check_with_the_real_instance_and_it_finds_what_it_reads(self):
        files = source_files(real_anim=True)
        for name in ("AlsCharacter.dal", "AlsInput.dal", "AlsAnimationInstance.dal"):
            try:
                dal.check_source(files[name], name, files=files)
            except dal.DalError as e:
                self.fail(f"{name} does not check against the real animation instance: {e.first}")

    def test_the_system_with_the_real_instance_agrees_in_both_python_implementations(self):
        mods = modules(real_anim=True)
        self.assertEqual(sorted(mods), ["AlsAnimationInstance", "AlsCharacter", "AlsInput"])
        params = system_params(3)
        frames = system_frames(60, 3, real_anim=True)
        a, b = run_system(mods, frames, "interp", params), run_system(mods, frames, "python", params)
        compare_systems(self, mods, a, b, 1e-9, logics=tuple(mods))


# ---- end of the file ----
