"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { buttonClass } from "@/components/ui/primitives";

import type { Bullet } from "@resunet/shared";

/**
 * Manual bullet editing, inline in the workspace.
 *
 * Editing is deliberately small: the three XYZ components as text fields,
 * Save / Cancel, nothing else. Saving PATCHes the bullet; the API
 * re-validates XYZ and resets the bullet to DRAFT, so the control then
 * offers Approve -- an edited bullet cannot reach a render until it is
 * re-approved, and approval itself re-checks XYZ server-side.
 */
export function EditBulletControl({
  resumeId,
  bullet,
}: {
  resumeId: string;
  bullet: Bullet;
}) {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const [editing, setEditing] = useState(false);
  const [action, setAction] = useState(bullet.action);
  const [metric, setMetric] = useState(bullet.metric);
  const [method, setMethod] = useState(bullet.method);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (bullet.id === null) return null;
  const bulletId = bullet.id;
  const needsApproval = bullet.status !== "approved";

  function readError(body: unknown): string {
    if (body !== null && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail?: unknown }).detail;
      if (typeof detail === "string") return detail;
    }
    return "Could not reach ResuNet.";
  }

  async function save() {
    setPending(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/resumes/${encodeURIComponent(resumeId)}/bullets/${encodeURIComponent(bulletId)}`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ action, metric, method }),
        },
      );
      if (!response.ok) {
        setError(readError(await response.json().catch(() => null)));
        return;
      }
      setEditing(false);
      startTransition(() => router.refresh());
    } catch {
      setError("Could not reach ResuNet.");
    } finally {
      setPending(false);
    }
  }

  async function approve() {
    setPending(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/resumes/${encodeURIComponent(resumeId)}/bullets/${encodeURIComponent(bulletId)}/approve`,
        { method: "POST" },
      );
      if (!response.ok) {
        setError(readError(await response.json().catch(() => null)));
        return;
      }
      startTransition(() => router.refresh());
    } catch {
      setError("Could not reach ResuNet.");
    } finally {
      setPending(false);
    }
  }

  if (!editing) {
    return (
      <div className="flex flex-wrap items-center gap-2 pt-2">
        <button
          type="button"
          disabled={pending}
          onClick={() => {
            setAction(bullet.action);
            setMetric(bullet.metric);
            setMethod(bullet.method);
            setEditing(true);
          }}
          className={buttonClass("secondary")}
        >
          Edit
        </button>
        {needsApproval ? (
          <>
            <button
              type="button"
              disabled={pending}
              onClick={() => void approve()}
              className={buttonClass("primary")}
            >
              {pending ? "Approving…" : "Approve"}
            </button>
            <span className="text-xs text-muted">
              Edited — needs re-approval before it can render.
            </span>
          </>
        ) : null}
        {error !== null ? (
          <p role="alert" className="text-xs text-danger">
            {error}
          </p>
        ) : null}
      </div>
    );
  }

  const fieldClass =
    "w-full rounded-card border border-line bg-paper px-2 py-1 text-sm outline-none focus:border-accent disabled:opacity-60";

  return (
    <div className="space-y-2 pt-2">
      <label className="block text-xs text-muted">
        X — what you did
        <textarea
          value={action}
          disabled={pending}
          rows={2}
          onChange={(event) => setAction(event.target.value)}
          className={`${fieldClass} mt-1`}
        />
      </label>
      <label className="block text-xs text-muted">
        Y — what it measured (must contain a number)
        <textarea
          value={metric}
          disabled={pending}
          rows={2}
          onChange={(event) => setMetric(event.target.value)}
          className={`${fieldClass} mt-1`}
        />
      </label>
      <label className="block text-xs text-muted">
        Z — how you did it
        <textarea
          value={method}
          disabled={pending}
          rows={2}
          onChange={(event) => setMethod(event.target.value)}
          className={`${fieldClass} mt-1`}
        />
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={pending}
          onClick={() => void save()}
          className={buttonClass("primary")}
        >
          {pending ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={() => setEditing(false)}
          className={buttonClass("secondary")}
        >
          Cancel
        </button>
        {error !== null ? (
          <p role="alert" className="text-xs text-danger">
            {error}
          </p>
        ) : null}
      </div>
    </div>
  );
}
