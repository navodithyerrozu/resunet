"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";

import { buttonClass } from "@/components/ui/primitives";

import type { JobSummary, ResumeCreate, ResumeTemplate } from "@resunet/shared";

const TEMPLATES: { value: ResumeTemplate; label: string }[] = [
  { value: "ats_classic", label: "Classic" },
  { value: "ats_compact", label: "Compact" },
  { value: "ats_technical", label: "Technical" },
];

export function CreateResumeForm({ jobs }: { jobs: JobSummary[] }) {
  const router = useRouter();
  const titleId = useId();
  const roleId = useId();
  const templateId = useId();
  const jobId = useId();

  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;

    const form = new FormData(event.currentTarget);
    const title = String(form.get("title") ?? "").trim();
    const targetRole = String(form.get("targetRole") ?? "").trim();
    const template = String(form.get("template") ?? "ats_classic") as ResumeTemplate;
    const targetJobId = String(form.get("targetJobId") ?? "").trim();

    const payload: ResumeCreate = { title, template };
    if (targetRole.length > 0) payload.targetRole = targetRole;
    if (targetJobId.length > 0) payload.targetJobId = targetJobId;

    setPending(true);
    setError(null);

    try {
      const response = await fetch("/api/resumes", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (response.status === 201) {
        const created = (await response.json()) as { id?: string | null };
        setOpen(false);
        if (typeof created.id === "string" && created.id.length > 0) {
          router.push(`/resumes/${encodeURIComponent(created.id)}`);
        } else {
          router.refresh();
        }
        return;
      }

      const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
      setError(typeof body?.detail === "string" ? body.detail : "Could not create the resume.");
    } catch {
      setError("Could not reach ResuNet.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div>
      <button type="button" className={buttonClass("primary")} onClick={() => setOpen((v) => !v)}>
        {open ? "Cancel" : "New resume"}
      </button>

      {open ? (
        <form
          onSubmit={onSubmit}
          className="mt-4 space-y-3 rounded-card border border-line bg-surface p-4"
        >
          <div>
            <label htmlFor={titleId} className="eyebrow block">
              Title
            </label>
            <input
              id={titleId}
              name="title"
              required
              minLength={1}
              disabled={pending}
              placeholder="Backend Engineer — Stripe"
              className="mt-1.5 w-full rounded-card border border-line bg-paper px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60"
            />
          </div>
          <div>
            <label htmlFor={roleId} className="eyebrow block">
              Target role
            </label>
            <input
              id={roleId}
              name="targetRole"
              disabled={pending}
              placeholder="Senior Backend Engineer"
              className="mt-1.5 w-full rounded-card border border-line bg-paper px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60"
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor={templateId} className="eyebrow block">
                Template
              </label>
              <select
                id={templateId}
                name="template"
                defaultValue="ats_classic"
                disabled={pending}
                className="mt-1.5 w-full rounded-card border border-line bg-paper px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60"
              >
                {TEMPLATES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor={jobId} className="eyebrow block">
                Target job
              </label>
              <select
                id={jobId}
                name="targetJobId"
                defaultValue=""
                disabled={pending}
                className="mt-1.5 w-full rounded-card border border-line bg-paper px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60"
              >
                <option value="">None</option>
                {jobs.map((job) => (
                  <option key={job.id} value={job.id}>
                    {job.companyName} — {job.title}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {error !== null ? (
            <p role="alert" className="text-sm text-danger">
              {error}
            </p>
          ) : null}
          <button type="submit" disabled={pending} className={buttonClass("primary")}>
            {pending ? "Creating…" : "Create"}
          </button>
        </form>
      ) : null}
    </div>
  );
}
