// The parts of the messages the dashboard reads. Full driver_state reference:
// driver_monitor/docs/detection-schema.md (source of truth: driver_monitor/src/driver_monitor/output/schema.py).

export type Severity = "NONE" | "INFO" | "WARNING" | "DANGER" | "CRITICAL";

export interface Detection {
  id: string;
  type: string;
  severity: Severity;
  status: "STARTED" | "ONGOING" | "ENDED";
  started_t: number;
  ended_t: number | null;
  duration_s: number;
  details: Record<string, unknown>;
}

export interface DriverState {
  type: "driver_state";
  t: number;
  frame: number;
  summary: { highest_severity: Severity; primary: string | null; active: string[] };
  quality: {
    fps: number;
    camera_fps: number | null;
    latency_ms: number;
    face_detected: boolean;
    faces_in_view: number;
    face_lost_for_s: number;
    issues: string[];
  };
  calibration: { status: "CALIBRATING" | "DONE" | "DEFAULTS"; progress: number; message: string | null };
  eyes: {
    openness: number | null;
    state: "OPEN" | "ALMOST_CLOSED" | "CLOSED" | "UNKNOWN";
    state_for_s: number;
    trend: string;
    openness_ratio: number | null;
    last_blink: { ended_t: number; duration_ms: number } | null;
  };
  head: { yaw_deg: number | null; pitch_deg: number | null; roll_deg: number | null };
  gaze: {
    direction: string;
    yaw_deg: number | null;
    pitch_deg: number | null;
    on_road: boolean | null;
    zone: string;
    zone_for_s: number;
  };
  mouth: { state: string };
  attention: {
    state: "ATTENTIVE" | "CHECKING_MIRROR" | "GLANCING_AWAY" | "DISTRACTED" | "UNKNOWN";
    eyes_off_road_s: number;
    off_road_last_30s_s: number;
    weighted_off_road_30s_s: number;
    longest_glance_60s_s: number;
    glances_60s: number;
  };
  drowsiness: {
    level: "ALERT" | "SLIGHTLY_DROWSY" | "DROWSY" | "VERY_DROWSY" | "SEVERELY_DROWSY";
    level_index: number;
    score: number;
    warming_up: boolean;
    contributing: string[];
    fatigue_load: number;
    indicators: {
      perclos: number | null;
      blink_rate_per_min: number | null;
      yawns_5min: number;
      head_nods_5min: number;
      microsleeps_10min: number;
    };
  };
  detections: Detection[];
}

/** From the server, GAZE_HZ times per second: the driver's gaze as a simulator region. */
export interface GazeUpdate {
  event: "gaze_update";
  timestamp: number;
  status: "ok" | "no_face" | "eyes_closed" | "calibrating" | "no_gaze_client";
  region: string | null;
}

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  at: number; // Date.now() when it arrived
}

/** From the server (attention.py): an object of the simulation and whether the driver looked at it in time. */
export interface AttentionEvent {
  event: "attention_event";
  t_s: number; // seconds since simulation_started
  kind: "appeared" | "zone_change" | "noticed" | "missed";
  object_id: string;
  type: string;
  zone: string | null; // null: farther than 30 m (no zone)
  window_s: number | null; // seconds the driver has to look after the object entered the zone
  reaction_s: number | null; // only for "noticed"
}

export interface AttentionSummary {
  event: "attention_summary";
  duration_s: number;
  noticed_count: number;
  missed_count: number;
  avg_reaction_s: number | null;
  missed: { t_s: number; object_id: string; type: string; zone: string }[];
}
