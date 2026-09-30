import { useEffect, useState } from "react";
import { Check, Crosshair, Pause, Play, Power, RotateCcw, Square, Undo2 } from "lucide-react";
import type { Command, SessionState } from "../lib/types";
import { Button, Kbd, cx } from "./ui";

const STEPS: Array<{ label: string; states: SessionState[] }> = [
  { label: "Open source", states: ["created"] },
  { label: "Select target", states: ["previewing"] },
  { label: "Observe", states: ["target_selected", "running", "paused", "stopping"] },
  { label: "Review", states: ["completed", "failed"] },
];

interface Props {
  state: SessionState | null;
  pending: string | null;
  onCommand: (command: Command) => void;
  onRestart: () => void;
  onLeave: () => void;
}

export function Controls({ state, pending, onCommand, onRestart, onLeave }: Props) {
  const [confirmStop, setConfirmStop] = useState(false);
  const current = STEPS.findIndex((step) => state !== null && step.states.includes(state));

  useEffect(() => {
    if (!confirmStop) return;
    const timer = setTimeout(() => setConfirmStop(false), 3500);
    return () => clearTimeout(timer);
  }, [confirmStop]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.code !== "Space" || target?.closest("button, input, select, textarea")) return;
      if (state === "running") onCommand("pause");
      else if (state === "paused") onCommand("resume");
      else return;
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state, onCommand]);

  const busy = (command: Command) => pending === command;
  const stop = () => {
    if (!confirmStop) return setConfirmStop(true);
    setConfirmStop(false);
    onCommand("stop");
  };

  return (
    <div className="flex flex-col gap-4 p-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
      <ol className="flex items-center gap-1.5" aria-label="Session progress">
        {STEPS.map((step, index) => {
          const done = index < current;
          const active = index === current;
          return (
            <li key={step.label} className="flex items-center gap-1.5">
              <span
                className={cx(
                  "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium transition-colors",
                  active && "bg-accent-soft text-accent",
                  done && "text-muted",
                  !active && !done && "text-faint",
                )}
                aria-current={active ? "step" : undefined}
              >
                <span
                  className={cx(
                    "grid size-4 place-items-center rounded-full text-[10px]",
                    active ? "bg-accent text-accent-ink" : done ? "bg-surface-2 text-muted" : "border border-line-strong",
                  )}
                >
                  {done ? <Check className="size-2.5" aria-hidden /> : index + 1}
                </span>
                <span className="hidden md:inline">{step.label}</span>
              </span>
              {index < STEPS.length - 1 && <span className="h-px w-3 bg-line-strong md:w-5" aria-hidden />}
            </li>
          );
        })}
      </ol>

      <div className="flex flex-wrap items-center gap-2">
        {state === "created" && (
          <Button variant="primary" icon={<Power className="size-4" />} loading={busy("open")} onClick={() => onCommand("open")}>
            Open source
          </Button>
        )}
        {state === "previewing" && (
          <Button variant="primary" icon={<Crosshair className="size-4" />} loading={busy("select_target")} onClick={() => onCommand("select_target")}>
            Lock target
          </Button>
        )}
        {state === "target_selected" && (
          <Button variant="primary" icon={<Play className="size-4" />} loading={busy("start")} onClick={() => onCommand("start")}>
            Start session
          </Button>
        )}
        {state === "running" && (
          <Button icon={<Pause className="size-4" />} loading={busy("pause")} onClick={() => onCommand("pause")}>
            Pause <Kbd>Space</Kbd>
          </Button>
        )}
        {state === "paused" && (
          <Button variant="primary" icon={<Play className="size-4" />} loading={busy("resume")} onClick={() => onCommand("resume")}>
            Resume <Kbd>Space</Kbd>
          </Button>
        )}
        {(state === "running" || state === "paused") && (
          <Button
            variant="quiet-danger"
            icon={<Square className="size-3.5" />}
            loading={busy("stop")}
            onClick={stop}
            aria-live="polite"
          >
            {confirmStop ? "Confirm end session" : "End session"}
          </Button>
        )}
        {(state === "completed" || state === "failed") && (
          <>
            <Button icon={<RotateCcw className="size-4" />} onClick={onRestart}>
              Run again
            </Button>
            <Button variant="primary" icon={<Undo2 className="size-4" />} onClick={onLeave}>
              New session
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
