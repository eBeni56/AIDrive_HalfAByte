// The driver display: ONE message at a time, the most important current problem.
//
// Every 200 ms `pick()` lists what is wrong right now (activeIssues, highest priority first) and decides what
// to show: a new problem with a HIGHER priority replaces the shown one at once; otherwise the shown message
// stays for at least HOLD_MS (even if its problem is already over), so the display never flickers.
import { useEffect, useState } from "react";
import { STALE_MS } from "./config";
import type { AttentionEvent, DriverState, GazeUpdate } from "./types";

const HOLD_MS = 3000; // a message stays at least this long
const AWAY_MS = 1000; // looking at none of the 8 regions this long -> "Eyes on the road"

export type Level = "ok" | "info" | "warn" | "danger";
export type IconName = "alarm" | "obstacle" | "phone" | "eye" | "coffee" | "face" | "camera" | "check";
export type Arrow = "left" | "right" | "ahead" | "behind" | "behind-left" | "behind-right";

export interface Issue {
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
let gaze: GazeUpdate | null = null;
let awaySince: number | null = null;
// simulator objects the driver still has to look at: object_id -> what, where, until when
const pending = new Map<string, { type: string; zone: string; until: number }>();

export const hudInput = {
  connection(ok: boolean) {
    connected = ok;
  },
  driverState(s: DriverState) {
    driver = s;
    driverAt = Date.now();
  },
  gaze(g: GazeUpdate) {
    gaze = g;
    const away = g.status === "ok" && g.region === "away";
    if (!away) awaySince = null;
    else if (awaySince === null) awaySince = Date.now();
  },
  simulation(msg: { event?: string }) {
    if (msg.event === "simulation_started" || msg.event === "simulation_ended") {
      pending.clear();
      return;
    }
    if (msg.event !== "attention_event") return;
    const e = msg as AttentionEvent;
    if ((e.kind === "appeared" || e.kind === "zone_change") && e.zone && e.window_s != null) {
      pending.set(e.object_id, { type: e.type, zone: e.zone, until: Date.now() + e.window_s * 1000 + 500 });
    } else {
      pending.delete(e.object_id); // noticed, missed, or out of every zone
    }
  },
};

// ------------------------------------------------------------------ what is wrong now
const WHERE: Record<string, { title: string; place: string; arrow: Arrow }> = {
  front_left: { title: "Watch ahead left", place: "ahead on the left", arrow: "left" },
  front_center: { title: "Obstacle ahead", place: "right in front of you", arrow: "ahead" },
  front_right: { title: "Watch ahead right", place: "ahead on the right", arrow: "right" },
  side_left: { title: "Look left", place: "in your left blind spot", arrow: "left" },
  side_right: { title: "Look right", place: "in your right blind spot", arrow: "right" },
  mirror_left: { title: "Check the left mirror", place: "behind you on the left", arrow: "behind-left" },
  mirror_center: { title: "Check the rear-view mirror", place: "right behind you", arrow: "behind" },
  mirror_right: { title: "Check the right mirror", place: "behind you on the right", arrow: "behind-right" },
};

const A: Record<string, string> = { car: "A car", truck: "A truck", bicycle: "A bicycle", motorcycle: "A motorcycle", pedestrian: "A pedestrian" };

function secs(s: number): string {
  return `${Math.max(0, s).toFixed(0)} s`;
}

export function activeIssues(now: number): Issue[] {
  const out: Issue[] = [];
  const fresh = connected && driver !== null && now - driverAt < STALE_MS ? driver : null;
  const det = (type: string) => fresh?.detections.find((d) => d.type === type && d.status !== "ENDED");

  const micro = det("MICROSLEEP");
  const closed = det("EYES_CLOSED");
  if (micro || closed) {
    out.push({
      key: "eyes-closed", priority: 100, level: "danger", icon: "alarm",
      title: "Wake up! Open your eyes", detail: `Your eyes have been closed for ${(micro ?? closed)!.duration_s.toFixed(1)} s`,
    });
  }
  if (det("HEAD_FALLING")) {
    out.push({ key: "head-falling", priority: 95, level: "danger", icon: "alarm", title: "Wake up!", detail: "Your head is dropping" });
  }

  // the most urgent object of the simulation the driver has not looked at yet
  for (const [id, p] of pending) if (now > p.until) pending.delete(id);
  const next = [...pending.values()].sort((a, b) => a.until - b.until)[0];
  if (next) {
    const w = WHERE[next.zone];
    out.push({
      key: `obstacle-${next.zone}-${next.type}`, priority: 90, level: "danger", icon: "obstacle",
      title: w?.title ?? "Look around", detail: `${A[next.type] ?? "Something"} is ${w?.place ?? next.zone}`, arrow: w?.arrow,
    });
  }

  const phone = det("PHONE_USE");
  if (phone) {
    out.push({
      key: "phone", priority: 80, level: "danger", icon: "phone",
      title: "Put down your phone", detail: `Phone in your hand for ${secs(phone.duration_s)}`,
    });
  }

  const distracted = det("DISTRACTED");
  const away = awaySince !== null && now - awaySince >= AWAY_MS && gaze?.status === "ok" && gaze.region === "away";
  if (distracted || away) {
    const down = distracted?.details.reason === "LOOKING_DOWN";
    out.push({
      key: "eyes-on-road", priority: 70, level: distracted?.severity === "DANGER" ? "danger" : "warn", icon: "eye",
      title: "Eyes on the road",
      detail: down ? "You are looking down" : "You are not looking at the road or the mirrors",
    });
  }

  if (fresh) {
    const lvl = fresh.drowsiness.level_index;
    if (lvl >= 3) {
      out.push({ key: "tired-3", priority: 60, level: "danger", icon: "coffee", title: "Take a break now", detail: "You are very tired" });
    } else if (lvl >= 1 || det("TIRED_EYES") || det("EYES_NARROWED") || det("YAWN")) {
      out.push({ key: "tired-1", priority: 50, level: "warn", icon: "coffee", title: "You seem tired", detail: "Plan a break soon" });
    }
    if (det("FACE_NOT_VISIBLE")) {
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

// ------------------------------------------------------------------ what to show
let shown: Issue = ALL_GOOD;
let shownAt = 0;

export function pick(now: number): Issue {
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
