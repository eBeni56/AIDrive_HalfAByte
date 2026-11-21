"""Detections from the camera client -> short alert texts and categories for the simulator.

The ONE place that words the alerts. driver_monitor decides WHEN something is a problem (its timing rules);
this file only says what to call it. Every function takes the detection's `details` (a dict, any field may
be missing) and never raises.
"""

SEVERITIES = ["INFO", "WARNING", "DANGER", "CRITICAL"]   # low -> high; "NONE" = nothing active


def severity_rank(s):
    """0 = NONE / unknown, 1 = INFO ... 4 = CRITICAL."""
    return SEVERITIES.index(s) + 1 if s in SEVERITIES else 0


# Category of each detection type: is the driver tired, not paying attention, or not visible at all?
TIRED = {"EYES_CLOSED", "MICROSLEEP", "EYES_ALMOST_CLOSED", "EYES_NARROWED", "TIRED_EYES", "YAWN",
         "HEAD_NOD", "HEAD_FALLING", "DROWSY"}
CATEGORY = {**{t: "tired" for t in TIRED}, "DISTRACTED": "distracted", "FACE_NOT_VISIBLE": "not_visible"}


def category(det_type):
    """"tired", "distracted", "not_visible" or "other" (a type this server does not know yet)."""
    return CATEGORY.get(det_type, "other")


def _num(d, key):
    v = d.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _secs(v):
    return f" for {v:.1f} s" if v is not None else ""


ZONE_WORDS = {
    "DOWN": "looking down", "UP": "looking up",
    "LEFT_SIDE": "looking out the left window", "RIGHT_SIDE": "looking out the right window",
    "LEFT_MIRROR": "staring at the left mirror", "RIGHT_MIRROR": "staring at the right mirror",
    "REAR_MIRROR": "staring at the rear-view mirror", "ROAD": "looking away",
}
TIRED_EYE_REASONS = {"HEAVY_EYELIDS": "heavy eyelids", "SLOW_BLINKS": "slow blinks",
                     "FREQUENT_LONG_BLINKS": "frequent long blinks"}
DROWSY_LEVELS = {
    "SLIGHTLY_DROWSY": "Slightly drowsy",
    "DROWSY": "Drowsy - consider a break",
    "VERY_DROWSY": "Very drowsy - take a break soon",
    "SEVERELY_DROWSY": "Severely drowsy - stop and rest",
}
FACE_CAUSES = {
    "TURNED_AWAY_LEFT": "turned away to the left", "TURNED_AWAY_RIGHT": "turned away to the right",
    "HEAD_DROPPED": "head dropped", "LEFT_FRAME": "out of the camera view", "OCCLUDED": "face covered",
}
FALL_DIRECTIONS = {"FORWARD": "forward", "BACKWARD": "backward",
                   "SIDEWAYS_LEFT": "to the left", "SIDEWAYS_RIGHT": "to the right"}


def text(det_type, details):
    """One-line alert text for a detection."""
    d = details if isinstance(details, dict) else {}
    if det_type == "MICROSLEEP":
        return "MICROSLEEP - WAKE UP!" + (f" Eyes closed {_num(d, 'closed_for_s'):.1f} s"
                                          if _num(d, "closed_for_s") is not None else "")
    if det_type == "EYES_CLOSED":
        return "Eyes closed" + _secs(_num(d, "closed_for_s"))
    if det_type == "EYES_ALMOST_CLOSED":
        return "Eyes almost closed - you look tired"
    if det_type == "EYES_NARROWED":
        return "Eyes narrower than normal - getting tired"
    if det_type == "TIRED_EYES":
        reasons = [TIRED_EYE_REASONS.get(r, str(r).lower().replace("_", " "))
                   for r in d.get("reasons", []) if isinstance(r, str)] if isinstance(d.get("reasons"), list) else []
        return "Tired eyes" + (": " + ", ".join(reasons) if reasons else "")
    if det_type == "YAWN":
        n = _num(d, "yawns_5min")   # earlier yawns in the window; the current one is not counted yet
        return "Yawning" + (f" ({int(n) + 1} yawns in 5 min)" if n is not None and n >= 1 else "")
    if det_type == "HEAD_NOD":
        return "Head nodding off - wake up!"
    if det_type == "HEAD_FALLING":
        where = FALL_DIRECTIONS.get(d.get("direction"))
        return "Head falling" + (f" {where}" if where else "") + " - WAKE UP!"
    if det_type == "DROWSY":
        return DROWSY_LEVELS.get(d.get("level"), "Drowsy")
    if det_type == "DISTRACTED":
        reason = d.get("reason")
        if reason == "LOOKING_DOWN":
            return "Not paying attention - looking down" + _secs(_num(d, "glance_down_s") or _num(d, "glance_s"))
        if reason == "CUMULATIVE":
            w = _num(d, "weighted_off_road_30s_s")
            return "Not paying attention - eyes off the road" + (f" {w:.0f} s in the last 30 s" if w is not None else "")
        return "Not paying attention - " + ZONE_WORDS.get(d.get("zone"), "eyes off the road") + _secs(_num(d, "glance_s"))
    if det_type == "FACE_NOT_VISIBLE":
        cause = FACE_CAUSES.get(d.get("likely_cause"))
        return "Driver not visible" + (f" - {cause}" if cause else "")
    return str(det_type).replace("_", " ").capitalize()   # a new type: still shown, just plainly worded
