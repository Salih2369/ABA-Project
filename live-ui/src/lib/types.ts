export const INDICATORS = [
  "orientation",
  "body_motion",
  "out_of_seat",
  "hand_motion",
  "posture_change",
] as const;

export type Indicator = (typeof INDICATORS)[number];
export type Signal = boolean | null;
export type Identity = "confirmed" | "uncertain";
export type Activity = "table" | "movement" | "break";
export type SessionState =
  | "created"
  | "previewing"
  | "target_selected"
  | "running"
  | "paused"
  | "stopping"
  | "completed"
  | "failed";
export type IndicatorState = "unobservable" | "not_applicable" | "inactive" | "candidate" | "active";
export type Command = "open" | "select_target" | "start" | "pause" | "resume" | "stop" | "set_activity";

export type ProviderKind = "synthetic" | "precomputed";

export interface Scenario {
  id: string;
  title: string;
  summary: string;
  kind: ProviderKind;
  duration_s: number | null;
  fps: number | null;
  exercises: string[];
}

export interface RecorderStatus {
  kind: string;
  state: string;
  frames: number;
  dropped_frames: number;
  pixels_stored: boolean;
}

export interface RecordingArtifact extends RecorderStatus {
  first_time: number | null;
  last_time: number | null;
  sha256: string;
  verified: boolean;
}

export interface SessionSnapshot {
  session_id: string;
  state: SessionState;
  provider: string;
  source: string;
  recording: string;
  recorder: RecorderStatus;
  activity: Activity;
  elapsed_s: number;
  last_video_time: number | null;
  termination_reason: string | null;
  provider_errors: number;
  stale_observations: number;
  last_sequence: number;
  scenario: Scenario;
}

export interface Observation {
  time: number;
  identity: Identity;
  signals: Record<Indicator, Signal>;
  values: Record<string, unknown>;
}

export interface EngineEvent {
  id: number;
  type: Indicator;
  start: number;
  end: number | null;
  end_reason?: string;
  duration: number;
  alerted_at: number | null;
  evidence_time: number;
}

export interface IndicatorSnapshot {
  state: IndicatorState;
  duration: number;
  count: number;
  observable_seconds: number;
  value: unknown;
}

export interface EngineSnapshot {
  time: number;
  activity: Activity;
  identity: Identity;
  indicators: Record<Indicator, IndicatorSnapshot>;
  events: EngineEvent[];
  alert: EngineEvent | null;
}

interface Envelope {
  schema_version: number;
  session_id: string;
  sequence: number;
  provider: string;
  monotonic_ms: number;
}

export type LiveEvent = Envelope &
  (
    | {
        event_type: "session_state";
        state: SessionState;
        reason?: string;
        simulation?: boolean;
        recorded?: boolean;
        recording?: RecordingArtifact | null;
      }
    | { event_type: "identity_state"; identity: Identity; video_time: number }
    | {
        event_type: "observation";
        source_frame_index: number;
        video_time: number;
        captured_monotonic_ms: number;
        processed_monotonic_ms: number;
        age_s: number;
        stale: boolean;
        observation: Observation;
      }
    | { event_type: "engine_state"; video_time: number; stale: boolean; state: EngineSnapshot }
    | ({ event_type: "recording_status" } & RecorderStatus)
    | {
        event_type: "performance";
        video_time: number;
        frames: number;
        provider_ms_p50: number;
        provider_ms_p95: number;
        provider_ms_max: number;
        max_age_s: number;
        stale_observations: number;
        recorder: RecorderStatus;
      }
    | { event_type: "activity"; activity: Activity; video_time: number | null }
    | { event_type: "error"; code: string; detail: string; video_time?: number; source_frame_index?: number }
  );

export interface StreamReady {
  event_type: "stream_ready";
  snapshot: SessionSnapshot;
  history_start: number;
  after: number;
}
