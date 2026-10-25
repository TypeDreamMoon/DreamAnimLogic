#!/usr/bin/env python
"""Runs the c3 comparison scenarios (moon/tools/c3_ue_compare.py of the Blender fork) in the sandbox host instead of DevTest.

    python c3_host_compare.py [--set als_ext_plain|als_ext] [--only a,b] [--out DIR]

moon/tools/c3_ue_compare.py has the engine and the project of DevTest hard-wired and refuses to run next to a DevTest editor.  This wrapper
imports it (read only: no bytecode is written next to it) and points it at I:\\UnrealEngine\\UE_5.8 and DreamVEdAnimHost, where the
`als_cmc` character class (/Script/DalRuntime.DalTestCharacter) exists, and then runs its own main().  Everything else -- the field, the
scenarios, the evaluation, the report -- is the fork's.  The editor script builds its field in a new unsaved map and never saves an
asset (the fork's driver checks that no .umap changed).
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
FORK = Path(r"I:/DCC/blender-moon-engine")
TOOL = FORK / "moon" / "tools" / "c3_ue_compare.py"
ENGINE = r"I:\UnrealEngine\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe"
PROJECT = r"I:\UnrealProject_Moon\DEV_58\DreamVEdAnimHost\DreamAnimTestHost.uproject"
OUT = r"G:\DCC\scratch\h5\c3"


BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_NO_WINDOW = 0x08000000


class _QuietSubprocess:
    """The subprocess module with every child started without a window and at below-normal priority (the editor, the PowerShell queries)."""

    def __getattr__(self, name):
        return getattr(subprocess, name)

    @staticmethod
    def Popen(*args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW
        return subprocess.Popen(*args, **kwargs)

    @staticmethod
    def run(*args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW
        return subprocess.run(*args, **kwargs)


def main(argv=None) -> int:
    spec = importlib.util.spec_from_file_location("c3_ue_compare_fork", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.subprocess = _QuietSubprocess()
    mod.UNREAL_EDITOR = ENGINE
    mod.PROJECT = PROJECT
    mod.DEFAULT_OUT = OUT
    args = list(sys.argv[1:] if argv is None else argv)
    if "--out" not in args:
        args += ["--out", OUT]
    return mod.main(args)


if __name__ == "__main__":
    sys.exit(main())
