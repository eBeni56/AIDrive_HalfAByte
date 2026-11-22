"""Converts the internal signals and detections of one frame into the `DriverState` JSON model."""

from __future__ import annotations

from driver_monitor.common.enums import DetectionStatus, Severity
from driver_monitor.detectors.base import Detection, r
from driver_monitor.output.schema import DetectionOut, Eyes, Gaze, Head, Summary
from driver_monitor.signals.eyes import EyesSignal
from driver_monitor.signals.gaze import GazeSignal
from driver_monitor.signals.head import HeadSignal


def summary(detections: list[Detection]) -> Summary:
    active = [d for d in detections if d.status is not DetectionStatus.ENDED]
    return Summary(highest_severity=Severity.max(*(d.severity for d in active)), active=[d.type for d in active])


def detections_out(detections: list[Detection]) -> list[DetectionOut]:
    out = [
        DetectionOut(
            id=d.id,
            type=d.type,
            severity=d.severity,
            status=d.status,
            started_t=round(d.started_t, 3),
            ended_t=r(d.ended_t, 3),
            duration_s=round(d.duration_s, 2),
            details=d.details,
        )
        for d in detections
    ]
    # going on first, the most severe first
    return sorted(out, key=lambda d: (d.status is DetectionStatus.ENDED, -d.severity.rank))


def eyes(e: EyesSignal) -> Eyes:
    return Eyes(state=e.state, state_for_s=round(e.state_for_s, 2), openness=r(e.openness), source=e.source)


def head(h: HeadSignal) -> Head:
    return Head(yaw_deg=r(h.yaw, 1), pitch_deg=r(h.pitch, 1), roll_deg=r(h.roll, 1))


def gaze(g: GazeSignal) -> Gaze:
    return Gaze(zone=g.zone, zone_for_s=round(g.zone_for_s, 2), yaw_deg=r(g.yaw, 1), pitch_deg=r(g.pitch, 1))
