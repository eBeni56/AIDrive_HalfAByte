// A console-like log that scrolls by itself while you are at the bottom (scroll up to read back).
import { useLayoutEffect, useRef } from "react";
import { useLog, type Log, type Tone } from "../log";

interface Props {
  title: string;
  log: Log;
  legend: [tag: string, tone: Tone, meaning: string][];
}

export function Terminal({ title, log, legend }: Props) {
  const lines = useLog(log);
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useLayoutEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <section className="panel terminal">
      <div className="panel-title">
        <span>{title}</span>
        <span className="legend">
          {legend.map(([tag, tone, meaning]) => (
            <span key={tag} className={`tone-${tone}`} title={meaning}>
              {tag}
            </span>
          ))}
        </span>
      </div>
      <div
        className="terminal-body"
        ref={box}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
        }}
      >
        {lines.length === 0 && <div className="terminal-empty">waiting for data…</div>}
        {lines.map((l) => (
          <div key={l.id} className={`line tone-${l.tone}`}>
            <span className="time">{l.time}</span>
            <span className="tag">{l.tag}</span>
            <span className="text">
              {l.text}
              {l.rows && (
                <dl className="stats">
                  {l.rows.map((r) => (
                    <div key={r.label} className={r.tone ? `tone-${r.tone}` : undefined}>
                      <dt>{r.label}</dt>
                      <dd>{r.value}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
