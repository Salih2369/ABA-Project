import { ACTIVITY_LABEL, ERROR_LABEL, INDICATOR_LABEL, SESSION_STATE_LABEL, TERMINATION_LABEL, titleCase } from "./format";
import type {
  Activity,
  EngineEvent,
  EngineSnapshot,
  Identity,
  RecorderStatus,
  RecordingArtifact,
  LiveEvent,
  Observation,
  SessionSnapshot,
  SessionState,
  StreamReady,
} from "./types";

export type Connection = "idle" | "connecting" | "live" | "reconnecting" | "closed";
export type Tone = "neutral" | "attention" | "critical" | "positive";

export interface TimelineItem {
  key: string;
  sequence: number;
  kind: "state" | "identity" | "alert" | "error" | "activity";
  videoTime: number | null;
  title: string;
  detail?: string;
  tone: Tone;
  repeat: number;
  code?: string;
}

export interface LiveState {
  snapshot: SessionSnapshot | null;
  sessionState: SessionState | null;
  lastSequence: number;
  videoTime: number | null;
  identity: Identity | null;
  observation: Observation | null;
  engine: EngineSnapshot | null;
  activity: Activity;
  alert: EngineEvent | null;
  alertCount: number;
  observationCount: number;
  providerErrors: number;
  latencyMs: number | null;
  /** Age of the latest result relative to the session clock, and whether it exceeded the stale limit. */
  ageS: number | null;
  analysisStale: boolean;
  staleCount: number;
  performance: { p50: number; p95: number; maxAgeS: number } | null;
  recorder: RecorderStatus | null;
  recording: RecordingArtifact | null;
  timeline: TimelineItem[];
  termination: { reason: string; recorded: boolean } | null;
  connection: Connection;
  gapDetected: boolean;
}

export type Action =
  | { type: "reset" }
  | { type: "snapshot"; snapshot: SessionSnapshot }
  | { type: "stream_ready"; ready: StreamReady }
  | { type: "event"; event: LiveEvent }
  | { type: "connection"; status: Connection }
  | { type: "dismiss_alert"; id: number };

export const TERMINAL: readonly SessionState[] = ["completed", "failed"];
const TIMELINE_LIMIT = 150;

export const initialState: LiveState = {
  snapshot: null,
  sessionState: null,
  lastSequence: 0,
  videoTime: null,
  identity: null,
  observation: null,
  engine: null,
  activity: "table",
  alert: null,
  alertCount: 0,
  observationCount: 0,
  providerErrors: 0,
  latencyMs: null,
  ageS: null,
  analysisStale: false,
  staleCount: 0,
  performance: null,
  recorder: null,
  recording: null,
  timeline: [],
  termination: null,
  connection: "idle",
  gapDetected: false,
};

function push(state: LiveState, item: Omit<TimelineItem, "key" | "repeat">): TimelineItem[] {
  const [top, ...rest] = state.timeline;
  if (top && item.kind === "error" && top.kind === "error" && top.code === item.code) {
    return [{ ...top, repeat: top.repeat + 1, videoTime: item.videoTime, sequence: item.sequence }, ...rest];
  }
  return [{ ...item, key: `${item.sequence}-${item.kind}`, repeat: 1 }, ...state.timeline].slice(0, TIMELINE_LIMIT);
}

function applyEvent(state: LiveState, event: LiveEvent): LiveState {
  if (event.sequence <= state.lastSequence) return state; // duplicate after a resume
  const gapDetected = state.gapDetected || (state.lastSequence > 0 && event.sequence > state.lastSequence + 1);
  const next: LiveState = { ...state, lastSequence: event.sequence, gapDetected };

  switch (event.event_type) {
    case "session_state": {
      const terminal = TERMINAL.includes(event.state);
      const reason = event.reason ? TERMINATION_LABEL[event.reason] ?? titleCase(event.reason) : undefined;
      return {
        ...next,
        sessionState: event.state,
        alert: terminal ? null : next.alert,
        recording: terminal ? event.recording ?? null : next.recording,
        termination: terminal ? { reason: event.reason ?? "unknown", recorded: event.recorded === true } : null,
        timeline: push(next, {
          sequence: event.sequence,
          kind: "state",
          videoTime: next.videoTime,
          title: SESSION_STATE_LABEL[event.state],
          detail: reason,
          tone: event.state === "failed" ? "critical" : event.state === "running" ? "positive" : "neutral",
        }),
      };
    }
    case "identity_state":
      return {
        ...next,
        identity: event.identity,
        // Identity loss invalidates any visible card: it is never shown as current.
        alert: event.identity === "uncertain" ? null : next.alert,
        timeline: push(next, {
          sequence: event.sequence,
          kind: "identity",
          videoTime: event.video_time,
          title: event.identity === "confirmed" ? "Identity confirmed" : "Identity uncertain",
          detail: event.identity === "uncertain" ? "All five indicators suppressed" : undefined,
          tone: event.identity === "confirmed" ? "positive" : "attention",
        }),
      };
    case "observation":
      return {
        ...next,
        observation: event.observation,
        videoTime: event.video_time,
        observationCount: next.observationCount + 1,
        latencyMs: event.processed_monotonic_ms - event.captured_monotonic_ms,
        ageS: event.age_s,
        analysisStale: event.stale,
        staleCount: next.staleCount + (event.stale ? 1 : 0),
      };
    case "engine_state": {
      const engine = event.state;
      // A candidate from a stale result is logged, but never shown as the current card.
      if (engine.alert && event.stale) {
        return {
          ...next,
          engine,
          activity: engine.activity,
          alert: null,
          alertCount: next.alertCount + 1,
          timeline: push(next, {
            sequence: event.sequence,
            kind: "alert",
            videoTime: engine.alert.evidence_time,
            title: `${INDICATOR_LABEL[engine.alert.type]} — candidate (late)`,
            detail: "Analysis was behind live; not shown as current",
            tone: "attention",
          }),
        };
      }
      if (engine.alert) {
        return {
          ...next,
          engine,
          activity: engine.activity,
          alert: engine.alert,
          alertCount: next.alertCount + 1,
          timeline: push(next, {
            sequence: event.sequence,
            kind: "alert",
            videoTime: engine.alert.evidence_time,
            title: `${INDICATOR_LABEL[engine.alert.type]} — candidate`,
            detail: "For therapist review",
            tone: "attention",
          }),
        };
      }
      const updated = next.alert ? engine.events.find((e) => e.id === next.alert?.id) ?? next.alert : null;
      return { ...next, engine, activity: engine.activity, alert: updated };
    }
    case "recording_status":
      return { ...next, recorder: { kind: event.kind, state: event.state, frames: event.frames,
        dropped_frames: event.dropped_frames, pixels_stored: event.pixels_stored } };
    case "performance":
      return {
        ...next,
        recorder: event.recorder,
        performance: { p50: event.provider_ms_p50, p95: event.provider_ms_p95, maxAgeS: event.max_age_s },
      };
    case "activity":
      return {
        ...next,
        activity: event.activity,
        timeline: push(next, {
          sequence: event.sequence,
          kind: "activity",
          videoTime: event.video_time,
          title: `Activity: ${ACTIVITY_LABEL[event.activity]}`,
          detail: "Open episodes closed at the boundary",
          tone: "neutral",
        }),
      };
    case "error":
      return {
        ...next,
        providerErrors: next.providerErrors + (event.code === "provider_failed" ? 1 : 0),
        timeline: push(next, {
          sequence: event.sequence,
          kind: "error",
          code: event.code,
          videoTime: event.video_time ?? next.videoTime,
          title: ERROR_LABEL[event.code] ?? titleCase(event.code),
          detail: "No observation was generated",
          tone: "critical",
        }),
      };
    default:
      // Forward compatibility: an event type this UI does not know is ignored, never fatal.
      return next;
  }
}

export function reducer(state: LiveState, action: Action): LiveState {
  switch (action.type) {
    case "reset":
      return initialState;
    case "snapshot":
      return {
        ...state,
        snapshot: action.snapshot,
        sessionState: state.lastSequence >= action.snapshot.last_sequence ? state.sessionState : action.snapshot.state,
      };
    case "stream_ready":
      return { ...state, snapshot: action.ready.snapshot, connection: "live" };
    case "connection":
      return { ...state, connection: action.status };
    case "dismiss_alert":
      return state.alert?.id === action.id ? { ...state, alert: null } : state;
    case "event":
      return applyEvent(state, action.event);
  }
}
