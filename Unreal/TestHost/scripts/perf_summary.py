"""Summarise the per-tick cost of the harness runs: python perf_summary.py <runs dir> <scenario> [<scenario> ...]

Reads <scenario>_<mode>_r<N>.json (modes compiled / interp; N = repeat) and prints, per mode, the median over the
repeats of the mean / p50 / p95 / p99 / max cost of one component tick in microseconds, plus the Python share for interp.
"""

from __future__ import annotations

import glob
import json
import os
import statistics
import sys


def load(runs, scenario, mode):
    out = []
    for path in sorted(glob.glob(os.path.join(runs, f"{scenario}_{mode}_r*.json"))):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if not d.get("error") and "tick_stats" in d.get("notes", {}):
            out.append(d["notes"]["tick_stats"])
    return out


def med(stats, key):
    vals = [s[key] for s in stats if key in s]
    return statistics.median(vals) if vals else float("nan")


def main():
    runs = sys.argv[1]
    print(f"{'scenario':10} {'mode':9} {'runs':>4} {'mean':>8} {'p50':>8} {'p95':>8} {'p99':>8} {'max':>8}   (us per tick; median over repeats)")
    for scenario in sys.argv[2:]:
        for mode in ("compiled", "interp"):
            st = load(runs, scenario, mode)
            if not st:
                continue
            line = f"{scenario:10} {mode:9} {len(st):4d} {med(st, 'mean_us'):8.1f} {med(st, 'p50_us'):8.1f} {med(st, 'p95_us'):8.1f} {med(st, 'p99_us'):8.1f} {med(st, 'max_us'):8.1f}"
            if mode == "interp":
                line += f"   python part {med(st, 'python_update_mean_us'):.1f}"
            print(line)


if __name__ == "__main__":
    main()
