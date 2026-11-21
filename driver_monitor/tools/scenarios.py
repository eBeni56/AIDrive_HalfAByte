"""Scripted driver scenarios run through the REAL pipeline (no camera, no MediaPipe).

Each scenario is a timeline of synthetic per-frame measurements (eye openness, head angles, eye gaze,
mouth opening, face present or not) at 30 fps. They are converted to the same FaceFeatures the
MediaPipe front-end produces and fed to DriverMonitor.process_features(), so every downstream class
(signals, windows, detectors, JSON builder) runs exactly as with a webcam.

Usage:
  uv run python tools/scenarios.py --list
  uv run python tools/scenarios.py microsleep            # print the captured messages (relevant blocks)
  uv run python tools/scenarios.py microsleep --full     # complete messages
  uv run python tools/scenarios.py --markdown > out.md   # all scenarios as Markdown (used for the docs)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from driver_monitor.config import AppConfig
from driver_monitor.output.schema import DriverState
from driver_monitor.perception.features import EyeFeatures, FaceFeatures, FrameFeatures
from driver_monitor.pipeline import DriverMonitor

FPS = 30.0
_PTS = np.zeros((478, 3), np.float32)

# Default "driver" values: openness 1 = normal open eye; angles in the schema convention.
BASE: dict[str, float] = {
    "openness": 1.0,
    "yaw": 0.0,
    "pitch": 0.0,
    "roll": 0.0,
    "eye_yaw": 0.0,
    "eye_pitch": 0.0,
    "jaw": 0.03,
    "mar": 0.04,
    "smile": 0.0,
    "look_down": 0.0,
    "face": 1.0,
    "blink_every": 0.0,
    "blink_s": 0.15,
}


def synth_face(p: dict[str, float], cfg: AppConfig) -> FaceFeatures | None:
    """Inverse of the signal maths: produce raw features that the pipeline turns back into `p`."""
    if p["face"] < 0.5:
        return None
    c = cfg.calibration
    open_ear, closed_ear = c.default_open_ear, c.default_open_ear * c.default_closed_ear_ratio
    open_b = c.default_open_blink
    closed_b = min(open_b + c.default_closed_blink_offset, 0.9)
    o = p["openness"]
    g = cfg.gaze
    ey, ep = p["eye_yaw"], p["eye_pitch"]
    # eye_yaw = -(w*Giris*dh + (1-w)*Gblend*blend_h): choose dh and blend_h that each give `ey`
    dh, blend_h = -ey / g.iris_yaw_gain_deg, -ey / g.blend_yaw_gain_deg
    dv, blend_v = ep / g.iris_pitch_gain_deg, ep / g.blend_pitch_gain_deg
    look_down = max(p["look_down"], max(0.0, -blend_v))
    look_up = blend_v + look_down

    def eye() -> EyeFeatures:
        return EyeFeatures(
            ear=closed_ear + o * (open_ear - closed_ear),
            blink=open_b + (1.0 - o) * (closed_b - open_b),
            iris_h=0.5 + dh,
            iris_v=dv,
            iris_visibility=0.8 * o,
        )

    return FaceFeatures(
        left=eye(),
        right=eye(),
        look_down=look_down,
        look_up=look_up,
        blend_h=blend_h,
        blend_v=blend_v,
        smile=p["smile"],
        cheek_squint=0.5 * p["smile"],
        eye_squint=0.0,
        jaw_open=p["jaw"],
        mar=p["mar"],
        yaw=p["yaw"],
        pitch=p["pitch"],
        roll=p["roll"],
        center=(0.5, 0.5),
        bbox=(0.35, 0.2, 0.65, 0.8),
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

    def repeat(self, n: int, block: Callable[[Script], Script]) -> Script:
        for _ in range(n):
            block(self)
        return self

    def frames(self) -> Iterator[tuple[float, dict[str, float]]]:
        state = dict(BASE)
        t = 0.0
        blink_next = 0.0
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
                p = dict(state)
                if p["blink_every"] > 0:
                    if t >= blink_next + p["blink_every"]:
                        blink_next = t
                    if blink_next <= t < blink_next + p["blink_s"] and t > 0.5:
                        p["openness"] = 0.03
                yield t, p
                t += 1.0 / FPS


Capture = tuple[str, Callable[[DriverState], bool]]


def at(t: float) -> Callable[[DriverState], bool]:
    return lambda s: s.t >= t - 1e-6


def on(dtype: str, status: str, after: float = 0.0) -> Callable[[DriverState], bool]:
    return lambda s: (
        s.t >= after and any(d.type.value == dtype and d.status.value == status for d in s.detections)
    )


@dataclass
class Scenario:
    name: str
    title: str
    story: str
    script: Script
    captures: list[Capture]
    blocks: list[str]
    note: str = ""


def run(sc: Scenario, cfg: AppConfig | None = None) -> list[tuple[str, DriverState]]:
    cfg = cfg or AppConfig()
    mon = DriverMonitor(cfg, calibrate=False)
    pending = list(sc.captures)
    out: list[tuple[str, DriverState]] = []
    for t, p in sc.script.frames():
        feats = FrameFeatures(t, 1280, 720, 120.0, synth_face(p, cfg), 1 if p["face"] >= 0.5 else 0)
        state = mon.process_features(feats, t, latency_ms=12.0).state
        state.quality.fps = 30.0
        while pending and pending[0][1](state):
            out.append((pending.pop(0)[0], state))
    for label, _ in pending:
        out.append((f"{label} (NOT REACHED)", state))
    return out


def pretty(obj: Any, indent: int = 0, width: int = 110) -> str:
    """JSON with short objects/lists kept on one line (much easier to read than indent=2)."""
    one = json.dumps(obj, ensure_ascii=False)
    if not isinstance(obj, dict | list) or len(one) + indent <= width:
        return one
    pad, inner = " " * indent, " " * (indent + 2)
    if isinstance(obj, list):
        items = [inner + pretty(v, indent + 2, width) for v in obj]
        return "[\n" + ",\n".join(items) + "\n" + pad + "]"
    items = [f"{inner}{json.dumps(k)}: {pretty(v, indent + 2, width)}" for k, v in obj.items()]
    return "{\n" + ",\n".join(items) + "\n" + pad + "}"


def view(s: DriverState, blocks: list[str], full: bool) -> dict[str, Any]:
    d = s.model_dump(mode="json")
    if full:
        return d
    keep = ["t", "summary", *blocks, "detections"]
    return {k: d[k] for k in keep if k in d}


# ---------------------------------------------------------------------------------------------- scenarios
NORMAL = {"blink_every": 4.0, "blink_s": 0.15}  # a healthy 15 blinks/min


def warm(script: Script, seconds: float = 62.0) -> Script:
    """A minute of normal driving first, so PERCLOS / blink statistics are warmed up."""
    return script.hold(seconds, **NORMAL)


SCENARIOS: list[Scenario] = [
    Scenario(
        "normal",
        "Normal, attentive driving",
        "A minute of normal driving: eyes open, 15 blinks/min, looking at the road.",
        warm(Script()).hold(2.0),
        [("after 64 s", at(63.9))],
        [],
        note="full",
    ),
    Scenario(
        "blink",
        "A normal blink (state is not a detection)",
        "Mid-blink the eyes are measured CLOSED, but a 150 ms closure never becomes a detection.",
        warm(Script()).hold(1.0, blink_every=0).hold(0.15, openness=0.03).hold(1.0, openness=1.0),
        [("mid-blink", at(63.1))],
        ["eyes", "gaze"],
    ),
    Scenario(
        "eyes_closed",
        "Eyes closed for 0.7 s (longer than a blink)",
        "The eyes close for 0.7 s, then reopen: EYES_CLOSED (WARNING) starts at 0.5 s and ends REOPENED.",
        warm(Script()).hold(1.0, blink_every=0).hold(0.7, openness=0.03).hold(1.0, openness=1.0),
        [("closed for 0.6 s", at(63.6)), ("reopened", on("EYES_CLOSED", "ENDED"))],
        ["eyes"],
    ),
    Scenario(
        "microsleep",
        "Microsleep: eyes closed 2.4 s",
        "EYES_CLOSED at 0.5 s escalates to MICROSLEEP (CRITICAL) at 1.0 s with the same start time; "
        "one microsleep forces the drowsiness level to at least VERY_DROWSY.",
        warm(Script()).hold(1.0, blink_every=0).hold(2.4, openness=0.03).hold(1.5, openness=1.0),
        [
            ("threshold crossed (1.0 s)", on("MICROSLEEP", "STARTED")),
            ("closed 1.8 s", at(64.8)),
            ("eyes reopened", on("MICROSLEEP", "ENDED")),
        ],
        ["eyes", "drowsiness"],
    ),
    Scenario(
        "eyes_almost_closed",
        "Eyes almost closed (heavy eyelids) for 2.6 s",
        "Lids hang at ~35 % of normal opening while looking straight ahead.",
        warm(Script()).hold(1.0, blink_every=0).hold(3.0, openness=0.35),
        [("after 2.6 s", at(65.6))],
        ["eyes"],
    ),
    Scenario(
        "tired_eyes",
        "Tired eyes over a minute",
        "No single dramatic event: for a minute the eyes are a bit heavy (70 %) and every blink is slow "
        "(450 ms) - TIRED_EYES plus a rising DROWSY level.",
        warm(Script()).hold(65.0, openness=0.7, blink_every=3.0, blink_s=0.45),
        [("after a minute", at(126.9))],
        ["drowsiness"],
    ),
    Scenario(
        "yawn",
        "Yawn",
        "Mouth wide open for 4.5 s with squeezed eyes. Two earlier yawns make it the third in 5 minutes.",
        warm(Script())
        .hold(4.0, jaw=0.8, mar=0.95, openness=0.45)
        .hold(20.0, jaw=0.03, mar=0.04, openness=1.0)
        .hold(4.0, jaw=0.8, mar=0.95, openness=0.45)
        .hold(20.0, jaw=0.03, mar=0.04, openness=1.0)
        .hold(4.5, jaw=0.8, mar=0.95, openness=0.45)
        .hold(2.0, jaw=0.03, mar=0.04, openness=1.0),
        [("third yawn, 2.1 s in", at(112.1)), ("third yawn ended", on("YAWN", "ENDED", after=112.0))],
        ["mouth", "drowsiness"],
    ),
    Scenario(
        "talking",
        "Talking and laughing (guards, no false detections)",
        "Mouth opens and closes every 0.3 s (speech) while smiling with squinted eyes.",
        warm(Script())
        .hold(0.0, blink_every=0)
        .hold(0.3, jaw=0.45, mar=0.5, smile=0.8, openness=0.45)
        .hold(0.2, jaw=0.05, mar=0.05)
        .hold(0.3, jaw=0.45, mar=0.5)
        .hold(0.2, jaw=0.05, mar=0.05)
        .hold(0.3, jaw=0.45, mar=0.5)
        .hold(0.2, jaw=0.05, mar=0.05)
        .hold(0.3, jaw=0.45, mar=0.5)
        .hold(0.2, jaw=0.05, mar=0.05)
        .hold(0.3, jaw=0.45, mar=0.5)
        .hold(0.2, jaw=0.05, mar=0.05)
        .hold(0.3, jaw=0.45, mar=0.5),
        [("after 3 s of laughing/talking", at(64.7))],
        ["eyes", "mouth"],
    ),
    Scenario(
        "head_nod",
        "Head nod (nodding off and catching yourself)",
        "Heavy eyes close, the head drops 17 deg in 0.4 s and snaps back up as the eyes open again.",
        warm(Script())
        .hold(1.0, blink_every=0, openness=0.3)
        .ramp(0.1, openness=0.03)
        .ramp(0.4, pitch=-17.0)
        .ramp(0.3, pitch=-6.0)
        .ramp(0.2, pitch=0.0, openness=1.0)
        .hold(1.5),
        [("head came back", on("HEAD_NOD", "ENDED"))],
        ["head"],
    ),
    Scenario(
        "head_falling",
        "Head falling forward (slow drift, then eyes close)",
        "Heavy eyes, the head sinks ~4.5 deg/s. While the eyes are still open this is NOT a fall (it could be "
        "a look at the phone); once the eyes close while the head keeps sinking -> HEAD_FALLING (CRITICAL).",
        warm(Script())
        .hold(1.0, blink_every=0, openness=0.4)
        .ramp(3.5, pitch=-16.0)
        .ramp(2.0, pitch=-24.0, openness=0.03)
        .ramp(1.0, pitch=-27.0),
        [("sinking, eyes heavy but open", at(66.0)), ("eyes closed, still sinking", at(69.4))],
        ["head", "eyes"],
    ),
    Scenario(
        "head_sideways",
        "Head slumping sideways",
        "The eyes close and the head tips toward the left shoulder.",
        warm(Script()).hold(1.0, blink_every=0, openness=0.03).ramp(3.0, roll=-24.0).hold(1.0),
        [("slumped", at(66.5))],
        ["head"],
    ),
    Scenario(
        "mirror_left",
        "Quick left-mirror check (normal, no detection)",
        "Head turns 21 deg left and the eyes another 12 deg, for 0.8 s.",
        warm(Script()).ramp(0.15, yaw=-21.0, eye_yaw=-12.0).hold(0.8).ramp(0.15, yaw=0.0, eye_yaw=0.0),
        [("looking at the mirror", at(62.9))],
        ["eyes", "gaze", "attention"],
    ),
    Scenario(
        "shoulder_right",
        "Looking far right (shoulder check)",
        "Head turned 58 deg right for 1.2 s: eye metrics are suspended, gaze falls back to head only.",
        warm(Script()).ramp(0.3, yaw=58.0).hold(1.2).ramp(0.3, yaw=0.0),
        [("head at 58 deg", at(63.4))],
        ["quality", "eyes", "gaze", "attention"],
    ),
    Scenario(
        "rear_mirror",
        "Looking up-right (rear-view mirror)",
        "Head 14 deg right / 9 deg up, eyes 11 deg right / 8 deg up, for 0.7 s.",
        warm(Script())
        .ramp(0.2, yaw=14.0, pitch=9.5, eye_yaw=10.8, eye_pitch=7.8)
        .hold(0.7)
        .ramp(0.2, yaw=0.0, pitch=0.0, eye_yaw=0.0, eye_pitch=0.0),
        [("in the mirror", at(62.8))],
        ["gaze", "attention"],
    ),
    Scenario(
        "look_down",
        "Looking down at a phone",
        "Head 24 deg down, eyes another 21 deg down, eyelids lowered by the gaze. WARNING at 2 s, "
        "DANGER at 3.5 s, ended when back on the road.",
        warm(Script())
        .ramp(0.3, pitch=-24.5, eye_pitch=-21.5, openness=0.46, look_down=0.6, yaw=6.0)
        .hold(4.4)
        .ramp(0.3, pitch=0.0, eye_pitch=0.0, openness=1.0, look_down=0.0, yaw=0.0)
        .hold(1.5),
        [("2.6 s down", at(64.9)), ("4.2 s down", at(66.5)), ("back on the road", on("DISTRACTED", "ENDED"))],
        ["eyes", "gaze", "attention"],
    ),
    Scenario(
        "cumulative",
        "Many short glances (cumulative distraction)",
        "Repeated 1.7 s glances down-right (radio) with 0.6 s back on the road.",
        warm(Script())
        .hold(1.7, yaw=18.0, pitch=-16.0, eye_yaw=9.0, eye_pitch=-15.0)
        .hold(0.6, yaw=0.0, pitch=0.0, eye_yaw=0.0, eye_pitch=0.0)
        .hold(1.7, yaw=18.0, pitch=-16.0, eye_yaw=9.0, eye_pitch=-15.0)
        .hold(0.6, yaw=0.0, pitch=0.0, eye_yaw=0.0, eye_pitch=0.0)
        .hold(1.7, yaw=18.0, pitch=-16.0, eye_yaw=9.0, eye_pitch=-15.0)
        .hold(0.6, yaw=0.0, pitch=0.0, eye_yaw=0.0, eye_pitch=0.0)
        .hold(1.7, yaw=18.0, pitch=-16.0, eye_yaw=9.0, eye_pitch=-15.0)
        .hold(0.6, yaw=0.0, pitch=0.0, eye_yaw=0.0, eye_pitch=0.0)
        .hold(1.7, yaw=18.0, pitch=-16.0, eye_yaw=9.0, eye_pitch=-15.0)
        .hold(0.6, yaw=0.0, pitch=0.0, eye_yaw=0.0, eye_pitch=0.0)
        .hold(1.7, yaw=18.0, pitch=-16.0, eye_yaw=9.0, eye_pitch=-15.0)
        .hold(0.6, yaw=0.0, pitch=0.0, eye_yaw=0.0, eye_pitch=0.0)
        .hold(1.7, yaw=18.0, pitch=-16.0, eye_yaw=9.0, eye_pitch=-15.0),
        [("cumulative limit reached", on("DISTRACTED", "STARTED"))],
        ["gaze", "attention"],
    ),
    Scenario(
        "face_lost_left",
        "Face lost after turning far left",
        "The head swings left past 70 deg and the face disappears from the camera.",
        warm(Script()).ramp(0.6, yaw=-72.0).hold(2.5, face=0.0),
        [("2.3 s without a face", at(64.9))],
        ["quality", "eyes", "gaze"],
    ),
    Scenario(
        "face_lost_drop",
        "Face lost after the head dropped",
        "Eyes close, the head sinks forward and drops out of view.",
        warm(Script()).hold(0.5, blink_every=0, openness=0.03).ramp(1.5, pitch=-34.0).hold(2.4, face=0.0),
        [("2.2 s without a face", at(66.2))],
        ["quality"],
    ),
    Scenario(
        "severe",
        "Severely drowsy - everything together",
        "Several microsleeps, yawns, nods and heavy eyes within a few minutes.",
        warm(Script())
        .hold(15.0, openness=0.6, blink_every=2.5, blink_s=0.5)
        .hold(1.6, openness=0.03, blink_every=0)
        .hold(10.0, openness=0.6, blink_every=2.5)
        .hold(4.0, jaw=0.8, mar=0.9, openness=0.4)
        .hold(10.0, jaw=0.03, mar=0.04, openness=0.6)
        .hold(1.8, openness=0.03, blink_every=0)
        .hold(12.0, openness=0.45, blink_every=2.5)
        .hold(4.0, jaw=0.8, mar=0.9)
        .hold(6.0, jaw=0.03, mar=0.04, openness=0.4),
        [("end", at(126.0))],
        ["drowsiness"],
    ),
    Scenario(
        "recovering",
        "Recovering - two microsleeps, then alert driving",
        "Two 1.4 s microsleeps 20 s apart (each alarmed instantly as MICROSLEEP CRITICAL), then the driver "
        "drives normally. The two closures stack to DROWSY; their weight halves every 5 minutes, so the "
        "level comes back down step by step. The events stay in the history (last 15 minutes).",
        warm(Script())
        .hold(1.4, openness=0.03, blink_every=0)
        .hold(20.0, openness=1.0, blink_every=4.0)
        .hold(1.4, openness=0.03, blink_every=0)
        .hold(720.0, openness=1.0, blink_every=4.0),
        [("30 s later", at(115.0)), ("5 minutes later", at(385.0)), ("12 minutes later", at(804.0))],
        ["drowsiness"],
    ),
    Scenario(
        "long_blinks_trend",
        "Repeated long blinks build up over minutes",
        "The eyes close for 0.8 s every 30 s (each one an EYES_CLOSED warning, none a microsleep). One alone "
        "means little, but they keep coming: the fatigue load stacks up and the level climbs over minutes.",
        warm(Script())
        .hold(0.0, blink_every=4.0)
        .repeat(
            12,
            lambda s: (
                s.hold(0.8, openness=0.03, blink_every=0)
                .hold(1.0, openness=1.0)  # no normal blink right after it (they would merge into one closure)
                .hold(28.2, blink_every=4.0)
            ),
        ),
        [("after 1 minute", at(122.0)), ("after 3 minutes", at(242.0)), ("after 6 minutes", at(421.0))],
        ["drowsiness"],
    ),
]


def markdown(full_names: set[str]) -> str:
    out: list[str] = []
    for i, sc in enumerate(SCENARIOS, 1):
        full = sc.note == "full" or sc.name in full_names
        out.append(
            f"### 10.{i} {sc.title}\n\n{sc.story}  \n*Scenario `{sc.name}`*"
            + (
                ""
                if full
                else f" - showing `summary`, {', '.join(f'`{b}`' for b in sc.blocks) or 'no signal blocks'}"
                " and `detections`"
            )
            + "\n"
        )
        for label, state in run(sc):
            out.append(f"**{label}**\n\n```json\n{pretty(view(state, sc.blocks, full))}\n```\n")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="scenario name (see --list)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--full", action="store_true", help="print complete messages")
    ap.add_argument("--markdown", action="store_true", help="all scenarios as Markdown")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if a.list or (not a.name and not a.markdown):
        for sc in SCENARIOS:
            print(f"{sc.name:20s} {sc.title}")
        return 0
    if a.markdown:
        print(markdown(set()))
        return 0
    sc = next((s for s in SCENARIOS if s.name == a.name), None)
    if sc is None:
        print(f"unknown scenario {a.name!r}; use --list", file=sys.stderr)
        return 1
    for label, state in run(sc):
        print(f"--- {label}")
        print(pretty(view(state, sc.blocks, a.full or sc.note == "full")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
