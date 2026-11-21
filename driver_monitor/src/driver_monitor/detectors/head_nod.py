"""HEAD_NOD: a quick drowsy drop of the head followed by a catch ("nodding off").

1. Drop: within drop_window_s the pitch falls >= drop_deg below its recent maximum, at a peak speed
   of >= min_velocity_dps (downward). The maximum must be a normal head position
   (start_min_pitch_deg..start_max_pitch_deg): coming back from looking up is not a nod.
2. Recovery: the head comes back up by >= recover_fraction of the drop within recover_within_s.
3. It only counts if the eyes were MEASURED CLOSED for >= min_closed_s during the drop (longer than the blink
   that accompanies a gaze shift) and OPEN for at most max_open_fraction of it. A glance down at a phone
   lowers the lids (openness ~0.4-0.6) with open eyes, and a "yes" nod keeps the eyes open; eyes that
   cannot be measured (head too far down/turned) never count as closed.
Reported once, when the head comes back (status ENDED). A drop that does not recover is handled by
HEAD_FALLING instead. DANGER if the eyes were closed >= danger_closed_s during the drop, else WARNING.
"""

from __future__ import annotations

from driver_monitor.common.enums import DetectionType, EyeState, Severity
from driver_monitor.common.timeseries import EventLog, History
from driver_monitor.config import NodConfig
from driver_monitor.detectors.base import Detection, EpisodeTracker, r
from driver_monitor.signals.eyes import EyesSignal
from driver_monitor.signals.head import HeadSignal


class HeadNodDetector:
    def __init__(self, cfg: NodConfig) -> None:
        self.cfg = cfg
        self.tracker = EpisodeTracker(DetectionType.HEAD_NOD)
        self.nods: EventLog[float] = EventLog(max(cfg.window_s, 600.0))
        self._hist = History(cfg.drop_window_s + 0.2)  # (t, pitch, pitch_velocity)
        self._down = False
        self._start_t = self._start_pitch = 0.0
        self._lowest = self._lowest_t = 0.0
        self._max_speed = 0.0
        self._open: list[float] = []
        self._closed_s = self._open_s = self._dip_s = 0.0
        self._last_t: float | None = None

    def reset(self) -> None:
        self.tracker.reset()
        self.nods.clear()
        self._hist.clear()
        self._down = False
        self._last_t = None

    def count(self, t: float) -> int:
        return self.nods.count(t, self.cfg.window_s)

    def update(self, head: HeadSignal, eyes: EyesSignal, t: float) -> None:
        self.nods.prune(t)
        c = self.cfg
        dt = t - self._last_t if self._last_t is not None else 0.0
        self._last_t = t
        if not head.valid or head.pitch is None:
            self._down = False
            self._hist.clear()
            return
        pitch, vel = head.pitch, head.pitch_velocity or 0.0
        self._hist.add(t, pitch, vel)

        if not self._down:
            if head.yaw is not None and abs(head.yaw) > c.max_yaw_deg:
                return
            window = self._hist.window(t, c.drop_window_s)
            top = max(window, key=lambda s: s.values[0])
            fastest = min(s.values[1] for s in window)
            normal_start = c.start_min_pitch_deg <= top.values[0] <= c.start_max_pitch_deg
            if normal_start and top.values[0] - pitch >= c.drop_deg and -fastest >= c.min_velocity_dps:
                self._down = True
                self._start_t, self._start_pitch = top.t, top.values[0]
                self._lowest, self._lowest_t = pitch, t
                self._max_speed = fastest
                # the drop is recognised part-way down: count the time since it began (top.t) as well
                self._dip_s = t - top.t
                closed_before = (
                    min(eyes.state_for_s, self._dip_s)
                    if eyes.reliable and eyes.state is EyeState.CLOSED
                    else 0.0
                )
                self._open, self._closed_s, self._open_s = [], closed_before, 0.0
                dt = 0.0  # this frame is already included in _dip_s
            else:
                return

        # in a drop: track the lowest point and the eyes (measured values only)
        if pitch < self._lowest:
            self._lowest, self._lowest_t = pitch, t
        self._max_speed = min(self._max_speed, vel)
        if eyes.reliable and eyes.openness is not None:
            self._open.append(eyes.openness)
        if eyes.reliable and eyes.state is EyeState.CLOSED:
            self._closed_s += dt
        if eyes.reliable and eyes.state is EyeState.OPEN:
            self._open_s += dt
        self._dip_s += dt
        drop = self._start_pitch - self._lowest
        recovered = pitch >= self._lowest + c.recover_fraction * drop
        if recovered:
            self._down = False
            mean_open = sum(self._open) / len(self._open) if self._open else None
            mostly_shut = self._open_s <= c.max_open_fraction * max(self._dip_s, 1e-6)
            if t - self._lowest_t <= c.recover_within_s and self._closed_s >= c.min_closed_s and mostly_shut:
                self.nods.add(t, drop)
                self.tracker.instant(
                    self._start_t,
                    t,
                    Severity.DANGER if self._closed_s >= c.danger_closed_s else Severity.WARNING,
                    {
                        "start_pitch_deg": r(self._start_pitch, 1),
                        "lowest_pitch_deg": r(self._lowest, 1),
                        "drop_deg": r(drop, 1),
                        "max_drop_velocity_dps": r(self._max_speed, 1),
                        "drop_time_s": r(self._lowest_t - self._start_t),
                        "recovery_s": r(t - self._lowest_t),
                        "recovered_fraction": r((pitch - self._lowest) / drop if drop > 0 else 1.0),
                        "mean_eye_openness": r(mean_open),
                        "eyes_closed_s": r(self._closed_s),
                        "eyes_open_fraction": r(self._open_s / self._dip_s if self._dip_s > 0 else 0.0),
                        "eyes_closed_during": True,
                        "head_nods_5min": self.count(t),
                    },
                )
        elif t - self._lowest_t > c.recover_within_s:
            self._down = False  # did not come back: not a nod (HEAD_FALLING territory)

    def emit(self) -> list[Detection]:
        return self.tracker.emit()
