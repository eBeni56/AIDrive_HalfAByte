import type { ReactNode } from "react";
import type { Tone } from "../format";
import type { DriverState } from "../types";

interface Props {
  connected: boolean;
  driver: DriverState | null; // null = no fresh camera data
}

function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`pill tone-${tone}`}>
      <span className="dot" />
      {children}
    </span>
  );
}

export function Header({ connected, driver }: Props) {
  const cal = driver?.calibration;
  return (
    <header className="header">
      <h1>
        AiDrive <span>Driver dashboard</span>
      </h1>
      <div className="pills">
        <Pill tone={connected ? "ok" : "danger"}>{connected ? "Server connected" : "Server offline"}</Pill>
        {!driver ? (
          <Pill tone="muted">No camera data</Pill>
        ) : (
          <>
            {cal?.status === "CALIBRATING" ? (
              <Pill tone="warn">Calibrating {Math.round(cal.progress * 100)}%</Pill>
            ) : (
              <Pill tone={cal?.status === "DONE" ? "ok" : "info"}>
                {cal?.status === "DONE" ? "Calibrated" : "Default calibration"}
              </Pill>
            )}
            <Pill tone={driver.quality.face_detected ? "ok" : "danger"}>
              {driver.quality.face_detected ? "Face detected" : "No face"}
            </Pill>
            <Pill tone="muted">{driver.quality.fps.toFixed(0)} fps</Pill>
          </>
        )}
      </div>
    </header>
  );
}
