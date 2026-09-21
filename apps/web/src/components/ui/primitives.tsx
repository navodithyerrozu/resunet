import type { ReactNode } from "react";

/**
 * Shared presentational primitives.
 *
 * All of these are plain server-renderable functions with no hooks and no event
 * handlers, so a page built from them stays a server component and ships no
 * JavaScript. Anything needing interactivity gets its own `"use client"` file
 * rather than turning these into client components -- that boundary is worth
 * more than the convenience of one shared Button.
 */

type CardProps = {
  children: ReactNode;
  /** Renders as the card's heading. Omit for an unlabelled surface. */
  title?: string;
  /** Small uppercase label above the title. Should type the section, not decorate it. */
  eyebrow?: string;
  /** Right-aligned slot in the header, for a count or a link. */
  aside?: ReactNode;
  className?: string;
};

export function Card({ children, title, eyebrow, aside, className = "" }: CardProps) {
  const hasHeader = title !== undefined || eyebrow !== undefined || aside !== undefined;
  return (
    <section
      className={`rounded-card border border-line bg-surface ${className}`}
    >
      {hasHeader ? (
        <header className="flex items-baseline justify-between gap-4 border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            {eyebrow !== undefined ? <p className="eyebrow mb-1">{eyebrow}</p> : null}
            {title !== undefined ? (
              <h2 className="truncate text-[0.9375rem] font-600 tracking-[-0.01em]">{title}</h2>
            ) : null}
          </div>
          {aside !== undefined ? <div className="shrink-0 text-sm text-muted">{aside}</div> : null}
        </header>
      ) : null}
      {children}
    </section>
  );
}

type ButtonVariant = "primary" | "secondary" | "quiet";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-accent text-white hover:bg-accent-hover",
  secondary: "border border-line bg-surface text-ink hover:border-line-strong",
  quiet: "text-muted hover:text-ink",
};

/**
 * Button styling as a class string rather than a component.
 *
 * A `<Button onClick={...}>` would have to be a client component, and every
 * server page that imported it for a plain link would pull the boundary along
 * with it. A function returning classes is usable from both sides of the divide
 * and applies equally to `<button>`, `<a>`, and next/link.
 */
export function buttonClass(variant: ButtonVariant = "secondary", extra = ""): string {
  return [
    "inline-flex items-center justify-center gap-1.5 rounded-card px-3 py-1.5",
    "text-sm font-500 transition-colors disabled:cursor-not-allowed disabled:opacity-60",
    BUTTON_VARIANTS[variant],
    extra,
  ]
    .filter(Boolean)
    .join(" ");
}

export type ChipTone = "neutral" | "ok" | "warn" | "danger" | "accent";

const CHIP_TONES: Record<ChipTone, string> = {
  neutral: "border-line bg-sunken text-muted",
  ok: "border-ok/25 bg-ok-wash text-ok",
  warn: "border-warn/25 bg-warn-wash text-warn",
  danger: "border-danger/25 bg-danger-wash text-danger",
  accent: "border-accent/25 bg-role-y-wash text-accent",
};

/**
 * A small status label.
 *
 * Tone is passed explicitly rather than derived from the text, so the same
 * word can read as neutral in one context and as a problem in another --
 * "queued" is fine on a render job and worth noticing on an application that
 * has been queued for a day.
 */
export function Chip({
  children,
  tone = "neutral",
  mono = false,
}: {
  children: ReactNode;
  tone?: ChipTone;
  mono?: boolean;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-chip border px-1.5 py-0.5 text-[0.6875rem] leading-4 font-500 ${
        CHIP_TONES[tone]
      } ${mono ? "font-mono tabular-nums" : ""}`}
    >
      {children}
    </span>
  );
}

/**
 * A horizontal score bar.
 *
 * `max` defaults to 100 to match ATS scores. The label is always rendered as a
 * number next to the bar: a bar alone communicates "roughly this much", and
 * users acting on an ATS score need the actual figure.
 */
export function Meter({
  value,
  max = 100,
  label,
}: {
  value: number;
  max?: number;
  label?: string;
}) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const tone = pct >= 75 ? "bg-ok" : pct >= 50 ? "bg-warn" : "bg-danger";
  return (
    <div className="flex items-center gap-2.5">
      <div
        className="h-1.5 flex-1 overflow-hidden rounded-full bg-sunken"
        role="meter"
        aria-valuenow={Math.round(value)}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-label={label ?? "Score"}
      >
        <div className={`h-full rounded-full ${tone}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-xs tabular-nums text-muted" data-numeric>
        {Math.round(value)}
      </span>
    </div>
  );
}

/**
 * An empty state.
 *
 * Always carries an action. An empty screen that only explains why it is empty
 * leaves the user to work out what to do next, which is the one thing the
 * interface is there for.
 */
export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <div className="px-5 py-12 text-center">
      <p className="text-sm font-600">{title}</p>
      <p className="mx-auto mt-1.5 max-w-sm text-sm text-muted">{body}</p>
      {action !== undefined ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

/** A labelled figure. Used in the dashboard header strip. */
export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div>
      <p className="eyebrow">{label}</p>
      <p className="mt-1 font-mono text-2xl leading-none tabular-nums" data-numeric>
        {value}
      </p>
      {hint !== undefined ? <p className="mt-1.5 text-xs text-faint">{hint}</p> : null}
    </div>
  );
}
