// A scrolling, terminal-like log: lines are appended by narrator.ts and shown by components/Terminal.tsx.
import { useSyncExternalStore } from "react";

/** Colour of a line's tag and text. */
export type Tone = "sim" | "cam" | "ok" | "info" | "warn" | "danger" | "muted";

export interface LogLine {
  id: number;
  time: string; // HH:MM:SS
  tag: string; // e.g. SIM, CAM, OK, MISSED, DRIVER
  tone: Tone;
  text: string;
  rows?: StatRow[]; // a small table under the text (the end-of-run statistics)
}

export interface StatRow {
  label: string;
  value: string;
  tone?: Tone;
}

const MAX_LINES = 500;
let nextId = 1;

export class Log {
  private lines: LogLine[] = [];
  private listeners = new Set<() => void>();

  add(tag: string, tone: Tone, text: string, rows?: StatRow[]): void {
    const time = new Date().toLocaleTimeString("en-GB", { hour12: false });
    this.lines = [...this.lines.slice(-(MAX_LINES - 1)), { id: nextId++, time, tag, tone, text, rows }];
    this.listeners.forEach((l) => l());
  }

  subscribe = (l: () => void) => {
    this.listeners.add(l);
    return () => {
      this.listeners.delete(l);
    };
  };

  snapshot = () => this.lines;
}

export function useLog(log: Log): LogLine[] {
  return useSyncExternalStore(log.subscribe, log.snapshot);
}

/** Left terminal: the simulation (its objects, zones, reactions) and where the driver looks. */
export const simLog = new Log();
