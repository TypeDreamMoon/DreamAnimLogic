"""Lowering of the v1 sugar: ``timer``, ``window``, ``buffer`` and ``combo`` become plain declarations.

The expansion happens on the syntax tree, before checking, so everything downstream (checker, IR,
interpreter, both backends) only knows the core language: states, enums, ``on`` handlers, ``host.``
commands and ordinary statements.  Programs that use none of the sugar pass through untouched.

* ``timer T = d;``                state ``T: float`` (remaining seconds); ``start(T)`` ``start(T, x)`` ``stop(T)``;
                                  ``T.active`` ``T.ready`` ``T.remaining``; ticked down at the start of every update
* ``window W [<- notify_state N] [closes_on M, ...];``
                                  state ``W: bool``; ``open(W)`` / ``close(W)``; handlers follow the notify state
* ``buffer B = d <- input A phase [when e];``
                                  a timer filled by the input event; ``B.pending``; ``consume(B)``
* ``combo C { ... }``             enum ``CStep`` and state ``C`` plus the transition rules, run at the start of ``update``

Order inside one update (also documented in the language reference): timers tick, events run their
handlers, the combo rules run, then your ``update`` body.
"""

from __future__ import annotations

from dataclasses import replace

from . import syntax as S
from .errors import Diagnostic, DalError, Pos, SourceFile


class Lowerer:
    def __init__(self, unit: S.SourceUnit, source: SourceFile):
        self.unit = unit
        self.source = source
        self.diags: list[Diagnostic] = []
        self.kinds: dict[str, str] = {}               # name -> timer | window | buffer | combo
        self.timer_default: dict[str, S.Expr | None] = {}
        self.dt_name = "dt"
        self.pre: list[S.Stmt] = []
        self.prefix: list[S.Stmt] = []                # statements inserted at the start of the update body

    # ---- diagnostics -------------------------------------------------------------------------------------------
    def err(self, code: str, message: str, pos: Pos) -> None:
        self.diags.append(self.source.diag(code, message, pos))

    # ---- tiny AST builders ------------------------------------------------------------------------------------------
    @staticmethod
    def name(n: str, pos: Pos) -> S.NameRef:
        return S.NameRef(pos, n)

    @staticmethod
    def flt(v: float, pos: Pos) -> S.FloatLit:
        return S.FloatLit(pos, v)

    @staticmethod
    def bool_(v: bool, pos: Pos) -> S.BoolLit:
        return S.BoolLit(pos, v)

    @staticmethod
    def bin(op: str, a: S.Expr, b: S.Expr, pos: Pos) -> S.BinaryExpr:
        return S.BinaryExpr(pos, op, a, b, pos)

    @staticmethod
    def assign(n: str, value: S.Expr, pos: Pos) -> S.AssignStmt:
        return S.AssignStmt(pos, n, "=", value, pos)

    @staticmethod
    def member(base: str, field: str, pos: Pos) -> S.MemberRef:
        return S.MemberRef(pos, S.NameRef(pos, base), field, pos)

    # ---- entry ----------------------------------------------------------------------------------------------------------
    PHASE_ORDER = ("pre_movement", "after_movement", "after_anim", "physics_walking", "prepare_root_motion")
    SUGAR_CALLS = ("start", "stop", "open", "close", "consume")

    def run(self) -> S.SourceUnit:
        sugar = (S.TimerDecl, S.WindowDecl, S.BufferDecl, S.ComboDecl)
        has_sugar = any(isinstance(d, sugar) for d in self.unit.decls)
        has_timer_handlers = any(isinstance(d, S.HandlerDecl) and d.event == "timer" for d in self.unit.decls)
        if not has_sugar and not has_timer_handlers and not self._any_call_stmt():
            return self.unit

        for d in self.unit.decls:
            if isinstance(d, S.UpdateDecl):
                self.dt_name = d.dt_name
        for d in self.unit.decls:
            if isinstance(d, S.TimerDecl):
                self.declare(d.name, "timer", d.pos)
                self.timer_default[d.name] = d.duration
            elif isinstance(d, S.WindowDecl):
                self.declare(d.name, "window", d.pos)
            elif isinstance(d, S.BufferDecl):
                self.declare(d.name, "buffer", d.pos)
                self.timer_default[d.name] = d.duration
            elif isinstance(d, S.ComboDecl):
                self.declare(d.name, "combo", d.pos)

        # `on timer T { ... }`: the handlers of a timer become private methods the countdown calls when it reaches zero
        fired: dict[str, list[str]] = {}
        methods: list[S.Decl] = []
        for d in self.unit.decls:
            if isinstance(d, S.HandlerDecl) and d.event == "timer":
                if self.kinds.get(d.name) != "timer":
                    what = f"'{d.name}' is a {self.kinds[d.name]}" if d.name in self.kinds else f"there is no timer '{d.name}'"
                    self.err("DAL3063", f"{what}: 'on timer' reacts to a declared timer (timer {d.name} = seconds;)", d.target_pos or d.pos)
                    continue
                names = fired.setdefault(d.name, [])
                mname = f"On{d.name}" if not names else f"On{d.name}_{len(names) + 1}"
                names.append(mname)
                methods.append(S.MethodDecl(d.pos, mname, [], None, None, d.body, False, False, name_pos=d.target_pos or d.pos))

        out: list[S.Decl] = []
        for d in self.unit.decls:
            if isinstance(d, S.HandlerDecl) and d.event == "timer":
                continue
            if isinstance(d, S.TimerDecl):
                out.append(S.StateDecl(d.pos, d.name, "float", d.pos, None, name_pos=d.name_pos))
                self.pre.append(self.tick(d.name, d.pos, fired.get(d.name, [])))
            elif isinstance(d, S.WindowDecl):
                out.extend(self.lower_window(d))
            elif isinstance(d, S.BufferDecl):
                out.extend(self.lower_buffer(d))
                self.pre.append(self.tick(d.name, d.pos))
            elif isinstance(d, S.ComboDecl):
                out.extend(self.lower_combo(d))
            else:
                out.append(d)

        out.extend(methods)

        # rewrite user code (and the generated code, which may contain user expressions)
        final: list[S.Decl] = []
        updates: list[S.UpdateDecl] = []
        for d in out:
            if isinstance(d, S.UpdateDecl):
                updates.append(d)
                continue
            final.append(self.rewrite_decl(d))
        logic = next((d for d in self.unit.decls if isinstance(d, S.LogicDecl)), None)
        logic_pos = logic.pos if logic is not None else Pos(1, 1, 2)
        if not updates and (self.pre or self.prefix):
            updates = [S.UpdateDecl(logic_pos, self.dt_name, logic_pos, [])]
        # timers, windows and combos belong to the earliest tick slot of the logic (events are handled there too)
        default_phase = (logic.phase if logic is not None and logic.kind == "actor" else None) or "pre_movement"

        def slot(u: S.UpdateDecl) -> int:
            ph = (u.phase if logic is not None and logic.kind == "actor" else None) or default_phase
            return self.PHASE_ORDER.index(ph)

        first = min(updates, key=slot) if updates else None
        for u in updates:
            if u is first:
                body = self.rewrite_stmts(self.prefix) + self.rewrite_stmts(u.body)
                final.append(replace(u, pre=self.pre + self.rewrite_stmts(u.pre), body=body))
            else:
                final.append(replace(u, pre=self.rewrite_stmts(u.pre), body=self.rewrite_stmts(u.body)))

        if self.diags:
            raise DalError(self.diags)
        return S.SourceUnit(final)

    def declare(self, name: str, kind: str, pos: Pos) -> None:
        if name in self.kinds:
            self.err("DAL3001", f"'{name}' is already declared as a {self.kinds[name]}", pos)
        self.kinds[name] = kind

    def _any_call_stmt(self) -> bool:
        def walk(stmts):
            for s in stmts:
                if isinstance(s, S.CallStmt) and s.callee in self.SUGAR_CALLS:
                    return True
                if isinstance(s, S.IfStmt) and (walk(s.then) or walk(s.orelse)):
                    return True
                if isinstance(s, (S.ForStmt, S.WhileStmt)) and walk(s.body):
                    return True
            return False

        for d in self.unit.decls:
            body = getattr(d, "body", None)
            if isinstance(body, list) and walk(body):
                return True
        return False

    def tick(self, n: str, pos: Pos, fired: list[str] | None = None) -> S.Stmt:
        """if (T > 0.0) { T = max(T - dt, 0.0); [if (T <= 0.0) { OnT(); }] }  -- the handlers run once, when the countdown reaches zero"""
        dec = S.CallExpr(pos, "max", [self.bin("-", self.name(n, pos), self.name(self.dt_name, pos), pos), self.flt(0.0, pos)])
        body: list[S.Stmt] = [self.assign(n, dec, pos)]
        if fired:
            body.append(S.IfStmt(pos, self.bin("<=", self.name(n, pos), self.flt(0.0, pos), pos),
                                 [S.CallStmt(pos, m, []) for m in fired], []))
        return S.IfStmt(pos, self.bin(">", self.name(n, pos), self.flt(0.0, pos), pos), body, [])

    # ---- window / buffer -----------------------------------------------------------------------------------------------------
    def lower_window(self, d: S.WindowDecl) -> list[S.Decl]:
        out: list[S.Decl] = [S.StateDecl(d.pos, d.name, "bool", d.pos, None, name_pos=d.name_pos)]
        if d.notify:
            out.append(S.HandlerDecl(d.pos, "notify_begin", d.notify, d.notify_pos, None, [self.assign(d.name, self.bool_(True, d.pos), d.pos)]))
            out.append(S.HandlerDecl(d.pos, "notify_end", d.notify, d.notify_pos, None, [self.assign(d.name, self.bool_(False, d.pos), d.pos)]))
        for m, mpos in d.closes_on:
            out.append(S.HandlerDecl(d.pos, "montage_ended", m, mpos, None, [self.assign(d.name, self.bool_(False, d.pos), d.pos)]))
        return out

    def lower_buffer(self, d: S.BufferDecl) -> list[S.Decl]:
        out: list[S.Decl] = [S.StateDecl(d.pos, d.name, "float", d.pos, None, name_pos=d.name_pos)]
        for t in d.triggers:
            fill: S.Stmt = self.assign(d.name, d.duration, d.pos)
            body: list[S.Stmt] = [S.IfStmt(d.pos, d.when, [fill], [])] if d.when is not None else [fill]
            out.append(S.HandlerDecl(d.pos, "input", t.action, t.pos, t.phase, body))
        return out

    # ---- combo -------------------------------------------------------------------------------------------------------------------
    def lower_combo(self, c: S.ComboDecl) -> list[S.Decl]:
        pos = c.pos
        enum_name = c.name + "Step"
        time_name = c.name + "StepTime"
        step_names = [st.name for st in c.steps]
        for i, st in enumerate(c.steps):
            if st.name == "Idle":
                self.err("DAL3052", "a combo step cannot be called 'Idle' (that is the state between combos)", st.pos)
            if st.name in step_names[:i]:
                self.err("DAL3001", f"step '{st.name}' is declared twice in combo {c.name}", st.pos)
        known = set(step_names) | {"Idle"}
        uses_time = any(st.after for st in c.steps)

        out: list[S.Decl] = [
            S.EnumDecl(pos, enum_name, [("Idle", pos)] + [(st.name, st.pos) for st in c.steps], name_pos=c.name_pos),
            S.StateDecl(pos, c.name, enum_name, pos, self.member(enum_name, "Idle", pos), name_pos=c.name_pos),
        ]
        if uses_time:
            out.append(S.StateDecl(pos, time_name, "float", pos, None, name_pos=c.name_pos))
            self.pre.append(S.IfStmt(pos, self.bin("!=", self.name(c.name, pos), self.member(enum_name, "Idle", pos), pos),
                                     [self.assign(time_name, self.bin("+", self.name(time_name, pos), self.name(self.dt_name, pos), pos), pos)], []))

        steps = {st.name: st for st in c.steps}

        def is_buffer(n: str | None) -> bool:
            return n is not None and self.kinds.get(n) == "buffer"

        def cond_of(rule: S.ComboRule) -> S.Expr:
            parts: list[S.Expr] = []
            if rule.trigger is not None:
                if not is_buffer(rule.trigger):
                    self.err("DAL3053", f"'{rule.trigger}' is not a buffer (rules react to 'on <buffer>' or 'when <condition>')", rule.trigger_pos or rule.pos)
                parts.append(self.bin(">", self.name(rule.trigger, rule.pos), self.flt(0.0, rule.pos), rule.pos))
            if rule.within is not None:
                parts.append(self.name(rule.within, rule.within_pos or rule.pos))
            if rule.when is not None:
                parts.append(rule.when)
            if not parts:
                self.err("DAL3054", "a combo rule needs 'on <buffer>' or 'when <condition>'", rule.pos)
                return self.bool_(False, rule.pos)
            cond = parts[0]
            for p in parts[1:]:
                cond = self.bin("&&", cond, p, rule.pos)
            return cond

        def goto(current: S.ComboStep | None, target: str, target_pos: Pos, consume: str | None) -> list[S.Stmt]:
            if target not in known:
                self.err("DAL3055", f"combo {c.name} has no step '{target}'", target_pos)
                return []
            stmts: list[S.Stmt] = []
            if current is not None:
                stmts.extend(current.exit)
            if consume is not None:
                stmts.append(self.assign(consume, self.flt(0.0, pos), pos))
            stmts.append(self.assign(c.name, self.member(enum_name, target, target_pos), pos))
            if uses_time:
                stmts.append(self.assign(time_name, self.flt(0.0, pos), pos))
            if target != "Idle":
                nxt = steps[target]
                if nxt.play is not None:
                    pl = nxt.play
                    args: list[S.Expr] = [self.name(pl.montage, pl.montage_pos)]
                    if pl.rate is not None or pl.section is not None:
                        args.append(pl.rate if pl.rate is not None else self.flt(1.0, pos))
                    if pl.section is not None:
                        args.append(pl.section)
                    stmts.append(S.CmdStmt(pos, "montage_play", args, pos))
                stmts.extend(nxt.enter)
            return stmts

        def chain(rules: list[tuple[S.Expr, list[S.Stmt]]]) -> list[S.Stmt]:
            """if (c1) {..} else if (c2) {..} ..."""
            tail: list[S.Stmt] = []
            for cond, body in reversed(rules):
                tail = [S.IfStmt(pos, cond, body, tail)]
            return tail

        # per-state rule chains
        state_chains: list[tuple[S.Expr, list[S.Stmt]]] = []
        idle_rules = [(cond_of(r), goto(None, r.target, r.target_pos, r.trigger if is_buffer(r.trigger) else None)) for r in c.starts]
        if idle_rules:
            state_chains.append((self.bin("==", self.name(c.name, pos), self.member(enum_name, "Idle", pos), pos), chain(idle_rules)))
        for st in c.steps:
            rules = [(cond_of(r), goto(st, r.target, r.target_pos, r.trigger if is_buffer(r.trigger) else None)) for r in st.rules]
            for seconds, target, tpos in st.after:
                rules.append((self.bin(">=", self.name(time_name, pos), seconds, pos), goto(st, target, tpos, None)))
            if rules:
                state_chains.append((self.bin("==", self.name(c.name, pos), self.member(enum_name, st.name, st.pos), pos), chain(rules)))
        main = chain(state_chains)

        if c.reset_when is not None:
            reset_body: list[S.Stmt] = chain([
                (self.bin("==", self.name(c.name, pos), self.member(enum_name, st.name, st.pos), pos), goto(st, "Idle", pos, None))
                for st in c.steps
            ])
            guarded = self.bin("&&", self.bin("!=", self.name(c.name, pos), self.member(enum_name, "Idle", pos), pos), c.reset_when, pos)
            self.prefix.append(S.IfStmt(pos, guarded, reset_body, main))
        else:
            self.prefix.extend(main)

        # a step ends when its montage ends on its own (an interruption by the next step changes the state first)
        by_montage: dict[str, list[S.ComboStep]] = {}
        for st in c.steps:
            if st.play is not None:
                by_montage.setdefault(st.play.montage, []).append(st)
        for m, group in by_montage.items():
            rules = [
                (self.bin("&&", self.bin("==", self.name(c.name, pos), self.member(enum_name, st.name, st.pos), pos),
                          S.UnaryExpr(pos, "!", self.name("interrupted", pos)), pos),
                 goto(st, "Idle", pos, None))
                for st in group
            ]
            out.append(S.HandlerDecl(pos, "montage_ended", m, group[0].play.montage_pos, None, chain(rules)))
        return out

    # ---- rewriting of timer / window / buffer uses ------------------------------------------------------------------------------------
    TIMER_MEMBERS = ("active", "ready", "remaining")
    BUFFER_MEMBERS = ("pending", "empty", "remaining")

    def rewrite_decl(self, d: S.Decl) -> S.Decl:
        if isinstance(d, (S.FnDecl, S.MethodDecl, S.PostUpdateDecl)):
            return replace(d, body=self.rewrite_stmts(d.body))
        if isinstance(d, S.EntryDecl):
            return replace(d, body=self.rewrite_stmts(d.body))
        if isinstance(d, S.HandlerDecl):
            return replace(d, body=self.rewrite_stmts(d.body))
        return d

    def rewrite_stmts(self, stmts: list[S.Stmt]) -> list[S.Stmt]:
        out: list[S.Stmt] = []
        for s in stmts:
            r = self.rewrite_stmt(s)
            if isinstance(r, list):
                out.extend(r)
            else:
                out.append(r)
        return out

    def rewrite_stmt(self, s: S.Stmt):
        if isinstance(s, S.CallStmt):
            return self.rewrite_call_stmt(s)
        if isinstance(s, S.LetStmt):
            return replace(s, init=self.rewrite_expr(s.init))
        if isinstance(s, (S.AssignStmt, S.MemberAssignStmt)):
            return replace(s, value=self.rewrite_expr(s.value))
        if isinstance(s, S.ExprStmt):
            return replace(s, expr=self.rewrite_expr(s.expr))
        if isinstance(s, S.IfStmt):
            return replace(s, cond=self.rewrite_expr(s.cond), then=self.rewrite_stmts(s.then), orelse=self.rewrite_stmts(s.orelse))
        if isinstance(s, S.ForStmt):
            return replace(s, lo=self.rewrite_expr(s.lo), hi=self.rewrite_expr(s.hi), body=self.rewrite_stmts(s.body))
        if isinstance(s, S.WhileStmt):
            return replace(s, cond=self.rewrite_expr(s.cond), bound=self.rewrite_expr(s.bound), body=self.rewrite_stmts(s.body))
        if isinstance(s, S.ReturnStmt) and s.value is not None:
            return replace(s, value=self.rewrite_expr(s.value))
        if isinstance(s, S.CmdStmt):
            return replace(s, args=[self.rewrite_expr(a) for a in s.args])
        return s

    def rewrite_call_stmt(self, s: S.CallStmt) -> list[S.Stmt]:
        pos = s.pos
        want = {"start": ("timer", "buffer"), "stop": ("timer", "buffer"), "open": ("window",), "close": ("window",), "consume": ("buffer",)}
        if s.callee not in want:
            # a call of a method (the checker knows whether there is one)
            return [replace(s, args=[self.rewrite_expr(a) for a in s.args])]
        if not s.args or not isinstance(s.args[0], S.NameRef) or self.kinds.get(s.args[0].name) not in want[s.callee]:
            what = " or ".join(want[s.callee])
            self.err("DAL3056", f"{s.callee}() takes a {what} as its first argument", pos)
            return []
        n = s.args[0].name
        if s.callee == "start":
            if len(s.args) > 2:
                self.err("DAL4041", "start() takes a timer and an optional duration", pos)
                return []
            if len(s.args) == 2:
                value = self.rewrite_expr(s.args[1])
            elif self.timer_default.get(n) is not None:
                value = self.rewrite_expr(self.timer_default[n])  # type: ignore[arg-type]
            else:
                self.err("DAL3057", f"timer '{n}' has no default duration: write start({n}, seconds) or declare 'timer {n} = seconds;'", pos)
                return []
            return [self.assign(n, value, pos)]
        if s.callee in ("stop", "consume"):
            return [self.assign(n, self.flt(0.0, pos), pos)]
        return [self.assign(n, self.bool_(s.callee == "open", pos), pos)]

    def rewrite_expr(self, e: S.Expr) -> S.Expr:
        if isinstance(e, S.MemberRef):
            if isinstance(e.base, S.NameRef) and e.base.name in self.kinds and self.kinds[e.base.name] in ("timer", "buffer"):
                n, kind, pos = e.base.name, self.kinds[e.base.name], e.pos
                allowed = self.TIMER_MEMBERS if kind == "timer" else self.BUFFER_MEMBERS
                if e.field in ("active", "pending"):
                    return self.bin(">", self.name(n, pos), self.flt(0.0, pos), pos)
                if e.field in ("ready", "empty"):
                    return self.bin("<=", self.name(n, pos), self.flt(0.0, pos), pos)
                if e.field == "remaining":
                    return self.name(n, pos)
                self.err("DAL3058", f"a {kind} has no member '{e.field}' (members: {', '.join(allowed)})", e.field_pos)
                return e
            return replace(e, base=self.rewrite_expr(e.base))
        if isinstance(e, S.UnaryExpr):
            return replace(e, operand=self.rewrite_expr(e.operand))
        if isinstance(e, S.BinaryExpr):
            return replace(e, left=self.rewrite_expr(e.left), right=self.rewrite_expr(e.right))
        if isinstance(e, S.CondExpr):
            return replace(e, cond=self.rewrite_expr(e.cond), then=self.rewrite_expr(e.then), orelse=self.rewrite_expr(e.orelse))
        if isinstance(e, S.CallExpr):
            return replace(e, args=[self.rewrite_expr(a) for a in e.args])
        if isinstance(e, S.HostCallExpr):
            return replace(e, args=[self.rewrite_expr(a) for a in e.args])
        if isinstance(e, S.MemberCallExpr):
            return replace(e, base=self.rewrite_expr(e.base), args=[self.rewrite_expr(a) for a in e.args])
        return e


def lower(unit: S.SourceUnit, source: SourceFile) -> S.SourceUnit:
    return Lowerer(unit, source).run()
