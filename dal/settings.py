"""Loading the data of ``settings`` types: JSON files, dicts, or nothing (the declared defaults).

A ``settings`` type is the DAL form of a data asset (``UAlsCharacterSettings``): nested members, curves, tag-keyed tables,
asset references.  ``load`` turns the data into instances of the classes of the interpreter or of a generated module;
``derived`` members are recomputed afterwards (the exported value, if any, is ignored).

Accepted input (the export of the ALS pack, and the spec's ``dreamved.dal.settings/1`` layout):

* the whole file ``{"$object": ..., "class": "/Script/ALS.AlsCharacterSettings", "props": {...}}`` or
  ``{"type": "AlsCharacterSettings", "values": {...}}``, or just the member dict;
* member names as written in C++ (``bAllowMantling``) or snake_case (``allow_mantling``); unknown members are reported in
  ``Report.warnings`` and ignored, missing ones keep their default;
* vectors as ``[x, y]`` / ``{"X": .., "Y": ..}``, rotators ``[pitch, yaw, roll]`` / ``{"Pitch": ..}``, quaternions ``[x, y, z, w]``;
* enums by member name (also ``EFoo::Member``), tags by full name, tag-keyed tables as objects, arrays as lists;
* nested settings / structs as objects (an ``Instanced`` sub-object ``{"$object", "class", "props"}`` is unwrapped);
* assets as a name, a path ``/Game/Dir/Name.Name`` or ``{"$asset": path, "class": ...}``; curves are resolved by name through an
  ``AssetResolver`` (``PackResolver`` reads ``<pack>/curves/<name>.curve.json``), or given inline.

Pure Python 3.11+; shared by the interpreter and the generated Python classes.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from . import runtime as rt
from .curve import Curve3, CurveError, RichCurve, curve_from_json


class SettingsError(ValueError):
    """The data does not fit the type; the message starts with the path of the offending member."""


@dataclass
class Report:
    warnings: list[str] = field(default_factory=list)       # unknown members, ignored values
    unresolved: list[str] = field(default_factory=list)     # assets / curves the resolver could not find

    def warn(self, text: str) -> None:
        self.warnings.append(text)


# ---- asset resolvers --------------------------------------------------------------------------------------------

def asset_name(v) -> str | None:
    """The name of an asset reference: 'A_Als_Stop', '/Game/Dir/A_Als_Stop.A_Als_Stop' and {"$asset": ...} all give 'A_Als_Stop'."""
    if v is None:
        return None
    if isinstance(v, dict):
        for k in ("$asset", "asset", "name", "path"):
            if isinstance(v.get(k), str):
                return asset_name(v[k])
        return None
    s = str(v)
    if not s or s.lower() in ("none", "null"):
        return None
    s = s.rsplit("/", 1)[-1]
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s


class AssetResolver:
    """Finds the assets that settings name.  The default knows nothing: curves stay unresolved, other assets keep their name."""

    def curve(self, name: str):
        """A ``RichCurve`` / ``Curve3`` for a curve asset name, or None."""
        return None

    def has_asset(self, kind: str, name: str) -> bool:
        return True


class PackResolver(AssetResolver):
    """Curves from ``<root>/curves/<name>.curve.json`` (or ``<name>.json``); other assets are taken on trust."""

    def __init__(self, root: str, curves: dict | None = None):
        self.root = root
        self.extra = dict(curves or {})
        self.cache: dict[str, object] = {}

    def curve(self, name: str):
        if name in self.cache:
            return self.cache[name]
        found = None
        if name in self.extra:
            found = self.extra[name]
            if isinstance(found, dict):
                found = curve_from_json(found, name)
        else:
            for fname in (f"{name}.curve.json", f"{name}.json"):
                path = os.path.join(self.root, "curves", fname)
                if os.path.isfile(path):
                    with open(path, "r", encoding="utf-8-sig") as f:
                        found = curve_from_json(json.load(f), name)
                    break
        self.cache[name] = found
        return found


# ---- schema: the types as plain data ---------------------------------------------------------------------------
#
# desc ::= "float" | "int" | "bool" | "vec2" | "vec3" | "rotator" | "quat" | "transform" | "curve" | "curve3" | "sequence" | "montage"
#        | "double" | "vec2d" | "vec3d" | "rotatord" | "quatd" | "transformd"
#        | ("enum", name, [members]) | ("tags", name, prefix, [members]) | ("struct", name) | ("settings", name)
#        | ("map", key desc, value desc) | ("array", element desc)
# schema ::= {type name: {"kind": "struct" | "settings", "fields": [(member, desc, derived)]}}

def desc_of(ty) -> object:
    from .types import ArrayType, AssetType, EnumType, MapType, StructType, TagType

    if isinstance(ty, EnumType):
        return ("enum", ty.name, list(ty.members))
    if isinstance(ty, TagType):
        return ("tags", ty.name, ty.prefix, list(ty.members))
    if isinstance(ty, StructType):
        return (ty.kind, ty.name)
    if isinstance(ty, MapType):
        return ("map", desc_of(ty.key), desc_of(ty.value))
    if isinstance(ty, ArrayType):
        return ("array", desc_of(ty.elem))
    if isinstance(ty, AssetType):
        return ty.name
    return ty.name


def collect_types(root) -> dict:
    """The struct / settings types reachable from ``root`` (a StructType), by name."""
    from .types import ArrayType, MapType, StructType

    out: dict = {}

    def visit(ty):
        if isinstance(ty, StructType):
            if ty.name in out:
                return
            out[ty.name] = ty
            for f in ty.fields:
                visit(f.ty)
        elif isinstance(ty, MapType):
            visit(ty.key)
            visit(ty.value)
        elif isinstance(ty, ArrayType):
            visit(ty.elem)

    visit(root)
    return out


def schema_of(types) -> dict:
    return {t.name: {"kind": t.kind, "fields": [(f.name, desc_of(f.ty), f.derived) for f in t.fields]} for t in types}


def _snake(name: str) -> str:
    s = name
    if len(s) > 1 and s[0] == "b" and s[1].isupper():
        s = s[1:]
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", s)
    return s.lower()


_ENUM_PREFIX = re.compile(r"^(.*::|EC[CR]_)")


# the double types read the same data as their float counterparts; in float32 mode only the float types round what they read
_DOUBLE_KINDS = {"double": "float", "vec2d": "vec2", "vec3d": "vec3", "rotatord": "rotator", "quatd": "quat", "transformd": "transform"}
_ROUND_KIND = {"float": rt.f32, "vec2": rt.f32_vec2, "vec3": rt.f32_vec3, "rotator": rt.f32_rotator, "quat": rt.f32_quat, "transform": rt.f32_transform}


class _Loader:
    def __init__(self, schema: dict, classes: dict, resolver: AssetResolver, report: Report, f32: bool = False):
        self.schema = schema
        self.classes = classes
        self.resolver = resolver
        self.report = report
        self.f32 = f32                                       # float32 mode: a float member is rounded to float32 as it is read

    # ---- structs ----------------------------------------------------------------------------------------------
    def build(self, name: str, data, path: str):
        info = self.schema[name]
        cls = self.classes[name]
        if data is None:
            return cls()
        data = self.unwrap(data, name, path)
        fields = info["fields"]
        by_key: dict[str, tuple] = {}
        for f in fields:
            by_key[f[0]] = f
            by_key.setdefault(_snake(f[0]), f)
        kw: dict = {}
        for key, v in data.items():
            if not isinstance(key, str):
                raise SettingsError(f"{path}: member names must be strings, not {key!r}")
            if key.startswith("$"):
                continue
            f = by_key.get(key)
            if f is None:
                self.report.warn(f"{path}.{key}: not a member of {name} (ignored)")
                continue
            if f[2]:
                continue                        # derived: recomputed from the other members
            kw[f[0]] = self.decode(f[1], v, f"{path}.{f[0]}")
        obj = cls(**kw)
        return obj

    def unwrap(self, data, name: str, path: str) -> dict:
        """The member dict of ``data`` (a whole file / an Instanced sub-object / just the members)."""
        if not isinstance(data, dict):
            raise SettingsError(f"{path}: expected an object for {name}, got {type(data).__name__}")
        declared = data.get("type") or data.get("class")
        if isinstance(declared, str) and ("props" in data or "values" in data):
            short = re.split(r"[./]", declared)[-1]
            if short.startswith("U") and short[1:] in self.schema and short not in self.schema:
                short = short[1:]
            if short != name and short.removeprefix("F") != name:
                raise SettingsError(f"{path}: the data is a {short}, expected {name}")
        for key in ("props", "values"):
            if isinstance(data.get(key), dict):
                return data[key]
        return data

    # ---- values -----------------------------------------------------------------------------------------------
    def decode(self, desc, v, path: str):
        if isinstance(desc, str):
            return self.decode_simple(desc, v, path)
        kind = desc[0]
        if kind == "enum":
            return self.decode_enum(desc, v, path)
        if kind == "tags":
            if v is None:
                return ""
            if not isinstance(v, str):
                raise SettingsError(f"{path}: expected a tag name, got {v!r}")
            return v
        if kind in ("struct", "settings"):
            if v is None:
                return self.classes[desc[1]]()
            return self.build(desc[1], v, path)
        if kind == "map":
            if v is None:
                return {}
            if not isinstance(v, dict):
                raise SettingsError(f"{path}: expected an object for a table, got {type(v).__name__}")
            return {self.decode_key(desc[1], k, f"{path}[{k!r}]"): self.decode(desc[2], x, f"{path}[{k!r}]") for k, x in v.items()}
        if kind == "array":
            if v is None:
                return ()
            if isinstance(v, dict) and isinstance(desc[1], tuple) and desc[1][:2] == ("enum", "Channel"):
                v = [k for k, x in v.items() if str(x).endswith("Block")]       # a collision response container: the channels that block
            if not isinstance(v, (list, tuple)):
                raise SettingsError(f"{path}: expected a list, got {type(v).__name__}")
            return tuple(self.decode(desc[1], x, f"{path}[{i}]") for i, x in enumerate(v))
        raise SettingsError(f"{path}: unknown type description {desc!r}")

    def decode_key(self, desc, k, path: str):
        if isinstance(desc, str) and desc == "int":
            try:
                return int(k)
            except (TypeError, ValueError):
                raise SettingsError(f"{path}: a table key must be an int, not {k!r}")
        if isinstance(desc, tuple) and desc[0] == "enum":
            return self.decode_enum(desc, k, path)
        if isinstance(desc, tuple) and desc[0] == "tags":
            return str(k)
        raise SettingsError(f"{path}: tables are keyed by tags, enums or ints")

    def decode_enum(self, desc, v, path: str) -> int:
        _k, name, members = desc
        if isinstance(v, bool) or v is None:
            raise SettingsError(f"{path}: expected a member of {name}, got {v!r}")
        if isinstance(v, int):
            if 0 <= v < len(members):
                return v
            raise SettingsError(f"{path}: {v} is not a member index of {name}")
        s = _ENUM_PREFIX.sub("", str(v))
        if s in members:
            return members.index(s)
        raise SettingsError(f"{path}: '{v}' is not a member of {name} ({', '.join(members)})")

    def decode_simple(self, kind: str, v, path: str):
        base = _DOUBLE_KINDS.get(kind, kind)
        out = self.decode_plain(base, v, path)
        if self.f32 and base is kind and kind in _ROUND_KIND:
            out = _ROUND_KIND[kind](out)
        return out

    def decode_plain(self, kind: str, v, path: str):
        if kind == "float":
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise SettingsError(f"{path}: expected a number, got {v!r}")
            return float(v)
        if kind == "int":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or (isinstance(v, float) and v != int(v)):
                raise SettingsError(f"{path}: expected an integer, got {v!r}")
            return int(v)
        if kind == "bool":
            if isinstance(v, bool):
                return v
            if v in (0, 1) and isinstance(v, int):
                return bool(v)
            raise SettingsError(f"{path}: expected true or false, got {v!r}")
        if kind in ("vec2", "vec3", "rotator", "quat"):
            return self.decode_vector(kind, v, path)
        if kind == "transform":
            return self.decode_transform(v, path)
        if kind in ("sequence", "montage"):
            name = asset_name(v)
            if name is not None and not self.resolver.has_asset(kind, name):
                self.report.unresolved.append(f"{path}: {kind} '{name}'")
            return name
        if kind in ("curve", "curve3"):
            return self.decode_curve(kind, v, path)
        raise SettingsError(f"{path}: unknown type {kind!r}")

    def decode_vector(self, kind: str, v, path: str):
        names = {"vec2": ("x", "y"), "vec3": ("x", "y", "z"), "rotator": ("pitch", "yaw", "roll"), "quat": ("x", "y", "z", "w")}[kind]
        if isinstance(v, dict):
            low = {str(k).lower(): x for k, x in v.items()}
            comps = []
            for n in names:
                if n not in low:
                    raise SettingsError(f"{path}: missing '{n}' in {kind}")
                comps.append(low[n])
        elif isinstance(v, (list, tuple)):
            comps = list(v)
        else:
            raise SettingsError(f"{path}: expected {len(names)} numbers or an object for a {kind}, got {v!r}")
        if len(comps) != len(names) or any(isinstance(c, bool) or not isinstance(c, (int, float)) for c in comps):
            raise SettingsError(f"{path}: a {kind} is {len(names)} numbers, got {v!r}")
        return {"vec2": rt.Vec2, "vec3": rt.Vec3, "rotator": rt.Rotator, "quat": rt.Quat}[kind](*comps)

    def decode_transform(self, v, path: str):
        """`{"location": [x, y, z], "rotation": [x, y, z, w] or {Pitch, Yaw, Roll}, "scale": [x, y, z]}`; the keys may be capitalised
        (Translation / Location, Rotation, Scale3D / Scale); missing parts are the identity."""
        if not isinstance(v, dict):
            raise SettingsError(f"{path}: expected an object with location, rotation and scale for a transform, got {v!r}")
        low = {str(k).lower(): x for k, x in v.items()}
        loc = low.get("location", low.get("translation"))
        rot = low.get("rotation")
        scale = low.get("scale", low.get("scale3d"))
        if isinstance(rot, dict) and "pitch" in {str(k).lower() for k in rot}:
            rot = rt.to_quat(self.decode_vector("rotator", rot, path + ".rotation"))
        return rt.Transform(self.decode_vector("vec3", loc, path + ".location") if loc is not None else rt.VEC3_ZERO,
                            self.decode_vector("quat", rot, path + ".rotation") if rot is not None else rt.QUAT_IDENTITY,
                            self.decode_vector("vec3", scale, path + ".scale") if scale is not None else rt.Vec3(1.0, 1.0, 1.0))

    def decode_curve(self, kind: str, v, path: str):
        if v is None:
            return None
        curve = None
        if isinstance(v, dict) and ("channels" in v or "keys" in v):
            try:
                curve = curve_from_json(v, str(v.get("name", path)))
            except CurveError as e:
                raise SettingsError(f"{path}: {e}")
        else:
            name = asset_name(v)
            if name is None:
                return None
            try:
                curve = self.resolver.curve(name)
            except (CurveError, OSError, ValueError) as e:
                raise SettingsError(f"{path}: curve '{name}': {e}")
            if curve is None:
                self.report.unresolved.append(f"{path}: curve '{name}'")
                return None
        want3 = kind == "curve3"
        if want3 != isinstance(curve, Curve3):
            raise SettingsError(f"{path}: expected a {'vector' if want3 else 'float'} curve, got a {'vector' if isinstance(curve, Curve3) else 'float'} curve")
        return curve


def _read_source(source):
    if source is None:
        return None
    if isinstance(source, (str, os.PathLike)):
        with open(source, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    return source


def load(schema: dict, classes: dict, type_name: str, source=None, resolver: AssetResolver | None = None, report: Report | None = None, f32: bool = False):
    """An instance of the settings type ``type_name`` from ``source`` (None, a dict, a JSON file path, or an instance already).

    ``schema`` and ``classes`` describe the type graph (``schema_of`` for the interpreter, ``DAL_SCHEMA`` / ``DAL_CLASSES`` of a
    generated module).  Problems that do not make the data unusable go to ``report``; the rest raises ``SettingsError``."""
    cls = classes[type_name]
    if isinstance(source, rt.Struct) and type(source) is cls:
        return source
    data = _read_source(source)
    loader = _Loader(schema, classes, resolver or AssetResolver(), report if report is not None else Report(), f32)
    return loader.build(type_name, data, type_name)


def load_ir(ty, source=None, resolver: AssetResolver | None = None, report: Report | None = None, f32: bool = False):
    """``load`` for a ``StructType`` of the checked IR (the interpreter's classes)."""
    from . import values

    types = collect_types(ty)
    schema = schema_of(types.values())
    classes = {n: values.struct_class(t) for n, t in types.items()}
    if isinstance(source, rt.Struct) and source._TYPE_NAME == ty.name:
        return values.copy_value(source)
    return load(schema, classes, ty.name, source, resolver, report, f32)
