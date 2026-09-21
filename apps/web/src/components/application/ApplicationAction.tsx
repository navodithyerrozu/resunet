"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { buttonClass } from "@/components/ui/primitives";

/**
 * Approve or retry a submission.
 *
 * Approving is irreversible: the automation worker replays the form and clicks
 * submit, and there is no unsend. So the button arms first and fires second. A
 * confirm dialog would be the conventional answer, but the two-state button
 * keeps the action in place, keeps the job title visible next to it, and cannot
 * be dismissed by a stray Escape while the user thinks they cancelled.
 *
 * The armed state resets on blur so a half-pressed button does not sit there
 * looking dangerous after the user has moved on.
 */

type Action = "approve" | "retry";

const COPY: Record<Action, { idle: string; armed: string; busy: string }> = {
  approve: { idle: "Approve and submit", armed: "Confirm submit", busy: "Submitting…" },
  retry: { idle: "Retry", armed: "Confirm retry", busy: "Queueing…" },
};

export function ApplicationAction({
  applicationId,
  action,
}: {
  applicationId: string;
  action: Action;
}) {
  const router = useRouter();
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  const copy = COPY[action];

  async function fire() {
    if (!armed) {
      setArmed(true);
      setMessage(null);
      return;
    }

    setBusy(true);
    setArmed(false);
    setMessage(null);

    try {
      const response = await fetch(`/api/applications/${encodeURIComponent(applicationId)}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action }),
      });

      if (response.ok) {
        // The route handler already revalidated the `applications` tag; this
        // pulls the fresh server render into the current view.
        startTransition(() => router.refresh());
        return;
      }

      const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
      setMessage(
        typeof body?.detail === "string" ? body.detail : "That did not go through. Try again.",
      );

      // 504 is the dangerous one: the submission may already be in flight. The
      // button stays disabled so a second click cannot file a duplicate
      // application, and the copy tells the user to look rather than to act.
      if (response.status === 504) return;
    } catch {
      setMessage("Could not reach ResuNet. Nothing was submitted.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={() => void fire()}
        onBlur={() => setArmed(false)}
        disabled={busy}
        aria-live="polite"
        className={buttonClass(armed ? "primary" : "secondary")}
      >
        {busy ? copy.busy : armed ? copy.armed : copy.idle}
      </button>
      {message !== null ? (
        <p role="alert" className="max-w-[18rem] text-right text-xs text-danger">
          {message}
        </p>
      ) : null}
    </div>
  );
}
