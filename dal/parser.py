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

HOOK_EVENTS = ("movement_mode_changed", "jumped", "landed", "crouch_started", "crouch_ended", "teleported", "physics_rotation")     # `on jumped { ... }`: no name
HANDLER_EVENTS = ("notify", "notify_begin", "notify_end", "notify_tick", "montage_ended", "montage_blending_out", "input") + HOOK_EVENTS
LIFECYCLE_EVENTS = ("begin_play", "end_play")         # `on begin_play { ... }`: no name, not an event of the queue
PHASES = ("pre_movement", "after_movement", "after_anim")
INPUT_PHASES = ("started", "triggered", "ongoing", "completed", "canceled")
LOGIC_KINDS = ("anim", "actor")


class Parser:
    def __init__(self, source: SourceFile):
        self.source = source
        self.tokens = tokenize(source)
        self.i = 0
        self.allow_strings = False

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
                "on": self.parse_handler,
                "timer": self.parse_timer,
                "window": self.parse_window,
                "buffer": self.parse_buffer,
                "combo": self.parse_combo,
                "montage": self.parse_montage,
            }.get(t.text)
            if handler is not None:
                return handler()
        if t.kind == "IDENT" and t.text == "action" and self.peek().kind == "IDENT":
            return self.parse_action()
        if t.kind == "IDENT" and t.text == "use":
            return self.parse_use()
        if t.kind == "IDENT" and t.text == "pub" and self.peek().kind == "IDENT" and self.peek().text in ("method", "view"):
            self.advance()
            return self.parse_method(pub=True)
        if t.kind == "IDENT" and t.text in ("method", "view") and self.peek().kind == "IDENT":
            return self.parse_method(pub=False)
        if t.kind == "IDENT" and t.text == "peer" and self.peek().kind == "IDENT":
            return self.parse_peer()
        if t.kind == "IDENT" and t.text == "settings" and self.peek().kind == "IDENT":
            return self.parse_settings()
        if t.kind == "IDENT" and t.text == "struct":
            return self.parse_struct()
        if t.kind == "IDENT" and t.text == "tags" and self.peek().kind == "IDENT":
            return self.parse_tags()
        if t.kind == "IDENT" and t.text in ("notify", "notify_state") and self.peek().kind == "IDENT":
            return self.parse_notify()
        if t.kind == "IDENT" and t.text == "post_update" and (self.peek().is_op("{") or self.peek().is_op("(")):
            return self.parse_post_update()
        raise self.error(
            "DAL2001",
            "expected a declaration (use, logic, enum, struct, settings, tags, notify, notify_state, const, param, input, state, fn, method, view, peer, extern, update, entry, "
            f"on, timer, window, buffer, combo, montage, action), found {self.describe(t)}",
        )

    def parse_post_update(self) -> S.PostUpdateDecl:
        """`post_update { ... }` or `post_update(dt) { ... }`."""
        kw = self.advance()
        dt_name = dt_pos = None
        if self.tok.is_op("("):
            dt_name, dt_pos = self.parse_dt_param("post_update")
        return S.PostUpdateDecl(kw.pos, dt_name, dt_pos, self.parse_block())

    def parse_notify(self) -> S.NotifyDecl:
        """`notify Name [branching] [cpp "ClassName"] { Member: type [= default]; ... }` (also `notify_state`)."""
        kw = self.advance()
        name = self.ident("a notify name")
        cpp = cpp_pos = None
        branching = False
        while True:
            if self.tok.is_kw("cpp"):
                self.advance()
                s = self.tok
                if s.kind != "STRING":
                    raise self.error("DAL2001", f"expected the C++ class name in quotes after 'cpp', found {self.describe(s)}")
                self.advance()
                if cpp is not None:
                    raise self.error("DAL2006", "'cpp' given twice", s.pos)
                cpp, cpp_pos = str(s.value), s.pos
            elif self.is_word("branching"):
                self.advance()
                branching = True
            else:
                break
        fields = self.parse_fields(kw.text)
        return S.NotifyDecl(kw.pos, name.text, kw.text == "notify_state", fields, cpp, cpp_pos, branching, name_pos=name.pos)

    def parse_use(self) -> S.UseDecl:
        kw = self.advance()      # use (a reserved word, so it is never a name)
        s = self.tok
        if s.kind != "STRING":
            raise self.error("DAL2001", f"expected the path of a library file in quotes after 'use', found {self.describe(s)}")
        self.advance()
        self.expect_op(";", "after the use path")
        return S.UseDecl(kw.pos, str(s.value), s.pos)

    def parse_fields(self, what: str, allow_derived: bool = False) -> list[S.FieldDecl]:
        """`{ name: type [= init]; ... }` -- the members of a struct (or settings) declaration."""
        self.expect_op("{", f"after the {what} name")
        fields: list[S.FieldDecl] = []
        while not self.tok.is_op("}"):
            if self.tok.kind == "EOF":
                raise self.error("DAL2001", f"expected '}}' to close the {what}, found end of file")
            derived = False
            if self.is_word("derived") and self.peek().kind == "IDENT":       # only settings may have them: the checker says so
                self.advance()
                derived = True
            f = self.ident("a member name")
            self.expect_op(":", "after the member name")
            type_name, type_pos = self.parse_type_name()
            init = None
            if self.tok.is_op("="):
                self.advance()
                init = self.parse_expr()
            self.expect_op(";", "after the member")
            fields.append(S.FieldDecl(f.text, type_name, f.pos, type_pos, init, derived))
        self.advance()
        return fields

    def parse_method(self, pub: bool) -> S.MethodDecl:
        kw = self.advance()      # method | view
        is_view = kw.text == "view"
        name = self.ident("a view name" if is_view else "a method name")
        params = self.parse_params()
        ret_name = ret_pos = None
        if self.tok.is_op("->"):
            self.advance()
            ret_name, ret_pos = self.parse_type_name()
        elif is_view:
            raise self.error("DAL2001", "expected '->' (a view declares the type it returns: view name(...) -> float)")
        body = self.parse_block()
        return S.MethodDecl(kw.pos, name.text, params, ret_name, ret_pos, body, pub, is_view, name_pos=name.pos)

    def parse_peer(self) -> S.PeerDecl:
        kw = self.advance()      # peer
        name = self.ident("a peer name")
        self.expect_op(":", "after the peer name")
        type_name, type_pos = self.parse_type_name()
        self.expect_op(";", "after the peer")
        return S.PeerDecl(kw.pos, name.text, type_name, type_pos, name_pos=name.pos)

    def parse_tags(self) -> S.TagsDecl:
        kw = self.advance()      # tags
        name = self.ident("a tag set name")
        self.expect_op("=", "after the tag set name (tags Name = \"Prefix\" { Member, ... })")
        s = self.tok
        if s.kind != "STRING":
            raise self.error("DAL2001", f"expected the tag prefix in quotes, found {self.describe(s)}")
        self.advance()
        self.expect_op("{", "after the tag prefix")
        members: list[tuple[str, Pos]] = []
        while True:
            m = self.ident("a tag name")
            members.append((m.text, m.pos))
            if self.tok.is_op(","):
                self.advance()
                if self.tok.is_op("}"):
                    break
                continue
            break
        self.expect_op("}", "to close the tag set")
        return S.TagsDecl(kw.pos, name.text, str(s.value), s.pos, members, name_pos=name.pos)

    def parse_settings(self) -> S.StructDecl:
        kw = self.advance()      # settings
        name = self.ident("a settings name")
        fields = self.parse_fields("settings", allow_derived=True)
        return S.StructDecl(kw.pos, name.text, fields, "settings", name_pos=name.pos)

    def parse_struct(self) -> S.StructDecl:
        kw = self.advance()      # struct (a reserved word, so it is never a name)
        name = self.ident("a struct name")
        fields = self.parse_fields("struct")
        return S.StructDecl(kw.pos, name.text, fields, name_pos=name.pos)

    def parse_type_name(self) -> tuple[str, Pos]:
        t = self.tok
        if t.is_kw("montage"):              # the asset type `montage` (the declaration keyword is a different use)
            self.advance()
            return "montage", t.pos
        if t.kind != "IDENT":
            raise self.error("DAL2001", f"expected a type name, found {self.describe(t)}")
        self.advance()
        if t.text in ("map", "array") and self.tok.is_op("<"):
            self.advance()
            args: list[str] = []
            while True:
                a, _p = self.parse_type_name()
                args.append(a)
                if self.tok.is_op(",") and t.text == "map" and len(args) < 2:
                    self.advance()
                    continue
                break
            need = 2 if t.text == "map" else 1
            if len(args) != need:
                raise self.error("DAL2001", f"{t.text}<...> takes {need} type argument(s) ({'map<Key, Value>' if need == 2 else 'array<Element>'})", t.pos)
            self.expect_op(">", f"to close {t.text}<...>")
            return f"{t.text}<{','.join(args)}>", t.pos
        return t.text, t.pos

    def parse_logic(self) -> S.LogicDecl:
        kw = self.advance()
        name = self.ident("a logic name")
        kind, kind_pos = "anim", None
        if self.tok.is_op(":"):
            self.advance()
            k = self.tok
            if k.kind != "IDENT" or k.text not in LOGIC_KINDS:
                raise self.error("DAL2020", f"expected the logic kind 'anim' or 'actor', found {self.describe(k)}")
            self.advance()
            kind, kind_pos = k.text, k.pos
        linked, linked_pos = None, None
        post_flush, post_flush_pos = False, None
        slot_events, slot_events_pos = False, None
        flush_peers, flush_peers_pos = False, None
        order: list = []
        while (self.is_word("linked") or self.is_word("post_flush") or self.is_word("slot_events") or self.is_word("flush_peers")
               or self.is_word("before") or self.is_word("after")):       # the options of the logic, in any order
            lk = self.advance()
            if lk.text == "flush_peers":
                if kind != "actor":
                    raise self.error("DAL2026", "only an actor logic calls its peers at once: logic Name : actor flush_peers in pre_movement;", lk.pos)
                flush_peers, flush_peers_pos = True, lk.pos
                continue
            if lk.text == "slot_events":
                if kind != "actor":
                    raise self.error("DAL2026", "only an actor logic has tick slots: logic Name : actor slot_events in after_movement;", lk.pos)
                slot_events, slot_events_pos = True, lk.pos
                continue
            if lk.text in ("before", "after"):
                if kind != "actor":
                    raise self.error("DAL2026", f"only an actor logic has tick slots to order: logic Name : actor {lk.text} Peer in pre_movement;", lk.pos)
                while True:
                    peer = self.ident("the name of a peer")
                    order.append((lk.text, peer.text, peer.pos))
                    if not self.tok.is_op(","):
                        break
                    self.advance()
                continue
            if lk.text == "post_flush":
                if kind != "anim":
                    raise self.error("DAL2025", "only an animation logic can flush its commands after the post_update: logic Name : anim post_flush;", lk.pos)
                post_flush, post_flush_pos = True, lk.pos
                continue
            if kind != "anim":
                raise self.error("DAL2024", "only an animation logic can be linked: logic Name : anim linked(MainLogic);", lk.pos)
            self.expect_op("(", "after 'linked'")
            parent = self.ident("the name of the main animation logic")
            self.expect_op(")", "to close linked(...)")
            linked, linked_pos = parent.text, parent.pos
        phase, phase_pos = self.parse_phase()
        self.expect_op(";", "after the logic name")
        return S.LogicDecl(kw.pos, name.text, kind, kind_pos, phase, phase_pos, linked, linked_pos, post_flush, post_flush_pos,
                           slot_events, slot_events_pos, order, name_pos=name.pos, flush_peers=flush_peers, flush_peers_pos=flush_peers_pos)

    def parse_phase(self) -> tuple[str | None, Pos | None]:
        """Optional `in pre_movement | after_movement | after_anim`."""
        if not self.tok.is_kw("in"):
            return None, None
        self.advance()
        t = self.tok
        if t.kind != "IDENT" or t.text not in PHASES:
            raise self.error("DAL2023", f"expected a tick slot ({', '.join(PHASES)}) after 'in', found {self.describe(t)}")
        self.advance()
        return t.text, t.pos

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
        return S.EnumDecl(kw.pos, name.text, members, name_pos=name.pos)

    def parse_const(self) -> S.ConstDecl:
        kw = self.advance()
        name = self.ident("a constant name")
        self.expect_op(":", "after the constant name")
        type_name, type_pos = self.parse_type_name()
        self.expect_op("=", "(a const needs a value)")
        init = self.parse_expr()
        self.expect_op(";", "after the constant")
        return S.ConstDecl(kw.pos, name.text, type_name, type_pos, init, name_pos=name.pos)

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
        return S.ParamDecl(kw.pos, name.text, type_name, type_pos, init, name_pos=name.pos)

    def parse_input(self) -> S.InputDecl:
        kw = self.advance()
        name = self.ident("an input name")
        self.expect_op(":", "after the input name")
        type_name, type_pos = self.parse_type_name()
        binding = None
        default = None
        peer = None
        if self.tok.is_op("<") and self.peek().is_op("-") and self.peek().pos.col == self.tok.pos.col + 1 \
                and self.peek().pos.line == self.tok.pos.line:
            self.advance()
            self.advance()
            if self.tok.kind == "IDENT":
                peer = self.parse_peer_binding()
            else:
                binding = self.parse_host_binding()
        elif self.tok.is_op("="):
            self.advance()
            default = self.parse_expr()
        self.expect_op(";", "after the input")
        return S.InputDecl(kw.pos, name.text, type_name, type_pos, binding, default, peer, name_pos=name.pos)

    def parse_peer_binding(self) -> S.PeerBinding:
        peer = self.ident("a peer name")
        path: list[str] = []
        path_pos: list[Pos] = []
        while self.tok.is_op("."):
            self.advance()
            m = self.tok
            if m.kind != "IDENT":
                raise self.error("DAL2001", f"expected a member name after '.', found {self.describe(m)}")
            self.advance()
            path.append(m.text)
            path_pos.append(m.pos)
        if not path:
            raise self.error("DAL2001", f"expected '.<state>' after the peer name '{peer.text}' (input X: T <- {peer.text}.State;)", self.tok.pos)
        return S.PeerBinding(peer.text, path, peer.pos, path_pos)

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
        arg_is_ident = False
        if self.tok.is_op("("):
            self.advance()
            s = self.tok
            if s.kind == "STRING":
                arg, arg_pos = s.value, s.pos
            elif s.kind == "IDENT":
                arg, arg_pos, arg_is_ident = s.text, s.pos, True
            else:
                raise self.error("DAL2001", f"expected a string or a declared name, found {self.describe(s)}")
            self.advance()
            arg2, arg2_pos = None, None
            if self.tok.is_op(","):
                self.advance()
                s2 = self.tok
                if s2.kind != "STRING":
                    raise self.error("DAL2001", f"expected a second quoted name, found {self.describe(s2)}")
                arg2, arg2_pos = s2.value, s2.pos
                self.advance()
            self.expect_op(")", "after the host item argument")
            return S.HostBinding(item.text, arg, Pos(h.pos.line, h.pos.col, item.pos.end_col), arg_pos, arg_is_ident, arg2, arg2_pos)
        return S.HostBinding(item.text, arg, Pos(h.pos.line, h.pos.col, item.pos.end_col), arg_pos, arg_is_ident)

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
        return S.StateDecl(kw.pos, name.text, type_name, type_pos, init, name_pos=name.pos)

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
        return S.FnDecl(kw.pos, name.text, params, ret_name, ret_pos, body, name_pos=name.pos)

    def parse_extern(self) -> S.ExternDecl:
        kw = self.advance()
        self.expect_kw("fn", "after 'extern'")
        name = self.ident("a function name")
        params = self.parse_params()
        self.expect_op("->", "(extern functions declare a return type)")
        ret_name, ret_pos = self.parse_type_name()
        decl = S.ExternDecl(kw.pos, name.text, params, ret_name, ret_pos, name_pos=name.pos)
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
        phase, phase_pos = self.parse_phase()
        body = self.parse_block()
        return S.UpdateDecl(kw.pos, dt_name, dt_pos, body, phase=phase, phase_pos=phase_pos)

    def parse_entry(self) -> S.EntryDecl:
        """`entry Name(dt, A: type = default, ...) [linked] { ... }`: a bare name first is the delta time (v0), then typed parameters."""
        kw = self.advance()
        name = self.ident("an entry name")
        self.expect_op("(", "after the entry name")
        dt_name, dt_pos = None, None
        if self.tok.kind == "IDENT" and not self.peek().is_op(":"):
            dt = self.ident("the name of the delta-time parameter")
            dt_name, dt_pos = dt.text, dt.pos
            if self.tok.is_op(","):
                self.advance()
        params: list[S.Param] = []
        while not self.tok.is_op(")"):
            pname = self.ident("a parameter name")
            self.expect_op(":", "after the parameter name")
            type_name, type_pos = self.parse_type_name()
            init = None
            if self.tok.is_op("="):
                self.advance()
                init = self.parse_expr()
            params.append(S.Param(pname.text, type_name, pname.pos, type_pos, init))
            if self.tok.is_op(","):
                self.advance()
                continue
            break
        self.expect_op(")", "to close the entry's parameter list")
        linked = False
        if self.is_word("linked"):
            self.advance()
            linked = True
        body = self.parse_block()
        return S.EntryDecl(kw.pos, name.text, dt_name, dt_pos, body, params, linked, name_pos=name.pos)

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
        if t.is_kw("for"):
            return self.parse_for()
        if t.is_kw("while"):
            return self.parse_while()
        if t.is_kw("break"):
            self.advance()
            self.expect_op(";", "after 'break'")
            return S.BreakStmt(t.pos)
        if t.is_kw("host"):
            return self.parse_cmd()
        if t.kind == "IDENT" and self.peek().is_op("(") and t.text not in RESERVED_WORDS:
            return self.parse_call_stmt()
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
                # a.b.c = value;  (a member of a struct state / variable)
                j = self.i
                while self.tokens[j].kind == "IDENT" and self.tokens[j + 1].is_op(".") and self.tokens[j + 2].kind == "IDENT":
                    j += 2
                after = self.tokens[j + 1]
                if after.kind == "OP" and after.text in ASSIGN_OPS and j > self.i:
                    return self.parse_member_assign()
                if after.is_op("(") and j > self.i:
                    # Peer.Method(args);
                    call = self.parse_postfix()
                    self.expect_op(";", "after the call")
                    return S.ExprStmt(t.pos, call)
        if t.kind in ("INT", "FLOAT", "STRING") or t.kind == "IDENT" or t.is_op("("):
            raise self.error(
                "DAL2011",
                f"expected a statement (let, var, assignment, if, return), found an expression starting with {self.describe(t)}",
            )
        raise self.error("DAL2001", f"expected a statement, found {self.describe(t)}")

    def parse_member_assign(self) -> S.MemberAssignStmt:
        name = self.advance()
        path: list[str] = []
        path_pos: list[Pos] = []
        while self.tok.is_op("."):
            self.advance()
            f = self.tok
            if f.kind != "IDENT":
                raise self.error("DAL2001", f"expected a member name after '.', found {self.describe(f)}")
            self.advance()
            path.append(f.text)
            path_pos.append(f.pos)
        op = self.advance()
        value = self.parse_expr()
        self.expect_op(";", "after the assignment")
        return S.MemberAssignStmt(name.pos, name.text, path, op.text, value, name.pos, path_pos)

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

    # ---- v1 / v2 statements ----------------------------------------------------------

    def parse_for(self) -> S.ForStmt:
        kw = self.advance()
        var = self.ident("the loop variable")
        self.expect_kw("in", "after the loop variable")
        lo = self.parse_additive()
        self.expect_op("..", "between the loop bounds (for i in 0..8)")
        hi = self.parse_additive()
        body = self.parse_block()
        return S.ForStmt(kw.pos, var.text, lo, hi, body, var.pos)

    def parse_while(self) -> S.WhileStmt:
        kw = self.advance()
        self.expect_op("(", "after 'while'")
        cond = self.parse_expr()
        self.expect_op(")", "to close the condition")
        if not (self.tok.kind == "IDENT" and self.tok.text == "bound"):
            raise self.error("DAL2001", f"a 'while' needs the bound on its runs: while (cond) bound 8 {{ ... }}, found {self.describe(self.tok)}")
        self.advance()
        bound = self.parse_additive()
        body = self.parse_block()
        return S.WhileStmt(kw.pos, cond, bound, body)

    def parse_args(self) -> list[S.Expr]:
        self.expect_op("(", "to open the argument list")
        args: list[S.Expr] = []
        if not self.tok.is_op(")"):
            while True:
                args.append(self.parse_expr())
                if self.tok.is_op(","):
                    self.advance()
                    continue
                break
        self.expect_op(")", "to close the argument list")
        return args

    def parse_cmd(self) -> S.CmdStmt:
        kw = self.advance()      # host
        self.expect_op(".", "after 'host'")
        name = self.tok
        if name.kind != "IDENT":
            raise self.error("DAL2001", f"expected a host command name after 'host.', found {self.describe(name)}")
        self.advance()
        self.allow_strings = True                  # the names of slots are strings (the checker says where they belong)
        try:
            args = self.parse_args()
        finally:
            self.allow_strings = False
        self.expect_op(";", "after the host command")
        return S.CmdStmt(kw.pos, name.text, args, name.pos)

    def parse_call_stmt(self) -> S.CallStmt:
        name = self.advance()
        args = self.parse_args()
        self.expect_op(";", "after the statement")
        return S.CallStmt(name.pos, name.text, args)

    # ---- v1 / v2 declarations ----------------------------------------------------------

    def is_word(self, text: str) -> bool:
        return self.tok.kind == "IDENT" and self.tok.text == text

    def at_left_arrow(self) -> bool:
        return (self.tok.is_op("<") and self.peek().is_op("-") and self.peek().pos.col == self.tok.pos.col + 1
                and self.peek().pos.line == self.tok.pos.line)

    def parse_handler(self) -> S.HandlerDecl:
        kw = self.advance()      # on
        t = self.tok
        if not (t.kind == "IDENT" or t.is_kw("input") or t.is_kw("timer")):
            raise self.error("DAL2001", f"expected an event name after 'on', found {self.describe(t)}")
        if t.is_kw("timer"):
            self.advance()
            name = self.ident("a timer name")
            return S.HandlerDecl(kw.pos, "timer", name.text, name.pos, None, self.parse_block())
        if t.kind == "IDENT" and t.text in LIFECYCLE_EVENTS:
            self.advance()
            return S.HandlerDecl(kw.pos, t.text, "", t.pos, None, self.parse_block())
        if t.text not in HANDLER_EVENTS:
            raise self.error("DAL2021", f"unknown event '{t.text}'; events: {', '.join(HANDLER_EVENTS + LIFECYCLE_EVENTS + ('timer',))}")
        self.advance()
        if t.text in HOOK_EVENTS:
            names: list[tuple[str, Pos]] = []
            if self.tok.is_op("("):
                self.advance()
                while not self.tok.is_op(")"):
                    p = self.ident("a parameter name")
                    names.append((p.text, p.pos))
                    if self.tok.is_op(","):
                        self.advance()
                        continue
                    break
                self.expect_op(")", "to close the parameter list")
            return S.HandlerDecl(kw.pos, t.text, "", t.pos, None, self.parse_block(), names)
        name = self.ident("the name the event is about")
        phase = None
        if t.text == "input":
            ph = self.tok
            if ph.kind != "IDENT" or ph.text not in INPUT_PHASES:
                raise self.error("DAL2022", f"expected the input phase ({', '.join(INPUT_PHASES)}), found {self.describe(ph)}")
            self.advance()
            phase = ph.text
        body = self.parse_block()
        return S.HandlerDecl(kw.pos, t.text, name.text, name.pos, phase, body)

    def parse_montage(self) -> S.MontageDecl:
        kw = self.advance()
        name = self.ident("a montage name")
        sections: list[tuple[str, Pos]] = []
        if self.tok.is_op("{"):
            self.advance()
            while not self.tok.is_op("}"):
                sec = self.ident("a section name")
                sections.append((sec.text, sec.pos))
                if self.tok.is_op(","):
                    self.advance()
                    continue
                break
            self.expect_op("}", "to close the section list")
        else:
            self.expect_op(";", "after the montage name")
        return S.MontageDecl(kw.pos, name.text, sections, name_pos=name.pos)

    def parse_action(self) -> S.ActionDecl:
        kw = self.advance()      # action
        name = self.ident("an action name")
        self.expect_op(":", "after the action name")
        type_name, type_pos = self.parse_type_name()
        self.expect_op(";", "after the action")
        return S.ActionDecl(kw.pos, name.text, type_name, type_pos, name_pos=name.pos)

    def parse_timer(self) -> S.TimerDecl:
        kw = self.advance()
        name = self.ident("a timer name")
        duration = None
        if self.tok.is_op("="):
            self.advance()
            duration = self.parse_expr()
        self.expect_op(";", "after the timer")
        return S.TimerDecl(kw.pos, name.text, duration, name_pos=name.pos)

    def parse_window(self) -> S.WindowDecl:
        kw = self.advance()
        name = self.ident("a window name")
        notify = None
        notify_pos = None
        closes: list[tuple[str, Pos]] = []
        if self.at_left_arrow():
            self.advance()
            self.advance()
            if not self.is_word("notify_state"):
                raise self.error("DAL2001", f"expected 'notify_state <name>' after '<-', found {self.describe(self.tok)}")
            self.advance()
            n = self.ident("the notify state name")
            notify, notify_pos = n.text, n.pos
        if self.is_word("closes_on"):
            self.advance()
            while True:
                m = self.ident("a montage name")
                closes.append((m.text, m.pos))
                if self.tok.is_op(","):
                    self.advance()
                    continue
                break
        self.expect_op(";", "after the window")
        return S.WindowDecl(kw.pos, name.text, notify, notify_pos, closes, name_pos=name.pos)

    def parse_buffer(self) -> S.BufferDecl:
        kw = self.advance()
        name = self.ident("a buffer name")
        self.expect_op("=", "(a buffer needs its window length in seconds)")
        duration = self.parse_additive()
        if not self.at_left_arrow():
            raise self.error("DAL2001", f"expected '<- input <Action> <phase>' after the buffer length, found {self.describe(self.tok)}")
        self.advance()
        self.advance()
        triggers: list[S.BufferTrigger] = []
        while True:
            self.expect_kw("input", "(buffers are filled by input events)")
            act = self.ident("an action name")
            ph = self.tok
            if ph.kind != "IDENT" or ph.text not in INPUT_PHASES:
                raise self.error("DAL2022", f"expected the input phase ({', '.join(INPUT_PHASES)}), found {self.describe(ph)}")
            self.advance()
            triggers.append(S.BufferTrigger(act.text, ph.text, act.pos))
            if self.tok.is_op(","):
                self.advance()
                continue
            break
        when = None
        if self.is_word("when"):
            self.advance()
            when = self.parse_expr()
        self.expect_op(";", "after the buffer")
        return S.BufferDecl(kw.pos, name.text, duration, triggers, when, name_pos=name.pos)

    def parse_combo_rule(self, first: Token) -> S.ComboRule:
        """[on Buffer] [within Window] [when expr] -> Target ;   (``first`` is the rule's first token, already consumed)"""
        trigger = trigger_pos = within = within_pos = when = None
        if first.is_kw("on"):
            t = self.ident("a buffer name")
            trigger, trigger_pos = t.text, t.pos
        elif first.kind == "IDENT" and first.text == "when":
            when = self.parse_expr()
        if when is None and self.is_word("within"):
            self.advance()
            w = self.ident("a window name")
            within, within_pos = w.text, w.pos
        if when is None and self.is_word("when"):
            self.advance()
            when = self.parse_expr()
        self.expect_op("->", "(a combo rule ends with '-> Step;')")
        target = self.ident("a step name")
        self.expect_op(";", "after the combo rule")
        return S.ComboRule(trigger, trigger_pos, within, within_pos, when, target.text, target.pos, first.pos)

    def parse_combo(self) -> S.ComboDecl:
        kw = self.advance()
        name = self.ident("a combo name")
        self.expect_op("{", "after the combo name")
        combo = S.ComboDecl(kw.pos, name.text, name_pos=name.pos)
        while not self.tok.is_op("}"):
            t = self.tok
            if t.kind == "EOF":
                raise self.error("DAL2001", "expected '}' to close the combo, found end of file")
            if t.kind == "IDENT" and t.text == "reset":
                self.advance()
                if not self.is_word("when"):
                    raise self.error("DAL2001", f"expected 'when <condition>' after 'reset', found {self.describe(self.tok)}")
                self.advance()
                combo.reset_when = self.parse_expr()
                self.expect_op(";", "after the reset rule")
            elif t.kind == "IDENT" and t.text == "start":
                self.advance()
                first = self.advance() if (self.tok.is_kw("on") or self.is_word("when")) else None
                if first is None:
                    raise self.error("DAL2001", f"expected 'on <buffer>' or 'when <condition>' after 'start', found {self.describe(self.tok)}")
                combo.starts.append(self.parse_combo_rule(first))
            elif t.kind == "IDENT" and t.text == "step":
                combo.steps.append(self.parse_combo_step())
            else:
                raise self.error("DAL2001", f"expected 'start', 'step' or 'reset' in a combo, found {self.describe(t)}")
        self.advance()
        return combo

    def parse_combo_step(self) -> S.ComboStep:
        kw = self.advance()      # step
        name = self.ident("a step name")
        self.expect_op("{", "after the step name")
        step = S.ComboStep(name.text, name.pos)
        while not self.tok.is_op("}"):
            t = self.tok
            if t.kind == "EOF":
                raise self.error("DAL2001", "expected '}' to close the step, found end of file")
            if t.kind == "IDENT" and t.text == "play":
                self.advance()
                m = self.ident("a montage name")
                play = S.ComboPlay(m.text, m.pos)
                while self.tok.kind == "IDENT" and self.tok.text in ("rate", "from"):
                    word = self.advance().text
                    value = self.parse_expr()
                    if word == "rate":
                        play.rate = value
                    else:
                        play.section = value
                self.expect_op(";", "after 'play'")
                step.play = play
            elif t.is_kw("on") or (t.kind == "IDENT" and t.text == "when"):
                self.advance()
                step.rules.append(self.parse_combo_rule(t))
            elif t.kind == "IDENT" and t.text == "after":
                self.advance()
                seconds = self.parse_expr()
                self.expect_op("->", "(a timeout rule ends with '-> Step;')")
                target = self.ident("a step name")
                self.expect_op(";", "after the timeout rule")
                step.after.append((seconds, target.text, target.pos))
            elif t.kind == "IDENT" and t.text in ("enter", "exit"):
                self.advance()
                block = self.parse_block()
                if t.text == "enter":
                    step.enter = block
                else:
                    step.exit = block
            else:
                raise self.error("DAL2001", f"expected play, on, when, after, enter or exit in a step, found {self.describe(t)}")
        self.advance()
        return step

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
            if t.is_op("["):
                self.advance()
                index = self.parse_expr()
                self.expect_op("]", "to close the index")
                expr = S.IndexExpr(expr.pos, expr, index)
                continue
            if t.is_op("("):
                if isinstance(expr, S.NameRef):
                    self.advance()
                    args, names, name_pos = self.parse_call_args()
                    named = any(n is not None for n in names)
                    expr = S.CallExpr(expr.pos, expr.name, args, names if named else None, name_pos if named else None)
                    continue
                if isinstance(expr, S.MemberRef):
                    # Peer.Method(args), curve.eval(x), map.has(k): the checker decides what the base may be
                    self.advance()
                    args, names, _name_pos = self.parse_call_args()
                    if any(n is not None for n in names):
                        raise self.error("DAL4054", "named arguments are only used to build structs: Name(member: value)", t.pos)
                    expr = S.MemberCallExpr(expr.pos, expr.base, expr.field, args, expr.field_pos)
                    continue
                raise self.error("DAL2004", "only functions can be called; write f(x), not x.f(...)", t.pos)
            break
        return expr

    def parse_call_args(self) -> tuple[list[S.Expr], list[str | None], list[Pos | None]]:
        """Arguments after '(' up to and including ')': `a, b` or, for a struct, `member: a, other: b`."""
        args: list[S.Expr] = []
        names: list[str | None] = []
        name_pos: list[Pos | None] = []
        if not self.tok.is_op(")"):
            while True:
                a = self.tok
                if a.kind == "IDENT" and self.peek().is_op(":") and a.text not in RESERVED_WORDS:
                    self.advance()
                    self.advance()
                    names.append(a.text)
                    name_pos.append(a.pos)
                else:
                    names.append(None)
                    name_pos.append(None)
                args.append(self.parse_expr())
                if self.tok.is_op(","):
                    self.advance()
                    continue
                break
        self.expect_op(")", "to close the argument list")
        return args, names, name_pos

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
            if self.peek().is_op(".") and self.peek(2).kind == "IDENT" and self.peek(3).is_op("("):
                self.advance()
                self.advance()
                name = self.advance()
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
                return S.HostCallExpr(t.pos, name.text, args)
            raise self.error(
                "DAL2012",
                "host values are not used directly in expressions; bind one to an input: "
                "input Name: type <- host.item;  (host queries take arguments: host.trace_line(a, b))",
            )
        if t.is_op("("):
            self.advance()
            inner = self.parse_expr()
            self.expect_op(")", "to close the parenthesis")
            return inner
        if t.kind == "STRING":
            if self.allow_strings:
                self.advance()
                return S.StringLit(t.pos, str(t.value))
            raise self.error("DAL2004", "strings are only allowed in host bindings, extern attributes and as the slot name of a host command")
        raise self.error("DAL2004", f"expected an expression, found {self.describe(t)}")


def parse_source(source: SourceFile) -> S.SourceUnit:
    return Parser(source).parse()
