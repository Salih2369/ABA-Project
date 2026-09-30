import { FileClock, FlaskConical } from "lucide-react";
import type { ProviderKind } from "../lib/types";
import type { Connection } from "../lib/reducer";
import { cx } from "./ui";

const CONNECTION: Record<Connection, { label: string; dot: string }> = {
  idle: { label: "No session", dot: "bg-faint" },
  connecting: { label: "Connecting", dot: "bg-attention animate-pulse" },
  live: { label: "Stream connected", dot: "bg-positive" },
  reconnecting: { label: "Reconnecting", dot: "bg-attention animate-pulse" },
  closed: { label: "Stream closed", dot: "bg-faint" },
};

export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <rect width="32" height="32" rx="9" fill="var(--accent)" />
      <circle cx="16" cy="16" r="6.5" fill="none" stroke="var(--accent-ink)" strokeWidth="2.4" />
      <circle cx="16" cy="16" r="2" fill="#f2c979" />
    </svg>
  );
}

export function TopBar({ connection, sessionId, onHome, providerKind }: {
  connection: Connection;
  sessionId: string | null;
  onHome: () => void;
  providerKind: ProviderKind | null;
}) {
  const replay = providerKind === "precomputed";
  const status = CONNECTION[connection];
  const brand = (
    <>
      <BrandMark className="size-9" />
      <div className="text-left leading-tight">
        <p className="text-[15px] font-semibold tracking-tight">ABA Visual Assistant</p>
        <p className="text-xs text-muted">Live session · engineering preview</p>
      </div>
    </>
  );
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-bg/80 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-[1440px] items-center gap-4 px-5 lg:px-8">
        {sessionId ? (
          <button
            onClick={onHome}
            className="-ml-2 flex items-center gap-3 rounded-xl px-2 py-1 transition-colors hover:bg-surface-2"
            aria-label="Back to all scenarios"
          >
            {brand}
          </button>
        ) : (
          <div className="flex items-center gap-3">{brand}</div>
        )}

        <span
          className="ml-2 hidden items-center gap-1.5 rounded-full border border-attention-line bg-attention-soft px-3 py-1 text-xs font-semibold tracking-wide text-attention sm:inline-flex"
          title={
            replay
              ? "Earlier precomputed rows replayed against the matching video. Not live inference; nothing is recorded."
              : "Synthetic source and observations. No camera, no model inference, nothing is recorded."
          }
        >
          {replay ? <FileClock className="size-3.5" aria-hidden /> : <FlaskConical className="size-3.5" aria-hidden />}
          {replay ? "PRECOMPUTED REPLAY" : "SIMULATION"}
        </span>

        <div className="ml-auto flex items-center gap-4 text-xs text-muted">
          {sessionId && <span className="hidden font-mono md:inline">{sessionId}</span>}
          <span className="inline-flex items-center gap-2" role="status" aria-live="polite">
            <span className={cx("size-2 rounded-full", status.dot)} aria-hidden />
            {status.label}
          </span>
        </div>
      </div>
    </header>
  );
}
