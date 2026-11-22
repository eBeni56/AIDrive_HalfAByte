// The driver display: ONE message at a time, the most important current problem.
//
// Every 200 ms `pick()` lists what is wrong right now (activeIssues, highest priority first) and decides what
// to show: a new problem with a HIGHER priority replaces the shown one at once; otherwise the shown message
// stays for at least HOLD_MS (even if its problem is already over), so the display never flickers.
// Outside a simulation nothing is tracked: before the first one a still message (IDLE); after the end the last
// message stays on the display (the data of the drive is kept until the next one starts).
import { useEffect, useState } from "react";
import { STALE_MS } from "./config";
import { seconds } from "./format";
import { REGIONS, type Arrow } from "./regions";
import type { AttentionEvent, DriverState } from "./types";

const HOLD_MS = 3000; // a message stays at least this long
const NO_FACE_MS = 1000; // the camera sees no face this long -> "The camera can't see you"

type Level = "ok" | "info" | "warn" | "danger";
export type IconName = "alarm" | "obstacle" | "phone" | "eye" | "face" | "camera" | "check" | "pause";

interface Issue {
  key: string; // the same problem keeps its key while it lasts (its detail may change)
  priority: number;
  level: Level;
  icon: IconName;
  title: string;
  detail: string;
  arrow?: Arrow;
}

// ------------------------------------------------------------------ input (fed by useServer.ts)
let driver: DriverState | null = null;
let driverAt = 0;
let connected = false;
let running = false; // a simulation runs (simulation_started .. simulation_ended)
let noFaceSince: number | null = null;
let noticedAt = 0; // when the driver last looked at a road user in time (its "look there" message goes at once)
// simulator objects the driver still has to look at: object_id -> what, where, until when
const pending = new Map<string, { type: string; zone: string; until: number }>();

export const hudInput = {
  connection(ok: boolean) {
    connected = ok;
    if (!ok) running = false;
  },
  driverState(s: DriverState) {
    driver = s;
    driverAt = Date.now();
    if (s.quality.face_detected) noFaceSince = null;
    else if (noFaceSince === null) noFaceSince = driverAt;
  },
  simulation(msg: { event?: string }) {
    if (msg.event === "simulation_started" || msg.event === "simulation_ended") {
      pending.clear();
      running = msg.event === "simulation_started";
      if (running) {
        driver = null; // a new run starts from fresh camera data
        noFaceSince = null;
        shownAt = 0; // the message of the last drive gives way at once
      }
      return;
    }
    if (msg.event !== "attention_event") return;
    const e = msg as AttentionEvent;
    // a road user to look at - unless the driver already looks there (checked: no message at all)
    if ((e.kind === "appeared" || e.kind === "zone_change") && e.zone && e.window_s != null && !e.looking) {
      pending.set(e.object_id, { type: e.type, zone: e.zone, until: Date.now() + e.window_s * 1000 + 500 });
    } else {
      if (e.kind === "noticed" && pending.has(e.object_id)) noticedAt = Date.now();
      pending.delete(e.object_id); // noticed, missed, already looked at, or out of every zone
    }
  },
};

// ------------------------------------------------------------------ what is wrong now
const A: Record<string, string> = { car: "A car", truck: "A truck", bicycle: "A bicycle", motorcycle: "A motorcycle", pedestrian: "A pedestrian" };

/** Everything wrong at `now`, the most important first. Reads the input, changes nothing. */
function activeIssues(now: number): Issue[] {
  const out: Issue[] = [];
  const fresh = connected && driver !== null && now - driverAt < STALE_MS ? driver : null;
  const det = (type: string) => fresh?.detections.find((d) => d.type === type && d.status !== "ENDED");

  const closed = det("EYES_CLOSED");
  if (closed) {
    out.push({
      key: "eyes-closed", priority: 100, level: "danger", icon: "alarm",
      title: "Wake up! Open your eyes", detail: `Your eyes have been closed for ${closed.duration_s.toFixed(1)} s`,
    });
  }

  // the most urgent object of the simulation the driver has not looked at yet (expired ones: forgetExpired)
  const next = [...pending.values()].sort((a, b) => a.until - b.until)[0];
  if (next) {
    const w = REGIONS[next.zone];
    out.push({
      key: `obstacle-${next.zone}-${next.type}`, priority: 90, level: "danger", icon: "obstacle",
      title: w?.title ?? "Look around", detail: `${A[next.type] ?? "Something"} is ${w?.place ?? next.zone}`, arrow: w?.arrow,
    });
  }

  const phone = det("PHONE_USE");
  if (phone) {
    out.push({
      key: "phone", priority: 80, level: "danger", icon: "phone",
      title: "Put down your phone", detail: `Phone in your hand for ${seconds(Math.max(0, phone.duration_s), 0)}`,
    });
  }

  // looking at none of the 8 regions (up or down) for 1 s - decided by the driver monitor
  const distracted = det("DISTRACTED");
  if (distracted) {
    const where = distracted.details.zone === "DOWN" ? "down" : distracted.details.zone === "UP" ? "up" : "away";
    out.push({
      key: "eyes-on-road", priority: 70, level: distracted.severity === "DANGER" ? "danger" : "warn", icon: "eye",
      title: "Eyes on the road", detail: `You are looking ${where}`,
    });
  }

  if (fresh) {
    if (noFaceSince !== null && now - noFaceSince >= NO_FACE_MS) {
      out.push({ key: "face", priority: 40, level: "warn", icon: "face", title: "The camera can't see you", detail: "Face the road, keep your face visible" });
    }
    if (fresh.calibration.status === "CALIBRATING") {
      out.push({ key: "calibrating", priority: 30, level: "info", icon: "eye", title: "Look straight ahead", detail: "Calibrating the camera…" });
    }
  } else {
    out.push({
      key: "no-camera", priority: 20, level: "info", icon: "camera",
      title: connected ? "Camera not connected" : "No connection to the server",
      detail: connected ? "Start the driver monitor" : "Reconnecting…",
    });
  }
  out.sort((a, b) => b.priority - a.priority);
  return out;
}

const ALL_GOOD: Issue = { key: "ok", priority: 0, level: "ok", icon: "check", title: "All good", detail: "Drive safely" };
const IDLE: Issue = {
  key: "idle", priority: 0, level: "info", icon: "pause",
  title: "No simulation running", detail: "Start a simulation in the simulator",
};
const NO_SERVER: Issue = {
  key: "no-server", priority: 20, level: "info", icon: "camera", title: "No connection to the server", detail: "Reconnecting…",
};

// ------------------------------------------------------------------ what to show
let shown: Issue = IDLE;
let shownAt = 0;

/** Objects whose look window (+0.5 s) is over: not shown any more (the server sends "missed"). */
function forgetExpired(now: number): void {
  for (const [id, p] of pending) if (now > p.until) pending.delete(id);
}

export function pick(now: number): Issue {
  if (!running) {
    // outside a run nothing is tracked: the last message stays (IDLE before the first drive)
    if (!connected) return shown.key === NO_SERVER.key ? shown : show(NO_SERVER, now);
    if (shown.key === NO_SERVER.key) return show(IDLE, now);
    return shown;
  }
  forgetExpired(now);
  const list = activeIssues(now);
  const top = list[0] ?? ALL_GOOD;
  const same = list.find((i) => i.key === shown.key);
  if (same) {
    // still going on: keep it (fresh detail), unless something more important came up
    if (top.priority > same.priority) return show(top, now);
    shown = same;
    return shown;
  }
  if (top.priority > shown.priority || now - shownAt >= HOLD_MS) return show(top, now);
  // a "look there" message whose road user was just looked at: gone at once, no HOLD_MS (it was checked)
  if (shown.icon === "obstacle" && noticedAt >= shownAt) return show(top, now);
  return shown; // over, but shown for less than HOLD_MS: keep it a little longer
}

function show(issue: Issue, now: number): Issue {
  shown = issue;
  shownAt = now;
  return issue;
}

/** The message to show, re-evaluated every 200 ms. */
export function useHud(): Issue {
  const [issue, setIssue] = useState<Issue>(() => pick(Date.now()));
  useEffect(() => {
    const id = window.setInterval(() => setIssue(pick(Date.now())), 200);
    return () => window.clearInterval(id);
  }, []);
  return issue;
}
