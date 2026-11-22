"""RUN LOG: what happened in the current simulation run, written as plain lines for the LLM coach.

Kept per run (reset at simulation_started):
  looks       every road user that needed a look: noticed in time (with the reaction) or missed (with the
              distance and where the driver looked during its look window)
  episodes    driver episodes from driver_state: eyes closed, phone in hand, looked up / down, each with its
              duration (logged when it ends)
  gaze        what the driver looked at, as time segments: road, left / rear / right mirror, side windows,
              looking away, phone in hand, looked up / down, eyes closed, no camera data. Gives the time
              shares, the mirror checks (a mirror look of >= MIRROR_LOOK_S) and the gaze during a look window.
  warnings    the lines the car spoke (not their repeats)
  tips        what the coach already said
Plus a one-line summary of each of the last MAX_PREVIOUS runs (memory only, lost on restart).

tip_report() and debrief_report() build the text the LLM gets: facts only, no conclusions (finding the
pattern, the advice and the words is the LLM's job). No networking and no clock in here: every method takes
`now`.
"""
from collections import Counter, defaultdict

from alerts import A_TYPE, ZONE_CATEGORY, place

MAX_PREVIOUS = 3          # previous runs remembered for the debrief
MAX_TIP_EVENTS = 12       # events in a tip report (the most recent ones)
MAX_DEBRIEF_EVENTS = 25   # driver episodes listed in a debrief
MAX_DEBRIEF_WARNINGS = 20 # spoken warnings listed in a debrief
GAZE_GAP_S = 0.5          # a longer gap between gaze samples is not counted (no data)
MIRROR_LOOK_S = 0.15      # a mirror look at least this long counts as a mirror check (the look dwell)
HALVES_MIN_S = 120.0      # mirror checks per half only for drives at least this long
DETECTION_TYPES = ("EYES_CLOSED", "PHONE_USE", "DISTRACTED")

REGION_LABEL = {"front": "road",
                "mirror_left": "left mirror", "mirror_center": "rear mirror", "mirror_right": "right mirror",
                "side_left": "left side window", "side_right": "right side window", "away": "looking away"}
MIRRORS = ("left mirror", "rear mirror", "right mirror")
SIDES = ("left", "right", "ahead", "behind")
PLURAL = {"car": "cars", "truck": "trucks", "bicycle": "bicycles", "motorcycle": "motorcycles",
          "pedestrian": "pedestrians"}
EPISODES = ("eyes closed", "phone in hand", "looked down", "looked up")


def clock(t_s):
    """Seconds -> "m:ss"."""
    t = max(0, int(round(t_s or 0)))
    return f"{t // 60}:{t % 60:02d}"


def times(n):
    return f"{n} time" if n == 1 else f"{n} times"


def share_group(label):
    """A gaze label -> its group in the debrief's time shares."""
    if label.startswith("road"):
        return "road"
    if label in MIRRORS or label in ("eyes closed", "no camera data"):
        return label
    if label.endswith("side window"):
        return "side windows"
    return "looking down or at the phone"   # phone in hand, looked up / down, looking away


def tip_group(label):
    """A gaze label -> road / mirrors / elsewhere / eyes closed / no data (the tip's totals; the same
    definitions as the dashboard's statistics in narrator.ts)."""
    g = share_group(label)
    if g in MIRRORS:
        return "mirrors"
    if g in ("side windows", "looking down or at the phone"):
        return "elsewhere"
    return g


def _number(v):
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else 0.0


def _avg(xs):
    return sum(xs) / len(xs) if xs else None


class RunLog:
    def __init__(self):
        self.run_no = 0
        self.previous = []        # one-line summaries of the last runs, oldest first
        self._reset()

    def _reset(self):
        self.running = False
        self.started_at = None
        self.events = []          # (t_s, text): looks and episodes, for the tip reports
        self.looks = []           # {"t", "kind", "type", "zone", "reaction", "distance", "window"}
        self.episodes = []        # (label, start t_s, duration)
        self.segments = []        # [start t_s, end t_s, gaze label], consecutive equal labels merged
        self.gaze_time = defaultdict(float)
        self._last_gaze = None    # (label, now)
        self.open = {}            # detection id -> {"type", "start", "dur", "up"}: episodes going on
        self.closed_ids = set()   # episodes already logged (an ENDED message may come more than once)
        self.counts = dict.fromkeys(DETECTION_TYPES, 0)
        self.warnings = []        # (t_s, line spoken)
        self.tips = []            # (t_s, text)

    @property
    def reactions(self):
        return [lk["reaction"] for lk in self.looks if lk["kind"] == "noticed"]

    @property
    def missed(self):
        return [lk for lk in self.looks if lk["kind"] == "missed"]

    # ---- run ----

    def start(self, now):
        self._reset()
        self.running, self.started_at = True, now
        self.run_no += 1

    def t_s(self, now):
        """Seconds since this run started, or None outside a run."""
        return round(now - self.started_at, 1) if self.running else None

    def end(self, now):
        """simulation_ended: returns {"duration_s", "something_happened", "report"} (the debrief text)."""
        if not self.running:
            return {"duration_s": 0.0, "something_happened": False, "report": ""}
        for det_id in list(self.open):
            self._close(det_id)
        info = {"duration_s": round(now - self.started_at, 1),
                "something_happened": bool(self.looks or any(self.counts.values())),
                "report": self.debrief_report(now)}
        self.previous = (self.previous + [self._one_line(now)])[-MAX_PREVIOUS:]
        self.running = False
        return info

    # ---- input ----

    def _label(self, status, region):
        if status == "eyes_closed":
            return "eyes closed"
        if status != "ok" or region is None:
            return "no camera data"
        types = {ep["type"]: ep for ep in self.open.values()}
        if "PHONE_USE" in types:
            return "phone in hand"
        if "DISTRACTED" in types:
            return "looked up" if types["DISTRACTED"]["up"] else "looked down"
        return REGION_LABEL.get(region, "looking away")

    def on_gaze(self, now, status, region):
        """Called with every gaze_update (GAZE_HZ): time shares and gaze segments."""
        if not self.running:
            return
        if self._last_gaze is not None:
            label, at = self._last_gaze
            dt = min(now - at, GAZE_GAP_S)
            self.gaze_time[label] += dt
            start = at - self.started_at
            last = self.segments[-1] if self.segments else None
            if last and last[2] == label and abs(last[1] - start) < 1e-6:
                last[1] = start + dt
            else:
                self.segments.append([start, start + dt, label])
        self._last_gaze = (self._label(status, region), now)

    def on_attention(self, ev):
        if not self.running or ev.get("event") != "attention_event" or ev.get("kind") not in ("noticed", "missed"):
            return
        what, where, t = A_TYPE.get(ev.get("type"), "something"), place(ev.get("zone")), _number(ev.get("t_s"))
        look = {"t": t, "kind": ev["kind"], "type": ev.get("type"), "zone": ev.get("zone"),
                "reaction": _number(ev.get("reaction_s")), "distance": ev.get("distance_m"),
                "window": _number(ev.get("window_s"))}
        self.looks.append(look)
        if ev["kind"] == "noticed":
            self.events.append((t, f"noticed in time: {what} {where} (after {look['reaction']:.1f} s)"))
        else:
            self.events.append((t, f"missed: {what} {where} (not looked at within {ev.get('window_s')} s)"))

    def on_driver_state(self, msg, now):
        """Driver episodes: counted when they start, logged with their length when they end."""
        if not self.running:
            return
        dets = msg.get("detections")
        seen = set()
        for d in dets if isinstance(dets, list) else []:
            if not isinstance(d, dict) or d.get("type") not in DETECTION_TYPES or not isinstance(d.get("id"), str):
                continue
            det_id, dur = d["id"], _number(d.get("duration_s"))
            if det_id in self.closed_ids:
                continue
            if det_id not in self.open:
                details = d.get("details")
                self.open[det_id] = {"type": d["type"], "start": max(0.0, now - self.started_at - dur), "dur": dur,
                                     "up": isinstance(details, dict) and details.get("zone") == "UP"}
                self.counts[d["type"]] += 1
            self.open[det_id]["dur"] = dur
            if d.get("status") == "ENDED":
                self._close(det_id)
            else:
                seen.add(det_id)
        for det_id in [i for i in self.open if i not in seen]:   # gone without ENDED (camera restarted)
            self._close(det_id)

    def on_warning(self, w):
        """A spoken warning (repeats are not listed)."""
        if self.running and not w.get("repeat") and isinstance(w.get("say"), str):
            self.warnings.append((_number(w.get("t_s")), w["say"]))

    def on_tip(self, text, now):
        if self.running:
            self.tips.append((self.t_s(now), text))

    def _close(self, det_id):
        ep = self.open.pop(det_id)
        self.closed_ids.add(det_id)
        label = {"EYES_CLOSED": "eyes closed", "PHONE_USE": "phone in hand",
                 "DISTRACTED": "looked up" if ep["up"] else "looked down"}[ep["type"]]
        self.episodes.append((label, round(ep["start"], 1), ep["dur"]))
        self.events.append((round(ep["start"], 1), f"{label} for {ep['dur']:.1f} s"))

    # ---- statistics ----

    def _gaze_in(self, a, b):
        """Where the driver looked between run times a and b: "phone in hand 1.6 s, road 0.4 s"."""
        spent = defaultdict(float)
        for s, e, label in self.segments:
            overlap = min(e, b) - max(s, a)
            if overlap > 0:
                spent[label] += overlap
        parts = [f"{label} {sec:.1f} s" for label, sec in sorted(spent.items(), key=lambda kv: -kv[1]) if sec >= 0.1]
        return ", ".join(parts) or "no gaze data"

    def _mirror_looks(self):
        return [(s, e, label) for s, e, label in self.segments if label in MIRRORS and e - s >= MIRROR_LOOK_S]

    def _mirror_line(self, duration):
        looks = self._mirror_looks()
        minutes = max(duration / 60, 1e-9)
        per = Counter(label for _, _, label in looks)
        line = (f"Mirror checks: {len(looks) / minutes:.1f} per minute (left {per['left mirror'] / minutes:.1f}, "
                f"right {per['right mirror'] / minutes:.1f}, rear {per['rear mirror'] / minutes:.1f})")
        if duration >= HALVES_MIN_S:
            half = duration / 2
            first = sum(1 for s, _, _ in looks if s < half)
            line += (f"; first half {first / (minutes / 2):.1f} per minute, "
                     f"second half {(len(looks) - first) / (minutes / 2):.1f} per minute")
        edges = [0.0] + [x for s, e, _ in looks for x in (s, e)] + [duration]
        longest = max(edges[i + 1] - edges[i] for i in range(0, len(edges) - 1, 2))
        return line + f"; longest stretch without a mirror look {longest:.0f} s."

    def _shares_line(self):
        total = sum(self.gaze_time.values())
        if total <= 0:
            return "Gaze shares: no gaze data."
        g = defaultdict(float)
        for label, sec in self.gaze_time.items():
            g[share_group(label)] += sec
        order = ("road", "left mirror", "rear mirror", "right mirror", "side windows",
                 "looking down or at the phone", "eyes closed", "no camera data")
        return "Gaze shares: " + ", ".join(f"{k} {round(100 * g[k] / total)} %" for k in order) + "."

    def _episodes_line(self):
        parts = []
        for label in EPISODES:
            durs = [d for lb, _, d in self.episodes if lb == label]
            if not durs:
                if label != "looked up":
                    parts.append(f"{label} 0 times")
                continue
            part = f"{label} {times(len(durs))} (total {sum(durs):.1f} s"
            parts.append(part + (f", longest {max(durs):.1f} s)" if len(durs) > 1 else ")"))
        return "Driver episodes: " + "; ".join(parts) + "."

    def _looks_lines(self, duration):
        noticed = [lk for lk in self.looks if lk["kind"] == "noticed"]
        n, k = len(self.looks), len(noticed)
        avg = _avg([lk["reaction"] for lk in noticed])
        lines = [f"Road users that needed a look: {n}. Noticed in time {k}"
                 + (f" (average reaction {avg:.2f} s)" if avg is not None else "") + f", missed {n - k}."]
        if not n:
            return lines
        by_side = defaultdict(lambda: [0, 0])
        by_type = defaultdict(lambda: [0, 0])
        for lk in self.looks:
            for table, key in ((by_side, ZONE_CATEGORY.get(lk["zone"], "near the car")),
                               (by_type, PLURAL.get(lk["type"], "others"))):
                table[key][1] += 1
                table[key][0] += lk["kind"] == "noticed"
        sides = [f"{s} {by_side[s][0]} of {by_side[s][1]}" for s in SIDES if s in by_side]
        if sides:
            sides[0] += " noticed"
            lines.append("By side: " + ", ".join(sides) + ".")
        lines.append("By type: " + ", ".join(f"{t} {a} of {b}" for t, (a, b) in by_type.items()) + ".")
        if noticed:
            r = [lk["reaction"] for lk in noticed]
            line = f"Reaction times: fastest {min(r):.2f} s, slowest {max(r):.2f} s"
            first = [lk["reaction"] for lk in noticed if lk["t"] < duration / 2]
            second = [lk["reaction"] for lk in noticed if lk["t"] >= duration / 2]
            if len(first) >= 2 and len(second) >= 2:
                line += (f"; first half of the drive {_avg(first):.2f} s average, "
                         f"second half {_avg(second):.2f} s")
            lines.append(line + ".")
        missed = sorted(self.missed, key=lambda lk: lk["t"])
        if missed:
            lines.append("Missed road users:")
            for lk in missed:
                dist = f", {lk['distance']:.0f} m away" if isinstance(lk["distance"], int | float) else ""
                lines.append(f"- {clock(lk['t'])} {A_TYPE.get(lk['type'], 'something')} {place(lk['zone'])}{dist}; "
                             f"gaze during the look window: {self._gaze_in(lk['t'] - lk['window'], lk['t'])}")
        return lines

    # ---- reports (the LLM's input) ----

    def _totals(self, now):
        n_noticed, n_missed = len(self.reactions), len(self.missed)
        parts = [f"{n_noticed + n_missed} road users needed a look: {n_noticed} noticed in time"]
        if self.reactions:
            parts[0] += f" (average reaction {_avg(self.reactions):.2f} s)"
        parts[0] += f", {n_missed} missed."
        total = sum(self.gaze_time.values())
        if total > 0:
            g = defaultdict(float)
            for label, sec in self.gaze_time.items():
                g[tip_group(label)] += sec
            g = {k: round(100 * g[k] / total) for k in ("road", "mirrors", "elsewhere", "eyes closed", "no camera data")}
            share = (f"Gaze: road {g['road']} %, mirrors {g['mirrors']} %, elsewhere {g['elsewhere']} %, "
                     f"eyes closed {g['eyes closed']} %")
            parts.append(share + (f", no camera data {g['no camera data']} %." if g["no camera data"] else "."))
        c = self.counts
        parts.append(f"Eyes closed {times(c['EYES_CLOSED'])}, phone in hand {times(c['PHONE_USE'])}, "
                     f"looked up or down {times(c['DISTRACTED'])}.")
        return " ".join(parts)

    def _tips_given(self):
        if not self.tips:
            return "Tips already given: none."
        return "Tips already given:\n" + "\n".join(f'- {clock(t)} "{text}"' for t, text in self.tips)

    def tip_report(self, now, since_t_s):
        """For a live tip: the events since the last tip (the newest MAX_TIP_EVENTS), the totals so far and the
        tips already given."""
        new = sorted(e for e in self.events if since_t_s is None or e[0] > since_t_s)[-MAX_TIP_EVENTS:]
        head = "Since your last tip:" if since_t_s is not None else "So far in this drive:"
        lines = [f"Drive time: {clock(now - self.started_at)}.", head]
        lines += [f"- {clock(t)} {text}" for t, text in new] or ["- nothing new"]
        lines += [f"This drive so far: {self._totals(now)}", self._tips_given()]
        return "\n".join(lines)

    def debrief_report(self, now):
        """For the end-of-drive analysis: the full statistics of the run, the warnings spoken, the tips given and
        the previous runs."""
        duration = now - self.started_at
        lines = [f"Drive {self.run_no} finished after {clock(duration)}."]
        lines += self._looks_lines(duration)
        lines.append(self._episodes_line())
        lines += [f"- {clock(t)} {label} for {d:.1f} s" for label, t, d in sorted(self.episodes, key=lambda e: e[1])
                  ][:MAX_DEBRIEF_EVENTS]
        lines.append(self._shares_line())
        lines.append(self._mirror_line(duration))
        if self.warnings:
            shown = sorted(self.warnings)[:MAX_DEBRIEF_WARNINGS]
            lines.append(f"Warnings spoken ({len(self.warnings)}): "
                         + "; ".join(f'{clock(t)} "{say}"' for t, say in shown))
        else:
            lines.append("Warnings spoken: none.")
        lines.append(self._tips_given().replace("Tips already given", "Tips given during the drive"))
        if self.previous:
            lines.append("Previous drives (oldest first):")
            lines += [f"- {p}" for p in self.previous]
        else:
            lines.append("Previous drives: none (this was the first).")
        return "\n".join(lines)

    def _one_line(self, now):
        r = self.reactions
        n_noticed, n_missed = len(r), len(self.missed)
        avg = f"{_avg(r):.2f} s" if r else "-"
        c = self.counts
        return (f"Drive {self.run_no} ({clock(now - self.started_at)}): {n_noticed} of {n_noticed + n_missed} "
                f"noticed in time, average reaction {avg}, eyes closed {times(c['EYES_CLOSED'])}, "
                f"phone {times(c['PHONE_USE'])}, looked up or down {times(c['DISTRACTED'])}.")
