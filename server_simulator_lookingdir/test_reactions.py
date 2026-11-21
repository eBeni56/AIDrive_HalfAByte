"""Tests for reactions.py: when the copilot speaks, and what.

Run: python -m unittest -v   (in this directory; needs only the server's requirements)
"""
import unittest

import reactions


def alert(det_id, det_type, duration=1.0, severity="WARNING", **details):
    category = "eyes_closed" if det_type == "EYES_CLOSED" else "not_paying_attention"
    return {"id": det_id, "type": det_type, "category": category, "severity": severity, "status": "ONGOING",
            "duration_s": duration, "text": "", "details": details}


def missed(obj_id="car_1", obj_type="car", zone="side_left"):
    return {"event": "attention_event", "kind": "missed", "object_id": obj_id, "type": obj_type, "zone": zone,
            "window_s": 1.0, "reaction_s": None, "t_s": 3.0}


class ReactionsTest(unittest.TestCase):
    def setUp(self):
        self.r = reactions.Reactions()

    def say(self, now, active=()):
        return [m["say"] for m in self.r.update(now, list(active))]

    def test_nothing_wrong_says_nothing(self):
        self.assertEqual(self.say(0.0), [])

    def test_long_blink_is_quiet_then_eyes_closed_is_said_and_repeated(self):
        self.assertEqual(self.say(0.0, [alert("eyes_closed-1", "EYES_CLOSED", 0.6)]), [])
        self.assertEqual(self.say(0.4, [alert("eyes_closed-1", "EYES_CLOSED", 1.0, "DANGER")]),
                         [reactions.EYES_CLOSED_LINE])
        self.assertEqual(self.say(1.0, [alert("eyes_closed-1", "EYES_CLOSED", 1.6, "DANGER")]), [])
        out = self.r.update(3.5, [alert("eyes_closed-1", "EYES_CLOSED", 4.1, "DANGER")])
        self.assertEqual([(m["kind"], m["repeat"], m["priority"]) for m in out], [("eyes_closed", True, 3)])

    def test_phone_and_looking_down_lines(self):
        self.assertEqual(self.say(0.0, [alert("phone_use-1", "PHONE_USE")]), [reactions.PHONE_LINE])
        r = reactions.Reactions()
        self.assertEqual([m["say"] for m in r.update(0.0, [alert("distracted-1", "DISTRACTED", zone="DOWN")])],
                         [reactions.LOOKING_DOWN_LINE])
        r = reactions.Reactions()
        self.assertEqual([m["say"] for m in r.update(0.0, [alert("distracted-2", "DISTRACTED", zone="UP")])],
                         [reactions.ATTENTION_LINE])

    def test_not_paying_attention_repeats_and_does_not_double_talk(self):
        down = alert("distracted-1", "DISTRACTED", zone="DOWN")
        phone = alert("phone_use-1", "PHONE_USE")
        self.assertEqual(self.say(0.0, [down]), [reactions.LOOKING_DOWN_LINE])
        self.assertEqual(self.say(1.0, [down, phone]), [])            # just said something: wait
        self.assertEqual(self.say(3.0, [down, phone]), [reactions.PHONE_LINE])
        self.assertEqual(self.say(5.0, [down, phone]), [])
        self.assertEqual(self.say(8.0, [down, phone]), [reactions.PHONE_LINE])   # every ATTENTION_REPEAT_S

    def test_eyes_closed_silences_the_attention_line(self):
        out = self.r.update(0.0, [alert("phone_use-1", "PHONE_USE"),
                                  alert("eyes_closed-1", "EYES_CLOSED", 1.2, "DANGER")])
        self.assertEqual([m["kind"] for m in out], ["eyes_closed"])

    def test_missed_object_is_said_once_with_its_side(self):
        self.r.on_attention(0.0, [missed(), {"event": "attention_event", "kind": "noticed", "object_id": "b",
                                             "type": "bicycle", "zone": "side_right"}])
        out = self.r.update(0.05, [])
        self.assertEqual([m["say"] for m in out], ["Car to the left of you!"])
        self.assertEqual(out[0]["object"]["side"], "left")
        self.assertEqual(self.say(0.2), [])

    def test_object_lines(self):
        self.assertEqual(reactions.object_line("pedestrian", "front_center"), "Person in front of you!")
        self.assertEqual(reactions.object_line("bicycle", "mirror_right"), "Bike coming up on your right!")
        self.assertEqual(reactions.object_line("truck", "front_left"), "Truck ahead on your left!")
        self.assertEqual(reactions.object_line("ufo", None), "Something near you!")

    def test_most_urgent_first(self):
        self.r.on_attention(0.0, [missed()])
        out = self.r.update(0.0, [alert("eyes_closed-1", "EYES_CLOSED", 1.1, "DANGER")])
        self.assertEqual([m["kind"] for m in out], ["eyes_closed", "missed_object"])


if __name__ == "__main__":
    unittest.main()
