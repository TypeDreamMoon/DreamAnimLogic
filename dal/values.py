"""Runtime values of the v3 types: structs, settings, tags.

The checker folds constants with them, the interpreter runs on them, and the settings loader builds them.  The
generated Python classes (``pybackend``) subclass the same ``dal.runtime.Struct`` base, so a value made by the
interpreter and one made by generated code can be told apart only by their class.
"""

from __future__ import annotations

from . import runtime as rt
from .types import (
    BOOL,
    FLOAT,
    INT,
    QUAT,
    ROTATOR,
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

    def __init__(self, **kw):
        for f in ty.fields:
            setattr(self, f.name, kw[f.name] if f.name in kw else member_default(f))

    ns = {"__slots__": names, "_FIELDS": names, "_TYPE": ty, "_TYPE_NAME": ty.name, "_IMMUTABLE": ty.is_settings,
          "__init__": __init__}
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


def coerce(ty: Type, v):
    """Convert a host-supplied or user-supplied value to the runtime representation of ``ty``."""
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
    if isinstance(ty, TagType):
        return "" if v is None else str(v)
    if isinstance(ty, StructType):
        if isinstance(v, rt.Struct) and v._TYPE_NAME == ty.name:
            return copy_value(v)
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
