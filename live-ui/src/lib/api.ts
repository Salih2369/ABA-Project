import type { Activity, Command, Scenario, SessionSnapshot } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body?.error === "string" ? body.error : `Request failed (${response.status})`;
    throw new ApiError(response.status, detail);
  }
  return body as T;
}

export const api = {
  health: () => request<{ status: string; mode: string }>("/api/live/health"),
  scenarios: () => request<{ scenarios: Scenario[] }>("/api/live/scenarios").then((r) => r.scenarios),
  createSession: (scenario: string) =>
    request<SessionSnapshot>("/api/live/sessions", { method: "POST", body: JSON.stringify({ scenario }) }),
  discardSession: (sessionId: string) =>
    fetch(`/api/live/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }).then(() => undefined),
  command: (sessionId: string, command: Command, activity?: Activity) =>
    request<SessionSnapshot>(`/api/live/sessions/${encodeURIComponent(sessionId)}/commands`, {
      method: "POST",
      body: JSON.stringify(activity ? { command, activity } : { command }),
    }),
};

export function eventsUrl(sessionId: string, after: number): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/live/sessions/${encodeURIComponent(sessionId)}/events?after=${after}`;
}
