"""Find the ``dal`` compiler package and make it importable (no ``unreal`` import).

The compiler is one Python package (``dal/``, pure Python 3.11+).  DalLive does not carry a copy; it looks, in order, at

1. the environment variable ``DREAMANIM_DAL`` (the folder that contains ``dal/`` -- the same variable ``dab.ps1`` uses),
2. the environment variable ``DAL_COMPILER`` (same meaning),
3. ``<project>/Config/DalLive.json``: ``{"compiler": "<folder that contains dal/>"}``,
4. the repository layout: this plugin lives in ``<DreamAnimLogic>/Unreal/DalLive``,
5. a vendored copy next to this package (``Content/Python/dal``).
"""

from __future__ import annotations

import importlib
import json
import os
import sys

PLUGIN_PYTHON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # .../DalLive/Content/Python


def _has_dal(folder: str | None) -> bool:
    return bool(folder) and os.path.isfile(os.path.join(folder, "dal", "__init__.py"))


def candidates(project_dir: str | None = None) -> list[tuple[str, str]]:
    """(source, folder) pairs in search order; the folder may not contain the package."""
    out: list[tuple[str, str]] = []
    for var in ("DREAMANIM_DAL", "DAL_COMPILER"):
        value = os.environ.get(var)
        if value:
            out.append((f"environment variable {var}", os.path.abspath(value)))
    if project_dir:
        cfg = os.path.join(project_dir, "Config", "DalLive.json")
        if os.path.isfile(cfg):
            try:
                with open(cfg, "r", encoding="utf-8-sig") as f:
                    folder = json.load(f).get("compiler")
                if folder:
                    out.append((cfg, os.path.abspath(os.path.join(project_dir, folder))))
            except (OSError, ValueError):
                pass
    plugin_root = os.path.dirname(os.path.dirname(PLUGIN_PYTHON_DIR))                # .../DalLive
    out.append(("repository layout", os.path.dirname(os.path.dirname(plugin_root))))  # .../DreamAnimLogic
    out.append(("vendored copy", PLUGIN_PYTHON_DIR))
    return out


def find_dal(project_dir: str | None = None) -> tuple[str, str] | None:
    for source, folder in candidates(project_dir):
        if _has_dal(folder):
            return source, folder
    return None


def ensure_dal(project_dir: str | None = None):
    """Import and return the ``dal`` package (raises RuntimeError with instructions when it cannot be found)."""
    try:
        return importlib.import_module("dal")
    except ImportError:
        pass
    found = find_dal(project_dir)
    if found is None:
        looked = "; ".join(f"{s}: {p}" for s, p in candidates(project_dir))
        raise RuntimeError(
            "DalLive cannot find the dal compiler package. Set the environment variable DREAMANIM_DAL to the folder that "
            "contains dal/ (e.g. the DreamAnimLogic checkout) or put \"compiler\" into <project>/Config/DalLive.json. "
            f"Looked at: {looked}"
        )
    _source, folder = found
    if folder not in sys.path:
        sys.path.insert(0, folder)
    return importlib.import_module("dal")
