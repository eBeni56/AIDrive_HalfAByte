"""Input from the DRIVER CAMERA client (driver_monitor): everything in its driver_state message.

The camera client sends one large JSON ~30x/s (reference: driver_monitor/docs/detection-schema.md).

For the gaze region (gaze_update "status" / "region") we read:
  gaze.yaw_deg          0 = straight ahead, NEGATIVE = LEFT, positive = right (or null)
  gaze.pitch_deg        0 = straight, POSITIVE = UP, negative = down (or null)
  gaze.zone             ROAD, REAR_MIRROR, LEFT_MIRROR, RIGHT_MIRROR, LEFT_SIDE, RIGHT_SIDE, UP, DOWN,
                        UNKNOWN (the camera client decides this from its own mirror/road settings)
  eyes.state            OPEN, CLOSED, UNKNOWN
  quality.face_detected true / false
  calibration.status    CALIBRATING, DONE, DEFAULTS

For the driver's condition (gaze_update "driver", see driver_block()) we also read eyes, head, gaze,
quality, calibration and the detections - ONLY the three the server handles (alerts.py):
EYES_CLOSED (eyes fully closed), DISTRACTED and PHONE_USE (not paying attention). Any other detection
type, and any drowsiness / mouth data an older camera client may still send, is dropped right here.
Detections are episodes with a stable id: the active ones are re-sent in every message, an ENDED one
exactly once. Ended ones are kept for RECENT_ALERT_S so a short one is not lost between gaze_updates.

dashboard_state() is the same cleaning for the dashboard: the driver_state it gets has only those three
detections.

Any field may be missing, null or of the wrong type: that just means "no data", never an error.
"""
import json
import logging
import math
import os
import time

import alerts

log = logging.getLogger("camera")

RECENT_ALERT_S = float(os.environ.get("RECENT_ALERT_S", "5"))   # how long an ENDED detection stays in "recent"

# gaze.zone -> simulator region. ROAD is split into front_left / front_center / front_right by yaw.
ZONE_TO_REGION = {
    "REAR_MIRROR": "mirror_center",
    "LEFT_MIRROR": "mirror_left",
    "RIGHT_MIRROR": "mirror_right",
    "LEFT_SIDE": "side_left",
    "RIGHT_SIDE": "side_right",
}

# Fields passed on to the simulator, per driver_state block (the rest is camera internals).
FIELDS = {
    "eyes": ["state", "state_for_s", "openness", "source"],
    "gaze": ["zone", "zone_for_s", "yaw_deg", "pitch_deg"],
    "head": ["yaw_deg", "pitch_deg", "roll_deg"],
    "quality": ["face_detected", "faces_in_view", "issues", "fps"],
    "calibration": ["status", "phase", "progress", "message"],
}
# top-level blocks of driver_state passed on to the dashboard (anything else, e.g. "drowsiness", is dropped)
DASHBOARD_KEYS = ["type", "schema_version", "t", "frame", "summary", "quality", "calibration", "eyes", "head",
                  "gaze", "detections"]


class Driver:
    """What the camera client last reported."""
    def __init__(self):
        self.yaw_deg = None
        self.pitch_deg = None
        self.zone = None
        self.eyes = None
        self.face_detected = None
        self.calibration = None
        self.received_at = None   # time.monotonic() at arrival (the message's own "t" is camera uptime)
        self.condition = {}       # cleaned blocks for driver_block(): eyes, gaze, head, quality, calibration
        self.active = []          # active detections of the latest message, as alert dicts
        self.recent = {}          # id -> (monotonic time it ended, alert dict)


driver = Driver()


def _dict(v):
    return v if isinstance(v, dict) else {}


def _angle(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _clean(v, depth=0):
    """A JSON-safe copy: finite numbers (floats rounded), short strings, bools, None, and lists / dicts of
    those. NaN / inf become None; anything else (or nested too deep) is dropped as None."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return round(v, 3) if math.isfinite(v) else None
    if isinstance(v, str):
        return v[:200]
    if depth >= 4:
        return None
    if isinstance(v, list):
        return [_clean(x, depth + 1) for x in v[:50]]
    if isinstance(v, dict):
        return {k: _clean(x, depth + 1) for k, x in list(v.items())[:60] if isinstance(k, str)}
    return None


def _pick(block, name):
    block = _dict(block)
    return {f: _clean(block.get(f)) for f in FIELDS[name]}


def region(zone, yaw_deg, front_split_deg):
    """Simulator region for a camera gaze zone. Looking at the road: front_left / front_center /
    front_right, split at -front_split_deg / +front_split_deg of yaw (front_center if yaw is unknown).
    UP, DOWN, UNKNOWN and anything unexpected: "away"."""
    if zone == "ROAD":
        if yaw_deg is None or abs(yaw_deg) <= front_split_deg:
            return "front_center"
        return "front_left" if yaw_deg < 0 else "front_right"
    return ZONE_TO_REGION.get(zone, "away")


def _alert(det):
    """A detection -> the alert dict sent to the simulator, or None if it has no usable id / type or is a
    type the server does not handle (only EYES_CLOSED, DISTRACTED, PHONE_USE)."""
    if not isinstance(det, dict) or not isinstance(det.get("id"), str) or det.get("type") not in alerts.HANDLED:
        return None
    t = det["type"]
    sev = det.get("severity") if det.get("severity") in alerts.SEVERITIES else "INFO"
    details = _clean(_dict(det.get("details")))
    duration = _angle(det.get("duration_s"))
    return {"id": det["id"][:80], "type": t[:40], "category": alerts.category(t), "severity": sev,
            "status": det.get("status") if isinstance(det.get("status"), str) else None,
            "duration_s": round(duration, 2) if duration is not None else None,
            "text": alerts.text(t, details), "details": details}


def _sort_key(a):
    return -alerts.severity_rank(a["severity"])   # stable sort: equal severity keeps the camera's order


def _detections(msg, now):
    """Update driver.active / driver.recent from the message's detections and log every change."""
    raw = msg.get("detections")
    dets = [a for a in (_alert(d) for d in raw) if a] if isinstance(raw, list) else []
    before = {a["id"]: a for a in driver.active}
    active = []
    for a in dets:
        if a["status"] == "ENDED":
            driver.recent[a["id"]] = (now, a)
            log.info("ALERT ENDED   %s %s after %ss", a["id"], a["type"], a["duration_s"])
            continue
        active.append(a)
        old = before.get(a["id"])
        if old is None:
            log.info("ALERT STARTED %s %s %s: %s", a["id"], a["type"], a["severity"], a["text"])
        elif old["severity"] != a["severity"]:
            log.info("ALERT CHANGED %s %s %s -> %s: %s", a["id"], a["type"], old["severity"], a["severity"],
                     a["text"])
    ended_ids = {a["id"] for a in dets if a["status"] == "ENDED"}
    for gone in before.keys() - {a["id"] for a in active} - ended_ids:   # vanished without an ENDED
        log.info("ALERT GONE    %s %s", gone, before[gone]["type"])
    active.sort(key=_sort_key)
    driver.active = active
    for k in [k for k, (t, _) in driver.recent.items() if now - t > RECENT_ALERT_S]:
        del driver.recent[k]


def handle(msg):
    """Handle one driver_state message (already a dict). Never raises."""
    now = time.monotonic()
    gaze = _dict(msg.get("gaze"))
    driver.yaw_deg = _angle(gaze.get("yaw_deg"))
    driver.pitch_deg = _angle(gaze.get("pitch_deg"))
    zone = gaze.get("zone")
    driver.zone = zone if isinstance(zone, str) else None
    driver.eyes = _dict(msg.get("eyes")).get("state")
    driver.face_detected = _dict(msg.get("quality")).get("face_detected")
    driver.calibration = _dict(msg.get("calibration")).get("status")
    driver.received_at = now

    driver.condition = {name: _pick(msg.get(name), name) for name in FIELDS}
    _detections(msg, now)
    log.debug("driver: yaw=%s pitch=%s zone=%s eyes=%s face=%s calib=%s alerts=%s", driver.yaw_deg,
              driver.pitch_deg, driver.zone, driver.eyes, driver.face_detected, driver.calibration,
              [a["type"] for a in driver.active])


def driver_block(now=None):
    """The "driver" object of gaze_update: the driver's condition from the latest driver_state.
    severity              highest severity of the active alerts: NONE/INFO/WARNING/DANGER/CRITICAL
    eyes_closed           an EYES_CLOSED alert is active (eyes fully closed longer than a blink)
    not_paying_attention  a DISTRACTED or PHONE_USE alert is active
    alerts    active detections (only the three above), most severe first; recent: ended in the last
              RECENT_ALERT_S, newest first
    plus the cleaned eyes / gaze / head / quality / calibration blocks."""
    now = time.monotonic() if now is None else now
    act = driver.active
    rank = max([alerts.severity_rank(a["severity"]) for a in act], default=0)
    recent = sorted(((t, a) for t, a in driver.recent.values() if now - t <= RECENT_ALERT_S),
                    key=lambda x: -x[0])
    return {
        "severity": alerts.SEVERITIES[rank - 1] if rank else "NONE",
        "eyes_closed": any(a["category"] == "eyes_closed" for a in act),
        "not_paying_attention": any(a["category"] == "not_paying_attention" for a in act),
        "alerts": act,
        "recent": [{**a, "ended_ago_s": round(now - t, 2)} for t, a in recent],
        **driver.condition,
    }


def dashboard_state(raw):
    """The driver_state text for the dashboard: the camera's message with only the three handled detections
    (summary recomputed from them) and without any other block (drowsiness, mouth, ...). None if `raw` is
    not a JSON object."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict):
        return None
    out = {k: msg[k] for k in DASHBOARD_KEYS if k in msg}
    dets = msg.get("detections")
    dets = [d for d in dets if isinstance(d, dict) and d.get("type") in alerts.HANDLED] if isinstance(dets, list) else []
    out["detections"] = dets
    active = [d for d in dets if d.get("status") != "ENDED"]
    top = max(active, key=lambda d: alerts.severity_rank(d.get("severity")), default=None)
    rank = alerts.severity_rank(top.get("severity")) if top else 0
    out["summary"] = {"highest_severity": alerts.SEVERITIES[rank - 1] if rank else "NONE",
                      "active": [d.get("type") for d in active]}
    return json.dumps(out)
