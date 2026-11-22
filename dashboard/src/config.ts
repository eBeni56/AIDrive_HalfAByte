// Addresses can be set at build time: VITE_WS_URL, VITE_CAMERA_URL (e.g. in dashboard/.env.local).
// A WS_URL starting with "/" means "same host as this page" (Docker: nginx passes /ws to the server).

function absoluteWs(url: string): string {
  if (!url.startsWith("/")) return url;
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}${url}`;
}

/** The server in central_point/ (same one the simulator uses). */
export const WS_URL = absoluteWs(import.meta.env.VITE_WS_URL ?? "ws://localhost:8765");

/** The driver monitor's camera preview with its overlay (MJPEG, camera_stream in its config). */
export const CAMERA_URL = import.meta.env.VITE_CAMERA_URL ?? "http://localhost:8090/stream.mjpg";

/** No driver_state for this long: the camera data is shown as missing. */
export const STALE_MS = 1500;

/** Wait between reconnect attempts (WebSocket and camera stream). */
export const RETRY_MS = 2000;
