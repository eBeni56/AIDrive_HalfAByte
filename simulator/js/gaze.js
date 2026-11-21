// Gaze display: receives gaze_update from the server, shows a status badge (setup + driving view)
// and highlights the looked-at region on the first-person view. Display only; sends nothing.

// Latest state. `t` = performance.now() when the last valid message arrived (0 = never).
const gaze = { status: null, region: null, t: 0 };
const badgeEl = document.getElementById("gaze-badge");
let badgeKey = "";   // last rendered badge content, to skip needless DOM writes

const GAZE_STATUSES = ["ok", "no_face", "eyes_closed", "calibrating", "no_gaze_client"];

function gazeFresh(now) { return gaze.t > 0 && (now === undefined ? performance.now() : now) - gaze.t <= GAZE_TIMEOUT_MS; }

// Parse one incoming frame. Ignores anything invalid; never throws.
function handleGazeMessage(data) {
  try {
    if (typeof data !== "string") return;
    const m = JSON.parse(data);
    if (!m || typeof m !== "object" || Array.isArray(m) || m.event !== "gaze_update") return;
    if (!GAZE_STATUSES.includes(m.status)) return;
    const region = m.status === "ok" ? m.region : null;
    if (m.status === "ok" && !GAZE_REGIONS.includes(region)) return;
    gaze.status = m.status; gaze.region = region;
    gaze.t = performance.now();
    updateGazeBadge();
  } catch (e) { /* malformed message: ignore */ }
}

// Badge in the DOM: shown in setup and in the driving view.
function updateGazeBadge() {
  if (!badgeEl) return;
  let style, text;
  if (!gazeFresh()) { style = GAZE_STATUS_STYLE.stale; text = style.text; }
  else {
    if (gaze.status === "ok" && gaze.region === "away") { style = GAZE_STATUS_STYLE.away; text = "Gaze: AWAY"; }
    else if (gaze.status === "ok") { style = GAZE_STATUS_STYLE.ok; text = "Gaze: " + gaze.region; }
    else { style = GAZE_STATUS_STYLE[gaze.status]; text = style.text; }
  }
  const key = text + "|" + style.color;
  if (key === badgeKey) return;
  badgeKey = key;
  badgeEl.style.borderColor = style.color;
  badgeEl.firstElementChild.style.background = style.color;
  badgeEl.children[1].textContent = text;
}
setInterval(updateGazeBadge, GAZE_CHECK_MS);   // the 1.5 s expiry also works on the setup screen
updateGazeBadge();

// ------------------------------ REGION GEOMETRY --------------------------------------
// The ONE place that maps a region name to a screen shape. Returns a polygon [[x, y], ...] in CSS px
// built from the same geometry the cockpit is drawn with (pillarGeom, mirrorRects), or null for
// "away" / unknown.
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
  const outer = (s, y) => at_(P[s].outerB, P[s].outerT, y);   // side-window side of the bar
  if (region === "side_left")  return [[0, 0], [outer("left", 0), 0], [outer("left", P.yb), P.yb], [0, P.yb]];
  if (region === "side_right") return [[W, 0], [outer("right", 0), 0], [outer("right", P.yb), P.yb], [W, P.yb]];
  const i = { front_left: 0, front_center: 1, front_right: 2 }[region];
  if (i === undefined) return null;
  const at = (y, k) => { const l = inner("left", y), r = inner("right", y); return l + (r - l) * k / 3; };
  return [[at(yTop, i), yTop], [at(yTop, i + 1), yTop], [at(yBot, i + 1), yBot], [at(yBot, i), yBot]];
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
  if (!gazeFresh(now) || gaze.status !== "ok") return;
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
