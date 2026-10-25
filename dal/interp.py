"""Reference interpreter over the IR.

This is the oracle for the Python backend: it walks the IR directly and shares
nothing with the generated code except the value types and the math library in
``dal.runtime`` (which have their own unit tests).  Use it to debug a program or
to compare against the generated Python class frame by frame
(``dal.harness``).
"""

from __future__ import annotations

from typing import Callable

from . import ir
from . import runtime as rt
from . import values as vals
from .builtins import BUILTINS, CONSTRUCTORS
from .hostapi import COMMANDS, HOOK_EVENTS, HOST_ITEMS, HostV1
from .types import (BOOL, DOUBLE, DOUBLE_TO_SINGLE, FLOAT, HIT, HIT_MEMBERS, SPRING_MEMBERS, TRANSFORM, TRANSFORMD, INT, MOVEMENT_MODE, QUAT, QUATD, ROTATOR, VEC2, VEC3, VOID, SPRING,
                    ArrayType, AssetType, EnumType, MapType, StructType, TagType, Type, zero_value)


class InterpError(Exception):
    pass


class _Return(Exception):
    pass


_COMPS = {"x": 0, "y": 1, "z": 2, "w": 3, "pitch": 0, "yaw": 1, "roll": 2}


def coerce(ty: Type, v):
    """Coerce a host- or user-supplied value to the runtime representation of ``ty``."""
    ty = DOUBLE_TO_SINGLE.get(ty, ty)              # a double is a Python float / the same vector class as its float counterpart
    if ty is FLOAT:
        return float(v)
    if ty is INT:
        return int(v)
    if ty is BOOL:
        return bool(v)
    if ty is VEC2:
        return rt.as_vec2(v)
    if ty is VEC3:
        return rt.as_vec3(v)
    if ty is ROTATOR:
        return rt.as_rotator(v)
    if ty is QUAT:
        return rt.as_quat(v)
    if ty is HIT:
        return rt.as_hit(v)
    if ty in SPRING_MEMBERS:
        return rt.as_spring(v) if ty is SPRING else rt.as_spring3(v)
    if ty is TRANSFORM:
        return rt.as_transform(v)
    if isinstance(ty, (StructType, TagType, EnumType, AssetType)):
        return vals.coerce(ty, v)
    raise InterpError(f"cannot coerce to {ty}")


def command_arg(expr: ir.Expr, value):
    """What the host receives for one command argument: names for montages and sections, plain runtime values otherwise."""
    if isinstance(expr, ir.MontageRef):
        return expr.name
    ty = expr.ty
    if isinstance(ty, EnumType):
        if ty.builtin:
            return getattr(rt, ty.name)(int(value))                        # MovementMode, MontageBlendMode ...
        return None if int(value) == 0 else ty.members[int(value)]       # a montage section (0 is 'None')
    return value


class Evaluator:
    """Expression/statement evaluator.  ``env`` maps local names; ``owner`` supplies inputs/states/params."""

    f32 = False                       # float32 mode (Evaluator32)
    folding = False                   # evaluating a constant at compile time: a float widened to a double is its float32 value there (the program is not running, nothing rounded it yet)

    def __init__(self, owner=None, externs: dict[str, Callable] | None = None, funcs: dict[str, ir.FnDef] | None = None,
                 methods: dict[str, ir.MethodDef] | None = None):
        self.owner = owner
        self.externs = externs or {}
        self.funcs = funcs or {}
        self.methods = methods or {}

    # ---- expressions ----------------------------------------------------------
    def ev(self, e: ir.Expr, env: dict):
        k = type(e)
        if k is ir.Lit:
            return e.value
        if k is ir.EnumLit:
            return e.ty.index(e.member)  # type: ignore[attr-defined]
        if k is ir.ConstRef:
            return e.value
        if k is ir.LocalRef:
            return env[e.name]
        if k is ir.InputRef or k is ir.StateRef or k is ir.ParamRef:
            if self.owner is None:
                raise InterpError(f"{e.name} is not available here")
            return self.owner.values[e.name]
        if k is ir.ToFloat:
            return float(self.ev(e.x, env))
        if k is ir.Convert:
            x = self.ev(e.x, env)
            if e.x.ty is INT:
                return float(x)
            return x if e.ty.is_double and not self.folding else rt.round32_any(x)     # widening keeps the value (in the float32 mode it is a float32 already); narrowing rounds to float32
        if k is ir.Unary:
            x = self.ev(e.x, env)
            if e.op == "!":
                return not x
            if e.ty is INT:
                return rt.i32(-x)
            return -x
        if k is ir.Binary:
            return self.binary(e, env)
        if k is ir.Cond:
            return self.ev(e.a, env) if self.ev(e.c, env) else self.ev(e.b, env)
        if k is ir.Member:
            return self.member(e, env)
        if k is ir.Make:
            args = [self.ev(a, env) for a in e.args]
            if e.ctor == "vec2":
                return rt.Vec2(*args)
            if e.ctor == "vec3":
                return rt.Vec3(args[0][0], args[0][1], args[1]) if e.sig_index == 1 else rt.Vec3(*args)
            if e.ctor == "rotator":
                return rt.Rotator(*args)
            if e.ctor == "quat":
                return rt.Quat(*args)
            if e.ctor == "transform":
                return rt.Transform(*args)
            raise InterpError(f"unknown constructor {e.ctor}")
        if k is ir.CallBuiltin:
            fn = getattr(rt, BUILTINS[e.name].py)
            return fn(*[self.ev(a, env) for a in e.args])
        if k is ir.CallFn:
            return self.call_fn(self.funcs[e.name], [self.ev(a, env) for a in e.args])
        if k is ir.CallExtern:
            fn = self.externs.get(e.name)
            if fn is None:
                raise InterpError(f"extern fn {e.name} has no stand-in")
            return coerce(e.ty, fn(*[self.ev(a, env) for a in e.args]))
        if k is ir.HostQuery:
            if self.owner is None:
                raise InterpError("host queries need a running logic")
            return self.owner.host_query(e, [self.ev(a, env) for a in e.args])
        if k is ir.Valid:
            return bool(self.ev(e.x, env))
        if k is ir.CurveEval:
            c, x = self.ev(e.curve, env), self.ev(e.x, env)
            return rt.curve_eval(c, x) if e.ty is FLOAT else rt.curve3_eval(c, x)
        if k is ir.Index:
            base, index = self.ev(e.base, env), self.ev(e.index, env)
            default = (lambda: vals.default_of(e.ty)) if isinstance(e.ty, StructType) else zero_value(e.ty)
            return rt.map_get(base, index, default) if isinstance(e.base.ty, MapType) else rt.array_get(base, index, default)
        if k is ir.MapHas:
            return self.ev(e.key, env) in self.ev(e.table, env)
        if k is ir.ArrayLen:
            return len(self.ev(e.items, env))
        if k is ir.CallMethod or k is ir.CallView:
            return self.call_method(self.methods[e.name], [self.ev(a, env) for a in e.args])
        if k is ir.CallPeer:
            if self.owner is None:
                raise InterpError("peer calls need a running logic")
            return self.owner.peer_call(e, [self.ev(a, env) for a in e.args])
        if k is ir.PeerValid:
            if self.owner is None:
                raise InterpError("peer queries need a running logic")
            return self.owner.peers.get(e.peer) is not None
        if k is ir.MakeStruct:
            return vals.new_struct(e.ty, {n: vals.copy_value(self.ev(v, env)) for n, v in e.fields})
        if k is ir.MontageRef:
            return e.name
        raise InterpError(f"cannot evaluate {k.__name__}")

    def binary(self, e: ir.Binary, env: dict):
        op = e.op
        if op == "&&":
            return bool(self.ev(e.a, env)) and bool(self.ev(e.b, env))
        if op == "||":
            return bool(self.ev(e.a, env)) or bool(self.ev(e.b, env))
        a = self.ev(e.a, env)
        b = self.ev(e.b, env)
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == ">":
            return a > b
        if op == ">=":
            return a >= b
        ta, tb = e.a.ty, e.b.ty
        if op == "+":
            return rt.i32(a + b) if ta is INT else a + b
        if op == "-":
            return rt.i32(a - b) if ta is INT else a - b
        if op == "*":
            if ta is INT and tb is INT:
                return rt.i32(a * b)
            if ta is QUAT or ta is QUATD:
                return rt.quat_mul(a, b)
            return a * b
        if op == "/":
            if ta is INT and tb is INT:
                return rt.idiv(a, b)
            if (ta is FLOAT or ta is DOUBLE) and (tb is FLOAT or tb is DOUBLE):
                return rt.fdiv(a, b)
            if e.ty.is_double:
                return rt.vdiv_recip(a, b)                      # vector / scalar as the engine does it: times the reciprocal
            return rt.vdiv_recip32(a, b) if self.f32 else a / b  # (zero-safe in Vec*)
        if op == "%":
            return rt.imod(a, b)
        raise InterpError(f"unknown operator {op}")

    def member(self, e: ir.Member, env: dict):
        v = self.ev(e.x, env)
        if isinstance(e.x.ty, StructType):
            return getattr(v, e.comps)
        comps = e.comps
        if comps in HIT_MEMBERS or e.x.ty in SPRING_MEMBERS or e.x.ty is TRANSFORM or e.x.ty is TRANSFORMD:
            return getattr(v, comps)
        if len(comps) == 1 or comps in ("pitch", "yaw", "roll"):
            return v[_COMPS[comps]]
        picked = [v[_COMPS[c]] for c in comps]
        return rt.Vec2(*picked) if len(picked) == 2 else rt.Vec3(*picked)

    # ---- statements ----------------------------------------------------------------
    def run_block(self, stmts: list[ir.Stmt], env: dict):
        for s in stmts:
            k = type(s)
            if k is ir.Let:
                env[s.name] = vals.copy_value(self.ev(s.init, env))
            elif k is ir.Assign:
                v = vals.copy_value(self.ev(s.value, env))
                if s.kind == "local":
                    env[s.name] = v
                else:
                    self.owner.values[s.name] = v
            elif k is ir.AssignField:
                v = vals.copy_value(self.ev(s.value, env))
                target = env[s.name] if s.kind == "local" else self.owner.values[s.name]
                for member in s.path[:-1]:
                    target = getattr(target, member)
                setattr(target, s.path[-1], v)
            elif k is ir.If:
                if self.ev(s.cond, env):
                    self.run_block(s.then, env)
                else:
                    self.run_block(s.orelse, env)
            elif k is ir.Return:
                raise _ReturnValue(None if s.value is None else vals.copy_value(self.ev(s.value, env)))
            elif k is ir.Command:
                implicit = [self.owner._source for _x in COMMANDS[s.name].implicit]      # host.montage_stop_source: the montage instance of the notify
                sent = s.args if s.written is None else s.args[:s.written]                  # a sparse command: only what the program wrote
                self.owner.emit_command(s.name, implicit + [command_arg(a, self.ev(a, env)) for a in sent])
            elif k is ir.ExprStmt:
                self.ev(s.expr, env)
            elif k is ir.For:
                for i in range(s.lo, s.hi):
                    env[s.var] = i
                    try:
                        self.run_block(s.body, env)
                    except _BreakLoop:
                        break
                env.pop(s.var, None)
            elif k is ir.While:
                for _run in range(s.bound):
                    if not self.ev(s.cond, env):
                        break
                    try:
                        self.run_block(s.body, env)
                    except _BreakLoop:
                        break
            elif k is ir.Break:
                raise _BreakLoop()

    def call_method(self, m: ir.MethodDef, args: list):
        """Run a method or a view; the result is None for a method without one."""
        env = {n: a for (n, _), a in zip(m.params, args)}
        try:
            self.run_block(m.body, env)
        except _ReturnValue as r:
            return r.value
        return None

    def call_fn(self, fn: ir.FnDef, args: list):
        env = {n: a for (n, _), a in zip(fn.params, args)}
        try:
            self.run_block(fn.body, env)
        except _ReturnValue as r:
            return r.value
        raise InterpError(f"fn {fn.name} finished without returning")


class _ReturnValue(Exception):
    def __init__(self, value):
        self.value = value


class _BreakLoop(Exception):
    """`break;` -- caught by the innermost loop."""


# the built-ins whose float result is exactly representable as float32 when the arguments are (selection, rounding to an integer, negation of the sign bit ...):
# the float32 mode does not round them again
EXACT_BUILTINS = frozenset({"abs", "sign", "min", "max", "clamp", "saturate", "floor", "ceil", "round", "trunc", "frac", "to_int", "floor_to_int", "ceil_to_int",
                            "round_to_int", "fmod", "is_nearly_zero", "is_nearly_equal", "quat_inverse"})
# the node kinds that make a new float value (everything else passes one on): their float results are rounded in float32 mode
ROUNDED_KINDS = frozenset({ir.Lit, ir.ConstRef, ir.ToFloat, ir.Convert, ir.CurveEval, ir.HostQuery, ir.CallExtern})


class Evaluator32(Evaluator):
    """The evaluator of the float32 mode: a ``float`` is a float32 held in a Python float, and every node that makes a new one rounds it
    (``dal.runtime.f32``): arithmetic on floats is exactly C++ ``float`` arithmetic.  Library functions are computed in double and rounded once."""

    f32 = True

    def ev(self, e: ir.Expr, env: dict):
        v = Evaluator.ev(self, e, env)
        fn = vals.ROUND32.get(e.ty)
        if fn is None:
            if isinstance(e.ty, StructType) and type(e) in (ir.MakeStruct, ir.Lit, ir.ConstRef):
                return vals.round32(e.ty, v)                       # (the members the program does not write have their declared defaults)
            return v
        k = type(e)
        if k is ir.Binary:
            return fn(v) if e.op in ("+", "-", "*", "/") else v
        if k is ir.CallBuiltin:
            return v if e.name in EXACT_BUILTINS else fn(v)
        return fn(v) if k in ROUNDED_KINDS else v


def eval_const(expr: ir.Expr):
    """Evaluate a constant expression (no inputs/state/params/locals/functions)."""
    ev = Evaluator()
    ev.folding = True
    return ev.ev(expr, {})


class Interpreter:
    """Runs a checked module.  Mirrors the API of the generated Python class."""

    def __init__(self, module: ir.Module, host=None, externs: dict[str, Callable] | None = None, peers: dict | None = None,
                 resolver=None, float32: bool | None = None, **params):
        self.module = module
        self.float32 = rt.want_float32(float32)                                       # every float is a float32 (see Evaluator32)
        self.resolver = resolver                                           # where settings find their curves (dal.settings.AssetResolver)
        self.host = host if host is not None else HostV1()
        self.overrides: dict[str, object] = {}
        self.values: dict[str, object] = {}
        self.peers: dict[str, object] = dict(peers) if peers else {}       # name -> the other logic (an Interpreter or a generated class)
        self.unresolved: set[str] = set()                                  # peers that were used but not connected
        self._dt = 0.0
        self._pending: list[tuple] = []
        self._now: list[tuple] = []                      # sync_events: events raised while an event was being handled
        self._in_now = False
        self._cmds: list[tuple] = []
        self._peer_queue: list[tuple] = []
        self._begun = False
        self._source = None                                                # the montage instance that raised the notify being handled
        self._params = {}
        for p in module.params:
            self._params[p.name] = p.default
        for name, val in params.items():
            p = next((x for x in module.params if x.name == name), None)
            if p is None:
                raise InterpError(f"unknown param {name!r}")
            if isinstance(p.ty, StructType) and p.ty.is_settings and not isinstance(val, rt.Struct):
                from . import settings

                self._params[name] = self._round(p.ty, settings.load_ir(p.ty, val, self.resolver, f32=self.float32))
            else:
                self._params[name] = self._c(p.ty, val)
        if self.float32:
            self._params = {n: self._round(p.ty, v) for p in module.params for n, v in [(p.name, self._params[p.name])]}
        self.ev = (Evaluator32 if self.float32 else Evaluator)(self, externs, {f.name: f for f in module.funcs}, {x.name: x for x in module.methods})
        self.reset()

    def _round(self, ty: Type, v):
        """The value rounded to float32 in float32 mode (as it is otherwise)."""
        return vals.round32(ty, v) if self.float32 else v

    def _c(self, ty: Type, v):
        """``coerce`` for this interpreter's mode."""
        return self._round(ty, coerce(ty, v))

    def reset(self) -> None:
        self.values.clear()
        self.values.update(self._params)
        for i in self.module.inputs:
            self.values[i.name] = self._round(i.ty, vals.copy_value(i.default))
        for s in self.module.states:
            self.values[s.name] = self._round(s.ty, vals.copy_value(s.default))
        self._dt = 0.0
        self._pending = []
        self._cmds = []
        self._called = []
        self._peer_queue = []
        self._begun = False

    # ---- peers ------------------------------------------------------------------------------------
    def connect(self, name: str, other) -> None:
        self.peers[name] = other

    def dal_get(self, name: str):
        """Another logic reads a state, input or param of this one."""
        return self.values[name]

    def dal_call(self, method: str, *args):
        m = self.module.find_method(method)
        if m is None or not m.pub or m.is_view:
            raise InterpError(f"no pub method {method!r}")
        return self.ev.call_method(m, [self._c(t, a) for (_n, t), a in zip(m.params, args)])

    def dal_enqueue(self, method: str, args: tuple) -> None:
        """A queued call from an actor logic: it runs at the start of the next update."""
        self._peer_queue.append((method, tuple(args)))

    def peer_call(self, e: ir.CallPeer, args: list):
        other = self.peers.get(e.peer)
        if other is None:
            self.unresolved.add(e.peer)
            return None if e.ty is VOID else vals.default_of(e.ty) if isinstance(e.ty, StructType) else zero_value(e.ty)
        if e.queued:
            other.dal_enqueue(e.method, tuple(args))
            return None
        if self.module.flush_peers and not any(other is p for p in self._called):
            self._called.append(other)
        result = other.dal_call(e.method, *args)
        return None if e.ty is VOID else self._c(e.ty, result)

    def _drain_peer_calls(self) -> None:
        queue, self._peer_queue = self._peer_queue, []
        for method, args in queue:
            self.dal_call(method, *args)

    # ---- host traffic ------------------------------------------------------------------------------
    def emit_command(self, name: str, args: list) -> None:
        self._cmds.append((name, tuple(args)))

    def host_query(self, e: ir.HostQuery, args: list):
        fn = getattr(self.host, e.name)
        return self._c(e.ty, fn(*args))

    def post_event(self, kind: str, name: str, *args) -> None:
        self._pending.append((kind, name) + args)

    def receive_event(self, event: tuple) -> None:
        """`sync_events`: the host raised an event (a hook, a declared notify) and the logic handles it now, in the middle of whatever the host is
        doing: sample the inputs, run the handlers, hand the commands over.  An event raised while a handler's commands run waits for that handler."""
        self._now.append(tuple(event))
        if self._in_now:
            return
        self._in_now = True
        try:
            while self._now:
                ev = self._now.pop(0)
                self._ensure_begun()
                if self.module.kind == "actor":                       # (an animation logic does not sample: its update may be in flight on a worker thread)
                    self.sample_inputs("__event__")
                self._dispatch([ev])
                if not self.module.post_flush:
                    self.flush()
        finally:
            self._in_now = False

    def _collect_events(self) -> list[tuple]:
        events, self._pending = self._pending, []
        poll = getattr(self.host, "poll_events", None)
        if poll is not None:
            events = events + [tuple(ev) for ev in poll()]
        return events

    def flush(self) -> None:
        cmds, self._cmds = self._cmds, []
        for name, args in cmds:
            fn = getattr(self.host, name, None)
            if fn is not None:
                fn(*args)
                if name == "snapshot_pose":
                    self._took_snapshot(args[0])
        called, self._called = self._called, []           # `flush_peers`: what the calls of the block made the peers queue runs now, in the order they were called
        for other in called:
            other.dal_flush()

    def dal_flush(self) -> None:
        """Hand the queued commands to the host now (a logic with `flush_peers` does it for the peers it called)."""
        self.flush()

    def take_commands(self) -> list[tuple]:
        """The commands queued since the last flush (and forget them) -- for hosts that execute them themselves."""
        cmds, self._cmds = self._cmds, []
        for name, args in cmds:
            if name == "snapshot_pose":
                self._took_snapshot(args[0])
        called, self._called = self._called, []           # `flush_peers`: the commands the called peers queued follow
        for other in called:
            cmds = cmds + other.take_commands()
        return cmds

    def _took_snapshot(self, path: str) -> None:
        """host.snapshot_pose(State): once the host took the pose, the snapshot state holds its name (what valid() sees)."""
        head, *rest = path.split(".")
        if not rest:
            self.values[head] = path
            return
        target = self.values[head]
        for member in rest[:-1]:
            target = getattr(target, member)
        setattr(target, rest[-1], path)

    # the generated class names these the same way
    def set_input(self, name: str, value) -> None:
        d = self.module.find_input(name)
        if d is None or not d.is_free:
            raise InterpError(f"{name!r} is not a free input")
        self.values[name] = self._c(d.ty, value)

    def sample_inputs(self, phase: str | None = None) -> None:
        h = self.host
        consumed: dict = {}                      # consume-on-read groups: one call to the host per sample
        only = self.module.consume_slots()       # the tick slots that sample the consume-on-read inputs (None: all of them)
        for i in self.module.inputs:
            if i.peer is not None:
                if i.name in self.overrides:
                    self.values[i.name] = self._c(i.ty, self.overrides[i.name])
                    continue
                other = self.peers.get(i.peer.peer)
                if other is None:
                    self.unresolved.add(i.peer.peer)
                    self.values[i.name] = zero_value(i.ty)
                    continue
                v = other.dal_get(i.peer.path[0])
                for member in i.peer.path[1:]:
                    v = getattr(v, member)
                self.values[i.name] = self._c(i.ty, v)
                continue
            if i.host is None:
                continue
            if i.name in self.overrides:
                self.values[i.name] = self._c(i.ty, self.overrides[i.name])
                continue
            item = i.host.item
            spec = HOST_ITEMS[item]
            if spec.consume:
                if phase is not None and only is not None and phase not in only:
                    continue                     # a slot whose block does not read it leaves it to the one that does
                if spec.consume not in consumed:
                    consumed[spec.consume] = getattr(h, spec.consume)()
                raw = consumed[spec.consume][spec.part]
            elif item == "action":
                raw = h.action(i.host.arg, i.ty.name)
            elif item == "notify":
                raw = h.notify(i.host.arg)
            elif item == "montage_section":
                name = h.montage_section(i.host.arg)
                raw = i.ty.members.index(name) if name in i.ty.members else 0    # type: ignore[attr-defined]
            elif spec.arg_kind == "string" or item.startswith("montage_"):
                raw = getattr(h, item)(i.host.arg, *(() if i.host.arg2 is None else (i.host.arg2,)))
            else:
                raw = getattr(h, item)
            self.values[i.name] = self._c(i.ty, raw)

    # ---- events -------------------------------------------------------------------------------------
    def _dispatch(self, events: list[tuple]) -> None:
        for ev in events:
            kind = ev[0]
            if kind in HOOK_EVENTS:                          # the hooks of the character have no name: the payload follows the kind
                for h in self.module.handlers:
                    if h.event == kind:
                        env = {n: self._c(t, ev[i + 1] if i + 1 < len(ev) else zero_value(t)) for i, (n, t) in enumerate(h.params)}
                        self._run(h.body, env)
                continue
            name = ev[1]
            for h in self.module.handlers:
                if h.event != kind or h.name != name:
                    continue
                env: dict = {}
                if kind in ("montage_ended", "montage_blending_out"):
                    env["interrupted"] = bool(ev[2])
                elif kind == "input":
                    if h.phase != ev[2]:
                        continue
                    env["value"] = self._c(h.params[0][1], ev[3] if len(ev) > 3 else 0.0)
                elif h.params:                                    # a declared notify: the payload (its properties) and the montage instance
                    env[h.params[0][0]] = self._c(h.params[0][1], ev[2] if len(ev) > 2 else None)
                    self._source = ev[3] if len(ev) > 3 else None
                try:
                    self.ev.run_block(h.body, env)
                except _ReturnValue:
                    pass
                finally:
                    self._source = None

    # ---- running ---------------------------------------------------------------------------------------
    def update(self, dt: float) -> None:
        """Every tick slot of the logic, in order (one for a program without tick slots):
        sample inputs, collect events, tick timers, run handlers, run the body, flush the commands."""
        slots = [u.phase for u in self.module.updates] or [self.module.default_phase]
        for phase in slots:
            if phase not in ir.HOOK_SLOTS:                       # a hook slot is run by the host, where it happens
                self.update_phase(phase, dt)

    def update_phase(self, phase: str, dt: float) -> None:
        """One tick slot.  The first slot of the logic also takes the queued peer calls, the events and the timers."""
        m = self.module
        u = next((x for x in m.updates if x.phase == phase), None)
        first = phase == (m.updates[0].phase if m.updates else m.default_phase)
        if u is None and not first:
            return
        self._ensure_begun()
        self.sample_inputs(phase)
        self._dt = rt.f32(dt) if self.float32 else float(dt)
        events: list[tuple] = []
        if first:
            self._drain_peer_calls()
            events = self._collect_events() if any(h.event not in ("begin_play", "end_play") for h in m.handlers) else []
            if u is not None:
                self._run(u.pre, {u.dt_name: self._dt})
            self._dispatch(events)
        elif m.slot_events and any(h.event not in ("begin_play", "end_play") for h in m.handlers):
            self._dispatch(self._collect_events())                 # an event raised since the slot before is handled in this one
        if u is not None:
            self._run(u.body, {u.dt_name: self._dt})
        if not m.post_flush:                                     # a post_flush logic hands its commands over after the post_update
            self.flush()

    def post_update(self) -> None:
        """The post_update block (anim logic: game thread, after the graph updated); the queued commands go to the host after it."""
        m = self.module
        if m.post_update is not None:
            self._run(m.post_update.body, {m.post_update.dt_name: self._dt})
        self.flush()

    def _run(self, body: list, env: dict) -> None:
        try:
            self.ev.run_block(body, env)
        except _ReturnValue:
            pass

    # ---- lifecycle -------------------------------------------------------------------------------------
    def _ensure_begun(self) -> None:
        if not self._begun:
            self.begin_play()

    def begin_play(self) -> None:
        """Run the `on begin_play` handlers once (update does it itself before the first frame when the host did not)."""
        if self._begun:
            return
        self._begun = True
        handlers = [h for h in self.module.handlers if h.event == "begin_play"]
        if handlers:
            self.sample_inputs()
            for h in handlers:
                self._run(h.body, {})
            self.flush()

    def end_play(self) -> None:
        handlers = [h for h in self.module.handlers if h.event == "end_play"]
        if handlers:
            self.sample_inputs()
            for h in handlers:
                self._run(h.body, {})
            self.flush()

    def call_entry(self, name: str, /, *args, **named) -> None:
        """Call an entry by name, with its parameters by position or by name (missing ones take their declared defaults)."""
        for e in self.module.entries:
            if e.name == name:
                env = self.bind_entry(e, args, named)
                if e.dt_name is not None:
                    env[e.dt_name] = self._dt
                try:
                    self.ev.run_block(e.body, env)
                except _ReturnValue:
                    pass
                if not self.module.post_flush:
                    self.flush()
                return
        if self.module.linked is not None:
            for e in self.module.linked.entries:
                if e.name == name:
                    parent = self.peers.get("Parent")
                    if parent is not None:
                        parent.call_entry(name, *args, **named)
                    return
        raise InterpError(f"no entry {name!r}")

    def bind_entry(self, e, args: tuple, named: dict) -> dict:
        """The locals of an entry call: the arguments, coerced to the parameter types, then the declared defaults."""
        names = [p.name for p in e.params]
        if len(args) > len(names):
            raise TypeError(f"entry {e.name} takes {len(names)} argument(s), {len(args)} given")
        given = dict(zip(names, args))
        for k, v in named.items():
            if k not in names:
                raise TypeError(f"entry {e.name} has no parameter {k!r} (parameters: {', '.join(names) or 'none'})")
            if k in given:
                raise TypeError(f"entry {e.name}: parameter {k!r} given twice")
            given[k] = v
        env: dict = {}
        for p in e.params:
            if p.name in given and not (given[p.name] is None and p.has_default and isinstance(p.ty, StructType)):
                env[p.name] = self._c(p.ty, given[p.name])        # (None for a struct stands for its declared default)
            elif p.has_default:
                env[p.name] = self._round(p.ty, vals.copy_value(p.default) if isinstance(p.default, rt.Struct) else p.default)
            else:
                raise TypeError(f"entry {e.name} needs the argument {p.name!r}")
        return env

    def snapshot(self) -> dict[str, object]:
        out: dict[str, object] = {}
        for i in self.module.inputs:
            rt.flatten_into(out, i.name, self.values[i.name])
        for s in self.module.states:
            rt.flatten_into(out, s.name, self.values[s.name])
        return out
