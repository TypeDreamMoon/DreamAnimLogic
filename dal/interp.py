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
from .builtins import BUILTINS, CONSTRUCTORS
from .types import BOOL, FLOAT, INT, QUAT, ROTATOR, VEC2, VEC3, EnumType, Type, zero_value


class InterpError(Exception):
    pass


class _Return(Exception):
    pass


_COMPS = {"x": 0, "y": 1, "z": 2, "w": 3, "pitch": 0, "yaw": 1, "roll": 2}


def coerce(ty: Type, v):
    """Coerce a host- or user-supplied value to the runtime representation of ``ty``."""
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
    if isinstance(ty, EnumType):
        return int(v)
    raise InterpError(f"cannot coerce to {ty}")


class Evaluator:
    """Expression/statement evaluator.  ``env`` maps local names; ``owner`` supplies inputs/states/params."""

    def __init__(self, owner=None, externs: dict[str, Callable] | None = None, funcs: dict[str, ir.FnDef] | None = None):
        self.owner = owner
        self.externs = externs or {}
        self.funcs = funcs or {}

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
            if ta is QUAT:
                return rt.quat_mul(a, b)
            return a * b
        if op == "/":
            if ta is INT and tb is INT:
                return rt.idiv(a, b)
            if ta is FLOAT and tb is FLOAT:
                return rt.fdiv(a, b)
            return a / b  # vector / scalar (zero-safe in Vec*)
        if op == "%":
            return rt.imod(a, b)
        raise InterpError(f"unknown operator {op}")

    def member(self, e: ir.Member, env: dict):
        v = self.ev(e.x, env)
        comps = e.comps
        if len(comps) == 1 or comps in ("pitch", "yaw", "roll"):
            return v[_COMPS[comps]]
        picked = [v[_COMPS[c]] for c in comps]
        return rt.Vec2(*picked) if len(picked) == 2 else rt.Vec3(*picked)

    # ---- statements ----------------------------------------------------------------
    def run_block(self, stmts: list[ir.Stmt], env: dict):
        for s in stmts:
            k = type(s)
            if k is ir.Let:
                env[s.name] = self.ev(s.init, env)
            elif k is ir.Assign:
                v = self.ev(s.value, env)
                if s.kind == "local":
                    env[s.name] = v
                else:
                    self.owner.values[s.name] = v
            elif k is ir.If:
                if self.ev(s.cond, env):
                    self.run_block(s.then, env)
                else:
                    self.run_block(s.orelse, env)
            elif k is ir.Return:
                raise _ReturnValue(None if s.value is None else self.ev(s.value, env))

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


def eval_const(expr: ir.Expr):
    """Evaluate a constant expression (no inputs/state/params/locals/functions)."""
    return Evaluator().ev(expr, {})


class Interpreter:
    """Runs a checked module.  Mirrors the API of the generated Python class."""

    def __init__(self, module: ir.Module, host=None, externs: dict[str, Callable] | None = None, **params):
        self.module = module
        self.host = host
        self.overrides: dict[str, object] = {}
        self.values: dict[str, object] = {}
        self._dt = 0.0
        self._params = {}
        for p in module.params:
            self._params[p.name] = p.default
        for name, val in params.items():
            p = next((x for x in module.params if x.name == name), None)
            if p is None:
                raise InterpError(f"unknown param {name!r}")
            self._params[name] = coerce(p.ty, val)
        self.ev = Evaluator(self, externs, {f.name: f for f in module.funcs})
        self.reset()

    def reset(self) -> None:
        self.values.clear()
        self.values.update(self._params)
        for i in self.module.inputs:
            self.values[i.name] = i.default
        for s in self.module.states:
            self.values[s.name] = s.default
        self._dt = 0.0

    # the generated class names these the same way
    def set_input(self, name: str, value) -> None:
        d = self.module.find_input(name)
        if d is None or d.host is not None:
            raise InterpError(f"{name!r} is not a free input")
        self.values[name] = coerce(d.ty, value)

    def sample_inputs(self) -> None:
        h = self.host
        for i in self.module.inputs:
            if i.host is None:
                continue
            if i.name in self.overrides:
                self.values[i.name] = coerce(i.ty, self.overrides[i.name])
                continue
            if h is None:
                continue
            item = i.host.item
            if item == "action":
                raw = h.action(i.host.arg, i.ty.name)
            elif item == "notify":
                raw = h.notify(i.host.arg)
            else:
                raw = getattr(h, item)
            self.values[i.name] = coerce(i.ty, raw)

    def update(self, dt: float) -> None:
        self.sample_inputs()
        self._dt = float(dt)
        u = self.module.update
        if u is not None:
            try:
                self.ev.run_block(u.body, {u.dt_name: self._dt})
            except _ReturnValue:
                pass

    def call_entry(self, name: str) -> None:
        for e in self.module.entries:
            if e.name == name:
                try:
                    self.ev.run_block(e.body, {e.dt_name: self._dt})
                except _ReturnValue:
                    pass
                return
        raise InterpError(f"no entry {name!r}")

    def snapshot(self) -> dict[str, object]:
        out: dict[str, object] = {}
        for i in self.module.inputs:
            out[i.name] = rt.to_plain(self.values[i.name])
        for s in self.module.states:
            out[s.name] = rt.to_plain(self.values[s.name])
        return out
