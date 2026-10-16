"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { XyzBullet } from "@/components/resume/XyzBullet";
import { ProposalReview } from "@/components/resume/ProposalReview";
import { buttonClass } from "@/components/ui/primitives";

import type { GenerateTurnResponse } from "@resunet/shared";

type EntryOption = { id: string; label: string };

export function GenerationChat({
  resumeId,
  entries,
  initialTurn = null,
}: {
  resumeId: string;
  entries: EntryOption[];
  initialTurn?: GenerateTurnResponse | null;
}) {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const [threadId, setThreadId] = useState<string | null>(initialTurn?.threadId ?? null);
  const [entryId, setEntryId] = useState(entries.length === 1 ? entries[0]?.id ?? "" : "");
  const [turn, setTurn] = useState<GenerateTurnResponse | null>(initialTurn);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function postStart() {
    const response = await fetch("/api/resumes/generate/start", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        resumeId,
        profileEntryId: entryId,
        seedNotes: draft.length > 0 ? draft : null,
      }),
    });
    return response;
  }

  async function postTurn(id: string, message: string) {
    return fetch(`/api/resumes/generate/${encodeURIComponent(id)}/turn`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message }),
    });
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const message = draft.trim();
    if (threadId !== null && message.length === 0) return;

    setPending(true);
    setError(null);

    try {
      const response =
        threadId === null ? await postStart() : await postTurn(threadId, message);
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
        setError(typeof body?.detail === "string" ? body.detail : "The agent did not respond.");
        return;
      }
      const next = (await response.json()) as GenerateTurnResponse;
      setTurn(next);
      setThreadId(next.threadId);
      setDraft("");
      startTransition(() => router.refresh());
    } catch {
      setError("Could not reach the generation agent.");
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="border-t border-line px-5 py-4">
      <p className="eyebrow">AI tailoring agent</p>
      <h3 className="mt-1 text-[0.9375rem] font-600">Draft bullets from notes</h3>

      {threadId === null ? (
        <label className="mt-3 block text-xs text-muted">
          Experience or project
          <select
            value={entryId}
            onChange={(event) => setEntryId(event.target.value)}
            disabled={pending || entries.length === 0}
            className="mt-1 w-full rounded-card border border-line bg-paper px-3 py-2 text-sm text-ink"
          >
            <option value="">Choose an entry</option>
            {entries.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {turn !== null ? (
        <div className="mt-3 space-y-3">
          <p className="text-sm text-ink">{turn.assistantMessage}</p>
          {turn.needsInput && turn.question !== null ? (
            <p className="rounded-card border border-warn/25 bg-warn-wash px-3 py-2 text-sm text-warn">
              {turn.question}
            </p>
          ) : null}
          {turn.proposals.map((proposal, index) => (
            <div key={`${proposal.text}-${String(index)}`} className="rounded-card border border-line p-3">
              <XyzBullet
                bullet={{
                  action: proposal.draft.action,
                  metric: proposal.draft.metric,
                  method: proposal.draft.method,
                  violations: proposal.violations,
                  skills: proposal.draft.skills,
                }}
              />
              <ProposalReview
                resumeId={resumeId}
                threadId={threadId ?? turn.threadId}
                proposal={proposal}
              />
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-2 text-sm text-muted">
          Paste messy notes. The agent will press for a metric before anything is saved.
        </p>
      )}

      {turn?.status === "completed" ? (
        <button
          type="button"
          onClick={() => {
            setThreadId(null);
            setTurn(null);
            setDraft("");
            setError(null);
          }}
          className={`${buttonClass("secondary")} mt-3`}
        >
          Start another draft
        </button>
      ) : null}
      {threadId === null || turn?.status === "awaiting_user" ? (
        <form onSubmit={submit} className="mt-3 space-y-2">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            rows={threadId === null ? 4 : 3}
            disabled={pending}
            placeholder={threadId === null ? "Raw notes from this role…" : "Answer the question…"}
            className="w-full rounded-card border border-line bg-paper px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60"
          />
          {error !== null ? (
            <p role="alert" className="text-sm text-danger">
              {error}
            </p>
          ) : null}
          <button
            type="submit"
            disabled={pending || (threadId === null && entryId.length === 0)}
            className={buttonClass("secondary")}
          >
            {pending ? "Thinking…" : threadId === null ? "Start thread" : "Send"}
          </button>
        </form>
      ) : null}
    </section>
  );
}
