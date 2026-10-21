"""State migration for a hot swap: which values survive when a running program is replaced by a newly compiled one.

No ``unreal`` import: this module (and ``session.py``) is tested in the compiler repo with the mock hosts.

Rules (documented in ``Unreal/DalLive/README.md``):

* a **state** (and a free input) with the same name and the same type keeps its value; an enum keeps its *member*
  (matched by name), so inserting a combo step or a montage section does not shift the others; a member that no longer
  exists falls back to the new default;
* a new state starts at its declared default; a removed one is dropped;
* a state whose type changed starts at its new default;
* ``param`` values are never carried: they come from the new program's defaults and the component's overrides;
* host-bound inputs are not carried either: the next update samples them again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dal.types import EnumType


@dataclass
class MigrationReport:
    kept: list = field(default_factory=list)       # names carried over with their value
    reset: list = field(default_factory=list)      # (name, reason): existed before, starts at the default now
    added: list = field(default_factory=list)      # new names (default value)
    dropped: list = field(default_factory=list)    # names that are gone

    def summary(self) -> str:
        parts = [f"kept {len(self.kept)} value(s)"]
        if self.added:
            parts.append("new: " + ", ".join(self.added))
        if self.dropped:
            parts.append("removed: " + ", ".join(self.dropped))
        if self.reset:
            parts.append("reset: " + ", ".join(f"{n} ({why})" for n, why in self.reset))
        return "; ".join(parts)


def _carryable(module):
    """Name -> type of the values a swap may carry: states and free inputs."""
    out = {s.name: s.ty for s in module.states}
    out.update({i.name: i.ty for i in module.inputs if i.host is None})
    return out


def _translate(old_ty, new_ty, value):
    """The value in the new type, or ``None`` when it cannot be carried (the caller reports why)."""
    old_enum = isinstance(old_ty, EnumType)
    new_enum = isinstance(new_ty, EnumType)
    if old_enum != new_enum:
        return None, "type changed"
    if old_enum:
        if old_ty.name != new_ty.name:
            return None, "type changed"
        member = old_ty.members[int(value)] if 0 <= int(value) < len(old_ty.members) else None
        if member is not None and member in new_ty.members:
            return new_ty.members.index(member), ""
        return None, f"enum member {member} is gone"
    if old_ty is not new_ty:
        return None, "type changed"
    return value, ""


def migrate_values(old_module, old_values: dict, new_module, new_values: dict) -> MigrationReport:
    """Copy the surviving values from ``old_values`` into ``new_values`` (which already holds the defaults)."""
    report = MigrationReport()
    old = _carryable(old_module)
    new = _carryable(new_module)
    for name, new_ty in new.items():
        if name not in old:
            report.added.append(name)
            continue
        value, why = _translate(old[name], new_ty, old_values[name])
        if why:
            report.reset.append((name, why))
        else:
            new_values[name] = value
            report.kept.append(name)
    for name in old:
        if name not in new:
            report.dropped.append(name)
    return report
