import Link from "next/link";

import { APPLICATION_STAGES, PIPELINE_RAIL } from "@resunet/shared";

import { ApplicationAction } from "@/components/application/ApplicationAction";
import { StatusChip } from "@/components/application/StatusChip";
import { SkillPile } from "@/components/physics/SkillPile";
import { Card, EmptyState, Meter, Stat, buttonClass } from "@/components/ui/primitives";
import { Timestamp } from "@/components/ui/Timestamp";
import { withFreshSession } from "@/lib/api/client";
import {
  funnelStats,
  listApplications,
  listBullets,
  listProfileSkills,
  listResumes,
  topRecommendations,
} from "@/lib/api/endpoints";
import { pluralize } from "@/lib/format";

import type { Metadata } from "next";
import type { ApplicationSummary, ResumeSummary } from "@resunet/shared";

export const metadata: Metadata = { title: "Dashboard" };

/**
 * The dashboard answers one question: what is stuck on me?
 *
 * Everything else on the page is context for that. The blocked list comes first
 * and at full width; the funnel, the recommendations, and the skill pile are
 * below it in descending order of how often a user would act on them. A layout
 * that led with a chart would look better in a screenshot and be worse to use.
 */

/**
 * Median, not mean.
 *
 * One abandoned draft scoring 31 drags a mean down far enough to make a set of
 * strong resumes look weak, and the number sits next to a bar the user is meant
 * to read as "where do I stand".
 */
function medianScore(resumes: ResumeSummary[]): number | null {
  const scores = resumes
    .map((resume) => resume.latestAtsScore)
    .filter((score): score is number => score !== null)
    .sort((a, b) => a - b);

  if (scores.length === 0) return null;
  const mid = Math.floor(scores.length / 2);
  return scores.length % 2 === 1
    ? (scores[mid] as number)
    : ((scores[mid - 1] as number) + (scores[mid] as number)) / 2;
}

/** Which action, if any, this application is waiting on. */
function actionFor(status: ApplicationSummary["status"]): "approve" | "retry" | null {
  if (status === "awaiting_approval") return "approve";
  if (status === "failed") return "retry";
  return null;
}

export default async function DashboardPage() {
  // One `withFreshSession` around the whole fan-out, so an expired access token
  // triggers exactly one refresh instead of four racing ones.
  const [funnel, applications, resumes, recommendations, skills] = await withFreshSession(() =>
    Promise.all([
      funnelStats(),
      listApplications({ limit: 50 }),
      listResumes({ limit: 10 }),
      topRecommendations(),
      listProfileSkills(),
    ]),
  );

  // Bullet counts still come from the primary resume so the "evidenced bullets"
  // stat stays tied to a working copy, not the cross-resume skill pile.
  const primaryResume = resumes.items[0];
  const bullets =
    primaryResume !== undefined
      ? await withFreshSession(() => listBullets(primaryResume.id, { limit: 200 }))
      : null;
  const compliantBullets = bullets?.items.filter((b) => b.violations.length === 0).length ?? 0;
  const totalBullets = bullets?.items.length ?? 0;

  const blocked = applications.items.filter((app) => APPLICATION_STAGES[app.status].needsUser);
  const inFlight = applications.items.filter(
    (app) => !APPLICATION_STAGES[app.status].terminal && app.status !== "draft",
  );
  const median = medianScore(resumes.items);

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Overview</p>
          <h1 className="mt-1.5 text-title">Dashboard</h1>
        </div>
        <Link href="/resumes" className={buttonClass("primary")}>
          Tailor a resume
        </Link>
      </header>

      <Card>
        <div className="grid grid-cols-2 divide-line md:grid-cols-4 md:divide-x">
          <div className="border-b border-line px-5 py-4 md:border-b-0">
            <Stat
              label="Waiting on you"
              value={blocked.length}
              hint={blocked.length === 0 ? "Nothing blocked" : "Listed below"}
            />
          </div>
          <div className="border-b border-line border-l border-l-line px-5 py-4 md:border-b-0 md:border-l-0">
            <Stat label="In flight" value={inFlight.length} hint="Submitted or moving" />
          </div>
          <div className="px-5 py-4">
            <Stat
              label="Evidenced bullets"
              value={compliantBullets}
              hint={
                totalBullets === 0
                  ? "No bullets yet"
                  : `of ${totalBullets} in ${primaryResume?.title ?? "your resume"}`
              }
            />
          </div>
          <div className="border-l border-line px-5 py-4">
            <Stat
              label="Median ATS"
              value={median === null ? "--" : Math.round(median)}
              hint={median === null ? "Score a resume to see this" : "Across all resumes"}
            />
          </div>
        </div>
      </Card>

      <Card
        title="Waiting on you"
        eyebrow="Blocked"
        aside={blocked.length > 0 ? pluralize(blocked.length, "item") : undefined}
      >
        {blocked.length === 0 ? (
          <EmptyState
            title="Nothing is blocked"
            body="Applications appear here when the automation pauses for approval, a submission fails, or a company replies."
          />
        ) : (
          <ul className="divide-y divide-line">
            {blocked.map((app) => {
              const action = actionFor(app.status);
              return (
                <li
                  key={app.id}
                  className="flex flex-wrap items-center justify-between gap-4 px-5 py-3.5"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <Link
                        href={`/applications?focus=${encodeURIComponent(app.id)}`}
                        className="truncate text-sm font-500 hover:text-accent"
                      >
                        {app.jobTitle}
                      </Link>
                      <StatusChip status={app.status} />
                    </div>
                    <p className="mt-0.5 text-xs text-muted">
                      {app.companyName} &middot; updated <Timestamp iso={app.updatedAt} />
                      {app.attemptCount > 1 ? (
                        <>
                          {" "}
                          &middot;{" "}
                          <span className="font-mono" data-numeric>
                            {pluralize(app.attemptCount, "attempt")}
                          </span>
                        </>
                      ) : null}
                    </p>
                  </div>
                  {action !== null ? (
                    <ApplicationAction applicationId={app.id} action={action} />
                  ) : (
                    <Link
                      href={`/applications?focus=${encodeURIComponent(app.id)}`}
                      className={buttonClass("quiet")}
                    >
                      Open
                    </Link>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Pipeline" eyebrow="Funnel" aside={`${applications.total} total`}>
          {/* The rail is ordered because the pipeline genuinely is ordered --
              an application moves through these stages in sequence. Numbering
              anything that is not a real sequence would be decoration. */}
          <ol className="divide-y divide-line">
            {PIPELINE_RAIL.map((status) => {
              const count = funnel[status] ?? 0;
              return (
                <li
                  key={status}
                  className="flex items-center gap-4 px-5 py-2"
                  data-empty={count === 0 ? "true" : "false"}
                >
                  <span
                    className={`w-32 shrink-0 text-sm ${count === 0 ? "text-faint" : "text-ink"}`}
                  >
                    {APPLICATION_STAGES[status].label}
                  </span>
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-sunken">
                    <div
                      className="h-full rounded-full bg-role-z"
                      style={{
                        width: `${
                          applications.total === 0
                            ? 0
                            : Math.min(100, (count / applications.total) * 100)
                        }%`,
                      }}
                    />
                  </div>
                  <span
                    className="w-8 shrink-0 text-right font-mono text-xs tabular-nums text-muted"
                    data-numeric
                  >
                    {count}
                  </span>
                </li>
              );
            })}
          </ol>
        </Card>

        <Card
          title="Roles worth a tailored resume"
          eyebrow="Matches"
          aside={<Link href="/resumes" className="hover:text-ink">All resumes</Link>}
        >
          {recommendations.length === 0 ? (
            <EmptyState
              title="No matches yet"
              body="Save a job description to compare it with the skills and experience in your profile."
            />
          ) : (
            <ul className="divide-y divide-line">
              {recommendations.slice(0, 5).map((job) => (
                <li key={job.id} className="px-5 py-3">
                  <div className="flex items-baseline justify-between gap-4">
                    <p className="truncate text-sm font-500">{job.title}</p>
                    {job.matchScore !== null ? (
                      <span className="w-28 shrink-0">
                        <Meter value={job.matchScore} label={`Match for ${job.title}`} />
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-0.5 text-xs text-muted">
                    {job.companyName}
                    {job.location !== null ? ` · ${job.location}` : ""}
                    {/* Whether we can file this one automatically is the single
                        most useful fact about a posting here, so it is stated
                        rather than left to an icon. */}
                    {job.supportsAutomation ? "" : " · manual application"}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card
        title="Skills you can evidence"
        eyebrow="Proof"
        aside={skills.length > 0 ? pluralize(skills.length, "skill") : undefined}
      >
        <p className="px-5 pt-3.5 text-xs text-muted">
          Only skills attached to a bullet that passes XYZ. A skill you have listed but never
          quantified does not appear.
        </p>
        <SkillPile skills={skills} />
        {skills.length > 0 ? (
          <p className="border-t border-line px-5 py-3 text-xs text-faint">
            {skills.map((skill) => `${skill.name} (${skill.count})`).join(", ")}
          </p>
        ) : null}
      </Card>
    </div>
  );
}
