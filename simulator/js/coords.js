// Pixel <-> meter conversion, lanes and zones.

// ===================== COORDINATE MATH =====================
// World frame: origin = center of the ego car (= center of the viewport).
//   x_m: + to the RIGHT of ego, - to the left.
//   y_m: + AHEAD of ego (screen up), - behind. Screen y grows DOWN, so the sign is flipped.
// All metric values are screen pixels divided by PIXELS_PER_METER.
function egoCenter() { return { x: window.innerWidth / 2, y: window.innerHeight / 2 }; }

function pxToMeters(px, py) {
  const c = egoCenter();
  return { x_m: (px - c.x) / PIXELS_PER_METER,
           y_m: (c.y - py) / PIXELS_PER_METER };   // flipped: up on screen = ahead = positive
}
function metersToPx(x_m, y_m) {
  const c = egoCenter();
  return { x: c.x + x_m * PIXELS_PER_METER, y: c.y - y_m * PIXELS_PER_METER };
}

const ROAD_HALF_M = 1.5 * LANE_WIDTH_M;   // 3 lanes, ego in the middle: road spans +-5.25 m

// Which lane a lateral offset falls in. Center lane spans +-1.75 m around ego.
function laneFromX(x_m) {
  if (x_m < -LANE_WIDTH_M / 2) return "left";
  if (x_m >  LANE_WIDTH_M / 2) return "right";
  return "center";
}

// Position sent to the backend: the object's center on the top-down map, in pixels, relative to
// the ego car's center. x_px: + right of ego. y_px: + AHEAD of ego (not the screen's downward y).
// Fixed scale (PIXELS_PER_METER), so it does not depend on the window size.
function mapPx(x_m, y_m) {
  return { x_px: Math.round(x_m * PIXELS_PER_METER), y_px: Math.round(y_m * PIXELS_PER_METER) };
}
