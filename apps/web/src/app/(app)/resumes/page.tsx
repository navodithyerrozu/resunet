import Link from "next/link";

import { ArchiveResumeButton } from "@/components/resume/ArchiveResumeButton";
import { CreateResumeForm } from "@/components/resume/CreateResumeForm";
import { Card, Chip, EmptyState, Meter } from "@/components/ui/primitives";
import { Timestamp } from "@/components/ui/Timestamp";
import { withFreshSession } from "@/lib/api/client";
import { listJobs, listResumes } from "@/lib/api/endpoints";

import type { Metadata } from "next";
import type { ResumeStatus, ResumeTemplate } from "@resunet/shared";
import type { ChipTone } from "@/components/ui/primitives";

export const metadata: Metadata = { title: "Resumes" };

const TEMPLATE_LABEL: Record<ResumeTemplate, string> = {
  ats_classic: "Classic",
  ats_compact: "Compact",
  ats_technical: "Technical",
};

const STATUS_TONE: Record<ResumeStatus, ChipTone> = {
  draft: "neutral",
  generating: "accent",
  ready: "ok",
  archived: "warn",
};

export default async function ResumesPage() {
  const [resumes, jobs] = await withFreshSession(() =>
    Promise.all([listResumes({ limit: 50 }), listJobs({ limit: 20, status: "open" })]),
  );

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Library</p>
          <h1 className="mt-1.5 text-title">Resumes</h1>
        </div>
        <CreateResumeForm jobs={jobs.items} />
      </header>

      <Card title="Working copies" eyebrow="Resumes" aside={`${resumes.total} total`}>
        {resumes.items.length === 0 ? (
          <EmptyState
            title="No resumes yet"
            body="Create a working copy, attach a target role, and draft XYZ bullets before you render a PDF."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-line text-xs uppercase tracking-wider text-faint">
                  <th className="px-5 py-2.5 font-500">Title</th>
                  <th className="px-5 py-2.5 font-500">Target role</th>
                  <th className="px-5 py-2.5 font-500">Template</th>
                  <th className="px-5 py-2.5 font-500">Status</th>
                  <th className="px-5 py-2.5 font-500">Version</th>
                  <th className="px-5 py-2.5 font-500">ATS</th>
                  <th className="px-5 py-2.5 font-500">Updated</th>
                  <th className="px-5 py-2.5 font-500">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {resumes.items.map((resume) => (
                  <tr key={resume.id}>
                    <td className="px-5 py-3">
                      <Link
                        href={`/resumes/${encodeURIComponent(resume.id)}`}
                        className="font-500 hover:text-accent"
                      >
                        {resume.title}
                      </Link>
                    </td>
                    <td className="px-5 py-3 text-muted">{resume.targetRole ?? "—"}</td>
                    <td className="px-5 py-3">
                      <Chip>{TEMPLATE_LABEL[resume.template]}</Chip>
                    </td>
                    <td className="px-5 py-3">
                      <Chip tone={STATUS_TONE[resume.status]}>{resume.status}</Chip>
                    </td>
                    <td className="px-5 py-3 font-mono tabular-nums" data-numeric>
                      v{resume.currentVersion}
                    </td>
                    <td className="px-5 py-3">
                      {resume.latestAtsScore !== null ? (
                        <Meter value={resume.latestAtsScore} label={`ATS for ${resume.title}`} />
                      ) : (
                        <span className="text-faint">—</span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-muted">
                      <Timestamp iso={resume.updatedAt} />
                    </td>
                    <td className="px-5 py-3 text-right">
                      {resume.status !== "archived" ? (
                        <ArchiveResumeButton resumeId={resume.id} />
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
