"""Command line: ``python -m dal <command> ...``."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__, ir
from .api import check_file
from .cppbackend import CppOptions, generate_cpp
from .errors import DalError
from .harness import verify
from .pybackend import generate_python


def _write_if_changed(path: str, text: str) -> bool:
    """Write ``text`` with LF endings unless the file already has exactly this content."""
    data = text.encode("utf-8")
    if os.path.isfile(path):
        with open(path, "rb") as f:
            if f.read() == data:
                return False
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return True


def _load(args) -> "tuple[ir.Module, list] | None":
    try:
        result = check_file(args.file, getattr(args, "standins", None))
    except DalError as e:
        for d in e.diagnostics:
            print(d.format(), file=sys.stderr)
        n = sum(1 for d in e.diagnostics if d.severity == "error")
        print(f"{args.file}: {n} error(s)", file=sys.stderr)
        return None
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return None
    for w in result.warnings:
        print(w.format(), file=sys.stderr)
    return result.module, result.warnings


def cmd_compile(args) -> int:
    loaded = _load(args)
    if loaded is None:
        return 1
    module, _ = loaded
    if not args.py and not args.cpp:
        print("error: give at least one of --py DIR and --cpp DIR", file=sys.stderr)
        return 2
    if args.py:
        path = os.path.join(args.py, f"{module.name}.py")
        changed = _write_if_changed(path, generate_python(module))
        print(f"{'wrote' if changed else 'up to date'} {path}")
    if args.cpp:
        opts = CppOptions(args.module, args.parent_class, args.parent_include)
        for name, text in generate_cpp(module, opts).items():
            path = os.path.join(args.cpp, name)
            changed = _write_if_changed(path, text)
            print(f"{'wrote' if changed else 'up to date'} {path}")
    return 0


def cmd_check(args) -> int:
    loaded = _load(args)
    if loaded is None:
        return 1
    module, warnings = loaded
    print(f"{args.file}: ok ({module.name}: {len(module.inputs)} inputs, {len(module.states)} states, "
          f"{len(module.params)} params, {len(module.funcs)} fns, {len(module.entries)} entries; "
          f"{len(warnings)} warning(s))")
    return 0


def cmd_verify(args) -> int:
    loaded = _load(args)
    if loaded is None:
        return 1
    module, _ = loaded
    diffs = verify(module, args.frames, args.seed, args.tol)
    if not diffs:
        print(f"{module.name}: interpreter and generated Python agree over {args.frames} frames (seed {args.seed})")
        return 0
    print(f"{module.name}: {len(diffs)} difference(s); first {min(len(diffs), 10)}:", file=sys.stderr)
    for frame, name, a, b in diffs[:10]:
        print(f"  frame {frame}: {name}: interpreter={a!r} python={b!r}", file=sys.stderr)
    return 1


def cmd_dump_ir(args) -> int:
    loaded = _load(args)
    if loaded is None:
        return 1
    sys.stdout.write(ir.dump(loaded[0]))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m dal", description="DAL (Dream Anim Logic) compiler")
    p.add_argument("--version", action="version", version=f"dal {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("compile", help="compile a .dal file to Python and/or Unreal C++")
    c.add_argument("file")
    c.add_argument("--py", metavar="DIR", help="write <Logic>.py here")
    c.add_argument("--cpp", metavar="DIR", help="write <Logic>.h/.cpp and DalMath.h here")
    c.add_argument("--module", default="", metavar="MYGAME_API", help="module API macro for the generated UCLASS")
    c.add_argument("--standins", metavar="FILE", help="Python stand-ins for extern fns (default: <file>_standins.py)")
    c.add_argument("--parent-class", default="UAnimInstance", help="C++ base class of the generated class")
    c.add_argument("--parent-include", default="Animation/AnimInstance.h", help="header that declares the base class")
    c.set_defaults(func=cmd_compile)

    k = sub.add_parser("check", help="parse and type-check only")
    k.add_argument("file")
    k.add_argument("--standins", metavar="FILE")
    k.set_defaults(func=cmd_check)

    v = sub.add_parser("verify", help="run interpreter and generated Python on random inputs and compare")
    v.add_argument("file")
    v.add_argument("--standins", metavar="FILE")
    v.add_argument("--frames", type=int, default=300)
    v.add_argument("--seed", type=int, default=1)
    v.add_argument("--tol", type=float, default=0.0, help="relative tolerance (0 = exact)")
    v.set_defaults(func=cmd_verify)

    d = sub.add_parser("dump-ir", help="print the typed IR")
    d.add_argument("file")
    d.add_argument("--standins", metavar="FILE")
    d.set_defaults(func=cmd_dump_ir)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
