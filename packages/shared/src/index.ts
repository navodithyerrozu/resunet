/**
 * The web app's view of the API contract.
 *
 * Every type here is an *alias into* `components["schemas"]` rather than a
 * hand-written copy. That indirection is the whole point: if a field is renamed
 * or a schema disappears on the backend, the next codegen run changes
 * `generated/api.ts` and these aliases stop compiling. A hand-maintained mirror
 * would keep compiling and silently disagree with the server, which is the
 * failure mode this package exists to prevent.
 *
 * Import from `@resunet/shared`, never from `@resunet/shared/src/generated/api`
 * -- the aliases are the supported surface, and going around them means a
 * schema rename shows up as a runtime `undefined` instead of a red squiggle.
 */

import type { components, paths } from "./generated/api";

export type { components, paths };

type Schemas = components["schemas"];

// ---------------------------------------------------------------------------
// Documents and DTOs
// ---------------------------------------------------------------------------

export type Resume = Schemas["Resume"];
export type ResumeSummary = Schemas["ResumeSummary"];
export type ResumeCreate = Schemas["ResumeCreate"];
export type ResumeUpdate = Schemas["ResumeUpdate"];
export type ResumeSection = Schemas["ResumeSection"];
export type ContactBlock = Schemas["ContactBlock"];
export type CareerProfile = Schemas["CareerProfile"];
export type ExperienceEntry = Schemas["ExperienceEntry"];
export type ProjectEntry = Schemas["ProjectEntry"];
export type EducationEntry = Schemas["EducationEntry"];
export type Skill = Schemas["Skill"];
export type Certification = Schemas["Certification"];
export type DateRange = Schemas["DateRange"];
export type EmploymentType = Schemas["EmploymentType"];
export type SkillProficiency = Schemas["SkillProficiency"];

export type Bullet = Schemas["Bullet"];
export type BulletProposal = Schemas["BulletProposal"];
export type XYZViolation = Schemas["XYZViolation"];

export type RenderJob = Schemas["RenderJob"];
export type RenderRequest = Schemas["RenderRequest"];

export type ATSScore = Schemas["ATSScore"];
export type ATSFinding = Schemas["ATSFinding"];
export type DimensionScore = Schemas["DimensionScore"];
export type KeywordAnalysis = Schemas["KeywordAnalysis"];
export type ScoreRequest = Schemas["ScoreRequest"];
export type ScoreSubmission = Schemas["ScoreSubmission"];

export type Application = Schemas["Application"];
export type ApplicationCreate = Schemas["ApplicationCreate"];
export type ApplicationSummary = Schemas["ApplicationSummary"];
export type SubmissionAttempt = Schemas["SubmissionAttempt"];
export type ApplicationEvent = Schemas["ApplicationEvent"];

export type JobSummary = Schemas["JobSummary"];
export type Job = Schemas["Job"];
export type JobIngestRequest = Schemas["JobIngestRequest"];

export type GenerateStartRequest = Schemas["GenerateStartRequest"];
export type GenerateTurnRequest = Schemas["GenerateTurnRequest"];
export type GenerateTurnResponse = Schemas["GenerateTurnResponse"];
export type ProposalDecision = Schemas["ProposalDecision"];

export type TokenPair = Schemas["TokenPair"];
export type UserCreate = Schemas["UserCreate"];
export type UserLogin = Schemas["UserLogin"];
export type UserPublic = Schemas["UserPublic"];
export type ErrorBody = Schemas["ErrorBody"];
export type ResumeVersion = Schemas["ResumeVersion"];
export type SkillCount = Schemas["SkillCount"];
export type FieldMapping = Schemas["FieldMapping"];
export type ScoreDimension = Schemas["ScoreDimension"];
export type FindingSeverity = Schemas["FindingSeverity"];

// ---------------------------------------------------------------------------
// Enum unions
// ---------------------------------------------------------------------------

export type ResumeStatus = Schemas["ResumeStatus"];
export type ResumeTemplate = Schemas["ResumeTemplate"];
export type RenderJobStatus = Schemas["RenderJobStatus"];
export type BulletStatus = Schemas["BulletStatus"];
export type BulletSource = Schemas["BulletSource"];
export type XYZViolationCode = Schemas["XYZViolationCode"];
export type ApplicationStatus = Schemas["ApplicationStatus"];
export type SubmissionMethod = Schemas["SubmissionMethod"];
export type ThreadStatus = Schemas["ThreadStatus"];
export type SectionKind = Schemas["SectionKind"];

/** A paginated envelope. Mirrors the backend's generic `Page[T]`. */
export type Page<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};

// ---------------------------------------------------------------------------
// Presentation metadata for application status
// ---------------------------------------------------------------------------

/**
 * Where each status sits in the pipeline, and whether it is a resting state.
 *
 * This is a `Record` over the full union rather than a lookup with a fallback,
 * so adding a status to the backend enum breaks the build here until someone
 * decides how it should render. A `??  "unknown"` default would instead quietly
 * drop new statuses into a grey pill forever.
 *
 * `rank` drives the stage rail in the tracker. Ordering is real information
 * here -- an application genuinely moves draft -> queued -> submitted -> offer --
 * which is why the UI is allowed to number these at all. `terminal` marks the
 * states nothing advances out of; `needsUser` marks the ones that are stuck
 * until a human does something, which is what the dashboard surfaces first.
 */
export const APPLICATION_STAGES: Record<
  ApplicationStatus,
  { rank: number; label: string; terminal: boolean; needsUser: boolean }
> = {
  draft: { rank: 0, label: "Draft", terminal: false, needsUser: true },
  queued: { rank: 1, label: "Queued", terminal: false, needsUser: false },
  submitting: { rank: 2, label: "Submitting", terminal: false, needsUser: false },
  awaiting_approval: {
    rank: 2,
    label: "Waiting on you",
    terminal: false,
    needsUser: true,
  },
  submitted: { rank: 3, label: "Submitted", terminal: false, needsUser: false },
  acknowledged: { rank: 4, label: "Acknowledged", terminal: false, needsUser: false },
  screening: { rank: 5, label: "Screening", terminal: false, needsUser: false },
  interviewing: { rank: 6, label: "Interviewing", terminal: false, needsUser: true },
  offer: { rank: 7, label: "Offer", terminal: true, needsUser: true },
  failed: { rank: 3, label: "Failed", terminal: false, needsUser: true },
  blocked: { rank: 3, label: "Blocked", terminal: false, needsUser: true },
  needs_review: { rank: 3, label: "Needs review", terminal: false, needsUser: true },
  rejected: { rank: 8, label: "Rejected", terminal: true, needsUser: false },
  withdrawn: { rank: 8, label: "Withdrawn", terminal: true, needsUser: false },
  ghosted: { rank: 8, label: "Ghosted", terminal: true, needsUser: false },
};

/** The ordered rail shown in the tracker. Excludes off-path and terminal states. */
export const PIPELINE_RAIL: readonly ApplicationStatus[] = [
  "draft",
  "queued",
  "submitting",
  "submitted",
  "acknowledged",
  "screening",
  "interviewing",
  "offer",
] as const;

/**
 * Human-readable copy for each violation code.
 *
 * The backend already sends `message` and `hint` per violation, and those are
 * what the UI should display -- they are written against the specific bullet.
 * This map is only for places that have a bare code and no violation object,
 * such as a filter chip or an aggregate count.
 */
export const VIOLATION_LABELS: Record<XYZViolationCode, string> = {
  missing_metric: "No number",
  vague_metric: "Thin metric",
  missing_method: "No method",
  weak_verb: "Weak verb",
  first_person: "First person",
  too_long: "Too long",
  past_tense_required: "Wrong tense",
};

/**
 * True when a bullet satisfies XYZ.
 *
 * Deliberately derived from `violations` rather than recomputed in TypeScript.
 * The validator is `BulletDraft.validate_xyz()` in Python and it must stay the
 * only implementation -- a second copy in the browser would drift, and the
 * server's answer is the one that gates persistence.
 */
export function isCompliant(bullet: Pick<Bullet, "violations">): boolean {
  return bullet.violations.length === 0;
}

/** Assemble the display line from its parts, matching the backend's `text`. */
export function bulletText(parts: Pick<Bullet, "action" | "metric" | "method">): string {
  return `${parts.action} ${parts.metric} ${parts.method}.`.replace(/\s{2,}/g, " ");
}
