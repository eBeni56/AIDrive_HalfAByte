"""EYES_CLOSED episodes from a timeline of eye openness."""

from __future__ import annotations

from driver_monitor.common.enums import DetectionStatus, Severity
from driver_monitor.config import EyeClosureConfig
from driver_monitor.detectors.base import Detection
from driver_monitor.detectors.eye_closure import EyeClosureDetector
from tests.helpers import eyes, frames


def run(det: EyeClosureDetector, timeline: list[tuple[float, float | None]]) -> list[Detection]:
    """timeline: (duration_s, openness or None for 'unknown'). Returns every emitted detection."""
    out: list[Detection] = []
    t = 0.0
    for dur, o in timeline:
        for ft in frames(t, dur):
            det.update(eyes(o), ft)
            out.extend(det.emit())
        t += dur
    return out


def started_and_ended(ds: list[Detection]) -> list[DetectionStatus]:
    return [d.status for d in ds if d.status is not DetectionStatus.ONGOING]


def test_normal_blink_is_not_a_detection() -> None:
    assert run(EyeClosureDetector(EyeClosureConfig()), [(2.0, 1.0), (0.3, 0.05), (2.0, 1.0)]) == []


def test_long_blink_is_a_warning_that_ends_when_the_eyes_open() -> None:
    ds = run(EyeClosureDetector(EyeClosureConfig()), [(1.0, 1.0), (0.7, 0.05), (1.0, 1.0)])
    assert started_and_ended(ds) == [DetectionStatus.STARTED, DetectionStatus.ENDED]
    assert all(d.severity is Severity.WARNING for d in ds)
    assert abs(ds[0].started_t - 1.0) < 0.05  # when the eyes closed, not when the 0.5 s threshold was crossed
    assert 0.65 < ds[-1].duration_s < 0.75  # ends when the eyes opened, not reopen_s later


def test_long_closure_becomes_danger_in_the_same_episode() -> None:
    ds = run(EyeClosureDetector(EyeClosureConfig()), [(1.0, 1.0), (1.6, 0.03), (1.0, 1.0)])
    assert started_and_ended(ds) == [DetectionStatus.STARTED, DetectionStatus.ENDED]
    assert len({d.id for d in ds}) == 1
    assert ds[0].severity is Severity.WARNING and ds[-1].severity is Severity.DANGER
    assert 1.55 < ds[-1].details["closed_for_s"] < 1.65


def test_flicker_does_not_split_a_closure() -> None:
    ds = run(EyeClosureDetector(EyeClosureConfig()), [(0.5, 1.0), (1.0, 0.03), (0.1, 0.8), (1.0, 0.03), (1.0, 1.0)])
    assert started_and_ended(ds) == [DetectionStatus.STARTED, DetectionStatus.ENDED]


def test_short_tracking_gap_keeps_the_closure_alive() -> None:
    ds = run(EyeClosureDetector(EyeClosureConfig()), [(0.5, 1.0), (0.6, 0.03), (0.5, None), (0.6, 0.03), (1.0, 1.0)])
    assert started_and_ended(ds) == [DetectionStatus.STARTED, DetectionStatus.ENDED]
    assert ds[-1].duration_s > 1.6


def test_long_tracking_gap_ends_it() -> None:
    ds = run(EyeClosureDetector(EyeClosureConfig()), [(0.5, 1.0), (0.8, 0.03), (2.0, None)])
    assert started_and_ended(ds) == [DetectionStatus.STARTED, DetectionStatus.ENDED]
    assert 0.75 < ds[-1].duration_s < 0.85  # ended when the tracking was lost
