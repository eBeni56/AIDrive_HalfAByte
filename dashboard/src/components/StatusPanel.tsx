// The bottom row: what the driver monitor has worked out from the camera.
import type { ReactNode } from "react";
import {
  attentionTone,
  deg,
  drowsinessTone,
  eyesTone,
  gazeTone,
  label,
  num,
  severityTone,
  type Tone,
} from "../format";
import type { DriverState, GazeUpdate } from "../types";

interface Props {
  driver: DriverState | null; // null = no fresh camera data
  gaze: GazeUpdate | null;
}

function Card({ title, value, tone, children }: { title: string; value: string; tone: Tone; children?: ReactNode }) {
  return (
    <div className={`card tone-${tone}`}>
      <div className="card-title">{title}</div>
      <div className="card-value">{value}</div>
      {children && <dl className="card-rows">{children}</dl>}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string | number }) {
  return (
    <>
      <dt>{k}</dt>
      <dd>{v}</dd>
    </>
  );
}

export function StatusPanel({ driver, gaze }: Props) {
  if (!driver) {
    return (
      <section className="panel status">
        <div className="status-empty">No camera data. The cards fill in once the driver monitor sends data.</div>
      </section>
    );
  }
  const { drowsiness: dr, attention: at, gaze: gz, eyes, head, mouth } = driver;
  const ind = dr.indicators;
  const active = driver.detections.filter((d) => d.status !== "ENDED");

  return (
    <section className="panel status">
      <Card title="Drowsiness" value={label(dr.level)} tone={drowsinessTone(dr)}>
        <Row k="Score" v={num(dr.score, 2)} />
        <Row k="PERCLOS" v={ind.perclos == null ? "–" : `${(ind.perclos * 100).toFixed(0)}%`} />
        <Row k="Blinks / min" v={num(ind.blink_rate_per_min, 0)} />
        <Row k="Yawns (5 min)" v={ind.yawns_5min} />
        <Row k="Microsleeps (10 min)" v={ind.microsleeps_10min} />
        {dr.warming_up && <Row k="" v="still learning…" />}
      </Card>

      <Card title="Attention" value={label(at.state)} tone={attentionTone(at)}>
        <Row k="Eyes off road" v={num(at.eyes_off_road_s, 1, " s")} />
        <Row k="Off road (30 s)" v={num(at.off_road_last_30s_s, 1, " s")} />
        <Row k="Longest glance (60 s)" v={num(at.longest_glance_60s_s, 1, " s")} />
        <Row k="Glances (60 s)" v={at.glances_60s} />
      </Card>

      <Card title="Gaze" value={label(gz.zone)} tone={gazeTone(gz.zone)}>
        <Row k="Direction" v={label(gz.direction)} />
        <Row k="Yaw / pitch" v={`${deg(gz.yaw_deg)} / ${deg(gz.pitch_deg)}`} />
        <Row k="For" v={num(gz.zone_for_s, 1, " s")} />
        <Row k="Simulator region" v={gaze?.status === "ok" ? label(gaze.region) : label(gaze?.status)} />
      </Card>

      <Card title="Eyes & head" value={`Eyes ${label(eyes.state).toLowerCase()}`} tone={eyesTone(eyes.state)}>
        <Row k="Openness" v={num(eyes.openness, 2)} />
        <Row k="vs. normal" v={eyes.openness_ratio == null ? "–" : `${(eyes.openness_ratio * 100).toFixed(0)}%`} />
        <Row k="Last blink" v={eyes.last_blink ? `${eyes.last_blink.duration_ms.toFixed(0)} ms` : "–"} />
        <Row k="Head yaw / pitch / roll" v={`${deg(head.yaw_deg)} / ${deg(head.pitch_deg)} / ${deg(head.roll_deg)}`} />
        <Row k="Mouth" v={label(mouth.state)} />
      </Card>

      <Card
        title="Active detections"
        value={active.length ? `${active.length} active` : "None"}
        tone={severityTone(driver.summary.highest_severity)}
      >
        {active.map((d) => (
          <Row key={d.id} k={label(d.type)} v={`${label(d.severity)} · ${d.duration_s.toFixed(1)} s`} />
        ))}
      </Card>
    </section>
  );
}
