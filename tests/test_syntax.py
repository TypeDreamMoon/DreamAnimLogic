"""Lexer and parser: tokens, accepted syntax, and error messages with line/column."""

import unittest

import _bootstrap  # noqa: F401

from dal import syntax as S
from dal.errors import DalError, SourceFile
from dal.lexer import tokenize
from dal.parser import parse_source


def toks(text):
    return [(t.kind, t.text) for t in tokenize(SourceFile(text)) if t.kind != "EOF"]


def parse(text):
    return parse_source(SourceFile(text))


def lex_error(text):
    try:
        tokenize(SourceFile(text))
    except DalError as e:
        d = e.first
        return d.code, d.pos.line, d.pos.col
    raise AssertionError("no error")


def parse_error(text):
    try:
        parse(text)
    except DalError as e:
        d = e.first
        return d.code, d.pos.line, d.pos.col, d.message
    raise AssertionError("no error")


class LexerTests(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(toks("1 2.5 1e3 1.5e-2 0"), [("INT", "1"), ("FLOAT", "2.5"), ("FLOAT", "1e3"), ("FLOAT", "1.5e-2"), ("INT", "0")])
        t = tokenize(SourceFile("2.5 7"))
        self.assertEqual((t[0].value, t[1].value), (2.5, 7))

    def test_operators_longest_match(self):
        self.assertEqual([x[1] for x in toks("a <= b >= c == d != e && f || g -> h += i")],
                         ["a", "<=", "b", ">=", "c", "==", "d", "!=", "e", "&&", "f", "||", "g", "->", "h", "+=", "i"])

    def test_less_minus_is_two_tokens(self):
        # 'a<-1' must stay a comparison; only the input declaration glues '<' '-' together
        self.assertEqual([x[1] for x in toks("a<-1")], ["a", "<", "-", "1"])

    def test_comments_and_positions(self):
        t = tokenize(SourceFile("// line\nlogic /* block\n spanning */ A;"))
        self.assertEqual([(x.text, x.pos.line, x.pos.col) for x in t if x.kind != "EOF"],
                         [("logic", 2, 1), ("A", 3, 14), (";", 3, 15)])

    def test_string_escapes(self):
        t = tokenize(SourceFile('"a\\"b\\\\c"'))
        self.assertEqual(t[0].value, 'a"b\\c')

    def test_keywords_vs_identifiers(self):
        self.assertEqual(toks("logic Logic input_x"), [("KEYWORD", "logic"), ("IDENT", "Logic"), ("IDENT", "input_x")])

    def test_bom_and_crlf(self):
        t = tokenize(SourceFile("﻿logic A;\r\nstate X: float;\r\n"))
        self.assertEqual(t[3].pos.line, 2)

    def test_lexical_errors(self):
        self.assertEqual(lex_error("logic A;\nstate X: float = 1 @ 2;"), ("DAL1001", 2, 20))
        self.assertEqual(lex_error("x = 1.;"), ("DAL1004", 1, 5))
        self.assertEqual(lex_error("x = 0.2f;"), ("DAL1004", 1, 5))
        self.assertEqual(lex_error("x = .5;"), ("DAL1004", 1, 5))
        self.assertEqual(lex_error("x = 99999999999;"), ("DAL1004", 1, 5))
        self.assertEqual(lex_error('x = "abc;'), ("DAL1002", 1, 5))
        self.assertEqual(lex_error("a\n/* never closed"), ("DAL1003", 2, 1))
        self.assertEqual(lex_error("state _x"), ("DAL1005", 1, 7))
        self.assertEqual(lex_error("state a__b"), ("DAL1005", 1, 7))

    def test_non_ascii_outside_comments_is_rejected(self):
        self.assertEqual(lex_error("state 速度: float;")[0], "DAL1001")
        toks("// 速度 comment is fine\nlogic A;")


class ParserTests(unittest.TestCase):
    def test_all_declaration_kinds(self):
        unit = parse(
            """
            logic L;
            enum E { A, B, }
            const C: float = 1.0 + 2;
            param P: float = 0.5;
            input I: vec3 <- host.velocity;
            input J: bool <- host.notify("Hit");
            input K: float = 2.0;
            state S: int;
            fn f(a: float, b: int) -> float { return a; }
            extern fn g(x: float) -> float cpp "Ns::G" include "G.h" py "g_py";
            update(dt) { let x = 1; var y = 2.0; y += x; if (y > 1.0) { S = 1; } else if (y < 0.0) { S = 2; } else { S = 3; } }
            entry Refresh(d) { S = 0; return; }
            """
        )
        kinds = [type(d).__name__ for d in unit.decls]
        self.assertEqual(kinds, ["LogicDecl", "EnumDecl", "ConstDecl", "ParamDecl", "InputDecl", "InputDecl", "InputDecl",
                                 "StateDecl", "FnDecl", "ExternDecl", "UpdateDecl", "EntryDecl"])
        inp = unit.decls[4]
        self.assertEqual((inp.binding.item, inp.binding.arg), ("velocity", None))
        self.assertEqual(unit.decls[5].binding.arg, "Hit")
        ext = unit.decls[9]
        self.assertEqual((ext.cpp, ext.include, ext.py), ("Ns::G", "G.h", "g_py"))

    def test_expression_precedence(self):
        unit = parse("logic L; const C: float = 1 + 2 * 3 - 4 / 2;")
        e = unit.decls[1].init
        self.assertEqual(e.op, "-")
        self.assertEqual(e.left.op, "+")
        self.assertEqual(e.left.right.op, "*")
        self.assertEqual(e.right.op, "/")

    def test_ternary_right_associative_and_logic(self):
        unit = parse("logic L; const C: bool = a || b && c ? x : y ? z : w;")
        e = unit.decls[1].init
        self.assertIsInstance(e, S.CondExpr)
        self.assertEqual(e.cond.op, "||")
        self.assertEqual(e.cond.right.op, "&&")
        self.assertIsInstance(e.orelse, S.CondExpr)

    def test_postfix_chain(self):
        unit = parse("logic L; const C: float = f(a, b).xy.x + Gait.Run;")
        e = unit.decls[1].init
        self.assertIsInstance(e.left, S.MemberRef)
        self.assertEqual(e.left.field, "x")
        self.assertIsInstance(e.left.base.base, S.CallExpr)
        self.assertIsInstance(e.right, S.MemberRef)

    def test_unary(self):
        unit = parse("logic L; const C: bool = !-x;")
        e = unit.decls[1].init
        self.assertEqual((e.op, e.operand.op), ("!", "-"))

    def test_syntax_errors_have_line_and_column(self):
        self.assertEqual(parse_error("logic A\nstate X: float;")[:3], ("DAL2001", 1, 8))
        self.assertEqual(parse_error("logic A;\nfoo;")[:3], ("DAL2001", 2, 1))
        self.assertEqual(parse_error("logic A;\nupdate(dt) { let x = ; }")[:3], ("DAL2004", 2, 22))
        self.assertEqual(parse_error("logic A;\nupdate(dt) { x = 1 }")[:3], ("DAL2001", 2, 20))
        self.assertEqual(parse_error("logic A;\nupdate(dt) {\n if (x) { }\n")[0], "DAL2001")
        self.assertEqual(parse_error("logic A;\nfn f() { }")[:3], ("DAL2001", 2, 8))

    def test_missing_semicolon_points_at_the_end_of_the_previous_line(self):
        code, line, col, msg = parse_error("logic A;\nstate X: float\nstate Y: float;")
        self.assertEqual((code, line), ("DAL2001", 2))
        self.assertEqual(col, len("state X: float") + 1)
        self.assertIn("';'", msg)

    def test_member_assignment_parses_and_the_checker_rejects_components_with_advice(self):
        # a.b = v parses as a member assignment (struct members, v3); assigning to a component of a vector is a type error
        unit = parse("logic A;\nupdate(dt) { V.x = 1.0; S.a.b += 2.0; }")
        first, second = unit.decls[1].body
        self.assertIsInstance(first, S.MemberAssignStmt)
        self.assertEqual((first.name, first.path, first.op), ("V", ["x"], "="))
        self.assertEqual((second.name, second.path, second.op), ("S", ["a", "b"], "+="))
        import dal
        with self.assertRaises(DalError) as cm:
            dal.check_source("logic A;\nstate V: vec2;\nupdate(dt) { V.x = 1.0; }", "t.dal")
        self.assertEqual(cm.exception.first.code, "DAL2010")
        self.assertIn("vec2", cm.exception.first.message)

    def test_struct_declarations_and_named_arguments(self):
        unit = parse("struct P { a: float = 1.0; b: vec3; }\nlogic A;\nstate S: P = P(a: 2.0, b: vec3(1.0, 2.0, 3.0));")
        st = unit.decls[0]
        self.assertIsInstance(st, S.StructDecl)
        self.assertEqual([(f.name, f.type_name) for f in st.fields], [("a", "float"), ("b", "vec3")])
        call = unit.decls[2].init
        self.assertEqual(call.names, ["a", "b"])
        self.assertEqual(parse("logic A;\nconst C: float = f(1.0, 2.0);").decls[1].init.names, None)
        self.assertEqual(parse_error("struct P { a: float }")[0], "DAL2001")
        self.assertEqual(parse_error("struct { }")[0], "DAL2001")

    def test_reserved_words_and_keywords_are_not_identifiers(self):
        self.assertEqual(parse_error("logic A;\nstate while: float;")[0], "DAL2002")
        self.assertEqual(parse_error("logic A;\nstate update: float;")[0], "DAL2003")

    def test_host_only_in_input_bindings(self):
        self.assertEqual(parse_error("logic A;\nupdate(dt) { X = host.time; }")[0], "DAL2012")

    def test_method_call_syntax_is_rejected(self):
        self.assertEqual(parse_error("logic A;\nconst C: float = x.f(1);")[0], "DAL2004")

    def test_else_if_nests(self):
        unit = parse("logic A; update(dt) { if (a) { } else if (b) { } else { } }")
        stmt = unit.decls[1].body[0]
        self.assertIsInstance(stmt.orelse[0], S.IfStmt)
        self.assertEqual(stmt.orelse[0].orelse, [])

    def test_error_excerpt_formatting(self):
        try:
            parse("logic A;\nstate X: float = 1 @ 2;") if False else tokenize(SourceFile("logic A;\nstate X: float = 1 @ 2;", "t.dal"))
        except DalError as e:
            text = e.first.format()
            self.assertIn("t.dal:2:20: error DAL1001", text)
            self.assertIn("state X: float = 1 @ 2;", text)
            self.assertIn("^", text)


if __name__ == "__main__":
    unittest.main()
