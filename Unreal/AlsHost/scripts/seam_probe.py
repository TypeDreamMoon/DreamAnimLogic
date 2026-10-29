# ANIM-54 (anim-moving-base.md 12.7, 1): repeats the movement component's floor sweep (ComputeFloorDist -> FloorSweepTest: a capsule of radius 30 and half height 84 from the
# capsule location 53.4 cm down) in a PIE world of L_DreamVEd_AlsRecord at positions taken from the truth takes (on the seam y = 0 between two coplanar floor tiles), for
# (a) all actors (what the movement component's sweep sees: the winner and its Time) and (b) each of the tiles under the capsule alone (every other static mesh actor
# ignored): the Time / Distance each tile gives, so the winner can be compared with them.  The result is a pure function of the position: all 68 positions out of 5 truth
# takes (25 of them frames in which the base flipped to the other tile) give the Time of the recorded frame to 1e-9 and the same winner.
#   SEAM_PIE=1 SEAM_IN=<positions json from seam_positions.py> SEAM_OUT=<result json> pwsh run_editor.ps1 -Script seam_probe.py -Fixed60
import json
import os
import sys

import unreal

sys.path.insert(0, "G:/DCC/AlsTruth/tools")
from harness import Wait, game_world, run  # noqa: E402

IN = os.environ["SEAM_IN"]
OUT = os.environ["SEAM_OUT"]
TRACE = unreal.TraceTypeQuery.ECC_VISIBILITY          # Visibility: the tiles block it like the pawn channel
RADIUS = 30.0


def log(m):
    unreal.log("seam_probe: " + m)


def vec(v):
    return [v.x, v.y, v.z]


def prop(res, name, default=None):
    try:
        return res.get_editor_property(name)
    except Exception:  # noqa: BLE001
        return default


def steps():
    try:
        yield Wait(6.0)
        if os.environ.get("SEAM_PIE"):
            import dreamved_unreal.anim_record as ar
            ar.begin_play()
            yield Wait(until=lambda: game_world() is not None, timeout=120)
            yield Wait(3.0)
            world = game_world()
        else:
            world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        actors = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.StaticMeshActor)
        tiles = {}
        for a in actors:
            loc = a.get_actor_location()
            sc = a.get_actor_scale3d()
            tiles[a.get_name()] = (a, [loc.x, loc.y, loc.z], [sc.x, sc.y, sc.z])
        log("%d static mesh actors" % len(tiles))
        positions = json.load(open(IN))
        results = []

        dbg = []

        def sweep(p, ignore):
            s = unreal.Vector(p[0], p[1], p[2])
            e = unreal.Vector(p[3], p[4], p[5])
            r = unreal.SystemLibrary.capsule_trace_single(world, s, e, RADIUS, p[6], TRACE, False, ignore, unreal.DrawDebugTrace.NONE, True)
            if len(dbg) < 2:
                dbg.append(repr(r)[:600])
                log("trace result: " + repr(r)[:400] + " type " + str(type(r)))
            res = r[-1] if isinstance(r, tuple) else r
            if res is None or isinstance(res, bool) or not hasattr(res, "to_tuple"):
                return None
            tup = res.to_tuple()
            if not tup[0]:
                return None
            d = {"time": tup[2], "distance": tup[3], "location": vec(tup[4]), "impact": vec(tup[5]), "start_penetrating": tup[1], "face": tup[15]}
            comp = tup[10]
            d["actor"] = comp.get_owner().get_name() if comp is not None else None
            return d

        for p in positions:
            entry = {"pos": p, "all": sweep(p, [])}
            entry["tiles"] = {}
            for name, (a, loc, sc) in tiles.items():
                if loc[2] < 1.0 and abs(sc[0]) >= 10.0 and abs(loc[0] - p[0]) < 2100.0 and abs(loc[1] - p[1]) < 2100.0:
                    others = [tiles[n][0] for n in tiles if n != name]
                    entry["tiles"][name] = {"center": loc, "scale": sc, "hit": sweep(p, others)}
            results.append(entry)
        json.dump({"debug": dbg, "results": results}, open(OUT, "w"))
        log("wrote %s (%d positions)" % (OUT, len(results)))
        if os.environ.get("SEAM_PIE"):
            ar.end_play()
            yield Wait(2.0)
    except Exception:
        import traceback
        unreal.log_error("seam_probe failed: " + traceback.format_exc())

    finally:
        pass

run(steps)
