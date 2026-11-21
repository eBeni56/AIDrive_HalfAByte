// The driver monitor's own preview (eye and lip contours, iris, gaze arrow, status panel, detections),
// streamed as MJPEG. The browser can't open the webcam itself: the driver monitor already uses it.
import { useEffect, useState } from "react";
import { CAMERA_URL, RETRY_MS } from "../config";

interface Props {
  live: boolean; // fresh driver_state is arriving (the driver monitor runs)
  calibrationMessage: string | null;
}

export function CameraPanel({ live, calibrationMessage }: Props) {
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState(false);

  // The driver monitor (re)started: open the stream again, an old connection may be dead.
  useEffect(() => {
    if (live) {
      setLoaded(false);
      setAttempt((a) => a + 1);
    }
  }, [live]);

  const retryLater = () => {
    setLoaded(false);
    window.setTimeout(() => setAttempt((a) => a + 1), RETRY_MS);
  };

  const showing = loaded && live;
  return (
    <section className="panel camera">
      <div className="panel-title">Camera</div>
      <div className="camera-frame">
        <img
          key={attempt}
          src={`${CAMERA_URL}?a=${attempt}`}
          alt="Driver camera with detections"
          onLoad={() => setLoaded(true)}
          onError={retryLater}
          style={{ visibility: showing ? "visible" : "hidden" }}
        />
        {!showing && (
          <div className="camera-empty">
            <strong>{live ? "Waiting for the camera stream…" : "No camera"}</strong>
            <span>
              Start the driver monitor: <code>cd driver_monitor</code>, <code>uv run python -m driver_monitor</code>
            </span>
          </div>
        )}
        {showing && calibrationMessage && <div className="camera-note">{calibrationMessage}</div>}
      </div>
    </section>
  );
}
