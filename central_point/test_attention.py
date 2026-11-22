"""Tests for attention.py: fake clock (plain numbers for `now`), no network, no sleeping.

  .venv/bin/python -m unittest test_attention -v
"""
import logging
import unittest

import attention
from attention import Attention, zone_of
from sim import SimObject

T0 = 100.0   # monotonic time of simulation_started (t_s is relative to it)

# One point per zone (center of the rectangle, or near the car for the open-ended columns).
POS = {
    "front_left": (-110, 400), "front_center": (0, 400), "front_right": (110, 400),
    "side_left": (-110, 0), "side_right": (110, 0),
    "mirror_left": (-110, -500), "mirror_center": (0, -500), "mirror_right": (110, -500),
    None: (0, 2000),
}


def obj(oid, zone, type_="car"):
    x, y = POS[zone]
    return SimObject(oid, type_, x, y)


def kinds(events):
    return [(e["kind"], e["object_id"]) for e in events]


class ZoneOfTest(unittest.TestCase):

    def test_every_zone(self):
        for zone, (x, y) in POS.items():
            self.assertEqual(zone_of(x, y), zone, (x, y))

    def test_columns(self):
        self.assertEqual(zone_of(-56, 400), "front_left")
        self.assertEqual(zone_of(-55, 400), "front_center")
        self.assertEqual(zone_of(55, 400), "front_center")
        self.assertEqual(zone_of(56, 400), "front_right")
        self.assertEqual(zone_of(-400, 0), "side_left")      # grass beyond the road
        self.assertEqual(zone_of(400, -300), "mirror_right")

    def test_front_rows(self):
        self.assertEqual(zone_of(-100, 72), "front_left")
        self.assertEqual(zone_of(-100, 71), "side_left")     # 71 belongs to the side
        self.assertEqual(zone_of(100, 943), "front_right")   # 30 m is still in
        self.assertIsNone(zone_of(100, 944))
        self.assertEqual(zone_of(0, 72), "front_center")
        self.assertEqual(zone_of(0, 943), "front_center")
        self.assertIsNone(zone_of(0, 944))

    def test_side_and_mirror_rows(self):
        self.assertEqual(zone_of(-100, -157), "side_left")   # -157 belongs to the side
        self.assertEqual(zone_of(-100, -158), "mirror_left")
        self.assertEqual(zone_of(100, -157), "side_right")
        self.assertEqual(zone_of(100, -158), "mirror_right")
        self.assertEqual(zone_of(-100, -943), "mirror_left")
        self.assertIsNone(zone_of(-100, -944))
        self.assertIsNone(zone_of(100, -944))

    def test_center_column_around_the_ego_car(self):
        self.assertIsNone(zone_of(0, 0))                     # overlaps the ego car
        self.assertIsNone(zone_of(0, 71))
        self.assertIsNone(zone_of(0, -71))
        self.assertEqual(zone_of(0, -72), "mirror_center")
        self.assertEqual(zone_of(0, -157), "mirror_center")  # no side zone in the center lane
        self.assertEqual(zone_of(0, -943), "mirror_center")
        self.assertIsNone(zone_of(0, -944))


class AttentionTest(unittest.TestCase):

    def setUp(self):
        self.a = Attention()
        self.a.start(T0)

    def test_new_object_starts_timer_and_sends_appeared(self):
        ev = self.a.on_tick(T0 + 1.0, [obj("obj_1", "mirror_left")], None)
        self.assertEqual(len(ev), 1)
        e = ev[0]
        self.assertEqual(list(e), ["event", "timestamp", "t_s", "kind", "object_id", "type",
                                   "zone", "window_s", "reaction_s"])
        self.assertEqual({k: v for k, v in e.items() if k != "timestamp"},
                         {"event": "attention_event", "t_s": 1.0, "kind": "appeared",
                          "object_id": "obj_1", "type": "car", "zone": "mirror_left",
                          "window_s": 2.0, "reaction_s": None})
        self.assertIsInstance(e["timestamp"], float)
        t = self.a.objects["obj_1"]
        self.assertEqual(t.deadline, T0 + 3.0)
        self.assertFalse(t.noticed or t.missed)

    def test_object_in_no_zone_has_no_timer(self):
        ev = self.a.on_tick(T0, [obj("far", None)], None)
        self.assertEqual(ev[0]["kind"], "appeared")
        self.assertIsNone(ev[0]["zone"])
        self.assertIsNone(ev[0]["window_s"])
        self.assertIsNone(self.a.objects["far"].deadline)
        self.assertEqual(self.a.update(T0 + 60, None), [])   # never missed

    def test_noticed_in_time_with_reaction(self):
        self.a.on_tick(T0, [obj("obj_1", "mirror_left")], None)
        self.assertEqual(self.a.update(T0 + 0.5, "mirror_left"), [])   # look starts
        self.assertEqual(self.a.update(T0 + 0.6, "mirror_left"), [])   # 0.10 s: not yet
        ev = self.a.update(T0 + 0.7, "mirror_left")                    # 0.20 s >= 0.15
        self.assertEqual(kinds(ev), [("noticed", "obj_1")])
        self.assertEqual(ev[0]["reaction_s"], 0.65)   # look completed at 0.5 + 0.15
        self.assertEqual(ev[0]["t_s"], 0.65)
        self.assertEqual(ev[0]["window_s"], 2.0)
        self.assertTrue(self.a.objects["obj_1"].noticed)
        self.assertEqual(self.a.update(T0 + 5, None), [])   # noticed: no miss later

    def test_missed_after_window(self):
        self.a.on_tick(T0, [obj("p", "front_center", "pedestrian")], None)
        self.assertEqual(self.a.update(T0 + 0.49, "away"), [])
        ev = self.a.update(T0 + 0.52, "away")
        self.assertEqual(kinds(ev), [("missed", "p")])
        self.assertEqual(ev[0]["t_s"], 0.5)          # when the timer ran out
        self.assertEqual(ev[0]["zone"], "front_center")
        self.assertEqual(ev[0]["window_s"], 0.5)
        self.assertIsNone(ev[0]["reaction_s"])
        # no late notice and no second miss while it stays in that zone
        for i in range(10):
            self.assertEqual(self.a.update(T0 + 0.6 + i * 0.05, "front_center"), [])
        self.assertEqual(self.a.on_tick(T0 + 1.2, [obj("p", "front_center", "pedestrian")],
                                        "front_center"), [])

    def test_no_data_and_away_are_not_looking(self):
        self.a.on_tick(T0, [obj("o", "side_left")], None)
        for i, region in enumerate([None, "away", None, "side_right", None]):
            self.assertEqual(self.a.update(T0 + 0.1 + i * 0.15, region), [])
        self.assertEqual(kinds(self.a.update(T0 + 1.0, None)), [("missed", "o")])

    def test_short_dwell_does_not_count(self):
        self.a.on_tick(T0, [obj("o", "front_left")], None)
        self.a.update(T0 + 0.10, "front_left")
        self.assertEqual(self.a.update(T0 + 0.20, "front_left"), [])   # 0.10 s only
        self.a.update(T0 + 0.25, "front_center")                       # sweep moves on
        self.a.update(T0 + 0.30, "front_left")                         # back: starts again
        self.assertEqual(self.a.update(T0 + 0.40, "front_left"), [])   # 0.10 s again
        self.assertEqual(kinds(self.a.update(T0 + 0.50, "away")), [("missed", "o")])

    def test_look_completing_after_deadline_is_missed(self):
        self.a.on_tick(T0, [obj("o", "front_left")], None)
        self.a.update(T0 + 0.40, "front_left")                 # would complete at 0.55
        ev = self.a.update(T0 + 0.55, "front_left")
        self.assertEqual(kinds(ev), [("missed", "o")])

    def test_look_before_entry_counts_from_entry(self):
        self.a.on_tick(T0, [obj("o", None)], "mirror_left")
        self.a.update(T0 + 0.1, "mirror_left")
        self.a.update(T0 + 0.5, "mirror_left")                 # looking for 0.5 s already
        ev = self.a.on_tick(T0 + 1.0, [obj("o", "mirror_left")], "mirror_left")
        self.assertEqual(kinds(ev), [("zone_change", "o")])
        self.assertEqual(self.a.update(T0 + 1.1, "mirror_left"), [])   # 0.10 s since entry
        ev = self.a.update(T0 + 1.15, "mirror_left")
        self.assertEqual(kinds(ev), [("noticed", "o")])
        self.assertEqual(ev[0]["reaction_s"], 0.15)

    def test_zone_change_resets_and_starts_new_window(self):
        self.a.on_tick(T0, [obj("o", "mirror_left")], None)
        self.a.update(T0 + 0.2, "mirror_left")
        self.assertEqual(kinds(self.a.update(T0 + 0.4, "mirror_left")), [("noticed", "o")])
        ev = self.a.on_tick(T0 + 1.0, [obj("o", "side_left")], "front_center")
        self.assertEqual(kinds(ev), [("zone_change", "o")])
        self.assertEqual((ev[0]["zone"], ev[0]["window_s"]), ("side_left", 1.0))
        t = self.a.objects["o"]
        self.assertFalse(t.noticed or t.missed)
        self.assertEqual((t.zone_entered_at, t.deadline), (T0 + 1.0, T0 + 2.0))
        self.assertEqual(self.a.update(T0 + 1.95, "front_center"), [])
        ev = self.a.update(T0 + 2.0, "front_center")
        self.assertEqual(kinds(ev), [("missed", "o")])
        self.assertEqual(ev[0]["zone"], "side_left")

    def test_zone_change_drops_running_timer(self):
        self.a.on_tick(T0, [obj("o", "mirror_left")], None)
        self.a.on_tick(T0 + 1.0, [obj("o", "side_left")], None)   # mirror timer dropped
        self.assertEqual(self.a.update(T0 + 1.9, None), [])
        ev = self.a.update(T0 + 2.0, None)
        self.assertEqual([(e["kind"], e["zone"]) for e in ev], [("missed", "side_left")])

    def test_expired_timer_counts_before_zone_change(self):
        self.a.on_tick(T0, [obj("o", "front_right")], None)
        ev = self.a.on_tick(T0 + 0.6, [obj("o", "side_right")], None)  # no update in between
        self.assertEqual([(e["kind"], e["zone"]) for e in ev],
                         [("missed", "front_right"), ("zone_change", "side_right")])

    def test_no_second_notice_in_same_zone(self):
        self.a.on_tick(T0, [obj("o", "front_center")], None)
        self.a.update(T0 + 0.1, "front_center")
        self.assertEqual(len(self.a.update(T0 + 0.3, "front_center")), 1)
        self.a.update(T0 + 0.4, "away")
        self.a.update(T0 + 0.5, "front_center")
        self.assertEqual(self.a.update(T0 + 0.8, "front_center"), [])
        self.assertEqual(self.a.on_tick(T0 + 0.9, [SimObject("o", "car", 10, 300)],
                                        "front_center"), [])   # moved, same zone
        self.assertEqual(self.a.update(T0 + 1.5, None), [])

    def test_parallel_timers(self):
        objs = [obj("a", "front_left"), obj("b", "side_right"), obj("c", "mirror_center"),
                obj("d", "mirror_right")]
        self.assertEqual(len(self.a.on_tick(T0, objs, None)), 4)
        got = []
        for i in range(1, 61):      # 3 s at 20 Hz
            now = T0 + i * 0.05
            region = "mirror_center" if 1.0 <= i * 0.05 < 1.5 else "away"
            got += [(e["kind"], e["object_id"], e["t_s"]) for e in self.a.update(now, region)]
        self.assertEqual(got, [("missed", "a", 0.5), ("missed", "b", 1.0),
                               ("noticed", "c", 1.15), ("missed", "d", 2.0)])

    def test_missing_object_is_forgotten(self):
        self.a.on_tick(T0, [obj("a", "front_left"), obj("b", "mirror_left")], None)
        self.assertEqual(self.a.on_tick(T0 + 0.1, [obj("b", "mirror_left")], None), [])
        self.assertNotIn("a", self.a.objects)
        self.assertEqual(kinds(self.a.update(T0 + 1.0, None)), [])
        ev = self.a.on_tick(T0 + 1.1, [obj("a", "front_left"), obj("b", "mirror_left")], None)
        self.assertEqual(kinds(ev), [("appeared", "a")])   # comes back as new

    def test_summary(self):
        self.a.on_tick(T0, [obj("a", "front_left"), obj("b", "mirror_left"),
                            obj("c", "front_center", "pedestrian"), obj("d", "side_left")],
                       None)
        self.a.update(T0 + 0.1, "front_left")
        self.a.update(T0 + 0.3, "front_left")          # a noticed, reaction 0.25
        self.a.update(T0 + 0.6, "away")                # c missed at 0.5
        self.a.update(T0 + 1.0, "mirror_left")
        self.a.update(T0 + 1.2, "mirror_left")         # b noticed, reaction 1.15 ; d missed 1.0
        self.a.on_tick(T0 + 2.0, [obj("e", "front_right", "truck")], None)
        out = self.a.end(T0 + 2.2, None)               # e: timer still running -> dropped
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertEqual(list(s), ["event", "timestamp", "duration_s", "noticed_count",
                                   "missed_count", "avg_reaction_s", "missed"])
        self.assertEqual(s["event"], "attention_summary")
        self.assertEqual(s["duration_s"], 2.2)
        self.assertEqual((s["noticed_count"], s["missed_count"]), (2, 2))
        self.assertEqual(s["avg_reaction_s"], 0.7)     # (0.25 + 1.15) / 2
        self.assertEqual(s["missed"], [
            {"t_s": 0.5, "object_id": "c", "type": "pedestrian", "zone": "front_center"},
            {"t_s": 1.0, "object_id": "d", "type": "car", "zone": "side_left"}])
        self.assertFalse(self.a.running)
        self.assertEqual(self.a.update(T0 + 5, None), [])   # stopped: no late alert

    def test_summary_without_notices_and_end_check(self):
        self.a.on_tick(T0, [obj("a", "front_left")], None)
        out = self.a.end(T0 + 0.7, None)    # deadline passed before the last 20 Hz check
        self.assertEqual([m.get("kind", m["event"]) for m in out],
                         ["missed", "attention_summary"])
        self.assertIsNone(out[-1]["avg_reaction_s"])
        self.assertEqual(out[-1]["missed_count"], 1)

    def test_start_resets_everything(self):
        self.a.on_tick(T0, [obj("a", "front_left")], None)
        self.a.update(T0 + 1, None)
        self.a.start(T0 + 10)
        self.assertEqual((self.a.objects, self.a.reactions, self.a.misses), ({}, [], []))
        ev = self.a.on_tick(T0 + 10.5, [obj("a", "front_left")], None)
        self.assertEqual(ev[0]["t_s"], 0.5)

    def test_end_without_start_sends_nothing(self):
        self.assertEqual(Attention().end(T0, None), [])

    def test_accepted_looks_can_hold_several_regions(self):
        saved = attention.ACCEPTED_LOOKS["side_left"]
        attention.ACCEPTED_LOOKS["side_left"] = {"side_left", "mirror_left"}
        try:
            self.a.on_tick(T0, [obj("o", "side_left")], None)
            self.a.update(T0 + 0.1, "mirror_left")
            self.a.update(T0 + 0.2, "side_left")       # still within the accepted set
            ev = self.a.update(T0 + 0.25, "side_left")
            self.assertEqual(ev[0]["reaction_s"], 0.25)
        finally:
            attention.ACCEPTED_LOOKS["side_left"] = saved


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()
