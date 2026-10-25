"""The C++ options that make settings read what an asset made with the original holds: settings types held as Instanced UObjects
(`object_settings`), structs that are the engine's own types (`native_structs`) and the defaults the constructors of the original set
(`channel_defaults`)."""

import unittest

import _bootstrap  # noqa: F401

import dal
import test_cpp_compile as tcc
from dal import harness
from dal.cli import main as cli_main
from dal.cppbackend import CppOptions, generate_cpp

PROGRAM = """
struct Span { Min: float; Max: float; }
settings Turn {
    Sequence: sequence;
    Rate: float = 1.2;
    Scale: bool = true;
    Angle: float = 0.0;
}
settings Rules {
    Left: Turn;
    Right: Turn;
    Reach: Span = Span(Min: 1.0, Max: 2.0);
    Gain: float = 2.0;
}
logic Pick;
param Cfg: Rules;
input Mirror: bool;
state Rate: float;
state Scale: bool;
state Angle: float;
state Reach: float;

fn rate_of(t: Turn) -> float {
    return t.Rate;
}

update(dt) {
    Rate = rate_of(Mirror ? Cfg.Left : Cfg.Right) * Cfg.Gain;
    Scale = Cfg.Left.Scale;
    Angle = Cfg.Left.Angle + Cfg.Right.Angle;
    Reach = Cfg.Reach.Max - Cfg.Reach.Min;
}
"""

DATA = {"Left": {"Rate": 3.0, "Scale": False, "Angle": 15.0}, "Right": {"Rate": 0.5, "Angle": -4.0}, "Reach": {"Min": 1.0, "Max": 4.5}, "Gain": 1.5}

OPTIONS = CppOptions(object_settings=("Turn",), native_structs=("Span=FFloatInterval=Math/Interval.h",))


def module():
    return dal.check_source(PROGRAM, "Pick.dal").module


class ObjectSettingsTests(unittest.TestCase):
    def test_the_settings_type_is_an_object_the_holders_point_to(self):
        h = generate_cpp(module(), OPTIONS)["Pick.h"]
        self.assertIn("UCLASS(BlueprintType, EditInlineNew)\nclass UTurn : public UObject\n{", h)
        self.assertIn('UPROPERTY(EditAnywhere, BlueprintReadOnly, Instanced, Category = "DAL")\n\tTObjectPtr<UTurn> Left;', h)
        self.assertIn("TObjectPtr<UTurn> Right;", h)
        self.assertNotIn("FTurn ", h)                                         # the value struct has another name: U<Name> and F<Name> would share an engine name
        self.assertIn("struct FTurnValue\n{", h)
        self.assertIn("FTurnValue ToValue() const", h)
        self.assertIn("inline FTurnValue DalValueOf(const UTurn* Object)", h)
        self.assertIn("Object != nullptr ? Object->ToValue() : FTurnValue()", h)

    def test_the_logic_reads_the_values_of_the_object(self):
        cpp = generate_cpp(module(), OPTIONS)["Pick.cpp"]
        self.assertIn("DalValueOf(DalSettings_Cfg().Left)", cpp)
        self.assertIn("DalValueOf(DalSettings_Cfg().Right)", cpp)
        self.assertIn("FTurnValue", cpp)

    def test_off_by_default(self):
        files = generate_cpp(module())
        self.assertIn("struct FTurn\n{", files["Pick.h"])
        self.assertIn("\tFTurn Left = FTurn();", files["Pick.h"])
        self.assertNotIn("UTurn", files["Pick.h"])
        self.assertIn("struct FSpan\n{", files["Pick.h"])

    def test_a_native_struct_is_not_declared(self):
        h = generate_cpp(module(), OPTIONS)["Pick.h"]
        self.assertNotIn("struct FSpan", h)
        self.assertIn("#include \"Math/Interval.h\"", h)
        self.assertIn("\tFFloatInterval Reach;", h)
        self.assertIn("Dal_S.Min = 1.0f; Dal_S.Max = 2.0f;", h)

    def test_the_types_are_restored_after_the_generation(self):
        m = module()
        generate_cpp(m, OPTIONS)
        self.assertEqual(sorted(st.cpp_type for st in m.structs), ["FSpan", "FTurn", "URules"])
        self.assertIn("struct FTurn\n{", generate_cpp(m)["Pick.h"])

    def test_unknown_names_are_ignored_and_a_wrong_kind_is_an_error(self):
        generate_cpp(module(), CppOptions(object_settings=("Nope",), native_structs=("Nope=FNope",)))
        with self.assertRaises(ValueError):
            generate_cpp(module(), CppOptions(object_settings=("Span",)))
        with self.assertRaises(ValueError):
            generate_cpp(module(), CppOptions(object_settings=("Rules",)))              # held by nobody: a data asset
        with self.assertRaises(ValueError):
            generate_cpp(module(), CppOptions(native_structs=("Span",)))

    def test_matches_python(self):
        m = module()
        for seed in (1, 2):
            tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 40, seed), options=OPTIONS, warnings_as_errors=True, params={"Cfg": DATA})

    def test_the_command_line_options(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "Pick.dal")
            with open(src, "w", encoding="utf-8") as f:
                f.write(PROGRAM)
            out = os.path.join(d, "out")
            self.assertEqual(cli_main(["compile", src, "--cpp", out, "--object-settings", "Turn", "--native-struct", "Span=FFloatInterval=Math/Interval.h"]), 0)
            with open(os.path.join(out, "Pick.h"), encoding="utf-8") as f:
                h = f.read()
            self.assertIn("class UTurn : public UObject", h)
            self.assertNotIn("struct FSpan", h)


CHANNELS = """
settings Trace {
    Ch: Channel = Channel.Visibility;
    Hits: channel_set;
    Also: channel_set;
    Reach: float = 50.0;
}
logic Probe;
param Cfg: Trace;
input From: vec3;
input To: vec3;
state Blocked: bool;
update(dt) {
    Blocked = host.sweep_capsule(From, To, 30.0, 90.0, Cfg.Ch, Cfg.Hits).blocking;
}
"""


class ChannelDefaultTests(unittest.TestCase):
    OPTIONS = CppOptions(channel_defaults=("Trace.Hits=WorldStatic,Destructible",))

    def test_the_constructor_sets_the_default(self):
        h = generate_cpp(dal.check_source(CHANNELS, "Probe.dal").module, self.OPTIONS)["Probe.h"]
        self.assertIn("Hits = { ECC_WorldStatic, ECC_Destructible };", h)
        self.assertNotIn("Also = {", h)                                       # only the member that was named
        plain = generate_cpp(dal.check_source(CHANNELS, "Probe.dal").module)["Probe.h"]
        self.assertNotIn("Hits = {", plain)

    def test_wrong_specs_are_errors_and_other_files_are_ignored(self):
        m = dal.check_source(CHANNELS, "Probe.dal").module
        generate_cpp(m, CppOptions(channel_defaults=("Elsewhere.Hits=WorldStatic",)))
        for spec in ("Trace.Reach=WorldStatic", "Trace.Nope=WorldStatic", "Trace.Hits=Nowhere", "Trace.Hits", "Trace=WorldStatic"):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                generate_cpp(m, CppOptions(channel_defaults=(spec,)))

    def test_compiles_and_the_default_reaches_the_query(self):
        m = dal.check_source(CHANNELS, "Probe.dal").module
        tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 20, 3), options=self.OPTIONS, warnings_as_errors=True)

    def test_the_command_line_option(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "Probe.dal")
            with open(src, "w", encoding="utf-8") as f:
                f.write(CHANNELS)
            out = os.path.join(d, "out")
            self.assertEqual(cli_main(["compile", src, "--cpp", out, "--channel-default", "Trace.Hits=Pawn,Camera"]), 0)
            with open(os.path.join(out, "Probe.h"), encoding="utf-8") as f:
                self.assertIn("Hits = { ECC_Pawn, ECC_Camera };", f.read())


if __name__ == "__main__":
    unittest.main()
