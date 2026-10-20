"""The host protocol (dal.hostapi): the tables, the Python base class a Blender adapter implements, and the mock hosts.

What a host has to provide is written down once, in the tables of ``dal.hostapi``.  These tests keep the
tables, ``HostV1``, ``MockHost`` / ``SimHost``, the checker and the C++ ``DalHost.h`` in step with each other.
"""

import inspect
import unittest

import _bootstrap  # noqa: F401

import dal
from dal import runtime as rt
from dal.hostapi import COMMANDS, EVENTS, HOST_ITEMS, QUERIES, HostV0, HostV1
from dal.mockhost import MockHost, SimHost, SimMontage


def arity(fn) -> int:
    return len(inspect.signature(fn).parameters)


class ProtocolTests(unittest.TestCase):
    def test_every_command_is_a_host_method_with_the_declared_arity(self):
        for name, cmd in COMMANDS.items():
            fn = getattr(HostV1, name, None)
            self.assertIsNotNone(fn, f"HostV1.{name}")
            self.assertEqual(arity(fn) - 1, len(cmd.params), name)         # canonical argument list: defaults are filled in
            self.assertTrue(inspect.getdoc(fn), f"{name} must say which DreamVEd call implements it")

    def test_every_query_is_a_host_method_with_the_declared_arity(self):
        for name, q in QUERIES.items():
            fn = getattr(HostV1, name, None)
            self.assertIsNotNone(fn, f"HostV1.{name}")
            self.assertEqual(arity(fn) - 1, len(q.params), name)

    def test_every_read_item_is_available_on_the_base_classes(self):
        for name, item in HOST_ITEMS.items():
            self.assertTrue(hasattr(HostV1, name), name)
            if item.arg:
                self.assertTrue(callable(getattr(HostV1, name)), name)
        self.assertTrue(hasattr(HostV0, "action") and hasattr(HostV0, "notify"))
        self.assertFalse(hasattr(HostV0, "poll_events"))                     # v0 hosts only supply values
        self.assertTrue(hasattr(HostV1, "poll_events"))

    def test_the_event_table_matches_the_checker(self):
        from dal.parser import HANDLER_EVENTS

        self.assertEqual(set(EVENTS), set(HANDLER_EVENTS))

    def test_a_v0_host_still_runs_a_v1_program_without_events(self):
        """The base class defaults are neutral: a host that only supplies values loses events and commands, not correctness."""
        module = dal.check_source("logic T : actor;\nstate N: int;\non notify X { N += 1; }\nupdate(dt) { host.jump(); N += 1; }", "t.dal").module
        from dal.pybackend import load_class

        class Plain(HostV0):
            pass

        logic = load_class(module)(Plain())
        logic.update(0.1)
        logic.update(0.1)
        self.assertEqual(logic.N, 2)

    def test_default_hosts_ignore_everything(self):
        host = HostV1()
        self.assertEqual(host.poll_events(), [])
        self.assertFalse(host.trace_line((0, 0, 0), (1, 0, 0)).blocking)
        self.assertEqual(host.target_count(), 0)
        self.assertEqual(host.montage_section("M"), None)
        host.montage_play("M", 1.0, None)           # no error


class DocumentationTests(unittest.TestCase):
    def test_the_generated_protocol_tables_name_everything(self):
        from dal.hostapi import protocol_markdown

        text = protocol_markdown()
        for name in (*HOST_ITEMS, *COMMANDS, *QUERIES, *EVENTS):
            self.assertIn(f"`{name}", text, name)
        self.assertNotIn(chr(13), text)

    def test_cli_prints_the_protocol(self):
        import io
        from contextlib import redirect_stdout

        from dal.cli import main

        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["host"]), 0)
        self.assertIn("montage_set_next_section", buf.getvalue())


class MockHostTests(unittest.TestCase):
    def test_commands_are_recorded_with_plain_arguments_and_echoed_into_state(self):
        host = MockHost()
        host.set_max_walk_speed(250.0)
        host.add_movement_input(rt.Vec3(1.0, 0.0, 0.0), 0.5)
        host.set_movement_mode(rt.MovementMode.Walking)
        host.camera_set_arm_length(300.0)
        host.montage_play("Light1", 1.0, None)
        self.assertEqual(host.calls, [("set_max_walk_speed", (250.0,)), ("add_movement_input", ((1.0, 0.0, 0.0), 0.5)),
                                      ("set_movement_mode", (1,)), ("camera_set_arm_length", (300.0,)), ("montage_play", ("Light1", 1.0, None))])
        self.assertEqual(host.state["max_walk_speed"], 250.0)
        self.assertEqual(host.state["camera_arm_length"], 300.0)

    def test_traces_hit_the_ground_plane_and_spheres(self):
        host = MockHost()
        host.ground_z = 0.0
        hit = host.trace_line(rt.Vec3(0.0, 0.0, 100.0), rt.Vec3(0.0, 0.0, -100.0))
        self.assertTrue(hit.blocking)
        self.assertAlmostEqual(hit.distance, 100.0)
        self.assertAlmostEqual(hit.time, 0.5)
        self.assertEqual(tuple(hit.normal), (0.0, 0.0, 1.0))
        host.ground_z = None
        host.spheres = [((50.0, 0.0, 0.0), 10.0)]
        hit = host.trace_line(rt.Vec3(0.0, 0.0, 0.0), rt.Vec3(100.0, 0.0, 0.0))
        self.assertAlmostEqual(hit.distance, 40.0)
        self.assertEqual(tuple(hit.point), (40.0, 0.0, 0.0))
        miss = host.trace_line(rt.Vec3(0.0, 50.0, 0.0), rt.Vec3(100.0, 50.0, 0.0))
        self.assertFalse(miss.blocking)
        self.assertEqual(miss.time, 1.0)
        self.assertEqual(tuple(miss.point), (100.0, 50.0, 0.0))
        swept = host.trace_sphere(rt.Vec3(0.0, 15.0, 0.0), rt.Vec3(100.0, 15.0, 0.0), 10.0)
        self.assertTrue(swept.blocking)                          # a fat ray grazes the sphere a plain ray misses
        self.assertFalse(host.trace_line(rt.Vec3(0.0, 15.0, 0.0), rt.Vec3(100.0, 15.0, 0.0)).blocking)

    def test_targets_are_addressed_by_index_and_by_stable_id(self):
        host = MockHost()
        host.targets = [(7, (1.0, 2.0, 3.0)), (9, (4.0, 5.0, 6.0))]
        self.assertEqual(host.target_count(), 2)
        self.assertEqual(host.target_id(1), 9)
        self.assertEqual(tuple(host.target_location(0)), (1.0, 2.0, 3.0))
        self.assertEqual(host.target_index(9), 1)
        self.assertEqual(host.target_index(8), -1)
        self.assertEqual(host.target_id(5), -1)
        self.assertEqual(tuple(host.target_location(5)), (0.0, 0.0, 0.0))

    def test_events_are_delivered_once(self):
        host = MockHost()
        host.events.append(("notify", "Hit"))
        self.assertEqual(host.poll_events(), [("notify", "Hit")])
        self.assertEqual(host.poll_events(), [])


class SimHostTests(unittest.TestCase):
    def host(self):
        return SimHost([SimMontage("M", 1.0, {"A": 0.0, "B": 0.5}, notifies=[(0.25, "Hit")], windows=[(0.3, 0.6, "Win")], blend_out=0.2)])

    def run_for(self, host, seconds, dt=0.05):
        events = []
        for _ in range(round(seconds / dt)):
            host.advance(dt)
            events += host.poll_events()
        return events

    def test_a_montage_plays_to_its_end_with_notifies_in_time_order(self):
        host = self.host()
        host.montage_play("M", 1.0, None)
        self.assertTrue(host.montage_playing("M"))
        self.assertEqual(host.montage_section("M"), "A")
        events = self.run_for(host, 1.2)
        names = [e[:2] for e in events]
        self.assertEqual(names, [("notify", "Hit"), ("notify_begin", "Win"), ("notify_end", "Win"), ("montage_blending_out", "M"), ("montage_ended", "M")])
        self.assertFalse(events[-1][2])                          # ran to its end: not interrupted
        self.assertFalse(host.montage_playing("M"))

    def test_playing_another_montage_interrupts_the_first(self):
        host = self.host()
        host.montage_play("M", 1.0, None)
        self.run_for(host, 0.1)
        host.montage_play("M", 1.0, "B")
        self.assertEqual(host.montage_section("M"), "B")
        events = self.run_for(host, 0.3)
        self.assertIn(("montage_blending_out", "M", True), events)
        self.assertIn(("montage_ended", "M", True), events)

    def test_commands_that_change_the_simulation(self):
        host = self.host()
        host.montage_play("M", 2.0, None)
        self.run_for(host, 0.1)
        self.assertAlmostEqual(host.montage_position("M"), 0.2)
        host.montage_jump_to_section("M", "B")
        self.assertEqual(host.montage_section("M"), "B")
        host.montage_stop("M", 0.1)
        self.assertFalse(host.montage_playing("M"))
        self.assertTrue(host.montage_active("M"))                # blending out
        events = self.run_for(host, 0.2)
        self.assertIn(("montage_ended", "M", True), events)
        self.assertFalse(host.montage_active("M"))


if __name__ == "__main__":
    unittest.main()
