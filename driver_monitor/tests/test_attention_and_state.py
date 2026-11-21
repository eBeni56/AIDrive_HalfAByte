from __future__ import annotations

from driver_monitor.common.enums import DetectionStatus, DrowsinessLevel, Severity
from driver_monitor.common.timeseries import History, TimeWeightedWindow
from driver_monitor.config import DistractionConfig, DrowsinessConfig, FaceVisibilityConfig
from driver_monitor.detectors.base import Detection
from driver_monitor.detectors.distraction import DistractionDetector
from driver_monitor.detectors.face_visibility import FaceVisibilityDetector
from driver_monitor.signals.gaze import GazeSignal
from driver_monitor.state.attention import AttentionTracker
from driver_monitor.state.drowsiness import DrowsinessEstimator, DrowsinessInputs
from driver_monitor.state.eye_stats import EyeStats
from driver_monitor.state.fatigue import FatigueSnapshot
from tests.helpers import frames, gaze


def _distraction(timeline: list[tuple[float, GazeSignal]]) -> list[Detection]:
    att, det = AttentionTracker(DistractionConfig()), DistractionDetector(DistractionConfig())
    out: list[Detection] = []
    t = 0.0
    for dur, g in timeline:
        for ft in frames(t, dur):
            det.update(att.update(g, ft), ft)
            out += det.emit()
        t += dur
    return out


def test_mirror_check_is_not_distraction() -> None:
    assert _distraction([(2.0, gaze(0)), (0.8, gaze(-34)), (3.0, gaze(0))]) == []


def test_long_glance_down_warning_then_danger() -> None:
    ds = _distraction([(1.0, gaze(0)), (4.2, gaze(5, -45)), (2.0, gaze(0))])
    started = next(d for d in ds if d.status is DetectionStatus.STARTED)
    assert started.details["reason"] == "LONG_GLANCE" and started.severity is Severity.WARNING
    assert abs(started.started_t - 1.0) < 0.1
    assert any(d.severity is Severity.DANGER for d in ds)
    ended = ds[-1]
    assert ended.status is DetectionStatus.ENDED and ended.details["direction"] == "DOWN"


def test_closed_eyes_do_not_count_as_distraction() -> None:
    unknown = GazeSignal()  # eyes closed / face lost -> gaze unknown
    assert (
        _distraction([(1.0, gaze(0)), (1.5, gaze(30)), (3.0, unknown), (0.3, gaze(30)), (2.0, gaze(0))]) == []
    )


def test_cumulative_short_glances() -> None:
    timeline = [(1.7, gaze(25, -30)), (0.6, gaze(0))] * 8
    ds = _distraction(timeline)
    started = [d for d in ds if d.status is DetectionStatus.STARTED]
    assert started and started[0].details["reason"] == "CUMULATIVE"


def test_face_lost_after_turning_left() -> None:
    det = FaceVisibilityDetector(FaceVisibilityConfig())
    hist = History(12.0)
    from driver_monitor.common.enums import EyeState
    from driver_monitor.perception.features import FaceFeatures

    class _F:  # minimal stand-in: only .center is read
        center = (0.4, 0.5)

    t = 0.0
    for i, ft in enumerate(frames(0.0, 1.0)):
        hist.add(ft, -2.0 * i, 0.0, 0.0)  # turning left fast
        det.update(_F(), EyeState.OPEN, False, hist, ft)  # type: ignore[arg-type]
        t = ft
    ds: list[Detection] = []
    for ft in frames(t + 0.033, 2.5):
        det.update(None, EyeState.UNKNOWN, False, hist, ft)
        ds += det.emit()
    assert ds and ds[0].details["likely_cause"] == "TURNED_AWAY_LEFT"
    assert FaceFeatures  # import check


def test_perclos_time_weighted_with_gaps() -> None:
    w = TimeWeightedWindow(60.0, 0.3)
    t = 0.0
    for ft in frames(0.0, 30.0):
        w.add(ft, 0.1 if 10 <= ft < 16 else 1.0)  # 6 s of closed eyes out of 30 s
        t = ft
    for ft in frames(t + 1 / 30, 10.0):
        w.add(ft, None)  # gap - must not count
    frac = w.fraction_at_or_below(0.2)
    assert frac is not None and abs(frac - 0.2) < 0.01


def _stats(perclos: float | None = 0.03) -> EyeStats:
    return EyeStats(perclos, 60.0, 0.92, 15.0, 180.0, 15, 0, False)


def _load(load: float, kind: str = "MICROSLEEP") -> FatigueSnapshot:
    return FatigueSnapshot(load, {kind: load} if load else {})


def test_single_event_is_corrected_and_leaves_no_memory() -> None:
    est = DrowsinessEstimator(DrowsinessConfig())
    levels = [est.update(DrowsinessInputs(_stats(), _load(1.1)), float(t)).level for t in range(0, 30)]
    assert levels[-1] is DrowsinessLevel.SLIGHTLY_DROWSY  # one ~2 s closure: slightly drowsy, not more
    # the load has faded (the ledger halves it every 5 min): the level comes back to ALERT
    levels = [est.update(DrowsinessInputs(_stats(), _load(0.3)), float(t)).level for t in range(30, 200)]
    assert levels[-1] is DrowsinessLevel.ALERT


def test_fatigue_memory_only_after_a_high_level() -> None:
    est = DrowsinessEstimator(DrowsinessConfig())
    for t in range(0, 60):
        s = est.update(DrowsinessInputs(_stats(), _load(4.0)), float(t))
    assert s.level is DrowsinessLevel.SEVERELY_DROWSY
    levels = [est.update(DrowsinessInputs(_stats(), _load(0.0)), float(t)).level for t in range(60, 900, 5)]
    assert DrowsinessLevel.VERY_DROWSY in levels  # one step at a time ...
    assert levels[-1] is DrowsinessLevel.DROWSY  # ... and never below peak - 2 (fatigue memory)


def test_high_perclos_alone_reaches_drowsy() -> None:
    est = DrowsinessEstimator(DrowsinessConfig())
    s = None
    for t in range(0, 60):
        s = est.update(DrowsinessInputs(_stats(perclos=0.16), _load(0.0)), float(t))
    assert s is not None and s.level is DrowsinessLevel.DROWSY and "HIGH_PERCLOS" in s.contributing
