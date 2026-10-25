"""Root motion override (`root_motion_override`, `root_motion_clear`), the custom movement mode (`set_movement_mode(mode, sub)`, a sparse command)
and the questions a montage asset answers (`montage_length` ... `montage_last_root_transform`)."""

import unittest

import _bootstrap  # noqa: F401

import dal
from dal import harness, hostapi
from dal import runtime as rt
from dal.cppbackend import EXEC, generate_cpp
from dal.mockhost import MockHost

try:
    import test_cpp_compile as tcc
except Exception:                                   # pragma: no cover
    tcc = None


def compile_ok(src, **kw):
    return dal.check_source(src, "t.dal", **kw).module


def codes(src):
    try:
        dal.check_source(src, "t.dal")
    except dal.DalError as e:
        return [d.code for d in e.diagnostics if d.severity == "error"]
    return []


def fr(dt=0.125, **kw):
    kw["dt"] = dt
    return kw


QUERIES = ("montage_length", "montage_rate_scale", "montage_sampling_rate", "montage_blend_in_time", "montage_blend_in_option",
           "montage_root_transform", "montage_last_root_transform")

CLIMB = """
settings Moves { Climb: montage; }

logic Rm : anim post_flush;
param Cfg: Moves;
input Go: float;
state Len: float;
state Rate: float;
state Samp: float;
state Blend: float;
state Opt: BlendOption;
state Z: float;
state EndX: float;
state Found: float;
state Phase: int;

method search(target: float) -> float {
    var lo = 0.0;
    var hi = host.montage_length(Cfg.Climb);
    var result = lo;
    for i in 0..24 {
        let mid = (lo + hi) * 0.5;
        let z = host.montage_root_transform(Cfg.Climb, mid).location.z;
        result = mid;
        if (abs(z - target) < 0.01 || hi - lo < 0.001) { break; }
        if (z < target) { lo = mid; } else { hi = mid; }
    }
    return result;
}

update(dt) {
    Len = host.montage_length(Cfg.Climb);
    Rate = host.montage_rate_scale(Cfg.Climb);
    Samp = host.montage_sampling_rate(Cfg.Climb);
    Blend = host.montage_blend_in_time(Cfg.Climb);
    Opt = host.montage_blend_in_option(Cfg.Climb);
    Z = host.montage_root_transform(Cfg.Climb, Go).location.z;
    EndX = host.montage_last_root_transform(Cfg.Climb).location.x;
    Found = search(Go * 10.0);
    if (Go > 0.5) {
        host.set_movement_mode(MovementMode.Custom, 3);
        host.root_motion_override(vec3(Go, 0.0, Go * 2.0), quat(0.0, 0.0, 0.0, 1.0));
        Phase = 1;
    } else {
        host.set_movement_mode(MovementMode.Walking);
        host.set_movement_mode(MovementMode.Falling, 0);
        host.root_motion_clear();
        Phase = 0;
    }
}
"""
PARAMS = {"Cfg": {"Climb": "M"}}
ASSET = {"length": 2.0, "rate_scale": 1.5, "sampling_rate": 30.0, "blend_in_time": 0.2, "blend_in_option": 3, "root_velocity": (1.0, 2.0, 5.0)}


def climb_frames():
    frames = [fr(free={"Go": g}, montage_assets={"M": ASSET}) for g in (0.0, 0.25, 0.75, 1.5, 3.0)]
    frames.append(fr(free={"Go": 0.5}))                                               # the asset is not scripted: the neutral answers
    return frames


class TableTests(unittest.TestCase):
    def test_the_commands_and_queries(self):
        for name in ("root_motion_override", "root_motion_clear"):
            self.assertIn(name, hostapi.COMMANDS)
            self.assertIn(name, EXEC)
            self.assertTrue(callable(getattr(hostapi.HostV1, name)))
        for name in QUERIES:
            self.assertIn(name, hostapi.QUERIES)
            self.assertTrue(hostapi.QUERIES[name].thread_safe, name)                    # pure reads of an asset: an animation instance may ask
            self.assertTrue(callable(getattr(hostapi.HostV1, name)))
        self.assertEqual(hostapi.QUERIES["montage_root_transform"].params[1].name, "float")

    def test_set_movement_mode_is_sparse(self):
        cmd = hostapi.COMMANDS["set_movement_mode"]
        self.assertTrue(cmd.sparse)
        self.assertEqual((len(cmd.params), cmd.min_args, cmd.defaults), (2, 1, (0,)))

    def test_the_neutral_answers(self):
        h = hostapi.HostV1()
        self.assertEqual((h.montage_length("M"), h.montage_rate_scale("M"), h.montage_sampling_rate("M")), (0.0, 1.0, 0.0))
        self.assertEqual(h.montage_blend_in_option("M"), rt.BlendOption.Linear)
        self.assertEqual(h.montage_root_transform("M", 1.0), rt.Transform())


class DiagnosticTests(unittest.TestCase):
    def check(self, stmts, *expected):
        src = ("settings Moves { Climb: montage; Walk: sequence; }\nlogic T : anim;\nparam Cfg: Moves;\nstate Q: float;\n"
               f"update(dt) {{ {stmts} }}")
        with self.subTest(stmts=stmts):
            self.assertEqual(codes(src), list(expected), stmts)

    def test_the_commands(self):
        self.check("host.root_motion_override(vec3(1.0, 0.0, 0.0), quat(0.0, 0.0, 0.0, 1.0));")
        self.check("host.root_motion_override(vec3(1.0, 0.0, 0.0));", "DAL4041")
        self.check("host.root_motion_override(1.0, quat(0.0, 0.0, 0.0, 1.0));", "DAL4002")
        self.check("host.root_motion_override(vec3(1.0, 0.0, 0.0), rotator(0.0, 90.0, 0.0));", "DAL4002")
        self.check("host.root_motion_clear();")
        self.check("host.root_motion_clear(1.0);", "DAL4041")

    def test_the_custom_mode(self):
        self.check("host.set_movement_mode(MovementMode.Custom);")
        self.check("host.set_movement_mode(MovementMode.Custom, 2);")
        self.check("host.set_movement_mode(MovementMode.Custom, 2.5);", "DAL4002")
        self.check("host.set_movement_mode(MovementMode.Custom, 2, 3);", "DAL4041")

    def test_the_queries(self):
        self.check("Q = host.montage_length(Cfg.Climb) + host.montage_rate_scale(Cfg.Climb) + host.montage_sampling_rate(Cfg.Climb)"
                   " + host.montage_blend_in_time(Cfg.Climb);")
        self.check("let o = host.montage_blend_in_option(Cfg.Climb); if (o == BlendOption.Cubic) { Q = 1.0; }")
        self.check("Q = host.montage_root_transform(Cfg.Climb, 0.5).location.z + host.montage_last_root_transform(Cfg.Climb).location.x;")
        self.check("Q = host.montage_length(Cfg.Walk);", "DAL4002")                           # a sequence is not a montage
        self.check("Q = host.montage_length(Q);", "DAL4002")
        self.check("Q = host.montage_root_transform(Cfg.Climb).location.z;", "DAL4041")
        self.check("Q = host.montage_root_transform(Cfg.Climb, 1).location.z;")               # an int widens


class SemanticsTests(unittest.TestCase):
    def run_both(self, frames, **params):
        module = compile_ok(CLIMB)
        py = harness.run_python(module, frames, **params)
        self.assertEqual(harness.compare(py, harness.run_interpreter(module, frames, **params)), [])
        return py

    def test_a_scripted_asset_answers(self):
        out = self.run_both(climb_frames(), **PARAMS)
        f = out[3]
        self.assertEqual((f["Len"], f["Rate"], f["Samp"], f["Opt"]), (2.0, 1.5, 30.0, 3))
        self.assertAlmostEqual(f["Blend"], 0.2)
        self.assertEqual(f["Z"], 7.5)                                                     # 5 * 1.5
        self.assertEqual(f["EndX"], 2.0)                                                  # the root at the end of the montage: 1 * 2
        self.assertAlmostEqual(out[2]["Found"], 1.5, delta=0.01)                           # the bisection finds the time of z = 7.5
        self.assertEqual(out[4]["Z"], 10.0)                                               # clamped to the length (5 * 2)

    def test_an_unknown_asset_answers_neutrally(self):
        f = self.run_both(climb_frames(), **PARAMS)[5]
        self.assertEqual((f["Len"], f["Rate"], f["Samp"], f["Blend"], f["Opt"], f["Z"], f["EndX"]), (0.0, 1.0, 0.0, 0.25, 0, 0.0, 0.0))
        f = self.run_both([fr(free={"Go": 0.5})], Cfg={})[0]                                # no montage in the data at all
        self.assertEqual((f["Len"], f["Rate"], f["Samp"], f["Blend"], f["Opt"]), (0.0, 1.0, 0.0, 0.25, 0))

    def test_the_commands_reach_the_host(self):
        out = self.run_both(climb_frames(), **PARAMS)
        self.assertEqual(out[0]["__cmds__"], (("set_movement_mode", (1,)), ("set_movement_mode", (3, 0)), ("root_motion_clear", ())))
        self.assertEqual(out[2]["__cmds__"], (("set_movement_mode", (6, 3)), ("root_motion_override", ((0.75, 0.0, 1.5), (0.0, 0.0, 0.0, 1.0)))))

    def test_a_sparse_command_sends_what_was_written(self):
        out = self.run_both([fr(free={"Go": 0.0})], **PARAMS)
        self.assertEqual(len(out[0]["__cmds__"][0][1]), 1)                                 # set_movement_mode(Walking): the mode alone
        self.assertEqual(len(out[0]["__cmds__"][1][1]), 2)                                 # set_movement_mode(Falling, 0): written, so sent

    def test_random_frames_agree(self):
        module = compile_ok(CLIMB)
        for seed in (1, 2):
            self.assertEqual(harness.verify(module, 80, seed, **PARAMS), [])

    def test_the_mock_host_answers_from_its_script(self):
        host = MockHost()
        host.montage_assets["M"] = {"length": 4.0, "root_velocity": (3.0, 0.0, 0.0)}
        self.assertEqual(host.montage_root_transform("M", 1.0).location, (3.0, 0.0, 0.0))
        self.assertEqual(host.montage_root_transform("M", 9.0).location, (12.0, 0.0, 0.0))
        self.assertEqual(host.montage_root_transform("M", -1.0).location, (0.0, 0.0, 0.0))
        self.assertEqual(host.montage_last_root_transform("M").location, (12.0, 0.0, 0.0))
        self.assertEqual(host.montage_root_transform(None, 1.0), rt.Transform())


class CppStructureTests(unittest.TestCase):
    def setUp(self):
        self.files = generate_cpp(compile_ok(CLIMB))
        self.cpp, self.h = self.files["Rm.cpp"], self.files["Rm.h"]

    def test_the_commands(self):
        cpp = self.cpp
        self.assertIn("DalHost::Cmd::RootMotionOverride(FVector(FVector3f(Go, 0.0f, Go * 2.0f)), FQuat(FQuat4f(0.0f, 0.0f, 0.0f, 1.0f)))", cpp)
        self.assertIn("DalHost::Cmd::RootMotionClear()", cpp)
        self.assertIn("DalAlsMovement->SetRootMotionOverride(DalCommand.VD, DalCommand.QD);", cpp)
        self.assertIn("DalAlsMovement->ClearRootMotionOverride();", cpp)
        self.assertIn("// Module dependencies: Core, CoreUObject, Engine, DalRuntime", self.h)

    def test_the_sub_mode_is_sent_when_the_program_writes_it(self):
        cpp = self.cpp
        self.assertIn("DalHost::Cmd::SetMovementMode(static_cast<int32>(MOVE_Custom), 3)", cpp)
        self.assertIn("DalMovement->SetMovementMode(static_cast<EMovementMode>(DalCommand.Mode), static_cast<uint8>(DalCommand.Count));", cpp)

    def test_a_program_that_does_not_is_unchanged(self):
        cpp = generate_cpp(compile_ok("logic T : actor;\nupdate(dt) { host.set_movement_mode(MovementMode.Walking); }"))["T.cpp"]
        self.assertIn("DalMovement->SetMovementMode(static_cast<EMovementMode>(DalCommand.Mode));", cpp)
        self.assertIn("DalHost::Cmd::SetMovementMode(static_cast<int32>(MOVE_Walking))", cpp)
        self.assertNotIn("DalCommand.Count", cpp)

    def test_the_queries(self):
        cpp = self.cpp
        self.assertIn("DalMontage->GetPlayLength()", cpp)
        self.assertIn("DalMontage->RateScale", cpp)
        self.assertIn("DalMontage->GetSamplingFrameRate().AsDecimal()", cpp)
        self.assertIn("DalMontage->BlendIn.GetBlendTime()", cpp)
        self.assertIn("DalMontage->BlendIn.GetBlendOption()", cpp)
        self.assertIn("DalMontage->SlotAnimTracks[0].AnimTrack", cpp)
        self.assertIn("ExtractRootTrackTransform(DalContext, nullptr)", cpp)
        self.assertIn('#include "Animation/AnimSequence.h"', cpp)

    def test_a_program_without_them_has_no_trace_of_them(self):
        files = generate_cpp(compile_ok("logic T : anim;\nstate S: float;\nupdate(dt) { S += dt; }"))
        for word in ("DalMontageRootTransform", "DalMontageLength", "AnimSequence.h", "RootMotionOverride"):
            self.assertNotIn(word, files["T.cpp"] + files["T.h"])


@unittest.skipIf(tcc is None or tcc.COMPILER is None, "no C++ compiler found")
class CppCompileTests(unittest.TestCase):
    def run_case(self, module, frames, **kw):
        return tcc.CppCompileTests("test_hero_gait_matches_python").run_case(module, frames, **kw)

    def test_the_answers_and_the_commands_match_python(self):
        module = compile_ok(CLIMB)
        effects = self.run_case(module, climb_frames(), params=PARAMS, warnings_as_errors=True)
        self.assertGreaterEqual(effects, 12)

    def test_random_frames(self):
        module = compile_ok(CLIMB)
        for seed in (1, 2):
            self.run_case(module, harness.random_frames(module, 60, seed), params=PARAMS, warnings_as_errors=True)


if __name__ == "__main__":
    unittest.main()
