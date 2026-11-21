// Tunable constants. Loaded first; everything else reads these.

// ===================== CONFIG =====================
// Backend WebSocket URL. Served over http (docker compose): the page's own address + /ws, which
// nginx passes to the server container. Opened as a file (index.html / run.bat): the server directly.
const WS_URL = location.protocol.startsWith("http")
  ? `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`
  : "ws://localhost:8765";
const RECONNECT_MS = 2000;
const LANE_WIDTH_M = 3.5;               // real-world lane width
const LANE_WIDTH_PX = 110;              // lane width on screen
const PIXELS_PER_METER = LANE_WIDTH_PX / LANE_WIDTH_M;   // the ONE scale constant

// Real-world footprint of each type in meters (w = lateral, l = length). Drawn pointing "up" (ahead).
// Tiny things get a minimum on-screen size so they stay visible/grabbable on a projector.
const TYPES = {
  car:        { w: 1.9, l: 4.5, color: "#e5484d" },
  truck:      { w: 2.5, l: 8.0, color: "#f5a524" },
  bicycle:    { w: 0.7, l: 1.8, color: "#3ecf8e" },
  motorcycle: { w: 0.9, l: 2.2, color: "#b57bff" },
  pedestrian: { w: 0.9, l: 0.9, color: "#4cc2ff" },
};
const EGO_SIZE = { w: TYPES.car.w, l: TYPES.car.l };   // the ego car is a car
const EGO_COLOR = "#ffffff";

// ------------------------------ BEHAVIOR TABLE ---------------------------------------
// The one place to tweak how things move during DRIVING.
//
//   type in SIDE lane (left/right), kind "vehicle"  -> mode "overtake":
//        drives straight ahead (+y) at speed_mps, keeps its lateral x. FINISHED once y_m >= 80.
//        It keeps driving away afterwards; it just stops counting for the end check.
//   type in CENTER lane, kind "vehicle"             -> mode "parked":
//        never moves. FINISHED immediately.
//   kind "pedestrian", any lane                      -> mode "cross":
//        teleported at start onto the grass on the side it was placed nearer to, then walks
//        laterally (constant y_m) to the grass on the far side. FINISHED there, then stands still.
const BEHAVIOR = {
  car:        { kind: "vehicle",    speed_mps: 5 },
  motorcycle: { kind: "vehicle",    speed_mps: 5 },
  truck:      { kind: "vehicle",    speed_mps: 3.5 },
  bicycle:    { kind: "vehicle",    speed_mps: 2 },
  pedestrian: { kind: "pedestrian", speed_mps: 1.4 },
};
const SPEED_STEPS = [1, 2, 0.25, 0.5];   // "Speed" button cycle (setup only); multiplies object speeds only
const SPEED_JITTER = 0.10;        // each object gets speed * (1 +- up to 10%), re-rolled every run
const OVERTAKE_DONE_Y_M = 80;     // side-lane vehicle is FINISHED this far ahead of ego
const PED_GRASS_OFFSET_M = 1.5;   // pedestrian start/end point: this far outside the road edge
const COUNTDOWN_S = 3;            // "3, 2, 1" (world frozen), then GO
const END_DELAY_S = 3;            // after the last object finishes, keep running this long, then freeze
const TICK_HZ = 10;               // simulation_tick rate (independent of the render frame rate)

// ------------------------------ GAZE DISPLAY (gaze_update from the server) -----------
const GAZE_TIMEOUT_MS = 1500;     // no message for this long -> "No gaze data", highlight removed
const GAZE_CHECK_MS = 250;        // how often the badge re-checks the timeout
const GAZE_REGIONS = ["front_left", "front_center", "front_right", "side_left", "side_right",
                      "mirror_center", "mirror_left", "mirror_right", "away"];
const GAZE_REGION_LABEL = {
  front_left: "FRONT LEFT", front_center: "FRONT CENTER", front_right: "FRONT RIGHT",
  side_left: "LEFT SIDE WINDOW", side_right: "RIGHT SIDE WINDOW",
  mirror_center: "REAR-VIEW MIRROR", mirror_left: "LEFT MIRROR", mirror_right: "RIGHT MIRROR",
  away: "AWAY",
};
// Badge text + color for each status the server sends (the "ok" text is built from the region). A message
// with any other status is ignored.
const GAZE_STATUS_STYLE = {
  ok:              { color: "#2ecc71" },
  no_face:         { color: "#ff4d4d", text: "No face detected" },
  eyes_closed:     { color: "#ff4d4d", text: "Eyes closed" },
  calibrating:     { color: "#4da3ff", text: "Camera calibrating - look straight ahead" },
  no_gaze_client:  { color: "#8a8f9c", text: "No camera connected" },
};
const GAZE_AWAY_STYLE = { color: "#ffb020" };                        // "ok" with region "away"
const GAZE_STALE_STYLE = { color: "#8a8f9c", text: "No gaze data" };  // nothing received for GAZE_TIMEOUT_MS
// Highlight: red frame (the fill's alpha is added when drawing); the "AWAY" label in the away color
const GAZE_HIGHLIGHT = { fill: "rgba(255,40,40,", border: "#ff2a2a", away: GAZE_AWAY_STYLE.color };
