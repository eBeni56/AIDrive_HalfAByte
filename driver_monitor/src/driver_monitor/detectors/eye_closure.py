"""EYES_CLOSED: the eyes are closed longer than a blink.

One closure = eyes.state CLOSED continuously (the state has hysteresis, so it does not flicker).
  shorter than closed_s (0.5 s)  a blink: nothing
  >= closed_s                    EYES_CLOSED, WARNING
  >= danger_s (1.0 s)            the same episode becomes DANGER (microsleep)
It ends when the eyes stayed open for reopen_s (ended_t = the moment they opened), so a 1-2 frame
flicker of the reading does not split one closure into two. Short tracking gaps (eyes UNKNOWN, e.g. the
head nods forward) keep a closure alive for up to unknown_hold_s.
details: closed_for_s.
"""

from __future__ import annotations

from driver_monitor.common.enums import DetectionType, EyeState, Severity
from driver_monitor.config import EyeClosureConfig
from driver_monitor.detectors.base import Detection, EpisodeTracker, r
from driver_monitor.signals.eyes import EyesSignal


class EyeClosureDetector:
    def __init__(self, cfg: EyeClosureConfig) -> None:
        self.cfg = cfg
        self.tracker = EpisodeTracker(DetectionType.EYES_CLOSED)
        self._closed_since: float | None = None  # start of the current closure
        self._open_since: float | None = None  # eyes open again since (not confirmed yet)
        self._unknown_since: float | None = None

    def reset(self) -> None:
        self.tracker.reset()
        self._closed_since = self._open_since = self._unknown_since = None

    def update(self, eyes: EyesSignal, t: float) -> None:
        c = self.cfg
        start = self._closed_since
        if start is None and eyes.state is not EyeState.CLOSED:
            return  # eyes open (or unknown) and no closure going on

        if not eyes.reliable:  # tracking gap during a closure
            if self._unknown_since is None:
                self._unknown_since = t
            if t - self._unknown_since > c.unknown_hold_s:
                self._finish(t, self._unknown_since)
            else:
                self.tracker.update(t)
            return
        self._unknown_since = None

        if eyes.state is EyeState.CLOSED:
            self._open_since = None
            if start is None:
                start = self._closed_since = t
            closed_for = t - start
            severity = Severity.DANGER if closed_for >= c.danger_s else Severity.WARNING
            details = {"closed_for_s": r(closed_for)}
            if self.tracker.is_active:
                self.tracker.update(t, details, severity)
            elif closed_for >= c.closed_s:
                self.tracker.start(t, start, severity, details)
            return

        # open again: confirmed after reopen_s
        if self._open_since is None:
            self._open_since = t
        if t - self._open_since >= c.reopen_s:
            self._finish(t, self._open_since)
        else:
            self.tracker.update(t)

    def _finish(self, t: float, ended_t: float) -> None:
        if self.tracker.is_active and self._closed_since is not None:
            self.tracker.end(t, ended_t, {"closed_for_s": r(ended_t - self._closed_since)})
        self._closed_since = self._open_since = self._unknown_since = None

    def emit(self) -> list[Detection]:
        return self.tracker.emit()
