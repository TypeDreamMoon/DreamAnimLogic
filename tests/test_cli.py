"""The command line: python -m dal compile / check / verify / dump-ir."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _bootstrap  # noqa: F401
import dal
from _bootstrap import EXAMPLES, ROOT

LEAN = os.path.join(EXAMPLES, "als_lean.dal")
GAIT = os.path.join(EXAMPLES, "hero_gait.dal")


def run(*args, cwd=ROOT):
    """Run `python -m dal ...` in a normal (non-isolated) child so the repo root is importable."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-m", "dal", *args], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout, p.stderr


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dal_cli_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_compile_writes_python_and_cpp(self):
        py_dir, cpp_dir = os.path.join(self.tmp, "py"), os.path.join(self.tmp, "cpp")
        code, out, err = run("compile", LEAN, "--py", py_dir, "--cpp", cpp_dir, "--module", "MYGAME_API")
        self.assertEqual(code, 0, err)
        for path in (os.path.join(py_dir, "AlsLean.py"), *(os.path.join(cpp_dir, n) for n in ("AlsLean.h", "AlsLean.cpp", "DalMath.h"))):
            self.assertTrue(os.path.isfile(path), path)
            with open(path, "rb") as f:
                self.assertNotIn(b"\r", f.read(), path)
        with open(os.path.join(cpp_dir, "AlsLean.h"), encoding="utf-8") as f:
            self.assertIn("class MYGAME_API UAlsLean", f.read())
        self.assertEqual(out.count("wrote"), 4)

    def test_second_compile_leaves_files_untouched(self):
        cpp_dir = os.path.join(self.tmp, "cpp")
        run("compile", GAIT, "--cpp", cpp_dir)
        before = {n: os.stat(os.path.join(cpp_dir, n)).st_mtime_ns for n in os.listdir(cpp_dir)}
        code, out, _ = run("compile", GAIT, "--cpp", cpp_dir)
        self.assertEqual(code, 0)
        self.assertEqual(out.count("up to date"), 3)
        after = {n: os.stat(os.path.join(cpp_dir, n)).st_mtime_ns for n in os.listdir(cpp_dir)}
        self.assertEqual(before, after)

    def test_generated_python_runs_standalone(self):
        py_dir = os.path.join(self.tmp, "py")
        self.assertEqual(run("compile", GAIT, "--py", py_dir)[0], 0)
        code = (
            "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
            "import HeroGait\n"
            "c = HeroGait.HeroGait()\n"
            "c.update(0.016)\n"
            "print(c.Steps, c.CurrentGait)\n" % (ROOT, py_dir)
        )
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.split()[0], "0")

    def test_errors_use_file_line_column_and_exit_code_1(self):
        bad = os.path.join(self.tmp, "bad.dal")
        with open(bad, "w", encoding="utf-8", newline="\n") as f:
            f.write("logic Bad;\nstate Speed: float;\nupdate(dt) {\n    Spede = 1.0;\n}\n")
        code, out, err = run("compile", bad, "--py", self.tmp)
        self.assertEqual(code, 1)
        self.assertIn("bad.dal:4:5: error DAL3040: undefined name 'Spede' (did you mean 'Speed'?)", err)
        self.assertIn("    Spede = 1.0;", err)
        self.assertIn("^^^^^", err)
        self.assertIn("1 error(s)", err)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "Bad.py")))

    def test_syntax_error_reports_position(self):
        bad = os.path.join(self.tmp, "syn.dal")
        with open(bad, "w", encoding="utf-8", newline="\n") as f:
            f.write("logic Syn;\nstate X: float\n")
        code, _, err = run("check", bad)
        self.assertEqual(code, 1)
        self.assertIn("syn.dal:2:15: error DAL2001: expected ';' after the state", err)

    def test_missing_standins_message(self):
        src = os.path.join(self.tmp, "ext.dal")
        with open(src, "w", encoding="utf-8", newline="\n") as f:
            f.write("logic Ext;\nextern fn f(x: float) -> float;\nstate S: float;\nupdate(dt) { S = f(1.0); }\n")
        code, _, err = run("check", src)
        self.assertEqual(code, 1)
        self.assertIn("DAL6001", err)
        self.assertIn("--standins", err)
        standins = os.path.join(self.tmp, "my_standins.py")
        with open(standins, "w", encoding="utf-8", newline="\n") as f:
            f.write("def f(x):\n    return x\n")
        self.assertEqual(run("check", src, "--standins", standins)[0], 0)
        # default discovery: <stem>_standins.py next to the source
        shutil.copy(standins, os.path.join(self.tmp, "ext_standins.py"))
        self.assertEqual(run("check", src)[0], 0)

    def test_check_verify_and_dump_ir(self):
        code, out, _ = run("check", LEAN)
        self.assertEqual(code, 0)
        self.assertIn("AlsLean: 5 inputs, 7 states, 1 params, 3 fns, 0 entries", out)
        code, out, _ = run("verify", GAIT, "--frames", "150", "--seed", "3")
        self.assertEqual(code, 0)
        self.assertIn("agree over 150 frames", out)
        code, out, _ = run("dump-ir", GAIT)
        self.assertEqual(code, 0)
        self.assertIn("fn gait_for(speed: float, sprint: bool) -> Gait", out)
        self.assertIn("entry ResetSteps(dt)", out)

    def test_usage_errors(self):
        self.assertEqual(run("compile", GAIT)[0], 2)           # nothing to write
        self.assertEqual(run("compile", os.path.join(self.tmp, "missing.dal"), "--py", self.tmp)[0], 1)
        code, out, _ = run("--version")
        self.assertEqual(code, 0)
        self.assertIn(f"dal {dal.__version__}", out)

    def test_warnings_do_not_fail_the_build(self):
        src = os.path.join(self.tmp, "warn.dal")
        with open(src, "w", encoding="utf-8", newline="\n") as f:
            f.write("logic Warn;\nstate S: float;\n")
        code, _, err = run("check", src)
        self.assertEqual(code, 0)
        self.assertIn("warning DAL5091", err)


if __name__ == "__main__":
    unittest.main()
