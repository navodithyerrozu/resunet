import Link from "next/link";

import { APPLICATION_STAGES, PIPELINE_RAIL } from "@resunet/shared";

import { ApplicationAction } from "@/components/application/ApplicationAction";
import { ApplicationFocus } from "@/components/application/ApplicationFocus";
import { StatusChip } from "@/components/application/StatusChip";
import { Card, Chip, EmptyState } from "@/components/ui/primitives";
import { Timestamp } from "@/components/ui/Timestamp";
import { withFreshSession } from "@/lib/api/client";
import { funnelStats, getApplication, listApplications } from "@/lib/api/endpoints";
import { ApiError } from "@/lib/api/errors";
import { pluralize } from "@/lib/format";

import type { Metadata } from "next";
import type { ApplicationStatus } from "@resunet/shared";

export const metadata: Metadata = { title: "Applications" };

const OFF_PATH: readonly ApplicationStatus[] = [
  "awaiting_approval",
  "failed",
  "rejected",
  "withdrawn",
];

type SearchParams = Promise<{ status?: string; focus?: string }>;

function isStatus(value: string | undefined): value is ApplicationStatus {
  return value !== undefined && value in APPLICATION_STAGES;
}

function actionFor(status: ApplicationStatus): "approve" | "retry" | null {
  if (status === "awaiting_approval") return "approve";
  if (status === "failed") return "retry";
  return null;
}

function trackerHref(status?: ApplicationStatus, focus?: string): string {
  const params = new URLSearchParams();
  if (status !== undefined) params.set("status", status);
  if (focus !== undefined && focus.length > 0) params.set("focus", focus);
  const query = params.toString();
  return query.length > 0 ? `/applications?${query}` : "/applications";
}

export default async function ApplicationsPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const params = await searchParams;
  const status = isStatus(params.status) ? params.status : undefined;
  const focus = params.focus !== undefined && params.focus.length > 0 ? params.focus : undefined;

  const listQuery: { limit: number; status?: ApplicationStatus } = { limit: 50 };
  if (status !== undefined) listQuery.status = status;

  const [apps, funnel, focused] = await withFreshSession(() =>
    Promise.all([
      listApplications(listQuery),
      funnelStats(),
      focus !== undefined
        ? getApplication(focus).catch((error: unknown) => {
            if (error instanceof ApiError && error.status === 404) return null;
            throw error;
          })
        : Promise.resolve(null),
    ]),
  );

  const focusedSummary =
    focused !== null ? (apps.items.find((item) => item.id === focused.id) ?? null) : null;

  return (
    <div className="space-y-8">
      <header>
        <p className="eyebrow">Tracker</p>
        <h1 className="mt-1.5 text-title">Applications</h1>
      </header>

      <Card title="Stage rail" eyebrow="Filter" aside={`${apps.total} shown`}>
        <div className="flex flex-wrap gap-2 px-5 py-4">
          <FilterChip href={trackerHref(undefined, focus)} active={status === undefined} count={undefined}>
            All
          </FilterChip>
          {PIPELINE_RAIL.map((stage) => (
            <FilterChip
              key={stage}
              href={trackerHref(stage, focus)}
              active={status === stage}
              count={funnel[stage] ?? 0}
            >
              {APPLICATION_STAGES[stage].label}
            </FilterChip>
          ))}
          {OFF_PATH.map((stage) => (
            <FilterChip
              key={stage}
              href={trackerHref(stage, focus)}
              active={status === stage}
              count={funnel[stage] ?? 0}
            >
              {APPLICATION_STAGES[stage].label}
            </FilterChip>
          ))}
        </div>
      </Card>

      <div className={`grid gap-6 ${focused !== null ? "lg:grid-cols-[minmax(0,1fr)_minmax(20rem,24rem)]" : ""}`}>
        <Card title="Queue" eyebrow="Applications" aside={pluralize(apps.items.length, "row")}>
          {apps.items.length === 0 ? (
            <EmptyState
              title="No applications in this view"
              body="Queued and submitted roles appear here. Change the stage filter to look elsewhere."
            />
          ) : (
            <ul className="divide-y divide-line">
              {apps.items.map((app) => {
                const action = actionFor(app.status);
                const selected = focus === app.id;
                return (
                  <li
                    key={app.id}
                    className={`flex flex-wrap items-center justify-between gap-4 px-5 py-3.5 ${
                      selected ? "bg-sunken" : ""
                    }`}
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <Link
                          href={trackerHref(status, app.id)}
                          className="truncate text-sm font-500 hover:text-accent"
                        >
                          {app.jobTitle}
                        </Link>
                        <StatusChip status={app.status} />
                      </div>
                      <p className="mt-0.5 text-xs text-muted">
                        {app.companyName} &middot; {app.method}
                        {" · "}
                        <span className="font-mono tabular-nums" data-numeric>
                          {pluralize(app.attemptCount, "attempt")}
                        </span>
                        {" · "}
                        submitted <Timestamp iso={app.submittedAt} />
                        {" · "}
                        updated <Timestamp iso={app.updatedAt} />
                      </p>
                    </div>
                    {action !== null ? (
                      <ApplicationAction applicationId={app.id} action={action} />
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        {focused !== null ? (
          <ApplicationFocus application={focused} summary={focusedSummary} />
        ) : null}
      </div>
    </div>
  );
}

function FilterChip({
  href,
  active,
  count,
  children,
}: {
  href: string;
  active: boolean;
  count: number | undefined;
  children: string;
}) {
  return (
    <Link href={href} aria-current={active ? "page" : undefined}>
      <Chip tone={active ? "accent" : "neutral"} mono={count !== undefined}>
        {children}
        {count !== undefined ? (
          <>
            {" "}
            <span className="font-mono tabular-nums" data-numeric>
              {count}
            </span>
          </>
        ) : null}
      </Chip>
    </Link>
  );
}
