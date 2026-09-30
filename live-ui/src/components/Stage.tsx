import { AnimatePresence, motion } from "motion/react";
import { CirclePause, CircleSlash, MousePointerClick, ScanLine, VideoOff } from "lucide-react";
import type { LiveState } from "../lib/reducer";
import { formatClock } from "../lib/format";
import { cx } from "./ui";

interface Props {
  live: LiveState;
  stale: boolean;
  canSelect: boolean;
  onSelect: () => void;
}

const SEATED = { x: 800, y: 600 };
const STANDING = { x: 1110, y: 560 };
const SEATED_LEGS = "M -30 -20 L -80 20 L -80 110 M 30 -20 L 80 20 L 80 110";
const STANDING_LEGS = "M -30 -20 L -34 110 M 30 -20 L 34 110";

/**
 * Abstract, clearly synthetic scene. It visualizes scripted signals only:
 * there is no camera frame, pose, or detection behind it.
 */
export function Stage({ live, stale, canSelect, onSelect }: Props) {
  const state = live.sessionState;
  const signals = live.observation?.signals;
  const uncertain = live.identity === "uncertain";
  const selected = state !== null && !["created", "previewing"].includes(state);
  const standing = signals?.out_of_seat === true;
  const position = standing ? STANDING : SEATED;
  const sourceOpen = state !== null && state !== "created";
  const replay = live.snapshot?.scenario.kind === "precomputed";

  return (
    <div className="stage-grid relative aspect-video overflow-hidden rounded-t-2xl text-white">
      <svg viewBox="0 0 1600 900" className={cx("absolute inset-0 size-full transition-[filter,opacity] duration-500", stale && "opacity-50 grayscale")} role="img" aria-label="Synthetic scene visualization">
        {/* Task area and seat zone */}
        <g opacity={sourceOpen ? 1 : 0.35} className="transition-opacity duration-500">
          <rect x="190" y="560" width="360" height="170" rx="18" fill="rgb(255 255 255 / 0.04)" stroke="rgb(255 255 255 / 0.14)" />
          <text x="210" y="600" fill="rgb(255 255 255 / 0.45)" fontSize="22" fontFamily="var(--font-mono)" letterSpacing="2">TASK AREA</text>
          <rect x="650" y="420" width="300" height="360" rx="22" fill="none" stroke="rgb(255 255 255 / 0.22)" strokeDasharray="10 10" />
          <text x="670" y="460" fill="rgb(255 255 255 / 0.45)" fontSize="22" fontFamily="var(--font-mono)" letterSpacing="2">SEAT</text>
        </g>

        {sourceOpen && (
          <motion.g
            initial={false}
            animate={{ x: position.x, y: position.y, opacity: uncertain ? 0.14 : 1 }}
            transition={{ type: "spring", stiffness: 70, damping: 16 }}
            style={{ cursor: canSelect ? "pointer" : "default" }}
            onClick={canSelect ? onSelect : undefined}
          >
            <Figure
              standing={standing}
              moving={signals?.body_motion === true}
              handsMoving={signals?.hand_motion === true}
              turned={signals?.orientation === true}
              highlight={canSelect}
            />
            <SelectionFrame visible={selected} uncertain={uncertain} standing={standing} />
          </motion.g>
        )}
      </svg>

      {/* HUD */}
      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-3 p-4">
        <span className="inline-flex items-center gap-2 rounded-lg bg-black/45 px-2.5 py-1.5 font-mono text-[11px] tracking-wider text-white/80 backdrop-blur">
          <ScanLine className="size-3.5" aria-hidden />
          {replay ? "FILE-AS-LIVE · PRECOMPUTED ROWS · VIDEO NOT SHOWN" : "SYNTHETIC SOURCE · NO VIDEO"}
        </span>
        {stale && sourceOpen ? (
          <span className="inline-flex items-center gap-2 rounded-lg bg-white/10 px-2.5 py-1.5 font-mono text-[11px] tracking-wider text-white/80 backdrop-blur">
            {state === "completed" || state === "failed" ? "SESSION ENDED · LAST KNOWN STATE" : "STALE · LAST KNOWN STATE"}
          </span>
        ) : selected && (
          <span
            className={cx(
              "inline-flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs font-medium backdrop-blur",
              uncertain ? "bg-amber-400/20 text-amber-200" : "bg-emerald-400/15 text-emerald-200",
            )}
          >
            <span className={cx("size-1.5 rounded-full", uncertain ? "bg-amber-300" : "bg-emerald-300")} aria-hidden />
            {uncertain ? "Identity uncertain" : live.identity === "confirmed" ? "Target confirmed" : "Target locked"}
          </span>
        )}
      </div>

      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between gap-3 bg-gradient-to-t from-black/60 to-transparent p-4 pt-12">
        <div>
          <p className="text-[11px] uppercase tracking-[0.18em] text-white/50">Video time</p>
          <p className="font-mono text-3xl font-medium tracking-tight tabular">{formatClock(live.videoTime)}</p>
        </div>
        <div className="text-right font-mono text-[11px] leading-relaxed text-white/55 tabular">
          <p>observations {live.observationCount}</p>
          <p>analysis {live.latencyMs == null ? "—" : `${live.latencyMs.toFixed(1)} ms`}</p>
          <p className={cx(live.analysisStale && "font-semibold text-amber-300")}>
            {live.ageS == null
              ? "result age —"
              : live.analysisStale
                ? `LATE · ${live.ageS.toFixed(1)} s behind`
                : `result age ${live.ageS.toFixed(2)} s`}
          </p>
        </div>
      </div>

      <AnimatePresence>
        {uncertain && selected && state === "running" && (
          <Overlay key="uncertain" tone="amber" icon={<CircleSlash className="size-5" aria-hidden />}>
            Target not visible — all indicators suppressed until identity is confirmed
          </Overlay>
        )}
        {state === "created" && (
          <Overlay key="closed" icon={<VideoOff className="size-5" aria-hidden />}>
            Source not opened
          </Overlay>
        )}
        {state === "previewing" && (
          <Overlay key="select" tone="accent" icon={<MousePointerClick className="size-5" aria-hidden />} position="top">
            Click the child you will observe to lock the target
          </Overlay>
        )}
        {state === "paused" && (
          <Overlay key="paused" icon={<CirclePause className="size-5" aria-hidden />}>
            Paused — session clock and analysis halted
          </Overlay>
        )}
      </AnimatePresence>
    </div>
  );
}

function Overlay({ children, icon, tone = "neutral", position = "center" }: {
  children: React.ReactNode;
  icon: React.ReactNode;
  tone?: "neutral" | "amber" | "accent";
  position?: "center" | "top";
}) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.97 }}
      transition={{ duration: 0.2 }}
      className={cx(
        "pointer-events-none absolute inset-x-0 flex justify-center px-6",
        position === "top" ? "top-16" : "top-1/2 -translate-y-1/2",
      )}
      role="status"
    >
      <span
        className={cx(
          "inline-flex items-center gap-2.5 rounded-xl px-4 py-2.5 text-sm font-medium shadow-lg backdrop-blur-md",
          tone === "amber" && "bg-amber-950/70 text-amber-100 ring-1 ring-amber-300/30",
          tone === "accent" && "bg-teal-950/70 text-teal-50 ring-1 ring-teal-300/30",
          tone === "neutral" && "bg-black/55 text-white/90 ring-1 ring-white/15",
        )}
      >
        {icon}
        {children}
      </span>
    </motion.div>
  );
}

function Figure({ standing, moving, handsMoving, turned, highlight }: {
  standing: boolean;
  moving: boolean;
  handsMoving: boolean;
  turned: boolean;
  highlight: boolean;
}) {
  const torso = standing ? 190 : 140;
  const stroke = "rgb(233 244 240 / 0.85)";
  return (
    <motion.g
      animate={moving ? { rotate: [-2.5, 2.5, -2.5] } : { rotate: 0 }}
      transition={moving ? { duration: 0.5, repeat: Infinity, ease: "easeInOut" } : { duration: 0.3 }}
    >
      {highlight && (
        <motion.circle
          r="190"
          cy={-60}
          fill="none"
          stroke="var(--accent)"
          strokeWidth="3"
          animate={{ opacity: [0.2, 0.7, 0.2], scale: [0.96, 1.03, 0.96] }}
          transition={{ duration: 1.8, repeat: Infinity }}
        />
      )}
      <circle r="190" cy={-60} fill="transparent" />
      <motion.circle
        cy={-torso - 70}
        r="40"
        cx={0}
        fill="rgb(233 244 240 / 0.12)"
        stroke={stroke}
        strokeWidth="4"
        animate={{ cx: turned ? -22 : 0 }}
      />
      <motion.line
        y1={-torso - 70}
        y2={-torso - 70}
        stroke="#f2c979"
        strokeWidth="4"
        strokeLinecap="round"
        x1={0}
        x2={0}
        animate={{ x1: turned ? -40 : 0, x2: turned ? -78 : 0, opacity: turned ? 1 : 0 }}
      />
      <motion.rect
        x="-58"
        width="116"
        rx="44"
        y={-torso - 20}
        height={torso}
        fill="rgb(233 244 240 / 0.1)"
        stroke={stroke}
        strokeWidth="4"
        animate={{ y: -torso - 20, height: torso }}
      />
      {[-1, 1].map((side) => (
        <motion.circle
          key={side}
          cx={side * 92}
          r="14"
          cy={-torso * 0.4}
          fill={handsMoving ? "#f2c979" : "rgb(233 244 240 / 0.7)"}
          animate={
            handsMoving
              ? { cy: [-torso * 0.45, -torso * 0.6, -torso * 0.45] }
              : { cy: -torso * 0.4 }
          }
          transition={handsMoving ? { duration: 0.35, repeat: Infinity, delay: side > 0 ? 0.15 : 0 } : undefined}
        />
      ))}
      <motion.path
        fill="none"
        stroke={stroke}
        strokeWidth="4"
        strokeLinecap="round"
        d={standing ? STANDING_LEGS : SEATED_LEGS}
        animate={{ d: standing ? STANDING_LEGS : SEATED_LEGS }}
      />
    </motion.g>
  );
}

function SelectionFrame({ visible, uncertain, standing }: { visible: boolean; uncertain: boolean; standing: boolean }) {
  if (!visible) return null;
  const top = standing ? -330 : -280;
  const w = 150;
  const bottom = 130;
  const c = 36;
  const color = uncertain ? "#f2c979" : "#5fc3b2";
  const corners = [
    `M ${-w} ${top + c} L ${-w} ${top} L ${-w + c} ${top}`,
    `M ${w - c} ${top} L ${w} ${top} L ${w} ${top + c}`,
    `M ${w} ${bottom - c} L ${w} ${bottom} L ${w - c} ${bottom}`,
    `M ${-w + c} ${bottom} L ${-w} ${bottom} L ${-w} ${bottom - c}`,
  ];
  return (
    <motion.g initial={{ opacity: 0, scale: 1.15 }} animate={{ opacity: 1, scale: 1 }} transition={{ duration: 0.35 }}>
      {corners.map((d) => (
        <path key={d} d={d} fill="none" stroke={color} strokeWidth="5" strokeLinecap="round" strokeDasharray={uncertain ? "8 10" : undefined} />
      ))}
      <rect x={-w} y={top - 52} width="128" height="36" rx="8" fill={color} />
      <text x={-w + 14} y={top - 27} fontSize="20" fontWeight="600" fill="#06231f" fontFamily="var(--font-mono)">
        TARGET
      </text>
    </motion.g>
  );
}
