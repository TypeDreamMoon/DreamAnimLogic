"""The ALS animation instance (``als/AlsAnimationInstance.dal``, ``als/AlsLinkedAnimationInstance.dal``) for the tests.

The instance reads the character through ``peer Character: AlsCharacter``.  The real ``AlsCharacter.dal`` is somebody else's
file and changes under it, so the behaviour tests compile the real instance next to ``STAND_IN_CHARACTER``: an actor logic with
the state the instance reads (``Gait``, ``LocomotionState``, ... with the names and types ALS gives them) whose state is set from
free inputs, which is what a test frame can drive.  ``real_character_present()`` tells a test that wants the real pair.
"""

from __future__ import annotations

import os

import _bootstrap  # noqa: F401
from _bootstrap import ROOT

import dal
from dal import harness
from dal.harness import ScriptedHost, call_items, load_standins
from dal.interp import Interpreter
from dal.pybackend import generate_python, load_class

ALS_DIR = os.path.join(ROOT, "als")
INSTANCE = "AlsAnimationInstance"
LINKED = "AlsLinkedAnimationInstance"
CHARACTER = "AlsCharacter"

STAND_IN_CHARACTER = """
// stand-in for als/AlsCharacter.dal: the state AlsAnimationInstance.dal reads, set from free inputs at the end of the movement phase
use "als_lib.dal";
peer Anim: AlsAnimationInstance;
logic AlsCharacter : actor in after_movement;
param Settings: AlsCharacterSettings;
input InViewMode: AlsViewMode = AlsViewMode.ThirdPerson;
input InLocomotionMode: AlsLocomotionMode = AlsLocomotionMode.Grounded;
input InRotationMode: AlsRotationMode = AlsRotationMode.ViewDirection;
input InStance: AlsStance = AlsStance.Standing;
input InGait: AlsGait = AlsGait.Walking;
input InOverlayMode: AlsOverlayMode = AlsOverlayMode.Default;
input InLocomotionAction: AlsLocomotionAction;
input InLocomotion: AlsLocomotionState;
input InView: AlsViewState;
input InRagdolling: AlsRagdollingState;
input DoJump: bool;
state ViewMode: AlsViewMode = AlsViewMode.ThirdPerson;
state LocomotionMode: AlsLocomotionMode = AlsLocomotionMode.Grounded;
state RotationMode: AlsRotationMode = AlsRotationMode.ViewDirection;
state Stance: AlsStance = AlsStance.Standing;
state Gait: AlsGait = AlsGait.Walking;
state OverlayMode: AlsOverlayMode = AlsOverlayMode.Default;
state LocomotionAction: AlsLocomotionAction;
state LocomotionState: AlsLocomotionState;
state ViewState: AlsViewState;
state RagdollingState: AlsRagdollingState;
update(dt) in after_movement {
    ViewMode = InViewMode;
    LocomotionMode = InLocomotionMode;
    RotationMode = InRotationMode;
    Stance = InStance;
    Gait = InGait;
    OverlayMode = InOverlayMode;
    LocomotionAction = InLocomotionAction;
    LocomotionState = InLocomotion;
    ViewState = InView;
    RagdollingState = InRagdolling;
    if (DoJump) {
        Anim.Jump();
    }
}
"""


def read(name: str) -> str:
    with open(os.path.join(ALS_DIR, name), encoding="utf-8") as f:
        return f.read()


def als_files_present() -> bool:
    return all(os.path.isfile(os.path.join(ALS_DIR, n)) for n in ("als_lib.dal", INSTANCE + ".dal", LINKED + ".dal"))


def real_character_present() -> bool:
    return os.path.isfile(os.path.join(ALS_DIR, CHARACTER + ".dal"))


def virtual_files(character: str = STAND_IN_CHARACTER) -> dict[str, str]:
    """The real library and instances with a character of your choice, as the virtual file system of ``dal.check_source``."""
    return {"als_lib.dal": read("als_lib.dal"), INSTANCE + ".dal": read(INSTANCE + ".dal"), LINKED + ".dal": read(LINKED + ".dal"),
            CHARACTER + ".dal": character}


def compile_system(linked: bool = False, character: str = STAND_IN_CHARACTER) -> dict:
    """``{name: ir.Module}`` of the stand-in character, the instance and (optionally) the linked instance; the order is the order of
    the logics on the actor (the first anim logic is the main instance of the mesh)."""
    files = virtual_files(character)
    names = [CHARACTER, INSTANCE] + ([LINKED] if linked else [])
    return {n: dal.check_source(files[n + ".dal"], n + ".dal", files=files).module for n in names}


def run_system(mods: dict, frames: list[dict], params: dict | None = None, backend: str = "python",
               schedule=None) -> list[dict]:
    """``harness.run_system`` with constructor parameters: ``params = {logic: {param: settings data / value}}``."""
    params = params or {}
    hosts = {n: ScriptedHost() for n in mods}
    objs: dict = {}
    for n, m in mods.items():
        if backend == "interp":
            objs[n] = Interpreter(m, hosts[n], externs=load_standins(m), **params.get(n, {}))
        else:
            objs[n] = load_class(m, generate_python(m))(hosts[n], **params.get(n, {}))
    for n, m in mods.items():
        for p in m.peers:
            if p.logic in objs:
                objs[n].connect(p.name, objs[p.logic])
        if m.linked is not None and m.linked.parent in objs:
            objs[n].connect("Parent", objs[m.linked.parent])
    steps = schedule or harness.default_schedule(mods)
    for n in sorted(mods, key=lambda k: mods[k].kind != "actor"):
        begin = getattr(objs[n], "begin_play", None)
        if begin is not None:
            hosts[n].load(frames[0] if frames else {})
            begin()
    out = []
    for f in frames:
        for n in mods:
            hosts[n].load(f)
            for name, value in f.get("free", {}).get(n, {}).items():
                objs[n].set_input(name, value)
        for n, slot in steps:
            if slot is None:
                objs[n].update(f["dt"])
            else:
                objs[n].update_phase(slot, f["dt"])
        for n in mods:
            for item in f.get("calls", {}).get(n, []):
                name, args = (item, {}) if isinstance(item, str) else (item[0], dict(item[1]) if len(item) > 1 else {})
                objs[n].call_entry(name, **args)
            if mods[n].kind == "anim" and hasattr(objs[n], "post_update"):         # the graph has updated: the game thread's post_update
                objs[n].post_update()
        snap = {}
        for n in mods:
            snap[n] = objs[n].snapshot()
            snap[n]["__cmds__"] = tuple(hosts[n].calls)
            hosts[n].calls.clear()
        out.append(snap)
    return out
