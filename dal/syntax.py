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


# ---- declarations ----------------------------------------------------------

@dataclass
class Decl:
    pos: Pos


@dataclass
class LogicDecl(Decl):
    name: str


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


@dataclass
class InputDecl(Decl):
    name: str
    type_name: str
    type_pos: Pos
    binding: HostBinding | None
    default: Expr | None


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


@dataclass
class EntryDecl(Decl):
    name: str
    dt_name: str
    dt_pos: Pos
    body: list[Stmt]


@dataclass
class SourceUnit:
    decls: list[Decl] = field(default_factory=list)
