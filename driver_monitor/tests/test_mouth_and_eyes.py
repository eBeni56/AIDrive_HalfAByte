from __future__ import annotations

from driver_monitor.common.enums import DetectionStatus, DetectionType
from driver_monitor.config import AlmostClosedConfig, EyeClosureConfig, EyesConfig, YawnConfig
from driver_monitor.detectors.almost_closed import EyesAlmostClosedDetector
from driver_monitor.detectors.yawn import YawnDetector
from tests.helpers import eyes, frames, mouth


def test_talking_is_not_a_yawn() -> None:
    det = YawnDetector(YawnConfig())
    t = 0.0
    for _ in range(20):  # open 0.3 s / closed 0.2 s, like (loud, wide) speech
        for ft in frames(t, 0.3):
            det.update(mouth(0.45, 0.5), eyes(1.0), 0.0, ft)
        t += 0.3
        for ft in frames(t, 0.2):
            det.update(mouth(0.05, 0.05), eyes(1.0), 0.0, ft)
        t += 0.2
        assert det.emit() == []
    assert det.count(t) == 0


def test_mouth_resting_slightly_open_is_not_a_yawn() -> None:
    det = YawnDetector(YawnConfig())
    for ft in frames(0.0, 6.0):
        det.update(mouth(0.25, 0.25), eyes(1.0), 0.0, ft)  # breathing through the mouth
    assert det.emit() == []


def test_jaw_open_ignored_when_head_is_down() -> None:
    # real recording: looking down at a phone, mouth closed, jawOpen reads 0.65 (chin compressed)
    det = YawnDetector(YawnConfig())
    for ft in frames(0.0, 4.0):
        det.update(mouth(0.65, 0.1), eyes(0.5), -20.0, ft)
    assert det.emit() == []


def test_wide_yawn_starts_quickly_and_is_counted() -> None:
    det = YawnDetector(YawnConfig())
    ds = []
    for ft in frames(0.0, 4.0):
        det.update(mouth(0.8, 0.95), eyes(0.4), 0.0, ft)
        ds += det.emit()
    for ft in frames(4.0, 1.0):
        det.update(mouth(0.05, 0.05), eyes(1.0), 0.0, ft)
        ds += det.emit()
    started = [d for d in ds if d.status is DetectionStatus.STARTED]
    assert len(started) == 1 and 0.35 < started[0].duration_s < 0.5 and started[0].started_t == 0.0
    ended = [d for d in ds if d.status is DetectionStatus.ENDED]
    assert len(ended) == 1 and ended[0].details["eyes_squeezed"] is True
    assert det.count(5.0) == 1


def test_natural_moderate_yawn_is_detected() -> None:
    # never fully open (MAR peaks at ~0.5), but one long opening of ~3 s with the head tilting back
    det = YawnDetector(YawnConfig())
    ds = []
    for ft in frames(0.0, 3.0):
        mar = 0.32 + 0.13 * min(ft, 3.0 - ft)  # rises to ~0.45 and falls again
        det.update(mouth(0.3, mar), eyes(0.8), 6.0 * ft, ft)
        ds += det.emit()
    for ft in frames(3.0, 1.0):
        det.update(mouth(0.02, 0.05), eyes(1.0), 0.0, ft)
        ds += det.emit()
    assert [d.status for d in ds if d.status is not DetectionStatus.ONGOING] == [
        DetectionStatus.STARTED,
        DetectionStatus.ENDED,
    ]
    assert ds[-1].details["head_tilt_back_deg"] > 5


def _almost(states: list[tuple[float, float, dict[str, bool]]], yawning: bool = False) -> list[DetectionType]:
    det = EyesAlmostClosedDetector(AlmostClosedConfig(), EyesConfig(), EyeClosureConfig())
    out: list[DetectionType] = []
    t = 0.0
    for dur, o, flags in states:
        for ft in frames(t, dur):
            det.update(eyes(o, **flags), yawning, ft)
            out += [d.type for d in det.emit() if d.status is DetectionStatus.STARTED]
        t += dur
    return out


def test_heavy_eyelids_detected_after_two_seconds() -> None:
    assert _almost([(1.0, 1.0, {}), (1.9, 0.35, {})]) == []
    assert _almost([(1.0, 1.0, {}), (2.5, 0.35, {})]) == [DetectionType.EYES_ALMOST_CLOSED]


def test_blinks_inside_heavy_eyelids_do_not_break_it() -> None:
    timeline = [(1.0, 0.35, {}), (0.15, 0.05, {}), (1.2, 0.35, {})]
    assert _almost(timeline) == [DetectionType.EYES_ALMOST_CLOSED]


def test_guards_looking_down_smiling_yawning() -> None:
    assert _almost([(4.0, 0.35, {"looking_down": True})]) == []
    assert _almost([(4.0, 0.35, {"smiling": True})]) == []
    assert _almost([(4.0, 0.35, {})], yawning=True) == []
