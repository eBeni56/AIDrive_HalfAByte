// Tunable constants. Loaded first; everything else reads these.

// ===================== CONFIG =====================
const WS_URL = "ws://localhost:8765";   // backend WebSocket URL
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
const EGO_SIZE = { w: 1.9, l: 4.5 };
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
