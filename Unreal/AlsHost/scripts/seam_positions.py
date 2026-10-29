# ANIM-54: the positions for seam_probe.py -- frames of the truth takes in which the capsule stands exactly on the seam y = 0 of two coplanar floor tiles (the 4 tiles of
# L_DreamVEd_AlsRecord meet at x = 0 / y = 0, centres at y = +-2000), sorted into: `flip` (the movement base is the tile at y = -2000: the engine's sweep picked it),
# `low` (the sweep's Time is below the exact value of a capsule resting on a plane: GJK's float32 noise) and `exact` (the exact value).  The recorded sweep is in the
# take: Cmc.CurrentFloor.HitResult.Time / TraceStart / TraceEnd, the base in Char.MovementBase.Location.
#   python seam_positions.py <out positions.json> [<out tags.json>]
import collections
import json
import random
import sys

import numpy as np

sys.path.insert(0, r"I:/DCC/blender-moon-engine/scripts/modules")
from dreamved.anim import teacher as T  # noqa: E402
from dreamved.anim.recording import Recording  # noqa: E402

TAKES = [("mantle_variants", "G:/DCC/AlsTruth/extra/als_mantle_variants.dvrec", (("flip", 3), ("low", 6), ("exact", 6))),
         ("in_air", "G:/DCC/AlsTruth/als_in_air.dvrec", (("flip", 11), ("low", 10), ("exact", 6))),
         ("roll", "G:/DCC/AlsTruth/als_roll.dvrec", (("flip", 4), ("low", 6))),
         ("stance_changes", "G:/DCC/AlsTruth/als_stance_changes.dvrec", (("flip", 3), ("low", 6))),
         ("base_roll_crouch", "G:/DCC/AlsTruth/base/als_base_roll_crouch.dvrec", (("flip", 4), ("low", 4)))]


def frames_of(path):
    rec = Recording.load(path)
    s = T._TruthReader(rec).series
    g = lambda k: s("final", k).astype(np.float64)  # noqa: E731
    return dict(x=g("Actor.Location.X"), y=g("Actor.Location.Y"), bx=g("Char.MovementBase.Location.X"), by=g("Char.MovementBase.Location.Y"),
                t=g("Cmc.CurrentFloor.HitResult.Time"), dist=g("Cmc.CurrentFloor.HitResult.Distance"),
                tsx=g("Cmc.CurrentFloor.HitResult.TraceStart.X"), tsy=g("Cmc.CurrentFloor.HitResult.TraceStart.Y"), tsz=g("Cmc.CurrentFloor.HitResult.TraceStart.Z"),
                tez=g("Cmc.CurrentFloor.HitResult.TraceEnd.Z"), mode=s("final", "Cmc.MovementMode"))


def main(out, tags_out=None):
    random.seed(7)
    pos, tags = [], []
    for take, path, kinds in TAKES:
        d = frames_of(path)
        seam = np.where((np.abs(d["y"]) < 1e-6) & (np.abs(np.abs(d["by"]) - 2000) < 1e-3) & (d["mode"] == "MOVE_Walking"))[0]
        exact = np.abs(d["dist"] - (d["tsz"] - 84.0)) < 5e-4
        pools = {"flip": [i for i in seam if d["by"][i] < 0], "low": [i for i in seam if not exact[i] and d["by"][i] > 0], "exact": [i for i in seam if exact[i] and d["by"][i] > 0]}
        for kind, k in kinds:
            pool = list(pools[kind])
            random.shuffle(pool)
            for i in pool[:k]:
                trace = float(d["tsz"][i] - d["tez"][i])
                height = round(30.0 + (trace - 47.4) * 10.0)                  # the pawn's half height: the trace is 47.4 + the shrink (height - 30) * 0.1 long
                shrink = np.float32(np.float32(height) - np.float32(30.0)) * (np.float32(1.0) - np.float32(0.9))
                half = float(np.float32(height) - shrink)
                pos.append([float(d["tsx"][i]), float(d["tsy"][i]), float(d["tsz"][i]), float(d["tsx"][i]), float(d["tsy"][i]), float(d["tez"][i]), half])
                tags.append(dict(take=take, frame=int(i), kind=kind, base_y=float(d["by"][i]), base_x=float(d["bx"][i]), rec_time=float(d["t"][i]), rec_dist=float(d["dist"][i])))
    json.dump(pos, open(out, "w"))
    if tags_out:
        json.dump(tags, open(tags_out, "w"))
    print(len(pos), "positions", dict(collections.Counter(t["kind"] for t in tags)))


if __name__ == "__main__":
    main(*sys.argv[1:3])
