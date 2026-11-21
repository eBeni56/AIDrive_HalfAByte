// The parts of the messages the dashboard reads. Full driver_state reference:
// driver_monitor/docs/detection-schema.md (source of truth: driver_monitor/src/driver_monitor/output/schema.py).

export type Severity = "NONE" | "WARNING" | "DANGER";

/** EYES_CLOSED, DISTRACTED (looking up or down: none of the 8 regions) or PHONE_USE. */
export interface Detection {
  id: string;
  type: "EYES_CLOSED" | "DISTRACTED" | "PHONE_USE";
  severity: Severity;
  status: "STARTED" | "ONGOING" | "ENDED";
  started_t: number;
  ended_t: number | null;
  duration_s: number;
  details: Record<string, unknown>;
}

export interface DriverState {
  type: "driver_state";
  schema_version: number;
  t: number;
  frame: number;
  summary: { highest_severity: Severity; active: string[] };
  quality: {
    fps: number;
    camera_fps: number | null;
    latency_ms: number;
    face_detected: boolean;
    faces_in_view: number;
    issues: string[];
  };
  calibration: { status: "CALIBRATING" | "DONE" | "DEFAULTS"; progress: number; message: string | null };
  eyes: { state: "OPEN" | "CLOSED" | "UNKNOWN"; state_for_s: number; openness: number | null };
  head: { yaw_deg: number | null; pitch_deg: number | null; roll_deg: number | null };
  gaze: { zone: string; zone_for_s: number; yaw_deg: number | null; pitch_deg: number | null };
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
