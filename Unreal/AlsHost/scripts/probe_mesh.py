# Dumps the properties of the player's mesh component, its animation instance and its movement component in a running game world (PIE), as JSON, so that
# the original ALS and the generated one can be diffed (the mesh tick options, the update rate optimisation, the notify settings ...).
# Environment: DUMP_OUT.  Run with run_editor.ps1 (-Script this file -Fixed60 [-Rendered]); with R1_ORIGINAL=1 the game mode override is left out like g_record.py.
import json
import os
import sys

sys.path.insert(0, "G:/DCC/AlsTruth/tools")
import unreal
from harness import Wait, run, log, game_world

OUT = os.environ.get("DUMP_OUT", "G:/DCC/scratch/ueres_logs/probe_mesh.json")
ORIGINAL = os.environ.get("R1_ORIGINAL", "0") == "1"
SKIP = {"acquire_editor_element_handle", "call_method", "cast", "get_class", "get_default_object", "get_editor_property", "get_fname", "get_full_name", "get_name",
        "get_outer", "get_outermost", "get_package", "get_path_name", "get_typed_outer", "get_world", "is_editor_property_overridden", "is_package_external",
        "modify", "rename", "reset_editor_property", "set_editor_properties", "set_editor_property", "static_class"}


def conv(v, depth=0):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return round(v, 5)
    if isinstance(v, (list, tuple)):
        return [conv(x, depth + 1) for x in v[:20]]
    if isinstance(v, unreal.Object):
        return "<%s %s>" % (v.get_class().get_name(), v.get_name())
    try:
        if depth < 2 and hasattr(v, "get_editor_property"):
            return {n: conv(safe(v, n), depth + 1) for n in dir(v) if not n.startswith("_") and n not in SKIP and not callable(safe_attr(v, n))}
    except Exception:  # noqa
        pass
    return str(v)


def safe_attr(o, n):
    try:
        return getattr(o, n)
    except Exception:  # noqa
        return None


def safe(o, n):
    try:
        return o.get_editor_property(n)
    except Exception:  # noqa
        return "<unreadable>"


def dump(o):
    out = {"__class__": o.get_class().get_name()}
    for n in dir(o):
        if n.startswith("_") or n in SKIP or callable(safe_attr(o, n)):
            continue
        v = safe(o, n)
        if isinstance(v, str) and v == "<unreadable>":
            continue
        try:
            out[n] = conv(v)
        except Exception:  # noqa
            out[n] = str(v)
    return out


def main():
    import dreamved_unreal.anim_record as ar
    yield Wait(6.0)
    if not ORIGINAL:
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        mode = unreal.load_class(None, "/Script/DreamAnimTestHost.DalAlsHostGameMode")
        world.get_world_settings().set_editor_property("default_game_mode", mode)
    ar.begin_play()
    yield Wait(until=lambda: game_world() is not None, timeout=120)
    yield Wait(4.0)
    ch = unreal.GameplayStatics.get_player_character(game_world(), 0)
    mesh = ch.get_component_by_class(unreal.SkeletalMeshComponent)
    res = {"actor": ch.get_class().get_name(), "mesh": dump(mesh), "anim": dump(mesh.get_anim_instance()), "cmc": dump(ch.character_movement)}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, sort_keys=True, default=str)
    log("probe written")
    ar.end_play()
    yield Wait(until=lambda: game_world() is None, timeout=60)


run(main)
