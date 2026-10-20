"""Recursive-descent parser for ``.dal``.

The parser reports the first syntax error it meets (``DalError`` with one
diagnostic, code ``DAL2xxx``).  Name resolution and typing happen later in
``check.py``.
"""

from __future__ import annotations

from . import syntax as S
from .errors import DalError, Pos, SourceFile
from .lexer import RESERVED_WORDS, Token, tokenize

ASSIGN_OPS = ("=", "+=", "-=", "*=", "/=")


class Parser:
    def __init__(self, source: SourceFile):
        self.source = source
        self.tokens = tokenize(source)
        self.i = 0

    # ---- token helpers -----------------------------------------------------

    @property
    def tok(self) -> Token:
        return self.tokens[self.i]

    def peek(self, n: int = 1) -> Token:
        j = min(self.i + n, len(self.tokens) - 1)
        return self.tokens[j]

    def advance(self) -> Token:
        t = self.tokens[self.i]
        if t.kind != "EOF":
            self.i += 1
        return t

    def error(self, code: str, message: str, pos: Pos | None = None) -> DalError:
        return DalError([self.source.diag(code, message, pos or self.tok.pos)])

    def describe(self, t: Token) -> str:
        if t.kind == "EOF":
            return "end of file"
        if t.kind == "STRING":
            return "a string"
        return f"'{t.text}'"

    def prev_end_pos(self) -> Pos:
        """Position just after the previous token (for 'missing ;' style errors)."""
        if self.i == 0:
            return self.tok.pos
        p = self.tokens[self.i - 1].pos
        return Pos(p.line, p.end_col, p.end_col + 1)

    def expect_op(self, text: str, context: str = "") -> Token:
        t = self.tok
        if t.is_op(text):
            return self.advance()
        where = f" {context}" if context else ""
        pos = t.pos
        if text in (";", ")", "}", "]") and self.i > 0 and self.tokens[self.i - 1].pos.line < t.pos.line:
            pos = self.prev_end_pos()
        raise self.error("DAL2001", f"expected '{text}'{where}, found {self.describe(t)}", pos)

    def expect_kw(self, text: str, context: str = "") -> Token:
        t = self.tok
        if t.is_kw(text):
            return self.advance()
        where = f" {context}" if context else ""
        raise self.error("DAL2001", f"expected '{text}'{where}, found {self.describe(t)}")

    def ident(self, what: str) -> Token:
        t = self.tok
        if t.kind == "IDENT":
            if t.text in RESERVED_WORDS:
                raise self.error(
                    "DAL2002",
                    f"'{t.text}' is reserved for a future version of the language and cannot be used as {what}",
                )
            return self.advance()
        if t.kind == "KEYWORD":
            raise self.error("DAL2003", f"'{t.text}' is a keyword and cannot be used as {what}")
        raise self.error("DAL2001", f"expected {what}, found {self.describe(t)}")

    # ---- top level ---------------------------------------------------------

    def parse(self) -> S.SourceUnit:
        unit = S.SourceUnit()
        while self.tok.kind != "EOF":
            unit.decls.append(self.parse_decl())
        return unit

    def parse_decl(self) -> S.Decl:
        t = self.tok
        if t.kind == "KEYWORD":
            handler = {
                "logic": self.parse_logic,
                "enum": self.parse_enum,
                "const": self.parse_const,
                "param": self.parse_param,
                "input": self.parse_input,
                "state": self.parse_state,
                "fn": self.parse_fn,
                "extern": self.parse_extern,
                "update": self.parse_update,
                "entry": self.parse_entry,
            }.get(t.text)
            if handler is not None:
                return handler()
        raise self.error(
            "DAL2001",
            "expected a declaration (logic, enum, const, param, input, state, fn, extern, update, entry), "
            f"found {self.describe(t)}",
        )

    def parse_type_name(self) -> tuple[str, Pos]:
        t = self.tok
        if t.kind != "IDENT":
            raise self.error("DAL2001", f"expected a type name, found {self.describe(t)}")
        self.advance()
        return t.text, t.pos

    def parse_logic(self) -> S.LogicDecl:
        kw = self.advance()
        name = self.ident("a logic name")
        self.expect_op(";", "after the logic name")
        return S.LogicDecl(kw.pos, name.text)

    def parse_enum(self) -> S.EnumDecl:
        kw = self.advance()
        name = self.ident("an enum name")
        self.expect_op("{", "after the enum name")
        members: list[tuple[str, Pos]] = []
        while True:
            m = self.ident("an enum member name")
            members.append((m.text, m.pos))
            if self.tok.is_op(","):
                self.advance()
                if self.tok.is_op("}"):
                    break
                continue
            break
        self.expect_op("}", "to close the enum")
        return S.EnumDecl(kw.pos, name.text, members)

    def parse_const(self) -> S.ConstDecl:
        kw = self.advance()
        name = self.ident("a constant name")
        self.expect_op(":", "after the constant name")
        type_name, type_pos = self.parse_type_name()
        self.expect_op("=", "(a const needs a value)")
        init = self.parse_expr()
        self.expect_op(";", "after the constant")
        return S.ConstDecl(kw.pos, name.text, type_name, type_pos, init)

    def parse_param(self) -> S.ParamDecl:
        kw = self.advance()
        name = self.ident("a parameter name")
        self.expect_op(":", "after the parameter name")
        type_name, type_pos = self.parse_type_name()
        init = None
        if self.tok.is_op("="):
            self.advance()
            init = self.parse_expr()
        self.expect_op(";", "after the parameter")
        return S.ParamDecl(kw.pos, name.text, type_name, type_pos, init)

    def parse_input(self) -> S.InputDecl:
        kw = self.advance()
        name = self.ident("an input name")
        self.expect_op(":", "after the input name")
        type_name, type_pos = self.parse_type_name()
        binding = None
        default = None
        if self.tok.is_op("<") and self.peek().is_op("-") and self.peek().pos.col == self.tok.pos.col + 1 \
                and self.peek().pos.line == self.tok.pos.line:
            self.advance()
            self.advance()
            binding = self.parse_host_binding()
        elif self.tok.is_op("="):
            self.advance()
            default = self.parse_expr()
        self.expect_op(";", "after the input")
        return S.InputDecl(kw.pos, name.text, type_name, type_pos, binding, default)

    def parse_host_binding(self) -> S.HostBinding:
        h = self.tok
        if not h.is_kw("host"):
            raise self.error("DAL2001", f"expected 'host.<item>' after '<-', found {self.describe(h)}")
        self.advance()
        self.expect_op(".", "after 'host'")
        item = self.tok
        if item.kind not in ("IDENT", "KEYWORD"):
            raise self.error("DAL2001", f"expected a host item name after 'host.', found {self.describe(item)}")
        self.advance()
        arg = None
        arg_pos = None
        if self.tok.is_op("("):
            self.advance()
            s = self.tok
            if s.kind != "STRING":
                raise self.error("DAL2001", f"expected a string argument, found {self.describe(s)}")
            self.advance()
            arg, arg_pos = s.value, s.pos
            self.expect_op(")", "after the host item argument")
        return S.HostBinding(item.text, arg, Pos(h.pos.line, h.pos.col, item.pos.end_col), arg_pos)

    def parse_state(self) -> S.StateDecl:
        kw = self.advance()
        name = self.ident("a state name")
        self.expect_op(":", "after the state name")
        type_name, type_pos = self.parse_type_name()
        init = None
        if self.tok.is_op("="):
            self.advance()
            init = self.parse_expr()
        self.expect_op(";", "after the state")
        return S.StateDecl(kw.pos, name.text, type_name, type_pos, init)

    def parse_params(self) -> list[S.Param]:
        self.expect_op("(", "to open the parameter list")
        params: list[S.Param] = []
        if not self.tok.is_op(")"):
            while True:
                name = self.ident("a parameter name")
                self.expect_op(":", "after the parameter name")
                type_name, type_pos = self.parse_type_name()
                params.append(S.Param(name.text, type_name, name.pos, type_pos))
                if self.tok.is_op(","):
                    self.advance()
                    continue
                break
        self.expect_op(")", "to close the parameter list")
        return params

    def parse_fn(self) -> S.FnDecl:
        kw = self.advance()
        name = self.ident("a function name")
        params = self.parse_params()
        self.expect_op("->", "(functions declare a return type: fn name(...) -> float)")
        ret_name, ret_pos = self.parse_type_name()
        body = self.parse_block()
        return S.FnDecl(kw.pos, name.text, params, ret_name, ret_pos, body)

    def parse_extern(self) -> S.ExternDecl:
        kw = self.advance()
        self.expect_kw("fn", "after 'extern'")
        name = self.ident("a function name")
        params = self.parse_params()
        self.expect_op("->", "(extern functions declare a return type)")
        ret_name, ret_pos = self.parse_type_name()
        decl = S.ExternDecl(kw.pos, name.text, params, ret_name, ret_pos)
        while self.tok.kind == "KEYWORD" and self.tok.text in ("cpp", "include", "py"):
            attr = self.advance()
            s = self.tok
            if s.kind != "STRING":
                raise self.error("DAL2001", f"expected a string after '{attr.text}', found {self.describe(s)}")
            self.advance()
            if getattr(decl, attr.text) is not None:
                raise self.error("DAL2006", f"'{attr.text}' given twice", attr.pos)
            setattr(decl, attr.text, s.value)
        self.expect_op(";", "after the extern declaration")
        return decl

    def parse_dt_param(self, what: str) -> tuple[str, Pos]:
        self.expect_op("(", f"after '{what}'")
        dt = self.ident("the name of the delta-time parameter")
        self.expect_op(")", "after the delta-time parameter")
        return dt.text, dt.pos

    def parse_update(self) -> S.UpdateDecl:
        kw = self.advance()
        dt_name, dt_pos = self.parse_dt_param("update")
        body = self.parse_block()
        return S.UpdateDecl(kw.pos, dt_name, dt_pos, body)

    def parse_entry(self) -> S.EntryDecl:
        kw = self.advance()
        name = self.ident("an entry name")
        dt_name, dt_pos = self.parse_dt_param("the entry name")
        body = self.parse_block()
        return S.EntryDecl(kw.pos, name.text, dt_name, dt_pos, body)

    # ---- statements --------------------------------------------------------

    def parse_block(self) -> list[S.Stmt]:
        self.expect_op("{", "to open a block")
        stmts: list[S.Stmt] = []
        while not self.tok.is_op("}"):
            if self.tok.kind == "EOF":
                raise self.error("DAL2001", "expected '}' to close the block, found end of file")
            stmts.append(self.parse_stmt())
        self.advance()
        return stmts

    def parse_stmt(self) -> S.Stmt:
        t = self.tok
        if t.is_kw("let") or t.is_kw("var"):
            return self.parse_let()
        if t.is_kw("if"):
            return self.parse_if()
        if t.is_kw("return"):
            self.advance()
            value = None
            if not self.tok.is_op(";"):
                value = self.parse_expr()
            self.expect_op(";", "after 'return'")
            return S.ReturnStmt(t.pos, value)
        if t.is_op("{"):
            raise self.error("DAL2007", "a bare '{' block is not a statement; blocks belong to 'if'/'else'")
        if t.kind == "IDENT":
            nxt = self.peek()
            if nxt.kind == "OP" and nxt.text in ASSIGN_OPS:
                return self.parse_assign()
            if nxt.is_op("."):
                # v.x = ... is not supported: values are immutable
                j = self.i
                while self.tokens[j].kind == "IDENT" and self.tokens[j + 1].is_op(".") and self.tokens[j + 2].kind == "IDENT":
                    j += 2
                after = self.tokens[j + 1]
                if after.kind == "OP" and after.text in ASSIGN_OPS and j > self.i:
                    raise self.error(
                        "DAL2010",
                        "cannot assign to a component; vectors are values: build a new one, e.g. v = vec2(a, b)",
                        t.pos,
                    )
        if t.kind in ("INT", "FLOAT", "STRING") or t.kind == "IDENT" or t.is_op("("):
            raise self.error(
                "DAL2011",
                f"expected a statement (let, var, assignment, if, return), found an expression starting with {self.describe(t)}",
            )
        raise self.error("DAL2001", f"expected a statement, found {self.describe(t)}")

    def parse_let(self) -> S.LetStmt:
        kw = self.advance()
        name = self.ident("a variable name")
        type_name = None
        type_pos = None
        if self.tok.is_op(":"):
            self.advance()
            type_name, type_pos = self.parse_type_name()
        self.expect_op("=", f"(a '{kw.text}' needs an initial value)")
        init = self.parse_expr()
        self.expect_op(";", f"after the '{kw.text}'")
        return S.LetStmt(kw.pos, name.text, kw.text == "var", type_name, init, name.pos, type_pos)

    def parse_assign(self) -> S.AssignStmt:
        name = self.advance()
        op = self.advance()
        value = self.parse_expr()
        self.expect_op(";", "after the assignment")
        return S.AssignStmt(name.pos, name.text, op.text, value, name.pos)

    def parse_if(self) -> S.IfStmt:
        kw = self.advance()
        self.expect_op("(", "after 'if' (the condition goes in parentheses)")
        cond = self.parse_expr()
        self.expect_op(")", "after the condition")
        then = self.parse_block()
        orelse: list[S.Stmt] = []
        if self.tok.is_kw("else"):
            self.advance()
            if self.tok.is_kw("if"):
                orelse = [self.parse_if()]
            else:
                orelse = self.parse_block()
        return S.IfStmt(kw.pos, cond, then, orelse)

    # ---- expressions -------------------------------------------------------

    def parse_expr(self) -> S.Expr:
        return self.parse_ternary()

    def parse_ternary(self) -> S.Expr:
        cond = self.parse_or()
        if self.tok.is_op("?"):
            q = self.advance()
            a = self.parse_expr()
            self.expect_op(":", "in the conditional expression")
            b = self.parse_expr()
            return S.CondExpr(cond.pos, cond, a, b)
        return cond

    def _binary_level(self, sub, ops: tuple[str, ...]) -> S.Expr:
        left = sub()
        while self.tok.kind == "OP" and self.tok.text in ops:
            op = self.advance()
            right = sub()
            left = S.BinaryExpr(left.pos, op.text, left, right, op.pos)
        return left

    def parse_or(self) -> S.Expr:
        return self._binary_level(self.parse_and, ("||",))

    def parse_and(self) -> S.Expr:
        return self._binary_level(self.parse_equality, ("&&",))

    def parse_equality(self) -> S.Expr:
        return self._binary_level(self.parse_compare, ("==", "!="))

    def parse_compare(self) -> S.Expr:
        return self._binary_level(self.parse_additive, ("<", "<=", ">", ">="))

    def parse_additive(self) -> S.Expr:
        return self._binary_level(self.parse_mul, ("+", "-"))

    def parse_mul(self) -> S.Expr:
        return self._binary_level(self.parse_unary, ("*", "/", "%"))

    def parse_unary(self) -> S.Expr:
        t = self.tok
        if t.kind == "OP" and t.text in ("-", "!"):
            self.advance()
            operand = self.parse_unary()
            return S.UnaryExpr(t.pos, t.text, operand)
        return self.parse_postfix()

    def parse_postfix(self) -> S.Expr:
        expr = self.parse_primary()
        while True:
            t = self.tok
            if t.is_op("."):
                self.advance()
                f = self.tok
                if f.kind != "IDENT":
                    raise self.error("DAL2001", f"expected a member name after '.', found {self.describe(f)}")
                self.advance()
                expr = S.MemberRef(expr.pos, expr, f.text, f.pos)
                continue
            if t.is_op("("):
                if isinstance(expr, S.NameRef):
                    self.advance()
                    args: list[S.Expr] = []
                    if not self.tok.is_op(")"):
                        while True:
                            args.append(self.parse_expr())
                            if self.tok.is_op(","):
                                self.advance()
                                continue
                            break
                    self.expect_op(")", "to close the argument list")
                    expr = S.CallExpr(expr.pos, expr.name, args)
                    continue
                raise self.error("DAL2004", "only functions can be called; write f(x), not x.f(...)", t.pos)
            break
        return expr

    def parse_primary(self) -> S.Expr:
        t = self.tok
        if t.kind == "INT":
            self.advance()
            return S.IntLit(t.pos, t.value)  # type: ignore[arg-type]
        if t.kind == "FLOAT":
            self.advance()
            return S.FloatLit(t.pos, t.value)  # type: ignore[arg-type]
        if t.is_kw("true") or t.is_kw("false"):
            self.advance()
            return S.BoolLit(t.pos, t.text == "true")
        if t.kind == "IDENT":
            if t.text in RESERVED_WORDS:
                raise self.error("DAL2002", f"'{t.text}' is reserved for a future version of the language")
            self.advance()
            return S.NameRef(t.pos, t.text)
        if t.is_kw("host"):
            raise self.error(
                "DAL2012",
                "host values are not used directly in expressions; bind one to an input: "
                "input Name: type <- host.item;",
            )
        if t.is_op("("):
            self.advance()
            inner = self.parse_expr()
            self.expect_op(")", "to close the parenthesis")
            return inner
        if t.kind == "STRING":
            raise self.error("DAL2004", "strings are only allowed in host bindings and extern attributes")
        raise self.error("DAL2004", f"expected an expression, found {self.describe(t)}")


def parse_source(source: SourceFile) -> S.SourceUnit:
    return Parser(source).parse()
