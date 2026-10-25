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
# Environment: as r1_record.py (R1_TAKES, R1_OUT, R1_RES, R1_SUFFIX, R1_TRUTH).
import os
import sys

TOOLS = "G:/DCC/AlsTruth/tools"
sys.path.insert(0, TOOLS)
import unreal
from harness import Wait, log

R1 = TOOLS + "/r1_record.py"
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
        if not comps:
            raise RuntimeError("the pawn %s has no %s component" % (actor.get_name(), LOGIC_CLASS))
        object.__setattr__(self, "_logic", comps[0])
        # the host editor runs without a renderer (-nullrhi, no window), where a mesh counts as not rendered and ticks only its montages: the original
        # takes were made with a rendered mesh, whose animation ticks fully
        for mesh in actor.get_components_by_class(unreal.SkeletalMeshComponent):
            mesh.set_editor_property("visibility_based_anim_tick_option", unreal.VisibilityBasedAnimTickOption.ALWAYS_TICK_POSE_AND_REFRESH_BONES)

    def __getattr__(self, name):
        if name in LOGIC_METHODS:
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


def load_driver():
    src = open(R1, encoding="utf-8").read()
    tail = "run(steps)"
    if not src.rstrip().endswith(tail):
        raise RuntimeError("r1_record.py does not end with run(steps)")
    src = src.rstrip()[: -len(tail)]
    ns = {"__name__": "r1_record_generated", "__file__": R1}
    exec(compile(src, R1, "exec"), ns)
    ns["unreal"] = _UnrealShim()

    real_compile = ns["ar"].compile_blueprints

    class _Ar:
        def __getattr__(self, name):
            return getattr(ns["ar_real"], name)

        @staticmethod
        def compile_blueprints(folder):
            paths = ns["ar_real"].asset_paths_from_dab(folder)
            paths = [p for p in paths if "ALSCamera" not in p and "PostProcessing" not in p]
            return real_compile(paths)

    ns["ar_real"] = ns["ar"]
    ns["ar"] = _Ar()
    return ns


def main():
    ns = load_driver()
    yield Wait(6.0)
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
