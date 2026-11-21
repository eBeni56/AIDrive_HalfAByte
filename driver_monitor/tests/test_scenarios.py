"""End-to-end regression: scripted scenarios through the whole pipeline (tools/scenarios.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from scenarios import SCENARIOS, run

# scenario -> for each capture: the set of detection types present in that message
EXPECTED: dict[str, list[set[str]]] = {
    "normal": [set()],
    "blink": [set()],
    "eyes_closed": [{"EYES_CLOSED"}, {"EYES_CLOSED"}],
    # one microsleep is alarmed instantly; the drowsiness LEVEL only follows as the evidence accumulates
    "microsleep": [{"MICROSLEEP", "EYES_CLOSED"}, {"MICROSLEEP"}, {"MICROSLEEP"}],
    "eyes_almost_closed": [{"EYES_ALMOST_CLOSED"}],
    "tired_eyes": [{"TIRED_EYES", "EYES_NARROWED", "DROWSY"}],
    "yawn": [{"YAWN"}, {"YAWN"}],
    "talking": [set()],
    "head_nod": [{"HEAD_NOD", "EYES_CLOSED"}],
    # heavy but open eyes while the head sinks are not a fall yet (could be a look at the phone)
    # the head sinks with half-open eyes looking down for > 1.2 s: also off the road (DISTRACTED)
    "head_falling": [{"EYES_ALMOST_CLOSED"}, {"HEAD_FALLING", "MICROSLEEP", "DISTRACTED"}],
    "head_sideways": [{"HEAD_FALLING", "MICROSLEEP", "DROWSY"}],
    "eyes_narrowed": [{"EYES_NARROWED", "DROWSY"}],
    "mirror_left": [set()],
    "look_side": [set()],
    "shoulder_right": [set()],
    "rear_mirror": [set()],
    "look_down": [{"DISTRACTED"}, {"DISTRACTED"}, {"DISTRACTED"}],
    "cumulative": [{"DISTRACTED"}],
    "face_lost_left": [{"FACE_NOT_VISIBLE"}],
    "face_lost_drop": [{"FACE_NOT_VISIBLE", "DROWSY"}],
    "severe": [{"DROWSY", "TIRED_EYES", "EYES_ALMOST_CLOSED", "EYES_NARROWED"}],
    "recovering": [{"DROWSY"}, {"DROWSY"}, set()],
    "long_blinks_trend": [{"DROWSY"}, {"DROWSY"}, {"DROWSY"}],
}


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_scenario(sc: object) -> None:
    results = run(sc)  # type: ignore[arg-type]
    assert all("NOT REACHED" not in label for label, _ in results), [label for label, _ in results]
    got = [{d.type.value for d in state.detections} for _, state in results]
    assert got == EXPECTED[sc.name]  # type: ignore[attr-defined]


def test_specific_values() -> None:
    by_name = {s.name: s for s in SCENARIOS}
    look = run(by_name["look_down"])
    assert look[0][1].eyes.lid_lowered_by_gaze is True and look[0][1].gaze.zone.value == "DOWN"
    assert look[0][1].detections[0].severity.value == "WARNING"  # 1.4 s down
    assert look[1][1].detections[0].severity.value == "DANGER"  # 2.8 s down
    assert look[0][1].detections[0].details["reason"] == "LOOKING_DOWN"
    narrowed = run(by_name["eyes_narrowed"])[0][1]
    assert narrowed.eyes.normal_learned and narrowed.drowsiness.level.value == "SLIGHTLY_DROWSY"
    assert narrowed.eyes.openness_ratio is not None and narrowed.eyes.openness_ratio < 0.85
    lost = run(by_name["face_lost_left"])[0][1]
    assert lost.detections[0].details["likely_cause"] == "TURNED_AWAY_LEFT"
    drop = run(by_name["face_lost_drop"])[0][1]
    assert next(d for d in drop.detections if d.type.value == "FACE_NOT_VISIBLE").details["likely_cause"] == (
        "HEAD_DROPPED"
    )
    mirror = run(by_name["mirror_left"])[0][1]
    assert mirror.gaze.zone.value == "LEFT_MIRROR" and mirror.attention.state.value == "CHECKING_MIRROR"
    assert run(by_name["rear_mirror"])[0][1].gaze.zone.value == "REAR_MIRROR"
    assert run(by_name["look_side"])[0][1].gaze.zone.value == "LEFT_SIDE"
    # acute: two microsleeps stack to DROWSY, then the level recovers while the history keeps the events
    recovering = run(by_name["recovering"])
    assert [s.drowsiness.level.value for _, s in recovering] == ["DROWSY", "SLIGHTLY_DROWSY", "ALERT"]
    h15 = next(h for h in recovering[1][1].drowsiness.history if h.window_s == 900)
    assert h15.microsleeps == 2 and h15.closed_eyes_s > 2.5
    # chronic: long blinks (never a microsleep) climb over minutes
    trend = run(by_name["long_blinks_trend"])
    assert [s.drowsiness.level.value for _, s in trend] == ["SLIGHTLY_DROWSY", "DROWSY", "VERY_DROWSY"]
    assert all(h.microsleeps == 0 for _, s in trend for h in s.drowsiness.history)
