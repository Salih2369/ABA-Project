import { describe, expect, it } from "vitest";
import { initialState, reducer, type LiveState } from "./reducer";
import type { EngineEvent, EngineSnapshot, LiveEvent } from "./types";

const base = { schema_version: 1, session_id: "synthetic", provider: "synthetic", monotonic_ms: 0 };

function apply(state: LiveState, ...events: Array<Record<string, unknown>>): LiveState {
  return events.reduce<LiveState>(
    (acc, event) => reducer(acc, { type: "event", event: { ...base, ...event } as LiveEvent }),
    state,
  );
}

const alert: EngineEvent = {
  id: 1, type: "out_of_seat", start: 0, end: null, duration: 2, alerted_at: 2, evidence_time: 2,
};

function engine(overrides: Partial<EngineSnapshot> = {}): EngineSnapshot {
  return { time: 2, activity: "table", identity: "confirmed", indicators: {} as EngineSnapshot["indicators"],
    events: [alert], alert: null, ...overrides };
}

describe("live reducer", () => {
  it("ignores duplicate sequences after a resume", () => {
    const once = apply(initialState, { sequence: 1, event_type: "session_state", state: "created" });
    const twice = apply(once, { sequence: 1, event_type: "session_state", state: "failed" });
    expect(twice).toBe(once);
  });

  it("ignores unknown event types instead of failing", () => {
    const state = apply(initialState, { sequence: 1, event_type: "from_a_newer_server", payload: 1 });
    expect(state.lastSequence).toBe(1);
    expect(state.timeline).toHaveLength(0);
  });

  it("flags sequence gaps", () => {
    const state = apply(initialState,
      { sequence: 1, event_type: "session_state", state: "created" },
      { sequence: 5, event_type: "session_state", state: "previewing" });
    expect(state.gapDetected).toBe(true);
  });

  it("shows alerts only from the engine output", () => {
    const state = apply(initialState, {
      sequence: 1, event_type: "observation", source_frame_index: 0, video_time: 0,
      captured_monotonic_ms: 10, processed_monotonic_ms: 14,
      observation: { time: 0, identity: "confirmed", signals: { out_of_seat: true }, values: {} },
    });
    expect(state.alert).toBeNull();
    expect(state.latencyMs).toBe(4);
    const alerted = apply(state, { sequence: 2, event_type: "engine_state", video_time: 2, state: engine({ alert }) });
    expect(alerted.alert?.type).toBe("out_of_seat");
    expect(alerted.alertCount).toBe(1);
  });

  it("clears the visible card when identity becomes uncertain", () => {
    const alerted = apply(initialState, { sequence: 1, event_type: "engine_state", video_time: 2, state: engine({ alert }) });
    const lost = apply(alerted, { sequence: 2, event_type: "identity_state", identity: "uncertain", video_time: 3 });
    expect(lost.alert).toBeNull();
    expect(lost.timeline[0]?.title).toBe("Identity uncertain");
  });

  it("clears the card and records termination on failure", () => {
    const alerted = apply(initialState, { sequence: 1, event_type: "engine_state", video_time: 2, state: engine({ alert }) });
    const failed = apply(alerted, {
      sequence: 2, event_type: "session_state", state: "failed", reason: "source_disconnected", recorded: false,
    });
    expect(failed.alert).toBeNull();
    expect(failed.termination).toEqual({ reason: "source_disconnected", recorded: false });
  });

  it("collapses repeated provider errors", () => {
    const error = { event_type: "error", code: "provider_failed", detail: "x" };
    const state = apply(initialState, { sequence: 1, ...error, video_time: 1 }, { sequence: 2, ...error, video_time: 1.2 });
    expect(state.timeline).toHaveLength(1);
    expect(state.timeline[0]?.repeat).toBe(2);
    expect(state.providerErrors).toBe(2);
  });

  it("never shows a candidate from a stale result as current", () => {
    const late = apply(initialState, {
      sequence: 1, event_type: "engine_state", video_time: 2, stale: true, state: engine({ alert }),
    });
    expect(late.alert).toBeNull();
    expect(late.alertCount).toBe(1);
    expect(late.timeline[0]?.title).toContain("late");
  });

  it("tracks the ending of the visible episode", () => {
    const alerted = apply(initialState, { sequence: 1, event_type: "engine_state", video_time: 2, state: engine({ alert }) });
    const ended = apply(alerted, {
      sequence: 2, event_type: "engine_state", video_time: 4, state: engine({ events: [{ ...alert, end: 3.4 }] }),
    });
    expect(ended.alert?.end).toBe(3.4);
  });
});
