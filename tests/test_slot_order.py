"""Two options of an actor logic with tick slots: `slot_events` (an event raised between two tick slots is handled in the second one, in the
same tick) and `before Peer` / `after Peer` (the order of two logics inside a tick slot)."""

import unittest

import _bootstrap  # noqa: F401

import cpp_system
import dal
from dal import harness, ir
from dal.cppbackend import generate_cpp
from dal.pybackend import generate_python, load_class
from dal.interp import Interpreter
from dal.mockhost import MockHost

try:
    import test_cpp_compile as tcc
except Exception:                                   # pragma: no cover
    tcc = None

CHR = """
logic Chr : actor{options} in pre_movement;
state Order: int;
state Seen: int;
state Jumps: int;
pub method Touch(v: int) {{ Order = (Order * 10 + v) % 1000000; }}
on jumped {{ Jumps += 1; Seen = (Seen * 10 + 1) % 1000000; }}
update(dt) in pre_movement {{ Order = (Order * 10 + 2) % 1000000; Seen = (Seen * 10 + 2) % 1000000; }}
update(dt) in after_movement {{ Seen = (Seen * 10 + 3) % 1000000; }}
"""
INP = """
peer Chr: Chr;
logic Inp : actor{options} in pre_movement;
state N: int;
update(dt) {{ N += 1; Chr.Touch(1); }}
"""


def files(chr_options="", inp_options=""):
    return {"Chr.dal": CHR.format(options=chr_options), "Inp.dal": INP.format(options=inp_options)}


def system(chr_options="", inp_options="", names=("Chr.dal", "Inp.dal")):
    f = files(chr_options, inp_options)
    return {n[:-4]: dal.check_source(f[n], n, files=f).module for n in names}


def errors(src, extra=None):
    try:
        dal.check_source(src, "t.dal", files=extra or {})
    except dal.DalError as e:
        return [(d.code, d.pos.line, d.pos.col) for d in e.diagnostics if d.severity == "error"]
    return []


def warnings(src, extra=None):
    return [d.code for d in dal.check_source(src, "t.dal", files=extra or {}).warnings]


class DiagnosticTests(unittest.TestCase):
    def test_the_options_belong_to_actor_logic(self):
        self.assertEqual(errors("logic T : anim slot_events;")[0][0], "DAL2026")
        self.assertEqual(errors("logic T : anim before Other;")[0][0], "DAL2026")
        self.assertEqual(errors("logic T : actor slot_events in after_movement;\nupdate(dt) { }"), [])

    def test_the_order_names_a_peer(self):
        base = {"Chr.dal": CHR.format(options="")}
        inp = "peer Chr: Chr;\nlogic Inp : actor {rel} Chr in pre_movement;\nupdate(dt) {{ }}"
        self.assertEqual(errors(inp.format(rel="before"), base), [])
        self.assertEqual(errors(inp.format(rel="after"), base), [])
        self.assertEqual(errors("logic Inp : actor before Chr in pre_movement;\nupdate(dt) { }", base)[0][0], "DAL3040")        # not declared as a peer
        self.assertEqual(errors("state Chr: int;\nlogic Inp : actor before Chr in pre_movement;\nupdate(dt) { }", base)[0][0], "DAL3076")
        self.assertEqual(errors("peer Chr: Chr;\nlogic Inp : actor before Chr after Chr in pre_movement;\nupdate(dt) { }", base)[0][0], "DAL5069")

    def test_an_animation_instance_has_no_slots_to_order(self):
        base = {"Anim.dal": "logic Anim;\nupdate(dt) { }"}
        self.assertEqual(errors("peer Anim: Anim;\nlogic Inp : actor before Anim in pre_movement;\nupdate(dt) { }", base)[0][0], "DAL5068")

    def test_no_slot_in_common_is_only_a_warning(self):
        base = {"Chr.dal": CHR.format(options="")}                                      # Chr has pre_movement and after_movement
        self.assertEqual(warnings("peer Chr: Chr;\nlogic Inp : actor before Chr in after_anim;\nupdate(dt) { }", base), ["DAL5095"])
        self.assertEqual(warnings("peer Chr: Chr;\nlogic Inp : actor before Chr in after_movement;\nupdate(dt) { }", base), [])

    def test_the_module_records_the_options(self):
        mods = system(" slot_events", " before Chr")
        self.assertTrue(mods["Chr"].slot_events)
        self.assertEqual([(o.relation, o.peer, o.logic, o.slots) for o in mods["Inp"].order], [("before", "Chr", "Chr", ("pre_movement",))])
        self.assertEqual(mods["Inp"].peers[0].slots, ["pre_movement", "after_movement"])
        self.assertIn("before Chr", ir.dump(mods["Inp"]))
        self.assertFalse(system()["Chr"].slot_events)


class OrderSemanticsTests(unittest.TestCase):
    FRAMES = [{"dt": 0.1}, {"dt": 0.1}]

    def run_system(self, mods, backend):
        return harness.run_system(mods, self.FRAMES, backend)

    def test_without_the_option_the_module_order_decides(self):
        mods = system()
        self.assertEqual(harness.default_schedule(mods)[:2], [("Chr", "pre_movement"), ("Inp", "pre_movement")])
        out = self.run_system(mods, "python")
        self.assertEqual(out[0]["Chr"]["Order"], 21)                             # the character's block first, then the input's call

    def test_before_runs_the_logic_first(self):
        mods = system("", " before Chr")
        self.assertEqual(harness.default_schedule(mods), [("Inp", "pre_movement"), ("Chr", "pre_movement"), ("Chr", "after_movement")])
        for backend in ("python", "interp"):
            out = self.run_system(mods, backend)
            self.assertEqual([f["Chr"]["Order"] for f in out], [12, 1212], backend)

    def test_after_is_the_same_order_said_from_the_other_side(self):
        mods = system("", " after Chr", names=("Chr.dal", "Inp.dal"))
        self.assertEqual(harness.default_schedule(mods)[:2], [("Chr", "pre_movement"), ("Inp", "pre_movement")])          # already in that order
        f = files()
        f["Chr.dal"] = f["Chr.dal"].replace("logic Chr : actor in pre_movement;", "peer Inp: Inp;\nlogic Chr : actor after Inp in pre_movement;")
        f["Inp.dal"] = f["Inp.dal"].replace("peer Chr: Chr;\n", "")
        f["Inp.dal"] = f["Inp.dal"].replace("Chr.Touch(1);", "")
        mods = {n[:-4]: dal.check_source(f[n], n, files=f).module for n in ("Chr.dal", "Inp.dal")}
        self.assertEqual(harness.default_schedule(mods)[:2], [("Inp", "pre_movement"), ("Chr", "pre_movement")])

    def test_a_cycle_is_reported(self):
        mods = system("", " before Chr")
        mods["Chr"].order.append(ir.OrderDef("before", "Inp", "Inp", ("pre_movement",)))
        with self.assertRaises(ValueError):
            harness.default_schedule(mods)

    def test_the_python_class_carries_the_order(self):
        text = generate_python(system("", " before Chr")["Inp"])
        self.assertIn("DAL_ORDER = (('before', 'Chr', 'Chr', ('pre_movement',)), )", text)
        self.assertNotIn("DAL_ORDER", generate_python(system()["Inp"]))


class SlotEventSemanticsTests(unittest.TestCase):
    """An event that arrives between the slots (the movement component raised it) is handled in the next slot of the same tick with slot_events."""

    def drive(self, module, backend):
        host = MockHost()
        logic = load_class(module, generate_python(module))(host) if backend == "python" else Interpreter(module, host)
        trace = []
        for frame in range(2):
            logic.update_phase("pre_movement", 0.1)
            host.events.append(("jumped",))                                          # raised while the character moved
            logic.update_phase("after_movement", 0.1)
            values = logic.values if backend == "interp" else vars(logic)
            trace.append((values["Jumps"], values["Seen"]))
        return trace

    def test_the_event_is_handled_in_the_slot_after_it(self):
        module = system(" slot_events")["Chr"]
        for backend in ("python", "interp"):
            self.assertEqual(self.drive(module, backend), [(1, 213), (2, 213213)], backend)       # pre (2), event (1), after (3)

    def test_without_it_the_event_waits_for_the_first_slot_of_the_next_tick(self):
        module = system()["Chr"]
        for backend in ("python", "interp"):
            self.assertEqual(self.drive(module, backend), [(0, 23), (1, 23123)], backend)

    def test_both_agree_on_random_frames(self):
        for options in ("", " slot_events"):
            module = system(options)["Chr"]
            self.assertEqual(harness.verify(module, 100, 2), [], options)


class CppTests(unittest.TestCase):
    def test_the_slot_events_option(self):
        cpp = generate_cpp(system(" slot_events")["Chr"])["Chr.cpp"]
        self.assertIn("DalTakeEvents();                                  // slot_events", cpp)
        self.assertNotIn("if (Phase == 0)", cpp.split("DalRunPhase")[-1].split("}")[0] + "")
        self.assertEqual(cpp.count("\tDalDispatchEvents();"), 2)                      # the start of each of the two slots
        plain = generate_cpp(system()["Chr"])["Chr.cpp"]
        self.assertIn("if (Phase == 0)", plain)
        self.assertEqual(plain.count("\tDalDispatchEvents();"), 1)

    def test_the_order(self):
        inp = generate_cpp(system("", " before Chr")["Inp"])["Inp.cpp"]
        self.assertIn("// before Chr", inp)
        self.assertIn("Chr->DalGetPhaseTick(0).AddPrerequisite(this, DalPhaseTicks[0]);", inp)
        after = generate_cpp(system("", " after Chr")["Inp"])["Inp.cpp"]
        self.assertIn("DalPhaseTicks[0].AddPrerequisite(Chr, Chr->DalGetPhaseTick(0));", after)

    def test_the_tick_function_is_public_on_every_phased_logic(self):
        files_ = generate_cpp(system()["Chr"])
        self.assertIn("FActorComponentTickFunction& DalGetPhaseTick(const int32 Slot);", files_["Chr.h"])
        self.assertIn("FActorComponentTickFunction& UChr::DalGetPhaseTick(const int32 Slot)", files_["Chr.cpp"])

    @unittest.skipIf(tcc is None or tcc.COMPILER is None, "no C++ compiler found")
    def test_the_system_matches_python(self):
        from test_cpp_system import compare_system

        for chr_options in (" slot_events", ""):
            mods = system(chr_options, " before Chr")
            schedule = harness.default_schedule(mods)
            self.assertEqual(schedule, [("Inp", "pre_movement"), ("Chr", "pre_movement"), ("Chr", "after_movement")])
            frames = [{"dt": 0.125, "step_events": {2: [("jumped",)]} if k % 2 == 0 else {}} for k in range(6)]        # raised between the slots of every other tick
            cpp, output = cpp_system.build_and_run_system(mods, frames, schedule)
            if tcc.COMPILER[0] == "msvc":
                self.assertNotIn("warning", output.lower(), output)
            py = harness.run_system(mods, frames, "python", schedule)
            compare_system(self, mods, py, cpp)
            self.assertEqual([f["Chr"]["Jumps"] for f in py], [1, 1, 2, 2, 3, 3] if chr_options else [0, 1, 1, 2, 2, 3], chr_options)


if __name__ == "__main__":
    unittest.main()
