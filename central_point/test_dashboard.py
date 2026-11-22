"""Tests for the dashboard routing: hello, driver_state kept for the dashboard, chat_message forwarded.

Run: python -m unittest -v   (in this directory; needs only the server's requirements)
"""
import json
import unittest

import attention
import camera
import server


class DashboardRouteTest(unittest.TestCase):
    def setUp(self):
        camera.driver = camera.Driver()
        server.latest_driver_state = None
        server.watch = attention.Attention()

    def test_hello_makes_a_dashboard(self):
        self.assertEqual(server.route(json.dumps({"type": "hello", "role": "dashboard"})), ("dashboard", []))

    def test_driver_state_is_kept_as_received(self):
        raw = json.dumps({"type": "driver_state", "gaze": {"zone": "ROAD"}})
        self.assertEqual(server.route(raw), ("camera", []))
        self.assertIs(server.latest_driver_state, raw)
        self.assertEqual(camera.driver.zone, "ROAD")   # the gaze decision still gets it

    def test_chat_message_is_forwarded(self):
        raw = json.dumps({"type": "chat_message", "role": "assistant", "text": "Take a break soon."})
        self.assertEqual(server.route(raw), ("chat", [raw]))

    def test_bad_chat_message_is_not_forwarded(self):
        for msg in [{"type": "chat_message", "role": "system", "text": "x"},
                    {"type": "chat_message", "role": "user"},
                    {"type": "chat_message", "role": "user", "text": 5}]:
            self.assertEqual(server.route(json.dumps(msg)), ("chat", []))

    def test_simulator_and_junk_unchanged(self):
        raw = json.dumps({"event": "simulation_started", "timestamp": 1.0})
        self.assertEqual(server.route(raw), ("simulator", [raw]))   # the dashboard is told too
        self.assertEqual(server.route("not json"), (None, []))
        self.assertEqual(server.route("[1, 2]"), (None, []))
        self.assertEqual(server.route(json.dumps({"type": "hello", "role": "other"})), (None, []))

    def test_simulation_goes_to_the_dashboard_with_attention_events(self):
        server.route(json.dumps({"event": "simulation_started", "timestamp": 1.0}))
        tick = json.dumps({"event": "simulation_tick", "timestamp": 2.0,
                           "objects": [{"object_id": "car1", "type": "car", "x_px": 0, "y_px": 400}]})
        role, out = server.route(tick)
        self.assertEqual(role, "simulator")
        self.assertEqual([json.loads(t)["kind"] for t in out], ["appeared"])   # not the tick itself
        _, out = server.route(json.dumps({"event": "simulation_ended", "timestamp": 3.0}))
        self.assertEqual([json.loads(t)["event"] for t in out], ["attention_summary", "simulation_ended"])

    def test_bad_simulator_message_sends_nothing(self):
        bad = json.dumps({"event": "simulation_tick", "timestamp": 1.0, "objects": 5})
        self.assertEqual(server.route(bad), ("simulator", []))


if __name__ == "__main__":
    unittest.main()
