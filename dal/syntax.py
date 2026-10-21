"""Syntax tree produced by the parser (untyped; the checker lowers it to IR)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import Pos


# ---- expressions -----------------------------------------------------------

@dataclass
class Expr:
    pos: Pos


@dataclass
class IntLit(Expr):
    value: int


@dataclass
class FloatLit(Expr):
    value: float


@dataclass
class BoolLit(Expr):
    value: bool


@dataclass
class NameRef(Expr):
    name: str


@dataclass
class MemberRef(Expr):
    base: Expr
    field: str
    field_pos: Pos


@dataclass
class CallExpr(Expr):
    callee: str
    args: list[Expr]
    names: list[str | None] | None = None        # struct construction: Name(member: value, ...)
    name_pos: list[Pos | None] | None = None


@dataclass
class UnaryExpr(Expr):
    op: str  # '-' '!'
    operand: Expr


@dataclass
class BinaryExpr(Expr):
    op: str
    left: Expr
    right: Expr
    op_pos: Pos = None  # type: ignore[assignment]


@dataclass
class CondExpr(Expr):
    cond: Expr
    then: Expr
    orelse: Expr


@dataclass
class IndexExpr(Expr):
    """Settings.RotationModes[Mode]: an element of a map or an array."""
    base: Expr
    index: Expr


@dataclass
class MemberCallExpr(Expr):
    """Peer.Method(args), curve.eval(x), map.has(key): a call on a value that is not a plain name."""
    base: Expr
    name: str
    args: list[Expr]
    name_pos: Pos = None  # type: ignore[assignment]


@dataclass
class HostCallExpr(Expr):
    """host.trace_line(a, b) -- a synchronous host query (actor logic only)."""
    name: str
    args: list[Expr]


# ---- statements ------------------------------------------------------------

@dataclass
class Stmt:
    pos: Pos


@dataclass
class LetStmt(Stmt):
    name: str
    mutable: bool
    type_name: str | None
    init: Expr
    name_pos: Pos = None  # type: ignore[assignment]
    type_pos: Pos | None = None


@dataclass
class AssignStmt(Stmt):
    name: str
    op: str  # '=' '+=' '-=' '*=' '/='
    value: Expr
    name_pos: Pos = None  # type: ignore[assignment]


@dataclass
class IfStmt(Stmt):
    cond: Expr
    then: list[Stmt]
    orelse: list[Stmt]  # empty if there is no else; 'else if' nests an IfStmt


@dataclass
class ReturnStmt(Stmt):
    value: Expr | None


@dataclass
class CmdStmt(Stmt):
    """host.montage_play(Light1); -- a command for the host (queued, executed after the update)."""
    name: str
    args: list[Expr]
    name_pos: Pos = None  # type: ignore[assignment]


@dataclass
class MemberAssignStmt(Stmt):
    """GroundedState.VelocityBlend.ForwardAmount = f;  -- a member of a struct state or variable."""
    name: str
    path: list[str]
    op: str
    value: Expr
    name_pos: Pos = None  # type: ignore[assignment]
    path_pos: list[Pos] = field(default_factory=list)


@dataclass
class CallStmt(Stmt):
    """start(Cooldown); consume(Buffer); ... -- statement forms of timers, windows and buffers (removed by lowering)."""
    callee: str
    args: list[Expr]


@dataclass
class ExprStmt(Stmt):
    """Peer.SetDesiredGait(x);  -- a call on a member (a call on a plain name is a CallStmt)."""
    expr: Expr


@dataclass
class ForStmt(Stmt):
    """for i in 0..8 { ... } -- bounds are constant expressions."""
    var: str
    lo: Expr
    hi: Expr
    body: list[Stmt]
    var_pos: Pos = None  # type: ignore[assignment]


# ---- declarations ----------------------------------------------------------

@dataclass
class Decl:
    pos: Pos
    name_pos: Pos | None = field(default=None, kw_only=True)
    # set when libraries are merged (``use``): the file the declaration was written in (None = the main file)
    src: object = field(default=None, kw_only=True, repr=False, compare=False)
    origin: str | None = field(default=None, kw_only=True, repr=False, compare=False)    # library key, None = main file


@dataclass
class LogicDecl(Decl):
    name: str
    kind: str = "anim"               # 'anim' (UAnimInstance parent) | 'actor' (UActorComponent)
    kind_pos: Pos | None = None
    phase: str | None = None         # `logic X : actor in after_movement;` -- the tick slot of the plain `update`
    phase_pos: Pos | None = None
    linked: str | None = None        # `logic X : anim linked(Parent);` -- the main instance whose `linked` entries X forwards to
    linked_pos: Pos | None = None
    post_flush: bool = False         # `logic X : anim post_flush;` -- the commands of a frame run right after its post_update, not at the next update
    post_flush_pos: Pos | None = None


@dataclass
class EnumDecl(Decl):
    name: str
    members: list[tuple[str, Pos]]


@dataclass
class ConstDecl(Decl):
    name: str
    type_name: str
    type_pos: Pos
    init: Expr


@dataclass
class ParamDecl(Decl):
    name: str
    type_name: str
    type_pos: Pos
    init: Expr | None


@dataclass
class HostBinding:
    item: str
    arg: str | None
    pos: Pos
    arg_pos: Pos | None = None
    arg_is_ident: bool = False       # host.montage_playing(Light1) rather than host.action("Move")
    arg2: str | None = None          # the second argument of a read that takes two names: host.ref_path_child_translation("a", "b")
    arg2_pos: Pos | None = None


@dataclass
class PeerBinding:
    """input X: T <- Character.LocomotionState.Speed;  -- a value read from a peer logic at the start of every block."""
    peer: str
    path: list[str]
    pos: Pos
    path_pos: list[Pos]


@dataclass
class InputDecl(Decl):
    name: str
    type_name: str
    type_pos: Pos
    binding: HostBinding | None
    default: Expr | None
    peer: PeerBinding | None = None


@dataclass
class StateDecl(Decl):
    name: str
    type_name: str
    type_pos: Pos
    init: Expr | None


@dataclass
class Param:
    name: str
    type_name: str
    pos: Pos
    type_pos: Pos
    init: "Expr | None" = None        # the default of an entry parameter


@dataclass
class FnDecl(Decl):
    name: str
    params: list[Param]
    ret_name: str
    ret_pos: Pos
    body: list[Stmt]


@dataclass
class ExternDecl(Decl):
    name: str
    params: list[Param]
    ret_name: str
    ret_pos: Pos
    cpp: str | None = None
    include: str | None = None
    py: str | None = None


@dataclass
class UpdateDecl(Decl):
    dt_name: str
    dt_pos: Pos
    body: list[Stmt]
    pre: list[Stmt] = field(default_factory=list)      # generated by lowering: timer ticks, run before events
    phase: str | None = None                           # update(dt) in after_movement { ... }
    phase_pos: Pos | None = None


@dataclass
class PostUpdateDecl(Decl):
    """post_update { ... } / post_update(dt) { ... }: anim logic, game thread, after the graph updated (NativePostUpdateAnimation)."""
    dt_name: str | None
    dt_pos: Pos | None
    body: list[Stmt]


@dataclass
class EntryDecl(Decl):
    name: str
    dt_name: str | None
    dt_pos: Pos | None
    body: list[Stmt]
    params: list[Param] = field(default_factory=list)      # entry Name(dt, A: float = 1.0, ...)
    linked: bool = False                                   # entry Name(...) linked { ... }


# ---- v1 / v2 declarations -----------------------------------------------------------------------------------

@dataclass
class MontageDecl(Decl):
    name: str
    sections: list[tuple[str, Pos]] = field(default_factory=list)


@dataclass
class ActionDecl(Decl):
    name: str
    type_name: str
    type_pos: Pos


@dataclass
class HandlerDecl(Decl):
    """on notify Hit { ... } / on montage_ended Light1 { ... } / on input Attack started { ... }"""
    event: str                      # notify notify_begin notify_end notify_tick montage_ended montage_blending_out input + the hook events
    name: str
    target_pos: Pos | None
    phase: str | None               # input: started triggered ongoing completed canceled
    body: list[Stmt]
    param_names: list[tuple[str, Pos]] = field(default_factory=list)      # hook events: on movement_mode_changed(prev, cur) { ... }


@dataclass
class TimerDecl(Decl):
    name: str
    duration: Expr | None


@dataclass
class WindowDecl(Decl):
    name: str
    notify: str | None              # notify state that opens / closes the window
    notify_pos: Pos | None
    closes_on: list[tuple[str, Pos]] = field(default_factory=list)


@dataclass
class BufferTrigger:
    action: str
    phase: str
    pos: Pos


@dataclass
class BufferDecl(Decl):
    name: str
    duration: Expr
    triggers: list[BufferTrigger]
    when: Expr | None = None


@dataclass
class ComboPlay:
    montage: str
    montage_pos: Pos
    rate: Expr | None = None
    section: Expr | None = None


@dataclass
class ComboRule:
    """start on Buffer within Window when expr -> Step;   or   when expr -> Step;"""
    trigger: str | None             # a buffer name, or None for a condition-only rule
    trigger_pos: Pos | None
    within: str | None
    within_pos: Pos | None
    when: Expr | None
    target: str                     # a step name or 'Idle'
    target_pos: Pos
    pos: Pos


@dataclass
class ComboStep:
    name: str
    pos: Pos
    play: ComboPlay | None = None
    rules: list[ComboRule] = field(default_factory=list)
    after: list[tuple[Expr, str, Pos]] = field(default_factory=list)     # after 1.2 -> Step
    enter: list[Stmt] = field(default_factory=list)
    exit: list[Stmt] = field(default_factory=list)


@dataclass
class ComboDecl(Decl):
    name: str
    reset_when: Expr | None = None
    starts: list[ComboRule] = field(default_factory=list)
    steps: list[ComboStep] = field(default_factory=list)


# ---- v3 declarations ----------------------------------------------------------------------------------------

@dataclass
class FieldDecl:
    """One member of a struct (or settings) declaration."""
    name: str
    type_name: str
    pos: Pos
    type_pos: Pos
    init: Expr | None = None
    derived: bool = False


@dataclass
class StructDecl(Decl):
    """struct AlsFootState { bValid: bool; Left: AlsFootSide; ThighAxis: vec3 = vec3(0.0, 0.0, -1.0); }
    settings AlsGeneral { LeanHalfLife: float = 0.2; derived Cos: float = cos(deg_to_rad(Angle)); }  (kind = 'settings')"""
    name: str
    fields: list[FieldDecl]
    kind: str = "struct"


@dataclass
class TagsDecl(Decl):
    """tags AlsGait = "Als.Gait" { Walking, Running }  -- a set of gameplay tags with a common prefix."""
    name: str
    prefix: str
    prefix_pos: Pos
    members: list[tuple[str, Pos]]


@dataclass
class MethodDecl(Decl):
    """method name(params) [-> T] { ... }  /  view name(params) -> T { ... }  (optionally `pub`)."""
    name: str
    params: list[Param]
    ret_name: str | None
    ret_pos: Pos | None
    body: list[Stmt]
    pub: bool = False
    is_view: bool = False


@dataclass
class PeerDecl(Decl):
    """peer Character: AlsCharacter;  -- another logic on the same actor."""
    name: str
    type_name: str
    type_pos: Pos


@dataclass
class NotifyDecl(Decl):
    """notify Name { Member: type = default; ... }  /  notify_state Name branching cpp "ClassName" { ... }

    An animation notify class of the program: the generated Unreal class carries the members as properties, and the
    handlers `on notify Name { ... payload.Member ... }` receive them."""
    name: str
    state: bool                                   # `notify_state`: a notify with a duration (begin / tick / end)
    fields: list[FieldDecl]
    cpp: str | None = None                        # the C++ class name without the U prefix (default: DalNotify_<Name>)
    cpp_pos: Pos | None = None
    branching: bool = False                       # bIsNativeBranchingPoint: fired from the montage tick (and the event carries the montage instance)


@dataclass
class UseDecl(Decl):
    """use "als_lib.dal"; -- merge the declarations of a library file (no logic) into this one."""
    path: str
    path_pos: Pos


@dataclass
class SourceUnit:
    decls: list[Decl] = field(default_factory=list)
