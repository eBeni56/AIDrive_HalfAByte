"""Detections from the camera client -> short alert texts and categories.

The server handles exactly THREE detections (everything else the camera might send is dropped on arrival):
  EYES_CLOSED  category "eyes_closed"            the eyes are closed longer than a blink (WARNING, DANGER after 1 s)
  DISTRACTED   category "not_paying_attention"   looking UP or DOWN (at none of the road / mirror / side-window
                                                 zones) for 1 s (DANGER after 2.5 s)
  PHONE_USE    category "not_paying_attention"   a phone in the driver's hand (DANGER after 3 s)
The third main feature, "did the driver look at the car / bike / person?", comes from the simulator side
(attention.py).

The ONE place that words the alerts for the displays (simulator, dashboard). driver_monitor decides WHEN
something is a problem (its timing rules); this file only says what to call it. Every function takes the
detection's `details` (a dict, any field may be missing) and never raises.
"""

SEVERITIES = ["INFO", "WARNING", "DANGER", "CRITICAL"]   # low -> high; "NONE" = nothing active


def severity_rank(s):
    """0 = NONE / unknown, 1 = INFO ... 4 = CRITICAL."""
    return SEVERITIES.index(s) + 1 if s in SEVERITIES else 0


CATEGORY = {"EYES_CLOSED": "eyes_closed", "DISTRACTED": "not_paying_attention", "PHONE_USE": "not_paying_attention"}
HANDLED = set(CATEGORY)   # every other detection type is ignored


def category(det_type):
    """"eyes_closed" or "not_paying_attention"; None for a type the server does not handle."""
    return CATEGORY.get(det_type)


def _num(d, key):
    v = d.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _secs(v):
    return f" for {v:.1f} s" if v is not None else ""


ZONE_WORDS = {"DOWN": "looking down", "UP": "looking up"}


def text(det_type, details):
    """One-line alert text for a detection the server handles ("" for any other type)."""
    d = details if isinstance(details, dict) else {}
    if det_type == "EYES_CLOSED":
        return "Eyes closed" + _secs(_num(d, "closed_for_s")) + " - open your eyes!"
    if det_type == "DISTRACTED":
        return "Not paying attention - " + ZONE_WORDS.get(d.get("zone"), "eyes off the road") + _secs(_num(d, "away_s"))
    if det_type == "PHONE_USE":
        return "Not paying attention - phone in hand" + _secs(_num(d, "seen_for_s"))
    return ""
