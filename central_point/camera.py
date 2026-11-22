"""Input from the DRIVER CAMERA client: pick out the fields we need from driver_state.

The camera client sends one large JSON ~30x/s. We only read:
  gaze.yaw_deg          0 = straight ahead, NEGATIVE = LEFT, positive = right (or null): logged only
  gaze.zone             ROAD, REAR_MIRROR, LEFT_MIRROR, RIGHT_MIRROR, LEFT_SIDE, RIGHT_SIDE, UP, DOWN,
                        UNKNOWN (the camera client decides this from its own mirror/road settings)
  eyes.state            OPEN, CLOSED, UNKNOWN
  quality.face_detected true / false
  calibration.status    CALIBRATING, DONE, DEFAULTS
Any field may be missing or null: that just means "no data", never an error.
"""
import logging
import math
import time

log = logging.getLogger("camera")

# gaze.zone -> simulator region. ROAD is the whole windshield ("front"): looking ahead sees everything ahead.
ZONE_TO_REGION = {
    "ROAD": "front",
    "REAR_MIRROR": "mirror_center",
    "LEFT_MIRROR": "mirror_left",
    "RIGHT_MIRROR": "mirror_right",
    "LEFT_SIDE": "side_left",
    "RIGHT_SIDE": "side_right",
}


class Driver:
    """What the camera client last reported."""

    def __init__(self):
        self.yaw_deg = None
        self.zone = None
        self.eyes = None
        self.face_detected = None
        self.calibration = None
        self.received_at = None   # time.monotonic() at arrival (the message's own "t" is camera uptime)


driver = Driver()


def _dict(v):
    return v if isinstance(v, dict) else {}


def _angle(v):
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        return None
    return float(v)


def region(zone):
    """Simulator region for a camera gaze zone: ROAD -> front (the whole windshield), the mirrors and side
    windows by name. UP, DOWN, UNKNOWN and anything unexpected: "away"."""
    return ZONE_TO_REGION.get(zone, "away")


def handle(msg):
    """Handle one driver_state message (already a dict). Never raises."""
    gaze = _dict(msg.get("gaze"))
    driver.yaw_deg = _angle(gaze.get("yaw_deg"))
    zone = gaze.get("zone")
    driver.zone = zone if isinstance(zone, str) else None
    driver.eyes = _dict(msg.get("eyes")).get("state")
    driver.face_detected = _dict(msg.get("quality")).get("face_detected")
    driver.calibration = _dict(msg.get("calibration")).get("status")
    driver.received_at = time.monotonic()
    log.debug("driver: yaw=%s zone=%s eyes=%s face=%s calib=%s", driver.yaw_deg, driver.zone,
              driver.eyes, driver.face_detected, driver.calibration)
