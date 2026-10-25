# PIE checks of what DalRuntime does that the recordings do not show: the hook events reach a sink in another module, the ragdoll service,
# the root motion override and the movement mode lock / input block on the real character.  Run in the host editor (run_checks.ps1).
#
# Result: <DALCHK_OUT> (JSON) with one entry per check: {"ok": bool, ...numbers}.
import json
import os
import sys
import traceback

sys.path.insert(0, "G:/DCC/AlsTruth/tools")
import unreal
from harness import Wait, run, log, game_world

OUT = os.environ.get("DALCHK_OUT", "G:/DCC/scratch/h5/runtime_checks.json")
GAME_MODE = "/Script/DreamAnimTestHost.DalAlsHostGameMode"
RES = {}


def probe(ch):
    return [c for c in ch.get_components_by_class(unreal.ActorComponent) if c.get_class().get_name() == "DalHookProbe"][0]


def logic(ch):
    return [c for c in ch.get_components_by_class(unreal.ActorComponent) if c.get_class().get_name() == "AlsCharacter"][0]


def make_tag(name):
    t = unreal.GameplayTag()
    t.import_text('(TagName="%s")' % name)
    return t


def gwait(seconds):
    """Waits for game time, not for the editor's wall clock: with -benchmark -fps=60 the game runs faster than real time."""
    t = 0.0
    while t < seconds:
        yield Wait(0.0)
        t += unreal.GameplayStatics.get_world_delta_seconds(game_world())


def frames(n):
    for _ in range(n):
        yield Wait(0.0)


def check(name, ok, **info):
    RES[name] = {"ok": bool(ok), **info}
    log("check %s: %s %s" % (name, "ok" if ok else "FAILED", json.dumps(info, default=str)[:300]))


def main():
    yield Wait(6.0)
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    world.get_world_settings().set_editor_property("default_game_mode", unreal.load_class(None, GAME_MODE))
    # a bare DalTestCharacter 1 km up: it falls for 14 s, long enough to crouch it in the air (PIE copies the editor world; the actor is never saved)
    tclass = unreal.load_class(None, "/Script/DalRuntime.DalTestCharacter")
    bare_editor = unreal.EditorLevelLibrary.spawn_actor_from_class(tclass, unreal.Vector(5000, 5000, 100000), unreal.Rotator(0, 0, 0))
    unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_request_begin_play()
    yield Wait(until=lambda: game_world() is not None, timeout=120)
    yield Wait(3.0)
    w = game_world()
    ch = unreal.GameplayStatics.get_player_character(w, 0)
    pr = probe(ch)
    als = logic(ch)
    cmc = ch.get_editor_property("character_movement")
    mesh = ch.get_component_by_class(unreal.SkeletalMeshComponent)
    # no renderer here (-nullrhi): a mesh that is not "rendered" would not refresh its bones, so the physics bodies could not be read through sockets
    mesh.set_editor_property("visibility_based_anim_tick_option", unreal.VisibilityBasedAnimTickOption.ALWAYS_TICK_POSE_AND_REFRESH_BONES)
    try:
        # ---- a bare DalTestCharacter crouching in the air (the c3 scenario als_crouch_air): the capsule center jumps down by the half height difference
        bare = [a for a in unreal.GameplayStatics.get_all_actors_of_class(w, unreal.load_class(None, "/Script/DalRuntime.DalTestCharacter")) if a != ch][0]
        bcmc = bare.get_editor_property("character_movement")
        zs = []
        for i in range(10):
            if i == 4:
                bare.crouch(False)
            zs.append(bare.get_actor_location().z)
            yield Wait(0.0)
        jumps = [zs[i + 1] - zs[i] for i in range(len(zs) - 1)]
        check("bare_character_crouch_in_the_air_keeps_the_base", min(jumps) < jumps[0] - 25.0, steps=[round(j, 2) for j in jumps],
              mode=str(bcmc.get_editor_property("movement_mode")), flag=bcmc.get_editor_property("crouch_maintains_base_location"),
              crouched=bare.get_editor_property("is_crouched"))

        # ---- hook events -------------------------------------------------------------------------------------------------------------
        yield from gwait(1.0)
        pr.reset_counts()
        yield from gwait(0.5)
        check("physics_rotation_every_frame", pr.get_editor_property("physics_rotation") > 10, count=pr.get_editor_property("physics_rotation"),
              delta=pr.get_editor_property("last_physics_rotation_delta"))
        pr.reset_counts()
        als.jump()
        yield from gwait(0.1)
        yield from gwait(0.2)
        jumped = pr.get_editor_property("jumped")
        modes = (pr.get_editor_property("last_mode_from"), pr.get_editor_property("last_mode_to"))
        check("jumped_and_mode_changed_to_falling", jumped == 1 and pr.get_editor_property("mode_changed") >= 1 and modes[1] == 3, jumped=jumped, modes=modes,
              log=list(pr.get_editor_property("log")))
        yield from gwait(2.0)
        check("landed_with_hit", pr.get_editor_property("landed") == 1 and pr.get_editor_property("last_landed_blocking")
              and pr.get_editor_property("last_landed_has_primitive") and pr.get_editor_property("last_landed_normal_z") > 0.9,
              landed=pr.get_editor_property("landed"), normal_z=pr.get_editor_property("last_landed_normal_z"), log=list(pr.get_editor_property("log")))
        pr.reset_counts()
        als.set_desired_stance(make_tag("Als.Stance.Crouching"))
        yield from gwait(0.5)
        als.set_desired_stance(make_tag("Als.Stance.Standing"))
        yield from gwait(0.5)
        check("crouch_started_and_ended", pr.get_editor_property("crouch_started") == 1 and pr.get_editor_property("crouch_ended") == 1,
              started=pr.get_editor_property("crouch_started"), ended=pr.get_editor_property("crouch_ended"))
        # ALS: after every mode change the crouch keeps the base location (the engine clears it outside Walking)
        ch.launch_character(unreal.Vector(0, 0, 600), False, False)
        yield from gwait(0.2)
        check("crouch_maintains_base_location_in_the_air", cmc.get_editor_property("movement_mode") == unreal.MovementMode.MOVE_FALLING
              and bool(cmc.get_editor_property("crouch_maintains_base_location")), mode=str(cmc.get_editor_property("movement_mode")),
              flag=cmc.get_editor_property("crouch_maintains_base_location"), always=cmc.get_editor_property("crouch_maintains_base_location_always"))
        yield from gwait(2.0)
        pr.reset_counts()
        loc = ch.get_actor_location()
        ch.set_actor_location(loc + unreal.Vector(300, 0, 0), False, True)
        yield from gwait(0.2)
        check("teleported", pr.get_editor_property("teleported") >= 1, count=pr.get_editor_property("teleported"))
        pr.reset_counts()
        pr.post_test_notify("TestNotify")
        check("notify_through_router", pr.get_editor_property("notifies") == 1, log=list(pr.get_editor_property("log")))

        # ---- movement mode lock and input block -----------------------------------------------------------------------------------------
        yield from gwait(1.0)
        start = ch.get_actor_location()
        ch.set_input_blocked(True)
        for _ in range(60):
            ch.add_movement_input(unreal.Vector(1, 0, 0), 1.0, False)
            yield Wait(0.0)
        moved = (ch.get_actor_location() - start).length()
        ch.set_input_blocked(False)
        check("input_blocked_no_movement", moved < 1.0, moved=moved)
        yield from gwait(0.5)
        ch.set_movement_mode_locked(True)
        z0 = ch.get_actor_location().z
        ch.launch_character(unreal.Vector(0, 0, 800), False, False)
        yield from gwait(0.5)
        locked_z = ch.get_actor_location().z - z0
        ch.set_movement_mode_locked(False)
        check("movement_mode_locked_launch_has_no_effect", locked_z < 20.0, rise=locked_z)
        yield from gwait(1.5)

        # ---- root motion override ----------------------------------------------------------------------------------------------------------
        start = ch.get_actor_location()
        ch.root_motion_override(unreal.Vector(300, 0, 0), unreal.Quat(0, 0, 0, 1))
        for _ in range(30):
            yield Wait(0.0)
            ch.root_motion_override(unreal.Vector(300, 0, 0), unreal.Quat(0, 0, 0, 1))
        moved = (ch.get_actor_location() - start).x
        ch.root_motion_clear()
        check("root_motion_override_moves", 120.0 < moved < 180.0, moved_x=moved, expected="about 150 cm in 0.5 s at 300 cm/s")

        # ---- ragdoll ------------------------------------------------------------------------------------------------------------------------
        rag = [c for c in ch.get_components_by_class(unreal.ActorComponent) if c.get_class().get_name() == "DalRagdollService"][0]
        supported = rag.is_supported()
        pelvis0 = mesh.get_socket_location("pelvis")
        rag.begin(True, 400.0)
        yield from gwait(0.1)
        simulating = mesh.is_any_simulating_physics()
        attached = mesh.get_attach_parent() is not None
        yield from gwait(1.5)
        pelvis1 = mesh.get_socket_location("pelvis")
        check("ragdoll_begin", supported and simulating and not attached and rag.is_ragdolling(), supported=supported, simulating=simulating, attached=attached)
        check("ragdoll_falls", pelvis1.z < pelvis0.z - 30.0, pelvis_z_before=pelvis0.z, pelvis_z_after=pelvis1.z)
        rag.add_force(unreal.Vector(0, 0, 1000), False)
        yield from gwait(0.1)
        rag.end()
        yield from gwait(0.2)
        check("ragdoll_end", (not mesh.is_any_simulating_physics()) and mesh.get_attach_parent() is not None and not rag.is_ragdolling(),
              simulating=mesh.is_any_simulating_physics(), attached=mesh.get_attach_parent() is not None)
    except Exception:
        RES["exception"] = traceback.format_exc()
        log("EXCEPTION\n" + RES["exception"])
    unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_request_end_play()
    yield Wait(until=lambda: game_world() is None, timeout=60)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(RES, fh, indent=1, default=str)
    log("checks written " + OUT)


run(main)
