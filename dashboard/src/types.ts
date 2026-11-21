// The fields of the server's messages that the dashboard reads (the messages carry more). Full driver_state
// reference: driver_monitor/docs/detection-schema.md; the attention messages: server_simulator_lookingdir/README.md.

/** EYES_CLOSED, DISTRACTED (looking up or down: none of the 8 regions) or PHONE_USE. */
interface Detection {
  id: string;
  type: "EYES_CLOSED" | "DISTRACTED" | "PHONE_USE";
  severity: "WARNING" | "DANGER";
  status: "STARTED" | "ONGOING" | "ENDED";
  duration_s: number;
  details: Record<string, unknown>; // DISTRACTED: zone ("UP" / "DOWN")
}

/** From the driver monitor, forwarded by the server DASHBOARD_HZ times per second. */
export interface DriverState {
  type: "driver_state";
  quality: { face_detected: boolean };
  calibration: { status: "CALIBRATING" | "DONE" | "DEFAULTS" };
  detections: Detection[];
}

/** From the server, GAZE_HZ times per second: the driver's gaze as a simulator region. */
export interface GazeUpdate {
  event: "gaze_update";
  status: "ok" | "no_face" | "eyes_closed" | "calibrating" | "no_gaze_client";
  region: string | null; // only with status "ok"
}

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
}

/** From the server (attention.py): an object of the simulation and whether the driver looked at it in time. */
export interface AttentionEvent {
  event: "attention_event";
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
