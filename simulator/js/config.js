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
const PIXELS_PER_METER = LANE_WIDTH_PX / LANE_WIDTH_M;   // the ONE scale constant (also of the positions sent)
// The setup screen is drawn zoomed out by this factor (display only: the positions sent keep PIXELS_PER_METER),
// so objects can be placed farther ahead and behind the ego car.
const SETUP_ZOOM = 0.5;
const SETUP_PX_PER_M = PIXELS_PER_METER * SETUP_ZOOM;

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
//        drives straight ahead (+y) at speed_mps, keeps its lateral x. FINISHED once it has left the
//        minimap at the top. It keeps driving away afterwards; it just stops counting for the end check.
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
const PED_GRASS_OFFSET_M = 1.5;   // pedestrian start/end point: this far outside the road edge
// Calibration, when the page loads (and with the Calibrate button): "look at the number" and a countdown;
// meanwhile the camera sets the driver's straight-ahead look (the 0,0 of every gaze angle; it needs ~4 s:
// recenter_s in the driver monitor's config), so keep this longer than that.
const CALIBRATION_S = 5;
const CALIBRATION_TEXT = "Calibration: look at the number";
// The run ends at once when every object has driven off the minimap, and in any case after MAX_RUN_S of driving
// (objects that never leave it: parked in the centre lane, a pedestrian who has crossed). Not shown anywhere.
const MAX_RUN_S = 40;
const TICK_HZ = 10;               // simulation_tick rate (independent of the render frame rate)

// ------------------------------ GAZE DISPLAY (gaze_update from the server) -----------
const GAZE_TIMEOUT_MS = 1500;     // no message for this long -> highlight removed
// 6 of them get a rectangle: front (the whole windshield), mirror_left / mirror_right (looking leftish /
// rightish), mirror_center (a bit up) and side_left / side_right (head turned fully: the side window). away
// gets an "AWAY" label.
const GAZE_REGIONS = ["front", "side_left", "side_right", "mirror_center", "mirror_left", "mirror_right", "away"];
const GAZE_REGION_LABEL = {
  front: "FRONT",
  side_left: "LEFT SIDE WINDOW", side_right: "RIGHT SIDE WINDOW",
  mirror_center: "REAR-VIEW MIRROR", mirror_left: "LEFT MIRROR", mirror_right: "RIGHT MIRROR",
  away: "AWAY",
};
// The statuses the server sends (only "ok" has a region, and only it is highlighted); any other is ignored.
const GAZE_STATUSES = ["ok", "no_face", "eyes_closed", "calibrating", "no_gaze_client"];
// Highlight: red frame (the fill's alpha is added when drawing); the "AWAY" label in amber
const GAZE_HIGHLIGHT = { fill: "rgba(255,40,40,", border: "#ff2a2a", away: "#ffb020" };
