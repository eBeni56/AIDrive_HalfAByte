"""Regression on REAL webcam recordings (cached MediaPipe output, see tools/cache_recording.py).

The recordings live in recordings/ (git-ignored, they show a real person); the tests are skipped when the
cached .npz files are missing. Expectations were checked frame by frame against the videos:
  r1  eyes closed ~3 s at 17 s, looks at the phone at 22-28 s, yawns at 36 s, turns far left/right later
  r2  looks down at 36 s, eyes closed ~3 s at 48 s
  r3  looks at the phone at 27-32 s, two yawns at 62 s and 67 s, four long eye closures from 81 s
  r4  looks left, right and up (no drowsiness)
  r5  mostly straight ahead, one quick glance down at 30 s (no drowsiness)
  r7  eyes closed three times for 2-6 s
  r8  straight ahead, eyes closed once (~2 s), then attentive again
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

from driver_monitor.common.enums import DetectionStatus, DrowsinessLevel
from driver_monitor.config import AppConfig
from driver_monitor.pipeline import DriverMonitor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from cache_recording import replay_features  # noqa: E402

REC = ROOT / "recordings"


def _run(n: int) -> tuple[Counter[str], DrowsinessLevel]:
    path = REC / f"recording-{n}.npz"
    if not path.exists():
        pytest.skip(f"{path.name} not available (run tools/cache_recording.py on the recordings)")
    feats = replay_features(path)
    mon = DriverMonitor(AppConfig(), base_dir=ROOT, calibrate=True)
    mon.start_calibration(feats[0].t)
    started: Counter[str] = Counter()
    peak = DrowsinessLevel.ALERT
    for f in feats:
        s = mon.process_features(f, f.t).state
        for d in s.detections:
            if d.status is DetectionStatus.STARTED or (
                d.status is DetectionStatus.ENDED and d.type.value == "HEAD_NOD"
            ):
                started[d.type.value] += 1
        if s.drowsiness.level.number > peak.number:
            peak = s.drowsiness.level
    return started, peak


FALSE_ALARMS = ("HEAD_FALLING", "HEAD_NOD")


def test_r1_closure_phone_and_yawn() -> None:
    got, _ = _run(1)
    assert got["MICROSLEEP"] >= 1 and got["DISTRACTED"] >= 1 and got["YAWN"] >= 1
    assert not any(got[k] for k in FALSE_ALARMS), got


def test_r2_closure_and_look_down() -> None:
    got, _ = _run(2)
    assert got["MICROSLEEP"] >= 1 and got["DISTRACTED"] >= 1
    assert not any(got[k] for k in FALSE_ALARMS), got


def test_r3_yawns_closures_phone() -> None:
    got, _ = _run(3)
    assert got["YAWN"] >= 2 and got["MICROSLEEP"] >= 3 and got["DISTRACTED"] >= 1
    assert not any(got[k] for k in FALSE_ALARMS), got


@pytest.mark.parametrize("n", [4, 5])
def test_looking_around_is_not_drowsiness(n: int) -> None:
    got, peak = _run(n)
    assert peak is DrowsinessLevel.ALERT, (got, peak)
    assert not any(got[k] for k in (*FALSE_ALARMS, "MICROSLEEP", "EYES_CLOSED", "YAWN")), got


def test_r7_every_long_closure_is_a_microsleep() -> None:
    got, _ = _run(7)
    assert got["MICROSLEEP"] >= 3
    assert not any(got[k] for k in FALSE_ALARMS), got


def test_r8_single_closure_is_alarmed_but_not_high_drowsiness() -> None:
    # looks ahead, closes the eyes once (~2 s), then stays attentive: instant MICROSLEEP alarm, but one
    # event alone must not push the level beyond SLIGHTLY_DROWSY
    got, peak = _run(8)
    assert got["MICROSLEEP"] == 1
    assert peak.number <= DrowsinessLevel.SLIGHTLY_DROWSY.number, peak
    assert not any(got[k] for k in FALSE_ALARMS), got
