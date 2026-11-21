// Drive phase rendering: cameras, projection, sprites, cockpit, mirrors, minimap, visibility.

// =====================================================================================
// ============================ DRIVE PHASE (first-person) ============================
// =====================================================================================
//
// COORDINATE CONVENTIONS — identical to setup (the backend receives x_m / y_m as map pixels, mapPx):
//   origin = center of the ego car, on the road surface
//   x_m    = lateral meters,  + right of ego, - left.  Center lane |x| <= 1.75, side lanes beyond.
//   y_m    = forward meters,  + ahead of ego, - behind.
//   z      = meters above the road (rendering only, never sent).
// The ego car never moves, so every speed is both "relative to us" and "over the ground".
// The camera positions below (driver's eye, mirrors) are used only for drawing and for visibility
// (o.vis; display-side only, the tick sends every object regardless); they never shift x_m / y_m.

// ------------------------------ CAMERAS ----------------------------------------------
const EYE = { x: -0.35, y: 0.0, z: 1.2 };  // driver's eye: left-hand drive, so a bit left of the car's center
const FOV_DEG = 110;                        // horizontal field of view of the windshield camera (wider = more
                                            // of the side lanes in view, everything smaller)
const HORIZON_FRAC = 0.42;                  // horizon line at 42% of screen height (more room for road;
                                            // equivalent to a lens shift, the projection stays a pinhole)
const NEAR_SPRITE_M = 0.5;                  // objects closer than this to a camera are not drawn by it
const NEAR_GROUND_M = 0.3;                  // near clipping plane for road polygons

// Mirror cameras sit at the mirror glass and look BACKWARD (see toCam for the axis handling).
// aimDeg angles a side mirror outward so the left mirror mostly shows the left lane behind us.
const MIRROR_CAMS = {
  mirror_center: { x: 0.0,   y: 0.5, z: 1.3, fovDeg: 70, aimDeg: 0 },
  mirror_left:   { x: -0.95, y: 0.7, z: 1.0, fovDeg: 60, aimDeg: -12 },
  mirror_right:  { x: 0.95,  y: 0.7, z: 1.0, fovDeg: 60, aimDeg: 12 },
};
// Text shown above objects in the first-person views (display only; events still use the real type)
const LABEL_NAME = { car: "car", truck: "truck", bicycle: "bike", motorcycle: "motorbike", pedestrian: "pedestrian" };
const VIEW_NAMES = ["windshield", "mirror_center", "mirror_left", "mirror_right"];

// Real-world size of each sprite in the first-person view, meters (w = width, h = height).
// Length for "which face points at the camera" comes from TYPES[type].l.
const SPRITE = {
  car:        { w: 1.9, h: 1.45 },
  truck:      { w: 2.5, h: 3.6 },
  bicycle:    { w: 0.6, h: 1.75 },   // incl. rider
  motorcycle: { w: 0.8, h: 1.55 },   // incl. rider
  pedestrian: { w: 0.5, h: 1.75 },
};

// Road markings (meters)
const EDGE_LINE_W = 0.2, DASH_W = 0.15, DASH_LEN = 3, DASH_GAP = 6, DASH_RANGE = 300;
const POST_SPACING = 25, POST_RANGE = 300;   // roadside delineator posts, for depth cues

// ------------------------------ PROJECTION -------------------------------------------
// A "view" is one virtual pinhole camera plus the canvas rectangle it paints into:
//   { name, camX, camY, camZ, back, aim, focal, cx, horizonY, clip: {x, y, w, h} }
//
// Step 1 — toCam(): world (x, y) -> camera frame (lat, depth)
//   Windshield (back = false):
//       depth = y - camY       meters in front of the camera
//       lat   = x - camX       meters to the camera's right
//   Mirror (back = true): the camera is turned around to look behind us.
//       depth = camY - y       "in front of the mirror camera" now means BEHIND the car
//       lat   = x - camX       <- deliberately NOT negated. Turning a camera around flips left/right
//                                 (something on our left would land on the right of the image), and a
//                                 mirror flips left/right once more. The two flips cancel, so an object
//                                 behind us on the LEFT (x < 0) is drawn on the LEFT of every mirror —
//                                 exactly what a driver sees.
//   aim (side mirrors only): the glass is angled outward, so we rotate (lat, depth) by `aim` radians
//       in the ground plane. aim < 0 swings the view toward the left lane, aim > 0 toward the right.
//       With aim = 0 it is just the plain turned-around camera.
// Step 2 — pinhole projection, focal length in pixels:
//       focal    = (view_width / 2) / tan(fov_horizontal / 2)
//       scale    = focal / depth                      pixels per meter at that depth (sprite_scale)
//       screen_x = cx       + lat * scale             (= cx + (lat / depth) * focal)
//       screen_y = horizonY + (camZ - z) * scale      z = 0 is the road; z > camZ draws above the horizon
function toCam(v, x, y) {
  let lat = x - v.camX;
  let depth = v.back ? v.camY - y : y - v.camY;
  if (v.aim) {
    const c = Math.cos(v.aim), s = Math.sin(v.aim);
    const lat2 = lat * c - depth * s;
    depth = lat * s + depth * c;
    lat = lat2;
  }
  return { lat, depth };
}

// Project the ground point under an object. null if it is behind / too close to this camera.
function projectBase(v, x, y) {
  const p = toCam(v, x, y);
  if (p.depth <= NEAR_SPRITE_M) return null;
  const scale = v.focal / p.depth;
  return { depth: p.depth, scale, sx: v.cx + p.lat * scale, sy: v.horizonY + v.camZ * scale };
}

// Fill a flat rectangle on the road (x0..x1, y0..y1, z = 0). The polygon is first clipped against the
// camera's near plane (one-plane Sutherland-Hodgman) so a quad that starts behind the camera and runs
// far ahead still projects to a correct trapezoid. Clipping in camera space is valid because toCam is
// a rotation + translation, so straight lines stay straight.
function groundQuad(c, v, x0, y0, x1, y1) {
  const pts = [toCam(v, x0, y0), toCam(v, x1, y0), toCam(v, x1, y1), toCam(v, x0, y1)];
  const out = [];
  for (let i = 0; i < 4; i++) {
    const a = pts[i], b = pts[(i + 1) % 4];
    const aIn = a.depth >= NEAR_GROUND_M, bIn = b.depth >= NEAR_GROUND_M;
    if (aIn) out.push(a);
    if (aIn !== bIn) {
      const t = (NEAR_GROUND_M - a.depth) / (b.depth - a.depth);
      out.push({ lat: a.lat + t * (b.lat - a.lat), depth: NEAR_GROUND_M });
    }
  }
  if (out.length < 3) return;
  c.beginPath();
  for (let i = 0; i < out.length; i++) {
    const s = v.focal / out[i].depth;
    const sx = v.cx + out[i].lat * s, sy = v.horizonY + v.camZ * s;
    if (i === 0) c.moveTo(sx, sy); else c.lineTo(sx, sy);
  }
  c.closePath();
  c.fill();
}

// ------------------------------ SPRITES ----------------------------------------------
const INK = "#14161b", GLASS = "#9fd3ff", SKIN = "#f1d3b0", CLOTH = "#2b2f3a";

function rr(c, x, y, w, h, r) {   // rounded-rect path
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  c.beginPath();
  c.moveTo(x + r, y);
  c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r);
  c.arcTo(x, y + h, x, y, r);         c.arcTo(x, y, x + w, y, r);
  c.closePath();
}
function fillOutlined(c, fill, lw) { c.fillStyle = fill; c.fill(); c.lineWidth = lw; c.strokeStyle = INK; c.stroke(); }
function line(c, x0, y0, x1, y1) { c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1); c.stroke(); }
function dot(c, x, y, r) { c.beginPath(); c.arc(x, y, Math.max(0.5, r), 0, 2 * Math.PI); }

// Draw one object with its bottom-center at (sx, sy) and size w x h pixels.
// front = true: we see the object's front (mirrors); false: its rear (windshield).
function drawSprite(c, type, sx, sy, w, h, color, front, walked) {
  const x0 = sx - w / 2, top = sy - h, lw = Math.max(1, w * 0.025);
  const lamp = front ? "#fff4b8" : "#ff2a2a";
  c.lineCap = "round";
  c.fillStyle = "rgba(0,0,0,.35)";                                   // ground shadow
  c.beginPath(); c.ellipse(sx, sy, w * 0.6, Math.max(1, h * 0.05), 0, 0, 2 * Math.PI); c.fill();

  switch (type) {
    case "car":
      c.fillStyle = INK;                                              // tyres
      c.fillRect(x0 + w * 0.05, sy - h * 0.2, w * 0.2, h * 0.2);
      c.fillRect(x0 + w * 0.75, sy - h * 0.2, w * 0.2, h * 0.2);
      rr(c, x0 + w * 0.14, top, w * 0.72, h * 0.5, w * 0.12); fillOutlined(c, color, lw);   // cabin
      rr(c, x0 + w * 0.2, top + h * 0.07, w * 0.6, h * 0.34, w * 0.07); c.fillStyle = GLASS; c.fill();
      rr(c, x0, sy - h * 0.62, w, h * 0.47, w * 0.1); fillOutlined(c, color, lw);           // body
      c.fillStyle = lamp;
      rr(c, x0 + w * 0.05, sy - h * 0.53, w * 0.18, h * 0.1, w * 0.03); c.fill();
      rr(c, x0 + w * 0.77, sy - h * 0.53, w * 0.18, h * 0.1, w * 0.03); c.fill();
      c.fillStyle = front ? INK : "#f2f2f2";                          // grille / number plate
      c.fillRect(sx - w * 0.13, sy - h * 0.36, w * 0.26, h * 0.09);
      break;

    case "truck":
      c.fillStyle = INK;
      c.fillRect(x0 + w * 0.04, sy - h * 0.12, w * 0.22, h * 0.12);
      c.fillRect(x0 + w * 0.74, sy - h * 0.12, w * 0.22, h * 0.12);
      if (front) {                                                    // cab, big windshield
        rr(c, x0, top + h * 0.04, w, h * 0.84, w * 0.08); fillOutlined(c, color, lw);
        rr(c, x0 + w * 0.08, top + h * 0.12, w * 0.84, h * 0.3, w * 0.04); c.fillStyle = GLASS; c.fill();
        c.fillStyle = INK; c.fillRect(x0 + w * 0.25, sy - h * 0.42, w * 0.5, h * 0.18);
        c.fillStyle = lamp;
        c.fillRect(x0 + w * 0.05, sy - h * 0.3, w * 0.15, h * 0.07);
        c.fillRect(x0 + w * 0.8, sy - h * 0.3, w * 0.15, h * 0.07);
      } else {                                                        // cargo box, rear doors
        rr(c, x0, top, w, h * 0.86, w * 0.03); fillOutlined(c, color, lw);
        c.strokeStyle = INK; c.lineWidth = lw; line(c, sx, top + h * 0.03, sx, top + h * 0.83);
        c.fillStyle = INK; c.fillRect(x0, sy - h * 0.17, w, h * 0.06);   // bumper
        c.fillStyle = lamp;
        c.fillRect(x0 + w * 0.03, sy - h * 0.26, w * 0.12, h * 0.06);
        c.fillRect(x0 + w * 0.85, sy - h * 0.26, w * 0.12, h * 0.06);
      }
      break;

    case "bicycle":
    case "motorcycle": {
      const moto = type === "motorcycle";
      const tyreW = w * (moto ? 0.24 : 0.14);
      rr(c, sx - tyreW / 2, sy - h * 0.4, tyreW, h * 0.4, tyreW / 2); c.fillStyle = INK; c.fill();   // wheel, edge-on
      if (moto) { rr(c, sx - w * 0.32, sy - h * 0.58, w * 0.64, h * 0.24, w * 0.12); fillOutlined(c, color, lw); }
      c.fillStyle = INK; c.fillRect(x0 + w * 0.04, sy - h * 0.55, w * 0.92, Math.max(1, h * 0.03));   // handlebar
      c.strokeStyle = CLOTH; c.lineWidth = Math.max(1, w * 0.14);                                       // legs
      line(c, sx - w * 0.13, sy - h * 0.5, sx - w * 0.17, sy - h * 0.18);
      line(c, sx + w * 0.13, sy - h * 0.5, sx + w * 0.17, sy - h * 0.22);
      rr(c, sx - w * 0.3, top + h * 0.17, w * 0.6, h * 0.38, w * 0.2);                                // torso
      fillOutlined(c, moto ? CLOTH : color, lw);
      c.strokeStyle = moto ? CLOTH : color; c.lineWidth = Math.max(1, w * 0.12);                       // arms
      line(c, sx - w * 0.26, top + h * 0.25, x0 + w * 0.08, sy - h * 0.53);
      line(c, sx + w * 0.26, top + h * 0.25, x0 + w * 0.92, sy - h * 0.53);
      dot(c, sx, top + h * 0.09, w * (moto ? 0.22 : 0.17)); fillOutlined(c, moto ? color : SKIN, lw);  // head / helmet
      if (moto) { c.fillStyle = INK; c.fillRect(sx - w * 0.15, top + h * 0.07, w * 0.3, h * 0.04); }   // visor
      dot(c, sx, sy - h * 0.45, w * 0.08); c.fillStyle = lamp; c.fill();
      break;
    }

    case "pedestrian": {                      // side view, walking: legs/arms swing with distance walked
      const swing = Math.sin(walked * 3.2) * w * 0.45;
      const hipY = sy - h * 0.47, neckY = top + h * 0.2;
      c.strokeStyle = CLOTH; c.lineWidth = Math.max(1, w * 0.24);
      line(c, sx, hipY, sx + swing, sy); line(c, sx, hipY, sx - swing, sy);
      c.strokeStyle = INK; c.lineWidth = Math.max(2, w * 0.55 + 2 * lw); line(c, sx, neckY + w * 0.2, sx, hipY);
      c.strokeStyle = color; c.lineWidth = Math.max(1, w * 0.55);     line(c, sx, neckY + w * 0.2, sx, hipY);
      c.strokeStyle = SKIN; c.lineWidth = Math.max(1, w * 0.15);
      line(c, sx, neckY + h * 0.03, sx - swing * 0.8, hipY + h * 0.02);
      line(c, sx, neckY + h * 0.03, sx + swing * 0.8, hipY + h * 0.02);
      dot(c, sx, top + h * 0.08, h * 0.075); fillOutlined(c, SKIN, lw);
      break;
    }
  }
}

function drawLabel(c, text, x, y, px) {
  c.font = `bold ${px}px system-ui, "Segoe UI", Arial, sans-serif`;
  c.textAlign = "center"; c.textBaseline = "bottom";
  c.lineWidth = 3; c.strokeStyle = "rgba(0,0,0,.85)"; c.strokeText(text, x, y - 3);
  c.fillStyle = "#fff"; c.fillText(text, x, y - 3);
}

function rectsOverlap(a, b) {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}

// ------------------------------ SCENE ------------------------------------------------
// Draw sky, ground, road and objects for one view, and record per object whether this view shows it.
function drawWorld(c, v, objs) {
  const { x, y, w, h } = v.clip;
  const sky = c.createLinearGradient(0, y, 0, v.horizonY);
  sky.addColorStop(0, "#6fa8dc"); sky.addColorStop(1, "#d4e9f6");
  c.fillStyle = sky; c.fillRect(x, y, w, v.horizonY - y);
  const treeLine = Math.max(2, v.focal * 0.012);
  c.fillStyle = "#2e5a34"; c.fillRect(x, v.horizonY - treeLine, w, treeLine);
  c.fillStyle = "#4a8a3f"; c.fillRect(x, v.horizonY, w, y + h - v.horizonY);

  // Road as one long quad (both directions; each camera's near-plane clip keeps only its half),
  // then solid edge lines and dashed lane lines.
  const R = ROAD_HALF_M, FAR = 3000;
  c.fillStyle = "#3b3e45"; groundQuad(c, v, -R, -FAR, R, FAR);
  c.fillStyle = "#f4f4f4";
  groundQuad(c, v, -R, -FAR, -R + EDGE_LINE_W, FAR);
  groundQuad(c, v, R - EDGE_LINE_W, -FAR, R, FAR);
  for (const lx of [-LANE_WIDTH_M / 2, LANE_WIDTH_M / 2]) {
    for (let yy = -DASH_RANGE; yy < DASH_RANGE; yy += DASH_LEN + DASH_GAP) {
      groundQuad(c, v, lx - DASH_W / 2, yy, lx + DASH_W / 2, yy + DASH_LEN);
    }
  }

  // Delineator posts, far to near.
  for (let i = 0; i <= (2 * POST_RANGE) / POST_SPACING; i++) {
    const yy = v.back ? -POST_RANGE + i * POST_SPACING : POST_RANGE - i * POST_SPACING;
    for (const side of [-1, 1]) {
      const p = projectBase(v, side * (R + 1.2), yy);
      if (!p || p.scale < 2) continue;
      const pw = Math.max(1, 0.15 * p.scale), ph = 1.0 * p.scale;
      c.fillStyle = "#eeeeee"; c.fillRect(p.sx - pw / 2, p.sy - ph, pw, ph);
      c.fillStyle = "#111";    c.fillRect(p.sx - pw / 2, p.sy - ph * 0.85, pw, ph * 0.15);
    }
  }

  // Objects. Each is a flat billboard standing on the face that points at this camera:
  // its rear (y - length/2) in the windshield, its front (y + length/2) in a mirror.
  const list = [];
  for (const o of objs) {
    o.vis[v.name] = false;
    const half = o.type === "pedestrian" ? 0 : TYPES[o.type].l / 2;
    const p = projectBase(v, o.x_m, v.back ? o.y_m + half : o.y_m - half);
    if (p) list.push({ o, p });
  }
  list.sort((a, b) => b.p.depth - a.p.depth);                 // far to near: nearer ones overlap
  for (const { o, p } of list) {
    const pw = SPRITE[o.type].w * p.scale, ph = SPRITE[o.type].h * p.scale;
    drawSprite(c, o.type, p.sx, p.sy, pw, ph, TYPES[o.type].color, v.back, o.walked);
    // Visibility comes from exactly this rectangle: the sprite's on-screen box after projection,
    // tested against what is really open glass for this view.
    const box = { x: p.sx - pw / 2, y: p.sy - ph, w: pw, h: ph };
    o.vis[v.name] = v.back ? rectsOverlap(box, v.clip) : windshieldSees(box);
    if (ph > 22) drawLabel(c, LABEL_NAME[o.type] || o.type, p.sx, p.sy - ph, v.back ? 11 : 16);
  }
}

// ------------------------------ COCKPIT & MIRRORS ------------------------------------
// A-pillar geometry (CSS px): each bar runs from y = yb (bottom, B) up to y = 0 (roof, T);
// outer = side-window edge, inner = windshield edge. Used by drawCockpit and the gaze highlight.
const PILLAR_INSET = 0.07;   // how far both pillars sit in from the screen edges (fraction of W); larger = narrower windshield
function pillarGeom(W, H) {
  const d = PILLAR_INSET;
  return { yb: H * 0.82,
    left:  { outerB: W * d,       innerB: W * (0.075 + d), innerT: W * (0.175 + d), outerT: W * (0.105 + d) },
    right: { outerB: W * (1 - d), innerB: W * (0.945 - d), innerT: W * (0.85 - d),  outerT: W * (0.9 - d) } };
}

// Static cockpit overlay. Also drawn once into an offscreen "occlusion mask" (see buildMask), so
// whatever this function covers really hides objects for visibility.
function drawCockpit(c, W, H) {
  const body = "#16181d", dash = "#23262e", rim = "#0b0c0f";
  c.fillStyle = body;
  c.fillRect(0, 0, W, H * 0.035);                            // roof liner
  // A-pillars: slanted bars, leaning inward toward the roof. Outside each bar is side-window glass.
  // The right one is farther from the (left-seated) driver, so it looks thinner.
  const P = pillarGeom(W, H);
  for (const s of [P.left, P.right]) {
    c.beginPath(); c.moveTo(s.outerB, P.yb); c.lineTo(s.innerB, P.yb); c.lineTo(s.innerT, 0); c.lineTo(s.outerT, 0);
    c.closePath(); c.fill();
  }
  // Dashboard across the bottom, with an instrument hood behind the wheel.
  c.fillStyle = dash;
  c.beginPath(); c.moveTo(0, H * 0.8); c.quadraticCurveTo(W / 2, H * 0.73, W, H * 0.8);
  c.lineTo(W, H); c.lineTo(0, H); c.closePath(); c.fill();
  c.fillStyle = body;
  c.beginPath(); c.ellipse(W / 2, H * 0.79, W * 0.12, H * 0.06, 0, Math.PI, 2 * Math.PI); c.fill();
  // Steering wheel: rim + two spokes; the hub is below the screen.
  const r = H * 0.36, wy = H * 1.08;
  c.strokeStyle = rim; c.lineCap = "butt";
  c.lineWidth = H * 0.045; c.beginPath(); c.arc(W / 2, wy, r, 0, 2 * Math.PI); c.stroke();
  c.lineWidth = H * 0.05;
  c.beginPath(); c.moveTo(W / 2 - r * 0.93, wy - r * 0.35); c.lineTo(W / 2, wy); c.lineTo(W / 2 + r * 0.93, wy - r * 0.35); c.stroke();
}

// Mirror layout on screen (glass rectangles, CSS px). body = which edge shows our own car's flank.
function mirrorRects(W, H) {
  const cw = Math.min(560, Math.max(220, W * 0.24)), ch = cw * 0.27;
  const sw = Math.min(300, Math.max(120, W * 0.13)), sh = sw * 0.6, m = W * 0.012;
  return {
    mirror_center: { x: W / 2 - cw / 2, y: H * 0.05,  w: cw, h: ch, body: 0 },
    mirror_left:   { x: m,              y: H * 0.545, w: sw, h: sh, body: +1 },  // flank on its inner (right) edge
    mirror_right:  { x: W - m - sw,     y: H * 0.545, w: sw, h: sh, body: -1 },  // flank on its inner (left) edge
  };
}

function drawMirrorHousing(c, m) {   // opaque frame (and stem) behind the glass
  const g = m.glass, pad = 5;
  c.fillStyle = "#0d0e11";
  if (m.name === "mirror_center") c.fillRect(g.x + g.w / 2 - 8, 0, 16, g.y);
  rr(c, g.x - pad, g.y - pad, g.w + 2 * pad, g.h + 2 * pad, 10);
  c.fill(); c.lineWidth = 2; c.strokeStyle = "#5a5f6b"; c.stroke();
}

function drawMirrorGlass(c, m) {     // our car's flank, dark tint, thin inner border
  const g = m.glass;
  if (g.body) {
    const bx = g.body > 0 ? g.x + g.w - m.bodyW : g.x;
    c.fillStyle = "#c8ccd4"; c.fillRect(bx, g.y, m.bodyW, g.h);
    c.fillStyle = "#9aa0aa"; c.fillRect(bx, g.y + g.h * 0.55, m.bodyW, g.h * 0.07);
    c.fillStyle = INK;       c.fillRect(g.body > 0 ? bx : bx + m.bodyW - 2, g.y, 2, g.h);
  }
  c.fillStyle = "rgba(10,20,40,.22)"; c.fillRect(g.x, g.y, g.w, g.h);
  c.lineWidth = 1; c.strokeStyle = "rgba(255,255,255,.35)"; c.strokeRect(g.x + 0.5, g.y + 0.5, g.w - 1, g.h - 1);
}

// ------------------------------ MINIMAP ----------------------------------------------
// Top-down overview in its own full-height panel on the right (separate canvas), drawn in the same
// style as the setup screen.
// Shows every object's setup position (faded icon) and a dashed trail to where it is now.
// Display only: it is NOT part of the cockpit occlusion mask, so it never affects visibility.
const MINIMAP = {
  widthFrac: 0.30,     // panel width as a fraction of the window width (the driving view gets the rest)
  margin: 6,           // gap between the panel edge and the map, px
  halfWidthM: 8,       // lateral meters shown on each side of ego (road is +-5.25 m, rest is grass)
  egoFrac: 0.62,       // ego's vertical position in the panel, from the top (more room ahead than behind)
};

// Setup SVG icons as images for canvas drawing (needs xmlns + explicit size to rasterize everywhere).
const iconImgs = {};
function iconImage(key, type, color) {
  if (!iconImgs[key]) {
    const svg = svgFor(type, color);
    const vb = svg.match(/viewBox="0 0 (\d+) (\d+)"/);
    const img = new Image();
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(
      svg.replace("<svg ", `<svg xmlns="http://www.w3.org/2000/svg" width="${vb[1] * 3}" height="${vb[2] * 3}" `));
    iconImgs[key] = img;
  }
  return iconImgs[key];
}
for (const t of Object.keys(TYPES)) iconImage(t, t, TYPES[t].color);   // preload
iconImage("ego", "car", EGO_COLOR);

function miniLayout(PW, H) {
  // The map fills the panel. Scale from the width (+-halfWidthM); the height shows as much as fits.
  const x = MINIMAP.margin, y = MINIMAP.margin, w = PW - 2 * x, h = H - 2 * y;
  const scale = w / (2 * MINIMAP.halfWidthM);
  return { x, y, w, h, scale, yAhead: (h / scale) * MINIMAP.egoFrac };
}

function drawMinimap(c) {
  const m = VW.mini, s = m.scale;
  const toMap = (x_m, y_m) => ({ x: m.x + m.w / 2 + x_m * s, y: m.y + (m.yAhead - y_m) * s });
  const icon = (img, x_m, y_m, w_m, l_m, alpha) => {
    if (!img.complete) return;
    const p = toMap(x_m, y_m), w = Math.max(w_m * s, 7), h = Math.max(l_m * s, 7);
    c.globalAlpha = alpha; c.drawImage(img, p.x - w / 2, p.y - h / 2, w, h); c.globalAlpha = 1;
  };

  c.save();
  rr(c, m.x - 4, m.y - 4, m.w + 8, m.h + 8, 9);                      // frame
  c.fillStyle = "#12141a"; c.fill(); c.lineWidth = 2; c.strokeStyle = "#5b6275"; c.stroke();
  c.beginPath(); c.rect(m.x, m.y, m.w, m.h); c.clip();

  // Grass + tufts, road, edge lines, dashed lane lines (same colors/proportions as setup)
  c.fillStyle = "#25552b"; c.fillRect(m.x, m.y, m.w, m.h);
  const roadL = toMap(-ROAD_HALF_M, 0).x, roadW = 2 * ROAD_HALF_M * s, grassW = (m.w - roadW) / 2;
  c.fillStyle = "rgba(47,107,54,.8)";
  for (let i = 0; i < 12; i++) {
    const fx = (i * 0.37 + 0.11) % 1, fy = (i * 0.61 + 0.05) % 1;
    const tx = fx < 0.5 ? m.x + 2 + fx * 2 * (grassW - 10) : roadL + roadW + 4 + (fx - 0.5) * 2 * (grassW - 10);
    c.beginPath(); c.ellipse(tx + 3, m.y + fy * m.h, 3.5, 3, 0, Math.PI, 2 * Math.PI); c.fill();
  }
  c.fillStyle = "#2a2d33"; c.fillRect(roadL, m.y, roadW, m.h);
  const edge = Math.max(1.5, (5 / PIXELS_PER_METER) * s);
  c.fillStyle = "#fff";
  c.fillRect(roadL - edge, m.y, edge, m.h); c.fillRect(roadL + roadW, m.y, edge, m.h);
  const dash = (40 / PIXELS_PER_METER) * s;
  c.strokeStyle = "#fff"; c.lineWidth = Math.max(1, (4 / PIXELS_PER_METER) * s); c.setLineDash([dash, dash]);
  for (const lx of [-LANE_WIDTH_M / 2, LANE_WIDTH_M / 2]) line(c, toMap(lx, 0).x, m.y, toMap(lx, 0).x, m.y + m.h);

  // Trails: dashed line from each object's setup position to its current position
  c.setLineDash([4, 3]); c.lineWidth = 2;
  for (const o of sim.objs) {
    if (Math.hypot(o.x_m - o.x0, o.y_m - o.y0) < 0.3) continue;
    const a = toMap(o.x0, o.y0), b = toMap(o.x_m, o.y_m);
    c.strokeStyle = TYPES[o.type].color; c.globalAlpha = 0.8; line(c, a.x, a.y, b.x, b.y); c.globalAlpha = 1;
  }
  c.setLineDash([]);

  // Faded icons at setup positions, then ego (with ring), then objects at their current positions
  for (const o of sim.objs) {
    if (Math.hypot(o.x_m - o.x0, o.y_m - o.y0) >= 0.3) icon(iconImgs[o.type], o.x0, o.y0, TYPES[o.type].w, TYPES[o.type].l, 0.3);
  }
  const e = toMap(0, 0), ew = EGO_SIZE.w * s + 6, eh = EGO_SIZE.l * s + 6;
  rr(c, e.x - ew / 2, e.y - eh / 2, ew, eh, 4); c.lineWidth = 2; c.strokeStyle = "#ffd54a"; c.stroke();
  icon(iconImgs.ego, 0, 0, EGO_SIZE.w, EGO_SIZE.l, 1);
  for (const o of sim.objs) icon(iconImgs[o.type], o.x_m, o.y_m, TYPES[o.type].w, TYPES[o.type].l, 1);

  // Objects beyond the map edge: small arrow at the edge + distance
  c.font = "bold 10px system-ui, 'Segoe UI', Arial, sans-serif"; c.textAlign = "center";
  for (const o of sim.objs) {
    const p = toMap(o.x_m, o.y_m), ahead = p.y < m.y, behind = p.y > m.y + m.h;
    if (!ahead && !behind) continue;
    const ay = ahead ? m.y + 2 : m.y + m.h - 2, dir = ahead ? 1 : -1;
    c.fillStyle = TYPES[o.type].color;
    c.beginPath(); c.moveTo(p.x, ay); c.lineTo(p.x - 6, ay + dir * 9); c.lineTo(p.x + 6, ay + dir * 9); c.closePath(); c.fill();
    c.textBaseline = ahead ? "top" : "bottom";
    c.lineWidth = 3; c.strokeStyle = "rgba(0,0,0,.85)";
    const txt = Math.round(Math.abs(o.y_m)) + " m";
    c.strokeText(txt, p.x, ay + dir * 11); c.fillStyle = "#fff"; c.fillText(txt, p.x, ay + dir * 11);
  }
  c.restore();
}

// ------------------------------ VIEW GEOMETRY & VISIBILITY ---------------------------
const canvas = document.getElementById("view");
const ctx = canvas.getContext("2d");
let VW = null;   // { W, H, PW, main, mirrors, mask, mini }, rebuilt on resize. W x H = driving view, PW = minimap panel width
const miniCanvas = document.getElementById("minimap");
const mctx = miniCanvas.getContext("2d");

function rad(d) { return (d * Math.PI) / 180; }

function resizeView() {
  const H = window.innerHeight, dpr = window.devicePixelRatio || 1;
  const PW = Math.round(window.innerWidth * MINIMAP.widthFrac), W = window.innerWidth - PW;
  document.documentElement.style.setProperty("--mini-w", PW + "px");
  canvas.style.width = W + "px"; canvas.style.height = H + "px";
  canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);                     // draw in CSS pixels
  miniCanvas.style.width = PW + "px"; miniCanvas.style.height = H + "px";
  miniCanvas.width = Math.round(PW * dpr); miniCanvas.height = Math.round(H * dpr);
  mctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const main = { name: "windshield", camX: EYE.x, camY: EYE.y, camZ: EYE.z, back: false, aim: 0,
    focal: (W / 2) / Math.tan(rad(FOV_DEG) / 2), cx: W / 2, horizonY: H * HORIZON_FRAC,
    clip: { x: 0, y: 0, w: W, h: H } };
  const rects = mirrorRects(W, H);
  const mirrors = Object.keys(MIRROR_CAMS).map(name => {
    const m = MIRROR_CAMS[name], g = rects[name], bodyW = g.body ? g.w * 0.2 : 0;
    return { name, camX: m.x, camY: m.y, camZ: m.z, back: true, aim: rad(m.aimDeg),
      focal: (g.w / 2) / Math.tan(rad(m.fovDeg) / 2), cx: g.x + g.w / 2, horizonY: g.y + g.h * 0.45,
      glass: g, bodyW,
      clip: { x: g.body < 0 ? g.x + bodyW : g.x, y: g.y, w: g.w - bodyW, h: g.h } };  // glass minus our flank
  });
  VW = { W, H, PW, main, mirrors, mask: buildMask(W, H, mirrors), mini: miniLayout(PW, H) };
}

// Occlusion mask for the windshield: the cockpit and the mirror housings drawn once, opaque, into an
// offscreen canvas. A pixel with alpha < 128 is open glass (windshield or front side window).
function buildMask(W, H, mirrors) {
  const off = document.createElement("canvas");
  off.width = W; off.height = H;
  const c = off.getContext("2d", { willReadFrequently: true });
  drawCockpit(c, W, H);
  for (const m of mirrors) drawMirrorHousing(c, m);
  return c.getImageData(0, 0, W, H).data;
}

// "windshield" visibility: the sprite box, cut by the canvas edge (the frame), must contain at least
// one pixel of open glass, i.e. it is not entirely behind A-pillars, dashboard, wheel or a mirror.
const MASK_STEP = 3;   // sample every 3 px
function windshieldSees(b) {
  const { W, H, mask } = VW;
  const x0 = Math.max(0, Math.floor(b.x)), x1 = Math.min(W - 1, Math.ceil(b.x + b.w));
  const y0 = Math.max(0, Math.floor(b.y)), y1 = Math.min(H - 1, Math.ceil(b.y + b.h));
  if (x0 > x1 || y0 > y1) return false;                       // fully outside the frame
  for (let yy = y0; yy <= y1; yy += MASK_STEP) {
    for (let xx = x0; xx <= x1; xx += MASK_STEP) {
      if (mask[(yy * W + xx) * 4 + 3] < 128) return true;
    }
  }
  return false;
}

function render() {
  const { W, H, main, mirrors } = VW;
  ctx.clearRect(0, 0, W, H);
  drawWorld(ctx, main, sim.objs);                             // 1. outside world through the windshield
  drawCockpit(ctx, W, H);                                     // 2. cockpit on top
  for (const m of mirrors) {                                  // 3. mirrors on top of everything
    drawMirrorHousing(ctx, m);
    ctx.save();
    ctx.beginPath(); ctx.rect(m.clip.x, m.clip.y, m.clip.w, m.clip.h); ctx.clip();
    drawWorld(ctx, m, sim.objs);
    ctx.restore();
    drawMirrorGlass(ctx, m);
  }
  drawGaze(ctx, W, H);                                        // 4. gaze highlight, always the last layer
  mctx.clearRect(0, 0, VW.PW, H);
  drawMinimap(mctx);                                          // minimap panel (own canvas, right)
}
