"""The ragdoll reads of the host: ``ragdoll_pelvis_location``, ``ragdoll_velocity``, ``ragdoll_facing_up`` (what ALS reads off the pelvis body
of the ragdoll in RefreshRagdolling and StopRagdollingImplementation).  Checker, the two Python implementations, the mock host, C++
(the generated code asks the ``UDalRagdollService`` of the actor) and the docs tables."""

import re
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

import dal
import test_cpp_compile as tcc
from dal import harness
from dal import runtime as rt
from dal.cppbackend import generate_cpp
from dal.hostapi import HOST_ITEMS, HostV0, HostV1, protocol_markdown
from dal.interp import Interpreter
from dal.mockhost import MockHost
from dal.pybackend import generate_python, load_class

SRC = """
logic Rg : actor;
input Pelvis: vec3 <- host.ragdoll_pelvis_location;
input Speed: vec3 <- host.ragdoll_velocity;
input Up: bool <- host.ragdoll_facing_up;
state Where: vec3;
state Fast: float;
state Face: bool;
update(dt) {
    Where = Pelvis + vec3(0.0, 0.0, 1.0);
    Fast = length(Speed);
    Face = Up && Fast > 100.0;
}
"""

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "Unreal" / "DalRuntime" / "Source" / "DalRuntime"
NAMES = ("ragdoll_pelvis_location", "ragdoll_velocity", "ragdoll_facing_up")


def codes(src):
    try:
        dal.check_source(src, "t.dal")
    except dal.DalError as e:
        return [d.code for d in e.diagnostics if d.severity == "error"]
    return []


def binding(item_text, ty):
    return "logic T;\ninput X: " + ty + " <- host." + item_text + ";\nupdate(dt) { }"


class RagdollReadCheckTests(unittest.TestCase):
    def test_binding_rules(self):
        self.assertEqual(codes(SRC), [])
        self.assertEqual(codes(binding("ragdoll_pelvis_location", "vec3")), [])
        self.assertEqual(codes(binding("ragdoll_velocity", "vec3")), [])
        self.assertEqual(codes(binding("ragdoll_facing_up", "bool")), [])
        self.assertEqual(codes(binding("ragdoll_velocity", "float")), ["DAL4020"])
        self.assertEqual(codes(binding("ragdoll_pelvis_location", "vec2")), ["DAL4020"])
        self.assertEqual(codes(binding("ragdoll_facing_up", "vec3")), ["DAL4020"])
        self.assertEqual(codes(binding('ragdoll_velocity("pelvis")', "vec3")), ["DAL3021"])           # reads of the service take no argument
        self.assertEqual(codes(binding("ragdoll_velocit", "vec3")), ["DAL3020"])

    def test_the_table_lists_them_with_their_meaning(self):
        for name in NAMES:
            self.assertIn(name, HOST_ITEMS)
            self.assertIn("UDalRagdollService", HOST_ITEMS[name].ue)
        text = protocol_markdown()
        for name in NAMES:
            self.assertIn("`%s`" % name, text)

    def test_neutral_defaults(self):
        for host in (HostV0(), HostV1(), MockHost()):
            self.assertEqual((host.ragdoll_pelvis_location, host.ragdoll_velocity, host.ragdoll_facing_up), (rt.VEC3_ZERO, rt.VEC3_ZERO, False))


class RagdollReadValueTests(unittest.TestCase):
    def test_values_reach_the_logic_in_both_implementations(self):
        m = dal.check_source(SRC, "t.dal").module
        self.assertIn("h.ragdoll_pelvis_location", generate_python(m))
        for make in (lambda h: load_class(m)(h), lambda h: Interpreter(m, h)):
            h = MockHost()
            obj = make(h)
            obj.update(0.1)
            self.assertEqual((obj.snapshot()["Where"], obj.snapshot()["Fast"], obj.snapshot()["Face"]), ((0.0, 0.0, 1.0), 0.0, False))      # no ragdoll: zero
            h.ragdoll_pelvis_location = (10.0, 20.0, 30.0)
            h.ragdoll_velocity = (300.0, 400.0, 0.0)
            h.ragdoll_facing_up = True
            obj.update(0.1)
            snap = obj.snapshot()
            self.assertEqual((snap["Pelvis"], snap["Speed"], snap["Up"]), ((10.0, 20.0, 30.0), (300.0, 400.0, 0.0), True))
            self.assertEqual((snap["Where"], snap["Fast"], snap["Face"]), ((10.0, 20.0, 31.0), 500.0, True))

    def test_a_program_that_does_not_read_them_asks_for_nothing(self):
        m = dal.check_source("logic T : actor;\ninput V: vec3 <- host.velocity;\nupdate(dt) { }\n", "t.dal").module
        files = generate_cpp(m)
        self.assertNotIn("Ragdoll", files["T.cpp"])

    def test_random_frames_agree(self):
        m = dal.check_source(SRC, "t.dal").module
        for seed in (1, 2, 3):
            self.assertEqual(harness.verify(m, 60, seed), [])


@unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
class RagdollReadCppTests(unittest.TestCase):
    def test_generated_text(self):
        files = generate_cpp(dal.check_source(SRC, "t.dal").module)
        cpp = files["Rg.cpp"]
        self.assertIn("const UDalRagdollService* DalRagdoll = Pawn != nullptr ? Pawn->FindComponentByClass<UDalRagdollService>() : nullptr;", cpp)
        self.assertIn("Pelvis = DalRagdoll != nullptr ? FVector3f(DalRagdoll->GetPelvisLocation()) : FVector3f(0.0f, 0.0f, 0.0f);", cpp)
        self.assertIn("Speed = DalRagdoll != nullptr ? FVector3f(DalRagdoll->GetPelvisVelocity()) : FVector3f(0.0f, 0.0f, 0.0f);", cpp)
        self.assertIn("Up = DalRagdoll != nullptr ? DalRagdoll->IsFacingUp() : false;", cpp)
        self.assertIn('#include "DalRagdollService.h"', cpp + files["Rg.h"])

    def test_the_module_needs_dalruntime(self):
        from dal.cppbackend import CppGen, CppOptions
        self.assertIn("DalRuntime", CppGen(dal.check_source(SRC, "t.dal").module, CppOptions()).modules())

    def test_actor_and_anim_logic_match_python(self):
        for src in (SRC, SRC.replace("logic Rg : actor;", "logic Rg;")):
            m = dal.check_source(src, "t.dal").module
            for seed in (1, 2):
                tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 40, seed), warnings_as_errors=True, default=3e-3)


class RuntimePluginTests(unittest.TestCase):
    def test_the_ragdoll_service_of_the_plugin_has_the_getters_the_generated_code_calls(self):
        header = (RUNTIME / "Public" / "DalRagdollService.h").read_text(encoding="utf-8")
        source = (RUNTIME / "Private" / "DalRagdollService.cpp").read_text(encoding="utf-8")
        for fn in ("GetPelvisLocation", "GetPelvisVelocity", "IsFacingUp"):
            self.assertTrue(re.search(r"\b%s\(\) const;" % fn, header), fn)
            self.assertIn("UDalRagdollService::%s() const" % fn, source)
        self.assertIn("FMath::UnwindDegrees(PelvisRotation.Roll) <= 0.0f", source)           # the test ALS makes


if __name__ == "__main__":
    unittest.main()
