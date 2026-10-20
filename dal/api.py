"""Public entry points of the compiler."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import ir
from .check import check
from .errors import DalError, Diagnostic, SourceFile
from .parser import parse_source


@dataclass
class CompileResult:
    module: ir.Module
    warnings: list[Diagnostic] = field(default_factory=list)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return f.read()


def default_standins_path(dal_path: str) -> str | None:
    """``<stem>_standins.py`` next to the ``.dal`` file, if it exists."""
    stem, _ = os.path.splitext(dal_path)
    cand = stem + "_standins.py"
    return cand if os.path.isfile(cand) else None


def check_source(text: str, filename: str = "<string>", standins=None) -> CompileResult:
    """Parse and check ``.dal`` text.

    ``standins`` is None, a path to a Python stand-in file, or a ``(filename, text)`` pair.
    Raises ``DalError`` if the program is not valid.
    """
    source = SourceFile(text, filename)
    unit = parse_source(source)
    standin = None
    if isinstance(standins, str):
        standin = (os.path.basename(standins), _read_text(standins))
    elif standins is not None:
        standin = (standins[0], standins[1])
    module, warnings = check(unit, source, standin)
    return CompileResult(module, warnings)


def check_file(path: str, standins=None) -> CompileResult:
    """Like ``check_source`` for a file; finds ``<stem>_standins.py`` automatically."""
    if standins is None:
        standins = default_standins_path(path)
    return check_source(_read_text(path), os.path.basename(path), standins)
