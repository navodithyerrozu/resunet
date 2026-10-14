"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { buttonClass } from "@/components/ui/primitives";

export function ArchiveResumeButton({ resumeId }: { resumeId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [, startTransition] = useTransition();

  async function archive() {
    if (busy) return;
    setBusy(true);
    try {
      const response = await fetch(`/api/resumes/${encodeURIComponent(resumeId)}`, {
        method: "DELETE",
      });
      if (response.ok) {
        startTransition(() => router.refresh());
        return;
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <button type="button" onClick={() => void archive()} disabled={busy} className={buttonClass("quiet")}>
      {busy ? "Archiving…" : "Archive"}
    </button>
  );
}
