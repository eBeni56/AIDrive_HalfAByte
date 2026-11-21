"""FatigueLedger: events from the detection stream -> decaying load + 1/5/15 min history."""

from __future__ import annotations

from driver_monitor.common.enums import DetectionStatus, DetectionType, Severity
from driver_monitor.config import DrowsinessConfig
from driver_monitor.detectors.base import Detection
from driver_monitor.state.fatigue import FatigueLedger, curve


def _det(kind: DetectionType, status: DetectionStatus, started: float, **details: object) -> Detection:
    return Detection("x", kind, Severity.WARNING, status, started, None, 0.0, dict(details))


def test_closure_weight_grows_with_duration_without_a_cliff_at_one_second() -> None:
    c = DrowsinessConfig().closure_weight_curve
    assert curve(0.5, c) < curve(0.95, c) < curve(1.05, c) < curve(2.0, c) < curve(5.0, c)
    assert curve(1.05, c) - curve(0.95, c) < 0.1


def test_single_closure_decays_with_half_life() -> None:
    led = FatigueLedger(DrowsinessConfig())
    led.ingest([_det(DetectionType.MICROSLEEP, DetectionStatus.STARTED, 10.0, closed_for_s=1.0)], 11.0)
    led.ingest([_det(DetectionType.MICROSLEEP, DetectionStatus.ENDED, 10.0, closed_for_s=2.0)], 12.0)
    w = curve(2.0, DrowsinessConfig().closure_weight_curve)
    assert abs(led.snapshot(10.0).load - w) < 1e-6
    assert abs(led.snapshot(310.0).load - w / 2) < 1e-6  # half-life 5 min
    one_min = led.snapshot(70.0).windows[0]
    assert one_min.microsleeps == 1 and one_min.long_eye_closures == 1 and one_min.closed_eyes_s == 2.0
    assert led.snapshot(80.0).windows[0].microsleeps == 0  # older than 1 min ...
    assert led.snapshot(80.0).windows[1].microsleeps == 1  # ... but still in the 5 min history


def test_escalated_closure_is_counted_once() -> None:
    led = FatigueLedger(DrowsinessConfig())
    led.ingest(
        [
            _det(DetectionType.EYES_CLOSED, DetectionStatus.ENDED, 5.0, outcome="ESCALATED_TO_MICROSLEEP"),
            _det(DetectionType.MICROSLEEP, DetectionStatus.STARTED, 5.0, closed_for_s=1.0),
        ],
        6.0,
    )
    assert led.snapshot(6.0).windows[0].long_eye_closures == 1


def test_long_blinks_stack_up() -> None:
    led = FatigueLedger(DrowsinessConfig())
    for k in range(10):  # 0.8 s closures every 30 s
        t = 30.0 * k
        led.ingest(
            [_det(DetectionType.EYES_CLOSED, DetectionStatus.ENDED, t, outcome="REOPENED", closed_for_s=0.8)],
            t + 1,
        )
    snap = led.snapshot(300.0)
    assert snap.load > 2.0 and snap.windows[1].long_eye_closures == 10 and snap.windows[1].microsleeps == 0
