"""Scene queries with channels, the extended hit, primitives and the thread-safe query set (G25, G20)."""

import unittest

import _bootstrap  # noqa: F401

import dal
import test_cpp_compile as tcc
from dal import harness
from dal import runtime as rt
from dal.cppbackend import generate_cpp
from dal.hostapi import QUERIES, HostV1
from dal.mockhost import MockHost
from dal.pybackend import generate_python, load_class

SETTINGS = "settings Cfg { Ch: Channel = Channel.Visibility; Resp: channel_set; }\n"

SWEEP = """
logic {name}{kind};
param C: Cfg;
input From: vec3;
input To: vec3;
state Blocked: bool;
state Time: float;
state Dist: float;
state Point: vec3;
state Normal: vec3;
state Loc: vec3;
state Pen: bool;
state Prim: primitive = none;
state Face: vec3;
state Walk: bool;
state Over: bool;
state Spd: float;
state Step: bool;
state Valid: bool;
state Tf: transform;
state Line: float;
state Sph: float;
update(dt) {{
    let h = host.sweep_capsule(From, To, 30.0, 90.0, C.Ch, C.Resp);
    Blocked = h.blocking;
    Time = h.time;
    Dist = h.distance;
    Point = h.point;
    Normal = h.normal;
    Loc = h.location;
    Pen = h.start_penetrating;
    Prim = h.primitive;
    Face = h.face_normal;
    Walk = host.is_walkable(h);
    Over = host.overlap_capsule(From, 30.0, 90.0, Channel.Pawn, C.Resp);
    Spd = host.primitive_speed(Prim);
    Step = host.primitive_can_step_up(Prim);
    Valid = host.primitive_valid(Prim);
    Tf = host.primitive_transform(Prim);
    Line = host.line_trace(From, To, Channel.Camera).distance;
    Sph = host.sweep_sphere(From, To, 10.0, Channel.Visibility, C.Resp).time;
}}
"""
ACTOR = SETTINGS + SWEEP.format(name="Qa", kind=" : actor")
ANIM = SETTINGS + SWEEP.format(name="Qa", kind="")


def codes(src):
    try:
        dal.check_source(src, "t.dal")
    except dal.DalError as e:
        return [d.code for d in e.diagnostics if d.severity == "error"]
    return []


def compile_ok(src):
    return dal.check_source(src, "t.dal").module


def frame(**free):
    return {"dt": 0.1, "free": free}


def scene(ground=0.0, spheres=(), primitives=None, **free):
    f = frame(**free)
    f["world"] = {"ground_z": ground, "spheres": [tuple(s) for s in spheres], "primitives": primitives or {}}
    return f


def run(src, frames, **params):
    m = compile_ok(src)
    a, b = harness.run_interpreter(m, frames, **params), harness.run_python(m, frames, **params)
    assert harness.compare(a, b) == [], harness.compare(a, b)[:3]
    return a


def near(test, a, b, places=5):
    for x, y in zip(a, b):
        test.assertAlmostEqual(x, y, places=places)


class QueryCheckTests(unittest.TestCase):
    def test_signatures(self):
        for name, n in (("sweep_capsule", 6), ("sweep_sphere", 5), ("line_trace", 3), ("overlap_capsule", 5), ("is_walkable", 1),
                        ("primitive_valid", 1), ("primitive_speed", 1), ("primitive_can_step_up", 1), ("primitive_transform", 1)):
            self.assertEqual(len(QUERIES[name].params), n, name)
            self.assertTrue(QUERIES[name].thread_safe, name)
            self.assertTrue(hasattr(HostV1, name), name)
        self.assertFalse(QUERIES["trace_line"].thread_safe)
        self.assertFalse(QUERIES["target_count"].thread_safe)

    def test_argument_errors(self):
        hdr = SETTINGS + "logic Q : actor;\nparam C: Cfg;\nstate X: float;\nupdate(dt) { "
        self.assertEqual(codes(hdr + "let h = host.sweep_capsule(VEC3_ZERO, VEC3_ZERO, 1.0, 2.0, C.Ch, C.Resp); }"), [])
        self.assertEqual(codes(hdr + "let h = host.sweep_capsule(VEC3_ZERO, VEC3_ZERO, 1.0, 2.0, C.Ch); }"), ["DAL4041"])
        self.assertEqual(codes(hdr + "let h = host.sweep_capsule(VEC3_ZERO, VEC3_ZERO, 1.0, 2.0, 3, C.Resp); }"), ["DAL4002"])
        self.assertEqual(codes(hdr + "let h = host.line_trace(VEC3_ZERO, VEC3_ZERO, MovementMode.Walking); }"), ["DAL4002"])
        self.assertEqual(codes(hdr + "X = host.primitive_speed(1.0); }"), ["DAL4002"])
        self.assertEqual(codes(hdr + "let b = host.is_walkable(VEC3_ZERO); }"), ["DAL4002"])
        self.assertEqual(codes(hdr + "let h = host.sweep_nope(VEC3_ZERO); }"), ["DAL3060"])
        self.assertEqual(codes(hdr + "let o = host.overlap_capsule(VEC3_ZERO, 1.0, 2.0, Channel.Pawn, C.Resp); X = o ? 1.0 : 0.0; }"), [])

    def test_channels_and_sets(self):
        ok = SETTINGS + "logic Q;\nparam C: Cfg;\nstate Ch: Channel = Channel.Camera;\nupdate(dt) { Ch = C.Ch; }"
        self.assertEqual(codes(ok), [])
        self.assertEqual(codes("settings S { L: array<Channel>; }\nlogic Q;\nparam P: S;\nstate N: int;\nupdate(dt) { N = P.L.len; }"), [])
        self.assertEqual(codes("logic Q;\nstate Ch: Channel;\nupdate(dt) { Ch = Channel.Nope; }"), ["DAL3032"])
        self.assertEqual(codes("enum Channel { A }\nlogic Q;\nupdate(dt) { }"), ["DAL3002"])
        # channel_set is array<Channel>
        both = "settings S { A: channel_set; B: array<Channel>; }\nlogic Q;\nparam P: S;\nupdate(dt) { let n = host.overlap_capsule(VEC3_ZERO, 1.0, 1.0, Channel.Pawn, P.B); }"
        self.assertEqual(codes(both.replace("logic Q;", "logic Q : actor;")), [])

    def test_the_thread_safe_set(self):
        # an anim instance may ask the queries marked as thread safe; the rest stays with the actor logic
        self.assertEqual(codes(ANIM), [])
        self.assertEqual(codes(ACTOR), [])
        for call in ("host.trace_line(VEC3_ZERO, VEC3_ZERO)", "host.trace_sphere(VEC3_ZERO, VEC3_ZERO, 1.0)", "host.target_count()"):
            with self.subTest(call=call):
                self.assertEqual(codes(f"logic Q;\nstate X: float;\nupdate(dt) {{ let a = {call}; }}"), ["DAL5041"])
        msg = None
        try:
            dal.check_source("logic Q;\nupdate(dt) { let a = host.target_count(); }", "t.dal")
        except dal.DalError as e:
            msg = e.diagnostics[0].message
        self.assertIn("sweep_capsule", msg)

    def test_queries_still_do_not_belong_in_functions_views_or_constants(self):
        hdr = SETTINGS + "logic Q;\nparam C: Cfg;\n"
        self.assertEqual(codes(hdr + "fn f(a: vec3, r: channel_set) -> bool { return host.overlap_capsule(a, 1.0, 1.0, Channel.Pawn, r); }\nupdate(dt) { }"), ["DAL5042"])
        self.assertEqual(codes(hdr + "view v(a: vec3) -> bool { return host.overlap_capsule(a, 1.0, 1.0, Channel.Pawn, C.Resp); }\nupdate(dt) { }"), ["DAL5044"])
        # an entry or a handler may ask (the entry of an anim instance runs on the worker thread, hence the thread-safe set)
        self.assertEqual(codes(hdr + "state B: bool;\nentry E(a: vec3 = VEC3_ZERO) { B = host.overlap_capsule(a, 1.0, 1.0, Channel.Pawn, C.Resp); }\nupdate(dt) { }"), [])

    def test_hit_members_and_primitives(self):
        hdr = "logic Q : actor;\nstate P: primitive = none;\nstate X: float;\nstate B: bool;\nupdate(dt) { "
        self.assertEqual(codes(hdr + "let h = host.trace_line(VEC3_ZERO, VEC3_ZERO); P = h.primitive; B = valid(P) && P != none && h.start_penetrating; }"), [])
        self.assertEqual(codes(hdr + "let h = host.trace_line(VEC3_ZERO, VEC3_ZERO); X = h.nope; }"), ["DAL4030"])
        self.assertEqual(codes("logic Q;\nstate H: hit;\nupdate(dt) { }"), ["DAL4023"])
        self.assertEqual(codes(hdr + "let h = host.trace_line(VEC3_ZERO, VEC3_ZERO); let g = host.trace_line(VEC3_ZERO, VEC3_ZERO); B = h.primitive == g.primitive; }"), [])
        self.assertEqual(codes(hdr + "P = 3; }"), ["DAL4002"])


class QueryPythonTests(unittest.TestCase):
    SCENE = dict(ground=0.0, spheres=[((300.0, 0.0, 50.0), 50.0)])

    def out(self, frames, src=ACTOR, **params):
        return run(src, frames, **params)

    def test_line_trace_hits_the_ground(self):
        f = scene(From=(0.0, 0.0, 100.0), To=(0.0, 0.0, -100.0), **self.SCENE)
        o = self.out([f])[0]
        self.assertEqual(o["Line"], 100.0)                                             # a line trace: the ground at half way
        self.assertTrue(o["Blocked"])

    def test_a_capsule_lands_with_its_bottom_on_the_ground(self):
        f = scene(From=(0.0, 0.0, 200.0), To=(0.0, 0.0, 0.0), primitives={"Ground": {"speed": 12.0, "can_step_up": False}}, **self.SCENE)
        o = self.out([f])[0]
        # half height 90: the capsule center stops at z = 90, after 110 of the 200 it travels
        self.assertAlmostEqual(o["Time"], 0.55)
        self.assertAlmostEqual(o["Dist"], 110.0)
        near(self, o["Loc"], (0.0, 0.0, 90.0))
        near(self, o["Point"], (0.0, 0.0, 0.0))
        self.assertEqual(o["Normal"], (0.0, 0.0, 1.0))
        self.assertEqual(o["Face"], (0.0, 0.0, 1.0))
        self.assertEqual((o["Prim"], o["Pen"], o["Valid"]), ("Ground", False, True))
        self.assertEqual((o["Walk"], o["Spd"], o["Step"]), (True, 12.0, False))
        self.assertEqual(o["Tf"], ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)))

    def test_a_shape_that_starts_inside_hits_at_time_zero(self):
        o = self.out([scene(From=(0.0, 0.0, 50.0), To=(0.0, 0.0, 400.0), **self.SCENE)])[0]
        self.assertEqual((o["Blocked"], o["Pen"], o["Time"], o["Dist"]), (True, True, 0.0, 0.0))
        near(self, o["Loc"], (0.0, 0.0, 50.0))
        self.assertFalse(o["Walk"])                                                    # standing in the ground is not standing on it
        self.assertTrue(o["Over"])                                                     # the capsule overlaps the ground

    def test_a_sphere_obstacle_and_its_slope(self):
        # a capsule walking into a sphere of radius 50 at (300, 0, 50): the side of the sphere is too steep to walk on
        o = self.out([scene(From=(0.0, 0.0, 120.0), To=(500.0, 0.0, 120.0), **self.SCENE)])[0]
        self.assertTrue(o["Blocked"])
        self.assertEqual(o["Prim"], "Sphere0")
        self.assertFalse(o["Walk"])
        self.assertLess(o["Normal"][0], 0.0)                                           # pushed back toward the start
        self.assertLess(o["Time"], 0.6)

    def test_overlaps(self):
        far = self.out([scene(From=(0.0, 0.0, 400.0), To=(0.0, 0.0, 500.0), **self.SCENE)])[0]
        self.assertFalse(far["Over"])
        at_sphere = self.out([scene(From=(300.0, 0.0, 130.0), To=(300.0, 0.0, 250.0), **self.SCENE)])[0]
        self.assertTrue(at_sphere["Over"])                                             # the top cap reaches into the sphere

    def test_no_scene_no_hit(self):
        host = MockHost()
        self.assertFalse(host.sweep_capsule(rt.Vec3(0, 0, 0), rt.Vec3(0, 0, -50), 30.0, 90.0, 3, ()).blocking)
        h = host.sweep_capsule(rt.Vec3(0, 0, 0), rt.Vec3(0, 0, -50), 30.0, 90.0, 3, ())
        self.assertEqual((h.location, h.point, h.primitive, h.start_penetrating), (rt.Vec3(0, 0, -50), rt.Vec3(0, 0, -50), None, False))
        self.assertFalse(host.primitive_valid(None))
        self.assertFalse(host.primitive_can_step_up(None))
        self.assertFalse(HostV1().overlap_capsule(rt.VEC3_ZERO, 1.0, 1.0, 0, ()))

    def test_is_walkable_reads_the_floor_angle_of_the_host(self):
        host = MockHost()
        flat = rt.Hit(True, (0, 0, 0), (0.0, 0.0, 1.0), 1.0, 0.5)
        steep = rt.Hit(True, (0, 0, 0), (0.8, 0.0, 0.6), 1.0, 0.5)
        self.assertTrue(host.is_walkable(flat))
        self.assertFalse(host.is_walkable(steep))
        host.walkable_floor_z = 0.5
        self.assertTrue(host.is_walkable(steep))
        self.assertFalse(host.is_walkable(rt.Hit(False)))
        self.assertFalse(host.is_walkable(rt.Hit(True, (0, 0, 0), (0.0, 0.0, 1.0), 0.0, 0.0, start_penetrating=True)))

    def test_the_channel_and_the_blocking_list_come_from_the_settings(self):
        data = {"Ch": "ECC_Camera", "Resp": ["ECC_WorldStatic", "WorldDynamic"]}
        m = compile_ok(ACTOR)
        cls = load_class(m)
        calls = []

        class Spy(MockHost):
            def sweep_capsule(self, start, end, radius, half_height, channel, responses):
                calls.append((channel, tuple(responses), radius, half_height))
                return super().sweep_capsule(start, end, radius, half_height, channel, responses)

        host = Spy()
        obj = cls(host, C=data)
        obj.set_input("From", (0, 0, 10)), obj.set_input("To", (0, 0, 20))
        obj.update(0.1)
        self.assertEqual(calls, [(rt.Channel.Camera, (0, 1), 30.0, 90.0)])

    def test_random(self):
        for src in (ACTOR, ANIM):
            m = compile_ok(src)
            for seed in (1, 2, 3):
                frames = harness.random_frames(m, 80, seed)
                for k, f in enumerate(frames):
                    f["free"]["From"] = tuple(((seed * 31 + k * 17 + j * 53) % 600) - 300.0 for j in range(3))
                    f["free"]["To"] = tuple(((seed * 13 + k * 29 + j * 71) % 600) - 300.0 for j in range(3))
                self.assertEqual(harness.compare(harness.run_interpreter(m, frames), harness.run_python(m, frames)), [], (src[:40], seed))

    def test_an_anim_logic_gets_a_scene_from_the_random_frames_only_when_it_asks(self):
        quiet = compile_ok("logic Q;\nstate X: float;\nupdate(dt) { X += dt; }")
        self.assertNotIn("world", harness.random_frames(quiet, 2, 1)[0])
        self.assertIn("world", harness.random_frames(compile_ok(ANIM), 2, 1)[0])


@unittest.skipIf(tcc.COMPILER is None, "no C++ compiler found")
class QueryCppTests(unittest.TestCase):
    def frames(self, m, seed, n=70):
        frames = harness.random_frames(m, n, seed)
        for k, f in enumerate(frames):
            f["free"]["From"] = tuple(((seed * 31 + k * 17 + j * 53) % 600) - 300.0 for j in range(3))
            f["free"]["To"] = tuple(((seed * 13 + k * 29 + j * 71) % 600) - 300.0 for j in range(3))
        return frames

    def test_actor_logic_matches_python(self):
        m = compile_ok(ACTOR)
        for seed in (1, 2, 3):
            tcc.CppCompileTests.run_case(self, m, self.frames(m, seed), warnings_as_errors=True, default=2e-3)

    def test_anim_logic_matches_python(self):
        m = compile_ok(ANIM)
        for seed in (4, 5):
            tcc.CppCompileTests.run_case(self, m, self.frames(m, seed), warnings_as_errors=True, default=2e-3)

    def test_hand_made_scenes_match_python(self):
        m = compile_ok(ACTOR)
        frames = [scene(From=(0.0, 0.0, 200.0), To=(0.0, 0.0, 0.0), primitives={"Ground": {"speed": 12.0, "can_step_up": False}}, **QueryPythonTests.SCENE),
                  scene(From=(0.0, 0.0, 50.0), To=(0.0, 0.0, 400.0), **QueryPythonTests.SCENE),
                  scene(From=(0.0, 0.0, 120.0), To=(500.0, 0.0, 120.0), primitives={"Sphere0": {"speed": 3.0, "transform": ((1.0, 2.0, 3.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))}}, **QueryPythonTests.SCENE),
                  scene(From=(300.0, 0.0, 130.0), To=(300.0, 0.0, 250.0), **QueryPythonTests.SCENE)]
        tcc.CppCompileTests.run_case(self, m, frames, warnings_as_errors=True, default=2e-3)

    def test_the_channel_lists_of_the_settings_reach_the_query(self):
        m = compile_ok(ACTOR)
        data = {"Ch": "ECC_Camera", "Resp": ["ECC_WorldStatic", "ECC_Destructible"]}
        tcc.CppCompileTests.run_case(self, m, self.frames(m, 7, 20), warnings_as_errors=True, params={"C": data}, default=2e-3)

    def test_generated_text(self):
        files = generate_cpp(compile_ok(ACTOR))
        h, cpp = files["Qa.h"], files["Qa.cpp"]
        self.assertIn("TEnumAsByte<ECollisionChannel> Ch = ECC_Visibility;", h)
        self.assertIn("TArray<TEnumAsByte<ECollisionChannel>> Resp;", h)
        self.assertIn("DalResponseParams.CollisionResponse.SetResponse(DalBlocking, ECR_Block);", cpp)
        self.assertIn("FCollisionShape::MakeCapsule(DalRadius, DalHalfHeight), DalParams, DalResponseParams);", cpp)
        self.assertIn("OverlapBlockingTestByChannel(FVector(DalCenter), FQuat::Identity, DalChannel", cpp)
        self.assertIn("DalResult.Primitive = DalHit.GetComponent();", cpp)
        self.assertIn("Spd = DalPrimitiveSpeed(Prim);", cpp)
        self.assertIn("FCollisionQueryParams DalParams(SCENE_QUERY_STAT(DalTrace), false, GetOwner());", cpp)
        anim = generate_cpp(compile_ok(ANIM))["Qa.cpp"]
        self.assertIn("FCollisionQueryParams DalParams(SCENE_QUERY_STAT(DalTrace), false, GetOwningActor());", anim)
        self.assertIn('#include "DalHost.h"', generate_cpp(compile_ok(ANIM))["Qa.h"])

    def test_programs_of_the_earlier_versions_are_unchanged(self):
        # the old trace queries fill the new members of a hit only in a program that can read them
        old = "logic Q : actor;\nstate B: bool;\nupdate(dt) { B = host.trace_line(VEC3_ZERO, VEC3_ZERO).blocking; }"
        new = "logic Q : actor;\nstate B: bool;\nupdate(dt) { B = host.trace_line(VEC3_ZERO, VEC3_ZERO).start_penetrating; }"
        self.assertNotIn("DalResult.Location", generate_cpp(compile_ok(old))["Q.cpp"])
        self.assertIn("DalResult.bStartPenetrating = DalHit.bStartPenetrating;", generate_cpp(compile_ok(new))["Q.cpp"])
        m = compile_ok(new)
        tcc.CppCompileTests.run_case(self, m, harness.random_frames(m, 60, 3), warnings_as_errors=True, default=2e-3)

    def test_the_documentation_marks_the_thread_safe_queries(self):
        from dal.hostapi import protocol_markdown

        text = protocol_markdown()
        self.assertIn("| `sweep_capsule` † |", text)
        self.assertIn("| `trace_line` |", text)


if __name__ == "__main__":
    unittest.main()
