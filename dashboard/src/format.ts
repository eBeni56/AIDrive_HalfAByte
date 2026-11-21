import type { DriverState, Severity } from "./types";

/** Colour class of a card / pill / row: ok (green), info (blue), warn (yellow), alert (orange), danger (red). */
export type Tone = "ok" | "info" | "warn" | "alert" | "danger" | "muted";

export function num(v: number | null | undefined, digits = 1, unit = ""): string {
  return v == null || !Number.isFinite(v) ? "–" : `${v.toFixed(digits)}${unit}`;
}

export function deg(v: number | null | undefined): string {
  return v == null ? "–" : `${v > 0 ? "+" : ""}${v.toFixed(0)}°`;
}

/** "LEFT_MIRROR" -> "Left mirror" */
export function label(v: string | null | undefined): string {
  if (!v) return "–";
  const s = v.replace(/_/g, " ").toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function severityTone(s: Severity): Tone {
  return ({ NONE: "ok", INFO: "info", WARNING: "warn", DANGER: "alert", CRITICAL: "danger" } as const)[s];
}

export function drowsinessTone(d: DriverState["drowsiness"]): Tone {
  return (["ok", "warn", "alert", "danger", "danger"] as const)[Math.max(0, Math.min(4, d.level_index))];
}

export function attentionTone(a: DriverState["attention"]): Tone {
  return ({ ATTENTIVE: "ok", CHECKING_MIRROR: "info", GLANCING_AWAY: "warn", DISTRACTED: "danger", UNKNOWN: "muted" } as const)[
    a.state
  ];
}

export function gazeTone(zone: string): Tone {
  if (zone === "ROAD") return "ok";
  if (zone.endsWith("MIRROR")) return "info";
  if (zone === "DOWN") return "danger";
  if (zone === "UNKNOWN") return "muted";
  return "warn";
}

export function eyesTone(state: DriverState["eyes"]["state"]): Tone {
  return ({ OPEN: "ok", ALMOST_CLOSED: "warn", CLOSED: "danger", UNKNOWN: "muted" } as const)[state];
}
