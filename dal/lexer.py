"""Lexer for ``.dal`` source."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import DalError, Pos, SourceFile

KEYWORDS = frozenset(
    {
        "logic", "enum", "const", "param", "input", "state", "fn", "extern",
        "update", "entry", "let", "var", "if", "else", "return", "true",
        "false", "cpp", "include", "py", "host",
    }
)

# Words kept free for later language versions (v1/v2).  They may not be used as
# identifiers today so that adding them later cannot break existing programs.
RESERVED_WORDS = frozenset(
    {
        "for", "while", "loop", "break", "continue", "match", "switch", "case",
        "struct", "use", "import", "event", "on", "emit", "timer", "window",
        "buffer", "combo", "montage", "cancel", "command", "self", "null",
        "export", "static", "type", "mut", "as", "in", "do", "yield",
    }
)

TWO_CHAR_OPS = ("->", "==", "!=", "<=", ">=", "&&", "||", "+=", "-=", "*=", "/=")
ONE_CHAR_OPS = "+-*/%=<>!?:,;.(){}[]"

INT_MAX = 2**31 - 1


@dataclass(frozen=True)
class Token:
    kind: str  # IDENT KEYWORD INT FLOAT STRING OP EOF
    text: str
    pos: Pos
    value: object = None

    def is_op(self, text: str) -> bool:
        return self.kind == "OP" and self.text == text

    def is_kw(self, text: str) -> bool:
        return self.kind == "KEYWORD" and self.text == text


def _is_ident_start(ch: str) -> bool:
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z")


def _is_ident_char(ch: str) -> bool:
    return _is_ident_start(ch) or ("0" <= ch <= "9") or ch == "_"


def _is_digit(ch: str) -> bool:
    return "0" <= ch <= "9"


def tokenize(source: SourceFile) -> list[Token]:
    """Turn source text into tokens; raises ``DalError`` on the first lexical error."""

    text = source.text
    n = len(text)
    tokens: list[Token] = []
    i = 0
    line = 1
    line_start = 0

    def fail(code: str, message: str, start: int, end: int | None = None) -> None:
        col = start - line_start + 1
        end_col = col + max((end if end is not None else start + 1) - start, 1)
        raise DalError([source.diag(code, message, Pos(line, col, end_col))])

    while i < n:
        ch = text[i]

        if ch == "\n":
            line += 1
            i += 1
            line_start = i
            continue
        if ch in " \t":
            i += 1
            continue

        # comments
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            start_line, start_col_off = line, i - line_start
            j = i + 2
            closed = False
            while j < n:
                if text[j] == "*" and j + 1 < n and text[j + 1] == "/":
                    closed = True
                    j += 2
                    break
                if text[j] == "\n":
                    line += 1
                    line_start = j + 1
                j += 1
            if not closed:
                raise DalError(
                    [
                        source.diag(
                            "DAL1003",
                            "unterminated block comment (missing '*/')",
                            Pos(start_line, start_col_off + 1, start_col_off + 3),
                        )
                    ]
                )
            i = j
            continue

        col = i - line_start + 1

        # identifiers / keywords
        if _is_ident_start(ch):
            j = i + 1
            while j < n and _is_ident_char(text[j]):
                j += 1
            word = text[i:j]
            if "__" in word:
                fail("DAL1005", "identifiers may not contain a double underscore", i, j)
            kind = "KEYWORD" if word in KEYWORDS else "IDENT"
            tokens.append(Token(kind, word, Pos(line, col, col + (j - i))))
            i = j
            continue

        if ch == "_":
            fail("DAL1005", "identifiers must start with a letter (a leading '_' is reserved for generated code)", i)

        # numbers
        if _is_digit(ch):
            j = i
            while j < n and _is_digit(text[j]):
                j += 1
            is_float = False
            if j < n and text[j] == ".":
                if j + 1 < n and _is_digit(text[j + 1]):
                    is_float = True
                    j += 1
                    while j < n and _is_digit(text[j]):
                        j += 1
                else:
                    fail("DAL1004", f"malformed number '{text[i:j + 1]}': write {text[i:j]}.0", i, j + 1)
            if j < n and text[j] in "eE":
                k = j + 1
                if k < n and text[k] in "+-":
                    k += 1
                if k < n and _is_digit(text[k]):
                    while k < n and _is_digit(text[k]):
                        k += 1
                    is_float = True
                    j = k
                else:
                    fail("DAL1004", "malformed number: exponent has no digits", i, k)
            if j < n and _is_ident_char(text[j]):
                k = j
                while k < n and _is_ident_char(text[k]):
                    k += 1
                fail("DAL1004", f"malformed number '{text[i:k]}' (numeric suffixes such as 'f' are not used)", i, k)
            literal = text[i:j]
            if is_float:
                tokens.append(Token("FLOAT", literal, Pos(line, col, col + (j - i)), float(literal)))
            else:
                value = int(literal)
                if value > INT_MAX:
                    fail("DAL1004", f"integer literal {literal} does not fit in a 32-bit int", i, j)
                tokens.append(Token("INT", literal, Pos(line, col, col + (j - i)), value))
            i = j
            continue

        if ch == "." and i + 1 < n and _is_digit(text[i + 1]):
            j = i + 1
            while j < n and _is_digit(text[j]):
                j += 1
            fail("DAL1004", f"malformed number '{text[i:j]}': write 0{text[i:j]}", i, j)

        # strings
        if ch == '"':
            j = i + 1
            chars: list[str] = []
            closed = False
            while j < n:
                c = text[j]
                if c == "\n":
                    break
                if c == "\\":
                    if j + 1 >= n:
                        break
                    esc = text[j + 1]
                    mapping = {'"': '"', "\\": "\\", "n": "\n", "t": "\t"}
                    if esc not in mapping:
                        fail("DAL1002", f"unknown escape '\\{esc}' in string", j, j + 2)
                    chars.append(mapping[esc])
                    j += 2
                    continue
                if c == '"':
                    closed = True
                    j += 1
                    break
                chars.append(c)
                j += 1
            if not closed:
                fail("DAL1002", "unterminated string literal", i, j)
            tokens.append(Token("STRING", text[i:j], Pos(line, col, col + (j - i)), "".join(chars)))
            i = j
            continue

        # operators
        two = text[i : i + 2]
        if two in TWO_CHAR_OPS:
            tokens.append(Token("OP", two, Pos(line, col, col + 2)))
            i += 2
            continue
        if ch in ONE_CHAR_OPS:
            tokens.append(Token("OP", ch, Pos(line, col, col + 1)))
            i += 1
            continue

        if ord(ch) > 127:
            fail("DAL1001", f"unexpected character {ch!r}: .dal source is ASCII outside comments and strings", i)
        fail("DAL1001", f"unexpected character {ch!r}", i)

    tokens.append(Token("EOF", "", Pos(line, n - line_start + 1, n - line_start + 2)))
    return tokens
