import os
import time
import sqlite3
import urllib.request
from datetime import datetime
from collections import deque

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision


# ==========================================
# 1. CONFIGURATION (Tweak these if needed)
# ==========================================
CAMERA_INDEX = 0            # 0 = default webcam, try 1 for external USB cam
FRAME_W, FRAME_H = 640, 480
PANEL_W = 340               # Width of the side counter panel

PHONE_CONF = 0.28           # Lower = catches phones more easily (0.25 - 0.45)
HAND_CONF = 0.35
FACE_CONF = 0.40
PAD_PX = 35                 # Margin around phone box to match hand overlap

CONFIRM_SEC = 0.6           # Hold phone this long to count as 1 pickup
COOLDOWN_SEC = 2.5          # Put phone down this long before next pickup counts
DETECT_EVERY_N_FRAMES = 2   # Run AI every 2nd frame for smooth CPU FPS

# Gaze / "Looking at Phone" tuning
GAZE_ANGLE_THRESHOLD_DEG = 28.0  # Max angle between gaze ray & phone to count as watching
PHONE_DEPTH_Z = 0.36             # Estimated normalized distance from face to phone
EYE_X_WEIGHT = 0.55              # How strongly horizontal iris movement steers gaze
EYE_Y_WEIGHT = 0.65              # How strongly eye look-down/up blendshapes steer gaze
WATCH_GRACE_SEC = 0.35           # Brief grace buffer so 1-frame flickers don't pause timer
WATCH_REQUIRES_HAND = False      # Set True to count Watch Time ONLY while phone is in hand

MODEL_DIR = "models"
MODELS = {
    "efficientdet_lite2.tflite": (
        "https://storage.googleapis.com/mediapipe-models/object_detector/"
        "efficientdet_lite2/int8/latest/efficientdet_lite2.tflite"
    ),
    "hand_landmarker.task": (
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
        "hand_landmarker/float16/latest/hand_landmarker.task"
    ),
    "face_landmarker.task": (
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
        "face_landmarker/float16/latest/face_landmarker.task"
    ),
}


# ==========================================
# 2. AUTO-DOWNLOAD MODELS & INIT DATABASE
# ==========================================
def ensure_models():
    os.makedirs(MODEL_DIR, exist_ok=True)
    paths = {}
    for filename, url in MODELS.items():
        path = os.path.join(MODEL_DIR, filename)
        if not os.path.exists(path):
            print(f"[Downloading] {filename} ...")
            urllib.request.urlretrieve(url, path)
            print(f"[Saved] {path}")
        paths[filename] = path
    return paths


def init_db():
    conn = sqlite3.connect("drive_logs.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS phone_pickups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            started_at TEXT,
            duration_sec REAL,
            watch_sec REAL DEFAULT 0.0
        )
    """)
    # Safely upgrade an older drive_logs.db created before gaze tracking
    try:
        conn.execute("ALTER TABLE phone_pickups ADD COLUMN watch_sec REAL DEFAULT 0.0")
    except sqlite3.OperationalError:
        pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS drive_sessions (
            session_id TEXT PRIMARY KEY,
            started_at TEXT,
            ended_at TEXT,
            total_pickups INTEGER,
            total_hold_sec REAL,
            total_watch_sec REAL
        )
    """)
    conn.commit()
    return conn


def log_pickup(conn, session_id, start_ts, duration_sec, watch_sec):
    started_str = datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO phone_pickups (session_id, started_at, duration_sec, watch_sec)
        VALUES (?, ?, ?, ?)
        """,
        (session_id, started_str, round(duration_sec, 2), round(watch_sec, 2)),
    )
    conn.commit()


def log_session(conn, session_id, start_ts, total_pickups, total_hold_sec, total_watch_sec):
    started_str = datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M:%S")
    ended_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT OR REPLACE INTO drive_sessions
            (session_id, started_at, ended_at, total_pickups, total_hold_sec, total_watch_sec)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            started_str,
            ended_str,
            total_pickups,
            round(total_hold_sec, 2),
            round(total_watch_sec, 2),
        ),
    )
    conn.commit()


# ==========================================
# 3. DETECTION, HAND-OVERLAP & GAZE LOGIC
# ==========================================
def _eye_horiz_offset(lm, corner_a, corner_b, iris_idx):
    x_min = min(lm[corner_a].x, lm[corner_b].x)
    x_max = max(lm[corner_a].x, lm[corner_b].x)
    span = x_max - x_min
    if span < 1e-5:
        return 0.0
    ratio = (lm[iris_idx].x - x_min) / span  # 0.0 = image-left, 0.5 = center, 1.0 = image-right
    return float(np.clip((ratio - 0.5) * 2.0, -1.0, 1.0))


def estimate_gaze(face_res, w, h):
    if not face_res.face_landmarks or not face_res.facial_transformation_matrixes:
        return None

    lm = face_res.face_landmarks[0]
    if len(lm) < 474:
        return None

    # Iris centers (468 & 473) give exact eye midpoint
    l_iris = (lm[468].x * w, lm[468].y * h)
    r_iris = (lm[473].x * w, lm[473].y * h)
    eye_cx = 0.5 * (l_iris[0] + r_iris[0])
    eye_cy = 0.5 * (l_iris[1] + r_iris[1])

    # Head forward direction from 4x4 transformation matrix (OpenGL +Y up -> OpenCV +Y down)
    mat = np.array(face_res.facial_transformation_matrixes[0], dtype=np.float32)
    head_dx = float(mat[0, 2])
    head_dy = float(-mat[1, 2])
    head_dz = max(0.15, float(mat[2, 2]))

    # Horizontal eye shift from iris position between eye corners (33/133 and 362/263)
    eye_dx = 0.5 * (
        _eye_horiz_offset(lm, 33, 133, 468) + _eye_horiz_offset(lm, 362, 263, 473)
    )

    # Vertical eye shift from MediaPipe eye look blendshapes
    eye_dy = 0.0
    if face_res.face_blendshapes:
        bs = {cat.category_name: cat.score for cat in face_res.face_blendshapes[0]}
        look_down = 0.5 * (bs.get("eyeLookDownLeft", 0.0) + bs.get("eyeLookDownRight", 0.0))
        look_up = 0.5 * (bs.get("eyeLookUpLeft", 0.0) + bs.get("eyeLookUpRight", 0.0))
        eye_dy = look_down - look_up

    gaze_vec = np.array(
        [
            head_dx + EYE_X_WEIGHT * eye_dx,
            head_dy + EYE_Y_WEIGHT * eye_dy,
            head_dz,
        ],
        dtype=np.float32,
    )
    gaze_vec /= np.linalg.norm(gaze_vec) + 1e-6

    return {
        "eye_center": (eye_cx, eye_cy),
        "irises": [
            (int(l_iris[0]), int(l_iris[1])),
            (int(r_iris[0]), int(r_iris[1])),
        ],
        "vec": gaze_vec,
    }


def run_detection(frame_bgr, phone_detector, hand_landmarker, face_landmarker):
    h, w, _ = frame_bgr.shape
    rgb = np.ascontiguousarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

    obj_res = phone_detector.detect(mp_image)
    hand_res = hand_landmarker.detect(mp_image)
    face_res = face_landmarker.detect(mp_image)

    # Extract key hand points (wrist, palm knuckles, thumb/index/middle tips)
    hand_points = []
    if hand_res.hand_landmarks:
        for hand in hand_res.hand_landmarks:
            for idx in (0, 4, 5, 8, 9, 12, 13, 17):
                hand_points.append((int(hand[idx].x * w), int(hand[idx].y * h)))

    gaze_info = estimate_gaze(face_res, w, h)

    phones = []
    phone_in_hand = False
    looking_at_phone = False
    best_gaze_angle = None

    if obj_res.detections:
        for det in obj_res.detections:
            b = det.bounding_box
            score = det.categories[0].score if det.categories else 0.0
            x1, y1 = max(0, b.origin_x), max(0, b.origin_y)
            x2, y2 = min(w, b.origin_x + b.width), min(h, b.origin_y + b.height)

            # 1. Check hand overlap
            in_hand = False
            for hx, hy in hand_points:
                if (x1 - PAD_PX) <= hx <= (x2 + PAD_PX) and (y1 - PAD_PX) <= hy <= (y2 + PAD_PX):
                    in_hand = True
                    phone_in_hand = True
                    break

            # 2. Check if driver's gaze is aimed at this phone
            is_watched = False
            if gaze_info is not None and (in_hand or not WATCH_REQUIRES_HAND):
                eye_cx, eye_cy = gaze_info["eye_center"]
                px = 0.5 * (x1 + x2)
                py = 0.5 * (y1 + y2)

                to_phone = np.array(
                    [
                        (px - eye_cx) / w,
                        (py - eye_cy) / w,
                        PHONE_DEPTH_Z,
                    ],
                    dtype=np.float32,
                )
                to_phone /= np.linalg.norm(to_phone) + 1e-6

                cos_sim = float(np.clip(np.dot(gaze_info["vec"], to_phone), -1.0, 1.0))
                angle_deg = float(np.degrees(np.arccos(cos_sim)))

                if best_gaze_angle is None or angle_deg < best_gaze_angle:
                    best_gaze_angle = angle_deg

                if angle_deg <= GAZE_ANGLE_THRESHOLD_DEG:
                    is_watched = True
                    looking_at_phone = True

            phones.append((x1, y1, x2, y2, score, in_hand, is_watched))

    return phone_in_hand, looking_at_phone, best_gaze_angle, phones, hand_points, gaze_info


# ==========================================
# 4. SIDE PANEL UI RENDERER
# ==========================================
def build_side_panel(
    height,
    width,
    pickup_count,
    total_watch_sec,
    current_watch_streak,
    total_hold_sec,
    state,
    watching_active,
    confirm_progress,
    gaze_angle,
    recent_events,
    fps,
):
    panel = np.full((height, width, 3), (24, 24, 30), dtype=np.uint8)

    # Header
    cv2.rectangle(panel, (0, 0), (width, 46), (38, 38, 48), -1)
    cv2.putText(
        panel, "DRIVER MONITOR", (16, 30),
        cv2.FONT_HERSHEY_SIMPLEX, 0.70, (240, 240, 245), 2, cv2.LINE_AA
    )
    cv2.putText(
        panel, f"{fps:.0f} FPS", (width - 78, 29),
        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (150, 155, 170), 1, cv2.LINE_AA
    )

    # Two Side-by-Side Metric Cards (Pickups vs. Watch Time)
    card_y1, card_y2 = 58, 148
    mid = width // 2

    # Card 1: Phone Pickups (Hand)
    cv2.rectangle(panel, (14, card_y1), (mid - 6, card_y2), (36, 36, 46), -1)
    cv2.rectangle(panel, (14, card_y1), (mid - 6, card_y2), (70, 70, 85), 1)
    cv2.putText(
        panel, "PICKUPS", (26, 82),
        cv2.FONT_HERSHEY_SIMPLEX, 0.50, (170, 175, 190), 1, cv2.LINE_AA
    )
    pickup_color = (50, 165, 255) if state == "IN_HAND" else (255, 255, 255)
    cv2.putText(
        panel, str(pickup_count), (26, 134),
        cv2.FONT_HERSHEY_SIMPLEX, 1.55, pickup_color, 3, cv2.LINE_AA
    )

    # Card 2: Phone Watching Time (Gaze)
    cv2.rectangle(panel, (mid + 6, card_y1), (width - 14, card_y2), (36, 36, 46), -1)
    watch_border = (60, 60, 235) if watching_active else (70, 70, 85)
    cv2.rectangle(panel, (mid + 6, card_y1), (width - 14, card_y2), watch_border, 2 if watching_active else 1)
    cv2.putText(
        panel, "WATCH TIME", (mid + 18, 82),
        cv2.FONT_HERSHEY_SIMPLEX, 0.50, (170, 175, 190), 1, cv2.LINE_AA
    )
    watch_color = (70, 80, 255) if watching_active else (255, 255, 255)
    cv2.putText(
        panel, f"{total_watch_sec:.1f}s", (mid + 18, 132),
        cv2.FONT_HERSHEY_SIMPLEX, 1.05, watch_color, 3, cv2.LINE_AA
    )

    # Live Status Badge
    if watching_active:
        badge_label, badge_color = "WATCHING PHONE!", (55, 55, 235)
    elif state == "IN_HAND":
        badge_label, badge_color = "IN HAND (EYES OFF)", (30, 140, 240)
    elif state == "CONFIRMING":
        badge_label, badge_color = "CHECKING PICKUP...", (40, 185, 235)
    elif state == "COOLDOWN":
        badge_label, badge_color = "PUTTING DOWN", (60, 150, 220)
    else:
        badge_label, badge_color = "SAFE / EYES ON ROAD", (55, 175, 85)

    cv2.rectangle(panel, (14, 160), (width - 14, 198), badge_color, -1)
    cv2.putText(
        panel, badge_label, (26, 185),
        cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2, cv2.LINE_AA
    )

    # Confirmation Progress Bar
    bar_x1, bar_y1, bar_x2, bar_y2 = 14, 206, width - 14, 214
    cv2.rectangle(panel, (bar_x1, bar_y1), (bar_x2, bar_y2), (50, 50, 62), -1)
    fill_w = int((bar_x2 - bar_x1) * max(0.0, min(1.0, confirm_progress)))
    if fill_w > 0:
        cv2.rectangle(panel, (bar_x1, bar_y1), (bar_x1 + fill_w, bar_y2), badge_color, -1)

    # Live Telemetry
    angle_text = f"{gaze_angle:4.1f} deg" if gaze_angle is not None else "No phone/face"
    angle_color = (80, 95, 255) if (gaze_angle is not None and gaze_angle <= GAZE_ANGLE_THRESHOLD_DEG) else (200, 205, 215)
    cv2.putText(
        panel, f"Gaze-to-Phone: {angle_text}", (16, 238),
        cv2.FONT_HERSHEY_SIMPLEX, 0.50, angle_color, 1, cv2.LINE_AA
    )
    cv2.putText(
        panel, f"Current Glance: {current_watch_streak:4.1f}s", (16, 260),
        cv2.FONT_HERSHEY_SIMPLEX, 0.50, (205, 210, 220), 1, cv2.LINE_AA
    )
    cv2.putText(
        panel, f"Total Hand Time: {total_hold_sec:4.1f}s", (16, 282),
        cv2.FONT_HERSHEY_SIMPLEX, 0.50, (170, 175, 190), 1, cv2.LINE_AA
    )

    # Recent Pickups Log
    cv2.line(panel, (14, 298), (width - 14, 298), (60, 60, 75), 1)
    cv2.putText(
        panel, "RECENT PICKUPS", (16, 320),
        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (160, 165, 180), 1, cv2.LINE_AA
    )

    if not recent_events:
        cv2.putText(
            panel, "No pickups logged yet", (16, 346),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (115, 120, 135), 1, cv2.LINE_AA
        )
    else:
        for i, ev in enumerate(recent_events):
            cv2.putText(
                panel, ev, (16, 346 + i * 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.43, (215, 220, 230), 1, cv2.LINE_AA
            )

    # Footer Controls
    cv2.putText(
        panel, f"Watch Threshold: <= {GAZE_ANGLE_THRESHOLD_DEG:.0f} deg", (16, height - 38),
        cv2.FONT_HERSHEY_SIMPLEX, 0.43, (125, 130, 145), 1, cv2.LINE_AA
    )
    cv2.putText(
        panel, "[R] Reset   [G] Gaze Ray   [Q] Quit", (16, height - 14),
        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (150, 155, 175), 1, cv2.LINE_AA
    )

    return panel


# ==========================================
# 5. MAIN LOOP
# ==========================================
def main():
    model_paths = ensure_models()
    conn = init_db()

    obj_options = vision.ObjectDetectorOptions(
        base_options=python.BaseOptions(model_asset_path=model_paths["efficientdet_lite2.tflite"]),
        score_threshold=PHONE_CONF,
        max_results=3,
        category_allowlist=["cell phone"],
    )
    phone_detector = vision.ObjectDetector.create_from_options(obj_options)

    hand_options = vision.HandLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=model_paths["hand_landmarker.task"]),
        num_hands=2,
        min_hand_detection_confidence=HAND_CONF,
        min_hand_presence_confidence=HAND_CONF,
    )
    hand_landmarker = vision.HandLandmarker.create_from_options(hand_options)

    face_options = vision.FaceLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=model_paths["face_landmarker.task"]),
        num_faces=1,
        min_face_detection_confidence=FACE_CONF,
        min_face_presence_confidence=FACE_CONF,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True,
    )
    face_landmarker = vision.FaceLandmarker.create_from_options(face_options)

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print(f"ERROR: Could not open camera {CAMERA_INDEX}. Try changing CAMERA_INDEX to 1.")
        conn.close()
        return

    session_start_ts = time.time()
    session_id = f"drive_{int(session_start_ts)}"

    # Hand pickup state machine
    state = "IDLE"          # IDLE | CONFIRMING | IN_HAND | COOLDOWN
    state_timer = None
    pickup_start = None
    pickup_count = 0
    total_hold_sec = 0.0
    pickup_watch_sec = 0.0

    # Screen watching timer state
    total_watch_sec = 0.0
    current_watch_streak = 0.0
    last_watch_seen_ts = 0.0
    watching_active = False

    recent_events = deque(maxlen=4)
    show_gaze_ray = True

    frame_idx = 0
    in_hand_detected = False
    looking_detected = False
    last_gaze_angle = None
    last_phones = []
    last_hand_points = []
    last_gaze_info = None

    prev_time = time.time()
    fps = 0.0

    print("Started! Press 'Q' to quit & save, 'R' to reset, 'G' to toggle gaze ray.")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Camera frame dropped. Exiting...")
                break

            frame = cv2.resize(frame, (FRAME_W, FRAME_H))
            frame = cv2.flip(frame, 1)  # Mirror view for natural webcam testing

            now = time.time()
            dt = min(now - prev_time, 0.25)  # Clamp dt against brief stalls
            prev_time = now
            if dt > 0:
                fps = 0.85 * fps + 0.15 * (1.0 / dt)

            # Run MediaPipe models every Nth frame
            if frame_idx % DETECT_EVERY_N_FRAMES == 0:
                (
                    in_hand_detected,
                    looking_detected,
                    last_gaze_angle,
                    last_phones,
                    last_hand_points,
                    last_gaze_info,
                ) = run_detection(frame, phone_detector, hand_landmarker, face_landmarker)
            frame_idx += 1

            # --- A. Update Screen-Watching Timer (ONLY when looking at phone) ---
            if looking_detected:
                last_watch_seen_ts = now
                watching_active = True
            elif watching_active and (now - last_watch_seen_ts) > WATCH_GRACE_SEC:
                watching_active = False
                current_watch_streak = 0.0

            if watching_active:
                total_watch_sec += dt
                current_watch_streak += dt
                if state in ("IN_HAND", "COOLDOWN"):
                    pickup_watch_sec += dt

            # --- B. Update Hand-Pickup State Machine (Counts distinct pickups) ---
            confirm_progress = 0.0
            current_hold_sec = 0.0

            if state == "IDLE":
                if in_hand_detected:
                    state = "CONFIRMING"
                    state_timer = now

            elif state == "CONFIRMING":
                if not in_hand_detected:
                    state = "IDLE"
                    state_timer = None
                else:
                    elapsed = now - state_timer
                    confirm_progress = elapsed / CONFIRM_SEC
                    if elapsed >= CONFIRM_SEC:
                        state = "IN_HAND"
                        pickup_start = state_timer
                        pickup_watch_sec = 0.0
                        pickup_count += 1
                        ts_label = datetime.fromtimestamp(pickup_start).strftime("%H:%M:%S")
                        recent_events.appendleft(f"#{pickup_count} {ts_label} (holding...)")
                        print(f"[PICKUP] #{pickup_count} started at {ts_label}")

            elif state == "IN_HAND":
                confirm_progress = 1.0
                current_hold_sec = now - pickup_start
                if not in_hand_detected:
                    state = "COOLDOWN"
                    state_timer = now

            elif state == "COOLDOWN":
                current_hold_sec = state_timer - pickup_start
                confirm_progress = max(0.0, 1.0 - (now - state_timer) / COOLDOWN_SEC)
                if in_hand_detected:
                    state = "IN_HAND"
                elif now - state_timer >= COOLDOWN_SEC:
                    duration = state_timer - pickup_start
                    total_hold_sec += duration
                    log_pickup(conn, session_id, pickup_start, duration, pickup_watch_sec)
                    ts_label = datetime.fromtimestamp(pickup_start).strftime("%H:%M:%S")
                    if recent_events:
                        recent_events[0] = (
                            f"#{pickup_count} {ts_label} | held {duration:.1f}s | eye {pickup_watch_sec:.1f}s"
                        )
                    print(
                        f"[LOGGED] Pickup #{pickup_count}: held {duration:.1f}s, "
                        f"watched screen {pickup_watch_sec:.1f}s"
                    )
                    state = "IDLE"
                    pickup_start = None
                    state_timer = None
                    pickup_watch_sec = 0.0

            # --- C. Draw Visual Overlays on Camera Feed ---
            for hx, hy in last_hand_points:
                cv2.circle(frame, (hx, hy), 3, (255, 220, 0), -1)

            if last_gaze_info is not None:
                for ix, iy in last_gaze_info["irises"]:
                    cv2.circle(frame, (ix, iy), 3, (0, 255, 255), -1)

                if show_gaze_ray:
                    ex, ey = last_gaze_info["eye_center"]
                    gx, gy, _ = last_gaze_info["vec"]
                    end_pt = (int(ex + gx * 160), int(ey + gy * 160))
                    ray_color = (0, 0, 255) if watching_active else (0, 230, 255)
                    cv2.arrowedLine(
                        frame, (int(ex), int(ey)), end_pt, ray_color, 3, cv2.LINE_AA, tipLength=0.22
                    )

            for x1, y1, x2, y2, score, in_hand, is_watched in last_phones:
                if is_watched:
                    box_color = (0, 0, 255)
                    tag = f"WATCHING PHONE {score:.0%}"
                elif in_hand:
                    box_color = (0, 155, 255)
                    tag = f"IN HAND (NOT LOOKING) {score:.0%}"
                else:
                    box_color = (0, 220, 120)
                    tag = f"Phone {score:.0%}"

                cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)
                cv2.putText(
                    frame, tag, (x1, max(22, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, box_color, 2, cv2.LINE_AA
                )

            # Frame border cue: red when watching screen, orange when holding without looking
            if watching_active:
                cv2.rectangle(frame, (0, 0), (FRAME_W - 1, FRAME_H - 1), (0, 0, 255), 8)
            elif state == "IN_HAND":
                cv2.rectangle(frame, (0, 0), (FRAME_W - 1, FRAME_H - 1), (0, 155, 255), 4)

            # Stitch camera feed + side panel
            panel = build_side_panel(
                FRAME_H,
                PANEL_W,
                pickup_count,
                total_watch_sec,
                current_watch_streak,
                total_hold_sec + current_hold_sec,
                state,
                watching_active,
                confirm_progress,
                last_gaze_angle,
                recent_events,
                fps,
            )
            combined = np.hstack((frame, panel))
            cv2.imshow("Driver Phone & Gaze Monitor (MediaPipe)", combined)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            elif key == ord("g"):
                show_gaze_ray = not show_gaze_ray
            elif key == ord("r"):
                # Save finished session before resetting
                log_session(
                    conn, session_id, session_start_ts, pickup_count,
                    total_hold_sec + current_hold_sec, total_watch_sec
                )
                session_start_ts = time.time()
                session_id = f"drive_{int(session_start_ts)}"
                pickup_count = 0
                total_hold_sec = 0.0
                total_watch_sec = 0.0
                current_watch_streak = 0.0
                pickup_watch_sec = 0.0
                watching_active = False
                recent_events.clear()
                state = "IDLE"
                pickup_start = None
                state_timer = None
                print("[RESET] Started a new drive session.")

    finally:
        if state in ("IN_HAND", "COOLDOWN") and pickup_start is not None:
            end_ts = state_timer if state == "COOLDOWN" else time.time()
            duration = max(0.0, end_ts - pickup_start)
            total_hold_sec += duration
            log_pickup(conn, session_id, pickup_start, duration, pickup_watch_sec)

        log_session(conn, session_id, session_start_ts, pickup_count, total_hold_sec, total_watch_sec)

        cap.release()
        cv2.destroyAllWindows()
        phone_detector.close()
        hand_landmarker.close()
        face_landmarker.close()
        conn.close()
        print(
            f"\nSession saved to drive_logs.db | Pickups: {pickup_count} | "
            f"Hand Hold: {total_hold_sec:.1f}s | Watch Time: {total_watch_sec:.1f}s"
        )


if __name__ == "__main__":
    main()