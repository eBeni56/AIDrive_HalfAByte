"""Gaze angles -> zone with the default (full-screen simulator) layout."""

from __future__ import annotations

import pytest

from driver_monitor.common.enums import GazeZone
from driver_monitor.config import GazeConfig, GazeZonesConfig
from driver_monitor.signals.gaze import ZoneClassifier


def on_road(yaw: float, pitch: float) -> bool:
    g = GazeConfig()
    return abs(yaw) <= g.on_road_yaw_deg and -g.on_road_pitch_down_deg <= pitch <= g.on_road_pitch_up_deg


def zone(yaw: float, pitch: float) -> GazeZone:
    return ZoneClassifier(GazeConfig(), GazeZonesConfig()).classify(yaw, pitch, on_road(yaw, pitch))


@pytest.mark.parametrize(
    ("yaw", "pitch"),
    [
        (-25, -10),  # just outside the road, lower left
        (-30, 0),  # eye level
        (-28, 12),  # a bit higher
        (-40, -35),  # deep into the lower left corner (the eyes do most of it)
        (-54, -5),  # far out, still the mirror
        (-22, -60),
    ],
)
def test_the_band_between_road_and_side_window_is_the_side_mirror(yaw: float, pitch: float) -> None:
    assert zone(yaw, pitch) is GazeZone.LEFT_MIRROR
    assert zone(-yaw, pitch) is GazeZone.RIGHT_MIRROR


@pytest.mark.parametrize(("yaw", "pitch"), [(-60, 0), (-75, -5), (-58, 10)])
def test_far_turned_head_is_the_side_window(yaw: float, pitch: float) -> None:
    assert zone(yaw, pitch) is GazeZone.LEFT_SIDE
    assert zone(-yaw, pitch) is GazeZone.RIGHT_SIDE


@pytest.mark.parametrize(
    ("yaw", "pitch", "expected"),
    [
        (0, 0, GazeZone.ROAD),
        (-15, 0, GazeZone.ROAD),  # front left: unchanged
        (-18, -8, GazeZone.ROAD),
        (15, -3, GazeZone.ROAD),  # front right: unchanged
        (-15, -20, GazeZone.DOWN),
        (0, -30, GazeZone.DOWN),
        (6, -46, GazeZone.DOWN),
        (2, 15, GazeZone.REAR_MIRROR),
        (0, 30, GazeZone.UP),
    ],
)
def test_road_down_up_and_rear_mirror_are_unchanged(yaw: float, pitch: float, expected: GazeZone) -> None:
    assert zone(yaw, pitch) is expected


def test_a_zone_is_kept_near_its_border() -> None:
    zc = ZoneClassifier(GazeConfig(), GazeZonesConfig())
    assert zc.classify(-22, -3, on_road(-22, -3)) is GazeZone.LEFT_MIRROR
    assert zc.classify(-18, -3, on_road(-18, -3)) is GazeZone.LEFT_MIRROR  # 2 deg inside the road: kept
    assert zc.classify(-15, -3, on_road(-15, -3)) is GazeZone.ROAD
