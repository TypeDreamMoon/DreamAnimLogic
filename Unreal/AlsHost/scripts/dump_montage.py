# Dumps the notifies of a montage asset the way the engine loaded them (class, name, time, tick type): which notify objects survive loading in this build.
# Environment: DUMP_OUT (JSON), DUMP_MONTAGE (asset path).  Run with run_editor.ps1 (-Script this file).
import json
import os
import sys

sys.path.insert(0, "G:/DCC/AlsTruth/tools")
import unreal
from harness import Wait, run, log

OUT = os.environ.get("DUMP_OUT", "G:/DCC/scratch/ueres_logs/montage.json")
PATH = os.environ.get("DUMP_MONTAGE", "/ALS/ALS/Character/Actions/AM_Als_Roll")


def main():
    yield Wait(8.0)
    m = unreal.load_asset(PATH)
    res = {"asset": PATH, "loaded": m is not None}
    if m is not None:
        res["class"] = m.get_class().get_name()
        rows = []
        for n in unreal.AnimationLibrary.get_animation_notify_events(m):
            row = {}
            for key in ("notify_name", "trigger_time_offset", "notify_trigger_chance", "montage_tick_type", "notify_filter_type", "notify_filter_lod",
                        "trigger_weight_threshold", "notify_state_class", "notify", "link", "display_time"):
                try:
                    v = n.get_editor_property(key)
                    row[key] = str(v) if not isinstance(v, (int, float, bool, str)) else v
                except Exception as e:  # noqa
                    row[key] = "ERR"
            try:
                row["notify_class"] = n.notify.get_class().get_name() if n.notify else None
            except Exception:  # noqa
                pass
            rows.append(row)
        res["notifies"] = rows
        res["length"] = m.get_play_length()
        try:
            res["slots"] = [str(x) for x in unreal.AnimationLibrary.get_montage_slot_names(m)]
        except Exception as e:  # noqa
            res["slots"] = "ERR %s" % e
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    log("montage dump written")


run(main)
