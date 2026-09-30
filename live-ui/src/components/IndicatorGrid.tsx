import { motion } from "motion/react";
import { Activity, Armchair, Compass, Hand, PersonStanding } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { INDICATOR_BASIS, INDICATOR_LABEL, INDICATOR_STATE_LABEL, formatDuration } from "../lib/format";
import type { LiveState } from "../lib/reducer";
import { INDICATORS, type Indicator, type IndicatorSnapshot, type IndicatorState, type Signal } from "../lib/types";
import { cx } from "./ui";

const ICON: Record<Indicator, LucideIcon> = {
  orientation: Compass,
  body_motion: Activity,
  out_of_seat: Armchair,
  hand_motion: Hand,
  posture_change: PersonStanding,
};

/** Provisional engine defaults (seconds); posture pulses are confirmed upstream. */
const PERSISTENCE: Record<Indicator, number> = {
  orientation: 2,
  body_motion: 2,
  out_of_seat: 2,
  hand_motion: 2,
  posture_change: 0,
};

const STATE_STYLE: Record<IndicatorState, string> = {
  active: "border-attention-line bg-attention-soft",
  candidate: "border-attention-line/70 bg-surface",
  inactive: "border-line bg-surface",
  unobservable: "border-dashed border-line-strong bg-surface hatched",
  not_applicable: "border-line bg-surface-2 opacity-70",
};

const BADGE_STYLE: Record<IndicatorState, string> = {
  active: "bg-attention text-surface",
  candidate: "bg-attention-soft text-attention ring-1 ring-attention-line",
  inactive: "bg-surface-2 text-muted",
  unobservable: "bg-surface-2 text-faint ring-1 ring-line-strong",
  not_applicable: "bg-surface-2 text-faint",
};

export function IndicatorGrid({ live, stale }: { live: LiveState; stale: boolean }) {
  const elapsed = live.videoTime ?? 0;
  return (
    <section aria-labelledby="indicators-title">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="indicators-title" className="text-[13px] font-semibold uppercase tracking-[0.08em] text-muted">
          Observable indicators
        </h2>
        <p className="text-xs text-faint">Provisional thresholds · experimental proxies, not clinical labels</p>
      </div>
      <div className={cx("grid grid-cols-2 gap-3 transition-opacity md:grid-cols-3 xl:grid-cols-5", stale && "opacity-50")}>
        {INDICATORS.map((key) => (
          <Tile
            key={key}
            indicator={key}
            snapshot={live.engine?.indicators[key]}
            signal={live.observation?.signals[key]}
            elapsed={elapsed}
          />
        ))}
      </div>
    </section>
  );
}

function Tile({ indicator, snapshot, signal, elapsed }: {
  indicator: Indicator;
  snapshot: IndicatorSnapshot | undefined;
  signal: Signal | undefined;
  elapsed: number;
}) {
  const Icon = ICON[indicator];
  const state: IndicatorState = snapshot?.state ?? "unobservable";
  const threshold = PERSISTENCE[indicator];
  const progress = state === "active" ? 1 : threshold > 0 ? Math.min(1, (snapshot?.duration ?? 0) / threshold) : 0;
  const coverage = elapsed > 0 && snapshot ? Math.min(1, snapshot.observable_seconds / elapsed) : 0;

  return (
    <motion.article
      layout
      className={cx("relative flex min-h-[188px] flex-col rounded-2xl border p-4 transition-colors duration-300", STATE_STYLE[state])}
      aria-label={`${INDICATOR_LABEL[indicator]}: ${INDICATOR_STATE_LABEL[state]}`}
    >
      <div className="flex items-start justify-between gap-2">
        <span className={cx("grid size-9 place-items-center rounded-xl", state === "active" ? "bg-surface/70 text-attention" : "bg-surface-2 text-muted")}>
          <Icon className="size-[18px]" aria-hidden />
        </span>
        <SignalDot signal={snapshot ? signal : undefined} />
      </div>
      <h3 className="mt-3 font-semibold tracking-tight">{INDICATOR_LABEL[indicator]}</h3>
      <p className="text-[11px] leading-snug text-faint">{INDICATOR_BASIS[indicator]}</p>

      <div className="mt-auto pt-3">
        <span className={cx("inline-flex rounded-md px-2 py-0.5 text-[11px] font-semibold", BADGE_STYLE[state])}>
          {INDICATOR_STATE_LABEL[state]}
        </span>
        <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden>
          <motion.div
            className={cx("h-full rounded-full", state === "active" ? "bg-attention" : "bg-attention/60")}
            initial={false}
            animate={{ width: `${(state === "candidate" || state === "active" ? progress : 0) * 100}%` }}
            transition={{ duration: 0.25 }}
          />
        </div>
        <dl className="mt-2.5 grid grid-cols-2 gap-x-2 text-[11px] text-muted tabular">
          <div>
            <dt className="text-faint">Episodes</dt>
            <dd className="font-medium text-ink">{snapshot?.count ?? 0}</dd>
          </div>
          <div>
            <dt className="text-faint">Observable</dt>
            <dd className="font-medium text-ink" title={snapshot ? formatDuration(snapshot.observable_seconds) : undefined}>
              {Math.round(coverage * 100)}%
            </dd>
          </div>
        </dl>
      </div>
    </motion.article>
  );
}

function SignalDot({ signal }: { signal: Signal | undefined }) {
  const label = signal === true ? "present" : signal === false ? "absent" : "unobservable";
  return (
    <span className="inline-flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-faint" title={`Latest raw signal: ${label}`}>
      <span
        className={cx(
          "size-2 rounded-full",
          signal === true && "bg-attention",
          signal === false && "bg-positive/70",
          (signal === null || signal === undefined) && "border border-dashed border-faint",
        )}
        aria-hidden
      />
      {label}
    </span>
  );
}
