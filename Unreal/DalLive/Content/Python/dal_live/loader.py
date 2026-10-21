"""Read, check and describe a .dal file for the live session (no ``unreal`` import)."""

from __future__ import annotations

import os
import traceback
from dataclasses import dataclass, field

import dal
from dal.api import default_standins_path
from dal.errors import DalError
from dal.types import BOOL, FLOAT, INT, QUAT, ROTATOR, VEC2, VEC3, EnumType


@dataclass
class LoadResult:
    module: object = None                       # dal.ir.Module when the file checked clean
    errors: list = field(default_factory=list)  # "file(line,col): error DALnnnn: message" lines (+ source excerpt lines)
    warnings: list = field(default_factory=list)
    fingerprint: object = None                  # what the file system said when the source was read
    externs: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.module is not None


def fingerprint(path: str):
    """(mtime_ns, size) of the .dal file and of its stand-in file; None while the .dal file cannot be read."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    parts = [(st.st_mtime_ns, st.st_size)]
    standins = default_standins_path(path)
    if standins:
        try:
            s2 = os.stat(standins)
            parts.append((s2.st_mtime_ns, s2.st_size))
        except OSError:
            parts.append(None)
    return tuple(parts)


def format_diagnostic(d, path: str) -> list[str]:
    """One compiler diagnostic as a Visual Studio style line (the file name is the full path) plus the quoted source."""
    line = f"{path}({d.pos.line},{d.pos.col}): {d.severity} {d.code}: {d.message}"
    out = [line]
    if d.source_line and d.pos.line > 0:
        text = d.source_line.rstrip("\r\n").expandtabs(4)
        out.append("    " + text)
        out.append("    " + " " * max(d.pos.col - 1, 0) + "^" * max(d.pos.span_end() - d.pos.col, 1))
    return out


def load_program(path: str) -> LoadResult:
    """Check ``path``.  Never raises: problems come back as ``errors`` (the caller keeps running the last good program)."""
    result = LoadResult(fingerprint=fingerprint(path))
    try:
        checked = dal.check_file(path)
    except DalError as e:
        for d in e.diagnostics:
            result.errors.extend(format_diagnostic(d, path))
        return result
    except OSError as e:
        result.errors.append(f"{path}: error: cannot read the file: {e}")
        return result
    except Exception as e:  # a compiler bug must not take the editor session down
        result.errors.append(f"{path}: error: internal compiler error: {type(e).__name__}: {e}")
        result.errors.extend("    " + ln for ln in traceback.format_exc().splitlines()[-6:])
        return result
    for w in checked.warnings:
        result.warnings.extend(format_diagnostic(w, path))
    try:
        from dal.harness import load_standins

        result.externs = load_standins(checked.module)
    except Exception as e:
        result.errors.append(f"{path}: error: cannot load the extern stand-ins: {type(e).__name__}: {e}")
        return result
    result.module = checked.module
    return result


def parse_param(ty, text: str):
    """The value of a ``param`` override written as text, in the runtime representation of ``ty`` (ValueError if invalid)."""
    s = text.strip()
    if ty is FLOAT:
        return float(s)
    if ty is INT:
        return int(float(s))
    if ty is BOOL:
        low = s.lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"not a bool: {text!r}")
    if ty in (VEC2, VEC3, ROTATOR, QUAT):
        parts = [p for p in s.strip("()[] ").replace(";", ",").split(",") if p.strip()]
        want = {VEC2: 2, VEC3: 3, ROTATOR: 3, QUAT: 4}[ty]
        if len(parts) != want:
            raise ValueError(f"{ty.name} needs {want} components: {text!r}")
        return tuple(float(p) for p in parts)
    if isinstance(ty, EnumType):
        if s in ty.members:
            return ty.members.index(s)
        return int(s)
    raise ValueError(f"cannot override a {ty}")
