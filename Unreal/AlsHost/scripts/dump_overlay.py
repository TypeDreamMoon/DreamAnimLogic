# Dumps what B_Als_Character (the Blueprint on top of AAlsCharacterExample) adds to the ALS character: the overlay animation instance classes (the map the
# Blueprint's OnOverlayModeChanged picks the linked layer class from), the other Blueprint variables, and -- as far as the editor's Python reaches --
# the nodes of its event and function graphs.  Run with the ORIGINAL ALS in the host (host_als.py restore), with run_editor.ps1 -Script this file.
# Output: $DUMP_OUT (JSON).
import json
import os
import sys

sys.path.insert(0, "G:/DCC/AlsTruth/tools")
import unreal
from harness import Wait, run, log

OUT = os.environ.get("DUMP_OUT", "G:/DCC/scratch/ueres_orig/overlay.json")
BP = "/ALS/ALS/Character/B_Als_Character"


def conv(v, depth=0):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return round(v, 5)
    if isinstance(v, (list, tuple)):
        return [conv(x, depth + 1) for x in v]
    if isinstance(v, dict):
        return {str(k): conv(x, depth + 1) for k, x in v.items()}
    if isinstance(v, unreal.Class):
        return "<class %s>" % v.get_path_name()
    if isinstance(v, unreal.Object):
        return "<%s %s>" % (v.get_class().get_name(), v.get_path_name())
    return str(v)


def main():
    yield Wait(8.0)
    res = {}
    cls = unreal.load_class(None, BP + "." + "B_Als_Character_C")
    cdo = unreal.get_default_object(cls)
    res["class"] = cls.get_path_name()
    names = [n for n in dir(cdo) if not n.startswith("_")]
    props = {}
    base_class = unreal.load_class(None, "/Script/ALSExtras.AlsCharacterExample")
    base = set(dir(unreal.get_default_object(base_class))) if base_class else set()
    res["blueprint_names"] = sorted(n for n in names if n not in base)           # what the Blueprint itself adds (variables, functions)
    for n in res["blueprint_names"]:
        try:
            v = getattr(cdo, n)
            if callable(v):
                continue
        except Exception:  # noqa
            continue
        try:
            props[n] = conv(cdo.get_editor_property(n))
        except Exception as e:  # noqa
            props[n] = "ERR %s" % e
    res["blueprint_props"] = props
    for n in ("OverlayAnimationInstanceClasses", "OverlayMode"):
        try:
            res["by_name_" + n] = conv(cdo.get_editor_property(n))
        except Exception as e:  # noqa
            res["by_name_" + n] = "ERR %s" % e
    bp = unreal.load_asset(BP)
    graphs = {}
    try:
        lib = unreal.BlueprintEditorLibrary
        for gname in ("EventGraph", "RefreshOverlayLinkedAnimationLayer", "RefreshOverlayObject", "SelectMantlingSettings", "AttachOverlayObject", "ClearOverlayObject"):
            g = lib.find_graph(bp, gname) if gname != "EventGraph" else lib.find_event_graph(bp)
            if g is None:
                graphs[gname] = None
                continue
            entry = {"class": g.get_class().get_name()}
            try:
                nodes = g.get_editor_property("nodes")
                entry["nodes"] = [{"class": n.get_class().get_name(), "title": str(n.get_node_title(unreal.NodeTitleType.FULL_TITLE)) if hasattr(n, "get_node_title") else "", "name": n.get_name()} for n in nodes]
            except Exception as e:  # noqa
                entry["nodes_error"] = str(e)
            graphs[gname] = entry
    except Exception as e:  # noqa
        graphs["error"] = str(e)
    res["graphs"] = graphs
    # the exported text of the class default and the function list of the generated class
    try:
        res["functions"] = sorted(n for n in dir(cdo) if not n.startswith("_") and "overlay" in n.lower())
    except Exception as e:  # noqa
        res["functions_error"] = str(e)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, sort_keys=True)
    log("overlay dump written")


run(main)
