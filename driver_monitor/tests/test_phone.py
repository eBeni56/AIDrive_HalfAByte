"""PHONE_USE episodes from a timeline of phone sightings (no model: the sightings are given)."""

from __future__ import annotations

from driver_monitor.common.enums import DetectionStatus, Severity
from driver_monitor.config import PhoneConfig
from driver_monitor.detectors.base import Detection
from driver_monitor.detectors.phone import PhoneDetector
from driver_monitor.perception.phone_finder import PhoneSighting
from tests.helpers import frames

DETECT_DT = 1 / 6  # the object detector delivers ~6 results per second


def run(det: PhoneDetector, timeline: list[tuple[float, float | None]]) -> list[Detection]:
    """timeline: (duration_s, phone score or None = no phone). Frames at 30 fps; a new sighting every
    DETECT_DT while a phone is visible (in between the last one is repeated, as PhoneFinder.latest does)."""
    out: list[Detection] = []
    t = 0.0
    sighting: PhoneSighting | None = None
    for dur, score in timeline:
        for ft in frames(t, dur):
            if score is None:
                sighting = None
            elif sighting is None or ft - sighting.t >= DETECT_DT:
                sighting = PhoneSighting(ft, score, (100, 200, 60, 110))
            det.update(sighting, ft)
            out.extend(det.emit())
        t += dur
    return out


def statuses(ds: list[Detection]) -> list[DetectionStatus]:
    return [d.status for d in ds if d.status is not DetectionStatus.ONGOING]


def test_short_glimpse_is_not_a_detection() -> None:
    assert run(PhoneDetector(PhoneConfig()), [(1.0, None), (0.3, 0.6), (2.0, None)]) == []


def test_phone_in_hand_starts_and_ends() -> None:
    ds = run(PhoneDetector(PhoneConfig()), [(1.0, None), (2.0, 0.7), (2.0, None)])
    assert statuses(ds) == [DetectionStatus.STARTED, DetectionStatus.ENDED]
    started = ds[0]
    assert started.severity is Severity.WARNING
    assert abs(started.started_t - 1.0) < 0.05  # from the first sighting, not from the 0.4 s threshold
    assert started.details["box"] == {"x": 100, "y": 200, "w": 60, "h": 110}
    ended = ds[-1]
    assert 1.8 < ended.duration_s < 2.05  # ends at the last sighting, not end_s later


def test_long_use_becomes_danger() -> None:
    ds = run(PhoneDetector(PhoneConfig()), [(4.0, 0.5)])
    assert ds[-1].severity is Severity.DANGER


def test_flicker_shorter_than_end_s_keeps_one_episode() -> None:
    ds = run(PhoneDetector(PhoneConfig()), [(1.0, 0.6), (0.5, None), (1.0, 0.6), (0.5, None), (1.0, 0.6)])
    assert statuses(ds) == [DetectionStatus.STARTED]
    assert ds[-1].duration_s > 3.5


def test_a_phone_seen_at_time_zero_is_measured_from_zero() -> None:
    # t = 0.0 is a valid first sighting (a video file starts there); it used to be read as "no time"
    ds = run(PhoneDetector(PhoneConfig()), [(2.0, 0.7), (2.0, None)])
    assert statuses(ds) == [DetectionStatus.STARTED, DetectionStatus.ENDED]
    assert ds[0].started_t == 0.0
    assert 1.7 < ds[-1].details["seen_for_s"] <= 2.0  # was negative: measured from the end instead
