import { CameraPanel } from "./components/CameraPanel";
import { ChatPanel } from "./components/ChatPanel";
import { Header } from "./components/Header";
import { StatusPanel } from "./components/StatusPanel";
import { STALE_MS } from "./config";
import { useNow, useServer } from "./useServer";

export function App() {
  const { connected, driver, driverAt, gaze } = useServer();
  const now = useNow();
  const fresh = connected && driver != null && now - driverAt < STALE_MS ? driver : null;
  const alarm = fresh && (fresh.summary.highest_severity === "DANGER" || fresh.summary.highest_severity === "CRITICAL");

  return (
    <div className={`app${alarm ? " alarm" : ""}`}>
      <Header connected={connected} driver={fresh} />
      <CameraPanel
        live={fresh != null}
        calibrationMessage={fresh?.calibration.status === "CALIBRATING" ? fresh.calibration.message : null}
      />
      <ChatPanel />
      <StatusPanel driver={fresh} gaze={gaze} />
    </div>
  );
}
