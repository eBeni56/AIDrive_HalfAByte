"""This driver's NORMAL eye opening while driving, and how narrow the eyes are right now relative to it.

Why not just the calibration value: people often open their eyes wider while "looking at the camera"
during calibration, and eye shapes differ a lot (narrow vs. wide eyes). The baseline is therefore learned
from the first `learn_s` seconds of clean driving samples (75th percentile - robust against blinks and
short narrowing), and every narrowing threshold is a RATIO to it. Nothing depends on absolute eye size.

Clean sample: eyes measured reliably and not closed, not just after a closure (lids still moving), head
near straight ahead, not looking down (lids follow the gaze), not smiling (cheeks push the lids) and not
yawning (eyes squeeze). Until the baseline is learned the calibrated open eye (1.0) is used.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from driver_monitor.common.enums import EyeState
from driver_monitor.config import EyeBaselineConfig, NarrowedEyesConfig
from driver_monitor.signals.eyes import EyesSignal
from driver_monitor.signals.head import HeadSignal


class EyeBaseline:
    def __init__(self, cfg: EyeBaselineConfig, narrowed: NarrowedEyesConfig) -> None:
        self.cfg, self.ncfg = cfg, narrowed
        self._learn: list[float] = []
        self._learned_s = 0.0
        self.value: float = 1.0
        self.learned = False
        self._recent: deque[tuple[float, float, float]] = deque()  # (t, openness, dt) clean samples
        self._last_t: float | None = None
        self._last_closed_t: float | None = None
        self.ratio: float | None = None

    def reset(self) -> None:
        self._learn.clear()
        self._learned_s = 0.0
        self.value, self.learned = 1.0, False
        self._recent.clear()
        self._last_t = self._last_closed_t = None
        self.ratio = None

    def _clean(self, eyes: EyesSignal, head: HeadSignal, yawning: bool, t: float) -> bool:
        c = self.cfg
        if eyes.state is EyeState.CLOSED:
            self._last_closed_t = t
            return False
        just_blinked = self._last_closed_t is not None and t - self._last_closed_t < c.after_blink_s
        head_ok = (
            head.valid
            and head.yaw is not None
            and head.pitch is not None
            and abs(head.yaw) <= c.max_head_angle_deg
            and abs(head.pitch) <= c.max_head_angle_deg
        )
        return (
            eyes.reliable
            and eyes.openness is not None
            and not just_blinked
            and head_ok
            and not eyes.looking_down
            and not eyes.smiling
            and not yawning
        )

    def update(self, eyes: EyesSignal, head: HeadSignal, yawning: bool, t: float) -> float | None:
        """Feed one frame; returns the current narrowing ratio (median openness / baseline) or None."""
        dt = min(t - self._last_t, 0.2) if self._last_t is not None else 0.0
        self._last_t = t
        n = self.ncfg
        if self._clean(eyes, head, yawning, t) and eyes.openness is not None:
            o = eyes.openness
            if not self.learned:
                self._learn.append(o)
                self._learned_s += dt
                if self._learned_s >= self.cfg.learn_s:
                    self.value = float(np.percentile(self._learn, self.cfg.percentile))
                    self.learned = True
                    self._learn.clear()
            self._recent.append((t, o, dt))
        while self._recent and t - self._recent[0][0] > n.window_s:
            self._recent.popleft()
        covered = sum(s[2] for s in self._recent)
        if covered >= n.min_valid_fraction * n.window_s and self.value > 0:
            self.ratio = float(np.median([s[1] for s in self._recent])) / self.value
        else:
            self.ratio = None
        return self.ratio
