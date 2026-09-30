import type { Activity, Indicator, IndicatorState, SessionState } from "./types";

export const INDICATOR_LABEL: Record<Indicator, string> = {
  orientation: "Orientation",
  body_motion: "Body motion",
  out_of_seat: "Out of seat",
  hand_motion: "Hand motion",
  posture_change: "Posture change",
};

/** Describes what the proxy measures, never what it means clinically. */
export const INDICATOR_BASIS: Record<Indicator, string> = {
  orientation: "Head-turn proxy relative to task area",
  body_motion: "Normalized torso and limb speed",
  out_of_seat: "Standing after a seated baseline",
  hand_motion: "Wrist speed relative to torso",
  posture_change: "Confirmed sit ↔ stand transition",
};

export const INDICATOR_STATE_LABEL: Record<IndicatorState, string> = {
  active: "Sustained",
  candidate: "Building",
  inactive: "Observed · none",
  unobservable: "Not observable",
  not_applicable: "Not applicable",
};

export const SESSION_STATE_LABEL: Record<SessionState, string> = {
  created: "Created",
  previewing: "Preview",
  target_selected: "Target selected",
  running: "Running",
  paused: "Paused",
  stopping: "Stopping",
  completed: "Completed",
  failed: "Failed",
};

export const ACTIVITY_LABEL: Record<Activity, string> = {
  table: "Table work",
  movement: "Movement",
  break: "Break",
};

export const TERMINATION_LABEL: Record<string, string> = {
  user_stop: "Stopped by therapist",
  source_eof: "Source reached its end",
  source_disconnected: "Source disconnected",
  worker_failed: "Analysis worker failed",
  server_shutdown: "Server shut down",
  user_left: "Left by therapist",
  recorder_failed: "Recorder failed",
};

export const ERROR_LABEL: Record<string, string> = {
  provider_failed: "Analysis unavailable for a frame",
  source_disconnected: "Video source disconnected",
  worker_failed: "Analysis worker failed",
  recorder_failed: "Recorder failed — no complete recording",
};

export function formatClock(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "--:--.-";
  const clamped = Math.max(0, seconds);
  const minutes = Math.floor(clamped / 60);
  const rest = clamped - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${rest.toFixed(1).padStart(4, "0")}`;
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
}

export function titleCase(value: string): string {
  return value.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
