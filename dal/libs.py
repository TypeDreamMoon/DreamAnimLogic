"""Library files: ``use "x.dal";``.

A library is a ``.dal`` file without a ``logic`` line that declares shared types and helpers: enums, structs, tag
sets, settings types, constants, functions and extern functions.  ``use`` is a compile-time merge, not a runtime
import: the declarations of every library (dependencies first, each file once) are placed in front of the
declarations of the file that uses them, and the checker sees one flat list.  Every declaration remembers the file
it was written in, so a diagnostic in a library points into the library.

The same machinery loads the logic a ``peer`` / ``linked`` declaration names (``<Logic>.dal`` next to the
file), see ``check.py``.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
from dataclasses import dataclass, field

from . import syntax as S
from .errors import DalError, SourceFile
from .parser import parse_source

# declarations a library file may contain
LIBRARY_DECLS = ("UseDecl", "EnumDecl", "ConstDecl", "FnDecl", "ExternDecl", "StructDecl", "TagsDecl", "SettingsDecl")


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return f.read()


@dataclass
class LibFile:
    key: str                      # normalised path (the cache key)
    filename: str                 # as shown in diagnostics, relative to the project root
    source: SourceFile
    unit: S.SourceUnit
    sha256: str
    deps: list[str] = field(default_factory=list)      # keys of the libraries this one uses directly

    @property
    def stem(self) -> str:
        """``als_lib.dal`` -> ``AlsLib``: the name of the generated C++ files."""
        return camel_stem(self.filename)


def camel_stem(filename: str) -> str:
    base = posixpath.basename(filename.replace("\\", "/"))
    base = base.rsplit(".", 1)[0] if "." in base else base
    parts = [p for p in base.replace("-", "_").split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts) or "Library"


class CompileContext:
    """What one compilation shares: where files come from, the libraries already read, the peers already summarised."""

    def __init__(self, main_key: str = "<string>", files: dict[str, str] | None = None, root: str | None = None):
        self.files = {posixpath.normpath(k.replace("\\", "/")): v for k, v in files.items()} if files is not None else None
        self.main_key = self._norm(main_key)
        if root is not None:
            self.root = self._norm(root)
        elif self.files is not None:
            self.root = posixpath.dirname(self.main_key)
        else:
            self.root = os.path.dirname(self.main_key) if os.path.isabs(self.main_key) else os.getcwd()
        self.libs: dict[str, LibFile] = {}
        self.types: dict[tuple[str, str], object] = {}        # (library key, name) -> type object shared by every checker
        self.ifaces: dict[str, object] = {}                   # logic file key -> its interface (peer / linked)
        self.loading: list[str] = []

    # ---- paths ---------------------------------------------------------------------------------------------
    def _norm(self, key: str) -> str:
        if self.files is not None:
            return posixpath.normpath(key.replace("\\", "/"))
        return os.path.normpath(os.path.abspath(key)) if os.path.isabs(key) or os.path.exists(key) else key

    def display(self, key: str) -> str:
        """Path of a file relative to the project root, with forward slashes."""
        if self.files is not None:
            root = self.root
            rel = posixpath.relpath(key, root) if root else key
        else:
            try:
                rel = os.path.relpath(key, self.root)
            except ValueError:
                rel = key
        return rel.replace("\\", "/")

    def resolve(self, from_key: str, path: str) -> tuple[str, str]:
        """(key, text) of the file ``path`` names, relative to the file ``from_key``.

        Raises ``FileNotFoundError`` when it does not exist and ``ValueError`` when it leaves the project root.
        """
        if self.files is not None:
            base = posixpath.dirname(from_key)
            key = posixpath.normpath(posixpath.join(base, path.replace("\\", "/")))
            root = self.root
            if root in ("", "."):
                inside = not (key == ".." or key.startswith("../"))
            else:
                inside = key == root or key.startswith(root.rstrip("/") + "/")
            if not inside:
                raise ValueError(path)
            if key not in self.files:
                raise FileNotFoundError(path)
            return key, self.files[key]
        base = os.path.dirname(from_key) if os.path.isabs(from_key) else os.getcwd()
        key = os.path.normpath(os.path.join(base, path))
        try:
            inside = os.path.commonpath([os.path.normcase(self.root), os.path.normcase(key)]) == os.path.normcase(self.root)
        except ValueError:
            inside = False
        if not inside:
            raise ValueError(path)
        if not os.path.isfile(key):
            raise FileNotFoundError(path)
        return key, read_text(key)

    # ---- loading --------------------------------------------------------------------------------------------
    def load(self, unit: S.SourceUnit, source: SourceFile, key: str) -> list[LibFile]:
        """The libraries ``unit`` uses, directly and indirectly: dependencies first, each file once.

        Raises ``DalError`` for a missing or unreadable file (DAL3073), a path that leaves the project root
        (DAL3074), a circular ``use`` (DAL3070), declarations a library may not contain (DAL3071) and syntax errors.
        """
        order: list[LibFile] = []
        done: set[str] = set()
        stack: list[str] = [key]

        def visit(u: S.SourceUnit, src: SourceFile, k: str) -> None:
            for d in u.decls:
                if not isinstance(d, S.UseDecl):
                    continue
                try:
                    lkey, text = self.resolve(k, d.path)
                except FileNotFoundError:
                    raise DalError([src.diag("DAL3073", f"cannot read library '{d.path}': no such file (paths are relative to the including file)", d.path_pos)])
                except ValueError:
                    raise DalError([src.diag("DAL3074", f"library path '{d.path}' leaves the project root", d.path_pos)])
                except OSError as e:
                    raise DalError([src.diag("DAL3073", f"cannot read library '{d.path}': {e}", d.path_pos)])
                if k != key:
                    deps = self.libs[k].deps
                    if lkey not in deps:
                        deps.append(lkey)
                if lkey in stack:
                    cycle = [self.display(x) for x in stack[stack.index(lkey):]] + [self.display(lkey)]
                    raise DalError([src.diag("DAL3070", "circular use: " + " -> ".join(cycle), d.path_pos)])
                if lkey in done:
                    continue
                lib = self.libs.get(lkey)
                if lib is None:
                    lsrc = SourceFile(text, self.display(lkey))
                    lunit = parse_source(lsrc)
                    self.check_library_decls(lunit, lsrc)
                    lib = LibFile(lkey, lsrc.filename, lsrc, lunit, hashlib.sha256(lsrc.text.encode("utf-8")).hexdigest())
                    self.libs[lkey] = lib
                stack.append(lkey)
                visit(lib.unit, lib.source, lkey)
                stack.pop()
                done.add(lkey)
                order.append(lib)

        visit(unit, source, key)
        return order

    @staticmethod
    def check_library_decls(unit: S.SourceUnit, source: SourceFile) -> None:
        bad = [d for d in unit.decls if type(d).__name__ not in LIBRARY_DECLS]
        if bad:
            d = bad[0]
            what = {"LogicDecl": "a 'logic' line", "ParamDecl": "a param", "InputDecl": "an input", "StateDecl": "a state",
                    "UpdateDecl": "an update", "EntryDecl": "an entry", "HandlerDecl": "an 'on' handler"}.get(type(d).__name__, type(d).__name__.replace("Decl", "").lower())
            raise DalError([source.diag("DAL3071", f"a library file declares shared types and helpers (enum, struct, tags, settings, const, fn, extern fn); "
                                                   f"it cannot contain {what}", d.name_pos or d.pos)])


def merge(unit: S.SourceUnit, source: SourceFile, key: str, libs: list[LibFile]) -> S.SourceUnit:
    """The declaration list the checker sees: library declarations (in ``use`` order), then the file's own."""
    out: list[S.Decl] = []
    for lib in libs:
        for d in lib.unit.decls:
            if isinstance(d, S.UseDecl):
                continue
            d.src = lib.source
            d.origin = lib.key
            out.append(d)
    for d in unit.decls:
        if isinstance(d, S.UseDecl):
            continue
        d.src = source
        d.origin = None
        out.append(d)
    return S.SourceUnit(out)
