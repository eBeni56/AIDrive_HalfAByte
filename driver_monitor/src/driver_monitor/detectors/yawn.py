"""YAWN: one continuous mouth opening that is either wide or long.

open      = MAR >= open_mar OR jawOpen >= open_jaw   (jawOpen ignored while the head is pitched down: it
            reads ~0.6 with a closed mouth when the chin is lowered)
opening   = consecutive open frames; dips shorter than dip_s do not split it (end_after_s once a yawn runs)
YAWN when the opening
  - reached WIDE (MAR >= wide_mar or jaw >= wide_jaw) and has lasted >= wide_min_s, or
  - reached MODERATE (MAR >= moderate_mar or jaw >= moderate_jaw) and has lasted >= moderate_min_s
    (natural yawns often do not open fully but last long).
Speech keeps opening and closing below open_mar most of the time, so it does not form one long opening.
started_t = when the mouth opened; the episode ends when the mouth has been below the open level for
end_after_s. Completed yawns are counted for the drowsiness score. The details carry the peak values,
how far the head tilted back (typical for a yawn) and whether the eyes squeezed.
"""

from __future__ import annotations

from driver_monitor.common.enums import DetectionType, Severity
from driver_monitor.common.timeseries import EventLog
from driver_monitor.config import YawnConfig
from driver_monitor.detectors.base import Detection, EpisodeTracker, r
from driver_monitor.signals.eyes import EyesSignal
from driver_monitor.signals.mouth import MouthSignal


class YawnDetector:
    _UNKNOWN_END_S = 1.0

    def __init__(self, cfg: YawnConfig, count_window_s: float = 300.0) -> None:
        self.cfg = cfg
        self.count_window_s = count_window_s
        self.tracker = EpisodeTracker(DetectionType.YAWN)
        self.yawns: EventLog[float] = EventLog(max(count_window_s, 900.0))
        self._open_since: float | None = None
        self._last_open_t = 0.0
        self._unknown_since: float | None = None
        self._max_jaw = self._max_mar = 0.0
        self._min_eyes = 1.0
        self._pitch0: float | None = None
        self._max_pitch: float | None = None
        self._jaw_ignored = False

    def reset(self) -> None:
        self.tracker.reset()
        self.yawns.clear()
        self._open_since = self._unknown_since = None

    @property
    def active(self) -> bool:
        return self.tracker.is_active

    def count(self, t: float) -> int:
        return self.yawns.count(t, self.count_window_s)

    def update(self, mouth: MouthSignal, eyes: EyesSignal, head_pitch: float | None, t: float) -> None:
        self.yawns.prune(t)
        c = self.cfg
        if mouth.jaw_open is None or mouth.mar is None:
            if self._unknown_since is None:
                self._unknown_since = t
            if self.tracker.is_active and t - self._unknown_since >= self._UNKNOWN_END_S:
                self._end(t, self._unknown_since)
            elif not self.tracker.is_active:
                self._open_since = None
            return
        self._unknown_since = None
        jaw_ok = head_pitch is None or head_pitch > c.ignore_jaw_below_pitch_deg
        jaw = mouth.jaw_open if jaw_ok else 0.0
        mar = mouth.mar
        is_open = mar >= c.open_mar or jaw >= c.open_jaw

        if self._open_since is None:
            if is_open:
                self._open_since, self._last_open_t = t, t
                self._max_jaw, self._max_mar, self._min_eyes = jaw, mar, 1.0
                self._pitch0 = self._max_pitch = head_pitch
                self._jaw_ignored = not jaw_ok
            return

        if is_open:
            self._last_open_t = t
            self._max_jaw, self._max_mar = max(self._max_jaw, jaw), max(self._max_mar, mar)
            if head_pitch is not None:
                self._max_pitch = head_pitch if self._max_pitch is None else max(self._max_pitch, head_pitch)
            self._jaw_ignored |= not jaw_ok
        if eyes.openness is not None:
            self._min_eyes = min(self._min_eyes, eyes.openness)

        gap = t - self._last_open_t
        if not self.tracker.is_active:
            if gap > c.dip_s:  # closed again before it qualified: talking, a short "oh", ...
                self._open_since = None
                return
            duration = self._last_open_t - self._open_since
            wide = self._max_mar >= c.wide_mar or self._max_jaw >= c.wide_jaw
            moderate = self._max_mar >= c.moderate_mar or self._max_jaw >= c.moderate_jaw
            if (wide and duration >= c.wide_min_s) or (moderate and duration >= c.moderate_min_s):
                self.tracker.start(t, self._open_since, Severity.INFO, {})
        if self.tracker.is_active:
            if gap >= c.end_after_s:
                self._end(t, self._last_open_t, jaw, mar)
            else:
                self.tracker.update(t, self._details(jaw, mar, t))

    def _details(self, jaw: float | None, mar: float | None, t: float) -> dict[str, object]:
        tilt = (
            self._max_pitch - self._pitch0
            if self._max_pitch is not None and self._pitch0 is not None
            else None
        )
        return {
            "jaw_open": r(jaw),
            "max_jaw_open": r(self._max_jaw),
            "mar": r(mar),
            "max_mar": r(self._max_mar),
            "open_s": r(self._last_open_t - (self._open_since or self._last_open_t)),
            "head_tilt_back_deg": r(tilt, 1),
            "jaw_ignored_head_down": self._jaw_ignored,
            "yawns_5min": self.count(t),
            "eyes_squeezed": self._min_eyes < self.cfg.eyes_squeezed_below,
        }

    def _end(self, t: float, ended_t: float, jaw: float | None = None, mar: float | None = None) -> None:
        self.yawns.add(ended_t, ended_t - (self._open_since or ended_t))
        self.tracker.end(t, ended_t, self._details(jaw, mar, t))
        self._open_since = None

    def emit(self) -> list[Detection]:
        return self.tracker.emit()
