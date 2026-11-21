"""Download the MediaPipe models into models/:
  face_landmarker.task      Face Landmarker (eyes, head pose, gaze)
  efficientdet_lite0.tflite Object Detector, COCO classes (phone in the hand: PHONE_USE)

Usage:  uv run python tools/download_model.py [--force]
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
MODELS = [
    (
        "face_landmarker.task",
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    ),
    (
        "efficientdet_lite0.tflite",
        "https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float16/1/efficientdet_lite0.tflite",
    ),
]


def download(name: str, url: str, force: bool) -> bool:
    target = MODELS_DIR / name
    if target.exists() and not force:
        print(f"Model already present: {target} ({target.stat().st_size / 1e6:.1f} MB)")
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    print(f"Downloading {url}\n -> {target}")
    with urllib.request.urlopen(url, timeout=60) as response, tmp.open("wb") as out:
        while chunk := response.read(1 << 16):
            out.write(chunk)
    size = tmp.stat().st_size
    if size < 1_000_000:
        tmp.unlink()
        print(f"Download looks wrong ({size} bytes). Check the URL / network.", file=sys.stderr)
        return False
    tmp.replace(target)
    print(f"Done ({size / 1e6:.1f} MB).")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="download even if the files exist")
    args = parser.parse_args()
    ok = all([download(name, url, args.force) for name, url in MODELS])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
