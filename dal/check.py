"""Name resolution and static type checking: syntax tree -> typed IR.

Errors are collected (with a poison ``ERROR`` type to avoid cascades) and raised
together as one ``DalError``; warnings are returned with the module.
"""

from __future__ import annotations

import ast as pyast
import difflib
import hashlib
from dataclasses import dataclass

from . import ir
from . import syntax as S
from .builtins import (
    BUILTIN_CONSTS,
    BUILTINS,
    CONSTRUCTORS,
    TYPE_NAMES,
    all_reserved_names,
    resolve_overload,
)
from .errors import DalError, Diagnostic, Pos, SourceFile
from .hostapi import HOST_ITEMS
from .interp import eval_const
from .lexer import RESERVED_WORDS
from .names import CPP_KEYWORDS, PY_RESERVED, reserved_reason
from .types import (
    BASIC_TYPES,
    BOOL,
    BUILTIN_ENUMS,
    ERROR,
    FLOAT,
    INT,
    QUAT,
    ROTATOR,
    VEC2,
    VEC3,
    EnumType,
    Type,
    zero_value,
)


@dataclass
class Sym:
    kind: str       # enum const param input state fn extern entry logic
    pos: Pos
    obj: object = None


@dataclass
class Local:
    ty: Type
    mutable: bool


class Scope:
    """Body scope: ``ctx`` is 'const' | 'fn' | 'update' | 'entry'."""

    def __init__(self, ctx: str, ret: Type | None = None, fn_name: str = ""):
        self.ctx = ctx
        self.ret = ret
        self.fn_name = fn_name
        self.frames: list[dict[str, Local]] = [{}]

    def push(self) -> None:
        self.frames.append({})

    def pop(self) -> None:
        self.frames.pop()

    def lookup(self, name: str) -> Local | None:
        for f in reversed(self.frames):
            if name in f:
                return f[name]
        return None

    def declare(self, name: str, local: Local) -> None:
        self.frames[-1][name] = local

    def visible_names(self) -> list[str]:
        out: list[str] = []
        for f in self.frames:
            out.extend(f)
        return out


_SWIZZLE = {"x": 0, "y": 1, "z": 2}


class Checker:
    def __init__(self, unit: S.SourceUnit, source: SourceFile, standin: tuple[str, str] | None = None):
        self.unit = unit
        self.source = source
        self.standin = standin
        self.diags: list[Diagnostic] = []
        self.globals: dict[str, Sym] = {}
        self.enums: dict[str, EnumType] = dict(BUILTIN_ENUMS)
        self.user_enums: list[EnumType] = []
        self.module = ir.Module(name="")
        self.fn_sigs: dict[str, tuple[list[tuple[str, Type]], Type]] = {}
        self.extern_sigs: dict[str, tuple[list[tuple[str, Type]], Type]] = {}
        self.fn_calls: dict[str, list[str]] = {}
        self.fn_pos: dict[str, Pos] = {}
        self.const_defs: dict[str, ir.ConstDef] = {}
        self.pending_consts: set[str] = set()
        self.reserved = all_reserved_names()

    # ---- diagnostics ---------------------------------------------------------------
    def err(self, code: str, message: str, pos: Pos) -> None:
        self.diags.append(self.source.diag(code, message, pos, "error"))

    def warn(self, code: str, message: str, pos: Pos) -> None:
        self.diags.append(self.source.diag(code, message, pos, "warning"))

    def bad(self, code: str, message: str, pos: Pos) -> ir.Expr:
        self.err(code, message, pos)
        return ir.Lit(ERROR, 0)

    def suggest(self, name: str, candidates) -> str:
        m = difflib.get_close_matches(name, list(candidates), n=1, cutoff=0.6)
        return f" (did you mean '{m[0]}'?)" if m else ""

    # ---- name declaration ----------------------------------------------------------------
    def valid_ident(self, name: str, pos: Pos, what: str, allow_py_keyword: bool = False) -> bool:
        if name in RESERVED_WORDS:
            self.err("DAL3003", f"'{name}' is reserved for a future version of the language", pos)
            return False
        if name in self.reserved:
            self.err("DAL3002", f"'{name}' is a built-in name and cannot be used as {what}", pos)
            return False
        reason = reserved_reason(name, allow_py_keyword=allow_py_keyword)
        if reason:
            self.err("DAL3004", f"'{name}' cannot be used as {what}: {reason}", pos)
            return False
        return True

    def declare_global(self, name: str, kind: str, pos: Pos, obj: object = None, what: str = "") -> bool:
        if not self.valid_ident(name, pos, what or f"a {kind} name"):
            return False
        prev = self.globals.get(name)
        if prev is not None:
            self.err("DAL3001", f"'{name}' is already declared as a {prev.kind} on line {prev.pos.line}", pos)
            return False
        self.globals[name] = Sym(kind, pos, obj)
        return True

    # ---- types ----------------------------------------------------------------------------------
    def resolve_type(self, name: str, pos: Pos) -> Type:
        if name in BASIC_TYPES:
            return BASIC_TYPES[name]
        if name in self.enums:
            return self.enums[name]
        cands = list(BASIC_TYPES) + list(self.enums)
        self.err("DAL3041", f"unknown type '{name}'" + self.suggest(name, cands), pos)
        return ERROR

    def assignable(self, target: Type, e: ir.Expr, pos: Pos, what: str) -> ir.Expr:
        """Return ``e`` converted to ``target`` or report DAL4002."""
        if target is ERROR or e.ty is ERROR:
            return e
        if e.ty is target:
            return e
        if target is FLOAT and e.ty is INT:
            return self.widen(e)
        hint = ""
        if target is INT and e.ty is FLOAT:
            hint = "; use to_int(), floor_to_int(), round_to_int() or ceil_to_int()"
        self.err("DAL4002", f"cannot use a value of type {e.ty} as {what} of type {target}{hint}", pos)
        return e

    # ---- run ----------------------------------------------------------------------------------------
    def run(self) -> ir.Module:
        m = self.module
        m.filename = self.source.filename
        m.source_sha256 = hashlib.sha256(self.source.text.encode("utf-8")).hexdigest()
        decls = self.unit.decls

        logics = [d for d in decls if isinstance(d, S.LogicDecl)]
        if not logics:
            self.err("DAL3010", "missing 'logic <Name>;' declaration (every .dal file declares exactly one logic)", Pos(1, 1, 2))
        else:
            if len(logics) > 1:
                self.err("DAL3011", "only one 'logic' declaration is allowed per file", logics[1].pos)
            self.declare_global(logics[0].name, "logic", logics[0].pos, what="a logic name")
            m.name = logics[0].name

        # 1. enums (types are needed by everything else)
        for d in decls:
            if isinstance(d, S.EnumDecl):
                self.declare_enum(d)

        # 2. consts, in source order
        for d in decls:
            if isinstance(d, S.ConstDecl):
                self.pending_consts.add(d.name)
        for d in decls:
            if isinstance(d, S.ConstDecl):
                self.declare_const(d)

        # 3. signatures of functions and externs, then the variable declarations
        for d in decls:
            if isinstance(d, S.FnDecl):
                self.declare_fn_sig(d)
            elif isinstance(d, S.ExternDecl):
                self.declare_extern(d)
        for d in decls:
            if isinstance(d, S.ParamDecl):
                self.declare_param(d)
            elif isinstance(d, S.InputDecl):
                self.declare_input(d)
            elif isinstance(d, S.StateDecl):
                self.declare_state(d)
        for d in decls:
            if isinstance(d, S.EntryDecl):
                self.declare_global(d.name, "entry", d.pos, what="an entry name")

        # 4. bodies
        for d in decls:
            if isinstance(d, S.FnDecl):
                self.check_fn(d)
        updates = [d for d in decls if isinstance(d, S.UpdateDecl)]
        if len(updates) > 1:
            self.err("DAL3012", "only one 'update' is allowed", updates[1].pos)
        for d in updates[:1]:
            m.update = self.check_update(d)
        for d in decls:
            if isinstance(d, S.EntryDecl):
                m.entries.append(self.check_entry(d))
        if m.update is None and not m.entries:
            self.warn("DAL5091", "the logic has no 'update' and no 'entry'; it never changes anything", Pos(1, 1, 2))

        self.order_functions()
        self.check_standins()

        errors = [d for d in self.diags if d.severity == "error"]
        if errors:
            raise DalError(self.diags)
        m.enums = self.user_enums
        return m

    # ---- declarations ---------------------------------------------------------------------------------------
    def declare_enum(self, d: S.EnumDecl) -> None:
        if not self.declare_global(d.name, "enum", d.pos, what="an enum name"):
            return
        seen: dict[str, Pos] = {}
        members: list[str] = []
        for mname, mpos in d.members:
            if mname in CPP_KEYWORDS:
                self.err("DAL3004", f"'{mname}' cannot be an enum member: it is a C++ keyword", mpos)
                continue
            if mname in seen:
                self.err("DAL3001", f"enum member '{mname}' is declared twice (first on line {seen[mname].line})", mpos)
                continue
            seen[mname] = mpos
            members.append(mname)
        et = EnumType(d.name, members)
        self.enums[d.name] = et
        self.user_enums.append(et)
        self.globals[d.name].obj = et

    def declare_const(self, d: S.ConstDecl) -> None:
        ok = self.declare_global(d.name, "const", d.pos, what="a constant name")
        ty = self.resolve_type(d.type_name, d.type_pos)
        sc = Scope("const")
        e = self.ex(d.init, sc)
        e = self.assignable(ty, e, d.init.pos, f"constant '{d.name}'")
        self.pending_consts.discard(d.name)
        if not ok or ty is ERROR or e.ty is ERROR:
            return
        value = self.fold(e, d.init.pos)
        if value is None:
            return
        cd = ir.ConstDef(d.name, ty, value, self.expr_text(d.init))
        self.const_defs[d.name] = cd
        self.module.consts.append(cd)
        self.globals[d.name].obj = cd

    def expr_text(self, e: S.Expr) -> str:
        line = self.source.line_text(e.pos.line)
        return line.strip()

    def fold(self, e: ir.Expr, pos: Pos):
        """Evaluate a constant expression, or report why it is not constant."""
        try:
            v = eval_const(e)
        except Exception as ex:  # noqa: BLE001
            self.err("DAL5002", f"expression is not a compile-time constant ({ex})", pos)
            return None
        flat = v if isinstance(v, tuple) else (v,)
        for c in flat:
            if isinstance(c, float) and (c != c or c in (float("inf"), float("-inf"))):
                self.err("DAL5003", "constant expression is not finite (inf or NaN)", pos)
                return None
        return v

    def const_value(self, init: S.Expr | None, ty: Type, pos: Pos, what: str):
        """Value of a ``= init`` clause (or the zero value)."""
        if init is None:
            return zero_value(ty) if ty is not ERROR else 0
        sc = Scope("const")
        e = self.ex(init, sc)
        e = self.assignable(ty, e, init.pos, what)
        if ty is ERROR or e.ty is ERROR:
            return 0
        v = self.fold(e, init.pos)
        return zero_value(ty) if v is None else v

    def declare_param(self, d: S.ParamDecl) -> None:
        ok = self.declare_global(d.name, "param", d.pos, what="a parameter name")
        ty = self.resolve_type(d.type_name, d.type_pos)
        value = self.const_value(d.init, ty, d.pos, f"param '{d.name}'")
        if ok and ty is not ERROR:
            self.module.params.append(ir.ParamDef(d.name, ty, value))

    def declare_state(self, d: S.StateDecl) -> None:
        ok = self.declare_global(d.name, "state", d.pos, what="a state name")
        ty = self.resolve_type(d.type_name, d.type_pos)
        value = self.const_value(d.init, ty, d.pos, f"state '{d.name}'")
        if ok and ty is not ERROR:
            self.module.states.append(ir.StateDef(d.name, ty, value))

    def declare_input(self, d: S.InputDecl) -> None:
        ok = self.declare_global(d.name, "input", d.pos, what="an input name")
        ty = self.resolve_type(d.type_name, d.type_pos)
        host = None
        value = None
        if d.binding is not None:
            host = self.check_binding(d, ty)
            value = zero_value(ty) if ty is not ERROR else 0
        else:
            value = self.const_value(d.default, ty, d.pos, f"input '{d.name}'")
        if ok and ty is not ERROR:
            self.module.inputs.append(ir.InputDef(d.name, ty, host, value))

    def check_binding(self, d: S.InputDecl, ty: Type) -> ir.HostUse | None:
        b = d.binding
        assert b is not None
        item = HOST_ITEMS.get(b.item)
        if item is None:
            self.err("DAL3020", f"unknown host item 'host.{b.item}'" + self.suggest(b.item, HOST_ITEMS), b.pos)
            return None
        if item.arg and b.arg is None:
            self.err("DAL3021", f"host.{item.name} needs a name argument, e.g. host.{item.name}(\"Name\")", b.pos)
            return None
        if not item.arg and b.arg is not None:
            self.err("DAL3021", f"host.{item.name} takes no argument", b.arg_pos or b.pos)
            return None
        if item.arg and (not b.arg or not b.arg.replace("_", "a").isalnum() or b.arg[0].isdigit()):
            self.err("DAL3022", f"host argument {b.arg!r} must be an identifier (letters, digits, '_')", b.arg_pos or b.pos)
            return None
        if ty is ERROR:
            return None
        if item.ty is not None:
            if ty is not item.ty:
                self.err("DAL4020", f"host.{item.name} has type {item.ty}, but input '{d.name}' is declared {ty}", d.type_pos)
                return None
        elif ty not in item.allowed:
            allowed = ", ".join(t.name for t in item.allowed)
            self.err("DAL4020", f"host.{item.name} can be read as {allowed}, not {ty}", d.type_pos)
            return None
        return ir.HostUse(b.item, b.arg)

    def declare_params(self, params: list[S.Param]) -> list[tuple[str, Type]] | None:
        out: list[tuple[str, Type]] = []
        seen: set[str] = set()
        ok = True
        for p in params:
            ty = self.resolve_type(p.type_name, p.type_pos)
            if not self.valid_ident(p.name, p.pos, "a parameter name"):
                ok = False
            elif p.name in seen:
                self.err("DAL3001", f"parameter '{p.name}' is declared twice", p.pos)
                ok = False
            elif p.name in self.globals:
                self.err("DAL3005", f"parameter '{p.name}' hides the {self.globals[p.name].kind} of the same name", p.pos)
                ok = False
            seen.add(p.name)
            out.append((p.name, ty))
        return out if ok else None

    def declare_fn_sig(self, d: S.FnDecl) -> None:
        self.declare_global(d.name, "fn", d.pos, what="a function name")
        params = self.declare_params(d.params)
        ret = self.resolve_type(d.ret_name, d.ret_pos)
        if params is not None:
            self.fn_sigs[d.name] = (params, ret)
        self.fn_pos[d.name] = d.pos

    def declare_extern(self, d: S.ExternDecl) -> None:
        self.declare_global(d.name, "extern", d.pos, what="a function name")
        params = self.declare_params(d.params)
        ret = self.resolve_type(d.ret_name, d.ret_pos)
        if params is None:
            return
        self.extern_sigs[d.name] = (params, ret)
        self.module.externs.append(ir.ExternDef(d.name, params, ret, d.cpp or d.name, d.include, d.py or d.name))

    # ---- bodies ---------------------------------------------------------------------------------------------------
    def check_fn(self, d: S.FnDecl) -> None:
        sig = self.fn_sigs.get(d.name)
        if sig is None:
            return
        params, ret = sig
        sc = Scope("fn", ret, d.name)
        for n, t in params:
            sc.declare(n, Local(t, False))
        self.fn_calls[d.name] = []
        body = self.block(d.body, sc)
        if ret is not ERROR and not self.always_returns(body):
            self.err("DAL5021", f"fn '{d.name}' may finish without returning a {ret}; every path must end in 'return'", d.pos)
        self.module.funcs.append(ir.FnDef(d.name, params, ret, body))

    def check_update(self, d: S.UpdateDecl) -> ir.UpdateDef:
        sc = Scope("update")
        if not self.valid_ident(d.dt_name, d.dt_pos, "the delta-time parameter"):
            pass
        elif d.dt_name in self.globals:
            self.err("DAL3005", f"'{d.dt_name}' hides the {self.globals[d.dt_name].kind} of the same name", d.dt_pos)
        sc.declare(d.dt_name, Local(FLOAT, False))
        return ir.UpdateDef(d.dt_name, self.block(d.body, sc))

    def check_entry(self, d: S.EntryDecl) -> ir.EntryDef:
        sc = Scope("entry")
        if not self.valid_ident(d.dt_name, d.dt_pos, "the delta-time parameter"):
            pass
        elif d.dt_name in self.globals:
            self.err("DAL3005", f"'{d.dt_name}' hides the {self.globals[d.dt_name].kind} of the same name", d.dt_pos)
        sc.declare(d.dt_name, Local(FLOAT, False))
        return ir.EntryDef(d.name, d.dt_name, self.block(d.body, sc))

    def always_returns(self, stmts: list[ir.Stmt]) -> bool:
        for s in stmts:
            if isinstance(s, ir.Return):
                return True
            if isinstance(s, ir.If) and s.orelse and self.always_returns(s.then) and self.always_returns(s.orelse):
                return True
        return False

    def block(self, stmts: list[S.Stmt], sc: Scope) -> list[ir.Stmt]:
        sc.push()
        out: list[ir.Stmt] = []
        terminated = False
        for s in stmts:
            if terminated:
                self.warn("DAL5090", "unreachable code after 'return'", s.pos)
                terminated = False
            r = self.stmt(s, sc)
            if r is not None:
                out.append(r)
                if isinstance(r, ir.Return):
                    terminated = True
        sc.pop()
        return out

    def stmt(self, s: S.Stmt, sc: Scope) -> ir.Stmt | None:
        if isinstance(s, S.LetStmt):
            return self.let_stmt(s, sc)
        if isinstance(s, S.AssignStmt):
            return self.assign_stmt(s, sc)
        if isinstance(s, S.IfStmt):
            c = self.ex(s.cond, sc)
            if c.ty is not BOOL and c.ty is not ERROR:
                self.err("DAL4003", f"the condition of 'if' must be bool, not {c.ty}", s.cond.pos)
            return ir.If(c, self.block(s.then, sc), self.block(s.orelse, sc))
        if isinstance(s, S.ReturnStmt):
            return self.return_stmt(s, sc)
        raise AssertionError(s)

    def let_stmt(self, s: S.LetStmt, sc: Scope) -> ir.Stmt | None:
        init = self.ex(s.init, sc)
        ty = init.ty
        if s.type_name is not None:
            ty = self.resolve_type(s.type_name, s.type_pos or s.pos)
            init = self.assignable(ty, init, s.init.pos, f"'{s.name}'")
        name_pos = s.name_pos or s.pos
        if not self.valid_ident(s.name, name_pos, "a variable name"):
            return None
        if s.name in self.globals:
            self.err("DAL3005", f"'{s.name}' hides the {self.globals[s.name].kind} of the same name; pick another name", name_pos)
            return None
        if sc.lookup(s.name) is not None:
            self.err("DAL3005", f"'{s.name}' is already declared in this scope; names cannot be shadowed", name_pos)
            return None
        sc.declare(s.name, Local(ty, s.mutable))
        return ir.Let(s.name, ty, init, s.mutable)

    def assign_stmt(self, s: S.AssignStmt, sc: Scope) -> ir.Stmt | None:
        name_pos = s.name_pos or s.pos
        loc = sc.lookup(s.name)
        kind = ""
        if loc is not None:
            if not loc.mutable:
                self.err("DAL5001", f"'{s.name}' is not assignable: declare it with 'var' to change it later", name_pos)
                self.ex(s.value, sc)
                return None
            kind, target_ty = "local", loc.ty
        else:
            sym = self.globals.get(s.name)
            if sym is None:
                self.err("DAL3040", f"undefined name '{s.name}'" + self.suggest(s.name, list(self.globals) + sc.visible_names()), name_pos)
                self.ex(s.value, sc)
                return None
            if sym.kind != "state":
                why = {
                    "input": "inputs are read-only inside the logic; copy it into a state or a local if you need to change it",
                    "param": "params are tunable constants for the logic",
                    "const": "constants cannot change",
                }.get(sym.kind, f"a {sym.kind} cannot be assigned")
                self.err("DAL5001", f"cannot assign to {sym.kind} '{s.name}': {why}", name_pos)
                self.ex(s.value, sc)
                return None
            if sc.ctx == "fn":
                self.err("DAL5010", "functions are pure and cannot write state; return a value and assign it in update/entry", name_pos)
                self.ex(s.value, sc)
                return None
            kind = "state"
            st = self.module.find_state(s.name)
            target_ty = st.ty if st else ERROR
        value = self.ex(s.value, sc)
        if s.op != "=":
            cur = ir.LocalRef(target_ty, s.name) if kind == "local" else ir.StateRef(target_ty, s.name)
            value = self.binary(s.op[0], cur, value, s.pos, name_pos)
        value = self.assignable(target_ty, value, s.value.pos, f"'{s.name}'")
        return ir.Assign(kind, s.name, value)

    def return_stmt(self, s: S.ReturnStmt, sc: Scope) -> ir.Stmt:
        if sc.ctx == "fn":
            if s.value is None:
                self.err("DAL5020", f"fn '{sc.fn_name}' must return a {sc.ret}", s.pos)
                return ir.Return(None)
            v = self.ex(s.value, sc)
            v = self.assignable(sc.ret or ERROR, v, s.value.pos, "the return value")
            return ir.Return(v)
        if s.value is not None:
            self.err("DAL5020", f"'{sc.ctx}' does not return a value", s.value.pos)
            self.ex(s.value, sc)
        return ir.Return(None)

    # ---- expressions ------------------------------------------------------------------------------------------------------
    def ex(self, e: S.Expr, sc: Scope) -> ir.Expr:
        if isinstance(e, S.IntLit):
            return ir.Lit(INT, e.value)
        if isinstance(e, S.FloatLit):
            return ir.Lit(FLOAT, e.value)
        if isinstance(e, S.BoolLit):
            return ir.Lit(BOOL, e.value)
        if isinstance(e, S.NameRef):
            return self.name_ref(e, sc)
        if isinstance(e, S.MemberRef):
            return self.member_ref(e, sc)
        if isinstance(e, S.CallExpr):
            return self.call(e, sc)
        if isinstance(e, S.UnaryExpr):
            return self.unary(e, sc)
        if isinstance(e, S.BinaryExpr):
            a = self.ex(e.left, sc)
            b = self.ex(e.right, sc)
            return self.binary(e.op, a, b, e.pos, e.op_pos or e.pos)
        if isinstance(e, S.CondExpr):
            return self.cond(e, sc)
        raise AssertionError(e)

    def name_ref(self, e: S.NameRef, sc: Scope) -> ir.Expr:
        name = e.name
        loc = sc.lookup(name)
        if loc is not None:
            return ir.LocalRef(loc.ty, name)
        if name in BUILTIN_CONSTS:
            c = BUILTIN_CONSTS[name]
            return ir.ConstRef(c.ty, name, c.value)
        sym = self.globals.get(name)
        if sym is None:
            if name in BUILTINS or name in CONSTRUCTORS:
                return self.bad("DAL3031", f"'{name}' is a function; call it as {name}(...)", e.pos)
            cands = list(self.globals) + sc.visible_names() + list(BUILTIN_CONSTS)
            return self.bad("DAL3040", f"undefined name '{name}'" + self.suggest(name, cands), e.pos)
        k = sym.kind
        if k == "const":
            if name in self.pending_consts:
                return self.bad("DAL3042", f"constant '{name}' is used before it is defined (constants are evaluated in order)", e.pos)
            cd = self.const_defs.get(name)
            if cd is None:
                return ir.Lit(ERROR, 0)
            return ir.ConstRef(cd.ty, name, cd.value)
        if k in ("param", "input", "state"):
            if sc.ctx == "const":
                return self.bad("DAL5002", f"a constant expression cannot read {k} '{name}'", e.pos)
            if sc.ctx == "fn":
                return self.bad(
                    "DAL5011",
                    f"fn bodies are pure and cannot read {k} '{name}'; pass it as an argument",
                    e.pos,
                )
            ty = self.var_type(k, name)
            return {"param": ir.ParamRef, "input": ir.InputRef, "state": ir.StateRef}[k](ty, name)
        if k == "enum":
            return self.bad("DAL3030", f"enum '{name}' is a type; write {name}.<Member> to name a value", e.pos)
        if k in ("fn", "extern", "entry"):
            return self.bad("DAL3031", f"'{name}' is a {'function' if k != 'entry' else 'entry'}; " + (f"call it as {name}(...)" if k != "entry" else "entries are called by the animation graph, not from code"), e.pos)
        return self.bad("DAL3040", f"'{name}' cannot be used as a value", e.pos)

    def var_type(self, kind: str, name: str) -> Type:
        coll = {"param": self.module.params, "input": self.module.inputs, "state": self.module.states}[kind]
        for d in coll:
            if d.name == name:
                return d.ty
        return ERROR

    def member_ref(self, e: S.MemberRef, sc: Scope) -> ir.Expr:
        base = e.base
        if isinstance(base, S.NameRef) and sc.lookup(base.name) is None:
            et = self.enums.get(base.name)
            if et is not None and base.name not in BUILTIN_CONSTS:
                if e.field in et.members:
                    return ir.EnumLit(et, e.field)
                return self.bad(
                    "DAL3032",
                    f"enum {et.name} has no member '{e.field}'" + self.suggest(e.field, et.members)
                    + f" (members: {', '.join(et.members)})",
                    e.field_pos,
                )
        b = self.ex(base, sc)
        if b.ty is ERROR:
            return b
        f = e.field
        if b.ty in (VEC2, VEC3):
            dim = 2 if b.ty is VEC2 else 3
            if not f or any(c not in "xyz" for c in f) or len(f) > 3:
                return self.bad("DAL4030", f"{b.ty} has no member '{f}' (components: x, y{', z' if dim == 3 else ''}; swizzles such as .xy are allowed)", e.field_pos)
            if any(_SWIZZLE[c] >= dim for c in f):
                return self.bad("DAL4030", f"{b.ty} has no component '{[c for c in f if _SWIZZLE[c] >= dim][0]}'", e.field_pos)
            ty = {1: FLOAT, 2: VEC2, 3: VEC3}[len(f)]
            return ir.Member(ty, b, f)
        if b.ty is ROTATOR:
            if f in ("pitch", "yaw", "roll"):
                return ir.Member(FLOAT, b, f)
            return self.bad("DAL4030", f"rotator has no member '{f}' (members: pitch, yaw, roll)", e.field_pos)
        if b.ty is QUAT:
            if f in ("x", "y", "z", "w"):
                return ir.Member(FLOAT, b, f)
            return self.bad("DAL4030", f"quat has no member '{f}' (members: x, y, z, w)", e.field_pos)
        return self.bad("DAL4030", f"type {b.ty} has no members", e.field_pos)

    def unary(self, e: S.UnaryExpr, sc: Scope) -> ir.Expr:
        x = self.ex(e.operand, sc)
        if x.ty is ERROR:
            return x
        if e.op == "!":
            if x.ty is not BOOL:
                return self.bad("DAL4001", f"operator '!' needs a bool, not {x.ty}", e.pos)
            return ir.Unary(BOOL, "!", x)
        if x.ty in (INT, FLOAT, VEC2, VEC3):
            return ir.Unary(x.ty, "-", x)
        return self.bad("DAL4001", f"operator '-' cannot be applied to {x.ty}", e.pos)

    def widen(self, x: ir.Expr) -> ir.Expr:
        if x.ty is not INT:
            return x
        if isinstance(x, ir.Lit):
            return ir.Lit(FLOAT, float(x.value))
        return ir.ToFloat(FLOAT, x)

    def binary(self, op: str, a: ir.Expr, b: ir.Expr, pos: Pos, op_pos: Pos) -> ir.Expr:
        ta, tb = a.ty, b.ty
        if ta is ERROR or tb is ERROR:
            return ir.Lit(ERROR, 0)
        num = ta.is_numeric and tb.is_numeric
        both_int = ta is INT and tb is INT

        def fail() -> ir.Expr:
            return self.bad("DAL4001", f"operator '{op}' cannot be applied to {ta} and {tb}", op_pos)

        if op in ("+", "-"):
            if num:
                return ir.Binary(INT, op, a, b) if both_int else ir.Binary(FLOAT, op, self.widen(a), self.widen(b))
            if ta is tb and ta.is_vector:
                return ir.Binary(ta, op, a, b)
            return fail()
        if op == "*":
            if num:
                return ir.Binary(INT, op, a, b) if both_int else ir.Binary(FLOAT, op, self.widen(a), self.widen(b))
            if ta is tb and ta.is_vector:
                return ir.Binary(ta, op, a, b)
            if ta.is_vector and tb.is_numeric:
                return ir.Binary(ta, op, a, self.widen(b))
            if ta.is_numeric and tb.is_vector:
                return ir.Binary(tb, op, self.widen(a), b)
            if ta is QUAT and tb is QUAT:
                return ir.Binary(QUAT, op, a, b)
            return fail()
        if op == "/":
            if num:
                return ir.Binary(INT, op, a, b) if both_int else ir.Binary(FLOAT, op, self.widen(a), self.widen(b))
            if ta.is_vector and tb.is_numeric:
                return ir.Binary(ta, op, a, self.widen(b))
            return fail()
        if op == "%":
            if both_int:
                return ir.Binary(INT, op, a, b)
            return self.bad("DAL4001", f"operator '%' needs two ints, not {ta} and {tb}; use fmod() for floats", op_pos)
        if op in ("<", "<=", ">", ">="):
            if num:
                return ir.Binary(BOOL, op, a, b) if both_int else ir.Binary(BOOL, op, self.widen(a), self.widen(b))
            return fail()
        if op in ("==", "!="):
            if num:
                return ir.Binary(BOOL, op, a, b) if both_int else ir.Binary(BOOL, op, self.widen(a), self.widen(b))
            if ta is BOOL and tb is BOOL:
                return ir.Binary(BOOL, op, a, b)
            if ta.is_enum and ta is tb:
                return ir.Binary(BOOL, op, a, b)
            if ta is tb and ta in (VEC2, VEC3, ROTATOR, QUAT):
                return self.bad("DAL4001", f"{ta} values cannot be compared with '{op}'; compare lengths or components, e.g. is_nearly_zero(length(a - b))", op_pos)
            return fail()
        if op in ("&&", "||"):
            if ta is BOOL and tb is BOOL:
                return ir.Binary(BOOL, op, a, b)
            return self.bad("DAL4001", f"operator '{op}' needs two bools, not {ta} and {tb}", op_pos)
        raise AssertionError(op)

    def cond(self, e: S.CondExpr, sc: Scope) -> ir.Expr:
        c = self.ex(e.cond, sc)
        a = self.ex(e.then, sc)
        b = self.ex(e.orelse, sc)
        if c.ty is not BOOL and c.ty is not ERROR:
            self.err("DAL4003", f"the condition of '?:' must be bool, not {c.ty}", e.cond.pos)
        if a.ty is ERROR or b.ty is ERROR:
            return ir.Lit(ERROR, 0)
        if a.ty is b.ty:
            return ir.Cond(a.ty, c, a, b)
        if a.ty.is_numeric and b.ty.is_numeric:
            return ir.Cond(FLOAT, c, self.widen(a), self.widen(b))
        return self.bad("DAL4004", f"the two branches of '?:' have different types ({a.ty} and {b.ty})", e.pos)

    # ---- calls -----------------------------------------------------------------------------------------------------------------------
    def call(self, e: S.CallExpr, sc: Scope) -> ir.Expr:
        name = e.callee
        args = [self.ex(a, sc) for a in e.args]
        if any(a.ty is ERROR for a in args):
            return ir.Lit(ERROR, 0)
        if sc.lookup(name) is not None:
            return self.bad("DAL3031", f"'{name}' is a variable, not a function", e.pos)
        if name in CONSTRUCTORS:
            sigs = CONSTRUCTORS[name]
            r = resolve_overload(sigs, [a.ty for a in args])
            if r is None:
                return self.bad("DAL4040", self.no_overload(name, sigs, args), e.pos)
            idx, widen = r
            args = [self.widen(a) if w else a for a, w in zip(args, widen)]
            return ir.Make(sigs[idx].ret, name, args, idx)
        if name in BUILTINS:
            b = BUILTINS[name]
            r = resolve_overload(b.sigs, [a.ty for a in args])
            if r is None:
                return self.bad("DAL4040", self.no_overload(name, b.sigs, args), e.pos)
            idx, widen = r
            args = [self.widen(a) if w else a for a, w in zip(args, widen)]
            return ir.CallBuiltin(b.sigs[idx].ret, name, idx, args)
        sym = self.globals.get(name)
        if sym is None:
            cands = list(BUILTINS) + list(CONSTRUCTORS) + [k for k, s in self.globals.items() if s.kind in ("fn", "extern")]
            return self.bad("DAL3040", f"undefined function '{name}'" + self.suggest(name, cands), e.pos)
        if sym.kind in ("fn", "extern"):
            if sc.ctx == "const":
                return self.bad("DAL5002", f"a constant expression cannot call '{name}'; only built-in functions are allowed", e.pos)
            sigs = self.fn_sigs if sym.kind == "fn" else self.extern_sigs
            sig = sigs.get(name)
            if sig is None:
                return ir.Lit(ERROR, 0)
            params, ret = sig
            if len(args) != len(params):
                return self.bad(
                    "DAL4041",
                    f"{name}() takes {len(params)} argument(s) ({', '.join(f'{n}: {t}' for n, t in params)}), got {len(args)}",
                    e.pos,
                )
            conv = []
            for (pn, pt), a, ae in zip(params, args, e.args):
                conv.append(self.assignable(pt, a, ae.pos, f"argument '{pn}' of {name}()"))
            if sym.kind == "fn":
                if sc.ctx == "fn":
                    self.fn_calls[sc.fn_name].append(name)
                return ir.CallFn(ret, name, conv)
            return ir.CallExtern(ret, name, conv)
        return self.bad("DAL3031", f"'{name}' is a {sym.kind}, not a function", e.pos)

    def no_overload(self, name: str, sigs, args: list[ir.Expr]) -> str:
        given = ", ".join(a.ty.name for a in args)
        lines = "; ".join(s.text(name) for s in sigs)
        return f"no version of {name}() accepts ({given}); available: {lines}"

    # ---- call graph ------------------------------------------------------------------------------------------------------------------
    def order_functions(self) -> None:
        funcs = {f.name: f for f in self.module.funcs}
        state: dict[str, int] = {}
        order: list[ir.FnDef] = []
        reported = False

        def visit(n: str, path: list[str]) -> None:
            nonlocal reported
            if n not in funcs:
                return
            st = state.get(n, 0)
            if st == 2:
                return
            if st == 1:
                if not reported:
                    cycle = path[path.index(n):] + [n]
                    self.err("DAL5030", "recursion is not allowed: " + " -> ".join(cycle), self.fn_pos.get(n, Pos(1, 1, 2)))
                    reported = True
                return
            state[n] = 1
            for c in self.fn_calls.get(n, []):
                visit(c, path + [n])
            state[n] = 2
            order.append(funcs[n])

        for f in list(self.module.funcs):
            visit(f.name, [])
        self.module.funcs = order

    # ---- extern stand-ins ---------------------------------------------------------------------------------------------------------------
    def check_standins(self) -> None:
        externs = self.module.externs
        if not externs:
            return
        decl_pos = {d.name: d.pos for d in self.unit.decls if isinstance(d, S.ExternDecl)}
        if self.standin is None:
            for x in externs:
                self.err(
                    "DAL6001",
                    f"extern fn '{x.name}' needs a Python stand-in: provide a file defining def {x.py}(...) "
                    f"(pass --standins FILE, or name it <source>_standins.py next to the .dal file)",
                    decl_pos.get(x.name, Pos(1, 1, 2)),
                )
            return
        fname, text = self.standin
        try:
            tree = pyast.parse(text, filename=fname)
        except SyntaxError as ex:
            self.err("DAL6005", f"stand-in file {fname} has a syntax error: {ex.msg} (line {ex.lineno})", decl_pos.get(externs[0].name, Pos(1, 1, 2)))
            return
        defs = {n.name: n for n in tree.body if isinstance(n, (pyast.FunctionDef,))}
        for x in externs:
            pos = decl_pos.get(x.name, Pos(1, 1, 2))
            fn = defs.get(x.py)
            if fn is None:
                self.err("DAL6002", f"stand-in file {fname} does not define def {x.py}(...) for extern fn '{x.name}'", pos)
                continue
            a = fn.args
            positional = [p.arg for p in (a.posonlyargs + a.args)]
            n_default = len(a.defaults)
            lo = len(positional) - n_default
            n = len(x.params)
            if a.vararg is None and not (lo <= n <= len(positional)):
                self.err(
                    "DAL6003",
                    f"stand-in {x.py}() takes {len(positional)} argument(s) but extern fn '{x.name}' declares {n}",
                    pos,
                )
                continue
            declared = [p for p, _ in x.params]
            if a.vararg is None and positional[:n] != declared:
                self.warn(
                    "DAL6004",
                    f"stand-in {x.py}({', '.join(positional)}) uses different parameter names than extern fn "
                    f"'{x.name}'({', '.join(declared)})",
                    pos,
                )
        self.module.standin_source = text
        self.module.standin_filename = fname


def check(unit: S.SourceUnit, source: SourceFile, standin: tuple[str, str] | None = None) -> tuple[ir.Module, list[Diagnostic]]:
    c = Checker(unit, source, standin)
    module = c.run()
    return module, [d for d in c.diags if d.severity == "warning"]
