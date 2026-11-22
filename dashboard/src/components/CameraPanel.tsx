// The driver monitor's own preview (eye contours, iris, gaze arrow, phone box, status panel, detections),
// streamed as MJPEG. The browser can't open the webcam itself: the driver monitor already uses it.
// Shown all the time, also outside a simulation (the stream comes from the driver monitor, not the server).
import { useEffect, useRef, useState } from "react";
import { CAMERA_URL, RETRY_MS } from "../config";

interface Props {
  live: boolean; // fresh driver_state is arriving (the server sends it only during a simulation)
  running: boolean; // a simulation runs
}

export function CameraPanel({ live, running }: Props) {
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const retryTimer = useRef<number | undefined>(undefined);
  const lostInRun = useRef(false); // the driver monitor went silent during a run (it may have restarted)

  useEffect(() => () => window.clearTimeout(retryTimer.current), []); // no retry after the panel is gone

  // The driver monitor (re)started: open the stream again, an old connection may be dead. Not at every GO
  // (driver_state only comes during a run): only when no image is shown or the camera was lost during a run.
  useEffect(() => {
    if (!live && running) lostInRun.current = true;
    if (live && (!loaded || lostInRun.current)) {
      lostInRun.current = false;
      setLoaded(false);
      setAttempt((a) => a + 1);
    }
  }, [live]); // only on a change of `live`: loaded / running are read as they are then

  const retryLater = () => {
    setLoaded(false);
    window.clearTimeout(retryTimer.current);
    retryTimer.current = window.setTimeout(() => setAttempt((a) => a + 1), RETRY_MS);
  };

  // during a run a stale image is hidden (no camera data); outside a run there is no driver_state to tell
  const showing = loaded && (live || !running);
  return (
    <section className="panel camera">
      <div className="panel-title">
        <span>Camera</span>
      </div>
      <div className="camera-frame">
        <img
          key={attempt}
          src={`${CAMERA_URL}?a=${attempt}`}
          alt="Driver camera with detections"
          onLoad={() => setLoaded(true)}
          onError={retryLater}
          draggable={false}
          style={{ visibility: showing ? "visible" : "hidden" }}
        />
        {!showing && <div className="camera-empty">no camera image</div>}
      </div>
    </section>
  );
}
