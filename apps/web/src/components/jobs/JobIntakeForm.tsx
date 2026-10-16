"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { buttonClass } from "@/components/ui/primitives";

export function JobIntakeForm() {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setPending(true);
    setError(null);
    setSaved(false);
    const url = String(form.get("url") ?? "").trim();
    try {
      const response = await fetch("/api/jobs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          title: String(form.get("title") ?? "").trim(),
          companyName: String(form.get("companyName") ?? "").trim(),
          descriptionRaw: String(form.get("descriptionRaw") ?? "").trim(),
          status: String(form.get("status") ?? "unknown"),
          ...(url ? { url } : {}),
        }),
      });
      const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
      if (!response.ok) {
        setError(typeof body?.detail === "string" ? body.detail : "Could not save this job.");
        return;
      }
      formElement.reset();
      setSaved(true);
      startTransition(() => router.refresh());
    } catch {
      setError("Could not reach the server. Your posting is still on this form; try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-3 p-5">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-muted">
          Job title
          <input name="title" required maxLength={250} className="mt-1 w-full rounded-card border border-line bg-paper px-3 py-2 text-sm text-ink" />
        </label>
        <label className="text-xs text-muted">
          Company
          <input name="companyName" required maxLength={200} className="mt-1 w-full rounded-card border border-line bg-paper px-3 py-2 text-sm text-ink" />
        </label>
      </div>
      <label className="block text-xs text-muted">
        Posting link <span className="text-faint">(optional reference; ResuNet does not fetch it)</span>
        <input name="url" type="url" className="mt-1 w-full rounded-card border border-line bg-paper px-3 py-2 text-sm text-ink" />
      </label>
      <label className="block text-xs text-muted">
        Job description
        <textarea name="descriptionRaw" required maxLength={60_000} rows={7} className="mt-1 w-full rounded-card border border-line bg-paper px-3 py-2 text-sm text-ink" />
      </label>
      <label className="block max-w-xs text-xs text-muted">
        Posting status
        <select name="status" defaultValue="unknown" className="mt-1 w-full rounded-card border border-line bg-paper px-3 py-2 text-sm text-ink">
          <option value="unknown">Not confirmed</option>
          <option value="open">Open</option>
          <option value="closed">Closed</option>
        </select>
      </label>
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
      {saved ? <p role="status" className="text-sm text-ok">Job saved and matched against your profile.</p> : null}
      <button type="submit" disabled={pending} className={buttonClass("primary")}>
        {pending ? "Saving…" : "Save job and match"}
      </button>
    </form>
  );
}
