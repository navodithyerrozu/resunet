"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";

export function RegisterForm() {
  const router = useRouter();
  const nameId = useId();
  const emailId = useId();
  const passwordId = useId();

  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;

    const form = new FormData(event.currentTarget);
    const fullName = String(form.get("fullName") ?? "").trim();
    const email = String(form.get("email") ?? "").trim();
    const password = String(form.get("password") ?? "");

    setPending(true);
    setError(null);

    try {
      const response = await fetch("/api/session/register", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ fullName, email, password }),
      });

      if (response.status === 204) {
        router.replace("/dashboard");
        router.refresh();
        return;
      }

      const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
      setError(
        typeof body?.detail === "string" ? body.detail : "Could not create the account. Try again.",
      );
    } catch {
      setError("Could not reach ResuNet. Check your connection and try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4" noValidate>
      <div>
        <label htmlFor={nameId} className="eyebrow block">
          Full name
        </label>
        <input
          id={nameId}
          name="fullName"
          type="text"
          autoComplete="name"
          required
          minLength={1}
          disabled={pending}
          className="mt-1.5 w-full rounded-card border border-line bg-surface px-3 py-2 text-sm outline-none transition-colors focus:border-accent disabled:opacity-60"
        />
      </div>

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
          autoComplete="new-password"
          required
          minLength={12}
          disabled={pending}
          className="mt-1.5 w-full rounded-card border border-line bg-surface px-3 py-2 text-sm outline-none transition-colors focus:border-accent disabled:opacity-60"
        />
        <p className="mt-1.5 text-xs text-faint">At least 12 characters.</p>
      </div>

      {error !== null ? (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={pending}
        className="w-full rounded-card bg-accent px-3 py-2 text-sm font-500 text-white transition-colors hover:bg-accent-hover disabled:opacity-60"
      >
        {pending ? "Creating account…" : "Create account"}
      </button>
    </form>
  );
}
