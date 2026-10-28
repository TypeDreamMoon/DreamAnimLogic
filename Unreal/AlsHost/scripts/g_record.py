# Records the truth takes of G:\DCC\AlsTruth again, with the ALS logic generated from als/*.dal instead of ALS-Refactored's C++.
#
# Run inside the host editor (see record_takes.ps1):  -ExecCmds="py <this file>"
#
# The routes, the driver commands and the recorder calls are the ones of the original takes: this script executes
# G:/DCC/AlsTruth/tools/r1_record.py (its last line, run(steps), left out) and changes three things around it:
#   * the world settings of the editor world get the game mode override ADalAlsHostGameMode (not saved: PIE copies the world), so the player
#     is ADalAlsHostCharacter -- a plain character with the generated components -- instead of B_Als_Character;
#   * the calls the driver makes on the character that were methods of AAlsCharacter (set_desired_gait, jump, start_rolling_grounded ...) go to
#     the UAlsCharacter component of the pawn, everything else to the pawn itself;
#   * the Animation Blueprints of the camera are not compiled (their parent class lives in ALSCamera, which is switched off).
#   * it adds the "ragdoll" take (start_ragdolling / stop_ragdolling in a run, at rest and in the air) and the two driver commands it needs; the
#     other takes are untouched, so their routes are still the ones of the original truth takes.
# R1_ORIGINAL=1 runs the same driver against the ORIGINAL ALS (host_als.py restore; B_Als_Character is the pawn): no game mode override, the driver's
# calls go straight to the character.  That is how the ragdoll take of the original and the original's takes with the current recorder are made.
# R1_PHASE=<json from phase_ref.py> starts each take with the animation instance at the same point of its 160-frame loop as the reference take did:
# the pose of a run depends on how many animation updates the instance had before the first recorded frame (Anim.UpdateCounter), because the idle
# and the locomotion loops of the graph run for 160 frames (2.67 s) -- two runs whose counters differ by a multiple of 160 give the same pose to
# 0.00-0.15 cm, whatever else differs, and otherwise they differ by up to 2.4 cm.  The wait is added before the recorder starts.  (The counter is
# not readable from Python; at a fixed 60 fps it is the world time in frames minus one or two, so the wait goes by the world time.)
# R1_PHASE_EXACT=1 with R1_PHASE starts at the reference's absolute world time instead (a number > 1: at that world frame, e.g. 900 when the editor is slower than the reference) (the ragdoll take: Chaos repeats bit for bit only from the same absolute time).
# Environment: as r1_record.py (R1_TAKES, R1_OUT, R1_RES, R1_SUFFIX, R1_TRUTH), and R1_ORIGINAL, R1_RENDERED, R1_PHASE, R1_PHASE_OFFSET, R1_PHASE_EXACT.
import os
import sys

TOOLS = "G:/DCC/AlsTruth/tools"
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.dirname(os.path.abspath(globals().get("__file__", "I:/DCC/DreamAnimLogic/Unreal/AlsHost/scripts/g_record.py"))))
import unreal
import als_takes
import base_takes
from harness import Wait, log

R1 = TOOLS + "/r1_record.py"
ORIGINAL = os.environ.get("R1_ORIGINAL", "0") == "1"
RENDERED = os.environ.get("R1_RENDERED", "0") == "1"
PHASE_FILE = os.environ.get("R1_PHASE", "")
PHASE_OFFSET = int(os.environ.get("R1_PHASE_OFFSET", "0"))       # updates between the wait's check and the first recorded frame (a constant of the driver; see phase_ref.py)
PHASE_LOOP = 160
PHASE_EXACT = float(os.environ.get("R1_PHASE_EXACT", "0"))            # 1: start at the reference take's absolute world time instead of the same point of the 160-frame loop; > 1: at that world frame
FPS = float(os.environ.get("R1_FPS", "60"))                      # the fixed frame rate of this editor run (-fps=<n>, run_editor.ps1 -Fps): the phase wait needs its step
GAME_MODE = "/Script/DreamAnimTestHost.DalAlsHostGameMode"
LOGIC_CLASS = "AlsCharacter"
# what the driver calls on the character and AAlsCharacter had (the generated component has them as BlueprintCallable methods)
LOGIC_METHODS = {"set_desired_gait", "set_desired_stance", "set_desired_rotation_mode", "set_overlay_mode", "set_desired_aiming", "set_view_mode",
                 "jump", "start_rolling_grounded", "start_rolling", "start_mantling", "start_ragdolling", "stop_ragdolling"}


class AlsPawn:
    """The pawn for everything but the methods of the ALS logic, which go to its component."""

    def __init__(self, actor):
        object.__setattr__(self, "_actor", actor)
        comps = [c for c in actor.get_components_by_class(unreal.ActorComponent) if c.get_class().get_name() == LOGIC_CLASS]
        if not comps and not ORIGINAL:
            raise RuntimeError("the pawn %s has no %s component" % (actor.get_name(), LOGIC_CLASS))
        object.__setattr__(self, "_logic", comps[0] if comps else None)
        # the host editor runs without a renderer (-nullrhi, no window), where a mesh counts as not rendered and ticks only its montages: the original
        # takes were made with a rendered mesh, whose animation ticks fully
        # (a rendered run has a renderer, the mesh counts as rendered and ticks like it did in the original takes: leave the options alone)
        if not RENDERED:
            for mesh in actor.get_components_by_class(unreal.SkeletalMeshComponent):
                mesh.set_editor_property("visibility_based_anim_tick_option", unreal.VisibilityBasedAnimTickOption.ALWAYS_TICK_POSE_AND_REFRESH_BONES)

    def __getattr__(self, name):
        if name in LOGIC_METHODS and self._logic is not None:
            return getattr(self._logic, name)
        return getattr(self._actor, name)


class _GameplayStatics:
    def __getattr__(self, name):
        return getattr(unreal.GameplayStatics, name)

    @staticmethod
    def get_player_character(world, index):
        actor = unreal.GameplayStatics.get_player_character(world, index)
        return AlsPawn(actor) if actor is not None else None


class _UnrealShim:
    """The unreal module with GameplayStatics.get_player_character returning an AlsPawn."""
    GameplayStatics = _GameplayStatics()

    def __getattr__(self, name):
        return getattr(unreal, name)


# The ragdoll route: a ragdoll from a run, from standing and from a jump, each followed by the get up.  step(...) keys: ragdoll -> start_ragdolling,
# get_up -> stop_ragdolling (which plays the get up montage when the ragdoll lies on the ground).
RAGDOLL_STEPS = """
RAGDOLL = [
    step("idle", 1.5),
    step("run", 2.0, gait="Running", move=(1, 0)),
    step("ragdoll_run", 4.0, ragdoll=True, move=(0, 0)),
    step("get_up", 4.5, get_up=True),
    step("run_again", 2.0, move=(1, 0)),
    step("stop", 1.5, move=(0, 0)),
    step("ragdoll_idle", 3.5, ragdoll=True),
    step("get_up_2", 4.5, get_up=True),
    step("jump_ragdoll", 5.0, jump=True, at=[(0.5, {"ragdoll": True})]),
    step("get_up_3", 4.5, get_up=True),
    step("end", 1.5),
]

"""


def add_ragdoll_take(src):
    """The driver source with the ragdoll take and its two commands added (each anchor must be there once)."""
    nl = chr(10)
    edits = [
        ("SCRIPTED = {" + nl, RAGDOLL_STEPS + "SCRIPTED = {" + nl),
        ('    "mantle": (MANTLE, "mantling over a 130 cm box"),' + nl + "}",
         '    "mantle": (MANTLE, "mantling over a 130 cm box"),' + nl
         + '    "ragdoll": (RAGDOLL, "ragdolling from a run, at rest and in the air, with the get up"),' + nl + "}"),
        ('        if c.get("roll"):' + nl + "            ch.start_rolling_grounded(1.0)" + nl + '            cmd("start_rolling_grounded", play_rate=1.0)' + nl,
         '        if c.get("roll"):' + nl + "            ch.start_rolling_grounded(1.0)" + nl + '            cmd("start_rolling_grounded", play_rate=1.0)' + nl
         + '        if c.get("ragdoll"):' + nl + "            ch.start_ragdolling()" + nl + '            cmd("start_ragdolling")' + nl
         + '        if c.get("get_up"):' + nl + "            ch.stop_ragdolling()" + nl + '            cmd("stop_ragdolling")' + nl),
    ]
    for old, new in edits:
        if src.count(old) != 1:
            raise RuntimeError("r1_record.py has changed: anchor not found once: " + old[:50])
        src = src.replace(old, new)
    return src


def add_phase_alignment(src):
    """The driver source with a wait before the recorder starts: until the animation instance's update counter is where the reference take's was."""
    anchor = '    rec = ar.start(WATCH, entry="AB_Als", mesh=mesh,'
    if src.count(anchor) != 1:
        raise RuntimeError("r1_record.py has changed: anchor not found once: " + anchor)
    return src.replace(anchor, '    yield Wait(until=lambda: _phase_aligned(mesh, name), timeout=30)' + chr(10) + anchor)


def phase_aligned(mesh, take):
    import json
    ref = json.load(open(PHASE_FILE, encoding="utf-8")).get(take)
    if ref is None:
        return True
    frames = unreal.GameplayStatics.get_time_seconds(mesh.get_world()) * 60.0      # (frames of 1/60 s; a float: other frame rates fall between them)
    if PHASE_EXACT:
        # the recording starts at the reference's own world time: a physics take (the ragdoll) does not repeat when it starts a loop (160 frames) later, only when it
        # starts at the same absolute time (float32 times in the solver: UE's own Chaos ragdoll differs 3-5 cm between runs that start at world frame 591 and 751, and is
        # bit-identical between two that start at 591).  If the editor is already past it the take starts at once and the log says so.
        want = float(ref) + PHASE_OFFSET if PHASE_EXACT <= 1.0 else PHASE_EXACT
        if frames > want + 0.25 * 60.0 / FPS:
            log("R1_PHASE_EXACT: the world is at frame %.2f, past the reference start %.2f: the take starts now" % (frames, want))
            return True
        return abs(frames - want) < 0.25 * 60.0 / FPS
    r = (frames - float(ref) + PHASE_OFFSET) % PHASE_LOOP
    tol = 0.25 * 60.0 / FPS                                                      # a quarter of a step of this run
    return r < tol or PHASE_LOOP - r < tol


def load_driver():
    src = open(R1, encoding="utf-8").read()
    tail = "run(steps)"
    if not src.rstrip().endswith(tail):
        raise RuntimeError("r1_record.py does not end with run(steps)")
    src = src.rstrip()[: -len(tail)]
    src = add_ragdoll_take(src)
    src = base_takes.add_base_takes(src)
    src = als_takes.add_als_takes(src)
    if PHASE_FILE:
        src = add_phase_alignment(src)
    ns = {"__name__": "r1_record_generated", "__file__": R1}
    ns["_phase_aligned"] = phase_aligned
    ns["BASE"] = base_takes
    exec(compile(src, R1, "exec"), ns)
    ns["unreal"] = _UnrealShim()

    real_compile = ns["ar"].compile_blueprints

    class _Ar:
        def __getattr__(self, name):
            return getattr(ns["ar_real"], name)

        @staticmethod
        def compile_blueprints(folder):
            paths = ns["ar_real"].asset_paths_from_dab(folder)
            if not ORIGINAL:
                paths = [p for p in paths if "ALSCamera" not in p and "PostProcessing" not in p]
            return real_compile(paths)

    ns["ar_real"] = ns["ar"]
    ns["ar"] = _Ar()
    return ns


def main():
    ns = load_driver()
    yield Wait(6.0)
    if not ORIGINAL:
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        mode = unreal.load_class(None, GAME_MODE)
        if mode is None:
            raise RuntimeError("cannot load " + GAME_MODE)
        world.get_world_settings().set_editor_property("default_game_mode", mode)
        log("game mode override: %s" % mode.get_path_name())
    for w in ns["steps"]():
        yield w


from harness import run  # noqa: E402

run(main)
