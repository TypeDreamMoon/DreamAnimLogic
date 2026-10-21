"""Shared driver of the W4 PIE harness (editor Python): starts PIE, runs a scenario one engine frame at a time from a
Slate post-tick callback, records states and host effects per frame, writes a JSON result and quits the editor.

A scenario is a generator ``scenario(ctx)``: each ``yield`` waits one engine frame (``yield n`` waits n frames).  The
callback runs once per engine tick after the world tick, so what a scenario injects before a yield is the input of the
next world tick, and what it records is the state after the previous one (the fixed-step recipe of the earlier CMC
comparison: ``-UseFixedTimeStep -FPS=60``).

Parameters come from environment variables (set by run_test.ps1): DALTEST_NAME, DALTEST_OUT.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback

import unreal

OUT_DIR = os.environ.get("DALTEST_OUT", r"G:\DCC\scratch\w4_logs\runs")
PROJECT_DIR = unreal.Paths.project_dir()
DAL_DIR = os.path.join(PROJECT_DIR, "Dal")          # the .dal files the harness runs (and edits)


def snake(name: str) -> str:
    """UE Python spelling of a C++ property name (HitsLeft -> hits_left, Light3Rate -> light3_rate)."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", s)
    return s.lower()


def pyname(obj, name: str) -> str:
    """The attribute name UE Python gives the C++ property `name` of obj (bool properties lose a leading In / Is / Has / Can ...:
    a bool called InAir is `air`)."""
    have = set(dir(obj))
    s = snake(name)
    cands = [s]
    for prefix in ("in_", "is_", "has_", "can_", "should_", "was_", "did_", "will_"):
        if s.startswith(prefix):
            cands.append(s[len(prefix):])
    for c in cands:
        if c in have:
            return c
    raise AttributeError(f"{type(obj).__name__} has none of {cands}")


def plain(v):
    """A UE value as JSON-safe plain data (enums as their member name)."""
    if isinstance(v, (bool, int, float, str)) or v is None:
        return v
    if hasattr(v, "name") and hasattr(v, "value") and not hasattr(v, "x"):          # a Python enum wrapper
        return str(v.name)
    tn = type(v).__name__
    if tn in ("Rotator3f", "Quat4f", "Vector2f"):
        # the float variants of rotator / quat / vec2 have no Python fields; their text export does (Pitch=..,Yaw=..,Roll=.. / X=..,Y=..)
        nums = [float(x) for x in re.findall(r"=(-?[0-9.eE+-]+)", v.export_text())]
        return nums
    for names in (("x", "y", "z", "w"), ("x", "y", "z"), ("x", "y"), ("pitch", "yaw", "roll")):
        if all(hasattr(v, n) for n in names):
            return [float(getattr(v, n)) for n in names]
    return str(v)


class Context:
    """What a scenario sees."""

    def __init__(self, runner):
        self.runner = runner
        self.world = None
        self.pawn = None
        self.pc = None
        self.frame = 0                  # frames since the scenario started (0 = the first callback)
        self.records: list[dict] = []
        self.notes: dict = {}
        self.events: list[dict] = []

    def log(self, text):
        unreal.log(f"[daltest] f{self.frame}: {text}")

    def mark(self, kind, **data):
        """An annotated moment in the result (an input was injected, the file was edited ...)."""
        self.events.append({"frame": self.frame, "kind": kind, **data})

    def inject(self, action, x=1.0, y=0.0, z=0.0):
        return unreal.DalTestLibrary.inject_vector(self.pc, action, unreal.Vector(x, y, z))


class Runner:
    SETTLE_FRAMES = 12         # editor ticks before PIE is requested

    def __init__(self, name, scenario, *, quit_when_done=True):
        self.name = name
        self.scenario = scenario
        self.ctx = Context(self)
        self.gen = None
        self.wait = 0
        self.stage = "settle"
        self.ticks = 0
        self.handle = None
        self.quit_when_done = quit_when_done
        self.error = None
        self.t_start = time.time()
        self.world_times: list[float] = []

    def start(self):
        unreal.EditorPythonScripting.set_keep_python_script_alive(True)
        self.handle = unreal.register_slate_post_tick_callback(self._tick)
        unreal.log(f"[daltest] {self.name}: started")

    # ---- the frame callback -----------------------------------------------------------------------------------------------

    def _tick(self, dt):
        self.ticks += 1
        try:
            self._step()
        except Exception:
            self.error = traceback.format_exc()
            unreal.log_error("[daltest] exception:\n" + self.error)
            self.finish()

    def _step(self):
        ctx = self.ctx
        if self.stage == "settle":
            if self.ticks >= self.SETTLE_FRAMES:
                unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_request_begin_play()
                self.stage = "wait_pie"
                self.pie_wait = 0
            return
        if self.stage == "wait_pie":
            self.pie_wait += 1
            world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
            pawn = unreal.GameplayStatics.get_player_pawn(world, 0) if world is not None else None
            ticking = world is not None and unreal.GameplayStatics.get_world_delta_seconds(world) > 0.0
            if pawn is not None and ticking:
                ctx.world, ctx.pawn = world, pawn
                ctx.pc = unreal.GameplayStatics.get_player_controller(world, 0)
                unreal.log(f"[daltest] PIE up after {self.pie_wait} ticks: pawn {pawn.get_name()}, world {world.get_name()}")
                self.gen = self.scenario(ctx)
                self.stage = "run"
            elif self.pie_wait > 1200:
                raise RuntimeError("PIE did not start ticking (world delta seconds stayed 0)")
            return
        if self.stage == "run":
            if self.wait > 0:
                self.wait -= 1
                ctx.frame += 1
                return
            try:
                n = next(self.gen)
            except StopIteration:
                self.finish()
                return
            self.wait = max(int(n or 1) - 1, 0)
            ctx.frame += 1
            return
        if self.stage == "ending":
            self.end_ticks += 1
            ended = not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor()
            if ended or self.end_ticks > 600:
                self._unhook()
            return

    # ---- finishing ----------------------------------------------------------------------------------------------------------

    def finish(self):
        if self.stage == "ending":
            return
        ctx = self.ctx
        result = {
            "name": self.name,
            "error": self.error,
            "wall_seconds": round(time.time() - self.t_start, 2),
            "frames": ctx.records,
            "events": ctx.events,
            "notes": ctx.notes,
        }
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, f"{self.name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, default=str)
        unreal.log(f"[daltest] {self.name}: wrote {path} ({len(ctx.records)} frames){' WITH ERROR' if self.error else ''}")
        les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        if les.is_in_play_in_editor():
            les.editor_request_end_play()
        self.stage = "ending"
        self.end_ticks = 0

    def _unhook(self):
        if self.handle is not None:
            unreal.unregister_slate_post_tick_callback(self.handle)
            self.handle = None
        unreal.log(f"[daltest] {self.name}: done")
        if self.quit_when_done:
            unreal.EditorPythonScripting.set_keep_python_script_alive(False)


def load_module_info(dal_path):
    """The checked module of a .dal file (names, types, kind) -- needs the dal package on sys.path (DalLive put it there)."""
    import dal

    return dal.check_file(dal_path).module


def asset(path):
    a = unreal.load_asset(path)          # EditorAssetLibrary is refused while PIE runs
    if a is None:
        raise RuntimeError(f"cannot load {path}")
    return a


def add_component(pawn, cls, props, ctx, *, tick_after_controller=True):
    """Create a component on the already playing pawn with its properties set before it registers (deferred finish)."""
    comp = unreal.DalTestLibrary.create_component(pawn, cls)
    for k, v in props.items():
        comp.set_editor_property(k, v)
    unreal.DalTestLibrary.register_new_component(comp)
    if tick_after_controller:
        # a fixed order for both the compiled and the interpreted component: controller (input) and movement first
        comp.add_tick_prerequisite_actor(ctx.pc)
        cmc = pawn.get_component_by_class(unreal.CharacterMovementComponent)
        if cmc is not None:
            comp.add_tick_prerequisite_component(cmc)
    return comp
