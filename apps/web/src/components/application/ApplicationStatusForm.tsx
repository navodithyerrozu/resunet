"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { buttonClass } from "@/components/ui/primitives";

import type { ApplicationStatus } from "@resunet/shared";

const NEXT: Partial<Record<ApplicationStatus, ApplicationStatus[]>> = {
  draft: ["submitted", "withdrawn"],
  submitted: ["acknowledged", "screening", "interviewing", "offer", "rejected", "withdrawn", "ghosted"],
  acknowledged: ["screening", "interviewing", "offer", "rejected", "withdrawn", "ghosted"],
  screening: ["interviewing", "offer", "rejected", "withdrawn", "ghosted"],
  interviewing: ["offer", "rejected", "withdrawn", "ghosted"],
  offer: ["withdrawn"],
  failed: ["withdrawn"],
  blocked: ["withdrawn"],
  needs_review: ["withdrawn"],
};

const LABEL: Partial<Record<ApplicationStatus, string>> = {
  submitted: "Submitted", acknowledged: "Acknowledged", screening: "Screening",
  interviewing: "Interviewing", offer: "Offer", rejected: "Rejected",
  withdrawn: "Withdrawn", ghosted: "No response",
};

export function ApplicationStatusForm({
  applicationId,
  current,
}: {
  applicationId: string;
  current: ApplicationStatus;
}) {
  const router = useRouter();
  const options = NEXT[current] ?? [];
  const [status, setStatus] = useState<ApplicationStatus | "">(options[0] ?? "");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function save() {
    if (!status || busy) return;
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch(`/api/applications/${encodeURIComponent(applicationId)}/status`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ status }),
      });
      const body = (await response.json().catch(() => null)) as { error?: { message?: string }; detail?: string } | null;
      if (!response.ok) {
        setMessage(body?.error?.message ?? body?.detail ?? "Status could not be updated.");
        return;
      }
      setMessage("Tracker updated.");
      router.refresh();
    } catch {
      setMessage("Could not reach ResuNet. Refresh to check the tracker.");
    } finally {
      setBusy(false);
    }
  }

  if (options.length === 0) return null;
  return (
    <div className="flex flex-wrap items-end gap-2 border-t border-line pt-4">
      <label className="text-xs text-muted">
        Update stage
        <select value={status} onChange={(event) => setStatus(event.target.value as ApplicationStatus)} className="mt-1 block rounded-card border border-line bg-paper px-3 py-2 text-sm text-ink">
          {options.map((option) => <option key={option} value={option}>{LABEL[option]}</option>)}
        </select>
      </label>
      <button type="button" onClick={() => void save()} disabled={busy || !status} className={buttonClass("secondary")}>
        {busy ? "Saving…" : "Save stage"}
      </button>
      {message ? <p role="status" className="text-xs text-muted">{message}</p> : null}
    </div>
  );
}
