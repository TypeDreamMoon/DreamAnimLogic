# Dumps the ALS data assets the way the engine loaded them (all properties, recursively) to JSON: run with the original ALS and with the generated one, diff the
# files with diff_dumps.py.  Output: $DUMP_OUT.  Run it with run_editor.ps1 (-Script this file).
import json
import os
import sys

sys.path.insert(0, "G:/DCC/AlsTruth/tools")
import unreal
from harness import Wait, run, log

OUT = os.environ.get("DUMP_OUT", "G:/DCC/scratch/h5/dump_settings.json")
ASSETS = [
    "/ALS/ALS/Data/Character/CS_Als_Default",
    "/ALS/ALS/Data/Character/Movement/MS_Als_Normal",
    "/ALS/ALS/Data/Character/Movement/MS_Als_Responsive",
    "/ALS/ALS/Data/Character/Mantle/MS_Als_High",
    "/ALS/ALS/Data/Character/Mantle/MS_Als_High_InAir",
    "/ALS/ALS/Data/Character/Mantle/MS_Als_Low",
    "/ALS/ALS/Data/Character/Mantle/MS_Als_Low_Box",
    "/ALS/ALS/Data/AnimationInstance/AIS_Als_Default",
]
SKIP = {"acquire_editor_element_handle", "call_method", "cast", "get_class", "get_default_object", "get_editor_property", "get_fname", "get_full_name",
        "get_name", "get_outer", "get_outermost", "get_package", "get_path_name", "get_typed_outer", "get_world", "is_editor_property_overridden",
        "is_package_external", "modify", "rename", "reset_editor_property", "set_editor_properties", "set_editor_property", "static_class"}


def names_of(o):
    return [n for n in dir(o) if not n.startswith("_") and n not in SKIP]


def conv(v, depth=0):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return round(v, 5)
    if isinstance(v, (list, tuple)):
        return [conv(x, depth + 1) for x in v]
    if isinstance(v, (set, frozenset)):
        return sorted(str(x) for x in v)
    if isinstance(v, dict):
        return {str(k): conv(x, depth + 1) for k, x in v.items()}
    if depth > 7:
        return "<deep>"
    if isinstance(v, unreal.Object):
        cls = v.get_class().get_name()
        path = v.get_path_name()
        if cls in ("AlsTurnInPlaceSettings",) or ":" in path.split(".", 1)[-1]:      # an instanced sub-object: its values
            return {"__class__": cls, **props(v, depth + 1)}
        return "<%s %s>" % (cls, path)
    if hasattr(v, "get_editor_property"):
        return {"__struct__": type(v).__name__, **props(v, depth + 1)}
    return str(v)


def props(o, depth):
    out = {}
    for n in names_of(o):
        try:
            x = o.get_editor_property(n)
        except Exception:  # noqa
            continue
        out[n] = conv(x, depth)
    return out


def main():
    yield Wait(8.0)
    res = {}
    for p in ASSETS:
        a = unreal.load_asset(p)
        res[p] = None if a is None else {"__class__": a.get_class().get_name(), **props(a, 0)}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    log("settings dump written")


run(main)
