"""Tests for the dashboard routing: hello, driver_state kept for the dashboard, chat_message forwarded.

Run: python -m unittest -v   (in this directory; needs only the server's requirements)
"""
import json
import unittest

import camera
import server


class DashboardRouteTest(unittest.TestCase):
    def setUp(self):
        camera.driver = camera.Driver()
        server.latest_driver_state = None

    def test_hello_makes_a_dashboard(self):
        self.assertEqual(server.route(json.dumps({"type": "hello", "role": "dashboard"})), ("dashboard", None))

    def test_driver_state_is_kept_as_received(self):
        raw = json.dumps({"type": "driver_state", "gaze": {"zone": "ROAD"}})
        self.assertEqual(server.route(raw), ("camera", None))
        self.assertIs(server.latest_driver_state, raw)
        self.assertEqual(camera.driver.zone, "ROAD")   # the gaze decision still gets it

    def test_chat_message_is_forwarded(self):
        raw = json.dumps({"type": "chat_message", "role": "assistant", "text": "Take a break soon."})
        self.assertEqual(server.route(raw), ("chat", raw))

    def test_bad_chat_message_is_not_forwarded(self):
        for msg in [{"type": "chat_message", "role": "system", "text": "x"},
                    {"type": "chat_message", "role": "user"},
                    {"type": "chat_message", "role": "user", "text": 5}]:
            self.assertEqual(server.route(json.dumps(msg)), ("chat", None))

    def test_simulator_and_junk_unchanged(self):
        self.assertEqual(server.route(json.dumps({"event": "simulation_started", "timestamp": 1.0})),
                         ("simulator", None))
        self.assertEqual(server.route("not json"), (None, None))
        self.assertEqual(server.route("[1, 2]"), (None, None))
        self.assertEqual(server.route(json.dumps({"type": "hello", "role": "other"})), (None, None))


if __name__ == "__main__":
    unittest.main()
