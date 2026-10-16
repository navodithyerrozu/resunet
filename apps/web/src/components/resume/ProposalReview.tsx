"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { buttonClass } from "@/components/ui/primitives";

import type { BulletProposal } from "@resunet/shared";

export function ProposalReview({
  resumeId,
  threadId,
  proposal,
}: {
  resumeId: string;
  threadId: string;
  proposal: BulletProposal;
}) {
  const router = useRouter();
  const [action, setAction] = useState<"accept" | "reject" | "revise" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [draft, setDraft] = useState({
    action: proposal.draft.action,
    metric: proposal.draft.metric,
    method: proposal.draft.method,
    skills: proposal.draft.skills,
  });

  async function decide(decision: "accept" | "reject" | "revise") {
    if (!proposal.bulletId || busy) return;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/resumes/generate/${encodeURIComponent(threadId)}/proposals/${encodeURIComponent(proposal.bulletId)}?resumeId=${encodeURIComponent(resumeId)}`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ action: decision, ...(decision === "revise" ? { draft } : {}) }),
        },
      );
      const body = (await response.json().catch(() => null)) as {
        detail?: string;
        error?: { message?: string };
      } | null;
      if (!response.ok) {
        setError(body?.detail ?? body?.error?.message ?? "The proposal could not be updated.");
        return;
      }
      setAction(decision);
      setMessage(
        decision === "reject"
          ? "Proposal rejected. It will stay out of the resume."
          : "Saved and attached to this resume. Render a new version to include it in the PDF.",
      );
      router.refresh();
    } catch {
      setError("Could not reach ResuNet. Refresh to check the proposal state.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-3 border-t border-line pt-3">
      {!proposal.bulletId ? (
        <p className="text-xs text-muted">Finish the generation questions before reviewing this saved proposal.</p>
      ) : proposal.decision ? (
        <p role="status" className="text-xs text-ok">
          {proposal.decision === "rejected"
            ? "Rejected. This proposal stays out of the resume."
            : "Accepted and attached to this resume. Render a new version to include it in the PDF."}
        </p>
      ) : action ? (
        <p role="status" className="text-xs text-ok">{message}</p>
      ) : (
        <div className="space-y-3">
          {action === "revise" ? (
            <div className="grid gap-2">
              {(["action", "metric", "method"] as const).map((field) => (
                <label key={field} className="text-xs text-muted">
                  {field === "action" ? "What you did" : field === "metric" ? "Measured result" : "How you did it"}
                  <input
                    value={draft[field]}
                    onChange={(event) => setDraft((current) => ({ ...current, [field]: event.target.value }))}
                    maxLength={field === "metric" ? 160 : 200}
                    className="mt-1 w-full rounded-card border border-line bg-paper px-3 py-2 text-sm text-ink"
                  />
                </label>
              ))}
              <div className="flex flex-wrap gap-2">
                <button type="button" disabled={busy} onClick={() => void decide("revise")} className={buttonClass("primary")}>
                  {busy ? "Saving…" : "Save revision"}
                </button>
                <button type="button" onClick={() => setAction(null)} className={buttonClass("secondary")}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {proposal.accepted ? (
                <button type="button" disabled={busy} onClick={() => void decide("accept")} className={buttonClass("primary")}>
                  Accept and attach
                </button>
              ) : null}
              <button type="button" disabled={busy} onClick={() => setAction("revise")} className={buttonClass("secondary")}>
                Revise
              </button>
              <button type="button" disabled={busy} onClick={() => void decide("reject")} className={buttonClass("secondary")}>
                Reject
              </button>
            </div>
          )}
          {error ? <p role="alert" className="text-xs text-danger">{error}</p> : null}
        </div>
      )}
    </div>
  );
}
