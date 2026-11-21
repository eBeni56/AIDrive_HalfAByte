import { CameraPanel } from "./components/CameraPanel";
import { ChatPanel } from "./components/ChatPanel";
import { DriverHud } from "./components/DriverHud";
import { Terminal } from "./components/Terminal";
import { simLog } from "./log";
import { useServer } from "./useServer";

export function App() {
  const cameraLive = useServer();

  return (
    <div className="app">
      <aside className="side">
        <CameraPanel live={cameraLive} />
        <ChatPanel />
      </aside>
      <Terminal
        title="Simulation"
        log={simLog}
        legend={[
          ["SIM", "sim", "input from the simulation"],
          ["CAM", "cam", "where the driver looks (camera)"],
          ["OK", "ok", "looked at the object in time"],
          ["MISSED", "danger", "did not look in time"],
        ]}
      />
      <DriverHud />
    </div>
  );
}
