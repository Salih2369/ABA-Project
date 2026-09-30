import type { ButtonHTMLAttributes, ReactNode } from "react";
import { LoaderCircle } from "lucide-react";
import type { Tone } from "../lib/reducer";

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "ghost" | "quiet-danger";

const VARIANT: Record<Variant, string> = {
  primary:
    "bg-accent text-accent-ink shadow-[0_1px_0_rgb(255_255_255/0.15)_inset,0_6px_16px_-8px_var(--accent)] hover:brightness-110",
  secondary: "bg-surface text-ink border border-line-strong hover:bg-surface-2",
  ghost: "text-muted hover:text-ink hover:bg-surface-2",
  "quiet-danger": "bg-surface text-critical border border-line-strong hover:bg-critical-soft hover:border-critical/40",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  icon?: ReactNode;
  loading?: boolean;
  size?: "md" | "lg";
}

export function Button({ variant = "secondary", icon, loading, size = "md", className, children, disabled, ...rest }: ButtonProps) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={cx(
        "inline-flex select-none items-center justify-center gap-2 rounded-xl font-medium transition-[background,filter,border-color,transform] duration-150 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-45",
        size === "lg" ? "h-12 px-5 text-[15px]" : "h-10 px-4 text-sm",
        VARIANT[variant],
        className,
      )}
    >
      {loading ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}

const TONE: Record<Tone, string> = {
  neutral: "bg-surface-2 text-muted border-line",
  attention: "bg-attention-soft text-attention border-attention-line",
  critical: "bg-critical-soft text-critical border-critical/30",
  positive: "bg-positive-soft text-positive border-positive/25",
};

export function Pill({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-medium",
        TONE[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Card({ children, className, as: Tag = "section", ...rest }: {
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "aside";
  "aria-label"?: string;
  "aria-labelledby"?: string;
}) {
  return (
    <Tag {...rest} className={cx("rounded-2xl border border-line bg-surface shadow-card", className)}>
      {children}
    </Tag>
  );
}

export function SectionTitle({ id, children, action }: { id?: string; children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 px-5 pt-4 pb-3">
      <h2 id={id} className="text-[13px] font-semibold uppercase tracking-[0.08em] text-muted">
        {children}
      </h2>
      {action}
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded-md border border-line-strong bg-surface-2 px-1.5 py-px font-mono text-[11px] text-muted">
      {children}
    </kbd>
  );
}
