"""FACE_NOT_VISIBLE: fail-safe when the driver's face cannot be tracked.

"No face" must never be read as "driver is fine". After lost_after_s without a face the episode starts,
with a guess of why, from the last known pose (the `context_s` before the face was lost):
  TURNED_AWAY_LEFT / TURNED_AWAY_RIGHT  last yaw far to a side or turning fast
  HEAD_DROPPED                          last pitch far down / dropping fast, or HEAD_FALLING was active
  LEFT_FRAME                            the face was near the image border
  OCCLUDED                              lost in the middle of the image with a normal pose (hand, object)
  UNKNOWN                               never seen
WARNING; DANGER after danger_after_s or if the head dropped. Ends when the face is back for recover_s.
"""

from __future__ import annotations

from driver_monitor.common.enums import DetectionType, EyeState, Severity
from driver_monitor.common.timeseries import History
from driver_monitor.config import FaceVisibilityConfig
from driver_monitor.detectors.base import Detection, EpisodeTracker, r
from driver_monitor.perception.features import FaceFeatures


class FaceVisibilityDetector:
    def __init__(self, cfg: FaceVisibilityConfig) -> None:
        self.cfg = cfg
        self.tracker = EpisodeTracker(DetectionType.FACE_NOT_VISIBLE)
        self._first_t: float | None = None
        self._last_seen: float | None = None
        self._last_center: tuple[float, float] | None = None
        self._last_eyes = EyeState.UNKNOWN
        self._head_falling_at_loss = False
        self._back_since: float | None = None
        self._details: dict[str, object] = {}

    def reset(self) -> None:
        self.tracker.reset()
        self._first_t = self._last_seen = self._back_since = None

    def lost_for(self, t: float) -> float:
        if self._last_seen is None:
            return t - self._first_t if self._first_t is not None else 0.0
        return max(0.0, t - self._last_seen)

    def update(
        self, face: FaceFeatures | None, eyes_state: EyeState, head_falling: bool, history: History, t: float
    ) -> None:
        c = self.cfg
        if self._first_t is None:
            self._first_t = t
        if face is not None:
            self._last_seen, self._last_center = t, face.center
            self._last_eyes = eyes_state
            self._head_falling_at_loss = head_falling
            if self.tracker.is_active:
                if self._back_since is None:
                    self._back_since = t
                if t - self._back_since >= c.recover_s:
                    self.tracker.end(
                        t, self._back_since, {"lost_for_s": r(self._back_since - (self._last_lost_t()))}
                    )
                    self._back_since = None
            return
        self._back_since = None
        lost = self.lost_for(t)
        if not self.tracker.is_active and lost >= c.lost_after_s:
            self._details = self._cause(history)
            severity = (
                Severity.DANGER if self._details["likely_cause"] == "HEAD_DROPPED" else Severity.WARNING
            )
            self.tracker.start(t, t - lost, severity, self._details)
        if self.tracker.is_active:
            severity = Severity.DANGER if lost >= c.danger_after_s else Severity.WARNING
            self.tracker.update(t, {"lost_for_s": r(lost)}, severity)

    def _last_lost_t(self) -> float:
        a = self.tracker.active
        return a.started_t if a is not None else 0.0

    def _cause(self, history: History) -> dict[str, object]:
        c = self.cfg
        if self._last_seen is None:
            return {
                "last_seen_t": None,
                "last_head": None,
                "last_face_center": None,
                "likely_cause": "UNKNOWN",
            }
        recent = history.window(self._last_seen, c.context_s)
        last = recent[-1] if recent else None
        yaw = pitch = roll = 0.0
        vyaw = vpitch = 0.0
        if last is not None:
            yaw, pitch, roll = last.values
            first = recent[0]
            span = last.t - first.t
            if span > 0.05:
                vyaw = (last.values[0] - first.values[0]) / span
                vpitch = (last.values[1] - first.values[1]) / span
        cx, cy = self._last_center or (0.5, 0.5)
        m = c.edge_margin
        if yaw <= -c.turned_yaw_deg or vyaw <= -c.turned_yaw_velocity_dps:
            cause = "TURNED_AWAY_LEFT"
        elif yaw >= c.turned_yaw_deg or vyaw >= c.turned_yaw_velocity_dps:
            cause = "TURNED_AWAY_RIGHT"
        elif (
            pitch <= c.dropped_pitch_deg
            or vpitch <= c.dropped_pitch_velocity_dps
            or self._head_falling_at_loss
        ):
            cause = "HEAD_DROPPED"
        elif cx < m or cx > 1 - m or cy < m or cy > 1 - m:
            cause = "LEFT_FRAME"
        else:
            cause = "OCCLUDED"
        return {
            "last_seen_t": round(self._last_seen, 3),
            "last_head": {"yaw_deg": r(yaw, 1), "pitch_deg": r(pitch, 1), "roll_deg": r(roll, 1)}
            if last is not None
            else None,
            "last_yaw_velocity_dps": r(vyaw, 1),
            "last_pitch_velocity_dps": r(vpitch, 1),
            "last_face_center": {"x": r(cx), "y": r(cy)},
            "last_eyes_state": self._last_eyes.value,
            "likely_cause": cause,
        }

    def emit(self) -> list[Detection]:
        return self.tracker.emit()
