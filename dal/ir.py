"""Typed intermediate representation.

The checker lowers the syntax tree to this IR; both backends and the reference
interpreter consume only the IR.  Every ``Expr`` carries its resolved type
``ty``; implicit int -> float conversions are explicit ``ToFloat`` nodes and
compound assignments are already expanded (``x += e`` is ``x = x + e``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import EnumType, Type


# ---- expressions -----------------------------------------------------------------

@dataclass
class Expr:
    ty: Type


@dataclass
class Lit(Expr):
    value: object          # float / int / bool / Vec2 / Vec3 / Rotator / Quat / enum index (int)


@dataclass
class EnumLit(Expr):
    member: str


@dataclass
class ConstRef(Expr):
    name: str
    value: object


@dataclass
class ParamRef(Expr):
    name: str


@dataclass
class InputRef(Expr):
    name: str


@dataclass
class StateRef(Expr):
    name: str


@dataclass
class LocalRef(Expr):
    name: str


@dataclass
class Unary(Expr):
    op: str                # '-' '!'
    x: Expr


@dataclass
class Binary(Expr):
    op: str                # + - * / % == != < <= > >= && ||
    a: Expr
    b: Expr


@dataclass
class Cond(Expr):
    c: Expr
    a: Expr
    b: Expr


@dataclass
class ToFloat(Expr):
    x: Expr


@dataclass
class Member(Expr):
    x: Expr
    comps: str             # 'x' / 'xy' / 'xyz' ... / 'pitch' / 'yaw' / 'roll'


@dataclass
class Make(Expr):
    ctor: str              # vec2 vec3 rotator quat
    args: list[Expr]
    sig_index: int = 0


@dataclass
class CallFn(Expr):
    name: str
    args: list[Expr]


@dataclass
class CallBuiltin(Expr):
    name: str
    sig_index: int
    args: list[Expr]


@dataclass
class CallExtern(Expr):
    name: str
    args: list[Expr]


@dataclass
class Valid(Expr):
    """valid(x): a tag is not empty / an asset reference is not null."""
    x: Expr


@dataclass
class MakeStruct(Expr):
    """Name(member: value, ...): a struct value; members that are not listed keep their declared defaults."""
    fields: list[tuple[str, Expr]]


@dataclass
class MontageRef(Expr):
    """A declared montage as a command argument (compile-time only)."""
    name: str


@dataclass
class HostQuery(Expr):
    """host.trace_line(a, b): a synchronous host query (actor logic only)."""
    name: str
    args: list[Expr]


# ---- statements ------------------------------------------------------------------------

@dataclass
class Stmt:
    pass


@dataclass
class Let(Stmt):
    name: str
    ty: Type
    init: Expr
    mutable: bool


@dataclass
class Assign(Stmt):
    kind: str              # 'state' | 'local'
    name: str
    value: Expr


@dataclass
class AssignField(Stmt):
    """S.a.b = v: assign a member of a struct state or local variable (``path`` are the member names)."""
    kind: str              # 'state' | 'local'
    name: str
    path: tuple[str, ...]
    value: Expr


@dataclass
class If(Stmt):
    cond: Expr
    then: list[Stmt]
    orelse: list[Stmt]


@dataclass
class Return(Stmt):
    value: Expr | None


@dataclass
class Command(Stmt):
    """host.montage_play(...): queued; the host runs it after the update.  ``args`` is the full canonical list."""
    name: str
    args: list[Expr]


@dataclass
class For(Stmt):
    """for var in lo..hi { body }  with constant bounds (hi exclusive)."""
    var: str
    lo: int
    hi: int
    body: list[Stmt]


# ---- declarations -------------------------------------------------------------------------

@dataclass
class ConstDef:
    name: str
    ty: Type
    value: object
    source: str = ""
    origin: str | None = None        # key of the library file that declares it (None = the file being compiled)


@dataclass
class ParamDef:
    name: str
    ty: Type
    default: object


@dataclass
class HostUse:
    item: str
    arg: str | None = None


@dataclass
class InputDef:
    name: str
    ty: Type
    host: HostUse | None
    default: object


@dataclass
class StateDef:
    name: str
    ty: Type
    default: object


@dataclass
class FnDef:
    name: str
    params: list[tuple[str, Type]]
    ret: Type
    body: list[Stmt]
    origin: str | None = None


@dataclass
class ExternDef:
    name: str
    params: list[tuple[str, Type]]
    ret: Type
    cpp: str
    include: str | None
    py: str
    origin: str | None = None


@dataclass
class UpdateDef:
    dt_name: str
    body: list[Stmt]
    pre: list[Stmt] = field(default_factory=list)      # timer ticks etc., run before events are handled


@dataclass
class MontageDef:
    name: str
    sections: list[str]                                  # declared section names (without the implicit None)
    enum: EnumType = None                                # type of Montage.section / section arguments  # type: ignore[assignment]


@dataclass
class ActionDef:
    name: str
    ty: Type                                             # bool / float / vec2 / vec3


@dataclass
class HandlerDef:
    event: str                                           # notify notify_begin notify_end montage_ended montage_blending_out input
    name: str
    phase: str | None
    params: list[tuple[str, Type]]                       # implicit locals: interrupted / value
    body: list[Stmt]


@dataclass
class EntryDef:
    name: str
    dt_name: str
    body: list[Stmt]


@dataclass
class LibRef:
    """A library file the module uses (``use "x.dal";``)."""
    key: str                  # normalised path, what ``origin`` fields hold
    filename: str             # relative to the project root, as in diagnostics
    stem: str                 # name of the generated C++ files (als_lib.dal -> AlsLib)
    sha256: str
    deps: list[str] = field(default_factory=list)     # keys of the libraries it uses directly


@dataclass
class Module:
    name: str
    enums: list[EnumType] = field(default_factory=list)       # user enums only
    consts: list[ConstDef] = field(default_factory=list)
    params: list[ParamDef] = field(default_factory=list)
    inputs: list[InputDef] = field(default_factory=list)
    states: list[StateDef] = field(default_factory=list)
    funcs: list[FnDef] = field(default_factory=list)          # topologically ordered: callees first
    externs: list[ExternDef] = field(default_factory=list)
    update: UpdateDef | None = None
    entries: list[EntryDef] = field(default_factory=list)
    kind: str = "anim"                                          # 'anim' (UAnimInstance) | 'actor' (UActorComponent)
    montages: list[MontageDef] = field(default_factory=list)
    actions: list[ActionDef] = field(default_factory=list)
    handlers: list[HandlerDef] = field(default_factory=list)
    filename: str = "<string>"
    source_sha256: str = ""
    standin_source: str = ""                                    # Python stand-in file text (bundled by the Python backend)
    standin_filename: str = ""
    structs: list = field(default_factory=list)                 # StructType, dependencies first (library structs included)
    tagsets: list = field(default_factory=list)                 # TagType (library tag sets included)
    libs: list[LibRef] = field(default_factory=list)            # libraries used, dependencies first
    is_library: bool = False                                    # a file without `logic`: only shared declarations

    # lookup helpers -----------------------------------------------------------
    def find_input(self, name: str) -> InputDef | None:
        return next((i for i in self.inputs if i.name == name), None)

    def find_state(self, name: str) -> StateDef | None:
        return next((s for s in self.states if s.name == name), None)

    def find_const(self, name: str) -> ConstDef | None:
        return next((c for c in self.consts if c.name == name), None)

    def find_fn(self, name: str) -> FnDef | None:
        return next((f for f in self.funcs if f.name == name), None)

    def find_extern(self, name: str) -> ExternDef | None:
        return next((e for e in self.externs if e.name == name), None)

    def find_montage(self, name: str) -> "MontageDef | None":
        return next((m for m in self.montages if m.name == name), None)

    def find_action(self, name: str) -> "ActionDef | None":
        return next((a for a in self.actions if a.name == name), None)

    def bodies(self):
        """All statement lists with an owner label."""
        for f in self.funcs:
            yield ("fn " + f.name, f.body)
        if self.update:
            yield ("update.pre", self.update.pre)
            yield ("update", self.update.body)
        for e in self.entries:
            yield ("entry " + e.name, e.body)
        for h in self.handlers:
            yield (f"on {h.event} {h.name}", h.body)

    @property
    def uses_commands(self) -> bool:
        return any(isinstance(n, Command) for _l, body in self.bodies() for n in walk_stmts(body))


# ---- traversal ---------------------------------------------------------------------------------

def children(e: Expr) -> list[Expr]:
    if isinstance(e, (Unary, ToFloat, Member, Valid)):
        return [e.x]
    if isinstance(e, Binary):
        return [e.a, e.b]
    if isinstance(e, Cond):
        return [e.c, e.a, e.b]
    if isinstance(e, (Make, CallFn, CallBuiltin, CallExtern, HostQuery)):
        return list(e.args)
    if isinstance(e, MakeStruct):
        return [v for _n, v in e.fields]
    return []


def walk_exprs(e: Expr):
    yield e
    for c in children(e):
        yield from walk_exprs(c)


def walk_stmts(stmts: list[Stmt]):
    """Yield every statement, nested ones included."""
    for s in stmts:
        yield s
        if isinstance(s, If):
            yield from walk_stmts(s.then)
            yield from walk_stmts(s.orelse)
        elif isinstance(s, For):
            yield from walk_stmts(s.body)


def walk_stmt_exprs(stmts: list[Stmt]):
    """Yield every expression (and sub-expression) in a statement list."""
    for s in stmts:
        if isinstance(s, Let):
            yield from walk_exprs(s.init)
        elif isinstance(s, (Assign, AssignField)):
            yield from walk_exprs(s.value)
        elif isinstance(s, If):
            yield from walk_exprs(s.cond)
            yield from walk_stmt_exprs(s.then)
            yield from walk_stmt_exprs(s.orelse)
        elif isinstance(s, Return) and s.value is not None:
            yield from walk_exprs(s.value)
        elif isinstance(s, Command):
            for a in s.args:
                yield from walk_exprs(a)
        elif isinstance(s, For):
            yield from walk_stmt_exprs(s.body)


# ---- text dump (debugging, tests) ----------------------------------------------------------------

def _fmt_value(v) -> str:
    return repr(v)


def dump_expr(e: Expr) -> str:
    if isinstance(e, Lit):
        return _fmt_value(e.value)
    if isinstance(e, EnumLit):
        return f"{e.ty.name}.{e.member}"
    if isinstance(e, ConstRef):
        return f"const:{e.name}"
    if isinstance(e, ParamRef):
        return f"param:{e.name}"
    if isinstance(e, InputRef):
        return f"input:{e.name}"
    if isinstance(e, StateRef):
        return f"state:{e.name}"
    if isinstance(e, LocalRef):
        return f"local:{e.name}"
    if isinstance(e, Unary):
        return f"({e.op}{dump_expr(e.x)})"
    if isinstance(e, Binary):
        return f"({dump_expr(e.a)} {e.op} {dump_expr(e.b)})"
    if isinstance(e, Cond):
        return f"({dump_expr(e.c)} ? {dump_expr(e.a)} : {dump_expr(e.b)})"
    if isinstance(e, ToFloat):
        return f"float({dump_expr(e.x)})"
    if isinstance(e, Member):
        return f"{dump_expr(e.x)}.{e.comps}"
    if isinstance(e, Make):
        return f"{e.ctor}[{e.sig_index}]({', '.join(dump_expr(a) for a in e.args)})"
    if isinstance(e, CallFn):
        return f"fn:{e.name}({', '.join(dump_expr(a) for a in e.args)})"
    if isinstance(e, CallBuiltin):
        return f"{e.name}[{e.sig_index}]({', '.join(dump_expr(a) for a in e.args)})"
    if isinstance(e, CallExtern):
        return f"extern:{e.name}({', '.join(dump_expr(a) for a in e.args)})"
    if isinstance(e, Valid):
        return f"valid({dump_expr(e.x)})"
    if isinstance(e, MakeStruct):
        return f"{e.ty.name}({', '.join(f'{n}: {dump_expr(v)}' for n, v in e.fields)})"
    if isinstance(e, MontageRef):
        return f"montage:{e.name}"
    if isinstance(e, HostQuery):
        return f"host.{e.name}({', '.join(dump_expr(a) for a in e.args)})"
    return f"<{type(e).__name__}>"


def dump_stmts(stmts: list[Stmt], indent: int = 1) -> list[str]:
    pad = "  " * indent
    out: list[str] = []
    for s in stmts:
        if isinstance(s, Let):
            kw = "var" if s.mutable else "let"
            out.append(f"{pad}{kw} {s.name}: {s.ty} = {dump_expr(s.init)}")
        elif isinstance(s, Assign):
            out.append(f"{pad}{s.kind}:{s.name} = {dump_expr(s.value)}")
        elif isinstance(s, AssignField):
            out.append(f"{pad}{s.kind}:{s.name}.{'.'.join(s.path)} = {dump_expr(s.value)}")
        elif isinstance(s, If):
            out.append(f"{pad}if {dump_expr(s.cond)}")
            out.extend(dump_stmts(s.then, indent + 1))
            if s.orelse:
                out.append(f"{pad}else")
                out.extend(dump_stmts(s.orelse, indent + 1))
        elif isinstance(s, Return):
            out.append(f"{pad}return" + (f" {dump_expr(s.value)}" if s.value is not None else ""))
        elif isinstance(s, Command):
            out.append(f"{pad}host.{s.name}({', '.join(dump_expr(a) for a in s.args)})")
        elif isinstance(s, For):
            out.append(f"{pad}for {s.var} in {s.lo}..{s.hi}")
            out.extend(dump_stmts(s.body, indent + 1))
    return out


def dump(m: Module) -> str:
    out = [f"library {m.name}" if m.is_library else f"logic {m.name}" + (f" : {m.kind}" if m.kind != "anim" else "")]
    for lib in m.libs:
        out.append(f"use {lib.filename!r}")
    for en in m.enums:
        out.append(f"enum {en.name} {{{', '.join(en.members)}}}")
    for ts in m.tagsets:
        out.append(f"tags {ts.name} = {ts.prefix!r} {{{', '.join(ts.members)}}}")
    for st in m.structs:
        out.append(f"{st.kind} {st.name} {{" + "; ".join(f"{f.name}: {f.ty}" for f in st.fields) + "}")
    for mt in m.montages:
        out.append(f"montage {mt.name} {{{', '.join(mt.sections)}}}")
    for a in m.actions:
        out.append(f"action {a.name}: {a.ty}")
    for c in m.consts:
        out.append(f"const {c.name}: {c.ty} = {_fmt_value(c.value)}")
    for p in m.params:
        out.append(f"param {p.name}: {p.ty} = {_fmt_value(p.default)}")
    for i in m.inputs:
        if i.host:
            h = f"host.{i.host.item}" + (f"({i.host.arg!r})" if i.host.arg is not None else "")
            out.append(f"input {i.name}: {i.ty} <- {h}")
        else:
            out.append(f"input {i.name}: {i.ty} = {_fmt_value(i.default)}")
    for s in m.states:
        out.append(f"state {s.name}: {s.ty} = {_fmt_value(s.default)}")
    for x in m.externs:
        ps = ", ".join(f"{n}: {t}" for n, t in x.params)
        out.append(f"extern fn {x.name}({ps}) -> {x.ret}  cpp={x.cpp!r} py={x.py!r}")
    for f in m.funcs:
        ps = ", ".join(f"{n}: {t}" for n, t in f.params)
        out.append(f"fn {f.name}({ps}) -> {f.ret}")
        out.extend(dump_stmts(f.body))
    if m.update:
        out.append(f"update({m.update.dt_name})")
        if m.update.pre:
            out.append("  pre:")
            out.extend(dump_stmts(m.update.pre, 2))
        out.extend(dump_stmts(m.update.body))
    for e in m.entries:
        out.append(f"entry {e.name}({e.dt_name})")
        out.extend(dump_stmts(e.body))
    for h in m.handlers:
        ps = ", ".join(f"{n}: {t}" for n, t in h.params)
        out.append(f"on {h.event} {h.name}" + (f" {h.phase}" if h.phase else "") + (f" ({ps})" if ps else ""))
        out.extend(dump_stmts(h.body))
    return "\n".join(out) + "\n"
