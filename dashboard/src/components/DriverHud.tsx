// The driver display: one big icon and one short instruction - the most important current problem.
import type { ReactNode } from "react";
import { useHud, type Arrow, type IconName } from "../hud";

const ICONS: Record<IconName, ReactNode> = {
  alarm: (
    <>
      <circle cx="12" cy="13" r="8" />
      <path d="M12 9v4l2 2M5 3 2 6M22 6l-3-3" />
    </>
  ),
  obstacle: (
    <>
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
      <path d="M12 9v4M12 17h.01" />
    </>
  ),
  phone: (
    <>
      <rect x="6" y="2" width="12" height="20" rx="2" />
      <path d="M11 18h2" />
      <path d="M3 3l18 18" className="strike" />
    </>
  ),
  eye: (
    <>
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  face: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M9 10h.01M15 10h.01M9 15.5c1.8-1 4.2-1 6 0" />
    </>
  ),
  camera: (
    <>
      <path d="M15 10l5-3v10l-5-3z" />
      <rect x="2" y="6" width="13" height="12" rx="2" />
      <path d="M2 2l20 20" className="strike" />
    </>
  ),
  check: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m8 12 3 3 5-6" />
    </>
  ),
};

const ARROW_ROTATION: Record<Arrow, number> = {
  ahead: 0, right: 90, "behind-right": 135, behind: 180, "behind-left": 225, left: 270,
};

export function DriverHud() {
  const issue = useHud();
  return (
    <section className="panel hud">
      <div className="panel-title">
        <span>Driver</span>
      </div>
      <div className={`hud-body level-${issue.level}`} key={issue.key}>
        <div className="hud-icon">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            {ICONS[issue.icon]}
          </svg>
          {issue.arrow && (
            <svg className="hud-arrow" viewBox="0 0 24 24" style={{ transform: `rotate(${ARROW_ROTATION[issue.arrow]}deg)` }} aria-hidden="true">
              <path d="M12 20V4M5 11l7-7 7 7" />
            </svg>
          )}
        </div>
        <div className="hud-title">{issue.title}</div>
        <div className="hud-detail">{issue.detail}</div>
      </div>
    </section>
  );
}
