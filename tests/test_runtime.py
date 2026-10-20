"""The Python runtime math against hand-derived Unreal Engine 5.8 semantics.

Every expectation below is worked out from the definition of the UE function named in the test
(FMath, FRotator, FQuat, TVector); the comments show the arithmetic where it is not obvious.
"""

import math
import unittest

import _bootstrap  # noqa: F401

from dal import runtime as rt
from dal.runtime import Quat, Rotator, Vec2, Vec3


class ValueTypeTests(unittest.TestCase):
    def test_vector_arithmetic(self):
        a, b = Vec3(1, 2, 3), Vec3(4, 5, 6)
        self.assertEqual(a + b, Vec3(5, 7, 9))
        self.assertEqual(b - a, Vec3(3, 3, 3))
        self.assertEqual(a * 2.0, Vec3(2, 4, 6))
        self.assertEqual(2.0 * a, Vec3(2, 4, 6))
        self.assertEqual(a * b, Vec3(4, 10, 18))
        self.assertEqual(-a, Vec3(-1, -2, -3))
        self.assertEqual(Vec2(1, 2) + Vec2(3, 4), Vec2(4, 6))

    def test_division_by_zero_gives_zero_vector(self):
        self.assertEqual(Vec3(1, 2, 3) / 0.0, Vec3(0, 0, 0))
        self.assertEqual(Vec2(1, 2) / 2.0, Vec2(0.5, 1.0))

    def test_values_are_immutable_hashable_tuples(self):
        v = Vec3(1, 2, 3)
        self.assertEqual(tuple(v), (1.0, 2.0, 3.0))
        self.assertEqual((v.x, v.y, v.z), (1.0, 2.0, 3.0))
        self.assertEqual(hash(v), hash(Vec3(1, 2, 3)))
        with self.assertRaises(AttributeError):
            v.x = 5  # type: ignore[misc]

    def test_rotator_and_quat_members(self):
        self.assertEqual((Rotator(1, 2, 3).pitch, Rotator(1, 2, 3).yaw, Rotator(1, 2, 3).roll), (1.0, 2.0, 3.0))
        self.assertEqual(Quat(1, 2, 3, 4).w, 4.0)
        self.assertEqual(Quat(1, 2, 3).w, 1.0)  # identity default for w

    def test_coercions_accept_plain_tuples(self):
        self.assertIs(type(rt.as_vec3((1, 2, 3))), Vec3)
        self.assertIs(type(rt.as_quat([0, 0, 0, 1])), Quat)
        v = Vec3(1, 2, 3)
        self.assertIs(rt.as_vec3(v), v)

    def test_swizzle(self):
        self.assertEqual(rt.swizzle(Vec3(1, 2, 3), "xy"), Vec2(1, 2))
        self.assertEqual(rt.swizzle(Vec3(1, 2, 3), "zx"), Vec2(3, 1))
        self.assertEqual(rt.swizzle(Vec2(1, 2), "yxy"), Vec3(2, 1, 2))


class IntegerTests(unittest.TestCase):
    def test_i32_wraps(self):
        self.assertEqual(rt.i32(2**31), -(2**31))
        self.assertEqual(rt.i32(-(2**31) - 1), 2**31 - 1)
        self.assertEqual(rt.i32(5), 5)

    def test_division_truncates_toward_zero(self):
        self.assertEqual(rt.idiv(7, 2), 3)
        self.assertEqual(rt.idiv(-7, 2), -3)
        self.assertEqual(rt.idiv(7, -2), -3)
        self.assertEqual(rt.idiv(-7, -2), 3)

    def test_remainder_has_the_sign_of_the_dividend(self):
        self.assertEqual(rt.imod(7, 2), 1)
        self.assertEqual(rt.imod(-7, 2), -1)
        self.assertEqual(rt.imod(7, -2), 1)
        self.assertEqual(rt.imod(-7, -2), -1)

    def test_division_by_zero_and_overflow_are_defined(self):
        self.assertEqual(rt.idiv(5, 0), 0)
        self.assertEqual(rt.imod(5, 0), 0)
        self.assertEqual(rt.idiv(-(2**31), -1), -(2**31))
        self.assertEqual(rt.fdiv(1.0, 0.0), 0.0)
        self.assertEqual(rt.fdiv(3.0, 2.0), 1.5)

    def test_float_to_int_conversions(self):
        self.assertEqual(rt.to_int(-2.7), -2)         # TruncToInt
        self.assertEqual(rt.floor_to_int(-2.7), -3)   # FloorToInt
        self.assertEqual(rt.ceil_to_int(2.1), 3)      # CeilToInt
        self.assertEqual(rt.round_to_int(2.5), 3)     # FloorToInt(F + 0.5)
        self.assertEqual(rt.round_to_int(-2.5), -2)
        self.assertEqual(rt.round_to_int(-2.6), -3)
        self.assertEqual(rt.to_int(1e20), 2**31 - 1)  # saturates instead of undefined behaviour
        self.assertEqual(rt.to_int(-1e20), -(2**31))
        self.assertEqual(rt.to_int(float("nan")), 0)

    def test_abs_of_int_min_wraps(self):
        self.assertEqual(rt.abs_(-(2**31)), -(2**31))
        self.assertEqual(rt.abs_(-3), 3)
        self.assertEqual(rt.abs_(-3.5), 3.5)


class ScalarTests(unittest.TestCase):
    def test_min_max_clamp_follow_fmath_definitions(self):
        self.assertEqual(rt.min_(1, 2), 1)
        self.assertEqual(rt.max_(1, 2), 2)
        self.assertEqual(rt.clamp(5.0, 0.0, 3.0), 3.0)
        self.assertEqual(rt.clamp(-5.0, 0.0, 3.0), 0.0)
        # FMath::Clamp is Max(Min(X, Max), Min): an inverted range yields the lower bound
        self.assertEqual(rt.clamp(5.0, 10.0, 0.0), 10.0)
        self.assertEqual(rt.saturate(1.5), 1.0)

    def test_sign_keeps_the_type(self):
        self.assertEqual((rt.sign(-3.0), rt.sign(0.0), rt.sign(2.0)), (-1.0, 0.0, 1.0))
        self.assertIsInstance(rt.sign(2.0), float)
        self.assertIsInstance(rt.sign(2), int)

    def test_rounding_functions(self):
        self.assertEqual(rt.round_(2.5), 3.0)       # RoundToFloat = FloorToFloat(F + 0.5)
        self.assertEqual(rt.round_(-2.5), -2.0)
        self.assertEqual(rt.floor_(-0.5), -1.0)
        self.assertEqual(rt.ceil_(-0.5), -0.0)
        self.assertEqual(rt.trunc_(-2.7), -2.0)
        self.assertAlmostEqual(rt.frac(-0.25), 0.75)  # FMath::Frac = Value - FloorToFloat(Value)
        self.assertAlmostEqual(rt.frac(2.25), 0.25)

    def test_fmod(self):
        self.assertAlmostEqual(rt.fmod(5.5, 2.0), 1.5)
        self.assertAlmostEqual(rt.fmod(-5.5, 2.0), -1.5)   # sign of the dividend, like C
        self.assertEqual(rt.fmod(1.0, 0.0), 0.0)            # FMath::Fmod returns 0 for |Y| <= SMALL_NUMBER

    def test_inverse_trig_clamps_its_argument(self):
        self.assertEqual(rt.acos(2.0), 0.0)
        self.assertAlmostEqual(rt.acos(-2.0), math.pi)
        self.assertAlmostEqual(rt.asin(-3.0), -math.pi / 2)

    def test_degrees_radians(self):
        self.assertAlmostEqual(rt.deg_to_rad(180.0), math.pi)
        self.assertAlmostEqual(rt.rad_to_deg(math.pi / 2), 90.0)

    def test_inv_exp_approx(self):
        self.assertEqual(rt.inv_exp_approx(0.0), 1.0)
        # 1 / (1 + 1.00746054 + 0.45053901 + 0.25724632)
        self.assertAlmostEqual(rt.inv_exp_approx(1.0), 1.0 / 2.71524587, places=12)
        for i in range(0, 101):
            x = i / 100.0
            self.assertLess(abs(rt.inv_exp_approx(x) - math.exp(-x)), 1.0e-3 * 1.5)  # documented < 0.1% on [0, 1]

    def test_nearly_helpers(self):
        self.assertTrue(rt.is_nearly_zero(1e-9))
        self.assertFalse(rt.is_nearly_zero(1e-7))
        self.assertTrue(rt.is_nearly_zero(0.05, 0.1))
        self.assertTrue(rt.is_nearly_equal(1.0, 1.0 + 1e-9))
        self.assertFalse(rt.is_nearly_equal(1.0, 1.001))
        self.assertTrue(rt.is_nearly_equal(1.0, 1.001, 0.01))

    def test_domain_errors_follow_ieee_without_raising(self):
        self.assertTrue(math.isnan(rt.sqrt(-1.0)))
        self.assertTrue(math.isinf(rt.inv_sqrt(0.0)))
        self.assertEqual(rt.log(0.0), -math.inf)
        self.assertEqual(rt.exp(1e6), math.inf)
        self.assertEqual(rt.pow_(0.0, -1.0), math.inf)
        self.assertTrue(math.isnan(rt.pow_(-8.0, 1.0 / 3.0)))
        self.assertTrue(math.isnan(rt.sin(math.inf)))


class AngleTests(unittest.TestCase):
    def test_unwind_degrees(self):
        for value, expected in ((190.0, -170.0), (-190.0, 170.0), (540.0, 180.0), (180.0, 180.0), (-180.0, -180.0), (360.0, 0.0), (0.0, 0.0)):
            self.assertAlmostEqual(rt.unwind_degrees(value), expected, msg=str(value))

    def test_normalize_axis_is_minus180_exclusive_to_180_inclusive(self):
        for value, expected in ((180.0, 180.0), (-180.0, 180.0), (190.0, -170.0), (360.0, 0.0), (-10.0, -10.0), (725.0, 5.0)):
            self.assertAlmostEqual(rt.normalize_axis(value), expected, msg=str(value))

    def test_clamp_axis(self):
        self.assertAlmostEqual(rt.clamp_axis(-10.0), 350.0)
        self.assertAlmostEqual(rt.clamp_axis(360.0), 0.0)
        self.assertAlmostEqual(rt.clamp_axis(720.5), 0.5)

    def test_delta_angle_takes_the_short_way(self):
        self.assertAlmostEqual(rt.delta_angle(170.0, -170.0), 20.0)
        self.assertAlmostEqual(rt.delta_angle(-170.0, 170.0), -20.0)
        self.assertAlmostEqual(rt.delta_angle(10.0, 10.0), 0.0)
        self.assertAlmostEqual(rt.delta_angle(0.0, 180.0), 180.0)
        self.assertAlmostEqual(rt.delta_angle(0.0, 270.0), -90.0)

    def test_clamp_angle(self):
        self.assertAlmostEqual(rt.clamp_angle(100.0, -45.0, 45.0), 45.0)
        self.assertAlmostEqual(rt.clamp_angle(-100.0, -45.0, 45.0), -45.0)
        self.assertAlmostEqual(rt.clamp_angle(10.0, -45.0, 45.0), 10.0)
        self.assertAlmostEqual(rt.clamp_angle(350.0, -45.0, 45.0), -10.0)
        # an arc that crosses 180: [170, 190]
        self.assertAlmostEqual(rt.clamp_angle(-175.0, 170.0, 190.0), -175.0)
        self.assertAlmostEqual(rt.clamp_angle(150.0, 170.0, 190.0), 170.0)

    def test_range_helpers(self):
        self.assertAlmostEqual(rt.range_pct(0.0, 10.0, 5.0), 0.5)
        self.assertEqual(rt.range_pct(5.0, 5.0, 7.0), 1.0)   # degenerate range: before / after
        self.assertEqual(rt.range_pct(5.0, 5.0, 3.0), 0.0)
        self.assertEqual(rt.map_range_clamped(0.0, 10.0, 100.0, 200.0, 15.0), 200.0)
        self.assertEqual(rt.map_range_clamped(0.0, 10.0, 100.0, 200.0, -5.0), 100.0)
        self.assertAlmostEqual(rt.map_range_clamped(0.0, 10.0, 100.0, 200.0, 2.5), 125.0)
        self.assertAlmostEqual(rt.map_range(0.0, 10.0, 100.0, 200.0, 15.0), 250.0)


class InterpolationTests(unittest.TestCase):
    def test_finterp_to(self):
        self.assertAlmostEqual(rt.interp_to(0.0, 10.0, 0.1, 5.0), 5.0)        # Dist * Clamp(dt * speed)
        self.assertAlmostEqual(rt.interp_to(0.0, 10.0, 1.0, 5.0), 10.0)       # dt * speed clamps to 1
        self.assertEqual(rt.interp_to(3.0, 10.0, 0.1, 0.0), 10.0)             # speed <= 0 jumps
        self.assertEqual(rt.interp_to(10.0 - 1e-5, 10.0, 0.1, 5.0), 10.0)     # Dist^2 < SMALL_NUMBER snaps
        self.assertAlmostEqual(rt.interp_to(10.0, 0.0, 0.1, 5.0), 5.0)

    def test_finterp_constant_to(self):
        self.assertAlmostEqual(rt.interp_constant_to(0.0, 10.0, 0.1, 5.0), 0.5)
        self.assertAlmostEqual(rt.interp_constant_to(9.8, 10.0, 0.1, 5.0), 10.0)
        self.assertAlmostEqual(rt.interp_constant_to(10.0, 0.0, 0.1, 5.0), 9.5)
        self.assertEqual(rt.interp_constant_to(5.0, 5.0, 0.1, 5.0), 5.0)

    def test_vector_interp_to(self):
        r = rt.interp_to(Vec3(0, 0, 0), Vec3(10, 0, 20), 0.1, 5.0)
        self.assertEqual(r, Vec3(5.0, 0.0, 10.0))
        # SizeSquared < KINDA_SMALL_NUMBER (1e-4) snaps to the target
        self.assertEqual(rt.interp_to(Vec3(0, 0, 0), Vec3(0.005, 0, 0), 0.1, 5.0), Vec3(0.005, 0, 0))
        self.assertEqual(rt.interp_to(Vec2(0, 0), Vec2(4, 2), 0.25, 2.0), Vec2(2.0, 1.0))

    def test_vector_interp_constant_to(self):
        r = rt.interp_constant_to(Vec3(0, 0, 0), Vec3(3, 4, 0), 1.0, 2.5)    # step = 2.5 along (0.6, 0.8)
        self.assertAlmostEqual(r.x, 1.5)
        self.assertAlmostEqual(r.y, 2.0)
        self.assertEqual(rt.interp_constant_to(Vec3(0, 0, 0), Vec3(3, 4, 0), 1.0, 5.0), Vec3(3, 4, 0))  # within reach
        self.assertEqual(rt.interp_constant_to(Vec2(1, 1), Vec2(5, 5), 1.0, 0.0), Vec2(1, 1))           # no step

    def test_rotator_interp_goes_the_short_way(self):
        r = rt.interp_to(Rotator(0, 170, 0), Rotator(0, -170, 0), 0.1, 5.0)
        self.assertAlmostEqual(r.yaw, 180.0)       # delta 20 deg * 0.5, then NormalizeAxis(180) = 180
        r = rt.interp_to(Rotator(0, 170, 0), Rotator(0, -170, 0), 0.2, 5.0)   # full snap
        self.assertAlmostEqual(r.yaw, -170.0)
        self.assertEqual(rt.interp_to(Rotator(1, 2, 3), Rotator(1, 2, 3), 0.1, 5.0), Rotator(1, 2, 3))
        self.assertEqual(rt.interp_to(Rotator(1, 2, 3), Rotator(9, 9, 9), 0.0, 5.0), Rotator(1, 2, 3))   # dt == 0
        self.assertEqual(rt.interp_to(Rotator(1, 2, 3), Rotator(9, 9, 9), 0.1, 0.0), Rotator(9, 9, 9))   # speed <= 0

    def test_rotator_interp_constant_to(self):
        r = rt.interp_constant_to(Rotator(0, 170, 0), Rotator(0, -170, 0), 0.1, 50.0)   # 5 deg per axis step
        self.assertAlmostEqual(r.yaw, 175.0)
        r = rt.interp_constant_to(Rotator(0, 170, 0), Rotator(0, -170, 0), 1.0, 50.0)
        self.assertAlmostEqual(r.yaw, -170.0)

    def test_quat_interp(self):
        q0 = Quat(0, 0, 0, 1)
        q1 = rt.to_quat(Rotator(0, 90, 0))
        half = rt.interp_to(q0, q1, 0.1, 5.0)               # slerp alpha 0.5 -> yaw 45
        self.assertAlmostEqual(rt.to_rotator(half).yaw, 45.0, places=9)
        self.assertEqual(rt.interp_to(q0, q1, 0.1, 0.0), q1)
        self.assertEqual(rt.interp_to(q0, q0, 0.1, 5.0), q0)
        # constant speed: dt * speed = 0.25 of the 90 degrees distance measured as AngularDistance
        c = rt.interp_constant_to(q0, q1, 0.1, 2.5)
        self.assertGreater(rt.to_rotator(c).yaw, 0.0)
        self.assertLess(rt.to_rotator(c).yaw, 90.0)

    def test_exponential_smoothing(self):
        v = rt.exponential_smoothing(10.0, 0.0, 0.1, 0.2)         # Target + (Cur - Target) * InvExpApprox(dt / T)
        self.assertAlmostEqual(v, 10.0 * rt.inv_exp_approx(0.5))
        self.assertEqual(rt.exponential_smoothing(10.0, 2.0, 0.1, 0.0), 2.0)   # smoothing time ~0 snaps
        w = rt.exponential_smoothing(Vec2(4, 0), Vec2(0, 0), 0.1, 0.2)
        self.assertAlmostEqual(w.x, 4.0 * rt.inv_exp_approx(0.5))


class VectorTests(unittest.TestCase):
    def test_lengths(self):
        self.assertAlmostEqual(rt.length(Vec3(2, 3, 6)), 7.0)
        self.assertAlmostEqual(rt.length_sq(Vec2(3, 4)), 25.0)
        self.assertAlmostEqual(rt.distance(Vec2(1, 1), Vec2(4, 5)), 5.0)

    def test_dot_cross(self):
        self.assertAlmostEqual(rt.dot(Vec3(1, 2, 3), Vec3(4, -5, 6)), 12.0)
        self.assertEqual(rt.cross(Vec3(1, 0, 0), Vec3(0, 1, 0)), Vec3(0, 0, 1))
        self.assertEqual(rt.cross(Vec3(0, 1, 0), Vec3(1, 0, 0)), Vec3(0, 0, -1))

    def test_normalize_is_safe(self):
        self.assertEqual(rt.normalize(Vec3(0, 0, 0)), Vec3(0, 0, 0))
        self.assertEqual(rt.normalize(Vec3(3, 0, 0)), Vec3(1, 0, 0))
        self.assertEqual(rt.normalize(Vec3(1e-5, 0, 0)), Vec3(0, 0, 0))     # SizeSquared 1e-10 < SMALL_NUMBER
        n = rt.normalize(Vec2(3, 4))
        self.assertAlmostEqual(n.x, 0.6)
        self.assertAlmostEqual(n.y, 0.8)
        self.assertEqual(rt.normalize(Vec2(0, 0)), Vec2(0, 0))

    def test_clamp_length(self):
        r = rt.clamp_length(Vec3(3, 4, 0), 2.5)
        self.assertAlmostEqual(r.x, 1.5)
        self.assertAlmostEqual(r.y, 2.0)
        self.assertEqual(rt.clamp_length(Vec3(3, 4, 0), 10.0), Vec3(3, 4, 0))
        self.assertEqual(rt.clamp_length(Vec3(3, 4, 0), 0.0), Vec3(0, 0, 0))   # MaxSize < KINDA_SMALL_NUMBER
        self.assertEqual(rt.clamp_length(Vec2(3, 4), 0.0), Vec2(0, 0))

    def test_lerp(self):
        self.assertAlmostEqual(rt.lerp(2.0, 6.0, 0.25), 3.0)
        self.assertEqual(rt.lerp(2.0, 6.0, 1.5), 8.0)                           # unclamped
        self.assertEqual(rt.lerp(Vec3(0, 0, 0), Vec3(2, 4, 6), 0.5), Vec3(1, 2, 3))


class RotationTests(unittest.TestCase):
    def assertVec(self, v, expected, places=12):
        for a, b in zip(v, expected):
            self.assertAlmostEqual(a, b, places=places)

    def test_yaw_quaternion(self):
        q = rt.to_quat(Rotator(0, 90, 0))
        self.assertVec(q, (0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5)))
        self.assertVec(rt.rotate(q, Vec3(1, 0, 0)), (0, 1, 0))      # yaw turns +X toward +Y
        self.assertVec(rt.unrotate(q, Vec3(0, 1, 0)), (1, 0, 0))

    def test_pitch_and_roll_follow_ue_conventions(self):
        pitch_up = rt.to_quat(Rotator(90, 0, 0))
        self.assertVec(rt.rotate(pitch_up, Vec3(1, 0, 0)), (0, 0, 1))      # positive pitch looks up
        roll = rt.to_quat(Rotator(0, 0, 90))
        self.assertVec(roll, (-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)))  # FRotator::Quaternion: X = -SR*CP*CY
        self.assertVec(rt.rotate(roll, Vec3(0, 1, 0)), (0, 0, -1))

    def test_rotator_overloads_match_quat(self):
        r = Rotator(20, -70, 35)
        v = Vec3(1, 2, 3)
        self.assertVec(rt.rotate(r, v), rt.rotate(rt.to_quat(r), v))
        self.assertVec(rt.unrotate(r, rt.rotate(r, v)), v)

    def test_quat_rotator_round_trip(self):
        for r in (Rotator(10, 30, 50), Rotator(-45, 120, -20), Rotator(80, -170, 5), Rotator(0, 0, 0), Rotator(-30, 179, 90)):
            back = rt.to_rotator(rt.to_quat(r))
            self.assertVec(back, tuple(r), places=9)

    def test_gimbal_lock_branches(self):
        for pitch in (90.0, -90.0):
            back = rt.to_rotator(rt.to_quat(Rotator(pitch, 40, 0)))
            self.assertAlmostEqual(back.pitch, pitch)
            self.assertAlmostEqual(back.yaw, 40.0, places=6)
            self.assertAlmostEqual(back.roll, 0.0)

    def test_composition_order(self):
        yaw = rt.to_quat(Rotator(0, 90, 0))
        pitch = rt.to_quat(Rotator(90, 0, 0))
        # A * B applies B first, then A
        self.assertVec(rt.rotate(yaw * pitch, Vec3(1, 0, 0)), (0, 0, 1))
        self.assertVec(rt.rotate(pitch * yaw, Vec3(1, 0, 0)), (0, 1, 0))
        half_turn = yaw * yaw
        self.assertVec(rt.rotate(half_turn, Vec3(1, 0, 0)), (-1, 0, 0))

    def test_inverse_and_normalize(self):
        q = rt.to_quat(Rotator(10, 20, 30))
        self.assertVec(q * rt.quat_inverse(q), (0, 0, 0, 1))
        self.assertVec(rt.normalize(Quat(0, 0, 0, 2)), (0, 0, 0, 1))
        self.assertEqual(rt.normalize(Quat(0, 0, 0, 0)), Quat(0, 0, 0, 1))      # too small -> identity

    def test_slerp_takes_the_shortest_path(self):
        a = Quat(0, 0, 0, 1)
        b = rt.to_quat(Rotator(0, 90, 0))
        mid = rt.slerp(a, b, 0.5)
        self.assertAlmostEqual(rt.to_rotator(mid).yaw, 45.0, places=9)
        neg_b = Quat(-b.x, -b.y, -b.z, -b.w)               # same rotation, opposite sign
        self.assertAlmostEqual(rt.to_rotator(rt.slerp(a, neg_b, 0.5)).yaw, 45.0, places=9)
        self.assertAlmostEqual(math.sqrt(sum(c * c for c in mid)), 1.0, places=12)
        self.assertVec(rt.slerp(a, b, 0.0), tuple(a))
        self.assertVec(rt.slerp(a, b, 1.0), tuple(b))

    def test_delta_rotator_and_normalize(self):
        d = rt.delta_rotator(Rotator(0, -170, 0), Rotator(0, 170, 0))
        self.assertAlmostEqual(d.yaw, 20.0)
        n = rt.normalize_rotator(Rotator(190, -190, 540))
        self.assertVec(tuple(n), (-170.0, 170.0, 180.0), places=9)


class MovementModeTests(unittest.TestCase):
    def test_values_match_emovementmode(self):
        self.assertEqual([m.value for m in rt.MovementMode], list(range(7)))
        self.assertEqual(rt.MovementMode.Falling, 3)
        self.assertEqual(rt.MovementMode.None_, 0)


if __name__ == "__main__":
    unittest.main()
