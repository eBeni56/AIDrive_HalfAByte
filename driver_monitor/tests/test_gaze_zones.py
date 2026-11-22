"""Gaze angles -> zone with the default (full-screen simulator) layout."""

from __future__ import annotations

import pytest

from driver_monitor.common.enums import GazeZone
from driver_monitor.config import GazeConfig, GazeZonesConfig
from driver_monitor.signals.gaze import ZoneClassifier


def classifier() -> ZoneClassifier:
    return ZoneClassifier(GazeConfig(), GazeZonesConfig())


def zone(yaw: float, pitch: float) -> GazeZone:
    """The zone of a fresh look (no previous zone, so no hysteresis)."""
    return classifier().classify(yaw, pitch)


@pytest.mark.parametrize(
    ("yaw", "pitch"),
    [
        (-25, -10),  # just outside the road, lower left
        (-30, 0),  # eye level
        (-28, 12),  # a bit higher
        (-33, -35),  # deep into the lower left corner (the eyes do most of it)
        (-43, 18),  # head turned ~45 deg and a bit up (a real look at the mirror)
        (-45, 0),
        (-58, -5),  # far out, still the mirror
        (-22, -60),
    ],
)
def test_the_band_between_road_and_side_window_is_the_side_mirror(yaw: float, pitch: float) -> None:
    assert zone(yaw, pitch) is GazeZone.LEFT_MIRROR
    assert zone(-yaw, pitch) is GazeZone.RIGHT_MIRROR


@pytest.mark.parametrize(("yaw", "pitch"), [(-65, 0), (-75, -5), (-90, 10)])
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
    zc = classifier()
    assert zc.classify(-22, -3) is GazeZone.LEFT_MIRROR
    assert zc.classify(-18, -3) is GazeZone.LEFT_MIRROR  # 2 deg inside the road: kept
    assert zc.classify(-15, -3) is GazeZone.ROAD


def test_no_gap_between_a_mirror_and_the_road() -> None:
    # Leaving the mirror toward the road: the mirror is kept up to hysteresis_deg inside the road box, and
    # the road starts exactly there (both use the same hysteresis), so no other zone appears in between.
    zc = classifier()
    seen = {zc.classify(yaw / 10, 0.0) for yaw in range(-300, -100)}
    assert seen == {GazeZone.LEFT_MIRROR, GazeZone.ROAD}


def test_road_hysteresis_keeps_the_off_road_zone() -> None:
    zc = classifier()
    assert zc.classify(15, 40) is GazeZone.UP
    assert zc.classify(15, 13.5) is GazeZone.UP  # inside the road box, but not hysteresis_deg inside yet
    assert zc.classify(15, 11.5) is GazeZone.ROAD
