"""Tests for gaze_update: camera driver_state -> status, region and the driver block (alerts, drowsiness).

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


def full_state(detections=(), zone="ROAD", eyes="OPEN", level="ALERT", level_index=0, attention="ATTENTIVE",
               reasons=(), severity="NONE"):
    msg = driver_state(zone, eyes=eyes)
    msg.update({
        "summary": {"highest_severity": severity, "primary": None, "active": [d["type"] for d in detections]},
        "attention": {"state": attention, "zone": zone, "eyes_off_road_s": 0.0, "glance_down_s": 0.0,
                      "off_road_last_30s_s": 0.0, "weighted_off_road_30s_s": 0.0,
                      "longest_glance_60s_s": 0.0, "glances_60s": 0},
        "drowsiness": {"level": level, "level_index": level_index, "score": 0.1, "peak_level": level,
                       "warming_up": False, "contributing": list(reasons), "fatigue_load": 0.5,
                       "history": [{"window_s": 60.0, "long_eye_closures": 3, "microsleeps": 1,
                                    "closed_eyes_s": 4.2, "yawns": 2, "head_nods": 0, "head_falls": 0,
                                    "narrowed_eyes": 0, "narrowed_eyes_s": 0.0},
                                   {"window_s": 300.0, "long_eye_closures": 5}],
                       "indicators": {"perclos": 0.14, "yawns_5min": 2, "microsleeps_10min": 1}},
        "detections": list(detections),
    })
    return msg


class DriverBlockTest(unittest.TestCase):
    """The "driver" object of gaze_update: tired / not paying attention / everything else."""
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
        self.assertEqual((d["severity"], d["tired"], d["distracted"], d["alerts"]), ("NONE", False, False, []))
        self.assertEqual(d["drowsiness"]["history"]["last_60s"]["long_eye_closures"], 3)
        self.assertEqual(d["drowsiness"]["history"]["last_5min"]["long_eye_closures"], 5)
        self.assertEqual(d["drowsiness"]["indicators"]["perclos"], 0.14)

    def test_looking_down_is_not_paying_attention(self):
        det = detection("distracted-1", "DISTRACTED", reason="LOOKING_DOWN", zone="DOWN", glance_s=1.5,
                        glance_down_s=1.5)
        out = self.send(full_state([det], zone="DOWN", attention="DISTRACTED", severity="WARNING"))
        self.assertEqual((out["status"], out["region"]), ("ok", "away"))
        d = out["driver"]
        self.assertTrue(d["distracted"])
        self.assertFalse(d["tired"])
        self.assertEqual(d["severity"], "WARNING")
        self.assertEqual(d["alerts"][0]["category"], "distracted")
        self.assertEqual(d["alerts"][0]["text"], "Not paying attention - looking down for 1.5 s")

    def test_long_glance_and_cumulative_texts(self):
        up = detection("distracted-2", "DISTRACTED", reason="LONG_GLANCE", zone="UP", glance_s=3.2)
        self.assertEqual(self.send(full_state([up]))["driver"]["alerts"][0]["text"],
                         "Not paying attention - looking up for 3.2 s")
        cum = detection("distracted-3", "DISTRACTED", reason="CUMULATIVE", weighted_off_road_30s_s=10.4)
        self.assertEqual(self.send(full_state([cum]))["driver"]["alerts"][0]["text"],
                         "Not paying attention - eyes off the road 10 s in the last 30 s")

    def test_almost_closed_eyes_are_tired(self):
        det = detection("eyes_almost_closed-1", "EYES_ALMOST_CLOSED", openness=0.4)
        out = self.send(full_state([det], eyes="ALMOST_CLOSED"))
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["driver"]["tired"])
        self.assertEqual(out["driver"]["alerts"][0]["text"], "Eyes almost closed - you look tired")

    def test_drowsiness_level_alone_is_tired(self):
        d = self.send(full_state(level="DROWSY", level_index=2, reasons=["HIGH_PERCLOS"]))["driver"]
        self.assertTrue(d["tired"])
        self.assertEqual(d["drowsiness"]["reasons"], ["HIGH_PERCLOS"])

    def test_microsleep_is_critical_and_sorted_first(self):
        dets = [detection("yawn-1", "YAWN", "INFO"),
                detection("microsleep-1", "MICROSLEEP", "CRITICAL", "STARTED", closed_for_s=1.03),
                detection("eyes_closed-1", "EYES_CLOSED", "WARNING", "ENDED", outcome="ESCALATED_TO_MICROSLEEP")]
        out = self.send(full_state(dets, eyes="CLOSED", severity="CRITICAL"))
        d = out["driver"]
        self.assertEqual(out["status"], "eyes_closed")
        self.assertEqual(d["severity"], "CRITICAL")
        self.assertEqual([a["type"] for a in d["alerts"]], ["MICROSLEEP", "YAWN"])
        self.assertEqual(d["alerts"][0]["text"], "MICROSLEEP - WAKE UP! Eyes closed 1.0 s")
        self.assertEqual([a["type"] for a in d["recent"]], ["EYES_CLOSED"])

    def test_one_shot_head_nod_stays_in_recent(self):
        self.send(full_state([detection("head_nod-1", "HEAD_NOD", "DANGER", "ENDED")]))
        d = self.send(full_state())["driver"]   # the next message no longer has it
        self.assertEqual([a["type"] for a in d["recent"]], ["HEAD_NOD"])
        self.assertEqual(d["recent"][0]["text"], "Head nodding off - wake up!")
        self.assertEqual(d["alerts"], [])

    def test_recent_expires(self):
        camera.handle(full_state([detection("yawn-1", "YAWN", "INFO", "ENDED")]))
        (_, alert), = camera.driver.recent.values()
        camera.driver.recent["yawn-1"] = (time.monotonic() - camera.RECENT_ALERT_S - 1, alert)
        self.assertEqual(self.send(full_state())["driver"]["recent"], [])

    def test_ended_alert_leaves_active(self):
        self.send(full_state([detection("drowsy-1", "DROWSY", "WARNING", level="DROWSY")]))
        d = self.send(full_state([detection("drowsy-1", "DROWSY", "WARNING", "ENDED", level="DROWSY")]))["driver"]
        self.assertEqual(d["alerts"], [])
        self.assertEqual(d["recent"][0]["id"], "drowsy-1")

    def test_garbage_never_breaks(self):
        for bad in [{"type": "driver_state", "detections": "nope"},
                    {"type": "driver_state", "detections": [1, None, {"id": 5}, {"type": "X"}]},
                    {"type": "driver_state", "drowsiness": {"history": [None, {"window_s": "x"}], "score": float("nan")},
                     "detections": [{"id": "a", "type": "NEW_THING", "severity": "HUGE",
                                     "details": {"x": float("inf"), "deep": [[[[[1]]]]]}}]}]:
            d = self.send(bad)["driver"]
            self.assertIsInstance(d, dict)
        self.assertEqual(d["alerts"][0]["category"], "other")
        self.assertEqual(d["alerts"][0]["severity"], "INFO")
        self.assertEqual(d["alerts"][0]["text"], "New thing")

    def test_alert_texts_for_every_type(self):
        for t in ["EYES_CLOSED", "MICROSLEEP", "EYES_ALMOST_CLOSED", "EYES_NARROWED", "TIRED_EYES", "YAWN",
                  "HEAD_NOD", "HEAD_FALLING", "DISTRACTED", "FACE_NOT_VISIBLE", "DROWSY"]:
            self.assertTrue(alerts.text(t, {}))
            self.assertTrue(alerts.text(t, {"reasons": "x", "closed_for_s": True}))
        self.assertEqual(alerts.text("TIRED_EYES", {"reasons": ["HEAVY_EYELIDS", "SLOW_BLINKS"]}),
                         "Tired eyes: heavy eyelids, slow blinks")
        self.assertEqual(alerts.text("FACE_NOT_VISIBLE", {"likely_cause": "HEAD_DROPPED"}),
                         "Driver not visible - head dropped")
        self.assertEqual(alerts.text("DROWSY", {"level": "SEVERELY_DROWSY"}), "Severely drowsy - stop and rest")


if __name__ == "__main__":
    unittest.main()
