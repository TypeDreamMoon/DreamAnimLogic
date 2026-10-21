"""Create the W4 test project from the installed Unreal Engine 5.8 Third Person C++ template (no downloads).

    python create_project.py [--dest G:\\DCC\\DalLiveHost] [--engine I:\\UnrealEngine\\UE_5.8]

What it does:
  1. copies Templates/TP_ThirdPerson (Source, Config) and renames TP_ThirdPerson -> DalLiveHost like the engine's
     project creation does with TemplateDefs.ini; drops the template's variants; makes its classes non-abstract;
  2. lays the overlay on top (this folder's `overlay/`: the module's Build.cs, the test character / game mode / library /
     timed components, config, the .uproject);
  3. copies the shared Mannequin and Input content packs of the template into Content/;
  4. compiles the example programs into Source/DalLiveHost/Dal with the compiler of this repository (hero_gait with --bag);
  5. copies the DalLive plugin into Plugins/ (re-run `scripts/sync_plugin.cmd` after changing the plugin).

Then:  scripts\\build.cmd, scripts\\run_editor.ps1 -Script scripts\\make_content.py (once), scripts\\run_all.ps1.
To remove the project: delete the folder (nothing outside it is touched; the engine is not modified).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
OLD, NEW = "TP_ThirdPerson", "DalLiveHost"
TEXT_EXT = (".cpp", ".h", ".ini", ".cs")


def convert(text: str) -> str:
    text = text.replace(OLD.upper(), NEW.upper()).replace(OLD.lower(), NEW.lower())
    return re.sub(re.escape(OLD), NEW, text, flags=re.IGNORECASE)


def copy_tree(src_root: str, dst_root: str) -> None:
    for dirpath, dirnames, filenames in os.walk(src_root):
        rel = os.path.relpath(dirpath, src_root)
        dirnames[:] = [d for d in dirnames if not d.startswith("Variant_")]
        if "Variant_" in rel:
            continue
        out_dir = os.path.join(dst_root, convert(rel) if rel != "." else "")
        os.makedirs(out_dir, exist_ok=True)
        for name in filenames:
            src, dst = os.path.join(dirpath, name), os.path.join(out_dir, convert(name))
            if name.lower().endswith(TEXT_EXT):
                with open(src, "r", encoding="utf-8-sig", newline="") as f:
                    data = f.read()
                with open(dst, "w", encoding="utf-8", newline="") as f:
                    f.write(convert(data))
            else:
                shutil.copy2(src, dst)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=r"G:\DCC\DalLiveHost")
    ap.add_argument("--engine", default=r"I:\UnrealEngine\UE_5.8")
    args = ap.parse_args()
    dest, engine = args.dest, args.engine
    if os.path.exists(dest):
        print(f"{dest} exists; delete it first", file=sys.stderr)
        return 1
    template = os.path.join(engine, "Templates", "TP_ThirdPerson")
    resources = os.path.join(engine, "Templates", "TemplateResources", "High")

    os.makedirs(dest)
    copy_tree(os.path.join(template, "Source"), os.path.join(dest, "Source"))
    copy_tree(os.path.join(template, "Config"), os.path.join(dest, "Config"))
    for junk in ("TemplateDefs.ini", "config.ini"):
        p = os.path.join(dest, "Config", junk)
        if os.path.exists(p):
            os.remove(p)
    for header in ("DalLiveHostCharacter.h", "DalLiveHostGameMode.h", "DalLiveHostPlayerController.h"):
        p = os.path.join(dest, "Source", "DalLiveHost", header)
        with open(p, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace("UCLASS(abstract)", "UCLASS()"))

    game_ini = os.path.join(dest, "Config", "DefaultGame.ini")
    with open(game_ini, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    with open(game_ini, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace("ProjectName=Third Person Game Template", "ProjectName=DalLiveHost"))

    shutil.copytree(os.path.join(HERE, "overlay"), dest, dirs_exist_ok=True)

    for pack, target in (("Characters", "Characters"), ("Input", "Input")):
        shutil.copytree(os.path.join(resources, pack, "Content"), os.path.join(dest, "Content", target))

    dal_dir = os.path.join(dest, "Source", "DalLiveHost", "Dal")
    for example, extra in (("melee_combo", []), ("lockon_camera", []), ("hero_gait", ["--bag"])):
        subprocess.run([sys.executable, "-m", "dal", "compile", os.path.join(REPO, "examples", example + ".dal"),
                        "--cpp", dal_dir, "--module", "DALLIVEHOST_API", *extra], cwd=REPO, check=True)

    sync = os.path.join(HERE, "scripts", "sync_plugin.cmd")
    subprocess.run(["cmd", "/d", "/c", sync, dest], check=False)
    print(f"created {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
