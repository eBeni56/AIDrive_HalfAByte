// Connection to the server: says hello as the dashboard, then receives
//   driver_state           (DASHBOARD_HZ, default 10/s)  what the camera detects   -> driver display
//   gaze_update            (GAZE_HZ, default 10/s)       where the driver looks    -> both
//   simulation_started / _ended, attention_event, attention_summary              -> both
//   chat_message           (when one is sent)                                     -> chat panel
// Reconnects every RETRY_MS if the server is not running or restarts.
import { useEffect, useState } from "react";
import { addChatMessage } from "./chat";
import { RETRY_MS, STALE_MS, WS_URL } from "./config";
import { hudInput } from "./hud";
import { onDriverState, onGaze, onServer, onSimulation } from "./narrator";
import type { DriverState, GazeUpdate } from "./types";

/** True while fresh driver_state arrives (the driver monitor runs and the server is connected). */
export function useServer(): boolean {
  const [cameraLive, setCameraLive] = useState(false);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry: number | undefined;
    let stopped = false;
    let lastDriverAt = 0;
    let live = false;
    let wasConnected = false;

    const setLive = (v: boolean) => {
      if (v === live) return;
      live = v;
      setCameraLive(v);
    };
    const check = window.setInterval(() => {
      if (live && Date.now() - lastDriverAt > STALE_MS) setLive(false);
    }, 250);

    const open = () => {
      ws = new WebSocket(WS_URL);
      ws.onopen = () => {
        ws?.send(JSON.stringify({ type: "hello", role: "dashboard" }));
        wasConnected = true;
        hudInput.connection(true);
        onServer(true);
      };
      ws.onmessage = (e: MessageEvent<string>) => {
        let msg: Record<string, unknown>;
        try {
          msg = JSON.parse(e.data);
        } catch {
          return;
        }
        if (msg.type === "driver_state") {
          lastDriverAt = Date.now();
          setLive(true);
          hudInput.driverState(msg as unknown as DriverState);
          onDriverState(msg as unknown as DriverState);
        } else if (msg.event === "gaze_update") {
          hudInput.gaze(msg as unknown as GazeUpdate);
          onGaze(msg as unknown as GazeUpdate);
        } else if (msg.type === "chat_message" && (msg.role === "user" || msg.role === "assistant")) {
          addChatMessage(msg.role, String(msg.text ?? ""));
        } else if (typeof msg.event === "string") {
          hudInput.simulation(msg);
          onSimulation(msg);
        }
      };
      ws.onclose = () => {
        if (wasConnected) onServer(false);
        hudInput.connection(false);
        wasConnected = false;
        setLive(false);
        if (!stopped) retry = window.setTimeout(open, RETRY_MS);
      };
    };

    open();
    return () => {
      stopped = true;
      window.clearTimeout(retry);
      window.clearInterval(check);
      ws?.close();
    };
  }, []);

  return cameraLive;
}
