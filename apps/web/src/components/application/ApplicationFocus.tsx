import { StatusChip } from "@/components/application/StatusChip";
import { ApplicationStatusForm } from "@/components/application/ApplicationStatusForm";
import { Card, Chip } from "@/components/ui/primitives";
import { Timestamp } from "@/components/ui/Timestamp";
import { duration } from "@/lib/format";

import type { Application, ApplicationSummary } from "@resunet/shared";

const OUTCOME_TONE = {
  success: "ok",
  retryable_failure: "warn",
  permanent_failure: "danger",
  blocked: "warn",
  needs_human: "accent",
} as const;

export function ApplicationFocus({
  application,
  summary,
}: {
  application: Application;
  summary: ApplicationSummary | null;
}) {
  return (
    <Card
      title={summary?.jobTitle ?? "Application"}
      eyebrow="Detail"
      aside={<StatusChip status={application.status} />}
    >
      <div className="space-y-5 px-5 py-4">
        <ApplicationStatusForm applicationId={application.id ?? ""} current={application.status} />
        <dl className="grid gap-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="eyebrow">Company</dt>
            <dd className="mt-1">{summary?.companyName ?? application.job_id}</dd>
          </div>
          <div>
            <dt className="eyebrow">Method</dt>
            <dd className="mt-1">{application.method}</dd>
          </div>
          <div>
            <dt className="eyebrow">Submitted</dt>
            <dd className="mt-1">
              <Timestamp iso={application.submitted_at} />
            </dd>
          </div>
          <div>
            <dt className="eyebrow">Attempts</dt>
            <dd className="mt-1 font-mono tabular-nums" data-numeric>
              {application.attempts.length}
            </dd>
          </div>
          {application.confirmation_id !== null ? (
            <div className="sm:col-span-2">
              <dt className="eyebrow">Confirmation</dt>
              <dd className="mt-1 font-mono text-xs">{application.confirmation_id}</dd>
            </div>
          ) : null}
          {application.notes !== null ? (
            <div className="sm:col-span-2">
              <dt className="eyebrow">Notes</dt>
              <dd className="mt-1 text-muted">{application.notes}</dd>
            </div>
          ) : null}
        </dl>

        <section>
          <h3 className="eyebrow">Timeline</h3>
          {application.events.length === 0 ? (
            <p className="mt-2 text-sm text-faint">No events recorded.</p>
          ) : (
            <ol className="mt-2 divide-y divide-line rounded-card border border-line">
              {application.events.map((event, index) => (
                <li key={`${event.at}-${event.kind}-${String(index)}`} className="px-3 py-2">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="text-sm font-500">{event.kind}</span>
                    <Timestamp iso={event.at} className="text-xs text-muted" />
                  </div>
                  <p className="mt-0.5 text-sm text-muted">{event.summary}</p>
                </li>
              ))}
            </ol>
          )}
        </section>

        <section>
          <h3 className="eyebrow">Submission attempts</h3>
          {application.attempts.length === 0 ? (
            <p className="mt-2 text-sm text-faint">No attempts yet.</p>
          ) : (
            <ul className="mt-2 space-y-3">
              {application.attempts.map((attempt) => (
                <li
                  key={attempt.attempt_number}
                  className="rounded-card border border-line px-3 py-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-mono text-xs tabular-nums" data-numeric>
                      Attempt {attempt.attempt_number}
                    </span>
                    {attempt.outcome !== null ? (
                      <Chip tone={OUTCOME_TONE[attempt.outcome]}>{attempt.outcome}</Chip>
                    ) : (
                      <Chip>in progress</Chip>
                    )}
                  </div>
                  <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
                    <div>
                      <dt className="text-faint">Block reason</dt>
                      <dd className="mt-0.5">{attempt.block_reason ?? "—"}</dd>
                    </div>
                    <div>
                      <dt className="text-faint">Failed step</dt>
                      <dd className="mt-0.5 font-mono">{attempt.failed_step ?? "—"}</dd>
                    </div>
                    <div>
                      <dt className="text-faint">Duration</dt>
                      <dd className="mt-0.5 font-mono tabular-nums" data-numeric>
                        {duration(attempt.duration_ms)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-faint">Finished</dt>
                      <dd className="mt-0.5">
                        <Timestamp iso={attempt.finished_at} />
                      </dd>
                    </div>
                  </dl>
                  {attempt.error_message !== null ? (
                    <p className="mt-2 text-xs text-danger">{attempt.error_message}</p>
                  ) : null}
                  {attempt.field_mappings.length > 0 ? (
                    <div className="mt-3 overflow-x-auto">
                      <table className="w-full text-left text-xs">
                        <thead>
                          <tr className="border-b border-line text-faint">
                            <th className="py-1 pr-3 font-500">Selector</th>
                            <th className="py-1 pr-3 font-500">Label</th>
                            <th className="py-1 pr-3 font-500">Value</th>
                            <th className="py-1 font-500">Confidence</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-line">
                          {attempt.field_mappings.map((mapping) => (
                            <tr key={`${mapping.selector}-${mapping.value_preview}`}>
                              <td className="py-1.5 pr-3 font-mono">{mapping.selector}</td>
                              <td className="py-1.5 pr-3">{mapping.label ?? "—"}</td>
                              <td className="py-1.5 pr-3">{mapping.value_preview}</td>
                              <td className="py-1.5 font-mono tabular-nums" data-numeric>
                                {Math.round(mapping.confidence * 100)}%
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </Card>
  );
}
