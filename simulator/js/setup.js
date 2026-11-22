// Setup phase: icons, scene, placed objects, setup events, drag & drop.

// ===================== ICONS (inline SVG, top-down, front = up) =====================
function svgFor(type, color) {
  const dark = "#1a1d24", glass = "#9fd3ff";
  switch (type) {
    case "car":
      return `<svg viewBox="0 0 40 80" preserveAspectRatio="none">
        <rect x="2" y="2" width="36" height="76" rx="10" fill="${color}" stroke="${dark}" stroke-width="2"/>
        <rect x="7" y="18" width="26" height="12" rx="3" fill="${glass}"/>
        <rect x="7" y="52" width="26" height="9" rx="3" fill="${glass}"/>
        <rect x="9" y="32" width="22" height="18" rx="3" fill="${color}" stroke="${dark}" stroke-width="1"/></svg>`;
    case "truck":
      return `<svg viewBox="0 0 40 80" preserveAspectRatio="none">
        <rect x="3" y="26" width="34" height="52" rx="3" fill="${color}" stroke="${dark}" stroke-width="2"/>
        <rect x="3" y="2" width="34" height="22" rx="7" fill="${color}" stroke="${dark}" stroke-width="2"/>
        <rect x="8" y="7" width="24" height="8" rx="2" fill="${glass}"/></svg>`;
    case "bicycle":
      return `<svg viewBox="0 0 40 80" preserveAspectRatio="none">
        <rect x="18" y="4" width="4" height="72" rx="2" fill="${dark}"/>
        <rect x="6" y="16" width="28" height="4" rx="2" fill="${dark}"/>
        <ellipse cx="20" cy="42" rx="10" ry="12" fill="${color}" stroke="${dark}" stroke-width="2"/>
        <circle cx="20" cy="40" r="6" fill="${dark}"/></svg>`;
    case "motorcycle":
      return `<svg viewBox="0 0 40 80" preserveAspectRatio="none">
        <rect x="14" y="2" width="12" height="76" rx="6" fill="${dark}"/>
        <rect x="3" y="16" width="34" height="5" rx="2" fill="${dark}"/>
        <ellipse cx="20" cy="44" rx="12" ry="14" fill="${color}" stroke="${dark}" stroke-width="2"/>
        <circle cx="20" cy="40" r="6" fill="${dark}"/></svg>`;
    case "pedestrian":
      return `<svg viewBox="0 0 40 40" preserveAspectRatio="none">
        <ellipse cx="20" cy="22" rx="17" ry="9" fill="${color}" stroke="${dark}" stroke-width="2"/>
        <circle cx="20" cy="20" r="8" fill="#f1d3b0" stroke="${dark}" stroke-width="2"/></svg>`;
  }
}

// ===================== SCENE SETUP =====================
const scene = document.getElementById("scene");
const road = document.getElementById("road");
const toolbar = document.getElementById("toolbar");
const egoRing = document.getElementById("ego-ring");

// Ego car: fixed, never moves
const ego = document.createElement("div");
ego.id = "ego";
ego.className = "obj";
ego.innerHTML = svgFor("car", EGO_COLOR);
scene.appendChild(ego);

// Toolbar icons
for (const type of Object.keys(TYPES)) {
  const btn = document.createElement("div");
  btn.className = "tool";
  btn.innerHTML = svgFor(type, TYPES[type].color) + `<span>${type}</span>`;
  btn.addEventListener("pointerdown", e => startDrag(e, btn, type, null));
  toolbar.insertBefore(btn, document.getElementById("clear"));
}

// A few static grass tufts (decorative only). Spot i: 0..1 across and down the grass, deterministic
// pseudo-random; the minimap (render.js) uses the same spots.
function tuftSpot(i) { return { fx: (i * 0.37 + 0.11) % 1, fy: (i * 0.61 + 0.05) % 1 }; }
for (let i = 0; i < 16; i++) {
  const t = document.createElement("div");
  const { fx, fy } = tuftSpot(i);
  t.className = "tuft";
  t.dataset.fx = fx.toFixed(3);
  t.dataset.fy = fy.toFixed(3);
  scene.insertBefore(t, road);
}

function sizePx(w_m, l_m) {
  // Real size in px, with a minimum so small objects stay visible.
  return { w: Math.max(w_m * PIXELS_PER_METER, 22), h: Math.max(l_m * PIXELS_PER_METER, 22) };
}

function setBox(el, w_m, l_m) {
  const s = sizePx(w_m, l_m);
  el.style.width = s.w + "px";
  el.style.height = s.h + "px";
}

function layout() {
  const c = egoCenter();
  const roadW = 3 * LANE_WIDTH_PX;
  road.style.width = roadW + "px";
  road.style.left = (c.x - roadW / 2 - 5) + "px";   // -5: left border width
  for (const t of document.querySelectorAll(".tuft")) {
    const fx = parseFloat(t.dataset.fx), fy = parseFloat(t.dataset.fy);
    // Put tufts on the grass: left or right of the road alternately
    const side = fx < 0.5 ? fx * 2 * (c.x - roadW / 2 - 30) : c.x + roadW / 2 + 20 + (fx - 0.5) * 2 * (c.x - roadW / 2 - 40);
    t.style.left = Math.max(0, side) + "px";
    t.style.top = (fy * (window.innerHeight - 150)) + "px";
  }
  setBox(ego, EGO_SIZE.w, EGO_SIZE.l);
  ego.style.left = c.x + "px"; ego.style.top = c.y + "px";
  const s = sizePx(EGO_SIZE.w, EGO_SIZE.l);
  egoRing.style.width = (s.w + 16) + "px"; egoRing.style.height = (s.h + 16) + "px";
  egoRing.style.left = c.x + "px"; egoRing.style.top = c.y + "px";
  for (const o of objects.values()) positionObj(o);
}

// ===================== PLACED OBJECTS =====================
const objects = new Map();   // object_id -> { id, type, x_m, y_m, el }
let nextId = 1;

function positionObj(o) {
  const p = metersToPx(o.x_m, o.y_m);
  o.el.style.left = p.x + "px";
  o.el.style.top = p.y + "px";
}

// Setup is local only: nothing is sent to the backend until the drive starts.
function removeObject(o) {
  o.el.remove();
  objects.delete(o.id);
  updateStartButton();
}

// "Start simulation" is only possible once at least one object is placed.
function updateStartButton() {
  document.getElementById("start").disabled = objects.size === 0;
}

function createObject(type, x_m, y_m) {
  const id = "obj_" + nextId++;
  const el = document.createElement("div");
  el.className = "obj";
  el.innerHTML = svgFor(type, TYPES[type].color);
  setBox(el, TYPES[type].w, TYPES[type].l);
  scene.appendChild(el);
  const o = { id, type, x_m, y_m, el };
  objects.set(id, o);
  positionObj(o);
  el.addEventListener("pointerdown", e => { if (e.button === 0) startDrag(e, el, type, o); });
  el.addEventListener("contextmenu", e => { e.preventDefault(); removeObject(o); });
  el.addEventListener("dblclick", () => { if (objects.has(o.id)) removeObject(o); });
  updateStartButton();
  return o;
}

// ===================== DRAGGING (pointer events) =====================
// A drop is valid only inside the three-lane road and above the toolbar.
function isValidDrop(px, py) {
  const m = pxToMeters(px, py);
  const aboveToolbar = py < toolbar.getBoundingClientRect().top && py >= 0;
  return aboveToolbar && Math.abs(m.x_m) <= ROAD_HALF_M;
}

// source: element that receives pointer capture; type: object type; existing: placed object or null.
function startDrag(e, source, type, existing) {
  if (e.button !== 0) return;
  e.preventDefault();
  source.setPointerCapture(e.pointerId);

  let ghost = null;
  let offX = 0, offY = 0;                  // grab offset so a moved object doesn't jump
  const startX = e.clientX, startY = e.clientY;
  let moved = false;
  const orig = existing ? { x_m: existing.x_m, y_m: existing.y_m } : null;

  if (existing) {
    const p = metersToPx(existing.x_m, existing.y_m);
    offX = p.x - e.clientX; offY = p.y - e.clientY;
    existing.el.classList.add("dragging");
  } else {
    ghost = document.createElement("div");
    ghost.className = "ghost";
    ghost.innerHTML = svgFor(type, TYPES[type].color);
    setBox(ghost, TYPES[type].w, TYPES[type].l);
    document.body.appendChild(ghost);
    ghost.style.left = e.clientX + "px"; ghost.style.top = e.clientY + "px";
  }

  function onMove(ev) {
    if (Math.hypot(ev.clientX - startX, ev.clientY - startY) > 3) moved = true;
    const px = ev.clientX + offX, py = ev.clientY + offY;
    if (existing) {
      const m = pxToMeters(px, py);
      existing.x_m = m.x_m; existing.y_m = m.y_m;
      positionObj(existing);
    } else {
      ghost.style.left = px + "px"; ghost.style.top = py + "px";
      ghost.classList.toggle("bad", !isValidDrop(px, py));
    }
  }

  function finish(ev, cancelled) {
    source.removeEventListener("pointermove", onMove);
    source.removeEventListener("pointerup", onUp);
    source.removeEventListener("pointercancel", onCancel);
    if (source.hasPointerCapture(ev.pointerId)) source.releasePointerCapture(ev.pointerId);
    const px = ev.clientX + offX, py = ev.clientY + offY;
    const ok = !cancelled && isValidDrop(px, py);

    if (existing) {
      existing.el.classList.remove("dragging");
      if (!moved || !ok) {                          // plain click, or dropped somewhere invalid: revert
        existing.x_m = orig.x_m; existing.y_m = orig.y_m;
        positionObj(existing);
      }
    } else {
      ghost.remove();
      if (ok) {
        const m = pxToMeters(px, py);
        createObject(type, m.x_m, m.y_m);
      }
    }
  }
  function onUp(ev) { finish(ev, false); }
  function onCancel(ev) { finish(ev, true); }

  source.addEventListener("pointermove", onMove);
  source.addEventListener("pointerup", onUp);
  source.addEventListener("pointercancel", onCancel);
}

// ===================== MISC =====================
scene.addEventListener("contextmenu", e => e.preventDefault());   // no browser menu on the road

// Speed button: cycles 1x -> 2x -> 0.25x -> 0.5x -> 1x. Read once when a run starts (planBehavior).
let speedIdx = 0;
const speedBtn = document.getElementById("speed");
function speedFactor() { return SPEED_STEPS[speedIdx]; }
speedBtn.addEventListener("click", () => {
  speedIdx = (speedIdx + 1) % SPEED_STEPS.length;
  speedBtn.textContent = speedFactor() + "x";
});

document.getElementById("clear").addEventListener("click", () => {
  for (const o of [...objects.values()]) removeObject(o);
});
