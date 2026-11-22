// The driver monitor's own preview (eye contours, iris, gaze arrow, phone box, status panel, detections),
// streamed as MJPEG. The browser can't open the webcam itself: the driver monitor already uses it.
import { useEffect, useRef, useState } from "react";
import { CAMERA_URL, RETRY_MS } from "../config";

interface Props {
  live: boolean; // fresh driver_state is arriving (the driver monitor runs)
}

export function CameraPanel({ live }: Props) {
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const retryTimer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(retryTimer.current), []); // no retry after the panel is gone

  // The driver monitor (re)started: open the stream again, an old connection may be dead.
  useEffect(() => {
    if (live) {
      setLoaded(false);
      setAttempt((a) => a + 1);
    }
  }, [live]);

  const retryLater = () => {
    setLoaded(false);
    window.clearTimeout(retryTimer.current);
    retryTimer.current = window.setTimeout(() => setAttempt((a) => a + 1), RETRY_MS);
  };

  const showing = loaded && live;
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
