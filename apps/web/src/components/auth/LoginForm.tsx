"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";

/**
 * Sign-in form.
 *
 * Posts to this app's own `/api/session` handler, never to FastAPI directly. The
 * browser therefore never sees a token, and the API's origin never needs to be
 * exposed to the client or allowed through CORS.
 */

/**
 * Validate a post-login destination.
 *
 * SECURITY: this is open-redirect defence. `?next=` arrives from the URL bar,
 * which means an attacker can set it. A bare `startsWith("/")` check is not
 * enough -- `//evil.example` is a protocol-relative URL that browsers treat as
 * absolute, so it would send a freshly-authenticated user off-site. Anything
 * that is not a single-slash relative path is discarded rather than sanitized.
 */
function safeDestination(next: string | null): string {
  if (next === null) return "/dashboard";
  if (!next.startsWith("/") || next.startsWith("//")) return "/dashboard";
  if (next.includes("\\") || next.includes(":")) return "/dashboard";
  return next;
}

export function LoginForm({ next, expired }: { next: string | null; expired: boolean }) {
  const router = useRouter();
  const emailId = useId();
  const passwordId = useId();

  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;

    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "").trim();
    const password = String(form.get("password") ?? "");

    setPending(true);
    setError(null);

    try {
      const response = await fetch("/api/session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
      });

      if (response.status === 204) {
        const destination = safeDestination(next);
        router.replace(destination);
        // Server components above this point cached an anonymous render. Without
        // a refresh the shell would paint before the session is visible to it.
        router.refresh();
        return;
      }

      const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
      setError(
        typeof body?.detail === "string" ? body.detail : "Sign-in failed. Try again.",
      );
    } catch {
      // A network-level failure, not an API rejection -- worth distinguishing,
      // because "check your connection" and "check your password" send the user
      // to different places.
      setError("Could not reach ResuNet. Check your connection and try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4" noValidate>
      {expired ? (
        <p className="rounded-card border border-warn/25 bg-warn-wash px-3 py-2 text-sm text-warn">
          Your session expired. Sign in to pick up where you left off.
        </p>
      ) : null}

      <div>
        <label htmlFor={emailId} className="eyebrow block">
          Email
        </label>
        <input
          id={emailId}
          name="email"
          type="email"
          autoComplete="username"
          required
          disabled={pending}
          className="mt-1.5 w-full rounded-card border border-line bg-surface px-3 py-2 text-sm outline-none transition-colors focus:border-accent disabled:opacity-60"
        />
      </div>

      <div>
        <label htmlFor={passwordId} className="eyebrow block">
          Password
        </label>
        <input
          id={passwordId}
          name="password"
          type="password"
          autoComplete="current-password"
          required
          disabled={pending}
          className="mt-1.5 w-full rounded-card border border-line bg-surface px-3 py-2 text-sm outline-none transition-colors focus:border-accent disabled:opacity-60"
        />
      </div>

      {error !== null ? (
        // `role="alert"` so a screen reader hears the failure without the user
        // having to hunt for it after pressing a button.
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={pending}
        className="w-full rounded-card bg-accent px-3 py-2 text-sm font-500 text-white transition-colors hover:bg-accent-hover disabled:opacity-60"
      >
        {pending ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
