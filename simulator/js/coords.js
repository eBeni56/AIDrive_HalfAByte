// Pixel <-> meter conversion, lanes and zones.

// ===================== COORDINATE MATH =====================
// World frame: origin = center of the ego car (= center of the setup screen's free area above the toolbar).
//   x_m: + to the RIGHT of ego, - to the left.
//   y_m: + AHEAD of ego (screen up), - behind. Screen y grows DOWN, so the sign is flipped.
// On the setup screen, metric values are screen pixels divided by SETUP_PX_PER_M (zoomed out).
function egoCenter() {
  const tb = document.getElementById("toolbar");
  return { x: window.innerWidth / 2, y: (window.innerHeight - (tb ? tb.offsetHeight : 0)) / 2 };
}

function pxToMeters(px, py) {
  const c = egoCenter();
  return { x_m: (px - c.x) / SETUP_PX_PER_M,
           y_m: (c.y - py) / SETUP_PX_PER_M };   // flipped: up on screen = ahead = positive
}
function metersToPx(x_m, y_m) {
  const c = egoCenter();
  return { x: c.x + x_m * SETUP_PX_PER_M, y: c.y - y_m * SETUP_PX_PER_M };
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
