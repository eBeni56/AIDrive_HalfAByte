// The free-layout workspace: each panel is a window. Drag a title bar to move it, an edge or a corner to resize
// it; edges snap to the workspace and to the other windows (hold Alt to place freely). Clicking a window brings
// it to the front. "Lock layout" stops all moving; "Reset layout" restores the default. The layout is saved in
// this browser (layout.ts). The panels inside scroll by themselves when their content does not fit.
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import {
  MIN_H_PX,
  MIN_W_PX,
  PANELS,
  defaultLayout,
  loadLayout,
  saveLayout,
  snapOffset,
  snapTargets,
  type Layout,
  type PanelId,
  type Rect,
} from "../layout";

type Edge = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
const EDGES: Edge[] = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];

interface Px {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface Drag {
  id: PanelId;
  mode: "move" | Edge;
  pointerX: number;
  pointerY: number;
  start: Px; // the window when the drag started, in pixels
  W: number; // workspace size then
  H: number;
  xs: number[]; // snap targets
  ys: number[];
}

interface Props {
  panels: Record<PanelId, ReactNode>;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), Math.max(lo, hi));

export function Workspace({ panels }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [layout, setLayout] = useState<Layout>(
    () => loadLayout() ?? defaultLayout(window.innerWidth - 16, window.innerHeight - 16),
  );
  const [drag, setDrag] = useState<Drag | null>(null);
  const [size, setSize] = useState({ W: 0, H: 0 }); // the workspace in pixels (for the size label)

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ W: el.clientWidth, H: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const update = (next: Layout, save = true) => {
    setLayout(next);
    if (save) saveLayout(next);
  };

  const toFront = (id: PanelId, l: Layout): Layout =>
    l.order[l.order.length - 1] === id ? l : { ...l, order: [...l.order.filter((p) => p !== id), id] };

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>, id: PanelId) => {
    if (e.button !== 0) return;
    const target = e.target as HTMLElement;
    const edge = target.dataset.edge as Edge | undefined;
    const onTitle = target.closest(".panel-title") !== null;
    const front = toFront(id, layout);
    if (front !== layout) update(front);
    if (layout.locked || (!edge && !onTitle) || !box.current) return;
    const W = box.current.clientWidth;
    const H = box.current.clientHeight;
    const r = layout.rects[id];
    const others = PANELS.filter((p) => p !== id).map((p) => layout.rects[p]);
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    setDrag({
      id,
      mode: edge ?? "move",
      pointerX: e.clientX,
      pointerY: e.clientY,
      start: { x: r.x * W, y: r.y * H, w: r.w * W, h: r.h * H },
      W,
      H,
      xs: snapTargets(W, others.map((o) => ({ a: o.x * W, b: (o.x + o.w) * W }))),
      ys: snapTargets(H, others.map((o) => ({ a: o.y * H, b: (o.y + o.h) * H }))),
    });
  };

  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag) return;
    const { start: s, W, H, mode } = drag;
    const dx = e.clientX - drag.pointerX;
    const dy = e.clientY - drag.pointerY;
    const snap = !e.altKey;
    const minW = Math.min(MIN_W_PX, W);
    const minH = Math.min(MIN_H_PX, H);
    let px: Px;
    if (mode === "move") {
      let x = s.x + dx;
      let y = s.y + dy;
      if (snap) {
        x += snapOffset([x, x + s.w], drag.xs);
        y += snapOffset([y, y + s.h], drag.ys);
      }
      px = { x: clamp(x, 0, W - s.w), y: clamp(y, 0, H - s.h), w: s.w, h: s.h };
    } else {
      let l = s.x;
      let r = s.x + s.w;
      let t = s.y;
      let b = s.y + s.h;
      const one = (v: number, targets: number[]) => (snap ? v + snapOffset([v], targets) : v);
      if (mode.includes("w")) l = clamp(one(s.x + dx, drag.xs), 0, r - minW);
      if (mode.includes("e")) r = clamp(one(s.x + s.w + dx, drag.xs), l + minW, W);
      if (mode.includes("n")) t = clamp(one(s.y + dy, drag.ys), 0, b - minH);
      if (mode.includes("s")) b = clamp(one(s.y + s.h + dy, drag.ys), t + minH, H);
      px = { x: l, y: t, w: r - l, h: b - t };
    }
    const rect: Rect = { x: px.x / W, y: px.y / H, w: px.w / W, h: px.h / H };
    update({ ...layout, rects: { ...layout.rects, [drag.id]: rect } }, false);
  };

  const onPointerUp = () => {
    if (!drag) return;
    setDrag(null);
    saveLayout(layout);
  };

  const reset = () => {
    const el = box.current;
    update({ ...defaultLayout(el?.clientWidth ?? 0, el?.clientHeight ?? 0), locked: layout.locked });
  };

  const pct = (v: number) => `${v * 100}%`;
  return (
    <div ref={box} className={`workspace${layout.locked ? " locked" : ""}${drag ? " busy" : ""}`}>
      {PANELS.map((id) => {
        const r = layout.rects[id];
        const active = drag?.id === id;
        return (
          <div
            key={id}
            className={`win${active ? " active" : ""}`}
            style={{ left: pct(r.x), top: pct(r.y), width: pct(r.w), height: pct(r.h), zIndex: layout.order.indexOf(id) + 1 }}
            onPointerDown={(e) => onPointerDown(e, id)}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
          >
            {panels[id]}
            {!layout.locked && EDGES.map((edge) => <div key={edge} className={`grip grip-${edge}`} data-edge={edge} />)}
            {active && (
              <div className="win-size">
                {Math.round(r.w * size.W)} × {Math.round(r.h * size.H)}
              </div>
            )}
          </div>
        );
      })}
      <div className="layout-tools">
        <button
          type="button"
          onClick={() => update({ ...layout, locked: !layout.locked })}
          title={layout.locked ? "Allow moving and resizing the panels" : "Fix the panels in place"}
        >
          {layout.locked ? "Unlock layout" : "Lock layout"}
        </button>
        <button type="button" onClick={reset} title="Back to the default arrangement">
          Reset layout
        </button>
      </div>
    </div>
  );
}
