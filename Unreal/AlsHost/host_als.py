#!/usr/bin/env python
"""Swaps the C++ generated from als/*.dal in for the logic layer of ALS-Refactored in a UE sandbox host, and back.

    python host_als.py apply   --gen <dir with the generated C++> [--host <project dir>] [--no-overlay]
    python host_als.py restore [--host <project dir>]
    python host_als.py status  [--host <project dir>]

Why this way (als-dal-rewrite.md decision 1: the generated module is `ALS`, with the classes and property names of the original, so that
the animation blueprints, the settings assets and the notify assets keep working): two modules cannot both be called ALS, and two
classes cannot both be UAlsAnimationInstance, so the original logic sources must not be in the build.  UBT builds every .cpp under a
module's directory and UHT reads every header, so "disabled" means "not under Source/".  This script moves them to
<plugin>/OriginalLogic/ (same relative paths), puts the generated sources in Source/ALS/{Public,Private}/Dal/, and edits the few
tracked files that have to change.  Everything it does is written to <plugin>/.dal_host_manifest.json; `restore` undoes it from that
file (moved files go back, created files are deleted, patched files get their original text).  The ALS plugin of the host is a git
clone, so `git -C <plugin> checkout -- Source ALS.uplugin` + deleting OriginalLogic and Source/ALS/*/Dal is the same thing by hand
(do NOT `git checkout -- .`: Content/ holds the animation blueprints rebuilt from .dab, they show up as modified on purpose).

What stays of the original ALS module: the animation graph nodes and rig units (Nodes/), the utility library (Utility/ minus the
gameplay tags, which the generated library declares), the constants.  What is replaced: AAlsCharacter, UAlsCharacterMovementComponent
(DalRuntime has the native part), UAlsAnimationInstance (+ proxy), UAlsLinkedAnimationInstance, the root motion source (DalRuntime),
every State and Settings header (the generated AlsLib.h has them), the gameplay tags.  What is switched off: ALSCamera and ALSExtras
(they are built on AAlsCharacter; the camera is not rewritten yet, als-dal-rewrite.md decision 5) and the five notify classes
that call the old classes (the .dal declares its own).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sys
from pathlib import Path

DEFAULT_HOST = Path(r"I:\UnrealProject_Moon\DEV_58\DreamVEdAnimHost")
HERE = Path(__file__).resolve().parent

# relative to Source/ALS/Public and Source/ALS/Private
MOVE_PUBLIC = [
    "AlsAnimationInstance.h", "AlsAnimationInstanceProxy.h", "AlsCharacter.h", "AlsCharacterMovementComponent.h", "AlsLinkedAnimationInstance.h",
    "RootMotionSources", "Settings", "State", "Utility/AlsGameplayTags.h",
    "Notifies/AlsAnimNotifyState_EarlyBlendOut.h", "Notifies/AlsAnimNotifyState_SetLocomotionAction.h", "Notifies/AlsAnimNotifyState_SetRootMotionScale.h",
    "Notifies/AlsAnimNotify_FootstepEffects.h", "Notifies/AlsAnimNotify_SetGroundedEntryMode.h",
]
MOVE_PRIVATE = [
    "AlsAnimationInstance.cpp", "AlsAnimationInstanceProxy.cpp", "AlsCharacter.cpp", "AlsCharacter_Actions.cpp", "AlsCharacter_Debug.cpp",
    "AlsCharacterMovementComponent.cpp", "AlsLinkedAnimationInstance.cpp", "RootMotionSources", "Settings", "Utility/AlsGameplayTags.cpp",
    "Notifies/AlsAnimNotifyState_EarlyBlendOut.cpp", "Notifies/AlsAnimNotifyState_SetLocomotionAction.cpp", "Notifies/AlsAnimNotifyState_SetRootMotionScale.cpp",
    "Notifies/AlsAnimNotify_FootstepEffects.cpp", "Notifies/AlsAnimNotify_SetGroundedEntryMode.cpp",
]
DISABLED_MODULES = ("ALSCamera", "ALSExtras")
SHIMS = {   # retained headers that include a moved one: the same path now includes the generated library
    "Source/ALS/Public/State/AlsMovementDirection.h": '#pragma once\n\n// Shim made by host_als.py: the movement direction types are declared by the generated library.\n#include "Dal/AlsLib.h"\n',
}
ALS_BUILD_CS_EXTRA = '''
		// ---- host_als.py: the generated DAL code (Public/Dal, Private/Dal) ----
		PublicIncludePaths.Add(System.IO.Path.Combine(ModuleDirectory, "Public", "Dal"));
		PublicDependencyModuleNames.AddRange(["DalRuntime", "EnhancedInput", "InputCore"]);
'''
HOST_DEPS = '"ALS", "DalRuntime", "GameplayTags", "InputCore", "AnimGraphRuntime"'


def read(p: Path) -> str:
    return p.read_bytes().decode("utf-8-sig") if p.suffix in (".cs", ".uplugin", ".uproject") else p.read_text(encoding="utf-8")


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self.data = {"moved": [], "created": [], "patched": {}, "gen": None}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")


def plugin_dir(host: Path) -> Path:
    return host / "Plugins" / "ALS-Refactored"


def patch(man: Manifest, root: Path, tag: str, rel: str, data: bytes):
    """Writes `data` over an existing file, remembering its original bytes (once)."""
    p = root / rel
    key = f"{tag}:{rel}"
    if key not in man.data["patched"]:
        man.data["patched"][key] = base64.b64encode(p.read_bytes()).decode("ascii")
    p.write_bytes(data)


def create(man: Manifest, root: Path, tag: str, rel: str, data: bytes):
    """Writes a file; one that did not exist is deleted by `restore`, one that did is patched."""
    p = root / rel
    if p.exists():
        patch(man, root, tag, rel, data)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    man.data["created"].append(f"{tag}:{rel}")
    p.write_bytes(data)


def move_merge(src: Path, dst: Path):
    """shutil.move that merges into an existing directory instead of nesting inside it."""
    if src.is_dir() and dst.is_dir():
        for child in sorted(src.iterdir()):
            move_merge(child, dst / child.name)
        src.rmdir()
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    shutil.move(str(src), str(dst))


def move_out(man: Manifest, plugin: Path, area: str, rels: list[str]):
    base = plugin / "Source" / "ALS" / area
    out = plugin / "OriginalLogic" / area
    for rel in rels:
        src = base / rel
        if not src.exists():
            continue
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        man.data["moved"].append(f"Source/ALS/{area}/{rel}")
        if src.parent.exists() and not any(src.parent.iterdir()) and src.parent != base:
            src.parent.rmdir()


def apply(host: Path, gen: Path, overlay: bool):
    plugin = plugin_dir(host)
    man = Manifest(plugin / ".dal_host_manifest.json")
    if man.data["moved"] or man.data["created"]:
        sys.exit("already applied: run `restore` first")
    man.data["gen"] = str(gen)

    # 1. the original logic out of the build
    move_out(man, plugin, "Public", MOVE_PUBLIC)
    move_out(man, plugin, "Private", MOVE_PRIVATE)
    man.save()

    # 2. the generated code in (DalHost.h is DalRuntime's: one event router per process)
    for f in sorted(gen.iterdir()):
        if f.name == "DalHost.h":
            continue
        if f.suffix == ".h":
            create(man, plugin, "plugin", f"Source/ALS/Public/Dal/{f.name}", f.read_bytes())
        elif f.suffix == ".cpp":
            create(man, plugin, "plugin", f"Source/ALS/Private/Dal/{f.name}", f.read_bytes())

    man.save()

    # 3. the tracked files that have to change
    for rel, text in SHIMS.items():
        create(man, plugin, "plugin", rel, text.encode("utf-8"))
    build = read(plugin / "Source/ALS/ALS.Build.cs")
    anchor = "\t\tif (Target.bBuildEditor)\n\t\t{\n\t\t\tPrivateDependencyModuleNames.AddRange([\n\t\t\t\t\"MessageLog\""
    if "host_als.py" in build or anchor not in build.replace("\r\n", "\n"):
        sys.exit("ALS.Build.cs is not what host_als.py expects")
    nl = "\r\n" if "\r\n" in build else "\n"
    build = build.replace("\r\n", "\n").replace(anchor, ALS_BUILD_CS_EXTRA.lstrip("\n") + "\n" + anchor).replace("\n", nl)
    patch(man, plugin, "plugin", "Source/ALS/ALS.Build.cs", build.encode("utf-8"))
    up = json.loads(read(plugin / "ALS.uplugin"))
    up["Modules"] = [m for m in up["Modules"] if m["Name"] not in DISABLED_MODULES]
    if not any(p["Name"] == "DalRuntime" for p in up["Plugins"]):
        up["Plugins"].append({"Name": "DalRuntime", "Enabled": True})
    patch(man, plugin, "plugin", "ALS.uplugin", (json.dumps(up, indent="\t") + "\n").encode("utf-8"))

    man.save()

    # 4. the host project: DalRuntime, the test character and game mode
    if overlay:
        mod = host / "Source" / "DreamAnimTestHost"
        for f in sorted((HERE / "overlay").rglob("*")):
            if f.is_file():
                rel = "Source/DreamAnimTestHost/" + f.relative_to(HERE / "overlay").as_posix()
                create(man, host, "host", rel, f.read_bytes())
    man.data["host"] = str(host)
    man.save()
    print("applied: %d moved, %d created, %d patched" % (len(man.data["moved"]), len(man.data["created"]), len(man.data["patched"])))


def restore(host: Path):
    plugin = plugin_dir(host)
    man = Manifest(plugin / ".dal_host_manifest.json")
    roots = {"plugin": plugin, "host": host}
    for key in man.data["created"]:
        tag, rel = key.split(":", 1)
        (roots[tag] / rel).unlink(missing_ok=True)
    for rel in man.data["moved"]:
        area = rel.split("/")[2]
        sub = rel.split("/", 3)[3]
        src = plugin / "OriginalLogic" / area / sub
        dst = plugin / rel
        if src.exists():
            move_merge(src, dst)
    for key, b64 in man.data["patched"].items():
        tag, rel = key.split(":", 1)
        (roots[tag] / rel).write_bytes(base64.b64decode(b64))
    for d in ("Public/Dal", "Private/Dal"):
        shutil.rmtree(plugin / "Source" / "ALS" / d, ignore_errors=True)
    shutil.rmtree(plugin / "OriginalLogic", ignore_errors=True)
    (plugin / ".dal_host_manifest.json").unlink(missing_ok=True)
    print("restored")


def status(host: Path):
    plugin = plugin_dir(host)
    man = Manifest(plugin / ".dal_host_manifest.json")
    print(json.dumps({k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in man.data.items()}, indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("apply", "restore", "status"))
    ap.add_argument("--host", type=Path, default=DEFAULT_HOST)
    ap.add_argument("--gen", type=Path)
    ap.add_argument("--no-overlay", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "apply":
        if a.gen is None:
            ap.error("--gen is required")
        apply(a.host, a.gen, not a.no_overlay)
    elif a.cmd == "restore":
        restore(a.host)
    else:
        status(a.host)


if __name__ == "__main__":
    main()
