"""Blink segmentation, EYES_CLOSED (longer than a blink) and MICROSLEEP (dangerously long closure).

One closure = eyes.state CLOSED continuously (state has hysteresis, so it does not flicker).
  < min_blink_s            glitch, ignored
  < eyes_closed_s (0.5 s)  normal blink -> blink statistics only
  >= eyes_closed_s         EYES_CLOSED (WARNING) while closed; if reopened before 1 s it was a long blink
  >= microsleep_s (1.0 s)  EYES_CLOSED ends (outcome ESCALATED_TO_MICROSLEEP) and MICROSLEEP (CRITICAL)
                           starts with the same started_t. It ends when the eyes stay not-closed for
                           reopen_confirm_s, so a brief flutter does not split one microsleep into two.
Short tracking gaps (eyes UNKNOWN) keep a closure alive for up to unknown_hold_s, and not-closed blips
shorter than flicker_s (landmark jitter) do not end it; the closure ends at the moment the eyes reopened.
"""

from __future__ import annotations

from dataclasses import dataclass

from driver_monitor.common.enums import DetectionType, EyeState, Severity
from driver_monitor.common.timeseries import EventLog
from driver_monitor.config import EyeClosureConfig
from driver_monitor.detectors.base import Detection, EpisodeTracker, r
from driver_monitor.signals.eyes import EyesSignal
from driver_monitor.signals.head import HeadSignal


@dataclass(slots=True)
class BlinkRecord:
    duration_s: float


class EyeClosureDetector:
    def __init__(self, cfg: EyeClosureConfig, microsleep_window_s: float = 600.0) -> None:
        self.cfg = cfg
        self.eyes_closed = EpisodeTracker(DetectionType.EYES_CLOSED)
        self.microsleep = EpisodeTracker(DetectionType.MICROSLEEP)
        self.blinks: EventLog[BlinkRecord] = EventLog(600.0)  # all completed closures < microsleep_s
        self.microsleeps: EventLog[float] = EventLog(max(microsleep_window_s, 600.0))
        self.microsleep_window_s = microsleep_window_s
        self.last_blink: tuple[float, float] | None = None  # (ended_t, duration_s)
        self._start: float | None = None
        self._min_open = 1.0
        self._unknown_since: float | None = None
        self._reopen_since: float | None = None

    def reset(self) -> None:
        self.eyes_closed.reset()
        self.microsleep.reset()
        self.blinks.clear()
        self.microsleeps.clear()
        self.last_blink = None
        self._start = None
        self._unknown_since = None
        self._reopen_since = None

    @property
    def closed_since(self) -> float | None:
        return self._start

    def microsleeps_in_window(self, t: float) -> int:
        return self.microsleeps.count(t, self.microsleep_window_s)

    def _details(self, eyes: EyesSignal, head: HeadSignal, t: float) -> dict[str, object]:
        closed_for = t - self._start if self._start is not None else 0.0
        return {
            "closed_for_s": r(closed_for),
            "min_openness": r(self._min_open),
            "left_openness": r(eyes.left.openness) if eyes.left else None,
            "right_openness": r(eyes.right.openness) if eyes.right else None,
            "head_pitch_deg": r(head.pitch, 1),
        }

    def update(self, eyes: EyesSignal, head: HeadSignal, head_falling: bool, t: float) -> None:
        self.blinks.prune(t)
        self.microsleeps.prune(t)
        c = self.cfg
        if not eyes.reliable:
            if self._start is None:
                return
            if self._unknown_since is None:
                self._unknown_since = t
            if t - self._unknown_since > c.unknown_hold_s:
                self._finish(t, self._unknown_since, "TRACKING_LOST")
            else:
                self._update_active(eyes, head, head_falling, t)
            return
        self._unknown_since = None

        if eyes.state is EyeState.CLOSED:
            self._reopen_since = None
            if self._start is None:
                self._start, self._min_open = t, 1.0
            if eyes.openness is not None:
                self._min_open = min(self._min_open, eyes.openness)
            closed_for = t - self._start
            if closed_for >= c.microsleep_s and not self.microsleep.is_active:
                if self.eyes_closed.is_active:
                    self.eyes_closed.end(
                        t,
                        details={
                            **self._details(eyes, head, t),
                            "seconds_until_microsleep": 0.0,
                            "outcome": "ESCALATED_TO_MICROSLEEP",
                        },
                    )
                self.microsleeps.add(t, closed_for)
                self.microsleep.start(t, self._start, Severity.CRITICAL, {"confirmed_t": round(t, 3)})
            elif (
                closed_for >= c.eyes_closed_s
                and not self.eyes_closed.is_active
                and not self.microsleep.is_active
            ):
                self.eyes_closed.start(
                    t,
                    self._start,
                    Severity.WARNING,
                    {"microsleep_threshold_s": c.microsleep_s, "outcome": None},
                )
            self._update_active(eyes, head, head_falling, t)
            return

        # eyes not closed: the closure ends once they stayed not-closed long enough (a 1-2 frame flicker of
        # the openness reading must not split one long closure into short ones)
        if self._start is None:
            return
        if self._reopen_since is None:
            self._reopen_since = t
        confirm = c.reopen_confirm_s if self.microsleep.is_active else c.flicker_s
        if t - self._reopen_since >= confirm:
            self._finish(t, self._reopen_since, "REOPENED")
        else:
            self._update_active(eyes, head, head_falling, t)

    def _update_active(self, eyes: EyesSignal, head: HeadSignal, head_falling: bool, t: float) -> None:
        if self._start is None:
            return
        d = self._details(eyes, head, t)
        if self.eyes_closed.is_active:
            left = max(self.cfg.microsleep_s - (t - self._start), 0.0)
            self.eyes_closed.update(t, {**d, "seconds_until_microsleep": r(left)})
        if self.microsleep.is_active:
            self.microsleep.update(
                t,
                {
                    **d,
                    "head_pitch_velocity_dps": r(head.pitch_velocity, 1),
                    "head_falling": head_falling,
                    "microsleeps_10min": self.microsleeps_in_window(t),
                },
            )

    def _finish(self, t: float, ended_t: float, outcome: str) -> None:
        assert self._start is not None
        duration = ended_t - self._start
        if self.microsleep.is_active:
            self.microsleep.end(t, ended_t, {"closed_for_s": r(duration), "outcome": outcome})
        else:
            if self.eyes_closed.is_active:
                self.eyes_closed.end(
                    t,
                    ended_t,
                    {"closed_for_s": r(duration), "seconds_until_microsleep": None, "outcome": outcome},
                )
            if outcome == "REOPENED" and duration >= self.cfg.min_blink_s:
                self.blinks.add(ended_t, BlinkRecord(duration))
                self.last_blink = (ended_t, duration)
        self._start = None
        self._reopen_since = None
        self._unknown_since = None

    def emit(self) -> list[Detection]:
        return self.eyes_closed.emit() + self.microsleep.emit()
