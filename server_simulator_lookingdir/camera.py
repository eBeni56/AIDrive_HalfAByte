"""Input from the DRIVER CAMERA client: pick out the fields we need from driver_state.

The camera client sends one large JSON ~30x/s. We only read:
  gaze.yaw_deg          0 = straight ahead, NEGATIVE = LEFT, positive = right (or null)
  gaze.pitch_deg        0 = straight, POSITIVE = UP, negative = down (or null)
  eyes.state            OPEN, ALMOST_CLOSED, CLOSED, UNKNOWN
  quality.face_detected true / false
  calibration.status    CALIBRATING, DONE, DEFAULTS
Any field may be missing or null: that just means "no data", never an error.
"""
import logging
import math
import time

log = logging.getLogger("camera")


class Driver:
    """What the camera client last reported."""
    yaw_deg = None
    pitch_deg = None
    eyes = None
    face_detected = None
    calibration = None
    received_at = None   # time.monotonic() at arrival (the message's own "t" is camera uptime)


driver = Driver()


def _dict(v):
    return v if isinstance(v, dict) else {}


def _angle(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def handle(msg):
    """Handle one driver_state message (already a dict). Never raises."""
    gaze = _dict(msg.get("gaze"))
    driver.yaw_deg = _angle(gaze.get("yaw_deg"))
    driver.pitch_deg = _angle(gaze.get("pitch_deg"))
    driver.eyes = _dict(msg.get("eyes")).get("state")
    driver.face_detected = _dict(msg.get("quality")).get("face_detected")
    driver.calibration = _dict(msg.get("calibration")).get("status")
    driver.received_at = time.monotonic()
    log.debug("driver: yaw=%s pitch=%s eyes=%s face=%s calib=%s", driver.yaw_deg,
              driver.pitch_deg, driver.eyes, driver.face_detected, driver.calibration)
