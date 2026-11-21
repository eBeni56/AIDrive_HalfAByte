"""Input from the DRIVER CAMERA client (driver_monitor): everything in its driver_state message.

The camera client sends one large JSON ~30x/s (reference: driver_monitor/docs/detection-schema.md).

For the gaze region (gaze_update "status" / "region") we read:
  gaze.yaw_deg          0 = straight ahead, NEGATIVE = LEFT, positive = right (or null)
  gaze.pitch_deg        0 = straight, POSITIVE = UP, negative = down (or null)
  gaze.zone             ROAD, REAR_MIRROR, LEFT_MIRROR, RIGHT_MIRROR, LEFT_SIDE, RIGHT_SIDE, UP, DOWN,
                        UNKNOWN (the camera client decides this from its own mirror/road settings)
  eyes.state            OPEN, ALMOST_CLOSED, CLOSED, UNKNOWN
  quality.face_detected true / false
  calibration.status    CALIBRATING, DONE, DEFAULTS

For the driver's condition (gaze_update "driver", see driver_block()) we also read summary, attention,
drowsiness (level, reasons, indicators, 1 / 5 / 15 min history), eyes, head, mouth, quality, calibration and
every detection (tired / not paying attention / not visible). Detections are episodes with a stable id:
the active ones are re-sent in every message, an ENDED one exactly once. Ended ones are kept for
RECENT_ALERT_S so a one-shot event (HEAD_NOD is only ever sent as ENDED) is not lost between gaze_updates.

Any field may be missing, null or of the wrong type: that just means "no data", never an error.
"""
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
    "attention": ["state", "zone", "eyes_off_road_s", "glance_down_s", "off_road_last_30s_s",
                  "weighted_off_road_30s_s", "longest_glance_60s_s", "glances_60s"],
    "drowsiness": ["level", "level_index", "score", "peak_level", "warming_up", "fatigue_load"],
    "indicators": ["perclos", "perclos_window_s", "blink_rate_per_min", "mean_blink_duration_ms",
                   "long_blinks_60s", "mean_openness_60s", "mean_openness_ratio_60s", "yawns_5min",
                   "head_nods_5min", "head_falls_10min", "microsleeps_10min"],
    "history": ["long_eye_closures", "microsleeps", "closed_eyes_s", "yawns", "head_nods", "head_falls",
                "narrowed_eyes", "narrowed_eyes_s"],
    "eyes": ["state", "state_for_s", "openness", "openness_ratio", "normal_learned", "trend",
             "lid_lowered_by_gaze"],
    "gaze": ["zone", "zone_for_s", "direction", "direction_for_s", "yaw_deg", "pitch_deg", "on_road"],
    "head": ["yaw_deg", "pitch_deg", "roll_deg"],
    "mouth": ["state", "state_for_s"],
    "quality": ["face_detected", "face_lost_for_s", "eye_metrics_reliable", "issues", "fps"],
    "calibration": ["status", "phase", "progress", "message"],
}
HISTORY_NAMES = {60: "last_60s", 300: "last_5min", 900: "last_15min"}


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
        self.condition = {}       # cleaned blocks for driver_block(): attention, drowsiness, eyes, ...
        self.summary_severity = "NONE"
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
    """A detection -> the alert dict sent to the simulator, or None if it has no usable id / type."""
    if not isinstance(det, dict) or not isinstance(det.get("id"), str) or not isinstance(det.get("type"), str):
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

    drowsy = _dict(msg.get("drowsiness"))
    cond = {name: _pick(msg.get(name), name)
            for name in ["attention", "eyes", "gaze", "head", "mouth", "quality", "calibration"]}
    d = _pick(drowsy, "drowsiness")
    reasons = drowsy.get("contributing")
    d["reasons"] = [r for r in reasons if isinstance(r, str)] if isinstance(reasons, list) else []
    d["indicators"] = _pick(drowsy.get("indicators"), "indicators")
    history = {}
    for w in drowsy.get("history") if isinstance(drowsy.get("history"), list) else []:
        win = _angle(_dict(w).get("window_s"))
        if win is not None:
            history[HISTORY_NAMES.get(int(win), f"last_{int(win)}s")] = _pick(w, "history")
    d["history"] = history
    cond["drowsiness"] = d
    driver.condition = cond
    sev = _dict(msg.get("summary")).get("highest_severity")
    driver.summary_severity = sev if sev in alerts.SEVERITIES else "NONE"
    _detections(msg, now)
    log.debug("driver: yaw=%s pitch=%s zone=%s eyes=%s face=%s calib=%s alerts=%s", driver.yaw_deg,
              driver.pitch_deg, driver.zone, driver.eyes, driver.face_detected, driver.calibration,
              [a["type"] for a in driver.active])


def driver_block(now=None):
    """The "driver" object of gaze_update: the driver's condition from the latest driver_state.
    severity  highest severity of the active alerts (or the camera's summary): NONE/INFO/WARNING/DANGER/CRITICAL
    tired     a tiredness alert is active or the drowsiness level is above ALERT
    distracted  a DISTRACTED alert is active (eyes open but not on the road for too long)
    alerts    active detections, most severe first; recent: ended in the last RECENT_ALERT_S, newest first
    plus the cleaned attention / drowsiness / eyes / gaze / head / mouth / quality / calibration blocks."""
    now = time.monotonic() if now is None else now
    act = driver.active
    rank = max([alerts.severity_rank(a["severity"]) for a in act] + [alerts.severity_rank(driver.summary_severity)])
    cond = driver.condition
    level = _dict(cond.get("drowsiness")).get("level_index")
    att = _dict(cond.get("attention")).get("state")
    recent = sorted(((t, a) for t, a in driver.recent.values() if now - t <= RECENT_ALERT_S),
                    key=lambda x: -x[0])
    return {
        "severity": alerts.SEVERITIES[rank - 1] if rank else "NONE",
        "tired": any(a["category"] == "tired" for a in act) or (isinstance(level, int) and level >= 1),
        "distracted": any(a["type"] == "DISTRACTED" for a in act) or att == "DISTRACTED",
        "alerts": act,
        "recent": [{**a, "ended_ago_s": round(now - t, 2)} for t, a in recent],
        **cond,
    }
