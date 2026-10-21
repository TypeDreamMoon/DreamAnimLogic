"""Name resolution and static type checking: syntax tree -> typed IR.

Errors are collected (with a poison ``ERROR`` type to avoid cascades) and raised
together as one ``DalError``; warnings are returned with the module.
"""

from __future__ import annotations

import ast as pyast
import difflib
import hashlib
import re
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
from .hostapi import COMMANDS, HOOK_EVENTS, HOST_ITEMS, QUERIES
from .interp import eval_const
from . import peers as peermod
from . import values
from .lexer import RESERVED_WORDS
from .libs import LIBRARY_DECLS
from .names import ANIM_NOTIFY_MEMBERS, CPP_KEYWORDS, PY_RESERVED, reserved_reason
from .types import (
    VOID,
    ASSET_TYPES,
    CHANNEL_SET,
    ArrayType,
    AssetType,
    CURVE,
    CURVE3,
    MapType,
    NONE_T,
    BASIC_TYPES,
    BOOL,
    BUILTIN_ENUMS,
    ERROR,
    FLOAT,
    HIT,
    HIT_MEMBERS,
    SPRING_MEMBERS,
    TRANSFORM,
    TRANSFORM_MEMBERS,
    INT,
    MONTAGE,
    MONTAGE_ASSET,
    MOVEMENT_MODE,
    NAME_T,
    QUAT,
    ROTATOR,
    SNAPSHOT,
    VEC2,
    VEC3,
    EnumType,
    FieldDef,
    StructType,
    TagType,
    Type,
    zero_value,
)


@dataclass
class Sym:
    kind: str       # enum const param input state fn extern entry logic
    pos: Pos
    obj: object = None
    file: str = ""  # file of the declaration (set for declarations from libraries)


# the components of the vector types, in constructor order, and the constructor that rebuilds one
COMPONENTS: dict = {VEC2: (("x", "y"), "vec2"), VEC3: (("x", "y", "z"), "vec3"), ROTATOR: (("pitch", "yaw", "roll"), "rotator"),
                    QUAT: (("x", "y", "z", "w"), "quat")}


@dataclass
class Local:
    ty: Type
    mutable: bool


@dataclass
class MethodSig:
    params: list[tuple[str, Type]]
    ret: Type | None
    pub: bool
    is_view: bool
    pos: Pos
    decl: object = None


class Scope:
    """Body scope: ``ctx`` is 'const' | 'fn' | 'update' | 'entry' | 'handler' | 'method' | 'view'."""

    def __init__(self, ctx: str, ret: Type | None = None, fn_name: str = ""):
        self.ctx = ctx
        self.ret = ret
        self.fn_name = fn_name
        self.notify_handler = False         # the handler of a declared branching notify: host.montage_stop_source names the montage instance that raised it
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


# attributes of the generated struct classes (dal.runtime.Struct): a member may not hide them
STRUCT_API = frozenset({"copy", "to_plain", "flatten", "from_value", "load"})

_SWIZZLE = {"x": 0, "y": 1, "z": 2}
_MONTAGE_STATE = {"playing": BOOL, "active": BOOL, "position": FLOAT}   # Light1.playing ... ('section' has the montage's own enum)
_HIT_TYPES = {"bool": BOOL, "vec3": VEC3, "float": FLOAT, "quat": QUAT, "primitive": ASSET_TYPES["primitive"]}
_ACTION_TYPES = (BOOL, FLOAT, VEC2, VEC3)


class Checker:
    def __init__(self, unit: S.SourceUnit, source: SourceFile, standin: tuple[str, str] | None = None,
                 ctx=None, libs=()):
        self.unit = unit
        self.source = source
        self.main_source = source
        self.ctx = ctx
        self.libs = list(libs)
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
        self.later: dict[str, str] = {}   # declared variables, for better messages in constant expressions
        self.later_fns: set[str] = set()  # functions declared later in the file
        self.invalid_names: set[str] = set()  # declared with an unusable name; uses are not reported again
        self.tagsets: dict[str, TagType] = {}
        self.structs: dict[str, StructType] = {}          # struct and settings types by name
        self.struct_decls: dict[str, S.StructDecl] = {}
        self.folding: set[str] = set()
        self.struct_cached: set[str] = set()              # struct types reused from another compilation unit (already complete)
        self.allow_settings_param = False                 # the next name read is the base of a member access
        self.interface_only = False                       # stop after the declarations (the checker run for a peer's interface)
        self.loop_depth = 0                               # how many loops enclose the statement being checked (`break` needs one)
        self.settings_init = False                        # the default of a settings member is being folded (it may build a settings value)
        self.method_sigs: dict[str, MethodSig] = {}
        self.entry_sigs: dict[int, list[ir.EntryParam]] = {}      # id(EntryDecl) -> its typed parameters
        self.method_calls: dict[str, list[str]] = {}      # call graph of methods and views (recursion check)
        self.peer_ifaces: dict[str, peermod.PeerIface] = {}
        self.notifies: dict[str, ir.NotifyDef] = {}       # the animation notify classes the program declares (and its libraries)
        self.value_calls: set[int] = set()                # ids of calls that may produce a value (the whole right-hand side of let / = / return)
        self.stmt_calls: set[int] = set()                 # ids of calls used as statements
        self.kind = "anim"

    def focus(self, d) -> None:
        """Report diagnostics against the file ``d`` was written in (a library, or the main file)."""
        self.source = getattr(d, "src", None) or self.main_source

    @staticmethod
    def origin_of(d) -> str | None:
        return getattr(d, "origin", None)

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
        reason = reserved_reason(name, allow_py_keyword=allow_py_keyword, allow_sample_locals=(what == "a peer name"))
        if reason:
            self.err("DAL3004", f"'{name}' cannot be used as {what}: {reason}", pos)
            return False
        return True

    def declare_global(self, name: str, kind: str, pos: Pos, obj: object = None, what: str = "") -> bool:
        if not self.valid_ident(name, pos, what or f"a {kind} name"):
            self.invalid_names.add(name)
            return False
        prev = self.globals.get(name)
        if prev is not None:
            where = f" of {prev.file}" if prev.file and prev.file != self.source.filename else ""
            self.err("DAL3001", f"'{name}' is already declared as a {prev.kind} on line {prev.pos.line}{where}", pos)
            return False
        self.globals[name] = Sym(kind, pos, obj, self.source.filename if self.source is not self.main_source else "")
        return True

    # ---- types ----------------------------------------------------------------------------------
    def split_type_args(self, text: str) -> list[str]:
        """`A,map<B,C>` -> ['A', 'map<B,C>']"""
        out, depth, cur = [], 0, ""
        for ch in text:
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth -= 1
            if ch == "," and depth == 0:
                out.append(cur)
                cur = ""
            else:
                cur += ch
        out.append(cur)
        return out

    def resolve_type(self, name: str, pos: Pos) -> Type:
        if name.startswith("map<") and name.endswith(">"):
            key_name, value_name = self.split_type_args(name[4:-1])
            key, value = self.resolve_type(key_name, pos), self.resolve_type(value_name, pos)
            if key is ERROR or value is ERROR:
                return ERROR
            if not (key.is_tags or key.is_enum or key is INT):
                self.err("DAL4052", f"a table is keyed by a tag set, an enum or int, not {key}", pos)
                return ERROR
            if value is HIT or value is MONTAGE or value in SPRING_MEMBERS or isinstance(value, (MapType, ArrayType)):
                self.err("DAL4052", f"a table cannot hold {value}", pos)
                return ERROR
            return MapType.of(key, value)
        if name.startswith("array<") and name.endswith(">"):
            elem = self.resolve_type(name[6:-1], pos)
            if elem is ERROR:
                return ERROR
            if elem is HIT or elem is MONTAGE or elem in SPRING_MEMBERS or isinstance(elem, (MapType, ArrayType)):
                self.err("DAL4052", f"a list cannot hold {elem}", pos)
                return ERROR
            return ArrayType.of(elem)
        if name == "channel_set":
            return CHANNEL_SET
        if name in ASSET_TYPES:
            return ASSET_TYPES[name]
        if name in BASIC_TYPES:
            return BASIC_TYPES[name]
        if name in self.enums:
            return self.enums[name]
        if name in self.structs:
            return self.structs[name]
        if name in self.tagsets:
            return self.tagsets[name]
        cands = list(BASIC_TYPES) + list(self.enums) + list(self.structs) + list(self.tagsets)
        self.err("DAL3041", f"unknown type '{name}'" + self.suggest(name, cands), pos)
        return ERROR

    def assignable(self, target: Type, e: ir.Expr, pos: Pos, what: str) -> ir.Expr:
        """Return ``e`` converted to ``target`` or report DAL4002."""
        if target is ERROR or e.ty is ERROR:
            return e
        if e.ty is target:
            return e
        if e.ty is NONE_T and isinstance(target, AssetType) and not target.settings_only:
            return ir.Lit(target, None)
        if target is FLOAT and e.ty is INT:
            return self.widen(e)
        hint = ""
        if e.ty is NONE_T:
            hint = "; 'none' is the empty value of a sequence or montage"
        if target is INT and e.ty is FLOAT:
            hint = "; use to_int(), floor_to_int(), round_to_int() or ceil_to_int()"
        self.err("DAL4002", f"cannot use a value of type {e.ty} as {what} of type {target}{hint}", pos)
        return e

    # ---- run ----------------------------------------------------------------------------------------
    def run(self) -> ir.Module:
        m = self.module
        m.filename = self.source.filename
        text_hash = hashlib.sha256(self.source.text.encode("utf-8"))
        if self.libs:
            # the output depends on the libraries too: hash them in, in use order (programs without libraries keep their hash)
            for lib in self.libs:
                text_hash.update(lib.sha256.encode("ascii"))
            m.libs = [ir.LibRef(lib.key, lib.filename, lib.stem, lib.sha256, list(lib.deps)) for lib in self.libs]
        m.source_sha256 = text_hash.hexdigest()
        decls = self.unit.decls

        logics = [d for d in decls if isinstance(d, S.LogicDecl)]
        own = [d for d in decls if self.origin_of(d) is None]
        if not logics:
            if own and all(type(d).__name__ in LIBRARY_DECLS for d in own):
                # a library compiled on its own: only shared declarations, no logic class
                from .libs import camel_stem
                m.is_library = True
                m.name = camel_stem(self.source.filename)
            else:
                self.err("DAL3010", "missing 'logic <Name>;' declaration (every .dal file declares exactly one logic)", Pos(1, 1, 2))
        else:
            if len(logics) > 1:
                self.err("DAL3011", "only one 'logic' declaration is allowed per file", logics[1].pos)
            self.focus(logics[0])
            self.declare_global(logics[0].name, "logic", logics[0].name_pos or logics[0].pos, what="a logic name")
            m.name = logics[0].name
            self.kind = m.kind = logics[0].kind

        # 1. enums, montages and actions (types are needed by everything else)
        for d in decls:
            self.focus(d)
            if isinstance(d, S.EnumDecl):
                self.declare_enum(d)
            elif isinstance(d, S.MontageDecl):
                self.declare_montage(d)
        for d in decls:
            self.focus(d)
            if isinstance(d, S.TagsDecl):
                self.declare_tags(d)
        self.module.tagsets = list(self.tagsets.values())
        for d in decls:
            self.focus(d)
            if isinstance(d, S.StructDecl):
                self.declare_struct(d)
        for d in decls:
            self.focus(d)
            if isinstance(d, S.StructDecl):
                self.declare_struct_members(d)
        self.order_structs()
        for d in decls:
            self.focus(d)
            if isinstance(d, S.ActionDecl):
                self.declare_action(d)
        for d in decls:
            self.focus(d)
            if isinstance(d, S.NotifyDecl):
                self.declare_notify(d)

        for d in decls:
            if isinstance(d, S.ParamDecl):
                self.later[d.name] = "param"
            elif isinstance(d, S.InputDecl):
                self.later[d.name] = "input"
            elif isinstance(d, S.StateDecl):
                self.later[d.name] = "state"
            elif isinstance(d, (S.FnDecl, S.ExternDecl, S.MethodDecl)):
                self.later_fns.add(d.name)

        # 2. consts, in source order
        for d in decls:
            if isinstance(d, S.ConstDecl):
                self.pending_consts.add(d.name)
        for d in decls:
            self.focus(d)
            if isinstance(d, S.ConstDecl):
                self.declare_const(d)
            elif isinstance(d, S.StructDecl):
                st = self.structs.get(d.name)
                if st is not None and self.struct_decls.get(d.name) is d:
                    self.ensure_defaults(st)
            elif isinstance(d, S.NotifyDecl):
                self.fold_notify_defaults(d)

        # 3. signatures of functions and externs, then the variable declarations
        for d in decls:
            self.focus(d)
            if isinstance(d, S.FnDecl):
                self.declare_fn_sig(d)
            elif isinstance(d, S.ExternDecl):
                self.declare_extern(d)
        for d in decls:
            self.focus(d)
            if isinstance(d, S.StructDecl):
                self.check_derived(d)
        self.refresh_settings_defaults()
        for d in decls:
            self.focus(d)
            if isinstance(d, S.PeerDecl):
                self.declare_peer(d)
        for d in decls:
            self.focus(d)
            if isinstance(d, S.ParamDecl):
                self.declare_param(d)
            elif isinstance(d, S.InputDecl):
                self.declare_input(d)
            elif isinstance(d, S.StateDecl):
                self.declare_state(d)
        for d in decls:
            self.focus(d)
            if isinstance(d, S.MethodDecl):
                self.declare_method_sig(d)
        for d in decls:
            self.focus(d)
            if isinstance(d, S.EntryDecl):
                self.declare_entry_sig(d)
        for d in logics[:1]:
            self.focus(d)
            if d.linked is not None:
                self.declare_linked(d)

        if self.interface_only:
            self.source = self.main_source
            if any(x.severity == "error" for x in self.diags):
                raise DalError(self.diags)
            m.enums = self.user_enums
            for d in decls:
                if isinstance(d, S.EntryDecl):
                    m.entries.append(ir.EntryDef(d.name, d.dt_name, [], self.entry_sigs.get(id(d), []), d.linked))
            for name, sig in self.method_sigs.items():
                m.methods.append(ir.MethodDef(name, sig.params, sig.ret, [], sig.pub, sig.is_view))
            return m

        # 4. bodies
        for d in decls:
            self.focus(d)
            if isinstance(d, S.FnDecl):
                self.check_fn(d)
        for d in decls:
            self.focus(d)
            if isinstance(d, S.MethodDecl):
                self.check_method(d)
        self.check_updates([d for d in decls if isinstance(d, S.UpdateDecl)], logics[0] if logics else None)
        self.check_post_update([d for d in decls if isinstance(d, S.PostUpdateDecl)], logics[0] if logics else None)
        self.check_logic_options(logics[0] if logics else None)
        for d in decls:
            self.focus(d)
            if isinstance(d, S.EntryDecl):
                m.entries.append(self.check_entry(d))
        for d in decls:
            self.focus(d)
            if isinstance(d, S.HandlerDecl):
                h = self.check_handler(d)
                if h is not None:
                    m.handlers.append(h)
        self.source = self.main_source
        if not m.is_library and m.update is None and not m.entries and not m.handlers and m.linked is None and m.post_update is None:
            self.warn("DAL5091", "the logic has no 'update', no 'entry' and no 'on' handler; it never changes anything", Pos(1, 1, 2))

        self.order_functions()
        self.order_methods()
        self.check_standins()

        errors = [d for d in self.diags if d.severity == "error"]
        if errors:
            raise DalError(self.diags)
        m.enums = self.user_enums
        return m

    # ---- methods, views, peers, tick slots ------------------------------------------------------------------------
    def declare_method_sig(self, d: S.MethodDecl) -> None:
        what = "a view name" if d.is_view else "a method name"
        ok = self.declare_global(d.name, "view" if d.is_view else "method", d.name_pos or d.pos, what=what)
        params = self.declare_params(d.params)
        ret: Type | None = None
        if d.ret_name is not None:
            ret = self.resolve_type(d.ret_name, d.ret_pos or d.pos)
            if ret is not ERROR:
                ret = self.storable(ret, d.ret_pos or d.pos, "a result", settings_ok=True)
        if ok and params is not None:
            self.method_sigs[d.name] = MethodSig(params, ret, d.pub, d.is_view, d.pos, d)

    def check_method(self, d: S.MethodDecl) -> None:
        sig = self.method_sigs.get(d.name)
        if sig is None or sig.decl is not d:
            return
        sc = Scope("view" if sig.is_view else "method", sig.ret, d.name)
        for n, t in sig.params:
            sc.declare(n, Local(t, False))
        self.method_calls[d.name] = []
        body = self.block(d.body, sc)
        if sig.ret is not None and sig.ret is not ERROR and not self.always_returns(body):
            self.err("DAL5021", f"{'view' if sig.is_view else 'method'} '{d.name}' may finish without returning a {sig.ret}; every path must end in 'return'", d.pos)
        self.module.methods.append(ir.MethodDef(d.name, sig.params, sig.ret, body, sig.pub, sig.is_view))

    def order_methods(self) -> None:
        """Methods and views may not call themselves, directly or through each other (DAL5046)."""
        state: dict[str, int] = {}
        reported = False

        def visit(n: str, path: list[str]) -> None:
            nonlocal reported
            if n not in self.method_calls:
                return
            st = state.get(n, 0)
            if st == 2:
                return
            if st == 1:
                if not reported:
                    cycle = path[path.index(n):] + [n]
                    sig = self.method_sigs.get(n)
                    self.err("DAL5046", "recursion is not allowed: " + " -> ".join(cycle), sig.pos if sig else Pos(1, 1, 2))
                    reported = True
                return
            state[n] = 1
            for c in self.method_calls.get(n, []):
                visit(c, path + [n])
            state[n] = 2

        for n in list(self.method_calls):
            visit(n, [])

    def declare_peer(self, d: S.PeerDecl) -> None:
        ok = self.declare_global(d.name, "peer", d.name_pos or d.pos, what="a peer name")
        if self.interface_only or not ok:
            return
        if self.ctx is None:
            self.err("DAL3072", "peers are found through the compile context (use dal.check_file / dal.check_source)", d.type_pos)
            return
        try:
            iface = peermod.load_interface(self.ctx, self.ctx.main_key, d.type_name)
        except FileNotFoundError:
            self.err("DAL3072", f"peer type '{d.type_name}': there is no {d.type_name}.dal next to this file", d.type_pos)
            return
        except DalError as e:
            self.diags.extend(e.diagnostics)
            return
        if iface.logic != d.type_name:
            self.err("DAL3072", f"{d.type_name}.dal declares the logic '{iface.logic}', not '{d.type_name}'", d.type_pos)
            return
        if self.kind == "anim" and iface.kind == "anim":
            self.err("DAL5045", "an anim logic cannot have another anim logic as a peer (they run on worker threads)", d.type_pos)
            return
        self.peer_ifaces[d.name] = iface
        self.module.peers.append(ir.PeerDef(d.name, iface.logic, iface.kind, iface.filename,
                                            {n: (list(ps), r) for n, (ps, r) in iface.methods.items()}, list(iface.slots)))

    def check_peer_binding(self, d: S.InputDecl, ty: Type) -> ir.PeerUse | None:
        b = d.peer
        assert b is not None
        iface = self.peer_ifaces.get(b.peer)
        if iface is None:
            sym = self.globals.get(b.peer)
            if sym is None:
                self.err("DAL3040", f"undefined name '{b.peer}'" + self.suggest(b.peer, [k for k, v in self.globals.items() if v.kind == "peer"])
                         + f" (declare the peer: peer {b.peer}: LogicName;)", b.pos)
            elif sym.kind != "peer":
                self.err("DAL3076", f"'{b.peer}' is a {sym.kind}, not a peer: an input can read a host item (host.x) or a peer (Peer.State)", b.pos)
            return None
        if self.kind == "actor" and iface.kind == "anim":
            self.err("DAL5062", f"{iface.logic} is an anim logic and runs on a worker thread: an actor logic cannot read its state", b.pos)
            return None
        first = b.path[0]
        cur = iface.vars.get(first)
        if cur is None:
            self.err("DAL3075", f"logic {iface.logic} has no state, input or param '{first}'" + self.suggest(first, iface.vars), b.path_pos[0])
            return None
        for member, mpos in zip(b.path[1:], b.path_pos[1:]):
            if not isinstance(cur, StructType):
                self.err("DAL4030", f"type {cur} has no members", mpos)
                return None
            fd = cur.field(member)
            if fd is None:
                self.err("DAL4030", f"{cur.kind} {cur.name} has no member '{member}'" + self.suggest(member, [x.name for x in cur.fields]), mpos)
                return None
            cur = fd.ty
        if ty is not ERROR and cur is not ty:
            self.err("DAL4020", f"{b.peer}.{'.'.join(b.path)} has type {cur}, but input '{d.name}' is declared {ty}", d.type_pos)
            return None
        return ir.PeerUse(b.peer, tuple(b.path), first in iface.settings_assets)

    PHASE_ORDER = ("pre_movement", "after_movement", "after_anim")

    def check_updates(self, updates: list[S.UpdateDecl], logic: S.LogicDecl | None) -> None:
        m = self.module
        logic_phase = logic.phase if logic is not None else None
        if self.kind == "anim":
            for x, pos in [(logic, logic.phase_pos)] if (logic is not None and logic.phase) else []:
                self.focus(logic)
                self.err("DAL5064", "tick slots (in pre_movement / after_movement / after_anim) are for actor logic; an anim logic has one update", pos or logic.pos)
            for d in updates:
                if d.phase:
                    self.focus(d)
                    self.err("DAL5064", "tick slots (in pre_movement / after_movement / after_anim) are for actor logic; an anim logic has one update", d.phase_pos or d.pos)
            logic_phase = None
        m.phases_explicit = bool(logic_phase) or any(d.phase for d in updates if self.kind == "actor")
        seen: dict[str, S.UpdateDecl] = {}
        slotted: list[tuple[str, S.UpdateDecl]] = []
        for d in updates:
            phase = (d.phase if self.kind == "actor" else None) or logic_phase or "pre_movement"
            if phase in seen:
                self.focus(d)
                if d.phase or seen[phase].phase:
                    self.err("DAL5049", f"two updates run in the tick slot {phase}; each slot has at most one update block", d.pos)
                else:
                    self.err("DAL3012", "only one 'update' is allowed", d.pos)
                continue
            seen[phase] = d
            slotted.append((phase, d))
        slotted.sort(key=lambda pd: self.PHASE_ORDER.index(pd[0]))
        for phase, d in slotted:
            self.focus(d)
            m.updates.append(self.check_update(d, phase))

    def check_logic_options(self, logic: S.LogicDecl | None) -> None:
        """`slot_events` and the tick slot order of the logic line (`before Peer`, `after Peer`)."""
        if logic is None:
            return
        m = self.module
        self.focus(logic)
        m.slot_events = logic.slot_events
        mine = [u.phase for u in m.updates] or [m.default_phase]
        for relation, peer, pos in logic.order:
            iface = self.peer_ifaces.get(peer)
            if iface is None:
                sym = self.globals.get(peer)
                if sym is None:
                    self.err("DAL3040", f"undefined name '{peer}'" + self.suggest(peer, [k for k, v in self.globals.items() if v.kind == "peer"])
                             + f" (declare the peer: peer {peer}: LogicName;)", pos)
                elif sym.kind != "peer":
                    self.err("DAL3076", f"'{peer}' is a {sym.kind}, not a peer: `{relation}` names a peer of this logic", pos)
                continue
            if iface.kind != "actor":
                self.err("DAL5068", f"{iface.logic} is an animation instance: it has no tick slots to be ordered against (its update runs on a worker thread)", pos)
                continue
            if any(o.peer == peer for o in m.order):
                self.err("DAL5069", f"the order against '{peer}' is declared twice", pos)
                continue
            common = tuple(s for s in mine if s in iface.slots)
            if not common:
                self.warn("DAL5095", f"{iface.logic} has no update block in a tick slot of this logic ({', '.join(mine)}): `{relation} {peer}` orders nothing", pos)
            m.order.append(ir.OrderDef(relation, peer, iface.logic, common))

    def check_post_update(self, posts: list[S.PostUpdateDecl], logic: S.LogicDecl | None) -> None:
        """`post_update { ... }` (anim logic): runs on the game thread after the animation graph updated, with the inputs of the frame's
        update; `logic X : anim post_flush;` moves the flush of the queued commands behind it."""
        m = self.module
        if logic is not None and logic.post_flush:
            m.post_flush = True
        for i, d in enumerate(posts):
            self.focus(d)
            if self.kind != "anim":
                self.err("DAL5065", "post_update belongs to an animation logic (logic Name;): it runs after the animation graph updated; an actor logic has tick slots (update ... in after_anim)", d.pos)
                continue
            if i > 0:
                self.err("DAL3079", "only one 'post_update' is allowed", d.pos)
                continue
            sc = Scope("update")
            if d.dt_name is not None:
                if not self.valid_ident(d.dt_name, d.dt_pos or d.pos, "the delta-time parameter"):
                    pass
                elif d.dt_name in self.globals:
                    self.err("DAL3005", f"'{d.dt_name}' hides the {self.globals[d.dt_name].kind} of the same name", d.dt_pos or d.pos)
                sc.declare(d.dt_name, Local(FLOAT, False))
            m.post_update = ir.UpdateDef(d.dt_name or "dt", self.block(d.body, sc), [], "post_update")
        if (m.post_update is not None or m.post_flush) and "post_update" in self.globals:
            sym = self.globals["post_update"]
            self.source = self.main_source
            self.err("DAL3004", f"'post_update' cannot be the name of a {sym.kind} in a logic that has a post_update block or post_flush: it is the generated method the host calls", sym.pos)

    # ---- tags -------------------------------------------------------------------------------------------------
    def declare_tags(self, d: S.TagsDecl) -> None:
        if not self.declare_global(d.name, "tags", d.name_pos or d.pos, what="a tag set name"):
            return
        origin = self.origin_of(d)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)*", d.prefix):
            self.err("DAL4057", f"'{d.prefix}' is not a gameplay tag prefix: dotted names such as \"Als.Gait\" (letters, digits, '_')", d.prefix_pos)
        for other in self.tagsets.values():
            if other.prefix == d.prefix:
                self.err("DAL4051", f"tag set {other.name} already uses the prefix \"{d.prefix}\"", d.prefix_pos)
        members: list[str] = []
        seen: set[str] = set()
        for mname, mpos in d.members:
            if mname == "None":
                self.err("DAL3059", "'None' is the implicit empty tag of every tag set", mpos)
            elif mname in seen:
                self.err("DAL3001", f"tag '{mname}' is declared twice in {d.name}", mpos)
            elif mname in CPP_KEYWORDS:
                self.err("DAL3004", f"'{mname}' cannot be a tag name: it is a C++ keyword", mpos)
            else:
                members.append(mname)
            seen.add(mname)
        cached = self.ctx.types.get((origin, d.name)) if (self.ctx is not None and origin is not None) else None
        tt = cached if cached is not None else TagType(d.name, d.prefix, members)
        tt.origin = origin
        if cached is None and self.ctx is not None and origin is not None:
            self.ctx.types[(origin, d.name)] = tt
        self.tagsets[d.name] = tt
        self.globals[d.name].obj = tt

    # ---- animation notifies ---------------------------------------------------------------------------------------
    def declare_notify(self, d: S.NotifyDecl) -> None:
        """`notify Name { ... }`: a class of animation notify.  Its members are the properties of the generated notify and the
        members of the `payload` its handlers receive; the name is not a type (the payload is only read through its members)."""
        what = "a notify state name" if d.state else "a notify name"
        if not self.declare_global(d.name, "notify", d.name_pos or d.pos, what=what):
            return
        origin = self.origin_of(d)
        cpp = d.cpp or ("DalNotifyState_" if d.state else "DalNotify_") + d.name
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", cpp) or cpp in CPP_KEYWORDS:
            self.err("DAL3066", f"'{cpp}' is not a C++ class name (letters, digits and '_'; the generated class is U{cpp})", d.cpp_pos or d.name_pos or d.pos)
            cpp = ("DalNotifyState_" if d.state else "DalNotify_") + d.name
        st = StructType(d.name, "notify")
        st.origin = origin
        st.cpp_type = "U" + cpp
        seen: dict[str, Pos] = {}
        for fd in d.fields:
            ty = self.resolve_type(fd.type_name, fd.type_pos)
            if fd.derived:
                self.err("DAL4052", "'derived' members belong in a settings type; a notify member is plain data", fd.pos)
            if fd.name in seen:
                self.err("DAL3001", f"member '{fd.name}' is declared twice in notify {d.name} (first on line {seen[fd.name].line})", fd.pos)
                continue
            seen[fd.name] = fd.pos
            if not self.valid_ident(fd.name, fd.pos, "a member name") or fd.name in STRUCT_API:
                if fd.name in STRUCT_API:
                    self.err("DAL3004", f"'{fd.name}' cannot be a member name: it is an attribute of the generated struct classes", fd.pos)
                continue
            if fd.name in ANIM_NOTIFY_MEMBERS:
                self.err("DAL3004", f"'{fd.name}' cannot be a member of a notify: it is a member of UAnimNotify / UAnimNotifyState", fd.pos)
                continue
            if ty is not ERROR and not self.struct_member_ok(st, ty):
                what_ = "hit results cannot be stored" if ty is HIT else f"a {ty} cannot be a member of a notify"
                self.err("DAL4050", f"member '{fd.name}' of notify {d.name}: {what_}", fd.type_pos)
                ty = ERROR
            st.fields.append(FieldDef(fd.name, ty))
        st.defaults_ready = True
        nd = ir.NotifyDef(d.name, d.state, st, cpp, d.branching, origin)
        self.notifies[d.name] = nd
        self.globals[d.name].obj = nd
        self.module.notifies.append(nd)

    def fold_notify_defaults(self, d: S.NotifyDecl) -> None:
        nd = self.notifies.get(d.name)
        if nd is None:
            return
        by_name = {fd.name: fd for fd in d.fields}
        for f in nd.struct.fields:
            fd = by_name.get(f.name)
            if fd is None or f.ty is ERROR:
                continue
            f.default = self.const_value(fd.init, f.ty, fd.pos, f"member '{f.name}' of notify {d.name}")

    # ---- structs ----------------------------------------------------------------------------------------------
    def declare_struct(self, d: S.StructDecl) -> None:
        if not self.declare_global(d.name, "struct", d.name_pos or d.pos, what="a struct name"):
            return
        origin = self.origin_of(d)
        cached = self.ctx.types.get((origin, d.name)) if (self.ctx is not None and origin is not None) else None
        st = cached if cached is not None else StructType(d.name, d.kind)
        st.origin = origin
        self.structs[d.name] = st
        self.struct_decls[d.name] = d
        self.globals[d.name].obj = st
        if cached is not None:
            self.struct_cached.add(d.name)
        elif self.ctx is not None and origin is not None:
            self.ctx.types[(origin, d.name)] = st

    def struct_member_ok(self, st: StructType, ty: Type) -> bool:
        if ty is HIT or ty is MONTAGE or ty in SPRING_MEMBERS:
            return False
        if ty is SNAPSHOT:
            return st.kind == "struct" and not st.is_notify          # a snapshot is state data: not settings data, not a notify's payload
        if isinstance(ty, AssetType):
            return st.kind == "settings" or not ty.settings_only
        if isinstance(ty, (MapType, ArrayType)):
            return st.kind == "settings"
        if isinstance(ty, StructType):
            if ty.is_settings and st.kind == "struct" and not st.is_notify:
                return True                                 # a settings value nested in a data asset (checked once the data assets are known: find_settings_roots)
            return st.kind == "settings" or ty.kind == "struct"
        return True

    def declare_struct_members(self, d: S.StructDecl) -> None:
        st = self.structs.get(d.name)
        if st is None or self.struct_decls.get(d.name) is not d:
            return
        if d.name in self.struct_cached:
            return                                       # shared with another compilation unit: already complete
        seen: dict[str, Pos] = {}
        for fd in d.fields:
            ty = self.resolve_type(fd.type_name, fd.type_pos)
            if fd.name in seen:
                self.err("DAL3001", f"member '{fd.name}' is declared twice in {st.kind} {d.name} (first on line {seen[fd.name].line})", fd.pos)
                continue
            seen[fd.name] = fd.pos
            if not self.valid_ident(fd.name, fd.pos, "a member name") or fd.name in STRUCT_API:
                if fd.name in STRUCT_API:
                    self.err("DAL3004", f"'{fd.name}' cannot be a member name: it is an attribute of the generated struct classes", fd.pos)
                continue
            if ty is not ERROR and not self.struct_member_ok(st, ty):
                what = "hit results cannot be stored" if ty is HIT else f"a {ty} cannot be a member of a {st.kind}"
                self.err("DAL4050" if st.kind == "struct" else "DAL4052", f"member '{fd.name}' of {st.kind} {d.name}: {what}", fd.type_pos)
                ty = ERROR
            if fd.derived and st.kind != "settings":
                self.err("DAL4052", f"'derived' members belong in a settings type; a struct member is plain data", fd.pos)
            if fd.derived and fd.init is None:
                self.err("DAL4052", f"derived member '{fd.name}' needs the expression it is computed from: derived {fd.name}: {fd.type_name} = ...;", fd.pos)
            st.fields.append(FieldDef(fd.name, ty, derived=fd.derived))

    def order_structs(self) -> None:
        """Dependencies first (a struct member of struct type needs its type declared before); reports DAL4053 for cycles."""
        order: list[StructType] = []
        state: dict[str, int] = {}
        reported = False

        def inner(ty: Type) -> StructType | None:
            """The struct a member holds: itself, or the value type of a table / the element type of a list."""
            if isinstance(ty, MapType):
                ty = ty.value
            elif isinstance(ty, ArrayType):
                ty = ty.elem
            return ty if isinstance(ty, StructType) else None

        def visit(st: StructType, path: list[str]) -> None:
            nonlocal reported
            if state.get(st.name, 0) == 2:
                return
            state[st.name] = 1
            for f in st.fields:
                sub_st = inner(f.ty)
                if sub_st is None:
                    continue
                if state.get(sub_st.name, 0) == 1:
                    # this member closes a cycle: report it once and cut it, so that no default value is infinite
                    if not reported:
                        d = self.struct_decls.get(sub_st.name)
                        if d is not None:
                            self.focus(d)
                            cycle = (path + [st.name])
                            cycle = cycle[cycle.index(sub_st.name):] + [sub_st.name]
                            self.err("DAL4053", f"{sub_st.kind} {sub_st.name} contains itself: " + " -> ".join(cycle), d.name_pos or d.pos)
                        reported = True
                    f.ty = ERROR
                else:
                    visit(sub_st, path + [st.name])
            state[st.name] = 2
            order.append(st)

        for d in self.unit.decls:
            if isinstance(d, S.StructDecl):
                st = self.structs.get(d.name)
                if st is not None and self.struct_decls.get(d.name) is d:
                    visit(st, [])
        self.module.structs = order
        self.find_settings_roots(order, inner)

    def find_settings_roots(self, order: list[StructType], inner) -> None:
        """A settings type that no other settings type of its file holds is a data asset (`UDataAsset` in C++); the others are
        plain value types inside one.  A type of a library that is a data asset there cannot be held by a logic's own settings."""
        held: set[tuple[str | None, str]] = set()
        for st in order:
            if st.is_settings:
                for f in st.fields:
                    sub_st = inner(f.ty)
                    if sub_st is not None and sub_st.is_settings and sub_st.origin == st.origin:
                        held.add((sub_st.origin, sub_st.name))
        for st in order:
            if st.is_settings and st.name not in self.struct_cached:
                st.asset_root = (st.origin, st.name) not in held
                st.cpp_type = ("U" if st.asset_root else "F") + st.name
        for st in order:
            if st.kind != "struct" or st.name in self.struct_cached:
                continue
            for f in st.fields:                            # a plain struct holds copies of the settings values nested in a data asset, not the data assets
                if isinstance(f.ty, StructType) and f.ty.is_settings and f.ty.asset_root:
                    d = self.struct_decls.get(st.name)
                    if d is not None:
                        self.focus(d)
                        self.err("DAL4050", f"member '{f.name}' of struct {st.name}: {f.ty.name} is a settings data asset (no other settings type holds it), "
                                            f"a struct can hold only the settings types nested in one", d.name_pos or d.pos)
                    f.ty = ERROR
        for st in order:
            if not st.is_settings or st.name in self.struct_cached:
                continue
            for f in st.fields:
                sub_st = inner(f.ty)
                if sub_st is not None and sub_st.is_settings and sub_st.asset_root and sub_st.origin != st.origin:
                    d = self.struct_decls.get(st.name)
                    if d is not None:
                        self.focus(d)
                        self.err("DAL4052", f"member '{f.name}' of settings {st.name}: {sub_st.name} is a data asset of its library (no settings type there holds it); "
                                            f"hold it in a settings type of the library, or read it through a param", d.name_pos or d.pos)
                    f.ty = ERROR

    def check_derived(self, d: S.StructDecl) -> None:
        """The expressions of `derived` members: they see the other members of the settings (and constants), nothing else."""
        st = self.structs.get(d.name)
        if st is None or self.struct_decls.get(d.name) is not d or d.name in self.struct_cached:
            return
        by_name = {fd.name: fd for fd in d.fields}
        sc = Scope("derived")
        for f in st.fields:
            if not f.derived and f.ty is not ERROR:
                sc.declare(f.name, Local(f.ty, False))
        for f in st.fields:
            fd = by_name.get(f.name)
            if not f.derived or fd is None or fd.init is None or f.ty is ERROR:
                continue
            e = self.ex(fd.init, sc)
            f.expr = self.assignable(f.ty, e, fd.init.pos, f"derived member '{f.name}'")
            sc.declare(f.name, Local(f.ty, False))

    def refresh_settings_defaults(self) -> None:
        """Default instances of nested settings were built before the `derived` expressions were known: build them again."""
        for st in self.module.structs:
            if st.name in self.struct_cached:
                continue
            for f in st.fields:
                if isinstance(f.ty, StructType) and f.ty.is_settings and not f.derived and f.default is not None:
                    if f.init_expr is not None:
                        folded = self.fold(*f.init_expr)                        # built by the declaration: with the same arguments, now that the derived members are known
                        f.default = values.default_of(f.ty) if folded is None else folded
                    else:
                        f.default = values.default_of(f.ty)

    def ensure_defaults(self, st: StructType) -> None:
        """Fold the declared member defaults of a struct (its nested structs first); idempotent."""
        if st.defaults_ready or st.name in self.folding:
            return
        d = self.struct_decls.get(st.name)
        if d is None or d.name in self.struct_cached:
            st.defaults_ready = True
            return
        self.folding.add(st.name)
        saved = self.source
        self.focus(d)
        by_name = {fd.name: fd for fd in d.fields}
        for f in st.fields:
            fd = by_name.get(f.name)
            if f.ty is ERROR or fd is None:
                continue
            if f.derived:
                continue
            if isinstance(f.ty, StructType):
                self.ensure_defaults(f.ty)
            if fd.init is not None and (isinstance(f.ty, (MapType, ArrayType)) or f.ty is CURVE or f.ty is CURVE3):
                self.err("DAL4052", f"member '{f.name}' of {st.kind} {st.name}: a {f.ty} has no initial value in the declaration (it comes from the data)", fd.pos)
                continue
            self.settings_init = st.kind == "settings"                  # the default of a settings member may build a nested settings value
            try:
                f.default = self.const_value(fd.init, f.ty, fd.pos, f"member '{f.name}' of {st.kind} {st.name}", keep=f)
            finally:
                self.settings_init = False
        st.defaults_ready = True
        self.folding.discard(st.name)
        self.source = saved

    # ---- declarations ---------------------------------------------------------------------------------------
    def declare_enum(self, d: S.EnumDecl) -> None:
        if not self.declare_global(d.name, "enum", d.name_pos or d.pos, what="an enum name"):
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
        origin = self.origin_of(d)
        cached = self.ctx.types.get((origin, d.name)) if (self.ctx is not None and origin is not None) else None
        et = cached if cached is not None else EnumType(d.name, members)
        et.origin = origin
        if cached is None and self.ctx is not None and origin is not None:
            self.ctx.types[(origin, d.name)] = et
        self.enums[d.name] = et
        self.user_enums.append(et)
        self.globals[d.name].obj = et

    def storable(self, ty: Type, pos: Pos, what: str, settings_ok: bool = False, snapshot_ok: bool = False, settings_value_ok: bool = False) -> Type:
        """A hit result cannot live in state, inputs, params or constants (Unreal cannot reflect it); neither can curves, tables and lists,
        and a settings value (a read-only data asset) only comes from a param."""
        if ty is HIT:
            self.err("DAL4023", f"{what} cannot have type hit: copy the fields you need (blocking, point, normal, distance, time)", pos)
            return ERROR
        if ty in SPRING_MEMBERS:
            self.err("DAL4023", f"{what} cannot have type {ty}: copy .value and .velocity into states of their own", pos)
            return ERROR
        if (isinstance(ty, AssetType) and ty.settings_only) or isinstance(ty, (MapType, ArrayType)):
            self.err("DAL4058", f"{what} cannot have type {ty}: curves, tables and lists belong in a settings type (read them from the param)", pos)
            return ERROR
        if ty is SNAPSHOT and not snapshot_ok:
            self.err("DAL4062", f"{what} cannot have type snapshot: a pose snapshot is a state (or a member of a struct state) that host.snapshot_pose(State) fills", pos)
            return ERROR
        if isinstance(ty, StructType) and ty.is_settings and not settings_ok:
            if settings_value_ok and not ty.asset_root:
                return ty                                    # a value type nested in a data asset: a state holds a (read-only) copy of one
            what_it_is = ("a data asset: only a param holds it, a state can hold one of the settings types nested in it" if settings_value_ok
                          else "a read-only data asset, it comes from a param (or from a member of another settings)")
            self.err("DAL4058", f"{what} cannot have type {ty}: a settings value is {what_it_is}", pos)
            return ERROR
        return ty

    def declare_const(self, d: S.ConstDecl) -> None:
        ok = self.declare_global(d.name, "const", d.name_pos or d.pos, what="a constant name")
        ty = self.storable(self.resolve_type(d.type_name, d.type_pos), d.type_pos, "a constant")
        sc = Scope("const")
        e = self.ex(d.init, sc)
        e = self.assignable(ty, e, d.init.pos, f"constant '{d.name}'")
        self.pending_consts.discard(d.name)
        if not ok or ty is ERROR or e.ty is ERROR:
            return
        value = self.fold(e, d.init.pos)
        if value is None:
            return
        cd = ir.ConstDef(d.name, ty, value, self.expr_text(d.init), self.origin_of(d))
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

    def zero(self, ty: Type):
        """The default value of a type; a struct's declared member defaults are folded first."""
        if ty is ERROR:
            return 0
        if isinstance(ty, StructType):
            self.ensure_defaults(ty)
        return zero_value(ty)

    def const_value(self, init: S.Expr | None, ty: Type, pos: Pos, what: str, keep: FieldDef | None = None):
        """Value of a ``= init`` clause (or the zero value).  ``keep`` is the member the clause belongs to: it remembers the expression of a settings value."""
        if init is None:
            return self.zero(ty)
        sc = Scope("const")
        e = self.ex(init, sc)
        e = self.assignable(ty, e, init.pos, what)
        if ty is ERROR or e.ty is ERROR:
            return 0
        if keep is not None and isinstance(ty, StructType) and ty.is_settings:
            keep.init_expr = (e, init.pos)
        v = self.fold(e, init.pos)
        return self.zero(ty) if v is None else v

    def declare_param(self, d: S.ParamDecl) -> None:
        ok = self.declare_global(d.name, "param", d.name_pos or d.pos, what="a parameter name")
        ty = self.storable(self.resolve_type(d.type_name, d.type_pos), d.type_pos, "a param", settings_ok=True)
        value = self.const_value(d.init, ty, d.pos, f"param '{d.name}'")
        if ok and ty is not ERROR:
            self.module.params.append(ir.ParamDef(d.name, ty, value))

    def declare_state(self, d: S.StateDecl) -> None:
        ok = self.declare_global(d.name, "state", d.name_pos or d.pos, what="a state name")
        ty = self.storable(self.resolve_type(d.type_name, d.type_pos), d.type_pos, "a state", snapshot_ok=True, settings_value_ok=True)
        value = self.const_value(d.init, ty, d.pos, f"state '{d.name}'")
        if ok and ty is not ERROR:
            self.module.states.append(ir.StateDef(d.name, ty, value))

    def declare_input(self, d: S.InputDecl) -> None:
        ok = self.declare_global(d.name, "input", d.name_pos or d.pos, what="an input name")
        ty = self.storable(self.resolve_type(d.type_name, d.type_pos), d.type_pos, "an input")
        host = None
        peer = None
        value = None
        if d.peer is not None:
            peer = ir.PeerUse(d.peer.peer, tuple(d.peer.path)) if self.interface_only else self.check_peer_binding(d, ty)
            value = self.zero(ty)
        elif d.binding is not None:
            host = self.check_binding(d, ty)
            value = self.zero(ty)
        else:
            value = self.const_value(d.default, ty, d.pos, f"input '{d.name}'")
        if ok and ty is not ERROR:
            self.module.inputs.append(ir.InputDef(d.name, ty, host, value, peer))

    def check_binding(self, d: S.InputDecl, ty: Type) -> ir.HostUse | None:
        b = d.binding
        assert b is not None
        item = HOST_ITEMS.get(b.item)
        if item is None:
            self.err("DAL3020", f"unknown host item 'host.{b.item}'" + self.suggest(b.item, HOST_ITEMS), b.pos)
            return None
        if item.arg and b.arg is None:
            what = "a declared montage" if item.arg_kind == "montage" else 'a quoted name, e.g. host.%s("Name")' % item.name
            self.err("DAL3021", f"host.{item.name} needs an argument: {what}", b.pos)
            return None
        if not item.arg and b.arg is not None:
            self.err("DAL3021", f"host.{item.name} takes no argument", b.arg_pos or b.pos)
            return None
        if item.arg and item.nargs == 2 and b.arg2 is None:
            self.err("DAL3021", f"host.{item.name} takes two names: host.{item.name}(\"Name\", \"Name\")", b.arg_pos or b.pos)
            return None
        if item.arg and item.nargs == 1 and b.arg2 is not None:
            self.err("DAL3021", f"host.{item.name} takes one name", b.arg2_pos or b.pos)
            return None
        if item.arg and item.nargs == 2:
            for nm in (b.arg, b.arg2):
                if not nm or not nm.replace("_", "a").isalnum() or nm[0].isdigit():
                    self.err("DAL3022", f"host argument {nm!r} must be an identifier (letters, digits, '_')", b.arg_pos or b.pos)
                    return None
        if item.arg:
            apos = b.arg_pos or b.pos
            if item.arg_kind == "montage":
                sym = self.globals.get(b.arg or "")
                if not b.arg_is_ident or sym is None or sym.kind != "montage":
                    self.err("DAL3023", f"host.{item.name} takes a declared montage (montage Name;), not {b.arg!r}", apos)
                    return None
            elif item.arg_kind == "action" and b.arg_is_ident:
                sym = self.globals.get(b.arg or "")
                if sym is None or sym.kind != "action":
                    self.err("DAL3023", f"unknown action '{b.arg}' (declare it: action {b.arg}: type;)", apos)
                    return None
            elif b.arg_is_ident:
                self.err("DAL3023", f"host.{item.name} takes a quoted name, e.g. host.{item.name}(\"{b.arg}\")", apos)
                return None
            elif not b.arg or not b.arg.replace("_", "a").isalnum() or b.arg[0].isdigit():
                self.err("DAL3022", f"host argument {b.arg!r} must be an identifier (letters, digits, '_')", apos)
                return None
        if ty is ERROR:
            return None
        if item.section:
            info = self.globals[b.arg].obj      # type: ignore[index]
            if ty is not info.enum:
                self.err("DAL4020", f"host.{item.name}({b.arg}) has type {info.enum.name}, but input '{d.name}' is declared {ty}", d.type_pos)
                return None
        elif item.ty is not None:
            if ty is not item.ty:
                self.err("DAL4020", f"host.{item.name} has type {item.ty}, but input '{d.name}' is declared {ty}", d.type_pos)
                return None
        elif ty not in item.allowed:
            allowed = ", ".join(t.name for t in item.allowed)
            self.err("DAL4020", f"host.{item.name} can be read as {allowed}, not {ty}", d.type_pos)
            return None
        if item.name == "action":
            self.use_action(b.arg or "", ty, b.arg_pos or b.pos)
        return ir.HostUse(b.item, b.arg, b.arg2)

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
        self.declare_global(d.name, "fn", d.name_pos or d.pos, what="a function name")
        params = self.declare_params(d.params)
        ret = self.resolve_type(d.ret_name, d.ret_pos)
        if params is not None:
            self.fn_sigs[d.name] = (params, ret)
        self.fn_pos[d.name] = d.pos

    def declare_extern(self, d: S.ExternDecl) -> None:
        self.declare_global(d.name, "extern", d.name_pos or d.pos, what="a function name")
        params = self.declare_params(d.params)
        ret = self.resolve_type(d.ret_name, d.ret_pos)
        if params is None:
            return
        self.extern_sigs[d.name] = (params, ret)
        self.module.externs.append(ir.ExternDef(d.name, params, ret, d.cpp or d.name, d.include, d.py or d.name, self.origin_of(d)))

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
        self.module.funcs.append(ir.FnDef(d.name, params, ret, body, self.origin_of(d)))

    def check_update(self, d: S.UpdateDecl, phase: str = "pre_movement") -> ir.UpdateDef:
        sc = Scope("update")
        if not self.valid_ident(d.dt_name, d.dt_pos, "the delta-time parameter"):
            pass
        elif d.dt_name in self.globals:
            self.err("DAL3005", f"'{d.dt_name}' hides the {self.globals[d.dt_name].kind} of the same name", d.dt_pos)
        sc.declare(d.dt_name, Local(FLOAT, False))
        pre = self.block(d.pre, sc)
        return ir.UpdateDef(d.dt_name, self.block(d.body, sc), pre, phase)

    def declare_entry_sig(self, d: S.EntryDecl) -> None:
        """The typed parameters of an entry: `entry Name(dt, A: float = 1.0, B: sequence)`."""
        self.declare_global(d.name, "entry", d.name_pos or d.pos, what="an entry name")
        if d.linked and self.kind != "anim":
            self.err("DAL3014", "only an animation logic has linked entries: a linked instance forwards them", d.name_pos or d.pos)
        params: list[ir.EntryParam] = []
        seen = {d.dt_name} if d.dt_name else set()
        has_default = False
        for p in d.params:
            ty = self.resolve_type(p.type_name, p.type_pos)
            if ty is not ERROR:
                ty = self.storable(ty, p.type_pos, "an entry parameter")
            if not self.valid_ident(p.name, p.pos, "a parameter name"):
                pass
            elif p.name in seen:
                self.err("DAL3001", f"parameter '{p.name}' is declared twice", p.pos)
            elif p.name in self.globals:
                self.err("DAL3005", f"parameter '{p.name}' hides the {self.globals[p.name].kind} of the same name", p.pos)
            seen.add(p.name)
            value, given = None, p.init is not None
            if given:
                value = self.const_value(p.init, ty, p.pos, f"parameter '{p.name}'")
                has_default = True
            elif has_default:
                self.err("DAL3013", f"parameter '{p.name}' needs a default value: it follows a parameter that has one", p.pos)
            params.append(ir.EntryParam(p.name, ty, value, given))
        self.entry_sigs[id(d)] = params

    def declare_linked(self, d: S.LogicDecl) -> None:
        """`logic X : anim linked(Main);` -- X forwards the `linked` entries of Main (found as Main.dal next to this file)."""
        if self.interface_only:
            return
        pos = d.linked_pos or d.pos
        if self.ctx is None:
            self.err("DAL3078", "the main logic is found through the compile context (use dal.check_file / dal.check_source)", pos)
            return
        if d.linked == d.name:
            self.err("DAL3078", "a logic cannot be linked to itself", pos)
            return
        try:
            iface = peermod.load_interface(self.ctx, self.ctx.main_key, d.linked)
        except FileNotFoundError:
            self.err("DAL3078", f"linked({d.linked}): there is no {d.linked}.dal next to this file", pos)
            return
        except DalError as e:
            self.diags.extend(e.diagnostics)
            return
        if iface.logic != d.linked:
            self.err("DAL3078", f"{d.linked}.dal declares the logic '{iface.logic}', not '{d.linked}'", pos)
            return
        if iface.kind != "anim":
            self.err("DAL3078", f"linked({d.linked}): {d.linked} is an actor logic; a linked instance belongs to an animation logic", pos)
            return
        forwarded = [e for e in iface.entries.values() if e.linked]
        if not forwarded:
            self.warn("DAL5092", f"{d.linked} has no 'linked' entries: there is nothing to forward", pos)
        for e in forwarded:
            self.declare_global(e.name, "entry", pos, what=f"the entry '{e.name}' forwarded from {d.linked}")
        self.module.linked = ir.LinkedDef(d.linked, [ir.EntryDef(e.name, None, [], list(e.params), True) for e in forwarded])

    def check_entry(self, d: S.EntryDecl) -> ir.EntryDef:
        sc = Scope("entry")
        if d.dt_name is not None:
            if not self.valid_ident(d.dt_name, d.dt_pos, "the delta-time parameter"):
                pass
            elif d.dt_name in self.globals:
                self.err("DAL3005", f"'{d.dt_name}' hides the {self.globals[d.dt_name].kind} of the same name", d.dt_pos)
            sc.declare(d.dt_name, Local(FLOAT, False))
        params = self.entry_sigs.get(id(d), [])
        for p in params:
            if sc.lookup(p.name) is None and p.name not in self.globals:
                sc.declare(p.name, Local(p.ty, False))
        return ir.EntryDef(d.name, d.dt_name, self.block(d.body, sc), params, d.linked)

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
        what = "return"
        for s in stmts:
            if terminated:
                self.warn("DAL5090", f"unreachable code after '{what}'", s.pos)
                terminated = False
            r = self.stmt(s, sc)
            if r is not None:
                out.append(r)
                if isinstance(r, (ir.Return, ir.Break)):
                    terminated = True
                    what = "return" if isinstance(r, ir.Return) else "break"
        sc.pop()
        return out

    def stmt(self, s: S.Stmt, sc: Scope) -> ir.Stmt | None:
        if isinstance(s, S.LetStmt):
            return self.let_stmt(s, sc)
        if isinstance(s, S.AssignStmt):
            return self.assign_stmt(s, sc)
        if isinstance(s, S.MemberAssignStmt):
            return self.member_assign_stmt(s, sc)
        if isinstance(s, S.IfStmt):
            c = self.ex(s.cond, sc)
            if c.ty is not BOOL and c.ty is not ERROR:
                self.err("DAL4003", f"the condition of 'if' must be bool, not {c.ty}", s.cond.pos)
            return ir.If(c, self.block(s.then, sc), self.block(s.orelse, sc))
        if isinstance(s, S.ReturnStmt):
            return self.return_stmt(s, sc)
        if isinstance(s, S.CmdStmt):
            return self.cmd_stmt(s, sc)
        if isinstance(s, S.ForStmt):
            return self.for_stmt(s, sc)
        if isinstance(s, S.WhileStmt):
            return self.while_stmt(s, sc)
        if isinstance(s, S.BreakStmt):
            if self.loop_depth == 0:
                self.err("DAL5053", "'break' leaves a loop: it belongs in the body of a 'for' or a 'while'", s.pos)
                return None
            return ir.Break()
        if isinstance(s, S.CallStmt):
            return self.call_stmt(s, sc)
        if isinstance(s, S.ExprStmt):
            return self.expr_stmt(s, sc)
        raise AssertionError(s)

    def call_stmt(self, s: S.CallStmt, sc: Scope) -> ir.Stmt | None:
        """`name(args);` -- a method called for what it does.  (The timer / window / buffer forms are gone after lowering.)"""
        sym = self.globals.get(s.callee)
        if sym is not None and sym.kind == "method" and sc.lookup(s.callee) is None:
            call = S.CallExpr(s.pos, s.callee, s.args)
            self.stmt_calls.add(id(call))
            r = self.ex(call, sc)
            return None if r.ty is ERROR else ir.ExprStmt(r)
        if sym is not None and sym.kind == "view" and sc.lookup(s.callee) is None:
            self.err("DAL2011", f"'{s.callee}(...);' is a view: it returns a value and changes nothing, use it in an expression", s.pos)
            return None
        self.err("DAL2011", f"'{s.callee}(...);' is not a statement", s.pos)
        return None

    def expr_stmt(self, s: S.ExprStmt, sc: Scope) -> ir.Stmt | None:
        self.stmt_calls.add(id(s.expr))
        r = self.ex(s.expr, sc)
        return None if r.ty is ERROR else ir.ExprStmt(r)

    def mark_value_call(self, e: S.Expr) -> None:
        """The whole right-hand side of `let`, `=` and `return` may be a call that produces a value."""
        if isinstance(e, (S.CallExpr, S.MemberCallExpr)):
            self.value_calls.add(id(e))

    def let_stmt(self, s: S.LetStmt, sc: Scope) -> ir.Stmt | None:
        self.mark_value_call(s.init)
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
        if ty is NONE_T:
            self.err("DAL4002", f"'{s.name}' needs a type: none is the empty value of a sequence or montage; write let {s.name}: sequence = none;", s.init.pos)
            return None
        if s.mutable and ((isinstance(ty, StructType) and ty.is_settings) or isinstance(ty, (MapType, ArrayType)) or (isinstance(ty, AssetType) and ty.settings_only)):
            self.err("DAL5004", f"'{s.name}' cannot be a var: a {ty} is read-only data; declare it with let", name_pos)
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
            if sym is None and s.name in self.invalid_names:
                self.ex(s.value, sc)
                return None
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
            if sc.ctx == "view":
                self.err("DAL5044", "a view only reads: it cannot write state; use a method", name_pos)
                self.ex(s.value, sc)
                return None
            kind = "state"
            st = self.module.find_state(s.name)
            target_ty = st.ty if st else ERROR
        if s.op == "=":
            self.mark_value_call(s.value)
        value = self.ex(s.value, sc)
        if s.op != "=":
            cur = ir.LocalRef(target_ty, s.name) if kind == "local" else ir.StateRef(target_ty, s.name)
            value = self.binary(s.op[0], cur, value, s.pos, name_pos)
        value = self.assignable(target_ty, value, s.value.pos, f"'{s.name}'")
        return ir.Assign(kind, s.name, value)

    def member_assign_stmt(self, s: S.MemberAssignStmt, sc: Scope) -> ir.Stmt | None:
        """`S.a.b = v;` -- the target is a struct state or a `var` whose members are followed down the path."""
        name_pos = s.name_pos or s.pos
        loc = sc.lookup(s.name)
        if loc is not None:
            if not loc.mutable:
                self.err("DAL5001", f"'{s.name}' is not assignable: declare it with 'var' to change it later", name_pos)
                self.ex(s.value, sc)
                return None
            kind, base_ty = "local", loc.ty
        else:
            sym = self.globals.get(s.name)
            if sym is None and s.name in self.invalid_names:
                self.ex(s.value, sc)
                return None
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
            if sc.ctx == "view":
                self.err("DAL5044", "a view only reads: it cannot write state; use a method", name_pos)
                self.ex(s.value, sc)
                return None
            kind = "state"
            st = self.module.find_state(s.name)
            base_ty = st.ty if st else ERROR
        cur_ty = base_ty
        component = None                     # (index in the path, name) when the target is a component of a vector
        for i, member in enumerate(s.path):
            pos = s.path_pos[i] if i < len(s.path_pos) else name_pos
            if cur_ty is ERROR:
                self.ex(s.value, sc)
                return None
            if cur_ty in COMPONENTS:
                names = COMPONENTS[cur_ty][0]
                if i != len(s.path) - 1:
                    self.err("DAL4030", f"a {cur_ty} component is a float and has no members", s.path_pos[i + 1] if i + 1 < len(s.path_pos) else pos)
                elif member in names:
                    component = (i, (member,))
                elif len(member) > 1 and all(c in "xyzw" for c in member) or member in ("xy", "xz", "yz", "xyz"):
                    self.err("DAL2010", f"cannot assign to a swizzle ({member}); assign the components one at a time: v.{member[0]} = ...", pos)
                else:
                    self.err("DAL4030", f"{cur_ty} has no component '{member}' (components: {', '.join(names)})", pos)
                if component is None:
                    self.ex(s.value, sc)
                    return None
                break
            if cur_ty is TRANSFORM:
                rest = s.path[i:]
                if member not in TRANSFORM_MEMBERS:
                    self.err("DAL4030", f"transform has no member '{member}' (members: {', '.join(TRANSFORM_MEMBERS)})", pos)
                elif len(rest) == 1:
                    component = (i, (member,))
                elif len(rest) == 2 and _HIT_TYPES[TRANSFORM_MEMBERS[member]] in COMPONENTS and rest[1] in COMPONENTS[_HIT_TYPES[TRANSFORM_MEMBERS[member]]][0]:
                    component = (i, tuple(rest))
                else:
                    self.err("DAL4030", f"cannot assign to .{'.'.join(rest)}: assign a whole member of the transform, or one component of its location / rotation / scale",
                             s.path_pos[i + 1] if i + 1 < len(s.path_pos) else pos)
                if component is None:
                    self.ex(s.value, sc)
                    return None
                break
            if not isinstance(cur_ty, StructType):
                self.err("DAL4030", f"type {cur_ty} has no members", pos)
                self.ex(s.value, sc)
                return None
            if cur_ty.is_settings:
                self.err("DAL5004", f"{cur_ty.name} is a settings value and read-only; copy what you need into a state", pos)
                self.ex(s.value, sc)
                return None
            fd = cur_ty.field(member)
            if fd is None:
                self.err("DAL4030", f"{cur_ty.kind} {cur_ty.name} has no member '{member}'" + self.suggest(member, [x.name for x in cur_ty.fields])
                         + f" (members: {', '.join(x.name for x in cur_ty.fields) or 'none'})", pos)
                self.ex(s.value, sc)
                return None
            cur_ty = fd.ty
        if component is not None:
            return self.component_assign(s, sc, kind, base_ty, cur_ty, component, name_pos)
        if s.op == "=":
            self.mark_value_call(s.value)
        value = self.ex(s.value, sc)
        if s.op != "=":
            base_ref = ir.LocalRef(base_ty, s.name) if kind == "local" else ir.StateRef(base_ty, s.name)
            cur: ir.Expr = base_ref
            walk_ty = base_ty
            for member in s.path:
                walk_ty = walk_ty.field(member).ty        # type: ignore[union-attr]
                cur = ir.Member(walk_ty, cur, member)
            value = self.binary(s.op[0], cur, value, s.pos, name_pos)
        value = self.assignable(cur_ty, value, s.value.pos, f"'{s.name}.{'.'.join(s.path)}'")
        return ir.AssignField(kind, s.name, tuple(s.path), value)

    def component_assign(self, s: S.MemberAssignStmt, sc: Scope, kind: str, base_ty: Type, vec_ty: Type, component: tuple[int, tuple[str, ...]], name_pos: Pos) -> ir.Stmt | None:
        """`v.z = x;`, `S.Rotation.yaw += d;`, `T.location = p;` -- the vector or transform is rebuilt with the one part replaced."""
        index, rest = component
        place = tuple(s.path[:index])
        target: ir.Expr = ir.LocalRef(base_ty, s.name) if kind == "local" else ir.StateRef(base_ty, s.name)
        walk_ty = base_ty
        for member in place:
            walk_ty = walk_ty.field(member).ty         # type: ignore[union-attr]
            target = ir.Member(walk_ty, target, member)
        leaf: ir.Expr = target                          # the part being replaced, as it reads now
        leaf_ty = walk_ty
        for part in rest:
            leaf_ty = FLOAT if leaf_ty in COMPONENTS else _HIT_TYPES[TRANSFORM_MEMBERS[part]]
            leaf = ir.Member(leaf_ty, leaf, part)
        if s.op == "=":
            self.mark_value_call(s.value)
        value = self.ex(s.value, sc)
        label = ".".join([s.name, *s.path])
        if s.op != "=":
            value = self.binary(s.op[0], leaf, value, s.pos, name_pos)
        value = self.assignable(leaf_ty, value, s.value.pos, f"'{label}'")
        rebuilt = self.rebuild(target, vec_ty, rest, value)
        if not place:
            return ir.Assign(kind, s.name, rebuilt)
        return ir.AssignField(kind, s.name, place, rebuilt)

    def rebuild(self, expr: ir.Expr, ty: Type, rest: tuple[str, ...], new: ir.Expr) -> ir.Expr:
        """`expr` (a vector or a transform) with the part named by `rest` replaced by `new`."""
        if ty is TRANSFORM:
            member = rest[0]
            member_ty = _HIT_TYPES[TRANSFORM_MEMBERS[member]]
            inner = new if len(rest) == 1 else self.rebuild(ir.Member(member_ty, expr, member), member_ty, rest[1:], new)
            args = [inner if n == member else ir.Member(_HIT_TYPES[t], expr, n) for n, t in TRANSFORM_MEMBERS.items()]
            return ir.Make(TRANSFORM, "transform", args, 1)
        names, ctor = COMPONENTS[ty]
        args = [new if c == rest[0] else ir.Member(FLOAT, expr, c) for c in names]
        return ir.Make(ty, ctor, args, 0)

    def return_stmt(self, s: S.ReturnStmt, sc: Scope) -> ir.Stmt:
        if sc.ctx in ("fn", "view") or (sc.ctx == "method" and sc.ret is not None):
            if s.value is None:
                what = "fn" if sc.ctx == "fn" else sc.ctx
                self.err("DAL5020", f"{what} '{sc.fn_name}' must return a {sc.ret}", s.pos)
                return ir.Return(None)
            self.mark_value_call(s.value)
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
        if isinstance(e, S.MemberCallExpr):
            return self.member_call(e, sc)
        if isinstance(e, S.IndexExpr):
            return self.index_expr(e, sc)
        if isinstance(e, S.UnaryExpr):
            return self.unary(e, sc)
        if isinstance(e, S.BinaryExpr):
            a = self.ex(e.left, sc)
            b = self.ex(e.right, sc)
            return self.binary(e.op, a, b, e.pos, e.op_pos or e.pos)
        if isinstance(e, S.CondExpr):
            return self.cond(e, sc)
        if isinstance(e, S.HostCallExpr):
            return self.host_query(e, sc)
        if isinstance(e, S.StringLit):
            return self.bad("DAL4061", "a string is only the slot name argument of a host command: host.stop_montages_with_slot(\"Transition\")", e.pos)
        raise AssertionError(e)

    def name_ref(self, e: S.NameRef, sc: Scope) -> ir.Expr:
        name = e.name
        loc = sc.lookup(name)
        if loc is not None:
            if isinstance(loc.ty, StructType) and loc.ty.is_notify and not self.allow_settings_param:
                return self.bad("DAL4060", f"'{name}' is the animation notify that raised the event: read its members ({name}.Member), it cannot be copied or passed on", e.pos)
            return ir.LocalRef(loc.ty, name)
        if name in self.invalid_names:
            return ir.Lit(ERROR, 0)
        if name in BUILTIN_CONSTS:
            c = BUILTIN_CONSTS[name]
            return ir.ConstRef(c.ty, name, c.value)
        sym = self.globals.get(name)
        if sym is None and name == "none":
            return ir.Lit(NONE_T, None)
        if sym is None:
            if name in self.pending_consts:
                return self.bad("DAL3042", f"constant '{name}' is used before it is defined (constants are evaluated in order)", e.pos)
            if sc.ctx == "const" and name in self.later:
                return self.bad("DAL5002", f"a constant expression cannot read {self.later[name]} '{name}'", e.pos)
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
            base_of_member = self.allow_settings_param
            self.allow_settings_param = False
            if sc.ctx in ("const", "derived"):
                return self.bad("DAL5002", f"a constant expression cannot read {k} '{name}'" if sc.ctx == "const"
                                else f"a derived member is computed from the other members of its settings and constants, not from {k} '{name}'", e.pos)
            if sc.ctx == "fn":
                return self.bad(
                    "DAL5011",
                    f"fn bodies are pure and cannot read {k} '{name}'; pass it as an argument",
                    e.pos,
                )
            ty = self.var_type(k, name)
            if k == "param" and isinstance(ty, StructType) and ty.is_settings and not base_of_member:
                return self.bad("DAL4059", f"settings param '{name}' is read through its members ({name}.Member...); it is a data asset, not a value", e.pos)
            return {"param": ir.ParamRef, "input": ir.InputRef, "state": ir.StateRef}[k](ty, name)
        if k == "enum":
            return self.bad("DAL3030", f"enum '{name}' is a type; write {name}.<Member> to name a value", e.pos)
        if k == "struct":
            return self.bad("DAL3030", f"{self.structs[name].kind} '{name}' is a type; write {name}(...) to build a value", e.pos)
        if k == "tags":
            return self.bad("DAL3030", f"tag set '{name}' is a type; write {name}.<Tag> to name a tag", e.pos)
        if k == "montage":
            return self.bad("DAL3030", f"montage '{name}' is not a value: write {name}.playing, {name}.position, {name}.section or "
                                       f"{name}.<Section>, or pass it to a host.montage_* command", e.pos)
        if k == "action":
            return self.bad("DAL3030", f"action '{name}' is not a value: bind it to an input (input X: type <- host.action({name});) "
                                       f"or handle it with 'on input {name} started'", e.pos)
        if k == "notify":
            return self.bad("DAL3030", f"notify '{name}' is not a value: handle it with 'on notify {name} {{ ... payload.Member ... }}'", e.pos)
        if k in ("method", "view"):
            return self.bad("DAL3031", f"'{name}' is a {k}; call it as {name}(...)", e.pos)
        if k == "peer":
            return self.bad("DAL3030", f"peer '{name}' is not a value: read its state through an input (input X: T <- {name}.State;) or call its methods ({name}.Method(...))", e.pos)
        if k in ("fn", "extern", "entry"):
            return self.bad("DAL3031", f"'{name}' is a {'function' if k != 'entry' else 'entry'}; " + (f"call it as {name}(...)" if k != "entry" else "entries are called by the animation graph, not from code"), e.pos)
        return self.bad("DAL3040", f"'{name}' cannot be used as a value", e.pos)

    def var_type(self, kind: str, name: str) -> Type:
        coll = {"param": self.module.params, "input": self.module.inputs, "state": self.module.states}[kind]
        for d in coll:
            if d.name == name:
                return d.ty
        return ERROR

    def montage_member(self, info: ir.MontageDef, e: S.MemberRef, sc: Scope) -> ir.Expr:
        f = e.field
        if f in info.sections:
            return ir.EnumLit(info.enum, f)
        if f not in _MONTAGE_STATE and f != "section":
            return self.bad("DAL3032", f"montage {info.name} has no member '{f}'" + self.suggest(f, list(_MONTAGE_STATE) + ["section"] + info.sections)
                            + f" (state: playing, active, position, section; sections: {', '.join(info.sections) or 'none declared'})", e.field_pos)
        if sc.ctx in ("fn", "const"):
            return self.bad("DAL5011" if sc.ctx == "fn" else "DAL5002",
                            f"{'fn bodies are pure and' if sc.ctx == 'fn' else 'a constant expression'} cannot read the state of montage {info.name}", e.pos)
        ty = info.enum if f == "section" else _MONTAGE_STATE[f]
        name = f"DalMontage_{info.name}_{f}"
        if self.module.find_input(name) is None:
            self.module.inputs.append(ir.InputDef(name, ty, ir.HostUse(f"montage_{f}", info.name), zero_value(ty)))
        return ir.InputRef(ty, name)

    def member_ref(self, e: S.MemberRef, sc: Scope) -> ir.Expr:
        base = e.base
        if isinstance(base, S.NameRef) and sc.lookup(base.name) is None:
            sym = self.globals.get(base.name)
            if sym is not None and sym.kind == "montage":
                return self.montage_member(sym.obj, e, sc)    # type: ignore[arg-type]
            tt = self.tagsets.get(base.name)
            if tt is not None and sym is not None and sym.kind == "tags":
                if e.field == "None" or e.field in tt.members:
                    return ir.Lit(tt, tt.full_name(e.field))
                return self.bad(
                    "DAL3032",
                    f"tag set {tt.name} has no tag '{e.field}'" + self.suggest(e.field, tt.members)
                    + f" (tags: {', '.join(tt.members)}, None)",
                    e.field_pos,
                )
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
        self.allow_settings_param = isinstance(base, S.NameRef)
        b = self.ex(base, sc)
        self.allow_settings_param = False
        if b.ty is ERROR:
            return b
        f = e.field
        if isinstance(b.ty, ArrayType):
            if f == "len":
                return ir.ArrayLen(INT, b)
            return self.bad("DAL4030", f"a list has no member '{f}' (members: len)", e.field_pos)
        if b.ty is HIT:
            if f in HIT_MEMBERS:
                return ir.Member(_HIT_TYPES[HIT_MEMBERS[f]], b, f)
            return self.bad("DAL4030", f"hit has no member '{f}' (members: {', '.join(HIT_MEMBERS)})", e.field_pos)
        if b.ty in SPRING_MEMBERS:
            members = SPRING_MEMBERS[b.ty]
            if f in members:
                return ir.Member(_HIT_TYPES[members[f]], b, f)
            return self.bad("DAL4030", f"{b.ty} has no member '{f}' (members: {', '.join(members)})", e.field_pos)
        if b.ty in (VEC2, VEC3):
            dim = 2 if b.ty is VEC2 else 3
            if not f or any(c not in "xyz" for c in f) or len(f) > 3:
                return self.bad("DAL4030", f"{b.ty} has no member '{f}' (components: x, y{', z' if dim == 3 else ''}; swizzles such as .xy are allowed)", e.field_pos)
            if any(_SWIZZLE[c] >= dim for c in f):
                return self.bad("DAL4030", f"{b.ty} has no component '{[c for c in f if _SWIZZLE[c] >= dim][0]}'", e.field_pos)
            ty = {1: FLOAT, 2: VEC2, 3: VEC3}[len(f)]
            return ir.Member(ty, b, f)
        if isinstance(b.ty, StructType):
            fd = b.ty.field(f)
            if fd is None:
                return self.bad("DAL4030", f"{b.ty.kind} {b.ty.name} has no member '{f}'" + self.suggest(f, [x.name for x in b.ty.fields])
                                + f" (members: {', '.join(x.name for x in b.ty.fields) or 'none'})", e.field_pos)
            return ir.Member(fd.ty, b, f)
        if b.ty is ROTATOR:
            if f in ("pitch", "yaw", "roll"):
                return ir.Member(FLOAT, b, f)
            return self.bad("DAL4030", f"rotator has no member '{f}' (members: pitch, yaw, roll)", e.field_pos)
        if b.ty is QUAT:
            if f in ("x", "y", "z", "w"):
                return ir.Member(FLOAT, b, f)
            return self.bad("DAL4030", f"quat has no member '{f}' (members: x, y, z, w)", e.field_pos)
        if b.ty is TRANSFORM:
            if f in TRANSFORM_MEMBERS:
                return ir.Member(_HIT_TYPES[TRANSFORM_MEMBERS[f]], b, f)
            return self.bad("DAL4030", f"transform has no member '{f}' (members: {', '.join(TRANSFORM_MEMBERS)})", e.field_pos)
        return self.bad("DAL4030", f"type {b.ty} has no members", e.field_pos)

    # ---- v1 / v2: montages, actions, handlers, commands, queries, loops --------------------------------------------------
    def declare_montage(self, d: S.MontageDecl) -> None:
        ok = self.declare_global(d.name, "montage", d.name_pos or d.pos, what="a montage name")
        members = ["None"]
        seen: set[str] = set()
        for sec, spos in d.sections:
            if sec in seen:
                self.err("DAL3001", f"section '{sec}' is declared twice in montage {d.name}", spos)
            elif sec in _MONTAGE_STATE or sec == "section":
                self.err("DAL3059", f"'{sec}' cannot be a section name: {d.name}.{sec} reads the montage state", spos)
            elif sec == "None":
                self.err("DAL3059", "'None' is the implicit 'no section' member", spos)
            elif sec in CPP_KEYWORDS:
                self.err("DAL3004", f"'{sec}' cannot be a section name: it is a C++ keyword", spos)
            else:
                members.append(sec)
            seen.add(sec)
        enum_name = f"{d.name}Section"
        et = EnumType(enum_name, members)
        if ok and self.declare_global(enum_name, "enum", d.name_pos or d.pos, et, what="the section enum of a montage"):
            self.enums[enum_name] = et
            self.user_enums.append(et)
        mdef = ir.MontageDef(d.name, members[1:], et)
        if ok:
            self.module.montages.append(mdef)
            self.globals[d.name].obj = mdef

    def declare_action(self, d: S.ActionDecl) -> None:
        ok = self.declare_global(d.name, "action", d.name_pos or d.pos, what="an action name")
        ty = self.resolve_type(d.type_name, d.type_pos)
        if ty is not ERROR and ty not in _ACTION_TYPES:
            self.err("DAL4021", f"an action has type bool, float, vec2 or vec3, not {ty}", d.type_pos)
            ty = ERROR
        if ok and ty is not ERROR:
            adef = ir.ActionDef(d.name, ty)
            self.module.actions.append(adef)
            self.globals[d.name].obj = adef

    def use_action(self, name: str, ty: Type, pos: Pos) -> None:
        """host.action("Name") / host.action(Name): register the action, or check that its type agrees."""
        existing = self.module.find_action(name)
        if existing is None:
            self.module.actions.append(ir.ActionDef(name, ty))
        elif existing.ty is not ty:
            self.err("DAL4022", f"action '{name}' is read as {ty} here but as {existing.ty} elsewhere", pos)

    def check_handler(self, d: S.HandlerDecl) -> ir.HandlerDef | None:
        pos = d.target_pos or d.pos
        params: list[tuple[str, Type]] = []
        if d.event in ("begin_play", "end_play"):
            sc = Scope("handler")
            return ir.HandlerDef(d.event, "", None, [], self.block(d.body, sc))
        if d.event in HOOK_EVENTS:
            return self.check_hook_handler(d)
        if d.event in ("notify", "notify_begin", "notify_end", "notify_tick"):
            nd = self.notifies.get(d.name)
            if nd is not None:
                if nd.state != (d.event != "notify"):
                    if nd.state:
                        self.err("DAL3064", f"'{d.name}' is a notify state (a notify with a duration): handle it with on notify_begin / notify_tick / notify_end, not on {d.event}", pos)
                    else:
                        self.err("DAL3064", f"'{d.name}' is a notify (it has no duration): handle it with on notify, not on {d.event}", pos)
                    return None
                params = [("payload", nd.struct)]
            elif d.event == "notify_tick":
                sym = self.globals.get(d.name)
                what = f"'{d.name}' is a {sym.kind}" if sym is not None else f"there is no notify state '{d.name}'"
                self.err("DAL3065", f"{what}: on notify_tick needs a declared notify state (notify_state {d.name} {{ ... }})", pos)
                return None
        if d.event in ("montage_ended", "montage_blending_out"):
            sym = self.globals.get(d.name)
            if sym is None or sym.kind != "montage":
                self.err("DAL3061", f"unknown montage '{d.name}'" + self.suggest(d.name, [k for k, v in self.globals.items() if v.kind == "montage"])
                         + " (declare it: montage Name;)", pos)
                return None
            params = [("interrupted", BOOL)]
        elif d.event == "input":
            if self.kind != "actor":
                self.err("DAL5040", "input events are only available in actor logic (logic Name : actor;): an anim instance has no input component", d.pos)
                return None
            adef = self.module.find_action(d.name)
            if adef is None:
                self.err("DAL3062", f"unknown action '{d.name}' (declare it: action {d.name}: bool;)", pos)
                return None
            params = [("value", adef.ty)]
        sc = Scope("handler")
        for n, t in params:
            if n in self.globals:
                what = {"interrupted": "interrupted flag", "payload": "animation notify"}.get(n, "action value")
                self.err("DAL3005", f"'{n}' is the implicit parameter of this handler ({what}) "
                                    f"and also the name of the {self.globals[n].kind}; rename the {self.globals[n].kind}", pos)
                continue
            sc.declare(n, Local(t, False))
        declared = self.notifies.get(d.name) if d.event in ("notify", "notify_begin", "notify_end", "notify_tick") else None
        sc.notify_handler = declared is not None and declared.branching       # only a branching notify knows its montage instance
        body = self.block(d.body, sc)
        return ir.HandlerDef(d.event, d.name, d.phase, params, body)

    def check_hook_handler(self, d: S.HandlerDecl) -> ir.HandlerDef | None:
        """`on movement_mode_changed(prev, cur) { ... }`, `on jumped { ... }`: events of the character without a name; the
        parameters (named by the program) carry the payload, or the list is left out when the handler does not need it."""
        payload = HOOK_EVENTS[d.event]
        sc = Scope("handler")
        params: list[tuple[str, Type]] = []
        if d.param_names:
            if len(d.param_names) != len(payload):
                want = f"{len(payload)} parameter(s) ({', '.join(t.name for t in payload)})" if payload else "no parameters"
                self.err("DAL4041", f"on {d.event} takes {want}, got {len(d.param_names)}", d.param_names[0][1])
                return None
            seen: set[str] = set()
            for (pname, ppos), ty in zip(d.param_names, payload):
                if not self.valid_ident(pname, ppos, "a parameter name"):
                    continue
                if pname in seen:
                    self.err("DAL3001", f"parameter '{pname}' is declared twice", ppos)
                    continue
                if pname in self.globals:
                    self.err("DAL3005", f"parameter '{pname}' hides the {self.globals[pname].kind} of the same name", ppos)
                    continue
                seen.add(pname)
                sc.declare(pname, Local(ty, False))
                params.append((pname, ty))
        return ir.HandlerDef(d.event, "", None, params, self.block(d.body, sc))

    def cmd_stmt(self, s: S.CmdStmt, sc: Scope) -> ir.Stmt | None:
        pos = s.name_pos or s.pos
        if sc.ctx in ("fn", "const"):
            self.err("DAL5042", "host commands are not allowed in functions (functions are pure); issue the command from update, an entry, a method or a handler", pos)
            return None
        if sc.ctx == "view":
            self.err("DAL5044", "a view only reads: it cannot issue host commands; use a method", pos)
            return None
        cmd = COMMANDS.get(s.name)
        if cmd is None:
            self.err("DAL3060", f"unknown host command 'host.{s.name}'" + self.suggest(s.name, COMMANDS), pos)
            for a in s.args:
                self.ex(a, sc)
            return None
        n = len(s.args)
        if n < cmd.min_args or n > len(cmd.params):
            want = str(len(cmd.params)) if cmd.min_args == len(cmd.params) else f"{cmd.min_args} to {len(cmd.params)}"
            self.err("DAL4041", f"host.{s.name}() takes {want} argument(s), got {n}", pos)
            return None
        args: list[ir.Expr] = []
        montage: ir.MontageDef | None = None
        asset_montage = False                        # the montage is a `montage` value (settings data), not a declared montage
        ok = True
        if "source" in cmd.implicit and not sc.notify_handler:
            self.err("DAL5066", f"host.{s.name}() names the montage instance that raised a notify: use it in the handler of a declared `branching` notify "
                                f"(notify_state Name branching {{ ... }}; the engine tells only a branching point which montage instance it comes from)", pos)
            return None
        if s.name == "snapshot_pose" and self.kind != "anim":
            self.err("DAL5067", "host.snapshot_pose() takes the pose of the animation instance: it belongs to an animation logic (logic Name;)", pos)
            return None
        place: tuple | None = None
        for i, spec in enumerate(cmd.params):
            if i >= n:
                default = cmd.defaults[i - cmd.min_args]
                if spec == "section":
                    if montage is not None:
                        args.append(ir.EnumLit(montage.enum, "None"))
                    elif asset_montage:
                        args.append(ir.Lit(NONE_T, None))           # a montage value has no sections: the host is told there is none
                elif isinstance(default, bool):
                    args.append(ir.Lit(BOOL, default))
                elif isinstance(spec, EnumType):
                    args.append(ir.EnumLit(spec, default))
                elif spec is INT:
                    args.append(ir.Lit(INT, int(default)))
                else:
                    args.append(ir.Lit(spec, float(default)))
                continue
            a = s.args[i]
            if spec == "montage":
                sym = self.globals.get(a.name) if isinstance(a, S.NameRef) else None
                if sym is not None and sym.kind == "montage":
                    montage = sym.obj    # type: ignore[assignment]
                    args.append(ir.MontageRef(MONTAGE, a.name))
                    continue
                v = self.ex(a, sc)
                if v.ty is ERROR:
                    ok = False
                elif v.ty is MONTAGE_ASSET:
                    asset_montage = True
                    args.append(v)
                else:
                    self.err("DAL4024", f"argument {i + 1} of host.{s.name}() must be a declared montage (montage Name;) or a montage value (a settings member), not {v.ty}", a.pos)
                    ok = False
            elif spec == "section":
                if asset_montage:
                    self.err("DAL4027", f"argument {i + 1} of host.{s.name}(): a montage value has no sections known when the program is compiled "
                                        f"(only a declared montage has); play it and move it with host.montage_set_position()", a.pos)
                    self.ex(a, sc)
                    ok = False
                    continue
                v = self.ex(a, sc)
                if montage is not None and v.ty is not ERROR and v.ty is not montage.enum:
                    self.err("DAL4025", f"argument {i + 1} of host.{s.name}() must be a section of montage {montage.name} "
                                        f"(e.g. {montage.name}.{montage.sections[0] if montage.sections else 'Section'}), not {v.ty}", a.pos)
                    ok = False
                args.append(v)
            elif spec == "place":
                v = self.ex(a, sc)
                if v.ty is ERROR:
                    ok = False
                    continue
                found = self.state_place(v)
                if v.ty is not SNAPSHOT or found is None:
                    got = f"a {v.ty}" if v.ty is not SNAPSHOT else "a local, input or parameter"
                    self.err("DAL4062", f"argument {i + 1} of host.{s.name}() is the snapshot to fill: a state of type snapshot or a snapshot member of a struct state "
                                        f"(host.snapshot_pose(RagdollPose);), not {got}", a.pos)
                    ok = False
                    continue
                place = found
                args.append(ir.Lit(NAME_T, ".".join((found[0], *found[1]))))
            elif spec == "name":
                if not isinstance(a, S.StringLit):
                    self.err("DAL4061", f"argument {i + 1} of host.{s.name}() is a name in quotes, e.g. host.{s.name}(\"Name\")", a.pos)
                    self.ex(a, sc)
                    ok = False
                    continue
                if not a.value or any(ord(c) < 32 or c in '"\\' for c in a.value):
                    self.err("DAL3067", f"'{a.value}' is not a usable slot name (printable characters, no quotes or backslashes, not empty)", a.pos)
                    ok = False
                    continue
                args.append(ir.Lit(NAME_T, a.value))
            else:
                v = self.ex(a, sc)
                v = self.assignable(spec, v, a.pos, f"argument {i + 1} of host.{s.name}()")
                args.append(v)
        if not ok:
            return None
        return ir.Command(s.name, args, place, n if cmd.sparse else None)

    def state_place(self, e: ir.Expr) -> tuple | None:
        """``(state, (member, ...))`` when ``e`` is a state or a member of a struct state (the places a command can fill), else None."""
        path: list[str] = []
        while isinstance(e, ir.Member) and isinstance(e.x.ty, StructType):
            path.insert(0, e.comps)
            e = e.x
        return (e.name, tuple(path)) if isinstance(e, ir.StateRef) else None

    def host_query(self, e: S.HostCallExpr, sc: Scope) -> ir.Expr:
        args = [self.ex(a, sc) for a in e.args]
        q = QUERIES.get(e.name)
        if q is None:
            return self.bad("DAL3060", f"unknown host query 'host.{e.name}'" + self.suggest(e.name, QUERIES), e.pos)
        if self.kind != "actor" and not q.thread_safe:
            safe = ", ".join(n for n, x in QUERIES.items() if x.thread_safe)
            return self.bad("DAL5041", f"host.{e.name}() asks the game world and is only available in actor logic (logic Name : actor;); "
                                       f"an anim instance runs on a worker thread and may ask only the thread-safe queries: {safe}", e.pos)
        if sc.ctx in ("fn", "const", "view"):
            return self.bad("DAL5042" if sc.ctx != "view" else "DAL5044",
                            "host queries are not allowed in functions, views or constants; call them in update, an entry, a method or a handler "
                            "and pass the result as an argument", e.pos)
        if any(a.ty is ERROR for a in args):
            return ir.Lit(ERROR, 0)
        if len(args) != len(q.params):
            return self.bad("DAL4041", f"host.{e.name}() takes {len(q.params)} argument(s), got {len(args)}", e.pos)
        conv = [self.assignable(t, a, ae.pos, f"argument {i + 1} of host.{e.name}()") for i, (t, a, ae) in enumerate(zip(q.params, args, e.args))]
        return ir.HostQuery(q.ret, e.name, conv)

    def for_stmt(self, s: S.ForStmt, sc: Scope) -> ir.Stmt | None:
        bounds: list[int] = []
        for label, e in (("start", s.lo), ("end", s.hi)):
            v = self.ex(e, Scope("const"))
            if v.ty is ERROR:
                return None
            if v.ty is not INT:
                self.err("DAL4026", f"the {label} of a loop range must be an int constant, not {v.ty}", e.pos)
                return None
            folded = self.fold(v, e.pos)
            if folded is None:
                return None
            bounds.append(int(folded))
        lo, hi = bounds
        if hi - lo > 64:
            self.err("DAL5050", f"a loop may run at most 64 times (this one {hi - lo}); loops are for small fixed ranges", s.pos)
            return None
        if hi < lo:
            self.err("DAL5051", f"the loop range {lo}..{hi} is reversed", s.pos)
            return None
        vpos = s.var_pos or s.pos
        if not self.valid_ident(s.var, vpos, "a loop variable"):
            return None
        if s.var in self.globals or sc.lookup(s.var) is not None:
            self.err("DAL3005", f"'{s.var}' hides another name; pick another loop variable", vpos)
            return None
        sc.push()
        sc.declare(s.var, Local(INT, False))
        self.loop_depth += 1
        try:
            body = self.block(s.body, sc)
        finally:
            self.loop_depth -= 1
        sc.pop()
        return ir.For(s.var, lo, hi, body)

    def while_stmt(self, s: S.WhileStmt, sc: Scope) -> ir.Stmt | None:
        """`while (cond) bound N { ... }`: the bound is a constant 1..64 (a program cannot loop for ever)."""
        c = self.ex(s.cond, sc)
        ok = True
        if c.ty is not BOOL and c.ty is not ERROR:
            self.err("DAL4003", f"the condition of 'while' must be bool, not {c.ty}", s.cond.pos)
        if c.ty is ERROR:
            ok = False
        v = self.ex(s.bound, Scope("const"))
        bound = 0
        if v.ty is ERROR:
            ok = False
        elif v.ty is not INT:
            self.err("DAL4026", f"the bound of a 'while' must be an int constant, not {v.ty}", s.bound.pos)
            ok = False
        else:
            folded = self.fold(v, s.bound.pos)
            if folded is None:
                ok = False
            else:
                bound = int(folded)
                if bound < 1:
                    self.err("DAL5051", f"the bound of a 'while' must be at least 1 (it is {bound})", s.bound.pos)
                    ok = False
                elif bound > 64:
                    self.err("DAL5050", f"a loop may run at most 64 times (this bound is {bound}); loops are for small fixed ranges", s.bound.pos)
                    ok = False
        self.loop_depth += 1
        try:
            body = self.block(s.body, sc)
        finally:
            self.loop_depth -= 1
        return ir.While(c, bound, body) if ok else None

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
            if ta is ROTATOR and tb is ROTATOR:
                return ir.Binary(ROTATOR, op, a, b)                      # component-wise, as FRotator's operators (no normalisation)
            return fail()
        if op == "*":
            if num:
                return ir.Binary(INT, op, a, b) if both_int else ir.Binary(FLOAT, op, self.widen(a), self.widen(b))
            if ta is ROTATOR and tb.is_numeric:
                return ir.Binary(ROTATOR, op, a, self.widen(b))
            if ta.is_numeric and tb is ROTATOR:
                return ir.Binary(ROTATOR, op, b, self.widen(a))          # scalar * rotator is the rotator scaled (FRotator has the one operator*)
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
            if ta.is_tags and ta is tb:
                return ir.Binary(BOOL, op, a, b)
            if isinstance(ta, AssetType) and not ta.settings_only and not ta.by_value and (tb is ta or tb is NONE_T):
                return ir.Binary(BOOL, op, a, b if tb is ta else ir.Lit(ta, None))
            if isinstance(tb, AssetType) and not tb.settings_only and not tb.by_value and ta is NONE_T:
                return ir.Binary(BOOL, op, ir.Lit(tb, None), b)
            if ta is tb and ta is SNAPSHOT:
                return self.bad("DAL4001", f"snapshot values cannot be compared with '{op}'; ask valid(snapshot)", op_pos)
            if ta is tb and ta in (VEC2, VEC3, ROTATOR, QUAT):
                return self.bad("DAL4001", f"{ta} values cannot be compared with '{op}'; compare lengths or components, e.g. is_nearly_zero(length(a - b))", op_pos)
            if ta is tb and ta is TRANSFORM:
                return self.bad("DAL4001", f"transform values cannot be compared with '{op}'; compare .location, .rotation or .scale", op_pos)
            if ta is tb and isinstance(ta, StructType):
                return self.bad("DAL4001", f"{ta.kind} values cannot be compared with '{op}'; compare the members you care about", op_pos)
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
        if a.ty is NONE_T and isinstance(b.ty, AssetType) and not b.ty.settings_only:
            return ir.Cond(b.ty, c, ir.Lit(b.ty, None), b)
        if b.ty is NONE_T and isinstance(a.ty, AssetType) and not a.ty.settings_only:
            return ir.Cond(a.ty, c, a, ir.Lit(a.ty, None))
        return self.bad("DAL4004", f"the two branches of '?:' have different types ({a.ty} and {b.ty})", e.pos)

    # ---- calls -----------------------------------------------------------------------------------------------------------------------
    def call(self, e: S.CallExpr, sc: Scope) -> ir.Expr:
        name = e.callee
        if (name == "valid" and name not in self.globals and len(e.args) == 1 and isinstance(e.args[0], S.NameRef) and sc.lookup(e.args[0].name) is None
                and e.args[0].name in self.globals and self.globals[e.args[0].name].kind == "peer"):
            if sc.ctx in ("fn", "const"):
                return self.bad("DAL5042", "valid(peer) looks at the other logics of the actor: use it in update, an entry, a method, a view or a handler", e.pos)
            return ir.PeerValid(BOOL, e.args[0].name) if e.args[0].name in self.peer_ifaces else ir.Lit(ERROR, 0)       # (an unloadable peer was reported)
        args = [self.ex(a, sc) for a in e.args]
        if any(a.ty is ERROR for a in args):
            return ir.Lit(ERROR, 0)
        if sc.lookup(name) is not None:
            return self.bad("DAL3031", f"'{name}' is a variable, not a function", e.pos)
        if name in self.invalid_names:
            return ir.Lit(ERROR, 0)
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
        if sym is not None and sym.kind == "struct":
            return self.make_struct(e, self.structs[name], args, sc)
        if name == "valid" and sym is None:
            if len(args) != 1 or not (args[0].ty.is_tags or args[0].ty.is_asset):
                given = ", ".join(a.ty.name for a in args)
                return self.bad("DAL4040", f"no version of valid() accepts ({given}); available: valid(tag) -> bool, valid(asset) -> bool", e.pos)
            return ir.Valid(BOOL, args[0])
        if sym is None and sc.ctx == "const" and name in self.later_fns:
            return self.bad("DAL5002", f"a constant expression cannot call '{name}'; only built-in functions are allowed", e.pos)
        if sym is None:
            cands = list(BUILTINS) + list(CONSTRUCTORS) + [k for k, s in self.globals.items() if s.kind in ("fn", "extern")]
            return self.bad("DAL3040", f"undefined function '{name}'" + self.suggest(name, cands), e.pos)
        if sym.kind in ("method", "view"):
            return self.call_method(e, name, args, sc)
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

    def call_allowed(self, e: S.Expr, what: str, ret: Type | None, sc: Scope, view: bool = False) -> bool:
        """Methods run in order and may change state, so a result is only taken from the whole right-hand side of a
        `let` / `=` / `return` (or the call is a statement); views are pure and may sit anywhere."""
        ctx = sc.ctx
        if ctx == "fn":
            self.err("DAL5012", f"fn bodies are pure: they cannot call {what}", e.pos)
            return False
        if ctx == "const":
            self.err("DAL5002", f"a constant expression cannot call {what}", e.pos)
            return False
        if view:
            return True
        if ctx == "view":
            self.err("DAL5044", f"a view only reads: it cannot call {what}", e.pos)
            return False
        if id(e) in self.stmt_calls:
            return True
        if ret is None:
            self.err("DAL4043", f"{what} returns nothing; call it as a statement: name(...);", e.pos)
            return False
        if id(e) not in self.value_calls:
            self.err("DAL4042", f"the result of {what} can only be the whole right-hand side of 'let', '=' or 'return' "
                                f"(methods change state, so they cannot hide inside an expression); put it in a variable first", e.pos)
            return False
        return True

    def call_method(self, e: S.CallExpr, name: str, args: list[ir.Expr], sc: Scope) -> ir.Expr:
        sig = self.method_sigs.get(name)
        if sig is None:
            return ir.Lit(ERROR, 0)
        what = f"{'view' if sig.is_view else 'method'} '{name}'"
        if not self.call_allowed(e, what, sig.ret, sc, sig.is_view):
            return ir.Lit(ERROR, 0)
        if len(args) != len(sig.params):
            return self.bad("DAL4041", f"{name}() takes {len(sig.params)} argument(s) ({', '.join(f'{n}: {t}' for n, t in sig.params)}), got {len(args)}", e.pos)
        conv = [self.assignable(pt, a, ae.pos, f"argument '{pn}' of {name}()") for (pn, pt), a, ae in zip(sig.params, args, e.args)]
        if sc.ctx in ("method", "view"):
            self.method_calls[sc.fn_name].append(name)
        if sig.is_view:
            return ir.CallView(sig.ret or ERROR, name, conv)
        return ir.CallMethod(sig.ret if sig.ret is not None else VOID, name, conv)

    def member_call(self, e: S.MemberCallExpr, sc: Scope) -> ir.Expr:
        base = e.base
        if isinstance(base, S.NameRef) and sc.lookup(base.name) is None and base.name in self.peer_ifaces:
            return self.peer_call(e, base.name, sc)
        if isinstance(base, S.NameRef) and sc.lookup(base.name) is None and base.name in self.globals and self.globals[base.name].kind == "peer":
            return ir.Lit(ERROR, 0)          # the peer could not be loaded: already reported
        if isinstance(base, S.NameRef) and sc.lookup(base.name) is None and base.name not in self.globals and base.name not in BUILTIN_CONSTS:
            return self.bad("DAL2004", "only functions can be called; write f(x), not x.f(...)", e.pos)
        self.allow_settings_param = isinstance(base, S.NameRef)
        b = self.ex(base, sc)
        self.allow_settings_param = False
        args = [self.ex(a, sc) for a in e.args]
        if b.ty is ERROR or any(a.ty is ERROR for a in args):
            return ir.Lit(ERROR, 0)
        if (b.ty is CURVE or b.ty is CURVE3) and e.name == "eval":
            if len(args) != 1:
                return self.bad("DAL4041", f"eval() takes 1 argument (the position on the curve), got {len(args)}", e.pos)
            x = self.assignable(FLOAT, args[0], e.args[0].pos, "the position on the curve")
            return ir.CurveEval(FLOAT if b.ty is CURVE else VEC3, b, x)
        if isinstance(b.ty, MapType) and e.name == "has":
            if len(args) != 1:
                return self.bad("DAL4041", f"has() takes 1 argument (the key), got {len(args)}", e.pos)
            return ir.MapHas(BOOL, b, self.assignable(b.ty.key, args[0], e.args[0].pos, "the key"))
        if b.ty is CURVE or b.ty is CURVE3:
            return self.bad("DAL4030", f"a curve has the method eval(x), not '{e.name}'", e.name_pos or e.pos)
        if isinstance(b.ty, MapType):
            return self.bad("DAL4030", f"a table has the method has(key), not '{e.name}'", e.name_pos or e.pos)
        return self.bad("DAL2004", "only functions can be called; write f(x), not x.f(...)", e.pos)

    def index_expr(self, e: S.IndexExpr, sc: Scope) -> ir.Expr:
        self.allow_settings_param = isinstance(e.base, S.NameRef)
        b = self.ex(e.base, sc)
        self.allow_settings_param = False
        i = self.ex(e.index, sc)
        if b.ty is ERROR or i.ty is ERROR:
            return ir.Lit(ERROR, 0)
        if isinstance(b.ty, MapType):
            return ir.Index(b.ty.value, b, self.assignable(b.ty.key, i, e.index.pos, "the key"))
        if isinstance(b.ty, ArrayType):
            return ir.Index(b.ty.elem, b, self.assignable(INT, i, e.index.pos, "the index"))
        return self.bad("DAL4044", f"type {b.ty} cannot be indexed (tables and lists can)", e.pos)

    def peer_call(self, e: S.MemberCallExpr, peer: str, sc: Scope) -> ir.Expr:
        iface = self.peer_ifaces[peer]
        args = [self.ex(a, sc) for a in e.args]
        if any(a.ty is ERROR for a in args):
            return ir.Lit(ERROR, 0)
        sig = iface.methods.get(e.name)
        if sig is None:
            hint = ""
            if e.name in iface.views:
                hint = f" ('{e.name}' is a view: read what you need through an input, input X: T <- {peer}.State;)"
            return self.bad("DAL3077", f"logic {iface.logic} has no 'pub method {e.name}'" + self.suggest(e.name, iface.methods) + hint, e.name_pos or e.pos)
        params, ret = sig
        what = f"'{peer}.{e.name}'"
        if self.kind == "anim":
            return self.bad("DAL5045", f"an anim logic cannot call another logic ({what}): it runs on a worker thread; use host commands or let the actor read the state", e.pos)
        queued = iface.kind == "anim"
        if not self.call_allowed(e, what, ret, sc):
            return ir.Lit(ERROR, 0)
        if queued and ret is not None and id(e) not in self.stmt_calls:
            return self.bad("DAL5063", f"{what} is queued for the animation instance (it runs at the start of its next update) and cannot return a value here", e.pos)
        if len(args) != len(params):
            return self.bad("DAL4041", f"{peer}.{e.name}() takes {len(params)} argument(s) ({', '.join(f'{n}: {t}' for n, t in params)}), got {len(args)}", e.pos)
        conv = [self.assignable(pt, a, ae.pos, f"argument '{pn}' of {peer}.{e.name}()") for (pn, pt), a, ae in zip(params, args, e.args)]
        return ir.CallPeer(ret if ret is not None else VOID, peer, e.name, conv, queued)

    def make_struct(self, e: S.CallExpr, st: StructType, args: list[ir.Expr], sc: Scope) -> ir.Expr:
        if st.kind == "settings" and not self.settings_init:
            return self.bad("DAL5004", f"{st.name} is a settings type: its values come from a param or from a member of another settings value, not from code "
                                       f"(only the declaration of a settings member may build one: Inner: {st.name} = {st.name}(Member: value);)", e.pos)
        names = e.names or [None] * len(args)
        if any(n is None for n in names):
            return self.bad("DAL4054", f"build a struct with named arguments: {st.name}(member: value, ...), not positional ones", e.pos)
        self.ensure_defaults(st)
        given: dict[str, ir.Expr] = {}
        ok = True
        for i, (n, a) in enumerate(zip(names, args)):
            apos = (e.name_pos[i] if e.name_pos and e.name_pos[i] else e.args[i].pos)
            fd = st.field(n)       # type: ignore[arg-type]
            if fd is None:
                self.err("DAL4030", f"struct {st.name} has no member '{n}'" + self.suggest(n, [x.name for x in st.fields])
                         + f" (members: {', '.join(x.name for x in st.fields) or 'none'})", apos)
                ok = False
                continue
            if n in given:
                self.err("DAL4056", f"member '{n}' is given twice", apos)
                ok = False
                continue
            given[n] = self.assignable(fd.ty, a, e.args[i].pos, f"member '{n}' of {st.name}")     # type: ignore[index]
        if not ok:
            return ir.Lit(ERROR, 0)
        return ir.MakeStruct(st, [(f.name, given[f.name]) for f in st.fields if f.name in given])

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
        decl_of = {d.name: d for d in self.unit.decls if isinstance(d, S.ExternDecl)}
        if self.standin is None:
            for x in externs:
                self.focus(decl_of[x.name])
                self.err(
                    "DAL6001",
                    f"extern fn '{x.name}' needs a Python stand-in: provide a file defining def {x.py}(...) "
                    f"(pass --standins FILE, or name it <source>_standins.py next to the .dal file)",
                    decl_pos.get(x.name, Pos(1, 1, 2)),
                )
            self.source = self.main_source
            return
        fname, text = self.standin
        self.focus(decl_of[externs[0].name])
        try:
            tree = pyast.parse(text, filename=fname)
        except SyntaxError as ex:
            self.source = self.main_source
            self.err("DAL6005", f"stand-in file {fname} has a syntax error: {ex.msg} (line {ex.lineno})", decl_pos.get(externs[0].name, Pos(1, 1, 2)))
            return
        first_pos = decl_pos.get(externs[0].name, Pos(1, 1, 2))
        taken = {self.module.name, "_rt"}
        taken |= {e.name for e in self.user_enums}
        taken |= {s.name for s in self.module.structs}
        taken |= {t.name for t in self.module.tagsets}
        taken |= {f"_fn_{f.name}" for f in self.module.funcs}
        taken |= {f"_K_{c.name}" for c in self.module.consts}
        for node in tree.body:
            if isinstance(node, pyast.ImportFrom) and node.module == "__future__":
                self.err("DAL6006", f"stand-in file {fname} may not use 'from __future__' imports: it is bundled into the middle of the generated module", first_pos)
            names: list[str] = []
            if isinstance(node, (pyast.FunctionDef, pyast.ClassDef)):
                names = [node.name]
            elif isinstance(node, pyast.Assign):
                names = [t.id for t in node.targets if isinstance(t, pyast.Name)]
            elif isinstance(node, (pyast.Import, pyast.ImportFrom)):
                names = [(a.asname or a.name).split(".")[0] for a in node.names]
            for n in names:
                if n in taken:
                    self.err("DAL6007", f"stand-in file {fname} defines '{n}', which the generated module needs for itself", first_pos)
        defs = {n.name: n for n in tree.body if isinstance(n, (pyast.FunctionDef,))}
        for x in externs:
            pos = decl_pos.get(x.name, Pos(1, 1, 2))
            self.focus(decl_of[x.name])
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
        self.source = self.main_source
        self.module.standin_source = text
        self.module.standin_filename = fname


def check(unit: S.SourceUnit, source: SourceFile, standin: tuple[str, str] | None = None,
          ctx=None, libs=()) -> tuple[ir.Module, list[Diagnostic]]:
    c = Checker(unit, source, standin, ctx, libs)
    module = c.run()
    return module, [d for d in c.diags if d.severity == "warning"]
