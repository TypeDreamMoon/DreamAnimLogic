#!/usr/bin/env python
"""Compares the ragdoll take of the generated ALS with the one of the original (both made by g_record.py, take "ragdoll").

    python compare_ragdoll.py <gen.dvrec> <orig.dvrec> [--json out.json] [--md out.md]

The physics of a ragdoll is not reproducible bit for bit (the bodies are simulated, a one-frame difference in when they were released grows), so this
does not compare the pose frame by frame.  It compares what the logic decides and when:

  * the driver's commands (start_ragdolling, stop_ragdolling, jump ...): the frame each acted in, in both takes;
  * the state machine: Char.LocomotionAction / Char.LocomotionMode / Cmc.MovementMode as a list of (frame, value) changes, per ragdoll episode the frame each
    state was entered in the two takes (the difference in frames), and how many frames of the take the states differ;
  * the animation montages the logic started (the flail loop, the get up front / back, the ones of the turn and the transitions afterwards): which
    montage and in which frame;
  * the get up choice: facing up or down when the ragdoll stopped, and so the montage (AM_Als_GetUp_Front / _Back) in both;
  * the final pose handshake: Anim.RagdollingState.FinalRagdollPose.bIsValid / the number of transforms, FlailPlayRate while ragdolling;
  * the capsule: where the actor is at the end of every episode (ragdoll lying on the ground) and 1 s after the get up, and its distance between the takes;
  * the pose: the mean bone translation error in the first frames after the ragdoll started / stopped (the continuity of the handshake), and over the
    episodes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, r"I:/DCC/blender-moon-engine/scripts/modules")
from dreamved.anim.recording import Recording  # noqa: E402


def changes(series):
    out = []
    prev = object()
    for f, v in enumerate(series):
        if v != prev:
            out.append((f, v))
            prev = v
    return out


def command_frames(rec, name):
    return [e["f"] for e in rec.events if e.get("type") == "command" and e.get("name") == name]


def montages(rec):
    return [(e["f"], e["montage"], round(e.get("rate", 1.0), 3), round(e.get("length", 0.0), 3)) for e in rec.events if e.get("type") == "montage_play"]


def episodes(rec):
    """The frames [start, end) where the locomotion action is Ragdolling, and the frame the action after it began."""
    action = rec.truth.series("final", "Char.LocomotionAction")
    out = []
    start = None
    for f, v in enumerate(action):
        ragdolling = v is not None and str(v).endswith("Ragdolling")
        if ragdolling and start is None:
            start = f
        if not ragdolling and start is not None:
            out.append((start, f))
            start = None
    if start is not None:
        out.append((start, len(action)))
    return out


def loc(rec, f):
    t = rec.truth
    return np.array([t.series("final", f"Actor.Location.{a}")[f] for a in "XYZ"], float)


def compare(gen_path, orig_path):
    gen, orig = Recording.load(gen_path), Recording.load(orig_path)
    n = min(gen.frame_count, orig.frame_count)
    res = {"gen": str(gen_path), "orig": str(orig_path), "frames": [gen.frame_count, orig.frame_count]}
    res["commands"] = {k: [command_frames(orig, k), command_frames(gen, k)] for k in ("start_ragdolling", "stop_ragdolling", "jump")}
    res["montages"] = {"orig": montages(orig), "gen": montages(gen)}

    # state machine
    states = {}
    for key in ("Char.LocomotionAction", "Char.LocomotionMode", "Cmc.MovementMode"):
        a, b = orig.truth.series("final", key)[:n], gen.truth.series("final", key)[:n]
        differ = int(sum(1 for x, y in zip(a, b) if x != y))
        states[key] = {"frames_differ": differ, "orig": changes(a), "gen": changes(b)}
    res["states"] = states

    # episodes
    eo, eg = episodes(orig), episodes(gen)
    res["episodes"] = {"orig": eo, "gen": eg}
    rows = []
    for k, (po, pg) in enumerate(zip(eo, eg)):
        row = {"episode": k, "start": [po[0], pg[0]], "end": [po[1], pg[1]], "length": [po[1] - po[0], pg[1] - pg[0]]}
        so, sg = po[1] - 1, pg[1] - 1
        row["location_at_end"] = {"orig": loc(orig, so).round(1).tolist(), "gen": loc(gen, sg).round(1).tolist(), "distance": float(np.linalg.norm(loc(orig, so) - loc(gen, sg)))}
        # one second after the get up started (when both takes still have it)
        a, b = min(po[1] + 60, orig.frame_count - 1), min(pg[1] + 60, gen.frame_count - 1)
        row["location_1s_after"] = {"orig": loc(orig, a).round(1).tolist(), "gen": loc(gen, b).round(1).tolist(), "distance": float(np.linalg.norm(loc(orig, a) - loc(gen, b)))}
        pr = orig.truth.series("final", "Char.RagdollingState.Velocity.Z")
        row["velocity_z_at_end"] = [float(pr[so]), float(gen.truth.series("final", "Char.RagdollingState.Velocity.Z")[sg])]
        rows.append(row)
    res["episode_rows"] = rows

    # the handshake
    hs = {}
    for key in ("Anim.RagdollingState.FinalRagdollPose.bIsValid", "Anim.RagdollingState.FinalRagdollPose.LocalTransforms.Num", "Anim.RagdollingState.FlailPlayRate"):
        if orig.truth.has("final", key) and gen.truth.has("final", key):
            a, b = orig.truth.series("final", key)[:n], gen.truth.series("final", key)[:n]
            if a.dtype != object:
                d = np.abs(np.asarray(a, float) - np.asarray(b, float))
                hs[key] = {"max_diff": float(np.nanmax(d)), "frames_over_1e-3": int((d > 1e-3).sum()),
                           "orig_changes": changes([round(float(x), 3) for x in a])[:8], "gen_changes": changes([round(float(x), 3) for x in b])[:8]}
            else:
                hs[key] = {"frames_differ": int(sum(1 for x, y in zip(a, b) if x != y))}
    res["handshake"] = hs

    # the pose
    pa, pb = orig.pose[:n], gen.pose[:n]
    dt = np.linalg.norm(pa[..., 4:7] - pb[..., 4:7], axis=-1).mean(axis=1)
    pose = {"mean_bone_translation_error_cm_all": float(dt.mean())}
    for k, (po, pg) in enumerate(zip(eo, eg)):
        for label, f0 in (("start", po[0]), ("stop", po[1])):
            seg = dt[f0:min(f0 + 30, n)]
            pose[f"episode{k}_{label}_first30_mean_cm"] = float(seg.mean()) if seg.size else None
        seg = dt[po[0]:min(po[1], n)]
        pose[f"episode{k}_ragdoll_mean_cm"] = float(seg.mean()) if seg.size else None
    res["pose"] = pose
    return res


def fmt_md(res):
    L = ["### ragdoll take: generated vs original", ""]
    L.append(f"frames: generated {res['frames'][0]}, original {res['frames'][1]}")
    L.append("")
    L.append("| command | frames (original) | frames (generated) |")
    L.append("|---|---|---|")
    for k, (a, b) in res["commands"].items():
        L.append(f"| {k} | {a} | {b} |")
    L.append("")
    L.append("| episode | ragdoll start (orig / gen) | end (orig / gen) | length (orig / gen) | capsule at the end: distance cm | 1 s after the get up: distance cm |")
    L.append("|---|---|---|---|---|---|")
    for r in res["episode_rows"]:
        L.append(f"| {r['episode']} | {r['start'][0]} / {r['start'][1]} | {r['end'][0]} / {r['end'][1]} | {r['length'][0]} / {r['length'][1]} | "
                 f"{r['location_at_end']['distance']:.1f} | {r['location_1s_after']['distance']:.1f} |")
    L.append("")
    L.append("| state | frames that differ |")
    L.append("|---|---|")
    for k, v in res["states"].items():
        L.append(f"| {k} | {v['frames_differ']} |")
    L.append("")
    L.append("montages started (frame, name, rate, length):")
    L.append("")
    L.append("- original: " + "; ".join(f"{f} {m}" for f, m, _r, _l in res["montages"]["orig"]))
    L.append("- generated: " + "; ".join(f"{f} {m}" for f, m, _r, _l in res["montages"]["gen"]))
    L.append("")
    for k, v in res["handshake"].items():
        L.append(f"- {k}: {json.dumps(v)}")
    L.append("")
    L.append("pose (mean bone translation error, cm): " + ", ".join(f"{k} {v:.2f}" for k, v in res["pose"].items() if v is not None))
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("gen")
    ap.add_argument("orig")
    ap.add_argument("--json")
    ap.add_argument("--md")
    a = ap.parse_args(argv)
    res = compare(a.gen, a.orig)
    md = fmt_md(res)
    print(md)
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    if a.md:
        Path(a.md).write_text(md, encoding="utf-8")


if __name__ == "__main__":
    main()
