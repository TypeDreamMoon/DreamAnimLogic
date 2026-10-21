"""What the ALS port needed beyond the first language: the mesh scale and the time dilation reads, settings values held in state and in
structs, a member of a peer's settings data asset read from C++, and long C++ test runs (the driver's stack)."""

import unittest

import _bootstrap  # noqa: F401

import cpp_system
import dal
from dal import harness, hostapi
from dal.cppbackend import generate_cpp
from dal.hostapi import HOST_ITEMS, HostV0, HostV1

try:
    import test_cpp_compile as tcc
except Exception:                                   # pragma: no cover
    tcc = None


def compile_ok(src, **kw):
    return dal.check_source(src, "t.dal", **kw).module


def codes(src, **kw):
    try:
        dal.check_source(src, "t.dal", **kw)
    except dal.DalError as e:
        return [d.code for d in e.diagnostics if d.severity == "error"]
    return []


def fr(dt=0.125, **kw):
    kw["dt"] = dt
    return kw


# ---- the reads -------------------------------------------------------------------------------------------------------------------------

READS = """
logic Rd : actor;
input TD: float <- host.time_dilation;
input AD: float <- host.actor_time_dilation;
input RD: float <- host.real_delta_time;
input MS: float <- host.mesh_scale;
input AS: float <- host.actor_scale;
state Eff: float;
state Real: float;
state Meshes: float;
update(dt) {
    Eff = dt / max(TD * AD, 0.0001);
    Real = RD;
    Meshes = MS * 10.0 + AS;
}
"""
READS_ANIM = READS.replace("logic Rd : actor;", "logic Rd;")
NAMES = ("mesh_scale", "time_dilation", "actor_time_dilation", "real_delta_time")


class ReadTests(unittest.TestCase):
    def test_the_items_are_in_the_table_and_on_the_base_class(self):
        for name in NAMES:
            with self.subTest(name=name):
                self.assertIn(name, HOST_ITEMS)
                self.assertTrue(HOST_ITEMS[name].ue)
                self.assertTrue(hasattr(HostV0, name) and hasattr(HostV1, name))
        h = HostV1()
        self.assertEqual((h.mesh_scale, h.time_dilation, h.actor_time_dilation, h.real_delta_time), (1.0, 1.0, 1.0, 0.0))

    def test_both_kinds_of_logic_can_read_them(self):
        self.assertEqual(codes(READS), [])
        self.assertEqual(codes(READS_ANIM), [])

    def test_the_values_reach_the_logic(self):
        for src in (READS, READS_ANIM):
            module = compile_ok(src)
            frames = [fr(0.25, time_dilation=0.5, actor_time_dilation=1.0, real_delta_time=0.125, mesh_scale=2.0, actor_scale=1.0),
                      fr(0.25, time_dilation=0.5, actor_time_dilation=0.5, real_delta_time=0.0625, mesh_scale=0.5, actor_scale=2.0)]
            py = harness.run_python(module, frames)
            self.assertEqual(harness.compare(py, harness.run_interpreter(module, frames)), [])
            self.assertEqual([f["Eff"] for f in py], [0.5, 1.0])               # the delta time without the dilation
            self.assertEqual([f["Real"] for f in py], [0.125, 0.0625])
            self.assertEqual([f["Meshes"] for f in py], [21.0, 7.0])          # the mesh's scale, not the capsule's

    def test_the_interpreter_and_python_agree_on_random_frames(self):
        for src in (READS, READS_ANIM):
            self.assertEqual(harness.verify(compile_ok(src), 100, 3), [])

    def test_the_generated_reads(self):
        files = generate_cpp(compile_ok(READS))
        cpp, h = files["Rd.cpp"], files["Rd.h"]
        self.assertIn("World->GetWorldSettings()->GetEffectiveTimeDilation()", cpp)
        self.assertIn("Pawn->CustomTimeDilation", cpp)
        self.assertIn("World->DeltaRealTimeSeconds", cpp)
        self.assertIn("DalMesh->GetComponentScale().Z", cpp)
        self.assertIn('#include "GameFramework/WorldSettings.h"', cpp + h)
        self.assertIn('#include "Components/SkeletalMeshComponent.h"', cpp + h)
        anim = generate_cpp(compile_ok(READS_ANIM))
        self.assertIn("GetSkelMeshComponent()", anim["Rd.cpp"])

    def test_a_program_without_them_has_no_trace_of_them(self):
        files = generate_cpp(compile_ok("logic Q : actor;\ninput T: float <- host.time;\nstate S: float;\nupdate(dt) { S = T; }"))
        text = files["Q.cpp"] + files["Q.h"]
        for word in ("WorldSettings", "CustomTimeDilation", "DeltaRealTimeSeconds", "SkeletalMeshComponent"):
            self.assertNotIn(word, text)


@unittest.skipIf(tcc is None or tcc.COMPILER is None, "no C++ compiler found")
class ReadCompileTests(unittest.TestCase):
    def test_the_reads_match_python(self):
        runner = tcc.CppCompileTests("test_hero_gait_matches_python")
        for src in (READS, READS_ANIM):
            module = compile_ok(src)
            for seed in (1, 2):
                runner.run_case(module, harness.random_frames(module, 60, seed), warnings_as_errors=True)


# ---- settings values in state ---------------------------------------------------------------------------------------------------------

SETTINGS = """
settings Turn {
    Sequence: sequence;
    Rate: float = 1.0;
    Curve: curve;
    Names: array<float>;
    derived Double: float = Rate * 2.0;
}
settings Als {
    Standing: Turn;
    Crouching: Turn;
    Speed: float = 5.0;
}
struct Queue { Set: Turn; Count: int; }
"""

HELD = SETTINGS + """
logic Ss : anim post_flush;
param Cfg: Als;
input Crouch: bool;
state Queued: Turn;
state Q: Queue;
state Seq: sequence = none;
state Sc: float;
state Dbl: float;
update(dt) {
    if (Crouch) {
        Queued = Cfg.Crouching;
        Q.Set = Cfg.Crouching;
    } else {
        Queued = Cfg.Standing;
        Q.Set = Cfg.Standing;
    }
    Q.Count += 1;
    Seq = Queued.Sequence;
    Sc = Queued.Rate + Q.Set.Rate;
    Dbl = Queued.Double;
}
"""
DATA = {"Standing": {"Sequence": "SeqS", "Rate": 1.5, "Names": [1.0, 2.0]}, "Crouching": {"Sequence": "SeqC", "Rate": 0.5}, "Speed": 7.0}


def held_with(stmts, extra=""):
    return SETTINGS + f"logic Ss : anim;\nparam Cfg: Als;\n{extra}\nupdate(dt) {{ {stmts} }}\n"


class SettingsStateDiagnosticTests(unittest.TestCase):
    def test_a_state_holds_the_settings_nested_in_a_data_asset(self):
        self.assertEqual(codes(HELD), [])
        self.assertEqual(codes(held_with("", "state Queued: Turn;")), [])
        self.assertEqual(codes(held_with("", "struct W { T: Turn; }\nstate Q: W;")), [])

    def test_the_data_asset_itself_stays_with_the_param(self):
        self.assertEqual(codes(held_with("", "state Root: Als;")), ["DAL4058"])
        self.assertEqual(codes(SETTINGS + "struct Bad { A: Als; }\nlogic T;\nupdate(dt) { }"), ["DAL4050"])

    def test_inputs_params_constants_and_entry_parameters_still_refuse_settings(self):
        for extra in ("input I: Turn;", "entry E(dt, T: Turn) { }", "const C: Turn = none;"):
            with self.subTest(extra=extra):
                self.assertTrue(codes(held_with("", extra)), extra)

    def test_the_copy_is_read_only(self):
        self.assertEqual(codes(held_with("Queued.Rate = 2.0;", "state Queued: Turn;")), ["DAL5004"])
        self.assertEqual(codes(held_with("Wv.T.Rate = 2.0;", "struct W { T: Turn; }\nstate Wv: W;")), ["DAL5004"])
        self.assertEqual(codes(held_with("Queued = Cfg.Standing; Queued = Cfg.Crouching;", "state Queued: Turn;")), [])         # the whole value can be replaced
        self.assertEqual(codes(held_with("Queued = Cfg.Speed;", "state Queued: Turn;")), ["DAL4002"])                   # of the same type


class SettingsStateSemanticsTests(unittest.TestCase):
    def test_the_state_follows_the_param(self):
        module = compile_ok(HELD)
        frames = [fr(free={"Crouch": c}) for c in (False, True, True, False)]
        py = harness.run_python(module, frames, Cfg=DATA)
        self.assertEqual(harness.compare(py, harness.run_interpreter(module, frames, Cfg=DATA)), [])
        self.assertEqual([f["Queued.Sequence"] for f in py], ["SeqS", "SeqC", "SeqC", "SeqS"])
        self.assertEqual([f["Seq"] for f in py], ["SeqS", "SeqC", "SeqC", "SeqS"])
        self.assertEqual([f["Sc"] for f in py], [3.0, 1.0, 1.0, 3.0])
        self.assertEqual([f["Dbl"] for f in py], [3.0, 1.0, 1.0, 3.0])                                   # the derived member is part of the value
        self.assertEqual([f["Q.Count"] for f in py], [1, 2, 3, 4])

    def test_tables_lists_and_curves_are_not_in_the_flat_snapshot(self):
        module = compile_ok(HELD)
        snap = harness.run_python(module, [fr(free={"Crouch": False})], Cfg=DATA)[0]
        self.assertNotIn("Queued.Names", snap)
        self.assertNotIn("Queued.Curve", snap)
        self.assertIn("Queued.Rate", snap)

    def test_the_default_is_the_declared_one(self):
        module = compile_ok(SETTINGS + "logic T : anim;\nparam Cfg: Als;\nstate Q: Turn;\nstate R: float;\nupdate(dt) { R = Q.Rate + Q.Double; }")
        out = harness.run_python(module, [fr()], Cfg=DATA)
        self.assertEqual(out[0]["R"], 3.0)

    def test_random_frames_agree(self):
        self.assertEqual(harness.verify(compile_ok(HELD), 80, 2, Cfg=DATA), [])


class SettingsStateCppTests(unittest.TestCase):
    def test_the_generated_state_is_the_value_struct(self):
        files = generate_cpp(compile_ok(HELD))
        self.assertIn("FTurn Queued = FTurn();", files["Ss.h"])
        self.assertIn("FQueue Q = FQueue();", files["Ss.h"])
        self.assertIn("Queued = DalSettings_Cfg().Crouching;", files["Ss.cpp"])
        self.assertIn("Seq = Queued.Sequence;", files["Ss.cpp"])

    @unittest.skipIf(tcc is None or tcc.COMPILER is None, "no C++ compiler found")
    def test_the_state_matches_python(self):
        module = compile_ok(HELD)
        runner = tcc.CppCompileTests("test_hero_gait_matches_python")
        frames = [fr(free={"Crouch": c}) for c in (False, True, True, False)]
        runner.run_case(module, frames, params={"Cfg": DATA}, warnings_as_errors=True)
        runner.run_case(module, harness.random_frames(module, 40, 5), params={"Cfg": DATA}, warnings_as_errors=True)


# ---- a member of a peer's settings -----------------------------------------------------------------------------------------------------

PEER_FILES = {
    "AlsChar.dal": SETTINGS + """
logic AlsChar : actor in after_movement;
param Cfg: Als;
input V: vec3 <- host.velocity;
state Moving: float;
update(dt) { Moving = length(V); }
""",
    "AlsAnim.dal": """
peer Char: AlsChar;
logic AlsAnim;
input Speed: float <- Char.Cfg.Speed;
input Rate: float <- Char.Cfg.Standing.Rate;
input Dbl: float <- Char.Cfg.Crouching.Double;
input Moving: float <- Char.Moving;
state Sum: float;
update(dt) { Sum = Speed + Rate * 10.0 + Dbl * 100.0 + Moving; }
""",
}


def peer_system():
    return {n[:-4]: dal.check_source(PEER_FILES[n], n, files=PEER_FILES).module for n in ("AlsChar.dal", "AlsAnim.dal")}


class PeerSettingsTests(unittest.TestCase):
    PARAMS = {"AlsChar": {"Cfg": DATA}}

    def test_the_path_is_checked_like_any_peer_path(self):
        peer_system()
        bad = dict(PEER_FILES)
        bad["AlsAnim.dal"] = PEER_FILES["AlsAnim.dal"].replace("Char.Cfg.Speed", "Char.Cfg.Nope")
        self.assertEqual(codes(bad["AlsAnim.dal"], files=bad), ["DAL4030"])

    def test_python_reads_the_value(self):
        mods = peer_system()
        frames = [{"dt": 0.1, "velocity": (3.0, 4.0, 0.0)}]
        out = harness.run_system(mods, frames, "python", params=self.PARAMS)
        self.assertEqual(out[0]["AlsAnim"]["Sum"], 7.0 + 15.0 + 100.0 + 5.0)                # 100 * Double of the crouching settings (Rate 0.5 -> 1.0)
        self.assertEqual(harness.compare([out[0]["AlsAnim"]], [harness.run_system(mods, frames, "interp", params=self.PARAMS)[0]["AlsAnim"]]), [])

    def test_the_generated_read_goes_through_the_accessor(self):
        cpp = generate_cpp(peer_system()["AlsAnim"])["AlsAnim.cpp"]
        self.assertIn("Char != nullptr ? Char->DalSettings_Cfg().Speed", cpp)
        self.assertIn("Char->DalSettings_Cfg().Standing.Rate", cpp)
        self.assertIn("Char->DalSettings_Cfg().Crouching.Double", cpp)
        self.assertIn("Char->Moving", cpp)                                               # a state is a plain member

    @unittest.skipIf(tcc is None or tcc.COMPILER is None, "no C++ compiler found")
    def test_the_system_matches_python(self):
        from test_cpp_system import compare_system

        mods = peer_system()
        frames = tcc.round_frames(harness.random_system_frames(mods, 40, 3))
        cpp, output = cpp_system.build_and_run_system(mods, frames, params=self.PARAMS)
        if tcc.COMPILER[0] == "msvc":
            self.assertNotIn("warning", output.lower(), output)
        compare_system(self, mods, harness.run_system(mods, frames, "python", params=self.PARAMS), cpp)


# ---- long runs --------------------------------------------------------------------------------------------------------------------------

@unittest.skipIf(tcc is None or tcc.COMPILER is None, "no C++ compiler found")
class LongRunTests(unittest.TestCase):
    def test_the_driver_runs_hundreds_of_frames(self):
        module = compile_ok("logic L : actor;\ninput V: vec3 <- host.velocity;\nstate S: float;\nstate N: int;\nupdate(dt) { S += length(V) * dt; N += 1; host.set_gravity_scale(S); }")
        frames = harness.random_frames(module, 400, 9)
        count = tcc.CppCompileTests("test_hero_gait_matches_python").run_case(module, frames, warnings_as_errors=True)
        self.assertGreaterEqual(count, 400)

    def test_the_system_driver_runs_hundreds_of_frames(self):
        from test_cpp_system import compare_system

        mods = peer_system()
        frames = tcc.round_frames(harness.random_system_frames(mods, 300, 4))
        cpp, _ = cpp_system.build_and_run_system(mods, frames, params=PeerSettingsTests.PARAMS)
        compare_system(self, mods, harness.run_system(mods, frames, "python", params=PeerSettingsTests.PARAMS), cpp)


if __name__ == "__main__":
    unittest.main()
