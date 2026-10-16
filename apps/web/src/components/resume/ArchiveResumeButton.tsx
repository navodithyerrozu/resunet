"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { buttonClass } from "@/components/ui/primitives";

export function ArchiveResumeButton({ resumeId }: { resumeId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  async function archive() {
    if (busy) return;
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch(`/api/resumes/${encodeURIComponent(resumeId)}`, {
        method: "DELETE",
      });
      if (response.ok) {
        startTransition(() => router.refresh());
        return;
      }
      const body = (await response.json().catch(() => null)) as {
        detail?: unknown;
        error?: { message?: unknown };
      } | null;
      const detail = typeof body?.detail === "string" ? body.detail : body?.error?.message;
      setMessage(typeof detail === "string" ? detail : "Could not archive this resume. Try again.");
    } catch {
      setMessage("Could not reach the server. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <button type="button" onClick={() => void archive()} disabled={busy} className={buttonClass("quiet")}>
        {busy ? "Archiving…" : "Archive"}
      </button>
      {message !== null ? (
        <p role="alert" className="max-w-sm text-right text-xs text-danger">
          {message}
        </p>
      ) : null}
    </div>
  );
}
