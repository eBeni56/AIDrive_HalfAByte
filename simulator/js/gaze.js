// Gaze display: receives gaze_update from the server and highlights the looked-at region on the first-person
// view. Display only; sends nothing. Only while driving (GO to the end, see setGazeActive): the straight-ahead
// look is set during the countdown, so before it there is nothing to show, and after the end the drive is over.

// Latest state. `t` = performance.now() when the last valid message arrived (0 = never).
const gaze = { status: null, region: null, t: 0, active: false };

function gazeFresh(now) { return gaze.t > 0 && (now === undefined ? performance.now() : now) - gaze.t <= GAZE_TIMEOUT_MS; }

// sim.js: true at GO, false at the end / abort. Forgets the last gaze, so an old one is never shown.
function setGazeActive(on) {
  gaze.active = on;
  gaze.status = null; gaze.region = null; gaze.t = 0;
}

// Parse one incoming frame. Ignores anything invalid (and everything outside the driving phase); never throws.
function handleGazeMessage(data) {
  try {
    if (!gaze.active || typeof data !== "string") return;
    const m = JSON.parse(data);
    if (!m || typeof m !== "object" || Array.isArray(m) || m.event !== "gaze_update") return;
    if (typeof m.status !== "string" || !GAZE_STATUSES.includes(m.status)) return;
    const region = m.status === "ok" ? m.region : null;
    if (m.status === "ok" && !GAZE_REGIONS.includes(region)) return;
    gaze.status = m.status; gaze.region = region;
    gaze.t = performance.now();
  } catch (e) { /* malformed message: ignore */ }
}

// ------------------------------ REGION GEOMETRY --------------------------------------
// The ONE place that maps a region name to a screen shape. Returns a polygon [[x, y], ...] in CSS px
// built from the same geometry the cockpit is drawn with (pillarGeom, mirrorRects), or null (no rectangle)
// for side_left / side_right (head turned fully to the side), "away" and unknown. Only 4 rectangles: the
// windshield and the 3 mirrors.
function gazeRegionShape(region, W, H) {
  const rect = r => [[r.x, r.y], [r.x + r.w, r.y], [r.x + r.w, r.y + r.h], [r.x, r.y + r.h]];
  if (region.startsWith("mirror_")) {
    const g = mirrorRects(W, H)[region], pad = 5;   // pad = the mirror housing frame
    return g ? rect({ x: g.x - pad, y: g.y - pad, w: g.w + 2 * pad, h: g.h + 2 * pad }) : null;
  }
  const P = pillarGeom(W, H);
  const yTop = H * 0.035, yBot = H * 0.76;          // below the roof liner, above the dashboard
  // x of a pillar edge at height y (the edges are straight lines from the bottom to the roof)
  const at_ = (b, t, y) => b + (t - b) * (P.yb - y) / P.yb;
  const inner = (s, y) => at_(P[s].innerB, P[s].innerT, y);   // windshield side of the bar
  if (region !== "front") return null;              // the whole windshield, pillar to pillar
  return [[inner("left", yTop), yTop], [inner("right", yTop), yTop],
          [inner("right", yBot), yBot], [inner("left", yBot), yBot]];
}

// ------------------------------ CANVAS HIGHLIGHT -------------------------------------
function gazeLabel(c, text, x, y, W, color) {
  c.font = "bold 22px system-ui, 'Segoe UI', Arial, sans-serif";
  c.textAlign = "center"; c.textBaseline = "middle";
  const w = c.measureText(text).width + 24;
  x = Math.max(w / 2 + 4, Math.min(W - w / 2 - 4, x));   // keep inside the screen
  rr(c, x - w / 2, y - 18, w, 36, 8);
  c.fillStyle = "rgba(0,0,0,.8)"; c.fill(); c.lineWidth = 2; c.strokeStyle = color; c.stroke();
  c.fillStyle = "#fff"; c.fillText(text, x, y + 1);
}

// Last layer of the frame: the highlight for the current gaze region (driving view only).
function drawGaze(c, W, H) {
  const now = performance.now();
  if (!gaze.active || !gazeFresh(now) || gaze.status !== "ok") return;
  const label = "LOOKING: " + GAZE_REGION_LABEL[gaze.region];
  const pulse = Math.sin(now / 250);
  c.save();
  if (gaze.region === "away") {
    gazeLabel(c, "AWAY", W / 2, H * 0.28, W, GAZE_HIGHLIGHT.away);
    c.restore(); return;
  }
  const pts = gazeRegionShape(gaze.region, W, H);
  if (!pts) { c.restore(); return; }
  c.beginPath();
  pts.forEach((p, k) => (k ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1])));
  c.closePath();
  c.fillStyle = GAZE_HIGHLIGHT.fill + (0.30 + 0.10 * pulse).toFixed(3) + ")"; c.fill();
  c.lineJoin = "round";
  c.lineWidth = 5 + pulse; c.strokeStyle = GAZE_HIGHLIGHT.border; c.stroke();
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  const isMirror = gaze.region.startsWith("mirror_");
  gazeLabel(c, label, cx, isMirror ? Math.max(...ys) + 30 : H * 0.2, W, GAZE_HIGHLIGHT.border);
  c.restore();
}
