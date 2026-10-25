#!/usr/bin/env python
"""Generates the C++ of the ALS rewrite (als/*.dal) into a folder, from a git revision of the repository or from the working tree.

    python Unreal/AlsHost/gen_als.py --out G:/DCC/scratch/h5/gen [--rev HEAD | --rev worktree]

The compiler itself is always the working tree's dal package.  --rev takes the .dal sources from a commit, so that the build of the
host is reproducible while other people edit als/ (the default is HEAD).  The logics are compiled one by one, in the order the generated
code depends on them (the library first), with --module ALS_API (the classes must be exported from the module called ALS) and the options that make the settings read what the assets of the original hold
(ASSET_COMPAT: double-precision members for the Control Rig, the turn-in-place settings as objects, FFloatInterval).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOGICS = ["AlsCharacter", "AlsAnimationInstance", "AlsLinkedAnimationInstance", "AlsInput"]
# What the assets of the original ALS hold, in the types they hold it in (the compiler options say why): the members of structs in the double-precision
# engine types (Control Rig pins), the turn-in-place settings as objects, FFloatInterval as the engine's type, and the collision channels the constructors of
# ALS's settings classes set (no asset holds a value that is the default).
ASSET_COMPAT = ["--double-members", "--object-settings", "AlsTurnInPlaceSettings", "--native-struct", "AlsFloatInterval=FFloatInterval=Math/Interval.h",
                "--channel-default", "AlsGeneralMantlingSettings.MantlingTraceResponseChannels=WorldStatic,WorldDynamic,Destructible",
                "--channel-default", "AlsGeneralMantlingSettings.MantlingTraceResponses=WorldStatic,WorldDynamic,Destructible",
                "--channel-default", "AlsInAirSettings.GroundPredictionResponseChannels=WorldStatic,WorldDynamic,Destructible",
                "--channel-default", "AlsInAirSettings.GroundPredictionSweepResponses=WorldStatic,WorldDynamic,Destructible"]


def sources(rev: str, into: Path) -> list[str]:
    """Copies als/*.dal (and any other file next to them) of the revision into `into`; returns the names."""
    names = []
    if rev == "worktree":
        for f in (REPO / "als").iterdir():
            if f.is_file():
                shutil.copy2(f, into / f.name)
                names.append(f.name)
        return names
    listing = subprocess.run(["git", "-C", str(REPO), "ls-tree", "--name-only", rev, "als/"], capture_output=True, text=True, check=True).stdout.split()
    for rel in listing:
        blob = subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{rel}"], capture_output=True, check=True).stdout
        if rel.endswith("/"):
            continue
        (into / Path(rel).name).write_bytes(blob)
        names.append(Path(rel).name)
    return names


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("--module", default="ALS_API")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(REPO))
    from dal import cli

    a.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="als_src_") as tmp:
        tmp_path = Path(tmp)
        names = sources(a.rev, tmp_path)
        print("sources of %s: %s" % (a.rev, ", ".join(sorted(n for n in names if n.endswith(".dal")))))
        for logic in LOGICS:
            code = cli.main(["compile", str(tmp_path / f"{logic}.dal"), "--cpp", str(a.out), "--module", a.module, *ASSET_COMPAT])
            if code:
                return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
