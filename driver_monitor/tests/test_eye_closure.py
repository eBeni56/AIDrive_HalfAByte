from __future__ import annotations

from driver_monitor.common.enums import DetectionStatus, DetectionType, Severity
from driver_monitor.config import EyeClosureConfig
from driver_monitor.detectors.base import Detection
from driver_monitor.detectors.eye_closure import EyeClosureDetector
from tests.helpers import eyes, frames, head


def run(det: EyeClosureDetector, timeline: list[tuple[float, float | None]]) -> list[Detection]:
    """timeline: (duration_s, openness or None for 'unknown'). Returns every emitted detection."""
    out: list[Detection] = []
    t = 0.0
    for dur, o in timeline:
        for ft in frames(t, dur):
            det.update(eyes(o), head(), False, ft)
            out.extend(det.emit())
        t += dur
    return out


def types_with_status(ds: list[Detection], status: DetectionStatus) -> list[DetectionType]:
    return [d.type for d in ds if d.status is status]


def test_normal_blink_is_not_a_detection() -> None:
    det = EyeClosureDetector(EyeClosureConfig())
    ds = run(det, [(2.0, 1.0), (0.2, 0.05), (2.0, 1.0)])
    assert ds == []
    assert len(list(det.blinks.since(4.2, 60))) == 1
    assert det.last_blink is not None and 0.15 < det.last_blink[1] < 0.25


def test_long_blink_raises_eyes_closed_and_ends_reopened() -> None:
    det = EyeClosureDetector(EyeClosureConfig())
    ds = run(det, [(1.0, 1.0), (0.7, 0.05), (1.0, 1.0)])
    assert types_with_status(ds, DetectionStatus.STARTED) == [DetectionType.EYES_CLOSED]
    ended = [d for d in ds if d.status is DetectionStatus.ENDED]
    assert len(ended) == 1 and ended[0].details["outcome"] == "REOPENED"
    assert 0.65 < ended[0].duration_s < 0.75
    assert det.microsleeps_in_window(3.0) == 0


def test_microsleep_escalation_keeps_start_time() -> None:
    det = EyeClosureDetector(EyeClosureConfig())
    ds = run(det, [(1.0, 1.0), (1.6, 0.03), (1.0, 1.0)])
    started = [d for d in ds if d.status is DetectionStatus.STARTED]
    assert [d.type for d in started] == [DetectionType.EYES_CLOSED, DetectionType.MICROSLEEP]
    ec_end = next(d for d in ds if d.type is DetectionType.EYES_CLOSED and d.status is DetectionStatus.ENDED)
    assert ec_end.details["outcome"] == "ESCALATED_TO_MICROSLEEP"
    ms = [d for d in ds if d.type is DetectionType.MICROSLEEP]
    assert all(d.severity is Severity.CRITICAL for d in ms)
    assert abs(ms[0].started_t - 1.0) < 0.05  # when the eyes closed, not when the threshold was crossed
    ms_end = ms[-1]
    assert ms_end.status is DetectionStatus.ENDED and 1.55 < ms_end.duration_s < 1.65
    assert det.microsleeps_in_window(4.0) == 1


def test_brief_flutter_does_not_split_a_microsleep() -> None:
    det = EyeClosureDetector(EyeClosureConfig())
    ds = run(det, [(0.5, 1.0), (1.5, 0.03), (0.1, 0.8), (1.0, 0.03), (1.0, 1.0)])
    assert types_with_status(ds, DetectionStatus.STARTED).count(DetectionType.MICROSLEEP) == 1


def test_short_tracking_gap_keeps_closure_alive() -> None:
    det = EyeClosureDetector(EyeClosureConfig())
    ds = run(det, [(0.5, 1.0), (0.6, 0.03), (0.5, None), (0.6, 0.03), (1.0, 1.0)])
    assert DetectionType.MICROSLEEP in types_with_status(ds, DetectionStatus.STARTED)


def test_every_episode_is_started_then_ongoing_then_ended_once() -> None:
    det = EyeClosureDetector(EyeClosureConfig())
    ds = [d for d in run(det, [(0.5, 1.0), (2.0, 0.03), (1.0, 1.0)]) if d.type is DetectionType.MICROSLEEP]
    statuses = [d.status for d in ds]
    assert statuses[0] is DetectionStatus.STARTED
    assert statuses[-1] is DetectionStatus.ENDED
    assert statuses.count(DetectionStatus.STARTED) == 1 and statuses.count(DetectionStatus.ENDED) == 1
    assert len({d.id for d in ds}) == 1
