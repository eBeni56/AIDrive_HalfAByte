// Connection to the server: says hello as the dashboard, then receives
//   driver_state  (DASHBOARD_HZ, default 10/s)  everything the camera detects
//   gaze_update   (GAZE_HZ, default 10/s)       the gaze as a simulator region
//   chat_message  (when one is sent)            -> the chat panel
// Reconnects every RETRY_MS if the server is not running or restarts.
import { useEffect, useState } from "react";
import { addChatMessage } from "./chat";
import { RETRY_MS, WS_URL } from "./config";
import type { DriverState, GazeUpdate } from "./types";

export interface ServerData {
  connected: boolean;
  driver: DriverState | null;
  driverAt: number; // Date.now() of the last driver_state (0 = never)
  gaze: GazeUpdate | null;
}

export function useServer(): ServerData {
  const [data, setData] = useState<ServerData>({ connected: false, driver: null, driverAt: 0, gaze: null });

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry: number | undefined;
    let stopped = false;

    const open = () => {
      ws = new WebSocket(WS_URL);
      ws.onopen = () => {
        ws?.send(JSON.stringify({ type: "hello", role: "dashboard" }));
        setData((d) => ({ ...d, connected: true }));
      };
      ws.onmessage = (e: MessageEvent<string>) => {
        let msg: Record<string, unknown>;
        try {
          msg = JSON.parse(e.data);
        } catch {
          return;
        }
        if (msg.type === "driver_state") {
          setData((d) => ({ ...d, driver: msg as unknown as DriverState, driverAt: Date.now() }));
        } else if (msg.event === "gaze_update") {
          setData((d) => ({ ...d, gaze: msg as unknown as GazeUpdate }));
        } else if (msg.type === "chat_message" && (msg.role === "user" || msg.role === "assistant")) {
          addChatMessage(msg.role, String(msg.text ?? ""));
        }
      };
      ws.onclose = () => {
        setData((d) => ({ ...d, connected: false }));
        if (!stopped) retry = window.setTimeout(open, RETRY_MS);
      };
    };

    open();
    return () => {
      stopped = true;
      window.clearTimeout(retry);
      ws?.close();
    };
  }, []);

  return data;
}

/** Re-renders every `ms` so "how old is the data" checks stay current without new messages. */
export function useNow(ms = 500): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}
