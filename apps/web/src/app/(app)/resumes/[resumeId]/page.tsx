import { notFound } from "next/navigation";

import { AtsScorePanel } from "@/components/resume/AtsScorePanel";
import { GenerationChat } from "@/components/resume/GenerationChat";
import { RenderTrigger } from "@/components/resume/RenderTrigger";
import { ResumeTemplateSelect, ResumeWorkspaceEditor } from "@/components/resume/ResumeWorkspaceEditor";
import { XyzBullet } from "@/components/resume/XyzBullet";
import { Card, Chip, EmptyState, Meter } from "@/components/ui/primitives";
import { withFreshSession } from "@/lib/api/client";
import { getResume, listAllBullets, listResumeVersions } from "@/lib/api/endpoints";
import { ApiError } from "@/lib/api/errors";

import type { Metadata } from "next";
import type { Bullet, Resume, ResumeStatus, ResumeVersion } from "@resunet/shared";
import type { ChipTone } from "@/components/ui/primitives";

export const metadata: Metadata = { title: "Resume workspace" };

type Params = Promise<{ resumeId: string }>;

const STATUS_TONE: Record<ResumeStatus, ChipTone> = {
  draft: "neutral",
  generating: "accent",
  ready: "ok",
  archived: "warn",
};

function currentVersionId(versions: ResumeVersion[], currentVersion: number): string | null {
  const match = versions.find((version) => version.version === currentVersion);
  return match?.id ?? null;
}

function bulletsForSection(sectionBulletIds: string[], byId: Map<string, Bullet>): Bullet[] {
  const out: Bullet[] = [];
  for (const id of sectionBulletIds) {
    const bullet = byId.get(id);
    if (bullet !== undefined) out.push(bullet);
  }
  return out;
}

export default async function ResumeWorkspacePage({ params }: { params: Params }) {
  const { resumeId } = await params;

  let resume: Resume;
  let bullets: { items: Bullet[] };
  let versions: { items: ResumeVersion[] };
  try {
    const loaded = await withFreshSession(() =>
      Promise.all([
        getResume(resumeId),
        listAllBullets(resumeId),
        listResumeVersions(resumeId, { limit: 50 }),
      ]),
    );
    resume = loaded[0];
    bullets = loaded[1];
    versions = loaded[2];
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }

  if (resume.id === null) notFound();

  const byId = new Map<string, Bullet>();
  const assigned = new Set<string>();
  for (const bullet of bullets.items) {
    if (bullet.id !== null) byId.set(bullet.id, bullet);
  }

  const orderedSections = [...resume.sections].sort((a, b) => a.order - b.order);
  for (const section of orderedSections) {
    for (const id of section.bullet_ids) assigned.add(id);
  }
  const unassigned = bullets.items.filter((bullet) => bullet.id === null || !assigned.has(bullet.id));

  const versionId = currentVersionId(versions.items, resume.current_version);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <p className="eyebrow">Workspace</p>
          <h1 className="mt-1.5 truncate text-title">{resume.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Chip tone={STATUS_TONE[resume.status]}>{resume.status}</Chip>
            {resume.target_role !== null ? (
              <span className="text-sm text-muted">{resume.target_role}</span>
            ) : null}
            <span className="font-mono text-xs tabular-nums text-faint" data-numeric>
              v{resume.current_version}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap items-end gap-6">
          <ResumeTemplateSelect resumeId={resume.id} template={resume.template} />
          <div className="w-40">
            <p className="eyebrow">Latest ATS</p>
            <div className="mt-1.5">
              {resume.latest_ats_score !== null ? (
                <Meter value={resume.latest_ats_score} label={`ATS for ${resume.title}`} />
              ) : (
                <span className="text-sm text-faint">Not scored</span>
              )}
            </div>
          </div>
          <RenderTrigger resumeId={resume.id} template={resume.template} />
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card title="Sections & bullets" eyebrow="Draft">
          <ResumeWorkspaceEditor resume={resume} />

          {orderedSections.length === 0 && bullets.items.length === 0 ? (
            <EmptyState
              title="No bullets yet"
              body="Write notes into the tailoring agent. It will press for a metric before anything is saved."
            />
          ) : (
            <div className="space-y-6 px-5 py-4">
              {orderedSections.map((section) => {
                const sectionBullets = bulletsForSection(section.bullet_ids, byId);
                if (!section.visible && sectionBullets.length === 0) return null;
                return (
                  <section key={`${section.kind}-${String(section.order)}`}>
                    <div className="mb-3 flex items-baseline gap-2">
                      <h3 className="text-sm font-600">{section.title}</h3>
                      {!section.visible ? <Chip>Hidden</Chip> : null}
                      <span className="font-mono text-xs tabular-nums text-faint" data-numeric>
                        {sectionBullets.length}
                      </span>
                    </div>
                    {sectionBullets.length === 0 ? (
                      <p className="text-sm text-faint">No bullets in this section.</p>
                    ) : (
                      <ul className="space-y-4">
                        {sectionBullets.map((bullet) => (
                          <li key={bullet.id ?? `${bullet.action}-${bullet.metric}`} className="border-t border-line pt-4">
                            <XyzBullet bullet={bullet} />
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>
                );
              })}
              {unassigned.length > 0 ? (
                <section>
                  <h3 className="mb-3 text-sm font-600">Unassigned</h3>
                  <ul className="space-y-4">
                    {unassigned.map((bullet) => (
                      <li key={bullet.id ?? `${bullet.action}-${bullet.metric}`} className="border-t border-line pt-4">
                        <XyzBullet bullet={bullet} />
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}
            </div>
          )}

          <GenerationChat resumeId={resume.id} />
        </Card>

        <div className="space-y-6">
          <Card title="Compiled PDF" eyebrow="Preview">
            {resume.current_version > 0 ? (
              <iframe
                title={`Rendered PDF for ${resume.title}`}
                src={`/api/resumes/${encodeURIComponent(resume.id)}/pdf?v=${String(resume.current_version)}`}
                className="h-[720px] w-full rounded-card border border-line bg-surface"
              />
            ) : (
              <EmptyState
                title="No PDF yet"
                body="Render a version to preview the compiled resume in this pane."
              />
            )}
          </Card>
          <AtsScorePanel resumeVersionId={versionId} jobId={resume.target_job_id} />
        </div>
      </div>
    </div>
  );
}
