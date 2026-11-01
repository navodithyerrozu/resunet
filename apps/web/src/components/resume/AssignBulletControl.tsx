"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { buttonClass } from "@/components/ui/primitives";

import type { ResumeSection } from "@resunet/shared";

/**
 * Assign an unassigned bullet to one of the resume's sections.
 *
 * Selection lives on the resume (see ResumeSection in the API models), so
 * assigning is a PATCH of the resume's sections with this bullet's id
 * appended to the chosen section's bullet_ids -- the same PATCH the section
 * reorder editor already uses. After the PATCH, a router refresh re-renders
 * the server page, moving the bullet out of "Unassigned" and into its
 * section, which is what makes it eligible for rendering.
 */
export function AssignBulletControl({
  resumeId,
  bulletId,
  sections,
}: {
  resumeId: string;
  bulletId: string;
  sections: ResumeSection[];
}) {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const assignable = sections.filter((section) => section.kind !== "summary");
  const defaultOrder =
    assignable.find((section) => section.kind === "projects")?.order ??
    assignable[0]?.order ??
    0;
  const [order, setOrder] = useState(defaultOrder);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (bulletId === null || assignable.length === 0) return null;

  async function assign() {
    setPending(true);
    setError(null);
    try {
      const nextSections = sections.map((section) =>
        section.order === order && !section.bullet_ids.includes(bulletId)
          ? { ...section, bullet_ids: [...section.bullet_ids, bulletId] }
          : section,
      );
      const response = await fetch(`/api/resumes/${encodeURIComponent(resumeId)}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ sections: nextSections }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as {
          detail?: unknown;
        } | null;
        setError(typeof body?.detail === "string" ? body.detail : "Assign failed.");
        return;
      }
      startTransition(() => router.refresh());
    } catch {
      setError("Could not reach ResuNet.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 pt-2">
      <label htmlFor={`assign-${bulletId}`} className="text-xs text-muted">
        Assign to
      </label>
      <select
        id={`assign-${bulletId}`}
        value={order}
        disabled={pending}
        onChange={(event) => setOrder(Number(event.target.value))}
        className="rounded-card border border-line bg-paper px-2 py-1 text-sm outline-none focus:border-accent disabled:opacity-60"
      >
        {assignable.map((section) => (
          <option key={section.order} value={section.order}>
            {section.title}
          </option>
        ))}
      </select>
      <button
        type="button"
        disabled={pending}
        onClick={() => void assign()}
        className={buttonClass("secondary")}
      >
        {pending ? "Assigning…" : "Assign"}
      </button>
      {error !== null ? (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}
