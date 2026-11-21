"""End-to-end regression: scripted scenarios through the whole pipeline (tools/scenarios.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from scenarios import SCENARIOS, run

# scenario -> for each capture: (gaze zone, eye state, detection types in that message)
EXPECTED: dict[str, list[tuple[str, str, set[str]]]] = {
    "normal": [("ROAD", "OPEN", set())],
    "blink": [("ROAD", "CLOSED", set()), ("ROAD", "OPEN", set())],
    "eyes_closed": [("UNKNOWN", "CLOSED", {"EYES_CLOSED"}), ("ROAD", "OPEN", {"EYES_CLOSED"})],
    "microsleep": [("UNKNOWN", "CLOSED", {"EYES_CLOSED"}), ("ROAD", "OPEN", {"EYES_CLOSED"})],
    "look_down": [
        ("DOWN", "OPEN", {"DISTRACTED"}),
        ("DOWN", "OPEN", {"DISTRACTED"}),
        ("ROAD", "OPEN", {"DISTRACTED"}),
    ],
    "mirror_left": [("LEFT_MIRROR", "OPEN", set())],
    "rear_mirror": [("REAR_MIRROR", "OPEN", set())],
    "look_side": [("LEFT_SIDE", "OPEN", set())],
    "shoulder_right": [("RIGHT_SIDE", "UNKNOWN", set())],
    "face_lost": [("UNKNOWN", "UNKNOWN", set())],
}


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_scenario(sc: object) -> None:
    results = run(sc)  # type: ignore[arg-type]
    assert all("NOT REACHED" not in label for label, _ in results), [label for label, _ in results]
    got = [(s.gaze.zone.value, s.eyes.state.value, {d.type.value for d in s.detections}) for _, s in results]
    assert got == EXPECTED[sc.name]  # type: ignore[attr-defined]


def _by_name(name: str) -> object:
    return next(s for s in SCENARIOS if s.name == name)


def test_severities_and_details() -> None:
    closed = run(_by_name("microsleep"))  # type: ignore[arg-type]
    assert closed[0][1].detections[0].severity.value == "DANGER"  # closed 1.8 s
    assert 2.3 < closed[1][1].detections[0].duration_s < 2.5
    short = run(_by_name("eyes_closed"))  # type: ignore[arg-type]
    assert short[1][1].detections[0].severity.value == "WARNING"
    look = run(_by_name("look_down"))  # type: ignore[arg-type]
    assert look[0][1].detections[0].severity.value == "WARNING"
    assert look[1][1].detections[0].severity.value == "DANGER"
    assert look[0][1].detections[0].details["zone"] == "DOWN"
    lost = run(_by_name("face_lost"))[0][1]  # type: ignore[arg-type]
    assert lost.quality.face_detected is False
