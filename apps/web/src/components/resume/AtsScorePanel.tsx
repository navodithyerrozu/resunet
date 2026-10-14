"use client";

import { useState } from "react";

import { Card, Chip, Meter, buttonClass } from "@/components/ui/primitives";

import type { ATSScore, FindingSeverity, ScoreDimension } from "@resunet/shared";
import type { ChipTone } from "@/components/ui/primitives";

const DIMENSION_LABELS: Record<ScoreDimension, string> = {
  keyword_match: "Keyword match",
  parseability: "Parseability",
  impact_quantification: "Impact",
  section_structure: "Structure",
  length_density: "Length",
  contact_completeness: "Contact",
  date_consistency: "Dates",
};

const SEVERITY_TONE: Record<FindingSeverity, ChipTone> = {
  critical: "danger",
  warning: "warn",
  info: "neutral",
};

function gradeTone(grade: string): ChipTone {
  if (grade.startsWith("A")) return "ok";
  if (grade.startsWith("B")) return "accent";
  if (grade.startsWith("C")) return "warn";
  return "danger";
}

export function AtsScorePanel({
  resumeVersionId,
  jobId,
}: {
  resumeVersionId: string | null;
  jobId: string | null;
}) {
  const [score, setScore] = useState<ATSScore | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (pending || resumeVersionId === null) return;
    setPending(true);
    setError(null);
    try {
      const payload: { resumeVersionId: string; jobId?: string } = { resumeVersionId };
      if (jobId !== null) payload.jobId = jobId;
      const response = await fetch("/api/ats/score", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
        setError(typeof body?.detail === "string" ? body.detail : "Scoring failed.");
        return;
      }
      setScore((await response.json()) as ATSScore);
    } catch {
      setError("Could not reach the scorer.");
    } finally {
      setPending(false);
    }
  }

  const keywords = score?.keyword_analysis;

  return (
    <Card
      title="ATS score"
      eyebrow="Inspector"
      aside={
        <button
          type="button"
          onClick={() => void run()}
          disabled={pending || resumeVersionId === null}
          className={buttonClass("secondary")}
        >
          {pending ? "Scoring…" : score === null ? "Run score" : "Refresh"}
        </button>
      }
    >
      {resumeVersionId === null ? (
        <p className="px-5 py-4 text-sm text-muted">Render a version before scoring. There is nothing to extract yet.</p>
      ) : null}

      {error !== null ? (
        <p role="alert" className="px-5 py-2 text-sm text-danger">
          {error}
        </p>
      ) : null}

      {score !== null ? (
        <div className="space-y-4 px-5 py-4">
          <div className="flex items-end justify-between gap-4">
            <div>
              <p className="eyebrow">Overall</p>
              <p className="mt-1 font-mono text-2xl tabular-nums" data-numeric>
                {Math.round(score.overall)}
              </p>
            </div>
            <Chip tone={gradeTone(score.grade)} mono>
              {score.grade}
            </Chip>
          </div>

          <ul className="space-y-2">
            {score.dimensions.map((dimension) => (
              <li key={dimension.dimension}>
                <div className="mb-1 flex items-baseline justify-between gap-2">
                  <span className="text-xs text-muted">{DIMENSION_LABELS[dimension.dimension]}</span>
                  {dimension.detail !== null ? (
                    <span className="truncate text-[0.6875rem] text-faint">{dimension.detail}</span>
                  ) : null}
                </div>
                <Meter value={dimension.score} label={DIMENSION_LABELS[dimension.dimension]} />
              </li>
            ))}
          </ul>

          {keywords !== null && keywords !== undefined ? (
            <div className="space-y-2">
              <p className="eyebrow">Keywords</p>
              <KeywordRow label="Required present" items={keywords.required_present} tone="ok" />
              <KeywordRow label="Required missing" items={keywords.required_missing} tone="danger" />
              <KeywordRow label="Preferred present" items={keywords.preferred_present} tone="accent" />
              <KeywordRow label="Preferred missing" items={keywords.preferred_missing} tone="warn" />
              {keywords.stuffing_warnings.length > 0 ? (
                <KeywordRow label="Stuffing" items={keywords.stuffing_warnings} tone="warn" />
              ) : null}
              <p className="font-mono text-xs tabular-nums text-muted" data-numeric>
                Required coverage {Math.round(keywords.required_coverage * 100)}%
              </p>
            </div>
          ) : null}

          <ul className="space-y-2">
            {score.findings.map((finding) => (
              <li
                key={finding.code}
                className="rounded-card border border-line px-3 py-2"
                data-severity={finding.severity}
              >
                <div className="flex items-center gap-2">
                  <Chip tone={SEVERITY_TONE[finding.severity]}>{finding.severity}</Chip>
                  <span className="text-sm font-500">{finding.message}</span>
                </div>
                {finding.suggestion !== null ? (
                  <p className="mt-1 text-xs text-muted">{finding.suggestion}</p>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : resumeVersionId !== null ? (
        <p className="px-5 py-4 text-sm text-muted">Run a score against the compiled PDF, not the editor model.</p>
      ) : null}
    </Card>
  );
}

function KeywordRow({
  label,
  items,
  tone,
}: {
  label: string;
  items: string[];
  tone: ChipTone;
}) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="mb-1 text-[0.6875rem] uppercase tracking-wider text-faint">{label}</p>
      <div className="flex flex-wrap gap-1">
        {items.map((item) => (
          <Chip key={item} tone={tone}>
            {item}
          </Chip>
        ))}
      </div>
    </div>
  );
}
