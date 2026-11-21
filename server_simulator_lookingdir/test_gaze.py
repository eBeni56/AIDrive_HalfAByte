"""Tests for gaze_update: camera driver_state -> status, region and the driver block (the three detections).

Run: python -m unittest -v   (in this directory; needs only the server's requirements)
"""
import json
import time
import unittest

import alerts
import camera
import server


def driver_state(zone="ROAD", yaw=0.0, eyes="OPEN", face=True, calib="DONE"):
    return {"type": "driver_state", "gaze": {"yaw_deg": yaw, "pitch_deg": 0.0, "zone": zone},
            "eyes": {"state": eyes}, "quality": {"face_detected": face},
            "calibration": {"status": calib}}


class RegionTest(unittest.TestCase):
    def test_mirrors_and_side_windows(self):
        for zone, region in [("REAR_MIRROR", "mirror_center"), ("LEFT_MIRROR", "mirror_left"),
                             ("RIGHT_MIRROR", "mirror_right"), ("LEFT_SIDE", "side_left"),
                             ("RIGHT_SIDE", "side_right")]:
            self.assertEqual(camera.region(zone, 0.0, 7), region)

    def test_road_is_split_by_yaw(self):
        self.assertEqual(camera.region("ROAD", -15.0, 7), "front_left")
        self.assertEqual(camera.region("ROAD", -7.0, 7), "front_center")
        self.assertEqual(camera.region("ROAD", 3.0, 7), "front_center")
        self.assertEqual(camera.region("ROAD", 12.0, 7), "front_right")
        self.assertEqual(camera.region("ROAD", None, 7), "front_center")

    def test_everything_else_is_away(self):
        for zone in ["UP", "DOWN", "UNKNOWN", None, "SOMETHING_NEW", 5]:
            self.assertEqual(camera.region(zone, 0.0, 7), "away")


class DecideGazeTest(unittest.TestCase):
    def setUp(self):
        camera.driver = camera.Driver()   # fresh state: no camera has reported

    def decide(self, msg=None, age_s=0.0):
        if msg is not None:
            camera.handle(msg)
        return server.decide_gaze(time.monotonic() + age_s)

    def test_no_camera_client(self):
        self.assertEqual(self.decide(), ("no_gaze_client", None))

    def test_camera_data_too_old(self):
        self.assertEqual(self.decide(driver_state(), age_s=server.CAMERA_TIMEOUT_S + 0.5),
                         ("no_gaze_client", None))

    def test_region_from_camera(self):
        self.assertEqual(self.decide(driver_state("LEFT_MIRROR", -25.0)), ("ok", "mirror_left"))
        self.assertEqual(self.decide(driver_state("ROAD", 14.0)), ("ok", "front_right"))
        self.assertEqual(self.decide(driver_state("DOWN", 0.0)), ("ok", "away"))

    def test_statuses_win_over_the_region(self):
        self.assertEqual(self.decide(driver_state(face=False)), ("no_face", None))
        self.assertEqual(self.decide(driver_state(eyes="CLOSED")), ("eyes_closed", None))
        self.assertEqual(self.decide(driver_state(calib="CALIBRATING")), ("calibrating", None))
        self.assertEqual(self.decide(driver_state(eyes="ALMOST_CLOSED")), ("ok", "front_center"))

    def test_missing_fields_are_no_data(self):
        self.assertEqual(self.decide({"type": "driver_state"}), ("ok", "away"))
        self.assertEqual(self.decide({"type": "driver_state", "gaze": {"zone": ["ROAD"]}}),
                         ("ok", "away"))


def detection(det_id, det_type, severity="WARNING", status="ONGOING", duration=1.0, **details):
    return {"id": det_id, "type": det_type, "severity": severity, "status": status, "started_t": 10.0,
            "ended_t": None, "duration_s": duration, "details": details}


def full_state(detections=(), zone="ROAD", eyes="OPEN", severity="NONE"):
    msg = driver_state(zone, eyes=eyes)
    msg.update({
        "schema_version": 2,
        "summary": {"highest_severity": severity, "active": [d["type"] for d in detections]},
        "detections": list(detections),
    })
    return msg


class DriverBlockTest(unittest.TestCase):
    """The "driver" object of gaze_update: eyes closed / not paying attention, nothing else."""
    def setUp(self):
        camera.driver = camera.Driver()

    def send(self, msg, age_s=0.0):
        camera.handle(msg)
        out, _ = server.gaze_message(time.monotonic() + age_s)
        json.dumps(out, allow_nan=False)   # must always be valid JSON
        return out

    def test_no_camera_means_no_driver_block(self):
        out, _ = server.gaze_message()
        self.assertEqual((out["status"], out["driver"]), ("no_gaze_client", None))

    def test_attentive_driver(self):
        d = self.send(full_state())["driver"]
        self.assertEqual((d["severity"], d["eyes_closed"], d["not_paying_attention"], d["alerts"]),
                         ("NONE", False, False, []))
        self.assertEqual(d["gaze"]["zone"], "ROAD")
        self.assertNotIn("drowsiness", d)
        self.assertNotIn("mouth", d)

    def test_looking_down_is_not_paying_attention(self):
        det = detection("distracted-1", "DISTRACTED", zone="DOWN", away_s=1.5)
        out = self.send(full_state([det], zone="DOWN", severity="WARNING"))
        self.assertEqual((out["status"], out["region"]), ("ok", "away"))
        d = out["driver"]
        self.assertTrue(d["not_paying_attention"])
        self.assertFalse(d["eyes_closed"])
        self.assertEqual(d["severity"], "WARNING")
        self.assertEqual(d["alerts"][0]["category"], "not_paying_attention")
        self.assertEqual(d["alerts"][0]["text"], "Not paying attention - looking down for 1.5 s")

    def test_phone_is_not_paying_attention(self):
        det = detection("phone_use-1", "PHONE_USE", "DANGER", seen_for_s=3.2)
        d = self.send(full_state([det]))["driver"]
        self.assertTrue(d["not_paying_attention"])
        self.assertEqual(d["alerts"][0]["text"], "Not paying attention - phone in hand for 3.2 s")

    def test_looking_up_text(self):
        up = detection("distracted-2", "DISTRACTED", zone="UP", away_s=3.2)
        self.assertEqual(self.send(full_state([up]))["driver"]["alerts"][0]["text"],
                         "Not paying attention - looking up for 3.2 s")

    def test_eyes_closed_is_danger_and_sorted_first(self):
        dets = [detection("distracted-1", "DISTRACTED", "WARNING", zone="UP"),
                detection("eyes_closed-1", "EYES_CLOSED", "DANGER", closed_for_s=1.23)]
        out = self.send(full_state(dets, eyes="CLOSED", severity="DANGER"))
        d = out["driver"]
        self.assertEqual(out["status"], "eyes_closed")
        self.assertEqual(d["severity"], "DANGER")
        self.assertTrue(d["eyes_closed"])
        self.assertEqual([a["type"] for a in d["alerts"]], ["EYES_CLOSED", "DISTRACTED"])
        self.assertEqual(d["alerts"][0]["text"], "Eyes closed for 1.2 s - open your eyes!")

    def test_tiredness_detections_are_dropped(self):
        dets = [detection("yawn-1", "YAWN", "INFO"), detection("drowsy-1", "DROWSY", "WARNING"),
                detection("microsleep-1", "MICROSLEEP", "CRITICAL"), detection("head_nod-1", "HEAD_NOD", "DANGER", "ENDED")]
        msg = full_state(dets, severity="CRITICAL")
        msg["drowsiness"] = {"level": "DROWSY", "level_index": 2}
        msg["mouth"] = {"state": "WIDE_OPEN"}
        d = self.send(msg)["driver"]
        self.assertEqual((d["severity"], d["alerts"], d["recent"]), ("NONE", [], []))
        self.assertNotIn("drowsiness", d)
        clean = json.loads(camera.dashboard_state(json.dumps(msg)))
        self.assertEqual(clean["detections"], [])
        self.assertEqual(clean["summary"], {"highest_severity": "NONE", "active": []})
        self.assertNotIn("drowsiness", clean)
        self.assertNotIn("mouth", clean)

    def test_dashboard_state_keeps_the_three(self):
        dets = [detection("phone_use-1", "PHONE_USE", "DANGER"), detection("yawn-1", "YAWN", "INFO")]
        clean = json.loads(camera.dashboard_state(json.dumps(full_state(dets))))
        self.assertEqual([d["type"] for d in clean["detections"]], ["PHONE_USE"])
        self.assertEqual(clean["summary"]["highest_severity"], "DANGER")
        self.assertIsNone(camera.dashboard_state("not json"))

    def test_ended_alert_goes_to_recent(self):
        self.send(full_state([detection("eyes_closed-1", "EYES_CLOSED", "WARNING")]))
        d = self.send(full_state([detection("eyes_closed-1", "EYES_CLOSED", "WARNING", "ENDED")]))["driver"]
        self.assertEqual(d["alerts"], [])
        self.assertEqual(d["recent"][0]["id"], "eyes_closed-1")

    def test_recent_expires(self):
        camera.handle(full_state([detection("eyes_closed-1", "EYES_CLOSED", "WARNING", "ENDED")]))
        (_, alert), = camera.driver.recent.values()
        camera.driver.recent["eyes_closed-1"] = (time.monotonic() - camera.RECENT_ALERT_S - 1, alert)
        self.assertEqual(self.send(full_state())["driver"]["recent"], [])

    def test_garbage_never_breaks(self):
        for bad in [{"type": "driver_state", "detections": "nope"},
                    {"type": "driver_state", "detections": [1, None, {"id": 5}, {"type": "X"}]},
                    {"type": "driver_state", "gaze": {"yaw_deg": float("nan")},
                     "detections": [{"id": "a", "type": "DISTRACTED", "severity": "HUGE",
                                     "details": {"x": float("inf"), "deep": [[[[[1]]]]]}}]}]:
            d = self.send(bad)["driver"]
            self.assertIsInstance(d, dict)
        self.assertEqual(d["alerts"][0]["category"], "not_paying_attention")
        self.assertEqual(d["alerts"][0]["severity"], "INFO")

    def test_alert_texts(self):
        for t in ["EYES_CLOSED", "DISTRACTED", "PHONE_USE"]:
            self.assertTrue(alerts.text(t, {}))
            self.assertTrue(alerts.text(t, {"reasons": "x", "closed_for_s": True}))
        self.assertEqual(alerts.text("YAWN", {}), "")
        self.assertIsNone(alerts.category("DROWSY"))


if __name__ == "__main__":
    unittest.main()
