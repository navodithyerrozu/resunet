import { Card, Chip, EmptyState, Meter } from "@/components/ui/primitives";
import { JobIntakeForm } from "@/components/jobs/JobIntakeForm";
import { withFreshSession } from "@/lib/api/client";
import { listJobs } from "@/lib/api/endpoints";

import type { Metadata } from "next";

export const metadata: Metadata = { title: "Jobs" };

export default async function JobsPage() {
  const jobs = await withFreshSession(() => listJobs({ limit: 100 }));

  return (
    <div className="space-y-6">
      <header>
        <p className="eyebrow">Opportunities</p>
        <h1 className="mt-1.5 text-title">Jobs</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted">
          Paste a posting to save it and compare its stated requirements with your profile.
          ResuNet does not fetch job links or contact job boards.
        </p>
      </header>

      <Card title="Save a posting" eyebrow="Job intake">
        <JobIntakeForm />
      </Card>

      <Card title="Saved jobs" eyebrow="Your matches" aside={`${jobs.total} total`}>
        {jobs.items.length === 0 ? (
          <EmptyState
            title="No saved postings"
            body="Paste a real job description above to create your first profile-based match."
          />
        ) : (
          <ul className="divide-y divide-line">
            {jobs.items.map((job) => (
              <li key={job.id} className="flex flex-wrap items-center justify-between gap-4 px-5 py-4">
                <div className="min-w-0">
                  <p className="truncate text-sm font-500">{job.title}</p>
                  <p className="mt-1 text-xs text-muted">
                    {job.companyName}{job.location ? ` · ${job.location}` : ""}
                    {job.supportsAutomation ? "" : " · manual application"}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <Chip tone={job.status === "open" ? "ok" : job.status === "closed" ? "warn" : "neutral"}>
                    {job.status === "unknown" ? "status unconfirmed" : job.status}
                  </Chip>
                  {job.matchScore !== null ? (
                    <span className="w-28">
                      <Meter value={job.matchScore} label={`Profile match for ${job.title}`} />
                    </span>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
