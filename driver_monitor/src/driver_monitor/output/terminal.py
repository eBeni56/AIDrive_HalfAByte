"""Terminal reporter: one live status line (~5 Hz) + a highlighted line for every detection change.

Event lines are printed when a detection STARTS, when its severity changes and when it ENDS - so the terminal shows the history of what happened while the status line shows now.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from typing import TextIO

from driver_monitor.common.enums import DetectionStatus, DetectionType, Severity
from driver_monitor.config import OutputConfig
from driver_monitor.output.schema import DetectionOut, DriverState

_RESET = "[0m"
_COLORS = {
    Severity.NONE: "[32m",  # green
    Severity.WARNING: "[33m",  # yellow
    Severity.DANGER: "[31;1m",  # bright red
}
_MARK = {Severity.WARNING: "▲", Severity.DANGER: "■"}


def describe(d: DetectionOut) -> str:
    """Short human-readable explanation of a detection (used for event lines and the overlay)."""
    x = d.details
    match d.type:
        case DetectionType.EYES_CLOSED:
            return f"eyes closed for {x.get('closed_for_s')}s"
        case DetectionType.DISTRACTED:
            where = {"DOWN": "down", "UP": "up"}.get(str(x.get("zone")), "away")
            return f"looking {where} for {x.get('away_s')}s"
        case DetectionType.PHONE_USE:
            return f"phone in view for {x.get('seen_for_s')}s (confidence {x.get('score')})"
    return ""


class TerminalReporter:
    def __init__(self, cfg: OutputConfig, stream: TextIO | None = None) -> None:
        self.cfg = cfg
        self.out = stream or sys.stdout
        tty = self.out.isatty()
        self.color = cfg.color and tty
        self.in_place = cfg.status_in_place and tty  # piped/redirected output: scroll instead
        if self.color and os.name == "nt":
            os.system("")  # enables ANSI escape processing in the Windows console
        self._last_status_t = -1e9
        self._last_json_t = -1e9
        self._status_shown = False
        self._seen: dict[str, Severity] = {}
        self._last_cal_msg: str | None = None
        self.print_json = False

    def _c(self, text: str, sev: Severity) -> str:
        return f"{_COLORS[sev]}{text}{_RESET}" if self.color else text

    def _clear_status(self) -> None:
        if self._status_shown and self.in_place:
            self.out.write("\r\x1b[2K")
        self._status_shown = False

    def line(self, text: str) -> None:
        self._clear_status()
        self.out.write(text + "\n")
        self.out.flush()

    def report(self, s: DriverState) -> None:
        self._calibration(s)
        self._events(s)
        if self.print_json and s.t - self._last_json_t >= self.cfg.json_print_interval_s:
            self._last_json_t = s.t
            self.line(json.dumps(s.model_dump(mode="json"), indent=2))
        if s.t - self._last_status_t >= 1.0 / max(self.cfg.terminal_hz, 0.1):
            self._last_status_t = s.t
            self._status(s)

    def _calibration(self, s: DriverState) -> None:
        msg = s.calibration.message if s.calibration.status.value == "CALIBRATING" else None
        if msg and msg != self._last_cal_msg:
            self.line(self._c(f"[{s.t:8.1f}s] CALIBRATION  {msg}", Severity.WARNING))
        if self._last_cal_msg and not msg:
            done = s.calibration
            note = f"  ({done.message})" if done.message else ""
            self.line(self._c(f"[{s.t:8.1f}s] CALIBRATION  {done.status.value}.{note}", Severity.NONE))
        self._last_cal_msg = msg

    def _events(self, s: DriverState) -> None:
        for d in s.detections:
            key = d.id
            prev = self._seen.get(key)
            if d.status is DetectionStatus.ENDED:
                self._seen.pop(key, None)
                self.line(
                    self._c(f"[{s.t:8.1f}s] ✓ {'ENDED':9s}{d.type.value:<19s}— {describe(d)}", Severity.NONE)
                )
                continue
            if prev is None:
                verb = "started"
            elif prev is not d.severity:
                verb = "now"
            else:
                continue
            self._seen[key] = d.severity
            mark = _MARK.get(d.severity, " ")
            self.line(
                self._c(
                    f"[{s.t:8.1f}s] {mark} {d.severity.value:9s}{d.type.value:<19s}— {verb}: {describe(d)}",
                    d.severity,
                )
            )

    def _status(self, s: DriverState) -> None:
        e, g, q = s.eyes, s.gaze, s.quality
        sev = s.summary.highest_severity
        gaze = g.zone.value
        if g.yaw_deg is not None and g.pitch_deg is not None:
            gaze += f" ({g.yaw_deg:+.0f}°,{g.pitch_deg:+.0f}°)"
        openness = "" if e.openness is None else f" {e.openness:.2f}"
        active = " ".join(d.type.value for d in s.active_detections())
        fps = f"{q.fps:.0f}fps" + (f" (cam {q.camera_fps:.0f})" if q.camera_fps is not None else "")
        parts = [f"[{s.t:8.1f}s] {(sev.value if sev is not Severity.NONE else 'OK'):8s}"]
        if s.calibration.status.value == "CALIBRATING":
            parts.append(f"CALIBRATING {s.calibration.progress:.0%}")
        else:
            parts += [f"eyes {e.state.value}{openness}", f"gaze {gaze}"]
        parts.append(f"{fps} {q.latency_ms:.0f}ms")
        if active:
            parts.append(active)
        text = " | ".join(parts)
        if not self.in_place:
            self.line(self._c(text, sev))
            return
        width = shutil.get_terminal_size((160, 20)).columns - 1
        if len(text) > width:
            text = text[: max(width - 1, 10)] + "…"
        self._clear_status()
        self.out.write(self._c(text, sev))
        self.out.flush()
        self._status_shown = True

    def close(self) -> None:
        self._clear_status()
        self.out.flush()
