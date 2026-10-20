"""DAL (Dream Anim Logic): one compiler, two backends (Unreal C++ and Python)."""

from __future__ import annotations

__version__ = "0.2.0"

from .api import CompileResult, check_file, check_source  # noqa: E402,F401
from .errors import DalError, Diagnostic  # noqa: E402,F401
