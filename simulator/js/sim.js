// Drive phase logic: per-object behavior, simulation flow, the messages sent to the backend.
// Runs the init calls.

// ------------------------------ BEHAVIOR (constants: BEHAVIOR in config.js) ----------
function planBehavior(src) {     // src = a placed setup object; returns a NEW sim object (setup is untouched)
  const b = BEHAVIOR[src.type];
  const jitter = (1 + (Math.random() * 2 - 1) * SPEED_JITTER) * speedFactor();   // x Speed button
  const o = { id: src.id, type: src.type, x_m: src.x_m, y_m: src.y_m,
              x0: src.x_m, y0: src.y_m,   // setup position, for the minimap trail
              mode: "parked", cruise: 0, targetX: src.x_m, finished: false,
              walked: 0 };        // meters walked, only drives the leg animation
  if (b.kind === "pedestrian") {
    const side = src.x_m <= 0 ? -1 : 1;                      // nearer road edge (tie -> left)
    const edge = ROAD_HALF_M + PED_GRASS_OFFSET_M;
    o.mode = "cross"; o.x_m = side * edge; o.targetX = -side * edge;
    o.cruise = b.speed_mps * jitter;
  } else if (laneFromX(src.x_m) !== "center") {
    o.mode = "overtake"; o.cruise = b.speed_mps * jitter;
  } else {
    o.mode = "parked"; o.finished = true;
  }
  return o;
}

// Advance one object by dt seconds.
function stepObject(o, dt) {
  if (o.mode === "overtake") {
    o.y_m += o.cruise * dt;
    if (o.y_m - TYPES[o.type].l / 2 > VW.mini.yAhead) o.finished = true;   // fully past the minimap's top edge
  } else if (o.mode === "cross" && !o.finished) {
    const left = Math.abs(o.targetX - o.x_m), step = o.cruise * dt;
    if (left <= step) {
      o.x_m = o.targetX; o.walked += left; o.finished = true;
      return;
    }
    o.x_m += Math.sign(o.targetX - o.x_m) * step; o.walked += step;
  }
}

// ------------------------------ CALIBRATION ------------------------------------------
// When the page loads (and with the Calibrate button): "look at the number" with a countdown over the setup
// screen; the number is where "straight ahead" is while driving (the middle of the driving view). It waits for
// the server connection, then sends calibration_started: the server asks the driver monitor to measure the
// straight-ahead look (recenter_gaze). Esc skips it. A simulation start needs no calibration.
const bigEl = document.getElementById("countdown");
const lookEl = document.getElementById("look-center");
const calibEl = document.getElementById("calib");
let calib = null;   // null = not calibrating; { started: performance.now() or null while waiting, timer }

function setBig(text, go) {
  if (bigEl.textContent !== text) bigEl.textContent = text;
  bigEl.classList.toggle("show", !!text);
  bigEl.classList.toggle("go", !!go);
}

function startCalibration() {
  if (sim || calib) return;
  // the driving view's width, so the number is where the road ahead will be
  document.documentElement.style.setProperty("--mini-w", Math.round(window.innerWidth * MINIMAP.widthFrac) + "px");
  calib = { started: null, timer: setInterval(calibrationStep, 100) };
  calibEl.classList.add("show");
  lookEl.classList.add("show");
  calibrationStep();
}

function calibrationStep() {
  if (!calib) return;
  if (calib.started === null) {                                // wait for the server
    if (!(ws && ws.readyState === WebSocket.OPEN)) { lookEl.textContent = "Connecting to the server…"; setBig(""); return; }
    emit({ event: "calibration_started", timestamp: nowS() });
    calib.started = performance.now();
  }
  const t = (performance.now() - calib.started) / 1000;
  if (t >= CALIBRATION_S) { endCalibration(); return; }
  lookEl.textContent = CALIBRATION_TEXT;
  setBig(String(Math.max(1, CALIBRATION_S - Math.floor(t))));
}

function endCalibration() {
  if (!calib) return;
  clearInterval(calib.timer);
  calib = null;
  calibEl.classList.remove("show");
  lookEl.classList.remove("show");
  setBig("");
}

// ------------------------------ SIMULATION FLOW --------------------------------------
// phases: "ready" ("Press SPACE to start", frozen) -> "driving" -> "done";  sim === null means SETUP.
// Messages: simulation_started (at SPACE = GO), simulation_tick (10 Hz), simulation_ended (the end, or an
// abort while driving), simulation_reset (leaving the run for the setup screen: a new simulation is being
// prepared, so the voice stops reading the analysis).
let sim = null;
let rafId = 0, lastNow = 0;
const pressEl = document.getElementById("press");
const doneEl = document.getElementById("done");
const nowS = () => Date.now() / 1000;

function startSimulation() {
  if (sim || calib || objects.size === 0) return;
  document.activeElement?.blur();                              // SPACE must not "click" a button again
  sim = { phase: "ready", t: 0, objs: [...objects.values()].map(planBehavior), ticks: 0, tickTimer: 0 };
  document.body.classList.add("sim");
  resizeView();
  pressEl.classList.add("show");
  render();
  lastNow = performance.now();
  rafId = requestAnimationFrame(frame);
}

function beginDriving() {
  sim.phase = "driving"; sim.t = 0;
  pressEl.classList.remove("show");
  setGazeActive(true);                                         // gaze.js: the highlight from now on
  emit({ event: "simulation_started", timestamp: nowS() });
  sendTick();                                                  // first snapshot right at GO
  sim.tickTimer = setInterval(sendTick, 1000 / TICK_HZ);       // 10 Hz, decoupled from rendering
}

function endSimulation() {
  sim.phase = "done";                                          // world freezes: update() does nothing now
  clearInterval(sim.tickTimer);
  setBig("");
  setGazeActive(false);                                        // no gaze shown after the end
  emit({ event: "simulation_ended", timestamp: nowS() });
  document.getElementById("done-info").textContent =
    `${sim.objs.length} object(s), ${sim.t.toFixed(1)} s of driving, ${sim.ticks} tick(s)`;
  doneEl.classList.add("show");
}

// Back to SETUP. An abort while driving also ends the run on the backend; leaving a run that was started
// (abort, or "Back to setup" after the end) sends simulation_reset: the voice stops reading the analysis.
// From "ready" nothing was started, so nothing is sent.
function exitToSetup() {
  if (!sim) return;
  if (sim.phase === "driving") emit({ event: "simulation_ended", timestamp: nowS() });
  if (sim.phase !== "ready") emit({ event: "simulation_reset", timestamp: nowS() });
  clearInterval(sim.tickTimer);
  cancelAnimationFrame(rafId);
  sim = null;
  setBig("");
  pressEl.classList.remove("show");
  setGazeActive(false);
  doneEl.classList.remove("show");
  document.body.classList.remove("sim");
  layout();                                                    // setup objects were never touched
}

function update(dt) {
  if (sim.phase !== "driving") return;                         // "ready": frozen until SPACE; "done": frozen
  sim.t += dt;
  setBig(sim.t < 1 ? "GO" : "", true);
  for (const o of sim.objs) stepObject(o, dt);
  // every object drove off the minimap (only side-lane vehicles can): end now; otherwise at MAX_RUN_S
  const allGone = sim.objs.every(o => o.mode === "overtake" && o.finished);
  if (allGone || sim.t >= MAX_RUN_S) endSimulation();
}

// simulation_tick: 10 Hz, one message with the position of EVERY object on the map, whether or not
// the driver can see it right now (windshield, mirrors, blind spot or far away: all included).
function sendTick() {
  if (!sim || sim.phase !== "driving") return;
  const ev = {
    event: "simulation_tick",
    timestamp: nowS(),
    objects: sim.objs.map(o => Object.assign({ object_id: o.id, type: o.type }, mapPx(o.x_m, o.y_m))),
  };
  sim.ticks++;
  emit(ev);
}

// requestAnimationFrame loop with delta time, so motion is frame-rate independent.
// dt is capped at 0.1 s so a hidden/stalled tab does not make objects jump.
function frame(now) {
  if (!sim) return;
  const dt = Math.min(0.1, Math.max(0, (now - lastNow) / 1000));
  lastNow = now;
  update(dt);
  render();
  rafId = requestAnimationFrame(frame);
}

document.getElementById("start").addEventListener("click", startSimulation);
document.getElementById("calibrate").addEventListener("click", startCalibration);
document.getElementById("abort").addEventListener("click", exitToSetup);
document.getElementById("back").addEventListener("click", exitToSetup);
document.addEventListener("keydown", e => {
  if (e.key === "Escape") {
    if (calib) endCalibration();                               // skip the calibration
    else if (sim) exitToSetup();
  } else if (e.code === "Space" && sim && sim.phase === "ready") {
    e.preventDefault();
    beginDriving();                                            // GO
  }
});
window.addEventListener("resize", () => { if (sim) { resizeView(); render(); } });

window.addEventListener("resize", layout);
layout();
connect();
updateStartButton();
startCalibration();                                            // once when the page loads (Calibrate: again)
