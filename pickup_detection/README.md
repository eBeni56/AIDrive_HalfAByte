# Phone Pickup & Gaze Detector

A small Python script that watches the driver through a webcam, counts how many times they pick up
their phone, and tracks how long they actually look at the screen.
It runs locally on CPU with MediaPipe and OpenCV, with no GPU or separate server needed.
The three MediaPipe model files are downloaded automatically on the first run.

## Quick start

Python 3 is required. Create a virtual environment, install the two dependencies, and run the
script:

```
python -m venv venv
venv/bin/pip install mediapipe opencv-python       # Windows: venv\Scripts\pip ...
venv/bin/python phone_tracker.py                   # Windows: venv\Scripts\python ...
```

On the first run it creates a `models/` folder and downloads ~24 MB of pretrained weights from
Google's MediaPipe storage. Once the window opens, the camera feed is on the left (mirrored) and
the live counters are on the right.

Controls inside the video window:
- **G** toggles the gaze arrow drawn from the eyes.
- **R** saves the current drive session and resets the counters for a new run.
- **Q** quits and saves any active pickup and the session summary.

If your webcam does not open or you want to use an external USB camera, change `CAMERA_INDEX` at
the top of `phone_tracker.py` (default `0`). Other tunable values (confidence thresholds, pickup
confirmation/cooldown times, gaze angle threshold) live in the same config block.

## Files

- `phone_tracker.py`: everything — model download, MediaPipe inference, gaze math, state machine, OpenCV UI, and SQLite logging
- `models/efficientdet_lite2.tflite`: int8 object detector, filtered to `cell phone` only (auto-downloaded)
- `models/hand_landmarker.task`: hand keypoint model used to tell in-hand phones apart from mounted ones (auto-downloaded)
- `models/face_landmarker.task`: face mesh + blendshapes + 3D head pose matrix used for gaze estimation (auto-downloaded)
- `drive_logs.db`: SQLite database created on first run; stores every pickup and session summary

## What it stores

Every run is stored locally in `drive_logs.db` under a `session_id` of the form `drive_<unix_ts>`.
Nothing is sent over the network (other than downloading the models once). A pickup row is written
when the driver puts the phone down and the 2.5 s cooldown finishes, or when **Q** is pressed
mid-pickup. A session row is written on **R** and on **Q**.

Events are also printed to the terminal when a pickup starts (`[PICKUP]`) and when it is saved
(`[LOGGED]`).

### `phone_pickups`

One row per distinct phone pickup. Holding the phone for 10 seconds produces one row, not 200
frame detections.

| Field | What it is |
|---|---|
| `id` | Auto-incremented primary key. |
| `session_id` | Links the pickup back to a row in `drive_sessions`. |
| `started_at` | Local timestamp (`YYYY-MM-DD HH:MM:SS`) when the pickup started confirming. |
| `duration_sec` | How long the phone stayed in the driver's hand, in seconds (rounded to 2 decimals). |
| `watch_sec` | How many of those seconds the driver was actually looking at the phone screen. |

### `drive_sessions`

One summary row per drive (created or updated on **R** and **Q**).

| Field | What it is |
|---|---|
| `session_id` | Primary key (`drive_<unix_ts>`). |
| `started_at` | Local timestamp when the session started. |
| `ended_at` | Local timestamp when the session ended or was reset. |
| `total_pickups` | Total number of confirmed pickups in the session. |
| `total_hold_sec` | Sum of hand-hold durations across the session, in seconds. |
| `total_watch_sec` | Total screen-watching time across the session, in seconds (includes mounted phones unless `WATCH_REQUIRES_HAND` is set to `True`). |

### Example

Rows saved after a short test run with two pickups:

```
phone_pickups:
id | session_id       | started_at          | duration_sec | watch_sec
1  | drive_1763715000 | 2026-11-21 10:50:12 | 4.82         | 3.15
2  | drive_1763715000 | 2026-11-21 10:51:03 | 2.10         | 0.00

drive_sessions:
session_id       | started_at          | ended_at            | total_pickups | total_hold_sec | total_watch_sec
drive_1763715000 | 2026-11-21 10:50:00 | 2026-11-21 10:52:00 | 2             | 6.92           | 3.15
```

In the example, pickup 1 was picked up and looked at for 3.15 s, while pickup 2 was picked up
without looking at the screen (`watch_sec` is `0.00`), so it still counted as a pickup without
adding to `total_watch_sec`.

## Detection in one line

Inference runs on every second frame: a phone counts as picked up once a hand landmark overlaps its
padded box for 0.6 s (resetting after 2.5 s away), while watch time ticks whenever the 3D head +
eye gaze vector points within 28° of the phone (with a 0.35 s grace buffer for blinks).