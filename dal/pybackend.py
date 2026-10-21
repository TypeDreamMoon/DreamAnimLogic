"""Python backend: IR -> a self-contained Python module with one logic class.

The generated module imports ``dal.runtime`` (value types and UE-semantics math)
and, if the program declares ``extern fn``, bundles the checked stand-in file.
Output is deterministic (no timestamps) so it can be diffed and golden-tested.
"""

from __future__ import annotations

from . import ir
from .builtins import BUILTIN_CONSTS, BUILTINS
from .types import BOOL, FLOAT, HIT, HIT_MEMBERS, INT, MOVEMENT_MODE, QUAT, ROTATOR, VEC2, VEC3, EnumType, Type, py_member_name

INDENT = "    "

_AS = {
    FLOAT: "float",
    INT: "int",
    BOOL: "bool",
    VEC2: "_rt.as_vec2",
    VEC3: "_rt.as_vec3",
    ROTATOR: "_rt.as_rotator",
    QUAT: "_rt.as_quat",
    HIT: "_rt.as_hit",
}


def py_enum_class(ty: EnumType) -> str:
    return "_rt.MovementMode" if ty.builtin else ty.name


def py_value(ty: Type, v) -> str:
    """Python source for a constant value of type ``ty``."""
    if ty is FLOAT:
        return repr(float(v))
    if ty is INT:
        return repr(int(v))
    if ty is BOOL:
        return "True" if v else "False"
    if ty is VEC2:
        return "_rt.Vec2(%s)" % ", ".join(repr(float(c)) for c in v)
    if ty is VEC3:
        return "_rt.Vec3(%s)" % ", ".join(repr(float(c)) for c in v)
    if ty is ROTATOR:
        return "_rt.Rotator(%s)" % ", ".join(repr(float(c)) for c in v)
    if ty is QUAT:
        return "_rt.Quat(%s)" % ", ".join(repr(float(c)) for c in v)
    if isinstance(ty, EnumType):
        return f"{py_enum_class(ty)}.{py_member_name(ty.members[int(v)])}"
    raise ValueError(f"cannot emit a {ty} constant")


def _is_nonzero_number(e: ir.Expr) -> bool:
    if isinstance(e, ir.Lit) and isinstance(e.value, (int, float)) and not isinstance(e.value, bool):
        return e.value != 0
    if isinstance(e, ir.ConstRef) and isinstance(e.value, (int, float)) and not isinstance(e.value, bool):
        return e.value != 0
    return False


def unparen(s: str) -> str:
    """Drop one redundant pair of outer parentheses: '(a + b)' -> 'a + b'."""
    if not (s.startswith("(") and s.endswith(")")):
        return s
    depth = 0
    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return s[1:-1] if i == len(s) - 1 else s
    return s


class PyGen:
    def __init__(self, m: ir.Module):
        self.m = m

    # ---- expressions ----------------------------------------------------------------
    def x(self, e: ir.Expr) -> str:
        k = type(e)
        if k is ir.Lit:
            return py_value(e.ty, e.value)
        if k is ir.EnumLit:
            return f"{py_enum_class(e.ty)}.{py_member_name(e.member)}"  # type: ignore[arg-type]
        if k is ir.ConstRef:
            if e.name in BUILTIN_CONSTS:
                return f"_rt.{e.name}"
            return f"_K_{e.name}"
        if k in (ir.ParamRef, ir.InputRef, ir.StateRef):
            return f"self.{e.name}"
        if k is ir.LocalRef:
            return e.name
        if k is ir.ToFloat:
            return f"float({self.x(e.x)})"
        if k is ir.Unary:
            if e.op == "!":
                return f"(not {self.x(e.x)})"
            return f"_rt.i32(-{self.x(e.x)})" if e.ty is INT else f"(-{self.x(e.x)})"
        if k is ir.Binary:
            return self.binary(e)
        if k is ir.Cond:
            return f"({self.x(e.a)} if {self.x(e.c)} else {self.x(e.b)})"
        if k is ir.Member:
            if e.comps in HIT_MEMBERS or len(e.comps) == 1 or e.comps in ("pitch", "yaw", "roll"):
                return f"{self.x(e.x)}.{e.comps}"
            return f"_rt.swizzle({self.x(e.x)}, {e.comps!r})"
        if k is ir.Make:
            args = ", ".join(unparen(self.x(a)) for a in e.args)
            if e.ctor == "vec2":
                return f"_rt.Vec2({args})"
            if e.ctor == "vec3":
                return f"_rt.vec3_from_vec2({args})" if e.sig_index == 1 else f"_rt.Vec3({args})"
            if e.ctor == "rotator":
                return f"_rt.Rotator({args})"
            return f"_rt.Quat({args})"
        if k is ir.CallBuiltin:
            return f"_rt.{BUILTINS[e.name].py}({', '.join(unparen(self.x(a)) for a in e.args)})"
        if k is ir.CallFn:
            return f"_fn_{e.name}({', '.join(unparen(self.x(a)) for a in e.args)})"
        if k is ir.CallExtern:
            ext = self.m.find_extern(e.name)
            call = f"{ext.py}({', '.join(unparen(self.x(a)) for a in e.args)})"  # type: ignore[union-attr]
            conv = _AS.get(e.ty, "int")
            return f"{conv}({call})"
        if k is ir.HostQuery:
            call = f"self.host.{e.name}({', '.join(unparen(self.x(a)) for a in e.args)})"
            return f"{_AS.get(e.ty, 'int')}({call})"
        raise AssertionError(k)

    def cmd_arg(self, e: ir.Expr) -> str:
        """How one command argument is passed to the host: names for montages and sections, runtime values otherwise."""
        if isinstance(e, ir.MontageRef):
            return repr(e.name)
        ty = e.ty
        if isinstance(ty, EnumType):
            if ty is MOVEMENT_MODE:
                return f"_rt.MovementMode({self.x(e)})"
            if isinstance(e, ir.EnumLit):
                return repr(None if e.member == "None" else e.member)
            return f"_rt.section_name(_SEC_{ty.name}, {self.x(e)})"
        return unparen(self.x(e))

    def binary(self, e: ir.Binary) -> str:
        a, b, op = self.x(e.a), self.x(e.b), e.op
        ta, tb = e.a.ty, e.b.ty
        if op == "&&":
            return f"({a} and {b})"
        if op == "||":
            return f"({a} or {b})"
        if op in ("==", "!=", "<", "<=", ">", ">="):
            return f"({a} {op} {b})"
        if op in ("+", "-", "*") and ta is INT and tb is INT:
            return f"_rt.i32({a} {op} {b})"
        if op == "/":
            if ta is INT and tb is INT:
                return f"_rt.idiv({a}, {b})"
            if ta is FLOAT and tb is FLOAT:
                return f"({a} / {b})" if _is_nonzero_number(e.b) else f"_rt.fdiv({a}, {b})"
            return f"({a} / {b})"
        if op == "%":
            return f"_rt.imod({a}, {b})"
        return f"({a} {op} {b})"

    # ---- statements ---------------------------------------------------------------------
    def stmts(self, body: list[ir.Stmt], depth: int) -> list[str]:
        pad = INDENT * depth
        out: list[str] = []
        for s in body:
            if isinstance(s, ir.Let):
                out.append(f"{pad}{s.name} = {unparen(self.x(s.init))}")
            elif isinstance(s, ir.Assign):
                target = f"self.{s.name}" if s.kind == "state" else s.name
                out.append(f"{pad}{target} = {unparen(self.x(s.value))}")
            elif isinstance(s, ir.If):
                kw = "if"
                cur = s
                while True:
                    out.append(f"{pad}{kw} {unparen(self.x(cur.cond))}:")
                    out.extend(self.stmts(cur.then, depth + 1) or [f"{pad}{INDENT}pass"])
                    if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ir.If):
                        cur = cur.orelse[0]
                        kw = "elif"
                        continue
                    if cur.orelse:
                        out.append(f"{pad}else:")
                        out.extend(self.stmts(cur.orelse, depth + 1) or [f"{pad}{INDENT}pass"])
                    break
            elif isinstance(s, ir.Return):
                out.append(f"{pad}return" + (f" {unparen(self.x(s.value))}" if s.value is not None else ""))
            elif isinstance(s, ir.Command):
                out.append(f"{pad}self._cmd({', '.join([repr(s.name)] + [self.cmd_arg(a) for a in s.args])})")
            elif isinstance(s, ir.For):
                out.append(f"{pad}for {s.var} in range({s.lo}, {s.hi}):")
                out.extend(self.stmts(s.body, depth + 1) or [f"{pad}{INDENT}pass"])
        return out

    # ---- module -------------------------------------------------------------------------------
    def module(self) -> str:
        from . import __version__

        m = self.m
        L: list[str] = []
        L.append(f"# Generated by dal {__version__} from {m.filename} (sha256 {m.source_sha256[:16]}...). Do not edit.")
        if m.libs:
            L.append("# libraries: " + ", ".join(f"{lib.filename} ({lib.sha256[:12]}...)" for lib in m.libs))
        L.append(f'"""DAL {"library" if m.is_library else "logic"} {m.name}: Python backend."""')
        L.append("")
        L.append("from dal import runtime as _rt")
        L.append("")

        for en in m.enums:
            L.append("")
            L.append(f"class {en.name}(_rt.IntEnum):")
            for i, mem in enumerate(en.members):
                L.append(f"{INDENT}{py_member_name(mem)} = {i}")
            L.append("")

        if m.standin_source:
            L.append("")
            L.append(f"# ---- extern stand-ins (bundled from {m.standin_filename}) ----")
            L.extend(m.standin_source.rstrip("\n").split("\n"))
            L.append(f"# ---- end of stand-ins ----")
            L.append("")

        if m.consts:
            L.append("")
            for c in m.consts:
                L.append(f"_K_{c.name} = {py_value(c.ty, c.value)}")
            L.append("")

        if m.montages:
            L.append("")
            for md in m.montages:
                L.append(f"_SEC_{md.enum.name} = ({', '.join(repr(n) for n in md.enum.members)})")
            L.append("")

        for f in m.funcs:
            L.append("")
            L.append(f"def _fn_{f.name}({', '.join(n for n, _ in f.params)}):")
            L.extend(self.stmts(f.body, 1) or [f"{INDENT}pass"])
            L.append("")

        if m.is_library:
            return "\n".join(L).rstrip("\n") + "\n"

        L.append("")
        L.append(f"class {m.name}(_rt.LogicBase):")
        L.append(f'{INDENT}DAL_NAME = "{m.name}"')
        L.append(f'{INDENT}DAL_VERSION = "{__version__}"')
        L.append(f'{INDENT}DAL_SOURCE_SHA256 = "{m.source_sha256}"')
        L.append(f"{INDENT}DAL_PARAMS = (")
        for p in m.params:
            L.append(f"{INDENT*2}({p.name!r}, {p.ty.name!r}, {py_value(p.ty, p.default)}),")
        L.append(f"{INDENT})")
        L.append(f"{INDENT}DAL_INPUTS = (")
        for i in m.inputs:
            spec = None
            if i.host:
                spec = f"host.{i.host.item}" + (f"({i.host.arg!r})" if i.host.arg is not None else "")
            L.append(f"{INDENT*2}({i.name!r}, {i.ty.name!r}, {spec!r}),")
        L.append(f"{INDENT})")
        L.append(f"{INDENT}DAL_STATES = (")
        for s in m.states:
            L.append(f"{INDENT*2}({s.name!r}, {s.ty.name!r}),")
        L.append(f"{INDENT})")
        L.append(f"{INDENT}DAL_ENTRIES = ({''.join(repr(e.name) + ', ' for e in m.entries)})")
        traffic = bool(m.handlers) or m.uses_commands
        if m.kind != "anim" or m.handlers or m.montages or m.actions:
            L.append(f"{INDENT}DAL_KIND = {m.kind!r}")
        if m.handlers:
            L.append(f"{INDENT}DAL_HANDLERS = (")
            for k, h in enumerate(m.handlers):
                vt = h.params[0][1].name if h.event == "input" else ""
                L.append(f"{INDENT*2}({h.event!r}, {h.name!r}, {h.phase!r}, '_h{k}', {vt!r}),")
            L.append(f"{INDENT})")
        L.append("")
        L.append(f"{INDENT}def __init__(self, host=None, **params):")
        L.append(f"{INDENT*2}_rt.LogicBase.__init__(self, host, params)")
        L.append(f"{INDENT*2}self.reset()")
        L.append("")
        L.append(f"{INDENT}def reset(self):")
        L.append(f'{INDENT*2}"""Restore every input and state to its declared default."""')
        L.append(f"{INDENT*2}self._dt = 0.0")
        if traffic:
            L.append(f"{INDENT*2}self._pending = []")
            L.append(f"{INDENT*2}self._cmds = []")
        for i in m.inputs:
            L.append(f"{INDENT*2}self.{i.name} = {py_value(i.ty, i.default)}")
        for s in m.states:
            L.append(f"{INDENT*2}self.{s.name} = {py_value(s.ty, s.default)}")
        L.append("")
        L.append(f"{INDENT}def _sample(self):")
        L.append(f'{INDENT*2}"""Read the host-bound inputs (a frame snapshot); overrides win over the host."""')
        bound = [i for i in m.inputs if i.host]
        if bound:
            L.append(f"{INDENT*2}h = self.host")
            L.append(f"{INDENT*2}ov = self.overrides")
        for i in bound:
            h = i.host
            assert h is not None
            if h.item == "montage_section":
                en = i.ty.name
                L.append(f"{INDENT*2}if {i.name!r} in ov:")
                L.append(f"{INDENT*3}self.{i.name} = {en}(int(ov[{i.name!r}]))")
                L.append(f"{INDENT*2}else:")
                L.append(f"{INDENT*3}n = h.montage_section({h.arg!r})")
                L.append(f"{INDENT*3}self.{i.name} = {en}(_SEC_{en}.index(n) if n in _SEC_{en} else 0)")
                continue
            if h.item == "action":
                src = f"h.action({h.arg!r}, {i.ty.name!r})"
            elif h.item == "notify":
                src = f"h.notify({h.arg!r})"
            elif h.item.startswith("montage_"):
                src = f"h.{h.item}({h.arg!r})"
            else:
                src = f"h.{h.item}"
            if isinstance(i.ty, EnumType):
                conv = f"{py_enum_class(i.ty)}(int({{}}))"
            else:
                conv = _AS[i.ty] + "({})"
            L.append(
                f"{INDENT*2}self.{i.name} = " + conv.format(f"ov[{i.name!r}] if {i.name!r} in ov else {src}")
            )
        if not bound:
            L.append(f"{INDENT*2}pass")
        L.append("")

        u = m.update
        dt_name = u.dt_name if u is not None else "dt"
        L.append(f"{INDENT}def update(self, {dt_name}):")
        L.append(f'{INDENT*2}"""One frame: sample the inputs, collect events, tick timers, run handlers, run the body, flush commands."""')
        L.append(f"{INDENT*2}self._sample()")
        if m.handlers:
            L.append(f"{INDENT*2}events = self._collect_events()")
        L.append(f"{INDENT*2}{dt_name} = float({dt_name})")
        L.append(f"{INDENT*2}self._dt = {dt_name}")
        if u is not None:
            L.extend(self.stmts(u.pre, 2))
        if m.handlers:
            L.append(f"{INDENT*2}self._dispatch(events)")
        if u is not None:
            L.extend(self.stmts(u.body, 2))
        if m.uses_commands:
            L.append(f"{INDENT*2}self._flush()")
        L.append("")
        for e in m.entries:
            L.append(f"{INDENT}def {e.name}(self):")
            L.append(f'{INDENT*2}"""Entry: runs with the delta time of the last update."""')
            L.append(f"{INDENT*2}{e.dt_name} = self._dt")
            L.extend(self.stmts(e.body, 2) or [f"{INDENT*2}pass"])
            if m.uses_commands:
                L.append(f"{INDENT*2}self._flush()")
            L.append("")
        for k, h in enumerate(m.handlers):
            ps = "".join(f", {n}" for n, _t in h.params)
            what = f"on {h.event} {h.name}" + (f" {h.phase}" if h.phase else "")
            L.append(f"{INDENT}def _h{k}(self{ps}):")
            L.append(f"{INDENT*2}# {what}")
            L.extend(self.stmts(h.body, 2) or [f"{INDENT*2}pass"])
            L.append("")
        return "\n".join(L).rstrip("\n") + "\n"


def generate_python(module: ir.Module) -> str:
    return PyGen(module).module()


def load_class(module: ir.Module, source: str | None = None):
    """Exec the generated code and return the logic class (used by tests and tools)."""
    src = source if source is not None else generate_python(module)
    ns: dict = {"__name__": f"dal_generated_{module.name}"}
    exec(compile(src, f"<dal:{module.name}>", "exec"), ns)  # noqa: S102
    return ns if module.is_library else ns[module.name]
