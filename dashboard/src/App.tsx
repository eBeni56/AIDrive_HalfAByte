import { CameraPanel } from "./components/CameraPanel";
import { ChatPanel } from "./components/ChatPanel";
import { DriverHud } from "./components/DriverHud";
import { Terminal } from "./components/Terminal";
import { Workspace } from "./components/Workspace";
import { simLog } from "./log";
import { useServer } from "./useServer";

export function App() {
  const cameraLive = useServer();

  // Each panel is a window of the workspace: moved by its title bar, resized by its edges (Workspace.tsx).
  return (
    <Workspace
      panels={{
        camera: <CameraPanel live={cameraLive} />,
        chat: <ChatPanel />,
        simulation: (
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
        ),
        driver: <DriverHud />,
      }}
    />
  );
}
