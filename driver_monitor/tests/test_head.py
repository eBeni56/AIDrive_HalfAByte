from __future__ import annotations

import itertools

from driver_monitor.common.enums import DetectionStatus, DetectionType, Severity
from driver_monitor.common.timeseries import History
from driver_monitor.config import HeadFallingConfig, NodConfig
from driver_monitor.detectors.base import Detection
from driver_monitor.detectors.head_falling import HeadFallingDetector
from driver_monitor.detectors.head_nod import HeadNodDetector
from tests.helpers import FPS, eyes, frames, head


def _nod(openness: float, start_pitch: float = 0.0) -> list[Detection]:
    det = HeadNodDetector(NodConfig())
    out: list[Detection] = []
    profile = [
        (1.0, start_pitch, 0.0),
        (0.4, start_pitch - 17.0, -45.0),
        (0.5, start_pitch, 34.0),
        (1.0, start_pitch, 0.0),
    ]  # (dur, end pitch, vel)
    t, pitch = 0.0, start_pitch
    since: float | None = None
    for dur, end_pitch, vel in profile:
        n = int(dur * FPS)
        for i, ft in enumerate(frames(t, dur)):
            p = pitch + (end_pitch - pitch) * (i + 1) / n
            # the eyes have the given openness during the dip, open otherwise
            o = openness if vel != 0.0 else 1.0
            since = (since if since is not None else ft) if o < 0.25 else None
            det.update(head(pitch=p, vpitch=vel), eyes(o, state_for=ft - since if since else 0.0), ft)
            out += det.emit()
        t, pitch = t + dur, end_pitch
    return out


def test_drowsy_nod_with_closed_eyes() -> None:
    ds = _nod(openness=0.1)
    assert [d.type for d in ds] == [DetectionType.HEAD_NOD]
    assert ds[0].status is DetectionStatus.ENDED and ds[0].details["drop_deg"] >= 15
    assert ds[0].details["eyes_closed_s"] >= 0.4


def test_nod_with_open_eyes_is_ignored() -> None:
    assert _nod(openness=1.0) == []


def test_glance_down_with_lowered_lids_is_not_a_nod() -> None:
    # looking down at something lowers the lids to ~0.4 with OPEN eyes (seen in real recordings)
    assert _nod(openness=0.4) == []


def test_coming_back_from_looking_up_is_not_a_nod() -> None:
    assert _nod(openness=0.1, start_pitch=25.0) == []


def _fall(
    pitch_of_t: list[tuple[float, float]],
    openness: float,
    duration: float = 6.0,
    yaw: float = 0.0,
    roll: float = 0.0,
) -> list[Detection]:
    det = HeadFallingDetector(HeadFallingConfig())
    hist = History(12.0)
    out: list[Detection] = []
    prev_p: float | None = None
    closed_since: float | None = None
    for ft in frames(0.0, duration):
        # piecewise-linear pitch profile
        p = pitch_of_t[-1][1]
        for (t0, p0), (t1, p1) in itertools.pairwise(pitch_of_t):
            if t0 <= ft <= t1:
                p = p0 + (p1 - p0) * (ft - t0) / (t1 - t0)
                break
        v = 0.0 if prev_p is None else (p - prev_p) * FPS
        prev_p = p
        hist.add(ft, yaw, p, roll)
        e = eyes(openness)
        if openness < 0.25:  # what EyeClosureDetector.closed_since would report
            closed_since = ft if closed_since is None else closed_since
        det.update(head(yaw=yaw, pitch=p, roll=roll, vpitch=v), e, closed_since, hist, False, ft)
        out += det.emit()
    return out


def test_slow_forward_drift_with_closed_eyes() -> None:
    ds = _fall([(0.0, 0.0), (1.0, 0.0), (4.5, -16.0), (6.0, -16.0)], openness=0.1)
    started = [d for d in ds if d.status is DetectionStatus.STARTED]
    assert len(started) == 1 and started[0].details["direction"] == "FORWARD"
    last = ds[-1]
    assert last.details["change_deg"] >= 12 and len(last.details["trend"]) == 4
    assert last.severity is Severity.CRITICAL


def test_looking_down_at_phone_with_lowered_lids_is_not_falling() -> None:
    # real recording: head 15-25 deg down, openness ~0.45-0.6 -> DISTRACTED, never HEAD_FALLING
    assert _fall([(0.0, 0.0), (1.0, 0.0), (4.5, -20.0), (6.0, -20.0)], openness=0.45) == []
    assert _fall([(0.0, 0.0), (0.5, -30.0), (6.0, -30.0)], openness=0.9) == []


def test_head_down_with_closed_eyes_is_critical() -> None:
    ds = _fall([(0.0, 0.0), (0.4, -25.0), (6.0, -25.0)], openness=0.05)
    assert ds and ds[-1].severity is Severity.CRITICAL
    assert ds[0].details["pattern"] == "SUDDEN_DROP"


def test_no_sideways_fall_when_head_turned_far() -> None:
    # near a profile view (turning to a side window) MediaPipe's roll/pitch are garbage
    assert _fall([(0.0, 0.0), (6.0, 0.0)], openness=0.1, yaw=40.0, roll=25.0) == []
