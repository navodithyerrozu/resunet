/**
 * Date and number formatting.
 *
 * No `server-only` marker: these are pure functions and both server components
 * and client components need them.
 *
 * On hydration mismatch -- relative times are computed from `Date.now()`, which
 * differs between the server render and the client hydration by however long the
 * response took. Callers render them inside `<Timestamp>`, which sets
 * `suppressHydrationWarning` and carries the machine-readable value in
 * `dateTime`. The alternative, rendering absolute timestamps and upgrading them
 * in an effect, costs a client component on every row of every table to fix a
 * cosmetic warning.
 */

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "just now", "12m ago", "3h ago", "5d ago", then an absolute date. */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "unknown";

  const delta = now - then;
  // Future timestamps happen: a scheduled render, or clock skew between this
  // process and Mongo. "in 4m" is more honest than clamping to "just now".
  const ahead = delta < 0;
  const abs = Math.abs(delta);

  let body: string;
  if (abs < MINUTE) return ahead ? "in a moment" : "just now";
  else if (abs < HOUR) body = `${Math.floor(abs / MINUTE)}m`;
  else if (abs < DAY) body = `${Math.floor(abs / HOUR)}h`;
  else if (abs < 30 * DAY) body = `${Math.floor(abs / DAY)}d`;
  else return absoluteDate(iso);

  return ahead ? `in ${body}` : `${body} ago`;
}

/** A stable absolute rendering, used for tooltips and anything over a month old. */
export function absoluteDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "unknown";
  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function absoluteDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "unknown";
  return date.toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** How long a worker took, in the units a human would use to judge it. */
export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "--";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

/** Bytes as a rendered PDF size. */
export function fileSize(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "--";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}
