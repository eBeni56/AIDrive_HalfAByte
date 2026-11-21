"""Run MediaPipe once over a video and save the raw per-frame output (.npz) for fast, camera-free replays.

What is saved per frame: time, landmarks (478 x 3, pixels), the 52 blendshapes, the 4x4 facial
transformation matrix, the number of faces and the frame brightness - i.e. exactly what FaceTracker
returns. Replaying it rebuilds FaceObservation -> FeatureExtractor -> DriverMonitor, so the CURRENT feature
and detector code is always what gets tested (tests/test_recordings.py).

Usage:
  uv run python tools/cache_recording.py recordings/clip.mp4                  # raw camera video
  uv run python tools/cache_recording.py recordings/recording-1.mp4 --screen  # screen capture of the preview
Options:
  --screen         the video is a screen recording of the (mirrored) preview window: crop the camera
                   area, un-mirror it and scale it back to 1280x720 (--crop to override the area)
  --crop Y0,Y1,X0,X1   camera area inside the screen recording (default 40,940,10,1610)
  --step N         use every N-th frame (default 2: 15 fps from a 30 fps file)
  -o OUT.npz       output file (default: next to the video, same name + .npz)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from driver_monitor.config import AppConfig  # noqa: E402
from driver_monitor.perception.face_tracker import FaceObservation, FaceTracker  # noqa: E402
from driver_monitor.perception.features import FeatureExtractor, FrameFeatures, frame_brightness  # noqa: E402

BLENDSHAPES = [
    "_neutral", "browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft", "browOuterUpRight",
    "cheekPuff", "cheekSquintLeft", "cheekSquintRight", "eyeBlinkLeft", "eyeBlinkRight", "eyeLookDownLeft",
    "eyeLookDownRight", "eyeLookInLeft", "eyeLookInRight", "eyeLookOutLeft", "eyeLookOutRight",
    "eyeLookUpLeft", "eyeLookUpRight", "eyeSquintLeft", "eyeSquintRight", "eyeWideLeft", "eyeWideRight",
    "jawForward", "jawLeft", "jawOpen", "jawRight", "mouthClose", "mouthDimpleLeft", "mouthDimpleRight",
    "mouthFrownLeft", "mouthFrownRight", "mouthFunnel", "mouthLeft", "mouthLowerDownLeft",
    "mouthLowerDownRight", "mouthPressLeft", "mouthPressRight", "mouthPucker", "mouthRight",
    "mouthRollLower", "mouthRollUpper", "mouthShrugLower", "mouthShrugUpper", "mouthSmileLeft",
    "mouthSmileRight", "mouthStretchLeft", "mouthStretchRight", "mouthUpperUpLeft", "mouthUpperUpRight",
    "noseSneerLeft", "noseSneerRight",
]  # fmt: skip


def screen_crop(frame: npt.NDArray[np.uint8], area: tuple[int, int, int, int]) -> npt.NDArray[np.uint8]:
    y0, y1, x0, x1 = area
    img = cv2.resize(frame[y0:y1, x0:x1], (1280, 720), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(cv2.flip(img, 1))


def cache(video: Path, out: Path, step: int, area: tuple[int, int, int, int] | None) -> int:
    cfg = AppConfig()
    tracker = FaceTracker(cfg.landmarker, ROOT)
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    ts, faces, brightness, pts, bss, mats, sizes = [], [], [], [], [], [], []
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % step == 0:
            img = screen_crop(frame, area) if area else frame
            t = i / fps
            obs = tracker.process(img, t)
            ts.append(t)
            brightness.append(frame_brightness(img))
            sizes.append(img.shape[:2])
            faces.append(obs.num_faces if obs else 0)
            pts.append(obs.pts if obs else np.zeros((478, 3), np.float32))
            bss.append(
                [obs.blendshapes.get(n, 0.0) for n in BLENDSHAPES] if obs else [0.0] * len(BLENDSHAPES)
            )
            mats.append(obs.matrix if obs is not None and obs.matrix is not None else np.full((4, 4), np.nan))
        i += 1
    tracker.close()
    np.savez_compressed(
        out,
        t=np.asarray(ts),
        num_faces=np.asarray(faces, np.int8),
        brightness=np.asarray(brightness, np.float32),
        size=np.asarray(sizes, np.int32),
        pts=np.asarray(pts, np.float32),
        blendshapes=np.asarray(bss, np.float32),
        matrix=np.asarray(mats, np.float64),
    )
    return len(ts)


def replay_features(path: Path, cfg: AppConfig | None = None) -> list[FrameFeatures]:
    """Load a cached recording and rebuild FrameFeatures with the current FeatureExtractor."""
    cfg = cfg or AppConfig()
    data = np.load(path)
    fx = FeatureExtractor(cfg.pose)
    out: list[FrameFeatures] = []
    for k, t in enumerate(data["t"]):
        h, w = (int(v) for v in data["size"][k])
        n = int(data["num_faces"][k])
        face = None
        if n > 0:
            m = data["matrix"][k]
            obs = FaceObservation(
                w,
                h,
                data["pts"][k],
                dict(zip(BLENDSHAPES, (float(v) for v in data["blendshapes"][k]), strict=True)),
                None if np.isnan(m).any() else m,
                n,
            )
            face = fx.face_features(obs)
        out.append(FrameFeatures(float(t), w, h, float(data["brightness"][k]), face, n))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path)
    ap.add_argument("--screen", action="store_true")
    ap.add_argument("--crop", default="40,940,10,1610")
    ap.add_argument("--step", type=int, default=2)
    ap.add_argument("-o", "--out", type=Path)
    args = ap.parse_args()
    area = tuple(int(v) for v in args.crop.split(",")) if args.screen else None
    out = args.out or args.video.with_suffix(".npz")
    n = cache(args.video, out, args.step, area)  # type: ignore[arg-type]
    print(f"{n} frames -> {out}")


if __name__ == "__main__":
    main()
