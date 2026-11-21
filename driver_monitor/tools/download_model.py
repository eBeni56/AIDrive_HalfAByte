"""Download the MediaPipe Face Landmarker model into models/face_landmarker.task.

Usage:  uv run python tools/download_model.py [--force]
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
TARGET = Path(__file__).resolve().parent.parent / "models" / "face_landmarker.task"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="download even if the file exists")
    args = parser.parse_args()

    if TARGET.exists() and not args.force:
        print(f"Model already present: {TARGET} ({TARGET.stat().st_size / 1e6:.1f} MB)")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    tmp = TARGET.with_suffix(".part")
    print(f"Downloading {MODEL_URL}\n -> {TARGET}")
    with urllib.request.urlopen(MODEL_URL, timeout=60) as response, tmp.open("wb") as out:
        while chunk := response.read(1 << 16):
            out.write(chunk)
    size = tmp.stat().st_size
    if size < 1_000_000:
        tmp.unlink()
        print(f"Download looks wrong ({size} bytes). Check the URL / network.", file=sys.stderr)
        return 1
    tmp.replace(TARGET)
    print(f"Done ({size / 1e6:.1f} MB).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
