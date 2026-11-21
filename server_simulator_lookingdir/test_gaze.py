"""Tests for the gaze decision: camera driver_state -> gaze_update status and region.

Run: python -m unittest -v   (in this directory; needs only the server's requirements)
"""
import time
import unittest

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


if __name__ == "__main__":
    unittest.main()
