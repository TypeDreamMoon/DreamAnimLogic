"""Diagnostics for the DAL compiler.

Every problem the compiler reports is a ``Diagnostic`` with a stable code
(``DAL1xxx`` lexical, ``DAL2xxx`` syntax, ``DAL3xxx`` names, ``DAL4xxx`` types,
``DAL5xxx`` semantics, ``DAL6xxx`` extern/stand-ins, ``DAL9xxx`` internal) and a
1-based line/column.  ``DalError`` carries a list of diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Pos:
    """1-based source position with an optional end column (same line)."""

    line: int
    col: int
    end_col: int = 0

    def span_end(self) -> int:
        return self.end_col if self.end_col > self.col else self.col + 1


NOWHERE = Pos(0, 0, 0)


@dataclass
class Diagnostic:
    code: str
    message: str
    pos: Pos
    severity: str = "error"  # "error" | "warning"
    filename: str = "<string>"
    source_line: str = ""

    def format(self, with_excerpt: bool = True) -> str:
        head = (
            f"{self.filename}:{self.pos.line}:{self.pos.col}: "
            f"{self.severity} {self.code}: {self.message}"
        )
        if not with_excerpt or not self.source_line or self.pos.line <= 0:
            return head
        text = self.source_line.rstrip("\r\n").expandtabs(4)
        caret_start = max(self.pos.col - 1, 0)
        width = max(self.pos.span_end() - self.pos.col, 1)
        return f"{head}\n    {text}\n    {' ' * caret_start}{'^' * width}"

    def __str__(self) -> str:
        return self.format()


class DalError(Exception):
    """Raised when compilation fails; ``diagnostics`` holds every error found."""

    def __init__(self, diagnostics: list[Diagnostic]):
        self.diagnostics = list(diagnostics)
        super().__init__("\n".join(d.format() for d in self.diagnostics))

    @property
    def first(self) -> Diagnostic:
        return self.diagnostics[0]

    @property
    def codes(self) -> list[str]:
        return [d.code for d in self.diagnostics]


class SourceFile:
    """Source text plus helpers to build diagnostics that quote the offending line."""

    def __init__(self, text: str, filename: str = "<string>"):
        if text.startswith("﻿"):
            text = text[1:]
        self.text = text.replace("\r\n", "\n").replace("\r", "\n")
        self.filename = filename
        self.lines = self.text.split("\n")

    def line_text(self, line: int) -> str:
        if 1 <= line <= len(self.lines):
            return self.lines[line - 1]
        return ""

    def diag(self, code: str, message: str, pos: Pos, severity: str = "error") -> Diagnostic:
        return Diagnostic(code, message, pos, severity, self.filename, self.line_text(pos.line))
