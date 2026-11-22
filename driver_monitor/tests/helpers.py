"""Builders for synthetic signals, so detector logic is tested without a camera or MediaPipe."""

from __future__ import annotations

from collections.abc import Iterator

from driver_monitor.common.enums import EyeState
from driver_monitor.config import EyesConfig
from driver_monitor.signals.eyes import EyesSignal

FPS = 30.0


def frames(start: float, duration: float, fps: float = FPS) -> Iterator[float]:
    n = round(duration * fps)
    for i in range(n):
        yield start + i / fps


def eyes(openness: float | None) -> EyesSignal:
    """None = not measurable (no face, head turned too far)."""
    if openness is None:
        return EyesSignal(EyeState.UNKNOWN, 0.0)
    closed = openness < EyesConfig().closed_enter
    return EyesSignal(EyeState.CLOSED if closed else EyeState.OPEN, 0.0, openness)
