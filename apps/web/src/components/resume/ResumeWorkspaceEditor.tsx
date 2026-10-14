"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { Chip, buttonClass } from "@/components/ui/primitives";

import type { Resume, ResumeSection, ResumeTemplate } from "@resunet/shared";

const TEMPLATES: { value: ResumeTemplate; label: string }[] = [
  { value: "ats_classic", label: "Classic" },
  { value: "ats_compact", label: "Compact" },
  { value: "ats_technical", label: "Technical" },
];

async function patchResume(resumeId: string, payload: Record<string, unknown>): Promise<string | null> {
  const response = await fetch(`/api/resumes/${encodeURIComponent(resumeId)}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (response.ok) return null;
  const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  return typeof body?.detail === "string" ? body.detail : "Update failed.";
}

export function ResumeTemplateSelect({
  resumeId,
  template,
}: {
  resumeId: string;
  template: ResumeTemplate;
}) {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const [value, setValue] = useState<ResumeTemplate>(template);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div>
      <label htmlFor={`tpl-${resumeId}`} className="eyebrow block">
        Template
      </label>
      <select
        id={`tpl-${resumeId}`}
        value={value}
        disabled={pending}
        onChange={(event) => {
          const next = event.target.value as ResumeTemplate;
          setValue(next);
          setPending(true);
          setError(null);
          void patchResume(resumeId, { template: next })
            .then((message) => {
              if (message !== null) {
                setError(message);
                setValue(template);
                return;
              }
              startTransition(() => router.refresh());
            })
            .catch(() => {
              setError("Could not reach ResuNet.");
              setValue(template);
            })
            .finally(() => setPending(false));
        }}
        className="mt-1.5 rounded-card border border-line bg-paper px-3 py-1.5 text-sm outline-none focus:border-accent"
      >
        {TEMPLATES.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {error !== null ? (
        <p role="alert" className="mt-1 text-xs text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function ResumeWorkspaceEditor({ resume }: { resume: Resume }) {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const resumeId = resume.id;
  const [summary, setSummary] = useState(resume.summary ?? "");
  const [sections, setSections] = useState<ResumeSection[]>(
    [...resume.sections].sort((a, b) => a.order - b.order),
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (resumeId === null) return null;

  async function patch(payload: Record<string, unknown>) {
    if (resumeId === null) return;
    setPending(true);
    setError(null);
    try {
      const message = await patchResume(resumeId, payload);
      if (message !== null) {
        setError(message);
        return;
      }
      startTransition(() => router.refresh());
    } catch {
      setError("Could not reach ResuNet.");
    } finally {
      setPending(false);
    }
  }

  function move(index: number, delta: number) {
    const nextIndex = index + delta;
    if (nextIndex < 0 || nextIndex >= sections.length) return;
    const next = [...sections];
    const current = next[index];
    const swap = next[nextIndex];
    if (current === undefined || swap === undefined) return;
    next[index] = swap;
    next[nextIndex] = current;
    const reindexed = next.map((section, order) => ({ ...section, order }));
    setSections(reindexed);
    void patch({ sections: reindexed });
  }

  return (
    <div className="space-y-4 border-b border-line px-5 py-4">
      <div>
        <label htmlFor={`sum-${resumeId}`} className="eyebrow block">
          Summary
        </label>
        <textarea
          id={`sum-${resumeId}`}
          value={summary}
          disabled={pending}
          rows={4}
          onChange={(event) => setSummary(event.target.value)}
          onBlur={() => {
            if (summary !== (resume.summary ?? "")) void patch({ summary });
          }}
          className="mt-1.5 w-full rounded-card border border-line bg-paper px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60"
        />
      </div>

      {sections.length > 0 ? (
        <div>
          <p className="eyebrow">Section order</p>
          <ol className="mt-2 divide-y divide-line rounded-card border border-line">
            {sections.map((section, index) => (
              <li key={`${section.kind}-${section.order}`} className="flex items-center gap-2 px-3 py-2">
                <span className="font-mono text-xs tabular-nums text-faint" data-numeric>
                  {section.order + 1}
                </span>
                <span className="flex-1 text-sm">{section.title}</span>
                {!section.visible ? <Chip>Hidden</Chip> : null}
                <button
                  type="button"
                  className={buttonClass("quiet")}
                  disabled={pending || index === 0}
                  onClick={() => move(index, -1)}
                >
                  Up
                </button>
                <button
                  type="button"
                  className={buttonClass("quiet")}
                  disabled={pending || index === sections.length - 1}
                  onClick={() => move(index, 1)}
                >
                  Down
                </button>
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      {error !== null ? (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}
