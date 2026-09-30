import { AnimatePresence, motion } from "motion/react";
import {
  CircleAlert,
  CircleCheck,
  CircleDot,
  Eye,
  EyeOff,
  Flag,
  Layers,
  MessageSquareDashed,
  Repeat,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { ACTIVITY_LABEL, INDICATOR_LABEL, formatClock } from "../lib/format";
import type { LiveState, TimelineItem, Tone } from "../lib/reducer";
import type { Activity, EngineEvent, SessionSnapshot } from "../lib/types";
import { Button, Card, SectionTitle, cx } from "./ui";

export function CandidateCard({ alert, onAcknowledge, unavailable }: {
  alert: EngineEvent | null;
  onAcknowledge: (id: number) => void;
  unavailable: boolean;
}) {
  return (
    <Card aria-labelledby="candidate-title" className="overflow-hidden">
      <SectionTitle id="candidate-title">Candidate observation</SectionTitle>
      <div aria-live="polite" className="px-5 pb-5">
        <AnimatePresence mode="wait" initial={false}>
          {alert && !unavailable ? (
            <motion.div
              key={alert.id}
              initial={{ opacity: 0, y: 10, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ type: "spring", stiffness: 260, damping: 24 }}
              className="rounded-xl border border-attention-line bg-attention-soft p-4"
            >
              <div className="flex items-start gap-3">
                <span className="relative mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-attention text-surface">
                  <Flag className="size-4" aria-hidden />
                  {alert.end === null && (
                    <span className="absolute -top-0.5 -right-0.5 size-2.5 animate-ping rounded-full bg-attention" aria-hidden />
                  )}
                </span>
                <div className="min-w-0">
                  <p className="text-lg font-semibold leading-tight tracking-tight text-ink">{INDICATOR_LABEL[alert.type]}</p>
                  <p className="text-sm text-attention">
                    {alert.end === null ? "Sustained · ongoing" : `Ended at ${formatClock(alert.end)}`}
                  </p>
                </div>
              </div>
              <dl className="mt-4 grid grid-cols-2 gap-3 rounded-lg bg-surface/60 p-3 font-mono text-xs tabular">
                <div>
                  <dt className="font-sans text-[11px] text-muted">Onset</dt>
                  <dd className="text-ink">{formatClock(alert.start)}</dd>
                </div>
                <div>
                  <dt className="font-sans text-[11px] text-muted">Evidence</dt>
                  <dd className="text-ink">{formatClock(alert.evidence_time)}</dd>
                </div>
              </dl>
              <p className="mt-3 text-xs leading-relaxed text-muted">
                The observed signal persisted past the provisional threshold. It is a prompt for your review, not a
                conclusion about attention, intent, or behavior function.
              </p>
              <Button variant="secondary" className="mt-4 w-full" onClick={() => onAcknowledge(alert.id)}>
                Acknowledge
              </Button>
            </motion.div>
          ) : (
            <motion.div
              key="empty"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex items-center gap-3 rounded-xl border border-dashed border-line-strong p-4 text-sm text-muted"
            >
              <MessageSquareDashed className="size-5 shrink-0 text-faint" aria-hidden />
              {unavailable
                ? "Unavailable while the stream, identity or analysis is not current."
                : "Nothing needs your attention right now."}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </Card>
  );
}

export function SessionFacts({ live, snapshot }: { live: LiveState; snapshot: SessionSnapshot | null }) {
  const facts: Array<[string, React.ReactNode]> = [
    ["Scenario", snapshot?.scenario.title ?? "—"],
    [
      "Identity",
      live.identity === null ? (
        <span className="text-faint">Not selected</span>
      ) : (
        <span className={cx("inline-flex items-center gap-1.5", live.identity === "confirmed" ? "text-positive" : "text-attention")}>
          {live.identity === "confirmed" ? <Eye className="size-3.5" aria-hidden /> : <EyeOff className="size-3.5" aria-hidden />}
          {live.identity === "confirmed" ? "Confirmed" : "Uncertain"}
        </span>
      ),
    ],
    ["Observations", snapshot?.scenario.kind === "precomputed" ? "Precomputed (not live)" : "Synthetic"],
    [
      "Recorder",
      <span className="text-muted" title="Simulated frame ledger: counts frames that reached the recorder. No pixels are stored.">
        Ledger · {live.recorder?.frames ?? 0} frames
        {live.recorder?.dropped_frames ? <span className="text-critical"> · {live.recorder.dropped_frames} dropped</span> : null}
      </span>,
    ],
    ["Candidates", live.alertCount],
    ["Analysis gaps", <span className={live.providerErrors ? "text-critical" : undefined}>{live.providerErrors}</span>],
    [
      "Analysis p95",
      live.performance ? (
        <span className={live.analysisStale ? "text-attention" : undefined}>{live.performance.p95.toFixed(0)} ms</span>
      ) : (
        <span className="text-faint">—</span>
      ),
    ],
    ["Late results", <span className={live.staleCount ? "text-attention" : undefined}>{live.staleCount}</span>],
  ];
  return (
    <Card aria-labelledby="facts-title">
      <SectionTitle id="facts-title">Session</SectionTitle>
      <dl className="divide-y divide-line px-5 pb-2 text-sm">
        {facts.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-3 py-2.5">
            <dt className="text-muted">{label}</dt>
            <dd className="text-right font-medium tabular">{value}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

const ACTIVITIES: Activity[] = ["table", "movement", "break"];

export function ActivitySwitch({ value, enabled, pending, onChange }: {
  value: Activity;
  enabled: boolean;
  pending: boolean;
  onChange: (activity: Activity) => void;
}) {
  return (
    <Card aria-labelledby="activity-title">
      <SectionTitle id="activity-title">Activity context</SectionTitle>
      <div className="px-5 pb-5">
        <div role="radiogroup" aria-labelledby="activity-title" className="grid grid-cols-3 gap-1 rounded-xl bg-surface-2 p-1">
          {ACTIVITIES.map((activity) => {
            const selected = value === activity;
            return (
              <button
                key={activity}
                role="radio"
                aria-checked={selected}
                disabled={!enabled || pending}
                onClick={() => !selected && onChange(activity)}
                className={cx(
                  "relative h-9 rounded-lg text-sm font-medium transition-colors disabled:opacity-50",
                  selected ? "text-ink" : "text-muted hover:text-ink",
                )}
              >
                {selected && (
                  <motion.span
                    layoutId="activity-pill"
                    className="absolute inset-0 rounded-lg bg-surface shadow-card ring-1 ring-line"
                    transition={{ type: "spring", stiffness: 400, damping: 32 }}
                  />
                )}
                <span className="relative">{ACTIVITY_LABEL[activity]}</span>
              </button>
            );
          })}
        </div>
        <p className="mt-2.5 text-xs leading-relaxed text-faint">
          Set by you. Movement makes orientation and seat not applicable; break suspends all candidates.
        </p>
      </div>
    </Card>
  );
}

const KIND_ICON: Record<TimelineItem["kind"], LucideIcon> = {
  state: CircleDot,
  identity: Eye,
  alert: Flag,
  error: CircleAlert,
  activity: Layers,
};

const TONE_STYLE: Record<Tone, string> = {
  neutral: "bg-surface-2 text-muted",
  attention: "bg-attention-soft text-attention",
  critical: "bg-critical-soft text-critical",
  positive: "bg-positive-soft text-positive",
};

export function Timeline({ items }: { items: TimelineItem[] }) {
  return (
    <Card aria-labelledby="timeline-title" className="flex min-h-0 flex-col">
      <SectionTitle id="timeline-title" action={<span className="text-xs text-faint tabular">{items.length}</span>}>
        Event log
      </SectionTitle>
      {items.length === 0 ? (
        <p className="flex items-center gap-2 px-5 pb-5 text-sm text-faint">
          <CircleCheck className="size-4" aria-hidden /> Events will appear here.
        </p>
      ) : (
        <ol className="scrollbar-thin max-h-[340px] overflow-y-auto px-3 pb-3">
          <AnimatePresence initial={false}>
            {items.map((item) => {
              const Icon = KIND_ICON[item.kind];
              return (
                <motion.li
                  key={item.key}
                  layout
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  className="overflow-hidden"
                >
                  <div className="flex items-start gap-3 rounded-lg px-2 py-2 hover:bg-surface-2">
                    <span className={cx("mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg", TONE_STYLE[item.tone])}>
                      <Icon className="size-3.5" aria-hidden />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="flex items-center gap-2 text-sm font-medium">
                        <span className="truncate">{item.title}</span>
                        {item.repeat > 1 && (
                          <span className="inline-flex items-center gap-0.5 rounded bg-surface-2 px-1 text-[10px] text-muted">
                            <Repeat className="size-2.5" aria-hidden />×{item.repeat}
                          </span>
                        )}
                      </p>
                      {item.detail && <p className="truncate text-xs text-muted">{item.detail}</p>}
                    </div>
                    <time className="shrink-0 pt-0.5 font-mono text-[11px] text-faint tabular">{formatClock(item.videoTime)}</time>
                  </div>
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ol>
      )}
    </Card>
  );
}
