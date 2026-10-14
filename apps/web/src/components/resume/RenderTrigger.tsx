"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { buttonClass } from "@/components/ui/primitives";
import { duration } from "@/lib/format";

import type { RenderJob, ResumeTemplate } from "@resunet/shared";

const POLL_MS = 1_500;

export function RenderTrigger({
  resumeId,
  template,
}: {
  resumeId: string;
  template: ResumeTemplate;
}) {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<RenderJob | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function poll(jobId: string): Promise<RenderJob> {
    for (;;) {
      const response = await fetch(`/api/resumes/render-jobs/${encodeURIComponent(jobId)}`);
      if (!response.ok) {
        throw new Error("poll failed");
      }
      const current = (await response.json()) as RenderJob;
      setJob(current);
      if (current.status === "succeeded" || current.status === "failed") return current;
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    }
  }

  async function render() {
    if (busy) return;
    setBusy(true);
    setMessage(null);
    setJob(null);

    try {
      const response = await fetch(`/api/resumes/${encodeURIComponent(resumeId)}/render`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ template }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
        setMessage(typeof body?.detail === "string" ? body.detail : "Render did not start.");
        return;
      }
      const accepted = (await response.json()) as RenderJob;
      setJob(accepted);
      const jobId = accepted.id;
      if (jobId === null || jobId.length === 0) {
        setMessage("Render queued, but no job id was returned.");
        return;
      }
      const finished = await poll(jobId);
      if (finished.status === "succeeded") {
        startTransition(() => router.refresh());
      }
    } catch {
      setMessage("Could not reach the render worker.");
    } finally {
      setBusy(false);
    }
  }

  const failed = job?.status === "failed";
  const log = job?.latex_log_tail;

  return (
    <div className="flex flex-col items-end gap-1">
      <button type="button" onClick={() => void render()} disabled={busy} className={buttonClass("primary")}>
        {busy ? "Rendering…" : "Render PDF"}
      </button>
      {job !== null && job.status === "succeeded" && job.duration_ms !== null ? (
        <p className="font-mono text-xs tabular-nums text-muted" data-numeric>
          Compiled in {duration(job.duration_ms)}
        </p>
      ) : null}
      {failed ? (
        <p role="alert" className="max-w-sm text-right text-xs text-danger">
          {job.error_message ?? "Compile failed."}
        </p>
      ) : null}
      {failed && log !== null && log !== undefined && log.length > 0 ? (
        <pre className="mt-1 max-h-40 max-w-md overflow-auto rounded-card border border-line bg-sunken p-2 text-left font-mono text-[0.6875rem] text-muted">
          {log}
        </pre>
      ) : null}
      {message !== null ? (
        <p role="alert" className="text-xs text-danger">
          {message}
        </p>
      ) : null}
    </div>
  );
}
