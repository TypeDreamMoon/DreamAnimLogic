#!/usr/bin/env python
"""Compares a take recorded with the ALS logic generated from als/*.dal against the original-ALS truth take of the same route.

    python compare_takes.py <gen.dvrec> <truth.dvrec> [--segments <r1_info_<take>.json>] [--json out.json] [--md out.md]

Both takes drove the same scripted inputs (G:/DCC/AlsTruth/tools/r1_record.py) at a fixed 60 Hz, so frame f is the same moment of the
route in both; the comparison is free-running (nothing is forced from the truth), frame by frame:

  * the actor: location and yaw, the movement component's velocity and mode;
  * the character state: Char.* scalars of the `final` channel (the original's AAlsCharacter members, the generated component's states);
    the keys the generated side keeps elsewhere are mapped (ALIASES);
  * the animation instance: Anim.* of the `final` channel, grouped by state struct;
  * what the movement component was given: Cmc.MaxWalkSpeed, MaxAccelerationWalking, ..., at `post_cmc` and `post_char`;
  * the output: the component-space pose of every bone (translation in cm, rotation in degrees) and the animation curves.

Numbers per field: largest absolute difference, RMS, the frame of the largest and the first frame above a tolerance; for enums, tags, bools:
number of frames that differ and the first one.  `--segments` (the info file the recorder driver wrote, or the manifest of the truth takes)
splits the pose / location columns by route segment.

Reads recordings with dreamved.anim.recording from the Blender fork (read only; numpy needed).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, r"I:/DCC/blender-moon-engine/scripts/modules")
from dreamved.anim.recording import Recording  # noqa: E402

# the original keeps these in the movement component; the generated logic has them as states of its own
ALIASES = {
    "Cmc.GaitAmount": "Char.GaitAmount",
    "Cmc.MaxAllowedGait": "Char.MaxAllowedGait",
    "Cmc.RotationMode": "Char.RotationMode",
    "Cmc.Stance": "Char.Stance",
    # the pushed value and the one in effect (DalCharacterMovementComponent.h): the original's property changes when PhysWalking starts
    "Cmc.MaxAccelerationWalking": "Cmc.MaxAccelerationWalkingApplied",
}
# movement component values the logic pushes (compared at post_cmc, where the character logic reads them, and at final)
PUSHED = ["Cmc.MaxWalkSpeed", "Cmc.MaxWalkSpeedCrouched", "Cmc.MaxAccelerationWalking", "Cmc.BrakingDecelerationWalking", "Cmc.GroundFriction",
          "Cmc.BrakingFrictionFactor", "Cmc.GaitAmount", "Cmc.MaxAllowedGait", "Cmc.MaxAcceleration"]
ANGLE_WORDS = ("Angle", "Yaw", "Pitch", "Roll", "Rotation")
TOLERANCE = {"default": 1e-3}


def wrap(d):
    return (d + 180.0) % 360.0 - 180.0


class Take:
    def __init__(self, path):
        self.path = path
        self.rec = Recording.load(path)
        self.t = self.rec.truth
        if self.t is None:
            raise SystemExit(f"{path}: no truth channels")
        self.paths = {ch: set(self.t.paths(ch)) for ch in self.t.channels}

    def has(self, ch, p):
        return p in self.paths[ch]

    def series(self, ch, p):
        return self.t.series(ch, p)

    def type_of(self, ch, p):
        return self.t.key_info(ch, p)["t"]


def key_pair(gen: Take, truth: Take, ch: str, p: str):
    """(truth path, gen path) for one logical key, or None when a side lacks it."""
    gp = ALIASES.get(p, p)
    return (p, gp) if truth.has(ch, p) and gen.has(ch, gp) else None


def num(a):
    return np.asarray(a, dtype=float)


def stats(a, b, angle=False, tol=1e-3):
    d = num(a) - num(b)
    if angle:
        d = wrap(d)
    ok = ~np.isnan(d)
    if not ok.any():
        return None
    ad = np.abs(d[ok])
    idx = np.flatnonzero(ok)
    worst = int(idx[np.argmax(ad)])
    over = np.flatnonzero(ok & (np.abs(np.nan_to_num(d)) > tol))
    return {"max": float(ad.max()), "rms": float(np.sqrt((ad ** 2).mean())), "mean": float(ad.mean()), "worst_frame": worst,
            "first_over": int(over[0]) if over.size else None, "n_over": int(over.size), "n": int(ok.sum())}


def discrete(a, b):
    a = np.asarray(a, dtype=object)
    b = np.asarray(b, dtype=object)
    same = np.array([(x == y) or (x is None and y is None) or (isinstance(x, float) and isinstance(y, float) and (x == y or (math.isnan(x) and math.isnan(y))))
                     for x, y in zip(a, b)])
    bad = np.flatnonzero(~same)
    return {"differ": int(bad.size), "first": int(bad[0]) if bad.size else None, "n": int(len(a)),
            "first_values": [str(a[bad[0]]), str(b[bad[0]])] if bad.size else None}


def compare_keys(gen: Take, truth: Take, ch: str, paths, n: int, prefix_filter=None):
    out = {}
    for p in paths:
        pair = key_pair(gen, truth, ch, p)
        if pair is None:
            continue
        tp, gp = pair
        kind_t, kind_g = truth.type_of(ch, tp), gen.type_of(ch, gp)
        a, b = truth.series(ch, tp)[:n], gen.series(ch, gp)[:n]
        if kind_t in "fi" and kind_g in "fi":
            angle = any(w in p for w in ANGLE_WORDS) and "Amount" not in p and "Speed" not in p
            s = stats(a, b, angle=angle, tol=TOLERANCE["default"])
            if s:
                out[p] = {"kind": "num", **s}
        elif kind_t == kind_g or {kind_t, kind_g} <= {"b", "f", "i"}:
            out[p] = {"kind": "disc", **discrete(a, b)}
    return out


def group_of(path: str) -> str:
    parts = path.split(".")
    return ".".join(parts[:2]) if len(parts) > 2 else parts[0] + "." + parts[1]


def summarize(fields: dict, limit=8):
    """Per group: worst numeric field, number of fields with any difference; discrete mismatches."""
    groups: dict = {}
    for p, f in fields.items():
        g = groups.setdefault(group_of(p), {"fields": 0, "num_diff": 0, "disc_diff": 0, "worst": None, "disc": []})
        g["fields"] += 1
        if f["kind"] == "num":
            if f["max"] > TOLERANCE["default"]:
                g["num_diff"] += 1
            if g["worst"] is None or f["max"] > g["worst"][1]:
                g["worst"] = (p, f["max"], f["worst_frame"], f["rms"])
        elif f["differ"]:
            g["disc_diff"] += 1
            g["disc"].append((p, f["differ"], f["first"], f["first_values"]))
    for g in groups.values():
        g["disc"].sort(key=lambda x: -x[1])
        g["disc"] = g["disc"][:limit]
    return groups


def pose_errors(gen: Take, truth: Take, n: int):
    """Per frame: largest and mean bone translation error (cm) and rotation error (degrees) of the component-space pose."""
    pa, pb = truth.rec.pose[:n], gen.rec.pose[:n]
    if pa.shape[1:] != pb.shape[1:]:
        return None
    # pose columns: rotation xyzw (0:4), translation (4:7), scale (7:10)
    dt = np.linalg.norm(pa[..., 4:7] - pb[..., 4:7], axis=-1)
    dot = np.abs(np.sum(pa[..., 0:4] * pb[..., 0:4], axis=-1)).clip(0, 1)
    dr = np.degrees(2.0 * np.arccos(dot))
    return {"t_max": dt.max(axis=1), "t_mean": dt.mean(axis=1), "r_max": dr.max(axis=1), "r_mean": dr.mean(axis=1), "bone_names": truth.rec.bone_names,
            "worst_bone_t": dt.argmax(axis=1)}


RIG_PREFIX = "var:GetControlRigInput."          # the watches of the recorder that call the pure function the Control Rig node reads (ALS: GetControlRigInput)


def read_matrix(rec: Recording, n: int):
    """The values the recorder read each frame (watches that are not part of a truth channel): (names, frames x names, NaN where not read)."""
    try:
        keys, vals, off = rec.arrays["read_keys"], rec.arrays["read_values"], rec.arrays["read_offsets"]
    except KeyError:
        return [], np.zeros((0, 0))
    n = min(n, len(off) - 1)
    mat = np.full((n, len(rec.keys)), np.nan)
    for f in range(n):
        a, b = int(off[f]), int(off[f + 1])
        mat[f, keys[a:b].astype(int)] = vals[a:b]
    return [k["name"] for k in rec.keys], mat


def rig_input_errors(gen: Take, truth: Take, n: int, tol=1e-3):
    """What the animation graph hands the foot IK of the Control Rig (GetControlRigInput: FootLeft/RightLocation and Rotation) in the two takes.
    Empty when a take has no such reads (a recording made before the recorder could call the function)."""
    gn, gm = read_matrix(gen.rec, n)
    tn, tm = read_matrix(truth.rec, n)
    gi = {k: i for i, k in enumerate(gn)}
    m = min(len(gm), len(tm))
    out = {}
    for i, name in enumerate(tn):
        if not name.startswith(RIG_PREFIX) or name not in gi:
            continue
        a, b = tm[:m, i], gm[:m, gi[name]]
        ok = ~np.isnan(a) & ~np.isnan(b)
        if not ok.any():
            continue
        d = np.abs(a[ok] - b[ok])
        out[name[len(RIG_PREFIX):]] = {"max": float(d.max()), "rms": float(np.sqrt((d ** 2).mean())), "frames_over_tol": int((d > tol).sum()), "frames": int(ok.sum())}
    return out


def segment_table(seg_info, n, columns: dict):
    rows = []
    for s in seg_info:
        a, b = int(s["start_frame"]), min(int(s.get("end_frame", n)), n)
        if a >= n or b <= a:
            continue
        row = {"segment": s["name"], "frames": [a, b]}
        for name, arr in columns.items():
            seg = np.asarray(arr[a:b], float)
            row[name] = float(np.nanmax(np.abs(seg))) if seg.size else None
        rows.append(row)
    return rows


def compare(gen_path, truth_path, seg_path=None, verbose=True):
    gen, truth = Take(gen_path), Take(truth_path)
    n = min(gen.rec.frame_count, truth.rec.frame_count)
    res = {"gen": str(gen_path), "truth": str(truth_path), "frames_gen": gen.rec.frame_count, "frames_truth": truth.rec.frame_count, "frames_compared": n}

    # --- the actor, the movement component
    act = {}
    for ax in "XYZ":
        s = stats(truth.series("final", f"Actor.Location.{ax}")[:n], gen.series("final", f"Actor.Location.{ax}")[:n])
        act[f"location_{ax}"] = s
    loc_err = np.sqrt(sum((num(truth.series("final", f"Actor.Location.{ax}")[:n]) - num(gen.series("final", f"Actor.Location.{ax}")[:n])) ** 2 for ax in "XYZ"))
    act["location_error_max"] = float(np.nanmax(loc_err))
    act["location_error_at_end"] = float(loc_err[-1])
    act["location_error_p95"] = float(np.nanpercentile(loc_err, 95))
    act["yaw"] = stats(truth.series("final", "Actor.Rotation.Yaw")[:n], gen.series("final", "Actor.Rotation.Yaw")[:n], angle=True)
    vel_err = np.sqrt(sum((num(truth.series("final", f"Cmc.Velocity.{ax}")[:n]) - num(gen.series("final", f"Cmc.Velocity.{ax}")[:n])) ** 2 for ax in "XYZ"))
    act["velocity_error_max"] = float(np.nanmax(vel_err))
    act["velocity_error_rms"] = float(np.sqrt(np.nanmean(vel_err ** 2)))
    act["movement_mode"] = discrete(truth.series("final", "Cmc.MovementMode")[:n], gen.series("final", "Cmc.MovementMode")[:n])
    res["actor"] = act

    # --- character state (final), anim instance (final)
    char_paths = sorted(p for p in truth.paths["final"] if p.startswith("Char.") and p not in ("Char.Camera",))
    char_paths = [p for p in char_paths if not any(s in p for s in ("Action", "Settings", "Input", "Camera", "AnimationInstance", "Mapping", "Sensitivity", "Rate", "Movement"))
                  or p.startswith("Char.LocomotionAction")]
    res["char"] = compare_keys(gen, truth, "final", char_paths, n)
    anim_paths = sorted(p for p in truth.paths["final"] if p.startswith("Anim."))
    res["anim"] = compare_keys(gen, truth, "final", anim_paths, n)
    # --- what the logic gave the movement component
    pushed = {}
    for ch in ("post_cmc", "post_char"):
        pushed[ch] = compare_keys(gen, truth, ch, [p for p in PUSHED if key_pair(gen, truth, ch, p)], n)
    res["pushed"] = pushed

    # --- the pose and the curves
    pe = pose_errors(gen, truth, n)
    if pe:
        res["pose"] = {"translation_max_cm": float(pe["t_max"].max()), "translation_mean_cm": float(pe["t_mean"].mean()),
                       "translation_p95_cm": float(np.percentile(pe["t_max"], 95)), "rotation_max_deg": float(pe["r_max"].max()),
                       "rotation_mean_deg": float(pe["r_mean"].mean()), "rotation_p95_deg": float(np.percentile(pe["r_max"], 95)),
                       "worst_frame": int(pe["t_max"].argmax()), "worst_bone": pe["bone_names"][int(pe["worst_bone_t"][int(pe["t_max"].argmax())])],
                       "frames_over_1cm": int((pe["t_max"] > 1.0).sum()), "frames_over_5cm": int((pe["t_max"] > 5.0).sum())}
    else:
        res["pose"] = {"error": "different skeletons"}
    ca, cb = truth.rec.arrays.get("curve_values"), gen.rec.arrays.get("curve_values")
    if ca is not None and cb is not None and ca.shape[1:] == cb.shape[1:]:
        d = np.abs(ca[:n] - cb[:n])
        res["curves"] = {"max": float(np.nanmax(d)), "mean": float(np.nanmean(d)), "frames_over_0.01": int((np.nanmax(d, axis=1) > 0.01).sum())}
        worst = np.unravel_index(np.nanargmax(d), d.shape)
        names = truth.rec.curve_names
        res["curves"]["worst"] = {"frame": int(worst[0]), "curve": names[worst[1]] if worst[1] < len(names) else int(worst[1])}

    rig = rig_input_errors(gen, truth, n)
    if rig:
        res["rig_input"] = rig

    # --- by route segment
    if seg_path:
        info = json.loads(Path(seg_path).read_text(encoding="utf-8"))
        segs = info.get("segments") or info.get("takes", {}).get(next(iter(info.get("takes", {})), ""), {}).get("segments") or []
        cols = {"location_error_cm": loc_err, "velocity_error": vel_err}
        if pe:
            cols["pose_translation_cm"] = pe["t_max"]
            cols["pose_rotation_deg"] = pe["r_max"]
        sp = compare_keys(gen, truth, "final", ["Char.LocomotionState.Speed"], n).get("Char.LocomotionState.Speed")
        cols["speed_error"] = num(truth.series("final", "Char.LocomotionState.Speed")[:n]) - num(gen.series("final", "Char.LocomotionState.Speed")[:n])
        res["segments"] = segment_table(segs, n, cols)
    return res


def fmt_md(res) -> str:
    L = []
    take = Path(res["truth"]).stem
    L.append(f"### {take}: {res['frames_compared']} frames compared (generated {res['frames_gen']}, original {res['frames_truth']})")
    a = res["actor"]
    L.append("")
    L.append("| actor / movement | value |")
    L.append("|---|---|")
    L.append(f"| location error (3D, cm): max / p95 / at the end | {a['location_error_max']:.2f} / {a['location_error_p95']:.2f} / {a['location_error_at_end']:.2f} |")
    L.append(f"| yaw error (deg): max / rms | {a['yaw']['max']:.3f} / {a['yaw']['rms']:.3f} |")
    L.append(f"| velocity error (cm/s): max / rms | {a['velocity_error_max']:.2f} / {a['velocity_error_rms']:.3f} |")
    mm = a["movement_mode"]
    L.append(f"| movement mode: frames that differ | {mm['differ']} (first {mm['first']}) |")
    if "pose" in res and "error" not in res["pose"]:
        p = res["pose"]
        L.append(f"| pose translation error (cm): max / p95 / mean | {p['translation_max_cm']:.2f} / {p['translation_p95_cm']:.2f} / {p['translation_mean_cm']:.3f} (worst {p['worst_bone']} at frame {p['worst_frame']}; {p['frames_over_1cm']} frames over 1 cm, {p['frames_over_5cm']} over 5 cm) |")
        L.append(f"| pose rotation error (deg): max / p95 / mean | {p['rotation_max_deg']:.2f} / {p['rotation_p95_deg']:.2f} / {p['rotation_mean_deg']:.3f} |")
    if "curves" in res:
        c = res["curves"]
        L.append(f"| curves: max / mean abs difference | {c['max']:.4f} / {c['mean']:.5f} (worst {c['worst']['curve']} at frame {c['worst']['frame']}; {c['frames_over_0.01']} frames over 0.01) |")
    for title, key in (("character state (Char.*)", "char"), ("animation instance (Anim.*)", "anim")):
        groups = summarize(res[key])
        L.append("")
        L.append(f"| {title} | fields | numeric fields off | worst numeric field (max, rms, frame) | discrete fields off |")
        L.append("|---|---|---|---|---|")
        for g, v in sorted(groups.items()):
            w = f"{v['worst'][0].split('.', 2)[-1]}: {v['worst'][1]:.4g}, {v['worst'][3]:.3g}, f{v['worst'][2]}" if v["worst"] else ""
            L.append(f"| {g} | {v['fields']} | {v['num_diff']} | {w} | {v['disc_diff']} |")
        disc = [(p, d, f, v) for g in groups.values() for (p, d, f, v) in g["disc"]]
        disc.sort(key=lambda x: -x[1])
        if disc:
            L.append("")
            L.append("discrete mismatches (frames differing, first frame, first values truth / generated): " +
                     "; ".join(f"{p} {d} f{f} {v[0]}/{v[1]}" for p, d, f, v in disc[:10]))
    for ch, fields in res["pushed"].items():
        bad = [(p, f["max"], f["rms"]) for p, f in fields.items() if f["kind"] == "num" and f["max"] > 1e-3]
        L.append("")
        L.append(f"pushed to the movement component, seen at {ch}: " + ("; ".join(f"{p.split('.',1)[1]} max {m:.4g} rms {r:.3g}" for p, m, r in sorted(bad, key=lambda x: -x[1])) or "all equal"))
    if res.get("rig_input"):
        r = res["rig_input"]
        worst = sorted(r.items(), key=lambda kv: -kv[1]["max"])[:4]
        L.append("")
        L.append(f"Control Rig input (GetControlRigInput, {len(r)} values): {sum(1 for v in r.values() if v['frames_over_tol'])} off; worst "
                 + "; ".join(f"{k} max {v['max']:.4g} rms {v['rms']:.3g} ({v['frames_over_tol']} frames over 1e-3)" for k, v in worst))
    if res.get("segments"):
        L.append("")
        L.append("| segment | frames | location error cm | velocity err | speed err | pose cm | pose deg |")
        L.append("|---|---|---|---|---|---|---|")
        for s in res["segments"]:
            L.append(f"| {s['segment']} | {s['frames'][0]}-{s['frames'][1]} | {s['location_error_cm']:.2f} | {s['velocity_error']:.2f} | {s['speed_error']:.2f} | "
                     f"{s.get('pose_translation_cm', float('nan')):.2f} | {s.get('pose_rotation_deg', float('nan')):.2f} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("gen")
    ap.add_argument("truth")
    ap.add_argument("--segments")
    ap.add_argument("--json")
    ap.add_argument("--md")
    a = ap.parse_args(argv)
    res = compare(a.gen, a.truth, a.segments)
    md = fmt_md(res)
    print(md)
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)), encoding="utf-8")
    if a.md:
        Path(a.md).write_text(md, encoding="utf-8")


if __name__ == "__main__":
    main()
