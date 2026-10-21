"""DalLive: hot reload of .dal logic in the Unreal editor (see Unreal/DalLive/README.md).

``dal_live.install()`` is called by ``init_unreal.py`` when the editor starts.  The pure-Python parts
(``loader``, ``swap``, ``session``) do not import ``unreal`` and are tested in the compiler repository; ``host`` and
``manager`` need the editor.
"""

from __future__ import annotations

__version__ = "0.1.0"

connect_error: str | None = None      # why install() did not connect (None = connected or not tried yet)


def install() -> bool:
    """Find the dal compiler, then connect the C++ host components to the interpreter (idempotent)."""
    global connect_error
    import unreal

    from . import bootstrap

    try:
        bootstrap.ensure_dal(unreal.Paths.project_dir())
    except RuntimeError as e:
        connect_error = str(e)
        unreal.log_error(f"DalLive: {e}")
        return False
    from .manager import manager       # imports dal: only after the compiler is on sys.path

    ok = manager.install()
    connect_error = None if ok else manager.connect_error
    return ok


def get_manager():
    from .manager import manager

    return manager
