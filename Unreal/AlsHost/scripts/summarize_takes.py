"""One row per take from the JSON files compare_takes.py wrote (--json <prefix><take>.json): python summarize_takes.py <prefix>

Columns: frames compared, largest location / velocity / yaw error, frames with a different movement mode, character fields whose discrete values
differ (and in how many frames), numeric character fields over tolerance, the pose error (translation cm mean / p95 / max, rotation deg mean / p95),
animation-instance fields over tolerance, the largest animation curve difference, and (when the takes have the reads of the recorder) how many values of
GetControlRigInput -- what the graph hands the foot IK of the Control Rig -- differ and by how much at most."""
import json
import sys

TAKES = ["lane", "speed_jumps", "stance_changes", "rotation_modes", "view_yaw_sweeps", "in_air", "view_mode", "roll", "mantle", "features"]
prefix = sys.argv[1]
rows = []
for t in TAKES:
    try:
        r = json.load(open(f"{prefix}{t}.json", encoding="utf-8"))
    except FileNotFoundError:
        continue
    a, p, c = r["actor"], r["pose"], r.get("curves", {})
    cd = [(k, v["differ"]) for k, v in r["char"].items() if v["kind"] == "disc" and v["differ"]]
    cn = [k for k, v in r["char"].items() if v["kind"] == "num" and v["max"] > 1e-2]
    an_num = sum(1 for v in r["anim"].values() if v["kind"] == "num" and v["max"] > 1e-3)
    an_disc = sum(1 for v in r["anim"].values() if v["kind"] == "disc" and v["differ"])
    rows.append((t, r["frames_compared"], round(a["location_error_max"], 2), round(a["velocity_error_max"], 2), round(a["yaw"]["max"], 1),
                 a["movement_mode"]["differ"], len(cd), sum(x[1] for x in cd), len(cn), round(p["translation_mean_cm"], 2), round(p["translation_p95_cm"], 1),
                 round(p["translation_max_cm"], 1), round(p["rotation_mean_deg"], 2), round(p["rotation_p95_deg"], 1), an_num, an_disc,
                 round(c.get("max", 0), 3),
                 sum(1 for v in r.get("rig_input", {}).values() if v["frames_over_tol"]) if r.get("rig_input") else "-",
                 round(max((v["max"] for v in r["rig_input"].values()), default=0), 3) if r.get("rig_input") else "-"))
header = ("take", "frames", "loc_max", "vel_max", "yaw_max", "mode_diff", "char_disc_fields", "char_disc_frames", "char_num_fields_off", "pose_t_mean",
          "pose_t_p95", "pose_t_max", "pose_r_mean", "pose_r_p95", "anim_num_off", "anim_disc_off", "curve_max", "rig_values_off", "rig_max")
print("\t".join(header))
for row in rows:
    print("\t".join(str(x) for x in row))
