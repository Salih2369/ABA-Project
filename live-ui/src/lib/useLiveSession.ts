import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { api, ApiError, eventsUrl } from "./api";
import { initialState, reducer, TERMINAL } from "./reducer";
import type { Activity, Command, LiveEvent, StreamReady } from "./types";

const MAX_BACKOFF_MS = 5000;

export function useLiveSession() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [pending, setPending] = useState<Command | "create" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const lastSequence = useRef(0);
  const terminal = useRef(false);
  lastSequence.current = state.lastSequence;
  terminal.current = state.sessionState !== null && TERMINAL.includes(state.sessionState);

  useEffect(() => {
    if (!sessionId) return;
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let disposed = false;

    const connect = () => {
      dispatch({ type: "connection", status: attempt === 0 ? "connecting" : "reconnecting" });
      socket = new WebSocket(eventsUrl(sessionId, lastSequence.current));
      socket.onmessage = (message) => {
        const data = JSON.parse(message.data as string) as LiveEvent | StreamReady;
        if (data.event_type === "stream_ready") {
          attempt = 0;
          dispatch({ type: "stream_ready", ready: data });
        } else {
          dispatch({ type: "event", event: data });
        }
      };
      socket.onclose = (close) => {
        if (disposed) return;
        if (close.code === 4404 || close.code === 1008 || terminal.current) {
          dispatch({ type: "connection", status: "closed" });
          return;
        }
        dispatch({ type: "connection", status: "reconnecting" });
        const delay = Math.min(MAX_BACKOFF_MS, 400 * 2 ** attempt);
        attempt += 1;
        retry = setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      disposed = true;
      clearTimeout(retry);
      socket?.close();
    };
  }, [sessionId]);

  const run = useCallback(async <T,>(label: Command | "create", task: () => Promise<T>) => {
    setPending(label);
    setError(null);
    try {
      return await task();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The local live-session server is unreachable.");
      return undefined;
    } finally {
      setPending(null);
    }
  }, []);

  const startScenario = useCallback(
    async (scenarioId: string) => {
      const snapshot = await run("create", () => api.createSession(scenarioId));
      if (!snapshot) return;
      if (sessionId) api.discardSession(sessionId).catch(() => undefined);
      dispatch({ type: "reset" });
      dispatch({ type: "snapshot", snapshot });
      setSessionId(snapshot.session_id);
    },
    [run, sessionId],
  );

  const send = useCallback(
    async (command: Command, activity?: Activity) => {
      if (!sessionId) return;
      const snapshot = await run(command, () => api.command(sessionId, command, activity));
      if (snapshot) dispatch({ type: "snapshot", snapshot });
    },
    [run, sessionId],
  );

  const leave = useCallback(() => {
    // Best effort: end the session on the server so it never lingers or holds capacity.
    if (sessionId) api.discardSession(sessionId).catch(() => undefined);
    setSessionId(null);
    setError(null);
    dispatch({ type: "reset" });
  }, [sessionId]);

  const dismissAlert = useCallback((id: number) => dispatch({ type: "dismiss_alert", id }), []);

  return { state, sessionId, pending, error, clearError: () => setError(null), startScenario, send, leave, dismissAlert };
}

export type LiveSessionController = ReturnType<typeof useLiveSession>;
