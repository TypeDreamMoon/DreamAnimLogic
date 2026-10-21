"""DalLive's pure-Python half (Unreal/DalLive/Content/Python/dal_live: loader, swap, session, bootstrap): the hot-swap rules
-- which state survives, what happens on a compile error or a runtime fault, when a changed file is compiled -- tested
with the mock hosts.  The Unreal half (UeHost, the C++ component) is exercised in PIE by Unreal/TestHost.
"""

import os
import shutil
import sys
import tempfile
import unittest

import _bootstrap  # noqa: F401
from _bootstrap import EXAMPLES, ROOT

PLUGIN_PYTHON = os.path.join(ROOT, "Unreal", "DalLive", "Content", "Python")
if PLUGIN_PYTHON not in sys.path:
    sys.path.insert(0, PLUGIN_PYTHON)

import dal
from dal_live import bootstrap, loader
from dal_live.session import Reporter, Session
from dal_live.swap import migrate_values
from dal.mockhost import MockHost

ANIM_SOURCE = """
logic Cmd;
state N: int;
update(dt) {
    N += 1;
    host.set_gravity_scale(2.0);
}
"""


def check(text, name="t.dal"):
    return dal.check_source(text, name).module


class MigrationTests(unittest.TestCase):
    def migrate(self, old_text, new_text, old_values=None):
        old, new = check(old_text), check(new_text)
        old_values = old_values or {}
        old_vals = {s.name: s.default for s in old.states}
        old_vals.update({i.name: i.default for i in old.inputs})
        old_vals.update(old_values)
        new_vals = {s.name: s.default for s in new.states}
        new_vals.update({i.name: i.default for i in new.inputs})
        report = migrate_values(old, old_vals, new, new_vals)
        return report, new_vals

    def test_matching_states_keep_their_values_and_new_ones_start_at_the_default(self):
        report, vals = self.migrate(
            "logic L : actor;\nstate A: int;\nstate B: float;\nupdate(dt) { A += 1; }",
            "logic L : actor;\nstate A: int;\nstate C: int = 7;\nupdate(dt) { A += 1; }",
            {"A": 5, "B": 2.5})
        self.assertEqual(vals, {"A": 5, "C": 7})
        self.assertEqual(report.kept, ["A"])
        self.assertEqual(report.added, ["C"])
        self.assertEqual(report.dropped, ["B"])
        self.assertIn("kept 1", report.summary())

    def test_a_state_whose_type_changed_restarts(self):
        report, vals = self.migrate(
            "logic L : actor;\nstate A: int;\nupdate(dt) { A += 1; }",
            "logic L : actor;\nstate A: float = 1.5;\nupdate(dt) { A += 1.0; }", {"A": 9})
        self.assertEqual(vals["A"], 1.5)
        self.assertEqual(report.reset, [("A", "type changed")])

    def test_enum_members_are_matched_by_name(self):
        old = "logic L : actor;\nenum E { A, B, C }\nstate S: E = E.C;\nupdate(dt) { }"
        new = "logic L : actor;\nenum E { Z, A, B, C }\nstate S: E;\nupdate(dt) { }"      # a member was inserted in front
        report, vals = self.migrate(old, new, {"S": 2})                                    # was C (index 2)
        self.assertEqual(vals["S"], 3)                                                     # is still C (index 3)
        gone = "logic L : actor;\nenum E { A, B }\nstate S: E;\nupdate(dt) { }"
        report, vals = self.migrate(old, gone, {"S": 2})
        self.assertEqual(vals["S"], 0)
        self.assertEqual(report.reset[0][0], "S")

    def test_host_bound_inputs_are_not_carried_but_free_inputs_are(self):
        src = "logic L : actor;\ninput G: bool <- host.is_grounded;\ninput Free: float;\nupdate(dt) { }"
        report, vals = self.migrate(src, src, {"G": True, "Free": 3.0})
        self.assertEqual(vals["Free"], 3.0)
        self.assertIs(vals["G"], False)                          # the host's value is sampled again; the old True is not carried
        self.assertEqual(report.kept, ["Free"])

    def test_the_montage_section_enum_follows_an_inserted_section(self):
        old = "logic L : actor;\nmontage M { A, B }\nstate S: MSection;\nupdate(dt) { }"
        new = "logic L : actor;\nmontage M { Pre, A, B }\nstate S: MSection;\nupdate(dt) { }"
        _report, vals = self.migrate(old, new, {"S": 2})                                    # B
        self.assertEqual(vals["S"], 3)


class ParamTests(unittest.TestCase):
    def test_parse_param(self):
        from dal.types import BOOL, FLOAT, INT, QUAT, ROTATOR, VEC3, EnumType

        self.assertEqual(loader.parse_param(FLOAT, " 600.5 "), 600.5)
        self.assertEqual(loader.parse_param(INT, "3"), 3)
        self.assertIs(loader.parse_param(BOOL, "True"), True)
        self.assertIs(loader.parse_param(BOOL, "off"), False)
        self.assertEqual(loader.parse_param(VEC3, "(1, 2, 3)"), (1.0, 2.0, 3.0))
        self.assertEqual(loader.parse_param(ROTATOR, "0,90,0"), (0.0, 90.0, 0.0))
        self.assertEqual(len(loader.parse_param(QUAT, "0,0,0,1")), 4)
        self.assertEqual(loader.parse_param(EnumType("E", ["A", "B"]), "B"), 1)
        for ty, text in ((BOOL, "maybe"), (VEC3, "1,2"), (FLOAT, "fast")):
            with self.assertRaises(ValueError):
                loader.parse_param(ty, text)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dal_live_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "melee_combo.dal")
        shutil.copyfile(os.path.join(EXAMPLES, "melee_combo.dal"), self.path)
        self.host = MockHost()
        self.reporter = Reporter()
        self.now = 100.0

    def session(self, **kw):
        s = Session(self.path, self.host, self.reporter, **kw)
        self.assertTrue(s.start())
        return s

    def edit(self, transform):
        with open(self.path, encoding="utf-8") as f:
            text = f.read()
        new = transform(text)
        self.assertNotEqual(new, text)
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
        # make sure the fingerprint changes even on a coarse file system clock
        st = os.stat(self.path)
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 7_000_000))

    def settle(self, s, steps=4):
        """Poll a few times, 0.1 s apart (the editor polls ten times a second): True if a program was swapped in."""
        swapped = False
        for _ in range(steps):
            self.now += 0.1
            swapped = s.poll(self.now) or swapped
        return swapped

    def hit(self, s):
        self.host.events.append(("notify", "Hit"))
        s.tick(1 / 60)

    def test_a_loaded_program_runs_through_the_host(self):
        s = self.session()
        self.assertEqual(s.generation, 1)
        self.assertEqual(s.module.kind, "actor")
        self.hit(s)
        self.hit(s)
        self.assertEqual(s.snapshot()["Hits"], 2)
        self.assertEqual(s.stats.count, 2)

    def test_a_changed_file_is_swapped_in_with_the_state_kept(self):
        s = self.session()
        for _ in range(3):
            self.hit(s)
        self.assertEqual(s.snapshot()["Hits"], 3)
        self.edit(lambda t: t.replace("Hits += 1;", "Hits += 10;"))
        self.assertTrue(self.settle(s))
        self.assertEqual(s.generation, 2)
        self.assertEqual(s.snapshot()["Hits"], 3)               # kept
        self.hit(s)
        self.assertEqual(s.snapshot()["Hits"], 13)              # the new behaviour
        self.assertIn("kept", s.last_report.summary())
        self.assertEqual([k for k, _ in self.reporter.messages if k == "reloaded"], ["reloaded", "reloaded"])

    def test_nothing_happens_while_the_file_is_still_being_written(self):
        s = self.session()
        self.edit(lambda t: t.replace("Hits += 1;", "Hits += 10;"))
        self.now += 0.1
        self.assertFalse(s.poll(self.now))                      # first sighting of the new fingerprint
        self.now += 0.05
        self.assertFalse(s.poll(self.now))                      # still inside the debounce time
        self.edit(lambda t: t + "\n// still typing\n")          # the file changed again: the wait starts over
        self.now += 0.1
        self.assertFalse(s.poll(self.now))
        self.now += 0.05
        self.assertFalse(s.poll(self.now))
        self.assertEqual(s.generation, 1)
        self.assertTrue(self.settle(s))
        self.assertEqual(s.generation, 2)

    def test_a_compile_error_keeps_the_last_good_program_running(self):
        s = self.session()
        self.hit(s)
        self.edit(lambda t: t.replace("Hits += 1;", "Hits += Oops;"))
        self.assertFalse(self.settle(s))
        self.assertEqual(s.generation, 1)
        self.assertTrue(any("error DAL" in ln and "Oops" in ln for ln in s.last_errors))
        self.assertTrue(s.last_errors[0].startswith(self.path + "("), s.last_errors[0])      # file(line,col): error ...
        self.assertEqual([k for k, _ in self.reporter.messages][-1], "compile_error")
        self.hit(s)
        self.assertEqual(s.snapshot()["Hits"], 2)               # still the old program
        n_messages = len(self.reporter.messages)
        self.settle(s, 6)
        self.assertEqual(len(self.reporter.messages), n_messages)   # the broken file is reported once, not on every poll
        self.edit(lambda t: t.replace("Hits += Oops;", "Hits += 5;"))
        self.assertTrue(self.settle(s))
        self.assertEqual(s.generation, 2)
        self.assertEqual(s.last_errors, [])
        self.hit(s)
        self.assertEqual(s.snapshot()["Hits"], 7)

    def test_a_program_that_never_compiled_is_not_running_until_the_file_is_fixed(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("logic L : actor;\nstate A: int\n")
        s = Session(self.path, self.host, self.reporter)
        self.assertFalse(s.start())
        self.assertFalse(s.running)
        s.tick(1 / 60)                                          # nothing to run: no error
        shutil.copyfile(os.path.join(EXAMPLES, "melee_combo.dal"), self.path)
        st = os.stat(self.path)
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 9_000_000))
        self.assertTrue(self.settle(s))
        self.assertTrue(s.running)
        self.assertEqual(s.generation, 1)

    def test_param_overrides_beat_the_defaults_of_every_generation(self):
        s = self.session(overrides={"AttackMoveSpeed": "200"})
        self.assertEqual(s.interp.values["AttackMoveSpeed"], 200.0)
        self.assertEqual(s.interp.values["WalkSpeed"], 600.0)
        self.edit(lambda t: t.replace("param AttackMoveSpeed: float = 120.0;", "param AttackMoveSpeed: float = 60.0;")
                  .replace("param WalkSpeed: float = 600.0;", "param WalkSpeed: float = 650.0;"))
        self.assertTrue(self.settle(s))
        self.assertEqual(s.interp.values["AttackMoveSpeed"], 200.0)      # the override stays
        self.assertEqual(s.interp.values["WalkSpeed"], 650.0)            # the new default applies (params are not carried)

    def test_a_runtime_error_stops_the_program_until_the_file_changes(self):
        class Exploding(MockHost):
            armed = False

            @property
            def is_grounded(self):
                if self.armed:
                    raise RuntimeError("the pawn is gone")
                return True

        self.host = Exploding()
        s = self.session()
        s.tick(1 / 60)
        self.host.armed = True
        s.tick(1 / 60)
        self.assertIn("the pawn is gone", s.fault)
        self.assertFalse(s.running)
        self.assertEqual(self.reporter.messages[-1][0], "runtime_error")
        before = s.stats.count
        s.tick(1 / 60)
        self.assertEqual(s.stats.count, before)                  # stopped: no more updates, no log spam
        self.host.armed = False
        self.edit(lambda t: t + "\n// touched\n")
        self.assertTrue(self.settle(s))
        self.assertIsNone(s.fault)
        s.tick(1 / 60)
        self.assertGreater(s.stats.count, before)

    def test_changing_the_logic_kind_restarts_from_the_defaults(self):
        s = self.session()
        self.hit(s)
        self.assertEqual(s.snapshot()["Hits"], 1)
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write("logic Counter;\nstate Hits: int;\nupdate(dt) { Hits += 1; }\n")
        st = os.stat(self.path)
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))
        self.assertTrue(self.settle(s))
        self.assertEqual(s.module.kind, "anim")
        self.assertIsNone(s.last_report)                         # no migration across kinds
        self.assertEqual(s.snapshot()["Hits"], 0)

    def test_anim_logic_commands_run_at_the_start_of_the_next_update(self):
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write(ANIM_SOURCE)
        s = self.session()
        s.tick(1 / 60)
        self.assertEqual(self.host.calls, [])                    # queued, not executed (generated NativeUpdateAnimation order)
        s.tick(1 / 60)
        self.assertEqual(self.host.calls, [("set_gravity_scale", (2.0,))])
        s.tick(1 / 60)
        self.assertEqual(len(self.host.calls), 2)
        self.assertEqual(s.snapshot()["N"], 3)

    def test_an_anim_post_update_runs_after_the_update_and_its_commands_wait_like_the_others(self):
        source = "logic Pu : anim post_flush;\nstate N: int;\nstate P: int;\nupdate(dt) { N += 1; }\npost_update { P = N * 10; host.set_gravity_scale(P * 1.0); }\n"
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write(source)
        s = self.session()
        s.tick(1 / 60)
        self.assertEqual((s.snapshot()["N"], s.snapshot()["P"]), (1, 10))        # the block ran in the same tick, after the update
        self.assertEqual(self.host.calls, [])                                    # its command is queued for the start of the next one
        s.tick(1 / 60)
        self.assertEqual(self.host.calls, [("set_gravity_scale", (10.0,))])
        self.assertEqual(s.snapshot()["P"], 20)

    def test_actor_logic_commands_run_at_the_end_of_the_same_update(self):
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write(ANIM_SOURCE.replace("logic Cmd;", "logic Cmd : actor;"))
        s = self.session()
        s.tick(1 / 60)
        self.assertEqual(self.host.calls, [("set_gravity_scale", (2.0,))])

    def test_a_swap_keeps_a_queued_anim_command_out_of_the_new_program(self):
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write(ANIM_SOURCE)
        s = self.session()
        s.tick(1 / 60)                                           # leaves one command waiting
        self.edit(lambda t: t.replace("2.0", "3.0"))
        self.assertTrue(self.settle(s))
        s.tick(1 / 60)
        self.assertEqual(self.host.calls, [])                    # the waiting command of the replaced program is dropped
        s.tick(1 / 60)
        self.assertEqual(self.host.calls, [("set_gravity_scale", (3.0,))])

    def test_the_standins_file_is_watched_too(self):
        src = os.path.join(EXAMPLES, "als_lean.dal")
        for name in ("als_lean.dal", "als_lean_standins.py"):
            shutil.copyfile(os.path.join(EXAMPLES, name), os.path.join(self.tmp, name))
        self.path = os.path.join(self.tmp, "als_lean.dal")
        s = Session(self.path, MockHost(), self.reporter)
        self.assertTrue(s.start())
        self.assertEqual(len(loader.fingerprint(self.path)), 2)
        stand = os.path.join(self.tmp, "als_lean_standins.py")
        with open(stand, "a", encoding="utf-8", newline="\n") as f:
            f.write("\n# edited\n")
        st = os.stat(stand)
        os.utime(stand, ns=(st.st_atime_ns, st.st_mtime_ns + 6_000_000))
        self.assertTrue(self.settle(s))
        self.assertEqual(s.generation, 2)
        self.assertTrue(os.path.isfile(src))


class LoaderTests(unittest.TestCase):
    def test_diagnostics_are_visual_studio_lines_with_the_full_path(self):
        tmp = tempfile.mkdtemp(prefix="dal_live_")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "bad.dal")
        with open(path, "w", encoding="utf-8") as f:
            f.write("logic L : actor;\nstate A: int;\nupdate(dt) {\n    A = Missing;\n}\n")
        res = loader.load_program(path)
        self.assertFalse(res.ok)
        self.assertEqual(res.errors[0], f"{path}(4,9): error DAL3040: undefined name 'Missing'")
        self.assertEqual(res.errors[1].strip(), "A = Missing;")
        self.assertTrue(res.errors[2].strip().startswith("^"))

    def test_a_missing_file_is_a_report_not_an_exception(self):
        res = loader.load_program(os.path.join(tempfile.gettempdir(), "dal_live_does_not_exist.dal"))
        self.assertFalse(res.ok)
        self.assertIn("cannot read", res.errors[0])
        self.assertIsNone(res.fingerprint)


class BootstrapTests(unittest.TestCase):
    def test_the_compiler_is_found_by_the_repository_layout_and_the_environment(self):
        found = bootstrap.find_dal()
        self.assertIsNotNone(found)
        self.assertTrue(os.path.isfile(os.path.join(found[1], "dal", "__init__.py")))
        old = os.environ.get("DREAMANIM_DAL")
        os.environ["DREAMANIM_DAL"] = ROOT
        try:
            self.assertEqual(bootstrap.candidates()[0], ("environment variable DREAMANIM_DAL", ROOT))
            self.assertEqual(bootstrap.find_dal()[0], "environment variable DREAMANIM_DAL")
        finally:
            if old is None:
                del os.environ["DREAMANIM_DAL"]
            else:
                os.environ["DREAMANIM_DAL"] = old

    def test_a_project_config_file_names_the_compiler(self):
        tmp = tempfile.mkdtemp(prefix="dal_live_")
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "Config"))
        with open(os.path.join(tmp, "Config", "DalLive.json"), "w", encoding="utf-8") as f:
            f.write('{"compiler": "%s"}' % ROOT.replace("\\", "/"))
        sources = [s for s, _ in bootstrap.candidates(tmp)]
        self.assertIn(os.path.join(tmp, "Config", "DalLive.json"), sources)


if __name__ == "__main__":
    unittest.main()
