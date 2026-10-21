"""Python backend: IR -> a self-contained Python module with one logic class.

The generated module imports ``dal.runtime`` (value types and UE-semantics math)
and, if the program declares ``extern fn``, bundles the checked stand-in file.
Output is deterministic (no timestamps) so it can be diffed and golden-tested.
"""

from __future__ import annotations

from . import ir
from .builtins import BUILTIN_CONSTS, BUILTINS
from .hostapi import COMMANDS, HOOK_EVENTS, HOST_ITEMS
from .types import BOOL, FLOAT, HIT, HIT_MEMBERS, NAME_T, NONE_T, SPRING, SPRING3, SPRING_MEMBERS, TRANSFORM, INT, MOVEMENT_MODE, QUAT, ROTATOR, VEC2, VEC3, VOID, ArrayType, AssetType, EnumType, MapType, StructType, TagType, Type, py_member_name, zero_value

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
    TRANSFORM: "_rt.as_transform",
    SPRING: "_rt.as_spring",
    SPRING3: "_rt.as_spring3",
}


def py_enum_class(ty: EnumType) -> str:
    return f"_rt.{ty.name}" if ty.builtin else ty.name


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
    if ty is TRANSFORM:
        return "_rt.Transform(%s)" % ", ".join(py_value(t, c) for t, c in zip((VEC3, QUAT, VEC3), v))
    if isinstance(ty, EnumType):
        return f"{py_enum_class(ty)}.{py_member_name(ty.members[int(v)])}"
    if isinstance(ty, TagType):
        return repr(str(v))
    if isinstance(ty, StructType):
        return f"{ty.name}(" + ", ".join(f"{f.name}={py_value(f.ty, getattr(v, f.name))}" for f in ty.fields if not f.derived) + ")"
    if isinstance(ty, AssetType):
        return "None" if v is None else repr(str(v))
    if ty is NAME_T:
        return repr(str(v))
    if ty is NONE_T:
        return "None"
    if isinstance(ty, MapType):
        return "{" + ", ".join(f"{py_value(ty.key, k)}: {py_value(ty.value, x)}" for k, x in v.items()) + "}"
    if isinstance(ty, ArrayType):
        return "(" + "".join(py_value(ty.elem, x) + ", " for x in v) + ")"
    raise ValueError(f"cannot emit a {ty} constant")


def py_coercer(ty: Type) -> str:
    """A callable (as source) that converts a loose value to the runtime representation of ``ty``."""
    if ty in _AS:
        return _AS[ty]
    if isinstance(ty, StructType):
        return f"{ty.name}.from_value"
    if isinstance(ty, TagType):
        return "_rt.as_tag"
    if isinstance(ty, EnumType):
        return py_enum_class(ty)
    return "_rt.as_asset"


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
        self.self_locals = False          # in the body of _derive the members of the settings are attributes of self

    # ---- value semantics of structs -------------------------------------------------------
    @staticmethod
    def is_place(e: ir.Expr) -> bool:
        """An expression that names storage (so its struct value must be copied before it is stored)."""
        if isinstance(e, (ir.StateRef, ir.InputRef, ir.ParamRef, ir.LocalRef, ir.ConstRef)):
            return True
        if isinstance(e, ir.Index):
            return True
        return isinstance(e, ir.Member) and PyGen.is_place(e.x)

    def val(self, e: ir.Expr) -> str:
        """The expression as a value to store: structs read from storage are copied."""
        text = unparen(self.x(e))
        if isinstance(e.ty, StructType) and not e.ty.is_settings and self.is_place(e):
            return f"{self.x(e)}.copy()"
        return text

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
            return ("self." + e.name) if self.self_locals else e.name
        if k is ir.ToFloat:
            return f"float({self.x(e.x)})"
        if k is ir.Unary:
            if e.op == "!":
                return f"(not {self.x(e.x)})"
            return f"_rt.i32(-{self.x(e.x)})" if e.ty is INT else f"(-{self.x(e.x)})"
        if k is ir.Binary:
            return self.binary(e)
        if k is ir.Cond:
            if isinstance(e.ty, StructType):
                return f"({self.val(e.a)} if {self.x(e.c)} else {self.val(e.b)})"
            return f"({self.x(e.a)} if {self.x(e.c)} else {self.x(e.b)})"
        if k is ir.Valid:
            return f"bool({self.x(e.x)})"
        if k is ir.CurveEval:
            fn = "curve_eval" if e.ty is FLOAT else "curve3_eval"
            return f"_rt.{fn}({unparen(self.x(e.curve))}, {unparen(self.x(e.x))})"
        if k is ir.Index:
            default = f"lambda: {py_value(e.ty, zero_value(e.ty))}" if isinstance(e.ty, StructType) else py_value(e.ty, zero_value(e.ty))
            fn = "map_get" if isinstance(e.base.ty, MapType) else "array_get"
            return f"_rt.{fn}({unparen(self.x(e.base))}, {unparen(self.x(e.index))}, {default})"
        if k is ir.MapHas:
            return f"({unparen(self.x(e.key))} in {unparen(self.x(e.table))})"
        if k is ir.ArrayLen:
            return f"len({unparen(self.x(e.items))})"
        if k is ir.CallMethod or k is ir.CallView:
            x = self.m.find_method(e.name)
            name = e.name if x.pub else ("_v_" if x.is_view else "_m_") + e.name       # type: ignore[union-attr]
            return f"self.{name}({', '.join(unparen(self.x(a)) for a in e.args)})"
        if k is ir.CallPeer:
            args = "(" + "".join(self.val(a) + ", " for a in e.args) + ")"
            if e.ty is VOID:
                return f"self._peer_call({e.peer!r}, {e.method!r}, {args}, None, {e.queued})"
            default = py_value(e.ty, zero_value(e.ty))
            return f"{py_coercer(e.ty)}(self._peer_call({e.peer!r}, {e.method!r}, {args}, {default}))"
        if k is ir.MakeStruct:
            return f"{e.ty.name}(" + ", ".join(f"{n}={self.val(v)}" for n, v in e.fields) + ")"
        if k is ir.Member:
            if isinstance(e.x.ty, StructType):
                return f"{self.x(e.x)}.{e.comps}"
            if e.comps in HIT_MEMBERS or e.x.ty in SPRING_MEMBERS or e.x.ty is TRANSFORM or len(e.comps) == 1 or e.comps in ("pitch", "yaw", "roll"):
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
            if e.ctor == "transform":
                return f"_rt.Transform({args})"
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
            if ty.builtin:
                return f"_rt.{ty.name}({self.x(e)})"                        # MovementMode, MontageBlendMode ...
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
                out.append(f"{pad}{s.name} = {self.val(s.init)}")
            elif isinstance(s, ir.Assign):
                target = f"self.{s.name}" if s.kind == "state" else s.name
                out.append(f"{pad}{target} = {self.val(s.value)}")
            elif isinstance(s, ir.AssignField):
                target = (f"self.{s.name}" if s.kind == "state" else s.name) + "".join("." + m for m in s.path)
                out.append(f"{pad}{target} = {self.val(s.value)}")
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
            elif isinstance(s, ir.ExprStmt):
                out.append(f"{pad}{unparen(self.x(s.expr))}")
            elif isinstance(s, ir.Return):
                out.append(f"{pad}return" + (f" {self.val(s.value)}" if s.value is not None else ""))
            elif isinstance(s, ir.Command):
                implicit = ["self._source" for _x in COMMANDS[s.name].implicit]               # host.montage_stop_source: the montage instance of the notify
                out.append(f"{pad}self._cmd({', '.join([repr(s.name)] + implicit + [self.cmd_arg(a) for a in s.args])})")
            elif isinstance(s, ir.For):
                out.append(f"{pad}for {s.var} in range({s.lo}, {s.hi}):")
                out.extend(self.stmts(s.body, depth + 1) or [f"{pad}{INDENT}pass"])
        return out

    # ---- tag sets ---------------------------------------------------------------------------
    def tag_class(self, ts: TagType) -> list[str]:
        L = ["", f"class {ts.name}:", f'{INDENT}"""Gameplay tag set \'{ts.prefix}\': a tag value is its full name, the empty string is no tag."""',
             f"{INDENT}PREFIX = {ts.prefix!r}", f"{INDENT}None_ = ''"]
        for mem in ts.members:
            L.append(f"{INDENT}{py_member_name(mem)} = {ts.full_name(mem)!r}")
        L.append("")
        return L

    def entry_signature(self, e: ir.EntryDef) -> str:
        """`self, A, B=1.0, C=None`: a struct default is made when the call is made (None stands for it), the others are constants."""
        parts = ["self"]
        for p in e.params:
            if not p.has_default:
                parts.append(p.name)
            elif isinstance(p.ty, StructType):
                parts.append(f"{p.name}=None")
            else:
                parts.append(f"{p.name}={py_value(p.ty, p.default)}")
        return ", ".join(parts)

    def value_types(self) -> list[Type]:
        """Struct and tag types the class stores or accepts (params, inputs, states), in declaration order."""
        seen: list[Type] = []
        for d in [*self.m.params, *self.m.inputs, *self.m.states]:
            if isinstance(d.ty, (StructType, TagType)) and d.ty not in seen:
                seen.append(d.ty)
        for h in self.m.handlers:                                # the payload of a declared notify is converted from the event's dict
            for _n, ty in h.params:
                if isinstance(ty, StructType) and ty.is_notify and ty not in seen:
                    seen.append(ty)
        return seen

    # ---- struct classes ---------------------------------------------------------------------
    def struct_class(self, st: StructType) -> list[str]:
        settings = st.is_settings
        L = ["", f"class {st.name}(_rt.Struct):"]
        names = [f.name for f in st.fields]
        slots = "".join(repr(n) + ", " for n in names)
        L.append(f"{INDENT}__slots__ = ({slots})")
        L.append(f"{INDENT}_FIELDS = __slots__")
        L.append(f'{INDENT}_TYPE_NAME = "{st.name}"')
        if settings:
            L.append(f"{INDENT}_IMMUTABLE = True")
            unflat = [f.name for f in st.fields if isinstance(f.ty, (MapType, ArrayType)) or (isinstance(f.ty, AssetType) and f.ty.settings_only)]
            if unflat:
                L.append(f"{INDENT}_UNFLAT = {tuple(unflat)!r}")
        plain = [f for f in st.fields if not f.derived]
        if plain:
            # the properties of an animation notify come from the host as plain data: an enum member may be named
            conv = "".join(f"({f.name!r}, " + (f"lambda v: _rt.as_enum({py_enum_class(f.ty)}, v)" if st.is_notify and isinstance(f.ty, EnumType) else py_coercer(f.ty)) + "), " for f in plain)
            L.append(f"{INDENT}_SPEC = ({conv})")
        factories = [f for f in plain if isinstance(f.ty, StructType)]
        if factories:
            # a nested struct is made on demand; the factory is a lambda so that its body sees the module-level class
            # names, not the constructor parameter of the same name
            L.append("")
            for f in factories:
                L.append(f"{INDENT}_new_{f.name} = staticmethod(lambda: {py_value(f.ty, f.default)})")
        L.append("")
        params = ["self"]
        body: list[str] = []
        for f in plain:
            if isinstance(f.ty, StructType):
                params.append(f"{f.name}=None")
                body.append(f"{INDENT*2}self.{f.name} = self._new_{f.name}() if {f.name} is None else {f.name}")
            elif isinstance(f.ty, (MapType, ArrayType)):
                params.append(f"{f.name}=None")
                empty = "{}" if isinstance(f.ty, MapType) else "()"
                body.append(f"{INDENT*2}self.{f.name} = {empty} if {f.name} is None else {f.name}")
            else:
                params.append(f"{f.name}={py_value(f.ty, f.default) if f.default is not None else 'None'}")
                body.append(f"{INDENT*2}self.{f.name} = {f.name}")
        derived = [f for f in st.fields if f.derived]
        for f in derived:
            body.append(f"{INDENT*2}self.{f.name} = {py_value(f.ty, zero_value(f.ty))}")
        if derived:
            body.append(f"{INDENT*2}self._derive()")
        L.append(f"{INDENT}def __init__({', '.join(params)}):")
        L.extend(body or [f"{INDENT*2}pass"])
        if derived:
            L.append("")
            L.append(f"{INDENT}def _derive(self):")
            L.append(f'{INDENT*2}"""The derived members, computed from the others."""')
            self.self_locals = True
            for f in derived:
                L.append(f"{INDENT*2}self.{f.name} = {unparen(self.x(f.expr))}")
            self.self_locals = False
        if not settings:
            L.append("")
            L.append(f"{INDENT}def copy(self):")
            args = ", ".join(f"self.{f.name}.copy()" if isinstance(f.ty, StructType) and not f.ty.is_settings else f"self.{f.name}" for f in st.fields)
            L.append(f"{INDENT*2}return {st.name}({args})")
        else:
            L.append("")
            L.append(f"{INDENT}@classmethod")
            L.append(f"{INDENT}def load(cls, source=None, resolver=None, report=None):")
            L.append(f'{INDENT*2}"""An instance from a JSON file, a dict (see dal.settings) or None (the declared defaults)."""')
            L.append(f"{INDENT*2}return _settings.load(DAL_SCHEMA, DAL_CLASSES, {st.name!r}, source, resolver, report)")
        L.append("")
        return L

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
        if any(st.is_settings for st in m.structs):
            L.append("from dal import settings as _settings")
        L.append("")

        for en in m.enums:
            L.append("")
            L.append(f"class {en.name}(_rt.IntEnum):")
            for i, mem in enumerate(en.members):
                L.append(f"{INDENT}{py_member_name(mem)} = {i}")
            L.append("")

        for ts in m.tagsets:
            L.extend(self.tag_class(ts))
        for st in m.structs:
            L.extend(self.struct_class(st))
        for nd in m.notifies:                                   # the payload of an animation notify: its properties
            L.extend(self.struct_class(nd.struct))
        if any(st.is_settings for st in m.structs):
            from .settings import schema_of

            L.append("")
            L.append("DAL_SCHEMA = " + repr(schema_of(m.structs)))
            L.append("DAL_CLASSES = {" + ", ".join(f"{st.name!r}: {st.name}" for st in m.structs) + "}")
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
                spec = f"host.{i.host.item}" + (f"({i.host.arg!r}" + (f", {i.host.arg2!r}" if i.host.arg2 is not None else "") + ")" if i.host.arg is not None else "")
            elif i.peer:
                spec = f"peer.{i.peer.peer}." + ".".join(i.peer.path)
            L.append(f"{INDENT*2}({i.name!r}, {i.ty.name!r}, {spec!r}),")
        L.append(f"{INDENT})")
        L.append(f"{INDENT}DAL_STATES = (")
        for s in m.states:
            L.append(f"{INDENT*2}({s.name!r}, {s.ty.name!r}),")
        L.append(f"{INDENT})")
        forwarded = m.linked.entries if m.linked is not None else []
        L.append(f"{INDENT}DAL_ENTRIES = ({''.join(repr(e.name) + ', ' for e in [*m.entries, *forwarded])})")
        if any(e.params for e in [*m.entries, *forwarded]):
            L.append(f"{INDENT}DAL_ENTRY_PARAMS = {{")
            for e in [*m.entries, *forwarded]:
                if e.params:
                    L.append(f"{INDENT*2}{e.name!r}: (" + "".join(f"({p.name!r}, {p.ty.name!r}, {p.has_default!r}), " for p in e.params) + "),")
            L.append(f"{INDENT}}}")
        if m.linked is not None:
            L.append(f"{INDENT}DAL_LINKED = {m.linked.parent!r}")
        coerced = [t for t in self.value_types() if isinstance(t, (StructType, TagType))]
        if coerced:
            L.append(f"{INDENT}DAL_TYPES = {{" + ", ".join(f"{t.name!r}: {py_coercer(t)}" for t in coerced) + "}")
        traffic = bool(m.handlers) or m.uses_commands
        lifecycle = m.lifecycle
        event_handlers = [(k, h) for k, h in enumerate(m.handlers) if h.event not in ("begin_play", "end_play")]
        phased = m.phases_explicit or len(m.updates) > 1
        queue_target = m.kind == "anim" and any(x.pub and not x.is_view for x in m.methods)
        if m.kind != "anim" or m.handlers or m.montages or m.actions:
            L.append(f"{INDENT}DAL_KIND = {m.kind!r}")
        if event_handlers:
            L.append(f"{INDENT}DAL_HANDLERS = (")
            for k, h in event_handlers:
                vt = h.params[0][1].name if h.event == "input" else (str(len(h.params)) if h.event in HOOK_EVENTS else "")
                if h.params and isinstance(h.params[0][1], StructType) and h.params[0][1].is_notify:
                    vt = h.params[0][1].name
                L.append(f"{INDENT*2}({h.event!r}, {h.name!r}, {h.phase!r}, '_h{k}', {vt!r}),")
            L.append(f"{INDENT})")
        if m.post_flush:
            L.append(f"{INDENT}DAL_POST_FLUSH = True")
        if m.peers:
            L.append(f"{INDENT}DAL_PEERS = (" + "".join(f"({p.name!r}, {p.logic!r}), " for p in m.peers) + ")")
        pub_methods = [x for x in m.methods if x.pub and not x.is_view]
        pub_views = [x for x in m.methods if x.pub and x.is_view]
        if pub_methods:
            L.append(f"{INDENT}DAL_METHODS = (" + "".join(f"{x.name!r}, " for x in pub_methods) + ")")
        if pub_views:
            L.append(f"{INDENT}DAL_VIEWS = (" + "".join(f"{x.name!r}, " for x in pub_views) + ")")
        if phased:
            names = [u.phase for u in m.updates] or [m.default_phase]
            L.append(f"{INDENT}DAL_PHASES = (" + "".join(f"{n!r}, " for n in names) + ")")
        L.append("")
        if m.peers:
            L.append(f"{INDENT}def __init__(self, host=None, peers=None, **params):")
            L.append(f"{INDENT*2}_rt.LogicBase.__init__(self, host, params, peers)")
        else:
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
        if any(h.event == "begin_play" for h in lifecycle):
            L.append(f"{INDENT*2}self._begun = False")
        if queue_target:
            L.append(f"{INDENT*2}self._peer_queue = []")
        for i in m.inputs:
            L.append(f"{INDENT*2}self.{i.name} = {py_value(i.ty, i.default)}")
        for s in m.states:
            L.append(f"{INDENT*2}self.{s.name} = {py_value(s.ty, s.default)}")
        L.append("")
        L.append(f"{INDENT}def _sample(self):")
        L.append(f'{INDENT*2}"""Read the host-bound and peer-bound inputs (a frame snapshot); overrides win over both."""')
        bound = [i for i in m.inputs if i.host]
        peered = [i for i in m.inputs if i.peer]
        if bound:
            L.append(f"{INDENT*2}h = self.host")
        for method in dict.fromkeys(HOST_ITEMS[i.host.item].consume for i in bound if HOST_ITEMS[i.host.item].consume):
            L.append(f"{INDENT*2}c_{method} = None")
        if bound or peered:
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
            spec = HOST_ITEMS[h.item]
            if spec.consume:
                # consume-on-read: the host hands the group out once per sample, and not at all when every member is overridden
                conv = _AS[i.ty]
                L.append(f"{INDENT*2}if {i.name!r} in ov:")
                L.append(f"{INDENT*3}self.{i.name} = {conv}(ov[{i.name!r}])")
                L.append(f"{INDENT*2}else:")
                L.append(f"{INDENT*3}if c_{spec.consume} is None:")
                L.append(f"{INDENT*4}c_{spec.consume} = h.{spec.consume}()")
                L.append(f"{INDENT*3}self.{i.name} = {conv}(c_{spec.consume}[{spec.part}])")
                continue
            if h.item == "action":
                src = f"h.action({h.arg!r}, {i.ty.name!r})"
            elif h.item == "notify":
                src = f"h.notify({h.arg!r})"
            elif spec.arg_kind == "string" or h.item.startswith("montage_"):
                src = f"h.{h.item}({h.arg!r}" + (f", {h.arg2!r}" if h.arg2 is not None else "") + ")"
            else:
                src = f"h.{h.item}"
            if isinstance(i.ty, EnumType):
                conv = f"{py_enum_class(i.ty)}(int({{}}))"
            else:
                conv = _AS[i.ty] + "({})"
            L.append(
                f"{INDENT*2}self.{i.name} = " + conv.format(f"ov[{i.name!r}] if {i.name!r} in ov else {src}")
            )
        for i in peered:
            pu = i.peer
            assert pu is not None
            path = "(" + "".join(f"{x!r}, " for x in pu.path[1:]) + ")"
            L.append(f"{INDENT*2}if {i.name!r} in ov:")
            L.append(f"{INDENT*3}self.{i.name} = {py_coercer(i.ty)}(ov[{i.name!r}])")
            L.append(f"{INDENT*2}else:")
            L.append(f"{INDENT*3}p = self.peers.get({pu.peer!r})")
            L.append(f"{INDENT*3}if p is None:")
            L.append(f"{INDENT*4}self.unresolved.add({pu.peer!r})")
            L.append(f"{INDENT*4}self.{i.name} = {py_value(i.ty, i.default)}")
            L.append(f"{INDENT*3}else:")
            L.append(f"{INDENT*4}self.{i.name} = {py_coercer(i.ty)}(_rt.path_get(p.dal_get({pu.path[0]!r}), {path}))")
        if not bound and not peered:
            L.append(f"{INDENT*2}pass")
        L.append("")

        # ---- the update: one block (as in every earlier version) or one block per tick slot
        def block_lines(u, first: bool, depth: int) -> list[str]:
            """sample, [peer queue, events, timers, handlers], body, flush -- the steps of one update block."""
            pad = INDENT * depth
            dt = u.dt_name if u is not None else "dt"
            out: list[str] = []
            if any(h.event == "begin_play" for h in lifecycle):
                out.append(f"{pad}if not self._begun:")
                out.append(f"{pad}{INDENT}self.begin_play()")
            out.append(f"{pad}self._sample()")
            if first and queue_target:
                out.append(f"{pad}self._drain_peer_calls()")
            if first and event_handlers:
                out.append(f"{pad}events = self._collect_events()")
            out.append(f"{pad}{dt} = float({dt})")
            out.append(f"{pad}self._dt = {dt}")
            if first and u is not None:
                out.extend(self.stmts(u.pre, depth))
            if first and event_handlers:
                out.append(f"{pad}self._dispatch(events)")
            if u is not None:
                out.extend(self.stmts(u.body, depth))
            if m.uses_commands and not m.post_flush:
                out.append(f"{pad}self._flush()")
            return out

        if not phased:
            u = m.update
            dt_name = u.dt_name if u is not None else "dt"
            L.append(f"{INDENT}def update(self, {dt_name}):")
            L.append(f'{INDENT*2}"""One frame: sample the inputs, collect events, tick timers, run handlers, run the body, flush commands."""')
            L.extend(block_lines(u, True, 2))
            L.append("")
        else:
            slots = m.updates or [None]
            slot_names = [u.phase if u is not None else m.default_phase for u in slots]
            L.append(f"{INDENT}def update(self, dt):")
            L.append(f'{INDENT*2}"""One frame of every tick slot, in order (a host that interleaves the slots with other work calls update_phase)."""')
            for name in slot_names:
                L.append(f"{INDENT*2}self.update_phase({name!r}, dt)")
            L.append("")
            L.append(f"{INDENT}def update_phase(self, phase, dt):")
            L.append(f'{INDENT*2}"""One tick slot: pre_movement, after_movement or after_anim (a slot the logic does not use does nothing)."""')
            for k, name in enumerate(slot_names):
                L.append(f"{INDENT*2}{'if' if k == 0 else 'elif'} phase == {name!r}:")
                L.append(f"{INDENT*3}self._u_{name}(dt)")
            L.append("")
            for k, (u, name) in enumerate(zip(slots, slot_names)):
                L.append(f"{INDENT}def _u_{name}(self, {u.dt_name if u is not None else 'dt'}):")
                L.append(f"{INDENT*2}# update in {name}")
                L.extend(block_lines(u, k == 0, 2))
                L.append("")
        # ---- lifecycle
        if any(h.event == "begin_play" for h in lifecycle):
            L.append(f"{INDENT}def begin_play(self):")
            L.append(f'{INDENT*2}"""Run the `on begin_play` handlers once; update() does it itself before the first frame when the host did not."""')
            L.append(f"{INDENT*2}if self._begun:")
            L.append(f"{INDENT*3}return")
            L.append(f"{INDENT*2}self._begun = True")
            L.append(f"{INDENT*2}self._sample()")
            for k, h in enumerate(m.handlers):
                if h.event == "begin_play":
                    L.append(f"{INDENT*2}self._h{k}()")
            if m.uses_commands:
                L.append(f"{INDENT*2}self._flush()")
            L.append("")
        if any(h.event == "end_play" for h in lifecycle):
            L.append(f"{INDENT}def end_play(self):")
            L.append(f'{INDENT*2}"""Run the `on end_play` handlers."""')
            L.append(f"{INDENT*2}self._sample()")
            for k, h in enumerate(m.handlers):
                if h.event == "end_play":
                    L.append(f"{INDENT*2}self._h{k}()")
            if m.uses_commands:
                L.append(f"{INDENT*2}self._flush()")
            L.append("")
        # ---- post_update
        if m.post_update is not None or m.post_flush:
            L.append(f"{INDENT}def post_update(self):")
            L.append(f'{INDENT*2}"""The post_update block (game thread, after the animation graph updated), then the queued commands go to the host."""')
            if m.post_update is not None and m.post_update.dt_name:
                L.append(f"{INDENT*2}{m.post_update.dt_name} = self._dt")
            if m.post_update is not None:
                L.extend(self.stmts(m.post_update.body, 2))
            if m.uses_commands:
                L.append(f"{INDENT*2}self._flush()")
            elif m.post_update is None:
                L.append(f"{INDENT*2}pass")
            L.append("")
        # ---- methods and views
        for x in m.methods:
            name = (x.name if x.pub else ("_v_" if x.is_view else "_m_") + x.name)
            L.append(f"{INDENT}def {name}({', '.join(['self'] + [n for n, _t in x.params])}):")
            L.append(f"{INDENT*2}# {'pub ' if x.pub else ''}{'view' if x.is_view else 'method'} {x.name}")
            L.extend(self.stmts(x.body, 2) or [f"{INDENT*2}pass"])
            L.append("")
        for e in m.entries:
            L.append(f"{INDENT}def {e.name}({self.entry_signature(e)}):")
            L.append(f'{INDENT*2}"""Entry: runs with the delta time of the last update."""' if e.dt_name else f'{INDENT*2}"""Entry."""')
            for p in e.params:
                conv = py_coercer(p.ty)
                if isinstance(p.ty, EnumType):
                    conv = f"_rt.as_enum({py_enum_class(p.ty)}, {p.name})"
                    L.append(f"{INDENT*2}{p.name} = {conv}")
                elif p.has_default and isinstance(p.ty, StructType):
                    L.append(f"{INDENT*2}{p.name} = {py_value(p.ty, p.default)} if {p.name} is None else {conv}({p.name})")
                else:
                    L.append(f"{INDENT*2}{p.name} = {conv}({p.name})")
            if e.dt_name:
                L.append(f"{INDENT*2}{e.dt_name} = self._dt")
            L.extend(self.stmts(e.body, 2) or [f"{INDENT*2}pass"])
            if m.uses_commands and not m.post_flush:
                L.append(f"{INDENT*2}self._flush()")
            L.append("")
        for e in forwarded:
            # the arguments go to the main instance as they come: it knows the parameter types and the defaults
            L.append(f"{INDENT}def {e.name}(self, *args, **named):")
            L.append(f'{INDENT*2}"""Forwarded to the main instance ({m.linked.parent}.{e.name})."""')
            L.append(f"{INDENT*2}parent = self.peers.get('Parent')")
            L.append(f"{INDENT*2}if parent is not None:")
            L.append(f"{INDENT*3}parent.call_entry({e.name!r}, *args, **named)")
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
