import { absoluteDateTime, relativeTime } from "@/lib/format";

/**
 * A timestamp.
 *
 * Renders relative text with the absolute value in `title` and the raw ISO
 * string in `dateTime`, so the fast-scanning case ("2h ago") and the precise
 * case (hovering, or copying into a bug report) are both served.
 *
 * `suppressHydrationWarning` is deliberate and narrow: the server and the client
 * compute "how long ago" against different clocks, so the text legitimately
 * differs by the round-trip time. See `lib/format.ts`.
 */
export function Timestamp({
  iso,
  className = "",
}: {
  iso: string | null | undefined;
  className?: string;
}) {
  if (iso === null || iso === undefined) {
    return <span className={`text-faint ${className}`}>--</span>;
  }

  return (
    <time
      dateTime={iso}
      title={absoluteDateTime(iso)}
      suppressHydrationWarning
      className={className}
    >
      {relativeTime(iso)}
    </time>
  );
}
