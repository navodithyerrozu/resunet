"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { buttonClass } from "@/components/ui/primitives";

type Option = { id: string; label: string };

export function ApplicationCreateForm({
  jobs,
  versions,
}: {
  jobs: Option[];
  versions: Option[];
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch("/api/applications", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          jobId: String(data.get("jobId") ?? ""),
          resumeVersionId: String(data.get("resumeVersionId") ?? ""),
          method: "manual",
          idempotencyKey: crypto.randomUUID(),
        }),
      });
      const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
      if (!response.ok) {
        setMessage(typeof body?.detail === "string" ? body.detail : "Could not add the application.");
        return;
      }
      form.reset();
      setMessage("Draft added. ResuNet did not submit anything to the employer.");
      router.refresh();
    } catch {
      setMessage("Could not reach the server. Check the tracker before trying again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="grid gap-3 p-5 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
      <label className="text-xs text-muted">
        Open job
        <select name="jobId" required disabled={jobs.length === 0} className="mt-1 w-full rounded-card border border-line bg-paper px-3 py-2 text-sm text-ink">
          {jobs.length === 0 ? <option value="">Save an open job first</option> : null}
          {jobs.map((job) => <option key={job.id} value={job.id}>{job.label}</option>)}
        </select>
      </label>
      <label className="text-xs text-muted">
        Rendered resume version
        <select name="resumeVersionId" required disabled={versions.length === 0} className="mt-1 w-full rounded-card border border-line bg-paper px-3 py-2 text-sm text-ink">
          {versions.length === 0 ? <option value="">Render a resume PDF first</option> : null}
          {versions.map((version) => <option key={version.id} value={version.id}>{version.label}</option>)}
        </select>
      </label>
      <button type="submit" disabled={busy || jobs.length === 0 || versions.length === 0} className={buttonClass("primary")}>
        {busy ? "Adding…" : "Add draft"}
      </button>
      {message ? <p role="status" className="text-xs text-muted sm:col-span-3">{message}</p> : null}
    </form>
  );
}
