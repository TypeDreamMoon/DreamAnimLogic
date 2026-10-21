"""Peers: what one logic knows about another logic on the same actor.

``peer Character: AlsCharacter;`` names a logic by its type; the compiler finds ``AlsCharacter.dal`` next to the file
that declares the peer, runs the declaration half of the checker on it (no bodies) and keeps the result as a
``PeerIface``: the types of the variables other logics may read and the signatures of the ``pub`` methods they may call.
Libraries the two files share give both checkers the same type objects (``CompileContext.types``), so
``input L: AlsLocomotionState <- Character.LocomotionState`` type-checks by identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import DalError, SourceFile
from .libs import CompileContext, merge, read_text  # noqa: F401
from .types import Type


@dataclass
class PeerIface:
    logic: str
    kind: str                                    # 'anim' | 'actor'
    key: str
    filename: str
    vars: dict[str, Type] = field(default_factory=dict)                                   # states, inputs and params
    settings_assets: set[str] = field(default_factory=set)                                # params that are settings data assets (C++: DalSettings_<name>())
    methods: dict[str, tuple[list[tuple[str, Type]], Type | None]] = field(default_factory=dict)   # pub methods
    views: dict[str, tuple[list[tuple[str, Type]], Type | None]] = field(default_factory=dict)     # pub views
    entries: dict[str, "ir.EntryDef"] = field(default_factory=dict)      # entry name -> its signature (typed parameters, `linked` marker)


def load_interface(ctx: CompileContext, from_key: str, type_name: str) -> PeerIface:
    """The interface of the logic ``type_name`` (file ``<type_name>.dal`` next to ``from_key``).

    Raises ``FileNotFoundError`` when there is no such file and ``DalError`` when the file has errors in its declarations.
    """
    from .check import Checker
    from .lower import lower
    from .parser import parse_source

    key, text = ctx.resolve(from_key, type_name + ".dal")
    cached = ctx.ifaces.get(key)
    if cached is not None:
        return cached
    src = SourceFile(text, ctx.display(key))
    unit = parse_source(src)
    libs = ctx.load(unit, src, key)
    unit = lower(merge(unit, src, key, libs), src)
    checker = Checker(unit, src, None, ctx, libs)
    checker.interface_only = True
    module = checker.run()
    iface = PeerIface(module.name, module.kind, key, src.filename)
    for d in [*module.params, *module.inputs, *module.states]:
        iface.vars[d.name] = d.ty
    for d in module.params:
        if getattr(d.ty, "is_settings", False) and getattr(d.ty, "asset_root", False):
            iface.settings_assets.add(d.name)
    for x in module.methods:
        if x.pub:
            (iface.views if x.is_view else iface.methods)[x.name] = (list(x.params), x.ret)
    for e in module.entries:
        iface.entries[e.name] = e
    ctx.ifaces[key] = iface
    return iface
