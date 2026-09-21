"use client";

import { useEffect } from "react";

/**
 * Error boundary for the authenticated shell.
 *
 * Two rules here, both learned the hard way in apps like this one:
 *
 * 1. Never render `error.message`. In production Next.js replaces it with a
 *    generic string anyway, and in development it can carry a backend URL, a
 *    query, or a fragment of a stack trace. `digest` is the safe handle -- it is
 *    the same id logged on the server, so a user can quote it in a bug report.
 * 2. Say what to do next. "Something went wrong" with no action is the most
 *    common dead end in a React app.
 */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Stand-in for the real reporter. Wire this to Sentry (or whatever ends up
    // being used) rather than leaving it as a console call in production.
    console.error("[resunet] unhandled render error", error.digest ?? error.name);
  }, [error]);

  return (
    <div className="mx-auto max-w-md py-20 text-center">
      <p className="eyebrow">Error</p>
      <h1 className="mt-2 text-title">This screen did not load</h1>
      <p className="mt-3 text-sm text-muted">
        The API did not return what the page expected. Nothing was changed, so retrying is safe.
      </p>
      <div className="mt-6 flex items-center justify-center gap-3">
        <button
          type="button"
          onClick={reset}
          className="rounded-card bg-accent px-3 py-1.5 text-sm font-500 text-white transition-colors hover:bg-accent-hover"
        >
          Try again
        </button>
        <a href="/dashboard" className="text-sm text-muted underline hover:text-ink">
          Back to dashboard
        </a>
      </div>
      {error.digest !== undefined ? (
        <p className="mt-8 font-mono text-xs text-faint">Reference {error.digest}</p>
      ) : null}
    </div>
  );
}
