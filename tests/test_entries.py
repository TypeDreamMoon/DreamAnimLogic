"""Entries with typed parameters and defaults, and linked animation instances that forward them."""

import unittest

import _bootstrap  # noqa: F401

import cpp_system
import dal
import test_cpp_compile as tcc
from dal import harness
from dal.cppbackend import generate_cpp
from dal.interp import Interpreter
from dal.pybackend import generate_python, load_class

MAIN = """
enum Hips { Forward, Backward, Left }
tags Mode = "Als.Mode" { View, Velocity }
struct Blend { A: float = 1.0; B: float; }
logic Main;
input Speed: float;
state H: Hips;
state Clip: sequence = none;
state Mon: montage = none;
state M: Mode;
state Blendv: Blend;
state Count: int;
state T: float;
state Played: float;
state Last: float;
entry SetHips(NewHips: Hips = Hips.Forward) linked { H = NewHips; Count += 1; }
entry Play(Sequence: sequence, BlendIn: float = 0.2, PlayRate: float = 1.5, Where: Mode = Mode.View, Bl: Blend = Blend(A: 4.0)) linked {
    Clip = Sequence; T = BlendIn; Played = PlayRate; Count += 1;
    M = Where; Blendv = Bl;
}
entry Montage(Which: montage = none, Mix: vec2 = vec2(1.0, 2.0)) { Mon = Which; Last = Mix.x + Mix.y; }
entry Local(dt, X: float = 2.0) { T += dt * X; Last = X; }
entry Plain(dt) linked { T += dt; Count += 1; }
entry NoArgs() { Count += 10; }
update(dt) { Played += 0.0; }
"""

SUB = """
logic Sub : anim linked(Main);
state Seen: int;
entry Own(dt) { Seen += 1; }
update(dt) { Seen += 0; }
"""

FILES = {"Main.dal": MAIN, "Sub.dal": SUB}


def errors(src, name="t.dal", files=None):
    try:
        dal.check_source(src, name, files=files)
    except dal.DalError as e:
        return [(d.code, d.pos.line, d.pos.col) for d in e.diagnostics if d.severity == "error"]
    return []


def warnings(src, name="t.dal", files=None):
    return [d.code for d in dal.check_source(src, name, files=files).warnings]


HDR = "enum Hips { Forward, Backward, Left }\nstruct Blend { A: float = 1.0; }\nlogic T;\nstate N: int;\nstate F: float;\n"


class EntrySyntaxTests(unittest.TestCase):
    def codes(self, body, header=HDR):
        return [c for c, _l, _c in errors(header + body)]

    def test_the_forms_of_the_parameter_list(self):
        for ok in ("entry E(dt) { F += dt; }", "entry E() { N += 1; }", "entry E(X: float) { F = X; }", "entry E(dt, X: float = 1.5) { F = X + dt; }",
                   "entry E(X: float, Y: int = 2, Z: Hips = Hips.Left) { F = X; }", "entry E(B: Blend = Blend(A: 3.0)) { F = B.A; }",
                   "entry E(X: float = 1.0 + 2.0) { F = X; }"):
            with self.subTest(ok=ok):
                self.assertEqual(self.codes(ok), [])

    def test_declaration_errors(self):
        cases = [
            ("entry E(X: float = 1.0, Y: float) { }", ["DAL3013"]),
            ("entry E(X: float, X: float) { }", ["DAL3001"]),
            ("entry E(dt, dt: float) { }", ["DAL3001"]),
            ("entry E(N: float) { }", ["DAL3005"]),                          # hides a state
            ("entry E(X: hit) { }", ["DAL4023"]),
            ("entry E(X: Nope) { }", ["DAL3041"]),
            ("entry E(X: float = true) { }", ["DAL4002"]),
            ("entry E(X: float = F) { }", ["DAL5002"]),
            ("entry E(X: Hips = Hips.Nope) { }", ["DAL3032"]),
            ("entry E(from: float) { }", ["DAL3004"]),
            ("entry E(X: float) linked { }", []),                            # anim logics have linked entries
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual(self.codes(body), expected)

    def test_a_linked_entry_needs_an_animation_logic(self):
        self.assertEqual(self.codes("entry E() linked { }", HDR.replace("logic T;", "logic T : actor;")), ["DAL3014"])

    def test_positions(self):
        self.assertEqual(errors(HDR + "entry E(X: float = 1.0, Y: float) { }"), [("DAL3013", 6, 25)])
        self.assertEqual(errors(HDR + "entry E(\n    X: hit\n) { }"), [("DAL4023", 7, 8)])

    def test_the_old_form_is_untouched(self):
        m = dal.check_source("logic Q;\nstate X: float;\nentry Bump(delta) { X += delta; }\nupdate(dt) { }", "q.dal").module
        e = m.entries[0]
        self.assertEqual((e.dt_name, e.params, e.linked), ("delta", [], False))
        src = generate_python(m)
        self.assertIn("def Bump(self):\n        \"\"\"Entry: runs with the delta time of the last update.\"\"\"\n        delta = self._dt", src)
        self.assertNotIn("DAL_ENTRY_PARAMS", src)


class LinkedCheckTests(unittest.TestCase):
    def codes(self, sub, main=MAIN):
        files = {"Main.dal": main, "Sub.dal": sub}
        return [c for c, _l, _c in errors(sub, "Sub.dal", files)]

    def test_the_linked_logic_forwards_the_linked_entries(self):
        m = dal.check_source(SUB, "Sub.dal", files=FILES).module
        self.assertEqual(m.linked.parent, "Main")
        self.assertEqual([e.name for e in m.linked.entries], ["SetHips", "Play", "Plain"])
        play = m.linked.entries[1]
        self.assertEqual([(p.name, p.has_default) for p in play.params], [("Sequence", False), ("BlendIn", True), ("PlayRate", True), ("Where", True), ("Bl", True)])
        self.assertEqual(play.params[1].default, 0.2)
        self.assertEqual([e.name for e in m.entries], ["Own"])

    def test_errors(self):
        self.assertEqual(self.codes("logic Sub : anim linked(Nope);\nupdate(dt) { }"), ["DAL3078"])
        self.assertEqual(self.codes("logic Sub : anim linked(Sub);\nupdate(dt) { }"), ["DAL3078"])
        self.assertEqual(self.codes("logic Sub : anim linked(Main);\nentry SetHips() { }\nupdate(dt) { }"), ["DAL3001"])      # a forwarded name is taken
        self.assertEqual(self.codes("logic Sub : actor linked(Main);\nupdate(dt) { }"), ["DAL2024"])
        actor = MAIN.replace("logic Main;", "logic Main : actor;").replace(" linked {", " {")
        self.assertEqual(self.codes("logic Sub : anim linked(Main);\nupdate(dt) { }", actor), ["DAL3078"])

    def test_a_main_without_linked_entries_is_a_warning(self):
        main = MAIN.replace(" linked {", " {")
        files = {"Main.dal": main, "Sub.dal": SUB}
        self.assertEqual(warnings(SUB, "Sub.dal", files), ["DAL5092"])

    def test_without_the_main_file_next_to_it(self):
        self.assertEqual([c for c, _l, _c in errors(SUB, "Sub.dal", {"Sub.dal": SUB})], ["DAL3078"])

    def test_ir_dump_lists_entries_with_parameters(self):
        from dal import ir

        text = ir.dump(dal.check_source(MAIN, "Main.dal").module)
        self.assertIn("entry Play(Sequence: sequence, BlendIn: float = 0.2, PlayRate: float = 1.5", text)
        self.assertIn("entry Local(dt, X: float = 2.0)", text)
        self.assertIn("entry SetHips(NewHips: Hips = 0) linked", text)


class Reader:
    """The same attribute access for a generated class and for the interpreter (whose values live in a dict)."""

    def __init__(self, obj):
        self._obj = obj

    def __getattr__(self, name):
        values = getattr(self._obj, "values", None)
        if isinstance(self._obj, Interpreter) and name in values:
            return values[name]
        return getattr(self._obj, name)


class EntryCallTests(unittest.TestCase):
    def setUp(self):
        self.module = dal.check_source(MAIN, "Main.dal").module

    def objects(self):
        cls = load_class(self.module)
        return Reader(cls(None)), Reader(Interpreter(self.module))

    def both(self, fn):
        for obj in self.objects():
            with self.subTest(impl=type(obj._obj).__name__):
                fn(obj)

    def test_defaults_names_and_positions(self):
        def check(o):
            o.call_entry("SetHips")
            self.assertEqual(int(o.H), 0)
            o.call_entry("SetHips", "Left")                            # by member name
            self.assertEqual(int(o.H), 2)
            o.call_entry("SetHips", NewHips=1)                         # by index
            self.assertEqual(int(o.H), 1)
            o.call_entry("SetHips", NewHips="Hips.Backward")
            self.assertEqual(int(o.H), 1)
            o.call_entry("Play", "A_Stop")                             # the rest from the defaults
            self.assertEqual((o.Clip, o.T, o.Played, o.M), ("A_Stop", 0.2, 1.5, "Als.Mode.View"))
            self.assertEqual((o.Blendv.A, o.Blendv.B), (4.0, 0.0))
            o.call_entry("Play", Sequence="A_Go", PlayRate=3, Where="Als.Mode.Velocity", Bl={"A": 9.0, "B": 8.0})
            self.assertEqual((o.Clip, o.T, o.Played, o.M, o.Blendv.A, o.Blendv.B), ("A_Go", 0.2, 3.0, "Als.Mode.Velocity", 9.0, 8.0))
            o.call_entry("Play", "A_Go", 0.5, Bl=None)                 # None for a struct: its default
            self.assertEqual((o.T, o.Blendv.A), (0.5, 4.0))
            self.assertEqual(o.Count, 7)

        self.both(check)

    def test_the_delta_time_and_a_default_parameter(self):
        def check(o):
            o.update(0.5)
            o.call_entry("Local")
            self.assertEqual((o.T, o.Last), (1.0, 2.0))
            o.call_entry("Local", X=4.0)
            self.assertEqual((o.T, o.Last), (3.0, 4.0))
            o.call_entry("Plain")
            self.assertEqual(o.T, 3.5)
            o.call_entry("NoArgs")
            self.assertEqual(o.Count, 11)

        self.both(check)

    def test_assets_and_vectors(self):
        def check(o):
            o.call_entry("Montage", "AM_Roll", (3.0, 4.0))
            self.assertEqual((o.Mon, o.Last), ("AM_Roll", 7.0))
            o.call_entry("Montage")
            self.assertEqual((o.Mon, o.Last), (None, 3.0))

        self.both(check)

    def test_wrong_calls(self):
        def check(o):
            with self.assertRaises(TypeError):
                o.call_entry("Play")                                   # Sequence is required
            with self.assertRaises(TypeError):
                o.call_entry("Play", "A", Nope=1)
            with self.assertRaises(TypeError):
                o.call_entry("Play", "A", Sequence="B")
            with self.assertRaises(TypeError):
                o.call_entry("NoArgs", 1)
            with self.assertRaises((ValueError, KeyError)):
                o.call_entry("SetHips", "Sideways")
            with self.assertRaises((KeyError, Exception)):
                o.call_entry("Nope")

        self.both(check)

    def test_generated_python_has_the_parameter_table(self):
        src = generate_python(self.module)
        self.assertIn("DAL_ENTRY_PARAMS = {", src)
        self.assertIn("'Play': (('Sequence', 'sequence', False), ('BlendIn', 'float', True)", src)
        self.assertIn("def Play(self, Sequence, BlendIn=0.2, PlayRate=1.5, Where='Als.Mode.View', Bl=None):", src)
        self.assertIn("NewHips = _rt.as_enum(Hips, NewHips)", src)

    def test_interpreter_and_python_agree_on_random_calls(self):
        for seed in range(1, 8):
            with self.subTest(seed=seed):
                self.assertEqual(harness.verify(self.module, 120, seed), [])


class LinkedRunTests(unittest.TestCase):
    def modules(self):
        return {n: dal.check_source(FILES[n + ".dal"], n + ".dal", files=FILES).module for n in ("Main", "Sub")}

    def test_forwarding_calls_the_main_instance(self):
        mods = self.modules()
        for backend in ("interp", "python"):
            with self.subTest(backend=backend):
                frames = [{"dt": 0.5, "calls": {"Sub": [("Play", {"Sequence": "A_Stop", "PlayRate": 2.0}), ("SetHips", {"NewHips": "Left"}), "Plain", "Own"]}},
                          {"dt": 0.25, "calls": {"Sub": ["Plain", ("Play", {"Sequence": "A_Go"})]}}]
                out = harness.run_system(mods, frames, backend)
                main, sub = out[0]["Main"], out[0]["Sub"]
                self.assertEqual((main["Clip"], main["Played"], main["H"], main["Count"]), ("A_Stop", 2.0, 2, 3))
                self.assertAlmostEqual(main["T"], 0.7)                  # Play set 0.2, Plain added the delta time of the last update (0.5)
                self.assertEqual(sub["Seen"], 1)                       # Own ran on the linked instance
                main = out[1]["Main"]
                self.assertEqual((main["Clip"], main["Played"], main["Count"], main["T"]), ("A_Go", 1.5, 5, 0.2))

    def test_without_a_main_instance_a_forward_does_nothing(self):
        mods = self.modules()
        cls = load_class(mods["Sub"])
        sub = cls(None)
        sub.SetHips("Left")
        sub.Play("A_Stop")
        sub.update(0.1)
        it = Interpreter(mods["Sub"])
        it.call_entry("Plain")
        self.assertEqual(sub.Seen, 0)

    def test_random_system_agrees(self):
        mods = self.modules()
        for seed in (1, 2, 3):
            self.assertEqual(harness.verify_system(mods, 120, seed), [])


@unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
class EntryCppTests(unittest.TestCase):
    def test_parameters_and_defaults_match_python(self):
        module = dal.check_source(MAIN, "Main.dal").module
        for seed in (1, 2, 3):
            tcc.CppCompileTests.run_case(self, module, harness.random_frames(module, 120, seed), warnings_as_errors=True)

    def test_a_linked_instance_forwards_in_cpp(self):
        mods = {n: dal.check_source(FILES[n + ".dal"], n + ".dal", files=FILES).module for n in ("Main", "Sub")}
        frames = harness.random_system_frames(mods, 80, 2)
        py = harness.run_system(mods, frames, "python")
        cpp, output = cpp_system.build_and_run_system(mods, frames)
        self.assertNotIn("warning", output.lower())
        problems = []
        for i, (a, b) in enumerate(zip(py, cpp)):
            for n in mods:
                problems += [f"frame {i} {n}.{x[1]}: {x[2]!r} != {x[3]!r}" for x in harness.compare([a[n]], [b[n]], 1e-4) if not x[1].startswith("__")]
        self.assertEqual(problems[:5], [])
        main = cpp[-1]["Main"]
        self.assertGreater(main["Count"], 0)

    def test_generated_text(self):
        files = generate_cpp(dal.check_source(MAIN, "Main.dal").module)
        h, cpp = files["Main.h"], files["Main.cpp"]
        self.assertIn("void SetHips(EHips NewHips = EHips::Forward);", h)
        self.assertIn("void Play(TObjectPtr<UAnimSequenceBase> Sequence, float BlendIn = 0.2f, float PlayRate = 1.5f, FGameplayTag Where = ModeTags::View,", h)
        self.assertIn("void UMain::Local([[maybe_unused]] const float X)\n{\n\t[[maybe_unused]] const float dt = Dal_DeltaSeconds;", cpp)
        self.assertIn("void UMain::NoArgs()", cpp)
        sub = generate_cpp(dal.check_source(SUB, "Sub.dal", files=FILES).module)
        sh, sc = sub["Sub.h"], sub["Sub.cpp"]
        self.assertIn('#include "Main.h"', sh)
        self.assertIn("TWeakObjectPtr<UMain> Parent;", sh)
        self.assertIn("UMain* GetParent() const { return Parent.Get(); }", sh)
        self.assertIn("void SetHips(EHips NewHips = EHips::Forward);", sh)
        self.assertIn("void USub::SetHips([[maybe_unused]] const EHips NewHips)\n{\n\tif (Parent.IsValid())\n\t{\n\t\tParent->SetHips(NewHips);", sc)
        self.assertIn("bUseMainInstanceMontageEvaluationData = true;", sc)
        self.assertIn("Cast<UMain>(DalMesh->GetAnimInstance())", sc)


if __name__ == "__main__":
    unittest.main()
