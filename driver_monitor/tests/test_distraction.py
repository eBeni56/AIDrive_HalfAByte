"""DISTRACTED episodes from a timeline of gaze zones."""

from __future__ import annotations

from driver_monitor.common.enums import DetectionStatus, GazeZone, Severity
from driver_monitor.config import DistractionConfig
from driver_monitor.detectors.base import Detection
from driver_monitor.detectors.distraction import DistractionDetector
from tests.helpers import frames

Z = GazeZone


def run(timeline: list[tuple[float, GazeZone]]) -> list[Detection]:
    det = DistractionDetector(DistractionConfig())
    out: list[Detection] = []
    t = 0.0
    for dur, zone in timeline:
        for ft in frames(t, dur):
            det.update(zone, ft)
            out.extend(det.emit())
        t += dur
    return out


def test_road_mirrors_and_side_windows_are_never_distraction() -> None:
    zones = [Z.ROAD, Z.LEFT_MIRROR, Z.REAR_MIRROR, Z.RIGHT_MIRROR, Z.LEFT_SIDE, Z.RIGHT_SIDE]
    assert run([(5.0, z) for z in zones]) == []


def test_closed_eyes_or_no_face_are_not_distraction() -> None:
    assert run([(1.0, Z.ROAD), (4.0, Z.UNKNOWN)]) == []


def test_a_short_look_down_is_fine() -> None:
    assert run([(1.0, Z.ROAD), (0.8, Z.DOWN), (2.0, Z.ROAD)]) == []


def test_looking_down_warning_then_danger_then_back() -> None:
    ds = run([(1.0, Z.ROAD), (3.0, Z.DOWN), (1.0, Z.ROAD)])
    assert [d.status for d in ds if d.status is not DetectionStatus.ONGOING] == [
        DetectionStatus.STARTED,
        DetectionStatus.ENDED,
    ]
    assert ds[0].severity is Severity.WARNING and ds[0].details["zone"] == "DOWN"
    assert abs(ds[0].started_t - 1.0) < 0.05
    assert ds[-1].severity is Severity.DANGER
    assert 2.95 < ds[-1].duration_s < 3.05  # ends when the gaze came back, not end_s later


def test_looking_up_counts_too() -> None:
    ds = run([(1.5, Z.UP)])
    assert ds and ds[0].details["zone"] == "UP"


def test_a_glance_back_shorter_than_end_s_keeps_one_episode() -> None:
    ds = run([(1.5, Z.DOWN), (0.3, Z.ROAD), (1.5, Z.DOWN)])
    assert [d.status for d in ds].count(DetectionStatus.STARTED) == 1
