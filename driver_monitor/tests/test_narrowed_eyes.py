"""Personal eye baseline + EYES_NARROWED: everything is relative to the driver's own normal opening."""

from __future__ import annotations

import pytest

from driver_monitor.common.enums import DetectionStatus, DetectionType, DrowsinessLevel, Severity
from driver_monitor.config import DrowsinessConfig, EyeBaselineConfig, NarrowedEyesConfig
from driver_monitor.detectors.base import Detection
from driver_monitor.detectors.narrowed import EyesNarrowedDetector
from driver_monitor.state.drowsiness import DrowsinessEstimator, DrowsinessInputs
from driver_monitor.state.eye_baseline import EyeBaseline
from driver_monitor.state.eye_stats import EyeStats
from driver_monitor.state.fatigue import FatigueLedger, FatigueSnapshot
from tests.helpers import eyes, frames, head


def _run(timeline: list[tuple[float, float]], **eye_kw: bool) -> tuple[EyeBaseline, list[Detection]]:
    """timeline: (duration, openness) with blinks every 4 s; returns the baseline and all detections."""
    base = EyeBaseline(EyeBaselineConfig(), NarrowedEyesConfig())
    det = EyesNarrowedDetector(NarrowedEyesConfig())
    out: list[Detection] = []
    t0 = 0.0
    for dur, openness in timeline:
        for ft in frames(t0, dur):
            blink = (ft % 4.0) < 0.15
            e = eyes(0.05 if blink else openness, **eye_kw)
            ratio = base.update(e, head(), False, ft)
            det.update(ratio, base.value, base.learned, ft)
            out += det.emit()
        t0 += dur
    return base, out


@pytest.mark.parametrize("normal", [0.8, 1.0, 1.2])  # narrow-eyed, average and wide-eyed drivers
def test_narrowing_is_relative_to_the_drivers_own_normal(normal: float) -> None:
    base, ds = _run([(40.0, normal), (12.0, 0.8 * normal), (6.0, normal)])
    assert base.learned and abs(base.value - normal) < 0.02
    started = [d for d in ds if d.status is DetectionStatus.STARTED]
    assert len(started) == 1 and started[0].type is DetectionType.EYES_NARROWED
    assert 45.0 <= started[0].started_t + started[0].duration_s <= 49.0  # ~5 s after the narrowing began
    assert ds[-1].status is DetectionStatus.ENDED


@pytest.mark.parametrize("normal", [0.8, 1.0, 1.2])
def test_normal_eyes_and_small_variations_never_trigger(normal: float) -> None:
    _, ds = _run([(40.0, normal), (3.0, 0.75 * normal), (10.0, 0.92 * normal), (3.0, 0.7 * normal)])
    assert ds == []


def test_looking_down_or_smiling_never_counts_as_narrowed() -> None:
    _, ds = _run([(40.0, 1.0)])
    assert ds == []
    _, ds = _run([(40.0, 0.5)], looking_down=True)
    assert ds == []
    _, ds = _run([(40.0, 0.5)], smiling=True)
    assert ds == []


def test_strong_or_long_narrowing_is_a_warning() -> None:
    _, ds = _run([(40.0, 1.0), (10.0, 0.6)])
    assert ds[-1].severity is Severity.WARNING
    _, ds = _run([(40.0, 1.0), (30.0, 0.8)])
    assert ds[-1].severity is Severity.WARNING
    _, ds = _run([(40.0, 1.0), (10.0, 0.8)])
    assert ds[-1].severity is Severity.INFO


def _stats() -> EyeStats:
    return EyeStats(0.03, 60.0, 0.92, 15.0, 180.0, 15, 0, False)


def test_narrowed_eyes_make_the_driver_slightly_drowsy_then_recover() -> None:
    est = DrowsinessEstimator(DrowsinessConfig())
    calm = FatigueSnapshot(0.0, {})
    levels = [
        est.update(DrowsinessInputs(_stats(), calm, 0.8, t - 50.0), float(t)).level for t in range(50, 80)
    ]
    assert levels[-1] is DrowsinessLevel.SLIGHTLY_DROWSY
    state = est.update(DrowsinessInputs(_stats(), calm, 0.8, 30.0), 80.0)
    assert "NARROWED_EYES" in state.contributing
    levels = [est.update(DrowsinessInputs(_stats(), calm), float(t)).level for t in range(81, 200)]
    assert levels[-1] is DrowsinessLevel.ALERT


def test_narrowed_episodes_enter_the_fatigue_history() -> None:
    ledger = FatigueLedger(DrowsinessConfig())
    ep = Detection("n-1", DetectionType.EYES_NARROWED, Severity.INFO, DetectionStatus.ENDED, 10.0, 40.0, 30.0)
    ledger.ingest([ep], 40.0)
    snap = ledger.snapshot(40.0)
    assert abs(snap.load_by_kind["EYES_NARROWED"] - 0.25) < 1e-6  # 0.5 per minute of narrowing
    assert snap.windows[0].narrowed_eyes == 1 and snap.windows[0].narrowed_eyes_s == 30.0
