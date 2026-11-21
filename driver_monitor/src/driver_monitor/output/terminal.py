"""Terminal reporter: one live status line (~5 Hz) + a highlighted line for every detection change.

Event lines are printed when a detection STARTS, when its severity changes (or a DROWSY level changes)
and when it ENDS - so the terminal shows the history of what happened while the status line shows now.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from typing import Any, TextIO

from driver_monitor.common.enums import DetectionStatus, DetectionType, Severity
from driver_monitor.config import OutputConfig
from driver_monitor.output.schema import DetectionOut, DriverState

_RESET = "\x1b[0m"
_COLORS = {
    Severity.NONE: "\x1b[32m",  # green
    Severity.INFO: "\x1b[36m",  # cyan
    Severity.WARNING: "\x1b[33m",  # yellow
    Severity.DANGER: "\x1b[31;1m",  # bright red
    Severity.CRITICAL: "\x1b[97;41;1m",  # white on red
}
_MARK = {Severity.INFO: "●", Severity.WARNING: "▲", Severity.DANGER: "▲", Severity.CRITICAL: "■"}


def _f(x: Any, fmt: str = "+.0f", none: str = "  ?") -> str:
    return none if x is None else format(x, fmt)


def describe(d: DetectionOut) -> str:
    """Short human-readable explanation of a detection (used for event lines and the overlay)."""
    x = d.details
    ended = d.status is DetectionStatus.ENDED
    match d.type:
        case DetectionType.EYES_CLOSED:
            if ended:
                return f"{d.duration_s:.2f}s ({x.get('outcome')})"
            return f"eyes closed {d.duration_s:.2f}s (microsleep in {x.get('seconds_until_microsleep')}s)"
        case DetectionType.MICROSLEEP:
            n = x.get("microsleeps_10min")
            return (
                f"{d.duration_s:.2f}s total ({n} in last 10 min)"
                if ended
                else f"eyes closed {d.duration_s:.2f}s"
            )
        case DetectionType.EYES_ALMOST_CLOSED:
            return f"openness {x.get('openness')} for {d.duration_s:.1f}s"
        case DetectionType.TIRED_EYES:
            return (
                f"{', '.join(x.get('reasons') or []) or 'fading'} (mean openness {x.get('mean_openness_60s')}, "
                f"blink {x.get('mean_blink_duration_ms')} ms)"
            )
        case DetectionType.YAWN:
            return f"{d.duration_s:.1f}s ({x.get('yawns_5min')} in last 5 min)"
        case DetectionType.HEAD_NOD:
            closed = ", eyes closed" if x.get("eyes_closed_during") else ""
            return f"head dropped {x.get('drop_deg')}° in {x.get('drop_time_s')}s and came back{closed}"
        case DetectionType.HEAD_FALLING:
            still = ", still falling" if x.get("still_falling") else ""
            return (
                f"{x.get('direction')} {x.get('pattern')}: pitch {x.get('pitch_deg')}°, roll {x.get('roll_deg')}° "
                f"(moved {x.get('change_deg')}° in {d.duration_s:.1f}s{still}) eyes {x.get('eyes_state')}"
            )
        case DetectionType.DISTRACTED:
            if x.get("reason") == "CUMULATIVE":
                return (
                    f"{x.get('off_road_last_30s_s')}s off-road in the last 30s (looking {x.get('direction')})"
                )
            return (
                f"looking {x.get('direction')} for {x.get('longest_glance_s')}s "
                f"(gaze {x.get('gaze_yaw_deg')}°, {x.get('gaze_pitch_deg')}°)"
            )
        case DetectionType.FACE_NOT_VISIBLE:
            return f"no face for {x.get('lost_for_s')}s, likely {x.get('likely_cause')}"
        case DetectionType.DROWSY:
            return (
                f"{x.get('level')} score {x.get('score')} ({', '.join(x.get('contributing') or []) or '-'})"
            )
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
        self._seen: dict[str, tuple[Severity, Any]] = {}
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
            self.line(self._c(f"[{s.t:8.1f}s] CALIBRATION  {msg}", Severity.INFO))
        if self._last_cal_msg and not msg:
            done = s.calibration
            nh = done.neutral_head
            extra = f" neutral head yaw {nh.yaw_deg} pitch {nh.pitch_deg} roll {nh.roll_deg}" if nh else ""
            self.line(
                self._c(
                    f"[{s.t:8.1f}s] CALIBRATION  {done.status.value}.{extra}"
                    f"{'  (' + done.message + ')' if done.message else ''}",
                    Severity.NONE,
                )
            )
        self._last_cal_msg = msg

    def _events(self, s: DriverState) -> None:
        for d in s.detections:
            key = d.id
            level = d.details.get("level")
            prev = self._seen.get(key)
            if d.status is DetectionStatus.ENDED:
                self._seen.pop(key, None)
                self.line(
                    self._c(f"[{s.t:8.1f}s] ✓ {'ENDED':9s}{d.type.value:<19s}— {describe(d)}", Severity.NONE)
                )
                continue
            if prev is None:
                verb = "started"
            elif prev[0] is not d.severity or prev[1] != level:
                verb = "now"
            else:
                continue
            self._seen[key] = (d.severity, level)
            mark = _MARK.get(d.severity, " ")
            self.line(
                self._c(
                    f"[{s.t:8.1f}s] {mark} {d.severity.value:9s}{d.type.value:<19s}— {verb}: {describe(d)}",
                    d.severity,
                )
            )

    def _status(self, s: DriverState) -> None:
        e, h, g, m, dr, a = s.eyes, s.head, s.gaze, s.mouth, s.drowsiness, s.attention
        sev = s.summary.highest_severity
        head = f"y{_f(h.yaw_deg)} p{_f(h.pitch_deg)} r{_f(h.roll_deg)}"
        gaze = f"{g.direction.value}"
        if g.yaw_deg is not None:
            strength = "" if g.intensity.value == "NONE" else f" {g.intensity.value.lower()}"
            gaze += f"{strength} ({_f(g.yaw_deg)}°,{_f(g.pitch_deg)}°)"
        if g.on_road is False:
            gaze += f" {a.eyes_off_road_s:.1f}s"
        active = " ".join(f"{d.type.value}" for d in s.active_detections())
        parts = [
            f"[{s.t:8.1f}s] {(sev.value if sev is not Severity.NONE else 'OK'):8s}",
            f"eyes {e.state.value} {_f(e.openness, '.2f', '-')}",
            f"head {head}",
            f"gaze {gaze}",
            f"mouth {m.state.value}",
            f"drowsy {dr.level.value} {dr.score:.2f}" + (" (warming up)" if dr.warming_up else ""),
            f"attn {a.state.value}",
            f"{s.quality.fps:.0f}fps"
            + (f" (cam {s.quality.camera_fps:.0f})" if s.quality.camera_fps is not None else "")
            + f" {s.quality.latency_ms:.0f}ms",
        ]
        if s.calibration.status.value == "CALIBRATING":
            parts = [
                parts[0],
                f"CALIBRATING {s.calibration.progress:.0%}",
                parts[2],
                f"{s.quality.fps:.0f}fps",
            ]
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
