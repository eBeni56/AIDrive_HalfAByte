// The free layout of the dashboard: every panel is a window that can be moved (drag its title bar) and resized
// (drag an edge or a corner), like pictures on a slide. Positions are FRACTIONS of the workspace (0..1), so an
// arrangement keeps its proportions when the browser window changes (full screen <-> split screen). Saved in this
// browser's localStorage; "Reset layout" brings back the default.

export type PanelId = "camera" | "chat" | "simulation" | "driver";
export const PANELS: PanelId[] = ["camera", "chat", "simulation", "driver"];

/** A window's place, as fractions of the workspace: x, y = top left; w, h = size. */
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Layout {
  rects: Record<PanelId, Rect>;
  order: PanelId[]; // back to front: the last one is on top
  locked: boolean; // locked: nothing can be moved or resized (e.g. during a demo)
}

export const GAP_PX = 8; // the default gap between windows; edges also snap to this gap
export const SNAP_PX = 8; // an edge this close to another window's edge (or the workspace's) snaps to it
export const MIN_W_PX = 180;
export const MIN_H_PX = 110;

const STORAGE_KEY = "aidrive.dashboard.layout.v1";

/** The classic arrangement: camera above the chat on the left, then the Simulation log and the driver display. */
export function defaultLayout(W: number, H: number): Layout {
  W = W > 0 ? W : 1600;
  H = H > 0 ? H : 900;
  const g = GAP_PX;
  const sideW = Math.min(440, Math.max(300, W * 0.28));
  const camH = Math.min(H * 0.45, (sideW * 9) / 16 + 32); // a 16:9 image + the title bar
  const colW = (W - sideW - 2 * g) / 2;
  const px = (x: number, y: number, w: number, h: number): Rect => ({ x: x / W, y: y / H, w: w / W, h: h / H });
  return {
    rects: {
      camera: px(0, 0, sideW, camH),
      chat: px(0, camH + g, sideW, H - camH - g),
      simulation: px(sideW + g, 0, colW, H),
      driver: px(sideW + 2 * g + colW, 0, colW, H),
    },
    order: [...PANELS],
    locked: false,
  };
}

function isRect(r: unknown): r is Rect {
  if (!r || typeof r !== "object") return false;
  const { x, y, w, h } = r as Record<string, unknown>;
  const ok = (v: unknown) => typeof v === "number" && Number.isFinite(v);
  return ok(x) && ok(y) && ok(w) && ok(h) && (w as number) > 0 && (h as number) > 0 &&
    (x as number) >= 0 && (y as number) >= 0 && (x as number) + (w as number) <= 1.001 && (y as number) + (h as number) <= 1.001;
}

/** The saved layout, or null (nothing saved, unreadable, or storage blocked). */
export function loadLayout(): Layout | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<Layout>;
    if (!v || typeof v !== "object" || !v.rects || !PANELS.every((id) => isRect(v.rects![id]))) return null;
    const order = Array.isArray(v.order) && v.order.length === PANELS.length && PANELS.every((id) => v.order!.includes(id))
      ? v.order
      : [...PANELS];
    return { rects: v.rects, order, locked: v.locked === true };
  } catch {
    return null;
  }
}

export function saveLayout(layout: Layout): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
  } catch {
    /* storage blocked (private window): the layout just isn't remembered */
  }
}

// ------------------------------------------------------------------ snapping (in pixels)
/** The offset that moves one of `edges` onto the nearest of `targets` within SNAP_PX, or 0. */
export function snapOffset(edges: number[], targets: number[]): number {
  let best = 0;
  let bestDist = SNAP_PX + 1;
  for (const e of edges) {
    for (const t of targets) {
      const d = Math.abs(t - e);
      if (d <= SNAP_PX && d < bestDist) {
        best = t - e;
        bestDist = d;
      }
    }
  }
  return best;
}

/** Where edges may snap along one axis: the workspace's edges and every other window's edges (also one gap away). */
export function snapTargets(size: number, others: { a: number; b: number }[]): number[] {
  const out = [0, size];
  for (const { a, b } of others) out.push(a, b, a - GAP_PX, b + GAP_PX);
  return out;
}
