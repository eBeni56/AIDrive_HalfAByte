// Turns the server's messages into human sentences for the two terminals.
//
//   simulation terminal  simulation_started / _ended, attention_event (appeared, zone_change, noticed,
//                        missed), attention_summary   -> SIM / OK / MISSED lines
//                        gaze_update                  -> CAM lines: where the driver looks (when it changes)
//   (driver_state only counts detections for the end-of-run statistics; the driver's current problem is
//   shown by the driver display, hud.ts)
import { simLog, type StatRow } from "./log";
import type { AttentionEvent, AttentionSummary, DriverState, GazeUpdate } from "./types";

// ------------------------------------------------------------------ words
const ZONE: Record<string, string> = {
  front_left: "front-left zone",
  front_center: "zone straight ahead",
  front_right: "front-right zone",
  side_left: "left blind spot",
  side_right: "right blind spot",
  mirror_left: "left mirror zone",
  mirror_center: "rear-view mirror zone",
  mirror_right: "right mirror zone",
};

const ZONE_SHORT: Record<string, string> = {
  front_left: "front-left",
  front_center: "ahead",
  front_right: "front-right",
  side_left: "left blind spot",
  side_right: "right blind spot",
  mirror_left: "left mirror",
  mirror_center: "rear mirror",
  mirror_right: "right mirror",
};

const LOOK: Record<string, string> = {
  front_left: "the road, front left",
  front_center: "the road straight ahead",
  front_right: "the road, front right",
  side_left: "out of the left side window",
  side_right: "out of the right side window",
  mirror_left: "the left mirror",
  mirror_center: "the rear-view mirror",
  mirror_right: "the right mirror",
  away: "away from the road and the mirrors",
};

function thing(e: { type: string; object_id: string }): string {
  return `${e.type} (${e.object_id})`;
}

function seconds(s: number | null | undefined): string {
  return s == null ? "?" : `${Number(s.toFixed(2))} s`;
}

// ------------------------------------------------------------------ statistics of one run
// Collected while a simulation runs, shown as a small table when it ends.
type GazeKind = "road" | "mirrors" | "elsewhere" | "closed" | "nodata";

const stats = {
  running: false,
  gazeS: { road: 0, mirrors: 0, elsewhere: 0, closed: 0, nodata: 0 } as Record<GazeKind, number>,
  counts: new Map<string, number>(), // detection type -> how many started during the run
};
let lastGaze: { kind: GazeKind; at: number } | null = null;

function gazeKind(g: GazeUpdate): GazeKind {
  if (g.status === "eyes_closed") return "closed";
  if (g.status !== "ok") return "nodata";
  if (g.region?.startsWith("front_")) return "road";
  if (g.region?.startsWith("mirror_")) return "mirrors";
  return "elsewhere"; // side windows, away
}

function percent(part: number, total: number): string {
  return total > 0 ? `${Math.round((100 * part) / total)}%` : "–";
}

function statsTable(s: AttentionSummary): StatRow[] {
  const g = stats.gazeS;
  const total = g.road + g.mirrors + g.elsewhere + g.closed + g.nodata;
  const roadShare = total > 0 ? g.road / total : 0;
  const objects = s.noticed_count + s.missed_count;
  const count = (t: string) => stats.counts.get(t) ?? 0;
  const rows: StatRow[] = [
    { label: "Duration", value: seconds(s.duration_s) },
    {
      label: "Eyes on the road",
      value: percent(g.road, total),
      tone: total === 0 ? "muted" : roadShare >= 0.7 ? "ok" : roadShare >= 0.5 ? "warn" : "danger",
    },
    { label: "Mirror checks", value: percent(g.mirrors, total) },
    { label: "Looking elsewhere", value: percent(g.elsewhere, total), tone: g.elsewhere > 0 ? "warn" : undefined },
    { label: "Eyes closed", value: percent(g.closed, total), tone: g.closed > 0 ? "danger" : undefined },
  ];
  if (g.nodata > 0) rows.push({ label: "No camera data", value: percent(g.nodata, total), tone: "muted" });
  rows.push(
    { label: "Noticed in time", value: `${s.noticed_count} / ${objects}`, tone: objects && !s.missed_count ? "ok" : undefined },
    { label: "Missed", value: String(s.missed_count), tone: s.missed_count ? "danger" : "ok" },
    ...s.missed.map((m) => ({
      label: `  ${m.type} (${m.object_id})`,
      value: `${ZONE_SHORT[m.zone] ?? m.zone} at ${seconds(m.t_s)}`,
      tone: "danger" as const,
    })),
    { label: "Average reaction", value: s.avg_reaction_s == null ? "–" : seconds(s.avg_reaction_s) },
    { label: "Eye closures", value: String(count("EYES_CLOSED")), tone: count("EYES_CLOSED") ? "warn" : undefined },
    { label: "Looked up / down", value: String(count("DISTRACTED")), tone: count("DISTRACTED") ? "warn" : undefined },
    { label: "Phone in hand", value: String(count("PHONE_USE")), tone: count("PHONE_USE") ? "danger" : undefined },
  );
  return rows;
}

// ------------------------------------------------------------------ simulation terminal
export function onSimulation(msg: { event?: string }): void {
  if (msg.event === "simulation_started") {
    stats.running = true;
    stats.gazeS = { road: 0, mirrors: 0, elsewhere: 0, closed: 0, nodata: 0 };
    stats.counts.clear();
    lastGaze = null;
    simLog.add("SIM", "sim", "──── Simulation started ────");
  } else if (msg.event === "attention_event") {
    const e = msg as AttentionEvent;
    const zone = e.zone ? ZONE[e.zone] ?? e.zone : null;
    if (e.kind === "appeared") {
      simLog.add(
        "SIM",
        "sim",
        zone
          ? `New ${thing(e)} appeared in the ${zone} – the driver has ${seconds(e.window_s)} to look`
          : `New ${thing(e)} appeared, still far away (no zone yet)`,
      );
    } else if (e.kind === "zone_change") {
      simLog.add(
        "SIM",
        "sim",
        zone
          ? `The ${thing(e)} moved into the ${zone} – the driver has ${seconds(e.window_s)} to look`
          : `The ${thing(e)} moved out of range (no zone)`,
      );
    } else if (e.kind === "noticed") {
      simLog.add("OK", "ok", `The driver noticed the ${thing(e)} in the ${zone} in ${seconds(e.reaction_s)} (limit ${seconds(e.window_s)})`);
    } else if (e.kind === "missed") {
      simLog.add("MISSED", "danger", `The driver did NOT look at the ${thing(e)} in the ${zone} within ${seconds(e.window_s)}`);
    }
  } else if (msg.event === "attention_summary") {
    const s = msg as AttentionSummary;
    simLog.add("STATS", "sim", "──── Simulation ended ────", statsTable(s));
    stats.running = false;
  } else if (msg.event === "simulation_ended") {
    stats.running = false;
  }
}

// Gaze: logged only once a new value has held for GAZE_STABLE_MS (a quick sweep is not a "look").
const GAZE_STABLE_MS = 300;
let gazeLogged: string | null = null;
let gazeCandidate: string | null = null;
let gazeSince = 0;

function gazeKey(g: GazeUpdate): string {
  return g.status === "ok" ? `ok:${g.region}` : g.status;
}

function gazeSentence(g: GazeUpdate): [string, "cam" | "warn" | "danger" | "muted"] {
  switch (g.status) {
    case "ok":
      if (g.region === "away") return ["The driver looks away from the road and the mirrors", "warn"];
      return [`The driver looks at ${LOOK[g.region ?? ""] ?? g.region}`, "cam"];
    case "eyes_closed":
      return ["The driver's eyes are closed", "danger"];
    case "no_face":
      return ["The camera sees no face", "warn"];
    case "calibrating":
      return ["Camera calibrating – look straight ahead", "muted"];
    default:
      return ["No camera data – gaze unknown", "muted"];
  }
}

export function onGaze(g: GazeUpdate): void {
  const key = gazeKey(g);
  const now = Date.now();
  if (stats.running) {
    // time since the previous gaze_update counts for the previous kind (capped: a gap is not "looking")
    if (lastGaze) stats.gazeS[lastGaze.kind] += Math.min(now - lastGaze.at, 500) / 1000;
    lastGaze = { kind: gazeKind(g), at: now };
  }
  if (key !== gazeCandidate) {
    gazeCandidate = key;
    gazeSince = now;
  }
  if (key !== gazeLogged && now - gazeSince >= GAZE_STABLE_MS) {
    gazeLogged = key;
    const [text, tone] = gazeSentence(g);
    simLog.add("CAM", tone, text);
  }
}

// ------------------------------------------------------------------ driver_state (for the statistics)
// The driver's problems are shown by the driver display (hud.ts); here only the detections that start during
// a simulation are counted. The dashboard gets ~10 of the ~30 states per second, so a detection is counted
// when its id is first seen.
const seenIds = new Set<string>();

export function onDriverState(s: DriverState): void {
  for (const d of s.detections) {
    if (d.status === "ENDED" || seenIds.has(d.id)) continue;
    seenIds.add(d.id);
    if (stats.running) stats.counts.set(d.type, (stats.counts.get(d.type) ?? 0) + 1);
  }
  if (seenIds.size > 1000) seenIds.clear();
}

/** The connection to the server opened (true) or was lost (false). */
export function onServer(connected: boolean): void {
  simLog.add("SYS", "muted", connected ? "Connected to the server" : "Server connection lost – reconnecting…");
  if (!connected) gazeLogged = gazeCandidate = null;
}
