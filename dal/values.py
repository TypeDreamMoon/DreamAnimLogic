"""Runtime values of the v3 types: structs, settings, tags.

The checker folds constants with them, the interpreter runs on them, and the settings loader builds them.  The
generated Python classes (``pybackend``) subclass the same ``dal.runtime.Struct`` base, so a value made by the
interpreter and one made by generated code can be told apart only by their class.
"""

from __future__ import annotations

from . import runtime as rt
from .types import (
    ArrayType,
    AssetType,
    MapType,
    BOOL,
    DOUBLE_TO_SINGLE,
    FLOAT,
    HIT,
    INT,
    QUAT,
    ROTATOR,
    SPRING,
    SPRING3,
    TRANSFORM,
    VEC2,
    VEC3,
    EnumType,
    StructType,
    TagType,
    Type,
)


def struct_class(ty: StructType) -> type:
    """The Python class that carries values of ``ty`` in the interpreter (created once per type)."""
    cls = ty.__dict__.get("_pyclass")
    if cls is not None:
        return cls
    names = tuple(f.name for f in ty.fields)

    derived = [f for f in ty.fields if f.derived]

    def _derive(self):
        """Compute the ``derived`` members of a settings value from the others."""
        from .interp import Evaluator

        env = {f.name: getattr(self, f.name) for f in ty.fields if not f.derived}
        ev = Evaluator()
        for f in derived:
            if f.expr is None:                # not checked yet: the checker rebuilds the defaults once it is
                continue
            v = ev.ev(f.expr, env)
            setattr(self, f.name, v)
            env[f.name] = v

    def __init__(self, **kw):
        for f in ty.fields:
            setattr(self, f.name, kw[f.name] if f.name in kw else member_default(f))
        if derived:
            self._derive()

    ns = {"__slots__": names, "_FIELDS": names, "_TYPE": ty, "_TYPE_NAME": ty.name, "_IMMUTABLE": ty.is_settings,
          "_UNFLAT": tuple(f.name for f in ty.fields if ty.is_settings and (isinstance(f.ty, (MapType, ArrayType)) or (isinstance(f.ty, AssetType) and f.ty.settings_only))),
          "__init__": __init__, "_derive": _derive}
    cls = type(ty.name, (rt.Struct,), ns)
    ty._pyclass = cls
    return cls


def member_default(f):
    """A fresh default for one member of a struct."""
    if f.default is None:
        return default_of(f.ty) if f.ty.is_struct else None
    return copy_value(f.default)


def default_of(ty: Type):
    """A fresh default value of a type (the declared member defaults for a struct)."""
    from .types import zero_value

    if isinstance(ty, StructType):
        return struct_class(ty)()
    return zero_value(ty)


def copy_value(v):
    """Copy a value for storing: mutable structs are copied, everything else is shared."""
    if isinstance(v, rt.Struct):
        return v if v._IMMUTABLE else v.copy()
    return v


def new_struct(ty: StructType, members: dict):
    """A struct value with the given members (the rest keep their declared defaults)."""
    return struct_class(ty)(**members)


# float32 mode: how a value of a float type is rounded (the double types are never rounded)
ROUND32 = {FLOAT: rt.f32, VEC2: rt.f32_vec2, VEC3: rt.f32_vec3, ROTATOR: rt.f32_rotator, QUAT: rt.f32_quat, TRANSFORM: rt.f32_transform,
           SPRING: rt.f32_spring, SPRING3: rt.f32_spring3, HIT: rt.f32_hit}


def round32(ty: Type, v):
    """``v`` (a value of type ``ty``) with every float in it rounded to float32: scalars and vectors as they are, a struct member by member (its derived members
    recomputed), the tables and lists of a settings value element by element.  The double types are left alone."""
    fn = ROUND32.get(ty)
    if fn is not None:
        return fn(v)
    if isinstance(ty, StructType) and isinstance(v, rt.Struct):
        changed = False
        for f in ty.fields:
            if f.derived:
                continue
            cur = getattr(v, f.name)
            new = round32(f.ty, cur)
            if new is not cur and new != cur:
                setattr(v, f.name, new)
                changed = True
        if changed and hasattr(v, "_derive"):
            v._derive()
        if any(f.derived for f in ty.fields):
            for f in ty.fields:
                if f.derived:
                    setattr(v, f.name, round32(f.ty, getattr(v, f.name)))
        return v
    if isinstance(ty, MapType) and isinstance(v, dict):
        return {k: round32(ty.value, x) for k, x in v.items()}
    if isinstance(ty, ArrayType) and isinstance(v, tuple):
        return tuple(round32(ty.elem, x) for x in v)
    return v


def coerce(ty: Type, v):
    """Convert a host-supplied or user-supplied value to the runtime representation of ``ty``."""
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
    if ty is TRANSFORM:
        return rt.as_transform(v)
    if isinstance(ty, EnumType):
        if isinstance(v, str):
            name = v.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
            if name not in ty.members:
                raise ValueError(f"'{v}' is not a member of {ty.name} ({', '.join(ty.members)})")
            return ty.members.index(name)
        return int(v)
    if isinstance(ty, TagType):
        return "" if v is None else str(v)
    if isinstance(ty, AssetType):
        return v if (v is None or isinstance(v, str) or not isinstance(v, (int, float, bool))) else str(v)
    if isinstance(ty, (MapType, ArrayType)):
        return v
    if isinstance(ty, StructType):
        if isinstance(v, rt.Struct) and v._TYPE_NAME == ty.name:
            return copy_value(v)
        if ty.is_settings and not isinstance(v, rt.Struct):
            from . import settings

            return settings.load_ir(ty, v)
        if isinstance(v, rt.Struct):
            items = {n: getattr(v, n) for n in v._FIELDS}
        elif isinstance(v, dict):
            items = v
        elif v is None:
            return default_of(ty)
        else:
            raise TypeError(f"cannot use {type(v).__name__} as {ty.name}")
        kw = {}
        for f in ty.fields:
            if f.name in items:
                kw[f.name] = coerce(f.ty, items[f.name])
        return struct_class(ty)(**kw)
    return v


def flatten(prefix: str, value, out: dict) -> None:
    """Leaves of a value as ``{path: plain value}``; struct members are joined with '.'."""
    if isinstance(value, rt.Struct):
        for n in value._FIELDS:
            flatten(f"{prefix}.{n}", getattr(value, n), out)
    else:
        out[prefix] = rt.to_plain(value)
