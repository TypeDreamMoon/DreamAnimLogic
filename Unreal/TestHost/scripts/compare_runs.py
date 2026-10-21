"""Compare two harness result files frame by frame (compiled C++ vs interpreted DalLive): the logic's inputs and states and
the host effects the commands produced (CMC speed, montage state, control rotation, spring arm, location).

    python compare_runs.py runs/melee_compiled.json runs/melee_interp.json [--ignore TargetId] [--tol 1e-4]

Exit code 0 when every frame matches within the tolerances.  Enum members compare case- and underscore-insensitively
(the UE Python names are upper case: HIT1 / Hit1).  Floats: |a - b| <= atol + rtol * max(|a|, |b|) (the compiled side
computes in float, the interpreter in double).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys


def norm(v):
    if isinstance(v, str):
        return re.sub(r"[^a-z0-9]", "", v.lower())
    return v


def close(a, b, rtol, atol):
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= atol + rtol * max(abs(a), abs(b))
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(close(x, y, rtol, atol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(close(a[k], b[k], rtol, atol) for k in a)
    return norm(a) == norm(b)


def diff_size(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return 0.0 if a == b else 1.0
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == len(b):
        return max((diff_size(x, y) for x, y in zip(a, b)), default=0.0)
    if isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys():
        return max((diff_size(a[k], b[k]) for k in a), default=0.0)
    return 0.0 if norm(a) == norm(b) else 1.0


def flatten(rec, ignore):
    out = {}
    for k, v in rec.get("state", {}).items():
        if k not in ignore:
            out["state." + k] = v
    fx = rec.get("fx", {})
    for k, v in fx.items():
        if k == "montages":
            for m, mv in v.items():
                out[f"fx.montage.{m}.playing"] = mv[0]
                out[f"fx.montage.{m}.active"] = mv[1]
                out[f"fx.montage.{m}.position"] = mv[2]
                out[f"fx.montage.{m}.section"] = mv[3]
        else:
            out["fx." + k] = v
    return out


def compare(a, b, rtol=1e-4, atol=1e-4, ignore=(), per_field_tol=None, skew=0):
    """skew: accept a bool / enum field that differs at frame i when the other side has the same value within +-skew
    frames (a hard threshold crossed one frame apart because one side computes in float and the other in double);
    such frames are counted in report["skewed"] and do not make the comparison fail."""
    per_field_tol = per_field_tol or {}
    fa, fb = a["frames"], b["frames"]
    n = min(len(fa), len(fb))
    report = {"frames_a": len(fa), "frames_b": len(fb), "compared": n, "fields": {}, "bad_frames": 0, "first_bad": [], "skewed": {}}
    flat_a = [flatten(r, ignore) for r in fa]
    flat_b = [flatten(r, ignore) for r in fb]

    def skewed(key, i, va, vb):
        if skew <= 0 or isinstance(va, float) or isinstance(vb, float) or isinstance(va, (list, tuple)):
            return False
        for d in range(1, skew + 1):
            for j in (i - d, i + d):
                if 0 <= j < n and (norm(flat_b[j].get(key)) == norm(va) or norm(flat_a[j].get(key)) == norm(vb)):
                    return True
        return False

    for i in range(n):
        ra, rb = flat_a[i], flat_b[i]
        bad = False
        if abs(fa[i]["t"] - fb[i]["t"]) > 1e-6:
            report["fields"].setdefault("t", {"frames": 0, "max": 0.0, "first": i})
            e = report["fields"]["t"]
            e["frames"] += 1
            e["max"] = max(e["max"], abs(fa[i]["t"] - fb[i]["t"]))
            bad = True
        for key in sorted(set(ra) | set(rb)):
            va, vb = ra.get(key), rb.get(key)
            rt_, at_ = per_field_tol.get(key, (rtol, atol))
            if key not in ra or key not in rb or not close(va, vb, rt_, at_):
                if key in ra and key in rb and skewed(key, i, va, vb):
                    report["skewed"].setdefault(key, []).append(i)
                    continue
                e = report["fields"].setdefault(key, {"frames": 0, "max": 0.0, "first": i, "example": (va, vb)})
                e["frames"] += 1
                e["max"] = max(e["max"], diff_size(va, vb))
                bad = True
        if bad:
            report["bad_frames"] += 1
            if len(report["first_bad"]) < 5:
                report["first_bad"].append(i)
    return report


def max_diffs(a, b, ignore=()):
    """Largest absolute difference per numeric field over all frames (for the report, even when within tolerance)."""
    fa, fb = a["frames"], b["frames"]
    out = {}
    for i in range(min(len(fa), len(fb))):
        ra, rb = flatten(fa[i], ignore), flatten(fb[i], ignore)
        for key in ra:
            if key in rb:
                d = diff_size(ra[key], rb[key])
                if d > out.get(key, 0.0):
                    out[key] = d
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--ignore", action="append", default=[])
    ap.add_argument("--skew", type=int, default=0, help="accept discrete fields that differ only by a transition shifted by up to N frames")
    ap.add_argument("--rtol", type=float, default=1e-4)
    ap.add_argument("--atol", type=float, default=1e-4)
    ap.add_argument("--tol", action="append", default=[], help="field=rtol,atol overrides, e.g. fx.control_rotation=0,5e-3")
    args = ap.parse_args(argv)
    a = json.load(open(args.a, encoding="utf-8"))
    b = json.load(open(args.b, encoding="utf-8"))
    per = {}
    for t in args.tol:
        k, v = t.split("=")
        r, at = v.split(",")
        per[k] = (float(r), float(at))
    rep = compare(a, b, args.rtol, args.atol, set(args.ignore), per, args.skew)
    print(f"A {a['name']}: {rep['frames_a']} frames   B {b['name']}: {rep['frames_b']} frames   compared {rep['compared']}")
    if a.get("error") or b.get("error"):
        print("run errors:", a.get("error"), b.get("error"))
    md = max_diffs(a, b, set(args.ignore))
    nonzero = {k: v for k, v in md.items() if v > 0}
    print(f"frames with a mismatch: {rep['bad_frames']}")
    for k, e in rep["fields"].items():
        print(f"  MISMATCH {k}: {e['frames']} frame(s), max diff {e['max']:.6g}, first at {e['first']}, e.g. {e.get('example')}")
    for k, frames in rep["skewed"].items():
        print(f"  SKEW {k}: transition shifted by one frame at frame(s) {frames} (float vs double at a hard threshold)")
    if nonzero:
        print("largest differences within tolerance:", ", ".join(f"{k}={v:.3g}" for k, v in sorted(nonzero.items(), key=lambda kv: -kv[1])[:8]))
    ok = rep["bad_frames"] == 0 and rep["compared"] > 0 and rep["frames_a"] == rep["frames_b"]
    print("RESULT:", "IDENTICAL" if ok else "DIFFERENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
