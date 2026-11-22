"""Episodes: a detection that lives across many frames with a stable id.

Lifecycle per detection:  STARTED (first frame) -> ONGOING (following frames) -> ENDED (exactly one frame).
Detectors call start/update/end; the pipeline calls `emit()` once per frame, which returns what to
publish and advances STARTED -> ONGOING and drops ENDED episodes.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from driver_monitor.common.enums import DetectionStatus, DetectionType, Severity


@dataclass(slots=True)
class Detection:
    id: str
    type: DetectionType
    severity: Severity
    status: DetectionStatus
    started_t: float
    ended_t: float | None
    duration_s: float
    details: dict[str, Any] = field(default_factory=dict)


class EpisodeTracker:
    """Manages the (at most one) active episode of a detection type plus just-finished ones."""

    def __init__(self, dtype: DetectionType) -> None:
        self.dtype = dtype
        self._counter = 0
        self.active: Detection | None = None
        self._finished: list[Detection] = []

    @property
    def is_active(self) -> bool:
        return self.active is not None

    def reset(self) -> None:
        self.active = None
        self._finished.clear()

    def _new_id(self) -> str:
        self._counter += 1
        return f"{self.dtype.value.lower()}-{self._counter}"

    def start(self, now: float, started_t: float, severity: Severity, details: dict[str, Any]) -> None:
        assert self.active is None, "end the active episode first"
        self.active = Detection(
            self._new_id(),
            self.dtype,
            severity,
            DetectionStatus.STARTED,
            started_t,
            None,
            now - started_t,
            dict(details),
        )

    def update(self, now: float, details: dict[str, Any] | None = None, severity: Severity | None = None) -> None:
        """Keep the active episode going; its severity can only rise."""
        a = self.active
        if a is None:
            return
        a.duration_s = now - a.started_t
        if details:
            a.details.update(details)
        if severity is not None and severity.rank > a.severity.rank:
            a.severity = severity

    def end(self, now: float, ended_t: float | None = None, details: dict[str, Any] | None = None) -> None:
        a = self.active
        if a is None:
            return
        a.ended_t = ended_t if ended_t is not None else now
        a.duration_s = a.ended_t - a.started_t
        if details:
            a.details.update(details)
        a.status = DetectionStatus.ENDED
        self._finished.append(a)
        self.active = None

    def emit(self) -> list[Detection]:
        out = [copy.deepcopy(d) for d in self._finished]
        self._finished.clear()
        if self.active is not None:
            out.append(copy.deepcopy(self.active))
            self.active.status = DetectionStatus.ONGOING
        return out


def r(x: float | None, nd: int = 2) -> float | None:
    """Round for JSON output (None stays None)."""
    return None if x is None else round(float(x), nd)
