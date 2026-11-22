"""Scripted driver scenarios run through the REAL pipeline (no camera, no MediaPipe).

Each scenario is a timeline of synthetic per-frame measurements (eye openness, head angles, eye gaze, face
present or not) at 30 fps. They are converted to the same FaceFeatures the MediaPipe front-end produces and
fed to DriverMonitor.process_features(), so every downstream class (signals, detectors, JSON builder) runs
exactly as with a webcam. (The phone needs a real image: tests/test_phone.py covers it.)

Usage:
  uv run python tools/scenarios.py --list
  uv run python tools/scenarios.py eyes_closed            # print the captured messages
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from driver_monitor.calibration.profile import default_closed
from driver_monitor.config import AppConfig
from driver_monitor.output.schema import DriverState
from driver_monitor.perception.features import EyeFeatures, FaceFeatures, FrameFeatures
from driver_monitor.pipeline import DriverMonitor

FPS = 30.0
_PTS = np.zeros((478, 3), np.float32)

# Default "driver" values: openness 1 = normal open eye; angles in the schema convention.
BASE: dict[str, float] = {"openness": 1.0, "yaw": 0.0, "pitch": 0.0, "eye_yaw": 0.0, "eye_pitch": 0.0, "face": 1.0}


def synth_face(p: dict[str, float], cfg: AppConfig) -> FaceFeatures | None:
    """Inverse of the signal maths: produce raw features that the pipeline turns back into `p`."""
    if p["face"] < 0.5:
        return None
    c, g = cfg.calibration, cfg.gaze
    open_ear, open_b = c.default_open_ear, c.default_open_blink
    closed_ear, closed_b = default_closed(c, open_ear, open_b)
    o = p["openness"]
    ey, ep = p["eye_yaw"], p["eye_pitch"]
    # eye_yaw = -(w*Giris*dh + (1-w)*Gblend*blend_h): choose dh and blend_h that each give `ey`
    dh, blend_h = -ey / g.iris_yaw_gain_deg, -ey / g.blend_yaw_gain_deg
    dv, blend_v = ep / g.iris_pitch_gain_deg, ep / g.blend_pitch_gain_deg

    def eye() -> EyeFeatures:
        return EyeFeatures(
            ear=closed_ear + o * (open_ear - closed_ear),
            blink=open_b + (1.0 - o) * (closed_b - open_b),
            iris_h=0.5 + dh,
            iris_v=dv,
        )

    return FaceFeatures(
        left=eye(),
        right=eye(),
        blend_h=blend_h,
        blend_v=blend_v,
        yaw=p["yaw"],
        pitch=p["pitch"],
        roll=0.0,
        interocular_px=120.0,
        partially_out=False,
        pts=_PTS,
    )


@dataclass
class Script:
    """Timeline builder: hold() keeps values constant, ramp() changes them linearly."""

    steps: list[tuple[float, dict[str, float], bool]] = field(default_factory=list)

    def hold(self, dur: float, **params: float) -> Script:
        self.steps.append((dur, params, False))
        return self

    def ramp(self, dur: float, **targets: float) -> Script:
        self.steps.append((dur, targets, True))
        return self

    def frames(self) -> Iterator[tuple[float, dict[str, float]]]:
        state = dict(BASE)
        t = 0.0
        for dur, params, ramp in self.steps:
            start = dict(state)
            if not ramp:
                state.update(params)
            n = max(round(dur * FPS), 1)
            for i in range(n):
                if ramp:
                    a = (i + 1) / n
                    for k, v in params.items():
                        state[k] = start[k] + (v - start[k]) * a
                yield t, dict(state)
                t += 1.0 / FPS


Capture = tuple[str, Callable[[DriverState], bool]]


def at(t: float) -> Callable[[DriverState], bool]:
    return lambda s: s.t >= t - 1e-6


def on(dtype: str, status: str) -> Callable[[DriverState], bool]:
    return lambda s: any(d.type.value == dtype and d.status.value == status for d in s.detections)


@dataclass
class Scenario:
    name: str
    title: str
    script: Script
    captures: list[Capture]


def run(sc: Scenario, cfg: AppConfig | None = None) -> list[tuple[str, DriverState]]:
    cfg = cfg or AppConfig()
    mon = DriverMonitor(cfg, calibrate=False)
    pending = list(sc.captures)
    out: list[tuple[str, DriverState]] = []
    state: DriverState | None = None
    for t, p in sc.script.frames():
        feats = FrameFeatures(120.0, synth_face(p, cfg), 1 if p["face"] >= 0.5 else 0)
        state = mon.process_features(feats, t, latency_ms=12.0).state
        while pending and pending[0][1](state):
            out.append((pending.pop(0)[0], state))
    assert state is not None
    out += [(f"{label} (NOT REACHED)", state) for label, _ in pending]
    return out


# ---------------------------------------------------------------------------------------------- scenarios
SCENARIOS: list[Scenario] = [
    Scenario("normal", "Normal, attentive driving", Script().hold(3.0), [("after 3 s", at(2.9))]),
    Scenario(
        "blink",
        "A normal blink: the eyes are CLOSED for 0.15 s, which is not a detection",
        Script().hold(1.0).hold(0.15, openness=0.03).hold(1.0, openness=1.0),
        [("mid-blink", at(1.1)), ("after", at(2.0))],
    ),
    Scenario(
        "eyes_closed",
        "Eyes closed 0.7 s: EYES_CLOSED (WARNING) from 0.5 s, ended when they open",
        Script().hold(1.0).hold(0.7, openness=0.03).hold(1.0, openness=1.0),
        [("closed for 0.6 s", at(1.6)), ("reopened", on("EYES_CLOSED", "ENDED"))],
    ),
    Scenario(
        "microsleep",
        "Eyes closed 2.4 s: the same EYES_CLOSED episode becomes DANGER at 1 s",
        Script().hold(1.0).hold(2.4, openness=0.03).hold(1.0, openness=1.0),
        [("closed for 1.8 s", at(2.8)), ("reopened", on("EYES_CLOSED", "ENDED"))],
    ),
    Scenario(
        "look_down",
        "Looking down at a phone: DISTRACTED after 1 s, DANGER after 2.5 s",
        Script()
        .hold(1.0)
        .ramp(0.3, pitch=-24.5, eye_pitch=-21.5, openness=0.46)
        .hold(3.5)
        .ramp(0.3, pitch=0.0, eye_pitch=0.0, openness=1.0)
        .hold(1.0),
        [("1.5 s down", at(2.8)), ("3 s down", at(4.3)), ("back on the road", on("DISTRACTED", "ENDED"))],
    ),
    Scenario(
        "mirror_left",
        "Left-mirror check in the simulator, lower left corner (zone LEFT_MIRROR, no detection)",
        Script().hold(1.0).ramp(0.15, yaw=-16.0, eye_yaw=-12.0, pitch=-6.0, eye_pitch=-6.0).hold(1.5),
        [("looking at the mirror", at(2.2))],
    ),
    Scenario(
        "mirror_corner_right",
        "Right mirror: head a little right and down, the eyes deep into the lower right corner (RIGHT_MIRROR)",
        Script().hold(1.0).ramp(0.15, yaw=14.0, eye_yaw=16.0, pitch=-8.0, eye_pitch=-25.0).hold(1.5),
        [("looking at the corner", at(2.2))],
    ),
    Scenario(
        "rear_mirror",
        "Rear-view mirror check in the simulator (zone REAR_MIRROR, no detection)",
        Script().hold(1.0).ramp(0.2, yaw=2.0, pitch=8.0, eye_pitch=7.0).hold(1.5),
        [("in the mirror", at(2.2))],
    ),
    Scenario(
        "mirror_left_high",
        "Left mirror, higher up: at eye level, yaw -35 deg (zone LEFT_MIRROR)",
        Script().hold(1.0).ramp(0.2, yaw=-28.0, eye_yaw=-7.0).hold(1.5),
        [("at eye level", at(2.4))],
    ),
    Scenario(
        "look_side",
        "Looking out of the left side window for 3 s: head turned far (zone LEFT_SIDE, no detection)",
        Script().hold(1.0).ramp(0.2, yaw=-62.0).hold(3.0),
        [("3 s to the side", at(4.1))],
    ),
    Scenario(
        "shoulder_right",
        "Head turned 65 deg right: eyes not measurable, the gaze uses the head alone (RIGHT_SIDE)",
        Script().hold(1.0).ramp(0.3, yaw=65.0).hold(1.2),
        [("head at 65 deg", at(2.4))],
    ),
    Scenario(
        "face_lost",
        "The face leaves the camera: face_detected false, no detection",
        Script().hold(1.0).hold(2.5, face=0.0),
        [("2 s without a face", at(3.0))],
    ),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="scenario name (see --list)")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sc = next((s for s in SCENARIOS if s.name == a.name), None)
    if a.list or sc is None:
        for s in SCENARIOS:
            print(f"{s.name:16s} {s.title}")
        return 0 if a.list or not a.name else 1
    for label, state in run(sc):
        d: dict[str, Any] = state.model_dump(mode="json")
        print(f"--- {label}")
        print(json.dumps({k: d[k] for k in ("t", "summary", "eyes", "gaze", "detections")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
