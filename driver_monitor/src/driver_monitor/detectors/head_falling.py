"""HEAD_FALLING: the head sinks forward (or slumps sideways / tips back) WITH CLOSED EYES.

The eyes are the deciding signal. Looking down at a phone moves the head 15-25 deg down and lowers the lids
(openness ~0.5) with the eyes open - that is DISTRACTED, never HEAD_FALLING. So every start condition
needs eyes that are measured CLOSED (the eye-closure detector's view, which also bridges short tracking
gaps); "eyes unknown" never counts as closed.

direction FORWARD, started by either
  HELD_DOWN / SUDDEN_DROP  pitch <= -pitch_down_deg together with closed eyes for >= hold_s
                           (pattern SUDDEN_DROP if the head got there fast), or
  SLOW_DRIFT               pitch fell >= drift_min_deg over drift_window_s STEADILY (checked once per second,
                           every second lower by >= drift_min_slope_dps) and the eyes have been closed for
                           >= drift_closed_s. A nod (dip and back) or one fast move down is not steady.
direction SIDEWAYS_LEFT/RIGHT  |roll| >= roll_deg with closed eyes for hold_s, or a steady roll drift >= roll_drift_deg
direction BACKWARD             pitch >= backward_pitch_deg with eyes closed >= backward_closed_s
Nothing starts while |yaw| > max_yaw_deg: near a profile view MediaPipe's pitch/roll are unreliable
(turning to a side window produced fake "slumps" in real recordings).
DANGER; CRITICAL if the eyes are closed, a microsleep is active or the drop is >= critical_drop_deg.
Ends when the head is back within end_within_deg / end_roll_deg of neutral for end_after_s, or when the
eyes have been open again for end_eyes_open_s (awake, the head position is then the driver's choice).
The details carry the trend (where the head was 3, 2, 1, 0 s ago), the speed and the total drop.
"""

from __future__ import annotations

import itertools

from driver_monitor.common.enums import DetectionType, EyeState, Severity
from driver_monitor.common.timeseries import Debounce, EventLog, History, Sample
from driver_monitor.config import HeadFallingConfig
from driver_monitor.detectors.base import Detection, EpisodeTracker, r
from driver_monitor.signals.eyes import EyesSignal
from driver_monitor.signals.head import HeadSignal

_PITCH, _ROLL = 1, 2  # index into History samples of HeadPoseProcessor: (yaw, pitch, roll)


def _steady_change(history: History, t: float, idx: int, window_s: float, min_step: float) -> float | None:
    """Total change over `window_s` if it happened STEADILY: sampled once per second, every step moves the
    same way by >= min_step degrees. A quick dip-and-return (nod) or a single fast move (deliberate look
    down) is not steady. Returns None when the movement is not steady."""
    steps = max(round(window_s), 2)
    samples: list[Sample] = []
    for k in range(steps, -1, -1):
        s = history.at(t - k * window_s / steps)
        if s is None:
            return None
        samples.append(s)
    diffs = [b.values[idx] - a.values[idx] for a, b in itertools.pairwise(samples)]
    step = min_step * window_s / steps
    if all(d <= -step for d in diffs) or all(d >= step for d in diffs):
        return samples[-1].values[idx] - samples[0].values[idx]
    return None


class HeadFallingDetector:
    def __init__(self, cfg: HeadFallingConfig) -> None:
        self.cfg = cfg
        self.tracker = EpisodeTracker(DetectionType.HEAD_FALLING)
        self.falls: EventLog[bool] = EventLog(max(cfg.window_s, 600.0))  # value: eyes closed during it
        self._hold_fwd = Debounce(cfg.hold_s, 0.2)
        self._hold_side = Debounce(cfg.hold_s, 0.2)
        self._recover = Debounce(cfg.end_after_s, 0.1)
        self._eyes_open_since: float | None = None
        self._direction = "FORWARD"
        self._pattern = "HELD_DOWN"
        self._start_value = 0.0
        self._closed_during = False

    def reset(self) -> None:
        self.tracker.reset()
        self.falls.clear()
        self._hold_fwd.reset()
        self._hold_side.reset()
        self._recover.reset()
        self._eyes_open_since = None

    @property
    def active(self) -> bool:
        return self.tracker.is_active

    def count(self, t: float) -> int:
        return self.falls.count(t, self.cfg.window_s)

    def closed_eye_falls(self, t: float) -> int:
        return sum(1 for _, closed in self.falls.since(t, self.cfg.window_s) if closed)

    def update(
        self,
        head: HeadSignal,
        eyes: EyesSignal,
        closed_since: float | None,
        history: History,
        microsleep_active: bool,
        t: float,
    ) -> None:
        """`closed_since`: start of the ongoing eye closure (EyeClosureDetector.closed_since) or None."""
        if not head.valid or head.pitch is None or head.roll is None:
            if self.tracker.is_active:
                self.tracker.end(t, details={"outcome": "FACE_LOST"})
            self._hold_fwd.reset()
            self._hold_side.reset()
            return
        closed_for = t - closed_since if closed_since is not None else None
        if closed_for is None and eyes.reliable:
            self._eyes_open_since = self._eyes_open_since if self._eyes_open_since is not None else t
        else:
            self._eyes_open_since = None

        if not self.tracker.is_active:
            self._try_start(head, eyes, closed_for, history, t)
        if self.tracker.is_active:
            self._update_active(head, eyes, microsleep_active, history, t)

    def _try_start(
        self, head: HeadSignal, eyes: EyesSignal, closed_for: float | None, history: History, t: float
    ) -> None:
        c = self.cfg
        assert head.pitch is not None and head.roll is not None
        pitch, roll = head.pitch, head.roll
        if head.yaw is not None and abs(head.yaw) > c.max_yaw_deg:
            self._hold_fwd.reset()
            self._hold_side.reset()
            return
        closed = closed_for is not None
        window = history.window(t, c.drift_window_s)
        span = window[-1].t - window[0].t if len(window) > 1 else 0.0
        fwd = self._hold_fwd.update(pitch <= -c.pitch_down_deg and closed, t)
        side = self._hold_side.update(abs(roll) >= c.roll_deg and closed, t)
        drift_ok = (
            closed_for is not None and closed_for >= c.drift_closed_s and span >= 0.95 * c.drift_window_s
        )

        start: tuple[str, str, float] | None = None  # direction, pattern, started_t
        if fwd == "on" or self._hold_fwd.active:
            since = self._hold_fwd.since or t
            before = history.window(since, c.sudden_drop_window_s + 0.05)
            fast = bool(before) and max(s.values[_PITCH] for s in before) - pitch >= c.sudden_drop_deg
            start = ("FORWARD", "SUDDEN_DROP" if fast else "HELD_DOWN", since)
        elif drift_ok:
            change = _steady_change(history, t, _PITCH, c.drift_window_s, c.drift_min_slope_dps)
            if change is not None and change <= -c.drift_min_deg:
                start = ("FORWARD", "SLOW_DRIFT", window[0].t)
        if start is None and (side == "on" or self._hold_side.active):
            start = (
                "SIDEWAYS_LEFT" if roll < 0 else "SIDEWAYS_RIGHT",
                "HELD_DOWN",
                self._hold_side.since or t,
            )
        if start is None and drift_ok:
            roll_change = _steady_change(history, t, _ROLL, c.drift_window_s, c.drift_min_slope_dps)
            if (
                roll_change is not None
                and abs(roll_change) >= c.roll_drift_deg
                and abs(roll) >= 0.5 * c.roll_deg
            ):
                start = ("SIDEWAYS_LEFT" if roll_change < 0 else "SIDEWAYS_RIGHT", "SLOW_DRIFT", window[0].t)
        if (
            start is None
            and pitch >= c.backward_pitch_deg
            and closed_for is not None
            and closed_for >= c.backward_closed_s
        ):
            start = ("BACKWARD", "HELD_DOWN", t - closed_for)
        if start is None:
            return

        self._direction, self._pattern, started_t = start
        before = history.window(started_t, 3.0) or history.window(t, 3.0)
        if self._direction == "FORWARD":
            self._start_value = max(s.values[_PITCH] for s in before)
        elif self._direction == "BACKWARD":
            self._start_value = min(s.values[_PITCH] for s in before)
        else:
            self._start_value = min(before, key=lambda s: abs(s.values[_ROLL])).values[_ROLL]
        self._closed_during = True  # every start condition requires closed eyes
        self._recover.reset()
        self.tracker.start(
            t, min(started_t, t), Severity.DANGER, {"direction": self._direction, "pattern": self._pattern}
        )
        self.falls.add(t, True)

    def _update_active(
        self, head: HeadSignal, eyes: EyesSignal, microsleep_active: bool, history: History, t: float
    ) -> None:
        c = self.cfg
        assert head.pitch is not None and head.roll is not None
        pitch, roll = head.pitch, head.roll
        closed = eyes.state is EyeState.CLOSED or microsleep_active
        if self._direction == "FORWARD":
            change = self._start_value - pitch
            velocity = head.pitch_velocity or 0.0
            still = velocity <= -c.still_falling_dps
        elif self._direction == "BACKWARD":
            change = pitch - self._start_value
            velocity = head.pitch_velocity or 0.0
            still = velocity >= c.still_falling_dps
        else:
            change = abs(roll - self._start_value)
            velocity = head.roll_velocity or 0.0
            still = abs(velocity) >= c.still_falling_dps and (velocity < 0) == (roll < 0)

        severity = Severity.CRITICAL if closed or change >= c.critical_drop_deg else Severity.DANGER
        trend_idx = _ROLL if self._direction.startswith("SIDEWAYS") else _PITCH
        trend_key = "roll_deg" if trend_idx == _ROLL else "pitch_deg"
        trend = []
        for ago in (3.0, 2.0, 1.0, 0.0):
            s = history.at(t - ago)
            trend.append({"ago_s": ago, trend_key: r(s.values[trend_idx], 1) if s else None})
        details: dict[str, object] = {
            "direction": self._direction,
            "pattern": self._pattern,
            "pitch_deg": r(pitch, 1),
            "roll_deg": r(roll, 1),
            "start_value_deg": r(self._start_value, 1),
            "change_deg": r(change, 1),
            "velocity_dps": r(velocity, 1),
            "still_falling": still,
            "trend": trend,
            "eyes_state": eyes.state.value,
            "eyes_openness": r(eyes.openness),
            "eyes_closed_during": self._closed_during,
            "head_falls_10min": self.count(t),
        }
        if self._eyes_open_since is not None and t - self._eyes_open_since >= c.end_eyes_open_s:
            self.tracker.end(t, self._eyes_open_since, {**details, "outcome": "EYES_REOPENED"})
            return
        back = pitch > -c.end_within_deg and pitch < c.backward_pitch_deg * 0.5 and abs(roll) < c.end_roll_deg
        if self._recover.update(back, t) == "on":
            self.tracker.end(t, self._recover.since, {**details, "outcome": "RECOVERED"})
            return
        self.tracker.update(t, details, severity)

    def emit(self) -> list[Detection]:
        return self.tracker.emit()
