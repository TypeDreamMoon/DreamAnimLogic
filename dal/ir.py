"""Typed intermediate representation.

The checker lowers the syntax tree to this IR; both backends and the reference
interpreter consume only the IR.  Every ``Expr`` carries its resolved type
``ty``; implicit int -> float conversions are explicit ``ToFloat`` nodes, a conversion between
precisions (float -> double, vec3 -> vec3d, int -> double ... and the narrowing float(x), vec3(x) ...)
is a ``Convert`` node, and compound assignments are already expanded (``x += e`` is ``x = x + e``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import EnumType, Type

# Tick slots the host calls itself, at a point inside its own work, instead of the engine's tick running them in a fixed place: the movement
# component runs `physics_walking` at the start of its walking physics (UE: UCharacterMovementComponent::PhysWalking).  A hook slot is not part of
# `update(dt)` (which runs the scheduled slots), takes no part in the `before` / `after` order, and a host that does not run it never runs the block.
HOOK_SLOTS = ("physics_walking",)


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
class Convert(Expr):
    """A change of precision: ``ty`` is the type produced.  Widening (float -> double, vec3 -> vec3d ..., int -> double) is exact; narrowing
    (double -> float, vec3d -> vec3 ...) rounds every component to float32."""
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
class PeerValid(Expr):
    """valid(Peer): the peer logic is there (connected)."""
    peer: str


@dataclass
class CurveEval(Expr):
    """curve.eval(x) / curve3.eval(x): a float, or a vec3 for a vector curve; an unset curve gives zero."""
    curve: Expr
    x: Expr


@dataclass
class Index(Expr):
    """table[key] / items[i]: a missing key (or an index out of range) gives the default value of the element type."""
    base: Expr
    index: Expr


@dataclass
class MapHas(Expr):
    table: Expr
    key: Expr


@dataclass
class ArrayLen(Expr):
    items: Expr


@dataclass
class MakeStruct(Expr):
    """Name(member: value, ...): a struct value; members that are not listed keep their declared defaults."""
    fields: list[tuple[str, Expr]]


@dataclass
class CallMethod(Expr):
    """name(args): a call of a `method` (ty is VOID for a method without a result)."""
    name: str
    args: list[Expr]


@dataclass
class CallView(Expr):
    """name(args): a call of a `view`."""
    name: str
    args: list[Expr]


@dataclass
class CallPeer(Expr):
    """Peer.Method(args): a call of a `pub method` of another logic.  Between actor logics it runs at once; a call of an
    anim logic is queued and runs at the start of its next update (``queued``)."""
    peer: str
    method: str
    args: list[Expr]
    queued: bool = False


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
class While(Stmt):
    """while (cond) bound N { body }: the body runs while ``cond`` holds, at most ``bound`` times (a loop cannot run for ever)."""
    cond: Expr
    bound: int
    body: list[Stmt]


@dataclass
class Break(Stmt):
    """break; -- leaves the innermost loop."""


@dataclass
class Return(Stmt):
    value: Expr | None


@dataclass
class Command(Stmt):
    """host.montage_play(...): queued; the host runs it after the update.  ``args`` is the full canonical list."""
    name: str
    args: list[Expr]
    place: tuple | None = None       # host.snapshot_pose(place): (state name, member path) of the snapshot the command fills
    written: int | None = None       # a `sparse` command: how many arguments the program wrote (the host is sent those); None = all of them


@dataclass
class ExprStmt(Stmt):
    """A call used as a statement; a result is dropped."""
    expr: Expr


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
    arg2: str | None = None          # the second name of a read that takes two (ref_path_child_translation)


@dataclass
class PeerUse:
    """input X: T <- Peer.State.Member: where the value comes from."""
    peer: str
    path: tuple[str, ...]
    settings_asset: bool = False     # path[0] is a settings param of the peer that is a data asset: C++ reads it through Peer->DalSettings_<name>()


@dataclass
class InputDef:
    name: str
    ty: Type
    host: HostUse | None
    default: object
    peer: PeerUse | None = None

    @property
    def is_free(self) -> bool:
        """Not bound to the host or to a peer: set by game code."""
        return self.host is None and self.peer is None


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
    phase: str = "pre_movement"                         # tick slot: pre_movement | after_movement | after_anim | physics_walking (actor logic)


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
class EntryParam:
    name: str
    ty: Type
    default: object = None
    has_default: bool = False


@dataclass
class EntryDef:
    name: str
    dt_name: str | None
    body: list[Stmt]
    params: list[EntryParam] = field(default_factory=list)
    linked: bool = False                                 # the logic that is linked to this one forwards it


@dataclass
class LinkedDef:
    """`logic X : anim linked(Parent);` -- X forwards the `linked` entries of Parent."""
    parent: str
    entries: list[EntryDef]                              # signatures only (empty bodies)


@dataclass
class MethodDef:
    """A `method` (reads and writes state, issues commands) or, with ``is_view``, a read-only `view`."""
    name: str
    params: list[tuple[str, Type]]
    ret: Type | None
    body: list[Stmt]
    pub: bool = False
    is_view: bool = False


@dataclass
class PeerDef:
    """peer Character: AlsCharacter;  -- ``methods`` are the pub methods of the other logic (name -> parameters, result)."""
    name: str
    logic: str
    kind: str                                                    # 'anim' | 'actor'
    filename: str
    methods: dict[str, tuple[list[tuple[str, Type]], Type | None]] = field(default_factory=dict)
    slots: list[str] = field(default_factory=list)               # the tick slots an actor peer has an update block in


@dataclass
class OrderDef:
    """`logic X : actor before Peer;` / `after Peer`: in every tick slot both logics have, X's block runs before / after the peer's."""
    relation: str                                                # 'before' | 'after'
    peer: str                                                    # the peer's name in this logic
    logic: str                                                   # the peer's logic
    slots: tuple[str, ...] = ()                                  # the tick slots both have an update block in


@dataclass
class NotifyDef:
    """`notify Name { ... }` / `notify_state Name { ... }`: an animation notify class of the program.  ``struct`` is the type of the
    ``payload`` of its handlers (a StructType of kind 'notify', its members the properties of the notify)."""
    name: str
    state: bool
    struct: object                                  # StructType
    cpp_class: str                                  # the class name without the U prefix
    branching: bool = False
    origin: str | None = None                       # key of the library file that declares it (None = the file being compiled)


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
    updates: list[UpdateDef] = field(default_factory=list)      # one per tick slot, in slot order
    post_update: "UpdateDef | None" = None                      # anim logic: the block that runs after the graph update (game thread)
    post_flush: bool = False                                    # anim logic: its commands run right after the post_update of the same frame
    slot_events: bool = False                                   # actor logic: events are handled at the start of every tick slot
    sync_events: bool = False                                   # the hooks and declared notifies the host raises are handled when it raises them (receive_event)
    flush_peers: bool = False                                   # actor logic: the commands the called peers queue run with this logic's own at the end of its block
    order: list[OrderDef] = field(default_factory=list)         # actor logic: tick slot order against peers
    methods: list[MethodDef] = field(default_factory=list)      # methods and views, in source order
    peers: list[PeerDef] = field(default_factory=list)
    phases_explicit: bool = False                               # a tick slot was written (`in after_movement`)
    entries: list[EntryDef] = field(default_factory=list)
    linked: "LinkedDef | None" = None
    kind: str = "anim"                                          # 'anim' (UAnimInstance) | 'actor' (UActorComponent)
    montages: list[MontageDef] = field(default_factory=list)
    actions: list[ActionDef] = field(default_factory=list)
    handlers: list[HandlerDef] = field(default_factory=list)
    filename: str = "<string>"
    source_sha256: str = ""
    standin_source: str = ""                                    # Python stand-in file text (bundled by the Python backend)
    standin_filename: str = ""
    notifies: list[NotifyDef] = field(default_factory=list)     # the animation notify classes (library ones included), source order
    structs: list = field(default_factory=list)                 # StructType, dependencies first (library structs included)
    tagsets: list = field(default_factory=list)                 # TagType (library tag sets included)
    libs: list[LibRef] = field(default_factory=list)            # libraries used, dependencies first
    is_library: bool = False                                    # a file without `logic`: only shared declarations

    @property
    def update(self) -> UpdateDef | None:
        """The (first) update block: the only one for programs without tick slots."""
        return self.updates[0] if self.updates else None

    @property
    def default_phase(self) -> str:
        return self.updates[0].phase if self.updates else "pre_movement"

    @property
    def lifecycle(self) -> list[HandlerDef]:
        return [h for h in self.handlers if h.event in ("begin_play", "end_play")]

    def find_method(self, name: str) -> "MethodDef | None":
        return next((x for x in self.methods if x.name == name), None)

    def input_reads(self, bodies: list[list[Stmt]]) -> set[str]:
        """The inputs the statements read, the methods and views they call included."""
        reads: set[str] = set()
        called: set[str] = set()
        todo = list(bodies)
        while todo:
            for e in walk_stmt_exprs(todo.pop()):
                if isinstance(e, InputRef):
                    reads.add(e.name)
                elif isinstance(e, (CallMethod, CallView)) and e.name not in called:
                    called.add(e.name)
                    x = self.find_method(e.name)
                    if x is not None:
                        todo.append(x.body)
        return reads

    def consume_slots(self) -> tuple[str, ...] | None:
        """For an actor logic with several tick slots and consume-on-read host inputs (the pre-penetration velocity): the slots whose block reads
        them, when that is not every slot.  Sampling them at the start of every slot would hand the value to the first block, whether it reads it
        or not: they are sampled in these slots only.  None: nothing to restrict."""
        from .hostapi import HOST_ITEMS

        slots = [u.phase for u in self.updates] or [self.default_phase]
        consume = {i.name for i in self.inputs if i.host is not None and HOST_ITEMS[i.host.item].consume}
        if (len(slots) < 2 and not self.sync_events) or not consume:
            return None
        events = [h.body for h in self.handlers if h.event not in ("begin_play", "end_play")]
        reading: list[str] = []
        for k, u in enumerate(self.updates):
            bodies = [u.body, u.pre] + (events if (k == 0 or self.slot_events) and not self.sync_events else [])      # (sync_events: the handlers run when the event arrives, with a sample that consumes nothing)
            if self.input_reads(bodies) & consume:
                reading.append(u.phase)
        return tuple(reading) if (len(reading) < len(slots) or self.sync_events) else None

    def find_peer(self, name: str) -> "PeerDef | None":
        return next((p for p in self.peers if p.name == name), None)

    def find_notify(self, name: str) -> "NotifyDef | None":
        return next((n for n in self.notifies if n.name == name), None)

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
        for u in self.updates:
            yield ("update.pre", u.pre)
            yield ("update", u.body)
        if self.post_update is not None:
            yield ("post_update", self.post_update.body)
        for x in self.methods:
            yield (("view " if x.is_view else "method ") + x.name, x.body)
        for e in self.entries:
            yield ("entry " + e.name, e.body)
        for h in self.handlers:
            yield (f"on {h.event} {h.name}", h.body)

    @property
    def uses_commands(self) -> bool:
        return self.flush_peers or any(isinstance(n, Command) for _l, body in self.bodies() for n in walk_stmts(body))


# ---- traversal ---------------------------------------------------------------------------------

def children(e: Expr) -> list[Expr]:
    if isinstance(e, (Unary, ToFloat, Convert, Member, Valid)):
        return [e.x]
    if isinstance(e, CurveEval):
        return [e.curve, e.x]
    if isinstance(e, Index):
        return [e.base, e.index]
    if isinstance(e, MapHas):
        return [e.table, e.key]
    if isinstance(e, ArrayLen):
        return [e.items]
    if isinstance(e, Binary):
        return [e.a, e.b]
    if isinstance(e, Cond):
        return [e.c, e.a, e.b]
    if isinstance(e, (Make, CallFn, CallBuiltin, CallExtern, HostQuery, CallMethod, CallView, CallPeer)):
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
        elif isinstance(s, (For, While)):
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
        elif isinstance(s, ExprStmt):
            yield from walk_exprs(s.expr)
        elif isinstance(s, For):
            yield from walk_stmt_exprs(s.body)
        elif isinstance(s, While):
            yield from walk_exprs(s.cond)
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
    if isinstance(e, Convert):
        return f"{e.ty.name}({dump_expr(e.x)})"
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
    if isinstance(e, PeerValid):
        return f"valid(peer:{e.peer})"
    if isinstance(e, CurveEval):
        return f"{dump_expr(e.curve)}.eval({dump_expr(e.x)})"
    if isinstance(e, Index):
        return f"{dump_expr(e.base)}[{dump_expr(e.index)}]"
    if isinstance(e, MapHas):
        return f"{dump_expr(e.table)}.has({dump_expr(e.key)})"
    if isinstance(e, ArrayLen):
        return f"{dump_expr(e.items)}.len"
    if isinstance(e, CallMethod):
        return f"method:{e.name}({', '.join(dump_expr(a) for a in e.args)})"
    if isinstance(e, CallView):
        return f"view:{e.name}({', '.join(dump_expr(a) for a in e.args)})"
    if isinstance(e, CallPeer):
        return f"peer:{e.peer}.{e.method}({', '.join(dump_expr(a) for a in e.args)})" + (" queued" if e.queued else "")
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
            out.append(f"{pad}host.{s.name}({', '.join(dump_expr(a) for a in s.args)})" + (f"  -> {'.'.join((s.place[0], *s.place[1]))}" if s.place else ""))
        elif isinstance(s, ExprStmt):
            out.append(f"{pad}{dump_expr(s.expr)}")
        elif isinstance(s, For):
            out.append(f"{pad}for {s.var} in {s.lo}..{s.hi}")
            out.extend(dump_stmts(s.body, indent + 1))
        elif isinstance(s, While):
            out.append(f"{pad}while {dump_expr(s.cond)} bound {s.bound}")
            out.extend(dump_stmts(s.body, indent + 1))
        elif isinstance(s, Break):
            out.append(f"{pad}break")
    return out


def dump(m: Module) -> str:
    out = [f"library {m.name}" if m.is_library else f"logic {m.name}" + (f" : {m.kind}" if m.kind != "anim" else "") + (" post_flush" if m.post_flush else "")
           + (" slot_events" if m.slot_events else "") + (" sync_events" if m.sync_events else "") + (" flush_peers" if m.flush_peers else "") + "".join(f" {o.relation} {o.peer}" for o in m.order)]
    for lib in m.libs:
        out.append(f"use {lib.filename!r}")
    for en in m.enums:
        out.append(f"enum {en.name} {{{', '.join(en.members)}}}")
    for ts in m.tagsets:
        out.append(f"tags {ts.name} = {ts.prefix!r} {{{', '.join(ts.members)}}}")
    for st in m.structs:
        out.append(f"{st.kind} {st.name} {{" + "; ".join(("derived " if f.derived else "") + f"{f.name}: {f.ty}" for f in st.fields) + "}")
    for nd in m.notifies:
        out.append(("notify_state " if nd.state else "notify ") + nd.name + (" branching" if nd.branching else "") + f" cpp {nd.cpp_class!r} {{"
                   + "; ".join(f"{f.name}: {f.ty}" for f in nd.struct.fields) + "}")
    for mt in m.montages:
        out.append(f"montage {mt.name} {{{', '.join(mt.sections)}}}")
    for a in m.actions:
        out.append(f"action {a.name}: {a.ty}")
    for c in m.consts:
        out.append(f"const {c.name}: {c.ty} = {_fmt_value(c.value)}")
    for p in m.params:
        out.append(f"param {p.name}: {p.ty} = {_fmt_value(p.default)}")
    for p in m.peers:
        out.append(f"peer {p.name}: {p.logic}")
    for i in m.inputs:
        if i.peer:
            out.append(f"input {i.name}: {i.ty} <- {i.peer.peer}." + ".".join(i.peer.path))
        elif i.host:
            h = f"host.{i.host.item}" + (f"({i.host.arg!r}" + (f", {i.host.arg2!r}" if i.host.arg2 is not None else "") + ")" if i.host.arg is not None else "")
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
    for x in m.methods:
        ps = ", ".join(f"{n}: {t}" for n, t in x.params)
        out.append(("pub " if x.pub else "") + ("view " if x.is_view else "method ") + f"{x.name}({ps})" + (f" -> {x.ret}" if x.ret else ""))
        out.extend(dump_stmts(x.body))
    for u in m.updates:
        out.append(f"update({u.dt_name})" + (f" in {u.phase}" if m.phases_explicit else ""))
        if u.pre:
            out.append("  pre:")
            out.extend(dump_stmts(u.pre, 2))
        out.extend(dump_stmts(u.body))
    if m.post_update is not None:
        out.append(f"post_update({m.post_update.dt_name})")
        out.extend(dump_stmts(m.post_update.body))
    if m.linked is not None:
        out.append(f"linked({m.linked.parent}): " + ", ".join(x.name for x in m.linked.entries))
    for e in m.entries:
        head = ", ".join(([e.dt_name] if e.dt_name else []) + [f"{x.name}: {x.ty}" + (f" = {x.default!r}" if x.has_default else "") for x in e.params])
        out.append(f"entry {e.name}({head})" + (" linked" if e.linked else ""))
        out.extend(dump_stmts(e.body))
    for h in m.handlers:
        ps = ", ".join(f"{n}: {t}" for n, t in h.params)
        out.append(f"on {h.event} {h.name}" + (f" {h.phase}" if h.phase else "") + (f" ({ps})" if ps else ""))
        out.extend(dump_stmts(h.body))
    return "\n".join(out) + "\n"
