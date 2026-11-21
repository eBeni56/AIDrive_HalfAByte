"""Builders for synthetic signals, so detector logic is tested without a camera or MediaPipe."""

from __future__ import annotations

from collections.abc import Iterator

from driver_monitor.common.enums import (
    EyeSource,
    EyeState,
    EyeTrend,
    GazeDirection,
    Horizontal,
    Intensity,
    Vertical,
)
from driver_monitor.signals.eyes import EyesSignal
from driver_monitor.signals.gaze import GazeSignal
from driver_monitor.signals.head import HeadSignal
from driver_monitor.signals.mouth import MouthSignal

FPS = 30.0


def frames(start: float, duration: float, fps: float = FPS) -> Iterator[float]:
    n = round(duration * fps)
    for i in range(n):
        yield start + i / fps


def eyes(
    openness: float | None,
    state: EyeState | None = None,
    state_for: float = 0.0,
    reliable: bool = True,
    looking_down: bool = False,
    smiling: bool = False,
) -> EyesSignal:
    if openness is None or not reliable:
        return EyesSignal(False, EyeState.UNKNOWN, state_for)
    if state is None:
        state = (
            EyeState.CLOSED
            if openness < 0.25
            else EyeState.ALMOST_CLOSED
            if openness < 0.55
            else EyeState.OPEN
        )
    return EyesSignal(
        True,
        state,
        state_for,
        openness,
        0.0,
        EyeTrend.STABLE,
        EyeSource.BOTH,
        lid_lowered_by_gaze=looking_down,
        smiling=smiling,
        looking_down=looking_down,
    )


def head(
    yaw: float = 0.0,
    pitch: float = 0.0,
    roll: float = 0.0,
    vyaw: float = 0.0,
    vpitch: float = 0.0,
    vroll: float = 0.0,
) -> HeadSignal:
    return HeadSignal(True, yaw, pitch, roll, vyaw, vpitch, vroll)


def gaze(yaw: float, pitch: float = 0.0, on_road: bool | None = None) -> GazeSignal:
    if on_road is None:
        on_road = abs(yaw) <= 20 and -15 <= pitch <= 15
    h = Horizontal.LEFT if yaw <= -12 else Horizontal.RIGHT if yaw >= 12 else Horizontal.CENTER
    v = Vertical.DOWN if pitch <= -10 else Vertical.UP if pitch >= 12 else Vertical.CENTER
    return GazeSignal(
        GazeDirection.combine(h, v), h, v, Intensity.MODERATE, yaw, pitch, yaw, pitch, 0.0, 0.0, on_road, 0.0
    )


def mouth(jaw: float, mar: float) -> MouthSignal:
    return MouthSignal(jaw, mar)
