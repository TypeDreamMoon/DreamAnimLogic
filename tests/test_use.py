"""Library files (``use "x.dal";``): loading, merging, diagnostics that point into the library, the three backends."""

import os
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401

import cpp_driver
import dal
import test_cpp_compile as tcc
from dal import harness
from dal.cppbackend import generate_cpp
from dal.pybackend import generate_python, load_class

LIB = """
// shared by the logics of a character
enum Gait { Walk, Run, Sprint }
const WalkSpeed: float = 175.0;
const RunSpeed: float = 375.0;
fn clamp01(x: float) -> float { return clamp(x, 0.0, 1.0); }
fn gait_for(speed: float) -> Gait {
    if (speed < WalkSpeed + 10.0) { return Gait.Walk; }
    if (speed < RunSpeed + 10.0) { return Gait.Run; }
    return Gait.Sprint;
}
"""

MAIN = """
use "lib/gait_lib.dal";
logic Mover : actor;
input Velocity: vec3 <- host.velocity;
state G: Gait;
state Amount: float;
update(dt) {
    let speed = length(Velocity.xy);
    G = gait_for(speed);
    Amount = clamp01(speed / RunSpeed);
}
"""

OTHER = """
use "lib/gait_lib.dal";
logic Viewer;
input Speed: float;
state G: Gait;
update(dt) { G = gait_for(Speed); }
"""

FILES = {"lib/gait_lib.dal": LIB, "main.dal": MAIN, "other.dal": OTHER}


def check(src, name="main.dal", files=None):
    return dal.check_source(src, name, files=files if files is not None else FILES)


def codes(src, name="main.dal", files=None):
    try:
        check(src, name, files)
    except dal.DalError as e:
        return [(d.code, d.filename, d.pos.line, d.pos.col) for d in e.diagnostics if d.severity == "error"]
    return []


class UseTests(unittest.TestCase):
    def test_library_declarations_are_merged_in_use_order(self):
        m = check(MAIN).module
        self.assertEqual([e.name for e in m.enums], ["Gait"])
        self.assertEqual([c.name for c in m.consts], ["WalkSpeed", "RunSpeed"])
        self.assertEqual([f.name for f in m.funcs], ["clamp01", "gait_for"])
        self.assertEqual([lib.filename for lib in m.libs], ["lib/gait_lib.dal"])
        self.assertTrue(all(c.origin == m.libs[0].key for c in m.consts))
        self.assertEqual(m.name, "Mover")

    def test_a_program_with_use_behaves_like_the_program_with_the_library_pasted_in(self):
        merged = LIB + "logic Mover : actor;\n" + MAIN.split("logic Mover : actor;")[1]
        a = check(MAIN).module
        b = dal.check_source(merged, "merged.dal").module
        self.assertEqual(harness.run_python(a, harness.random_frames(a, 80, 3)), harness.run_python(b, harness.random_frames(b, 80, 3)))
        self.assertEqual(harness.verify(a, 80, 5), [])

    def test_two_logics_share_one_enum(self):
        a, b = check(MAIN).module, check(OTHER, "other.dal").module
        self.assertEqual(a.enums[0].origin, b.enums[0].origin)
        self.assertEqual(generate_cpp(a)["GaitLib.h"], generate_cpp(b)["GaitLib.h"])

    def test_diamond_and_nested_use_load_each_file_once(self):
        files = {"a.dal": 'use "c.dal";\nconst A: float = C + 1.0;', "b.dal": 'use "c.dal";\nconst B: float = C + 2.0;',
                 "c.dal": "const C: float = 1.0;", "main.dal": 'use "a.dal";\nuse "b.dal";\nlogic M;\nstate X: float = A + B;\nupdate(dt) { X += 1.0; }'}
        m = check(files["main.dal"], files=files).module
        self.assertEqual([c.name for c in m.consts], ["C", "A", "B"])
        self.assertEqual([lib.filename for lib in m.libs], ["c.dal", "a.dal", "b.dal"])
        self.assertEqual(m.states[0].default, 5.0)
        # the C++ header of a depends on the header of c
        h = generate_cpp(m)["A.h"]
        self.assertIn('#include "C.h"', h)

    def test_a_library_can_be_compiled_on_its_own(self):
        r = check(LIB, "gait_lib.dal")
        self.assertTrue(r.module.is_library)
        self.assertEqual(r.module.name, "GaitLib")
        ns = load_class(r.module)
        self.assertEqual(ns["Gait"].Sprint, 2)
        self.assertEqual(ns["_fn_gait_for"](300.0), ns["Gait"].Run)
        files = generate_cpp(r.module)
        self.assertEqual(sorted(files), ["DalMath.h", "GaitLib.h"])

    def test_a_file_without_logic_and_with_other_declarations_is_still_an_error(self):
        self.assertEqual(codes("state X: float;", "x.dal")[0][0], "DAL3010")
        self.assertEqual(codes("", "x.dal")[0][0], "DAL3010")

    def test_errors_in_a_library_point_into_the_library(self):
        bad = {"lib/l.dal": "const A: float = 1.0;\nfn f(x: float) -> float { return x + Missing; }", "m.dal": 'use "lib/l.dal";\nlogic M;\nupdate(dt) {}'}
        self.assertEqual(codes(bad["m.dal"], "m.dal", bad), [("DAL3040", "lib/l.dal", 2, 38)])

    def test_clash_between_library_and_program_names_the_file(self):
        files = {"l.dal": "const A: float = 1.0;", "m.dal": 'use "l.dal";\nlogic M;\nstate A: float;'}
        try:
            check(files["m.dal"], "m.dal", files)
        except dal.DalError as e:
            d = e.first
            self.assertEqual((d.code, d.filename, d.pos.line), ("DAL3001", "m.dal", 3))
            self.assertIn("line 1 of l.dal", d.message)
        else:
            self.fail("no error")

    def test_two_libraries_declaring_the_same_name_clash(self):
        files = {"a.dal": "const A: float = 1.0;", "b.dal": "const A: float = 2.0;", "m.dal": 'use "a.dal";\nuse "b.dal";\nlogic M;\nupdate(dt) {}'}
        self.assertEqual(codes(files["m.dal"], "m.dal", files), [("DAL3001", "b.dal", 1, 7)])

    def test_missing_and_escaping_paths(self):
        files = {"m.dal": 'use "nope.dal";\nlogic M;\nupdate(dt) {}'}
        self.assertEqual(codes(files["m.dal"], "m.dal", files), [("DAL3073", "m.dal", 1, 5)])
        files = {"m.dal": 'use "../up.dal";\nlogic M;\nupdate(dt) {}', "../up.dal": "const A: float = 1.0;"}
        self.assertEqual(codes(files["m.dal"], "m.dal", files), [("DAL3074", "m.dal", 1, 5)])
        nested = {"sub/m.dal": 'use "../up.dal";\nlogic M;\nupdate(dt) {}', "up.dal": "const A: float = 1.0;"}
        self.assertEqual(codes(nested["sub/m.dal"], "sub/m.dal", nested), [("DAL3074", "sub/m.dal", 1, 5)])

    def test_circular_use_is_reported_with_the_cycle(self):
        files = {"a.dal": 'use "b.dal";\nconst A: float = 1.0;', "b.dal": 'use "a.dal";\nconst B: float = 1.0;', "m.dal": 'use "a.dal";\nlogic M;\nupdate(dt) {}'}
        try:
            check(files["m.dal"], "m.dal", files)
        except dal.DalError as e:
            self.assertEqual((e.first.code, e.first.filename), ("DAL3070", "b.dal"))
            self.assertIn("a.dal -> b.dal -> a.dal", e.first.message)
        else:
            self.fail("no error")
        files = {"m.dal": 'use "m.dal";\nlogic M;\nupdate(dt) {}'}
        self.assertEqual(codes(files["m.dal"], "m.dal", files)[0][0], "DAL3070")

    def test_a_library_may_not_contain_state_or_a_logic(self):
        for body, what in (("logic L;", "logic"), ("state X: float;", "state"), ("input I: float;", "input"), ("update(dt) {}", "update"), ("param P: float;", "param")):
            files = {"l.dal": body, "m.dal": 'use "l.dal";\nlogic M;\nupdate(dt) {}'}
            c = codes(files["m.dal"], "m.dal", files)
            self.assertEqual(c[0][:2], ("DAL3071", "l.dal"), body)

    def test_syntax_error_in_a_library_names_the_library(self):
        files = {"l.dal": "const A: float = ;", "m.dal": 'use "l.dal";\nlogic M;\nupdate(dt) {}'}
        c = codes(files["m.dal"], "m.dal", files)
        self.assertEqual(c[0][:2], ("DAL2004", "l.dal"))

    def test_use_needs_a_string(self):
        self.assertEqual(codes("use foo;\nlogic M;", "m.dal", {})[0][0], "DAL2001")
        self.assertEqual(codes('use "a.dal"\nlogic M;', "m.dal", {})[0][0], "DAL2001")

    def test_use_is_not_a_name(self):
        self.assertEqual(codes("logic M;\nstate use: float;", "m.dal", {})[0][0], "DAL2002")

    def test_hash_changes_with_the_library(self):
        a = check(MAIN).module
        files = dict(FILES)
        files["lib/gait_lib.dal"] = LIB + "\nconst Extra: float = 1.0;"
        b = check(MAIN, files=files).module
        self.assertNotEqual(a.source_sha256, b.source_sha256)
        # a program without libraries keeps the plain hash of its text
        import hashlib
        plain = dal.check_source("logic Q;\nupdate(dt) {}", "q.dal").module
        self.assertEqual(plain.source_sha256, hashlib.sha256(b"logic Q;\nupdate(dt) {}").hexdigest())


class UseOnDiskTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dal3_use_")
        os.makedirs(os.path.join(self.dir, "lib"))
        for name, text in FILES.items():
            with open(os.path.join(self.dir, name), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_check_file_resolves_relative_to_the_including_file(self):
        r = dal.check_file(os.path.join(self.dir, "main.dal"))
        self.assertEqual([lib.filename for lib in r.module.libs], ["lib/gait_lib.dal"])

    def test_cli_compiles_a_program_and_a_library(self):
        import contextlib
        import io
        from dal import cli
        out = os.path.join(self.dir, "out")
        with contextlib.redirect_stdout(io.StringIO()):
            self._cli(cli, out)

    def _cli(self, cli, out):
        self.assertEqual(cli.main(["compile", os.path.join(self.dir, "main.dal"), "--py", out, "--cpp", out]), 0)
        self.assertTrue(os.path.isfile(os.path.join(out, "GaitLib.h")))
        self.assertTrue(os.path.isfile(os.path.join(out, "Mover.cpp")))
        self.assertEqual(cli.main(["compile", os.path.join(self.dir, "lib", "gait_lib.dal"), "--py", out, "--cpp", out]), 0)
        self.assertTrue(os.path.isfile(os.path.join(out, "GaitLib.py")))
        self.assertEqual(cli.main(["check", os.path.join(self.dir, "lib", "gait_lib.dal")]), 0)
        self.assertEqual(cli.main(["verify", os.path.join(self.dir, "lib", "gait_lib.dal")]), 0)

    def test_the_root_is_the_folder_of_the_program(self):
        with open(os.path.join(self.dir, "lib", "up.dal"), "w", encoding="utf-8", newline="\n") as f:
            f.write('use "../main.dal";\n')
        with open(os.path.join(self.dir, "esc.dal"), "w", encoding="utf-8", newline="\n") as f:
            f.write('use "../outside.dal";\nlogic E;\nupdate(dt) {}\n')
        with self.assertRaises(dal.DalError) as cm:
            dal.check_file(os.path.join(self.dir, "esc.dal"))
        self.assertEqual(cm.exception.first.code, "DAL3074")


@unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
class UseCppTests(unittest.TestCase):
    def test_logic_with_a_library_compiles_and_matches_python(self):
        m = check(MAIN).module
        for seed in (1, 2):
            tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 120, seed), warnings_as_errors=True)

    def test_two_logics_include_the_same_library_header(self):
        # the generated headers of both logics and the library header go through the compiler in one translation unit
        a, b = check(MAIN).module, check(OTHER, "other.dal").module
        fa, fb = generate_cpp(a), generate_cpp(b)
        self.assertEqual(fa["GaitLib.h"], fb["GaitLib.h"])
        for m in (a, b):
            tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 40, 4), warnings_as_errors=True)


if __name__ == "__main__":
    unittest.main()
