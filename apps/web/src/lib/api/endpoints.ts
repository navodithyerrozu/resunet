import "server-only";

import { apiFetch, apiFetchBinary } from "./client";

import type {
  ATSScore,
  Application,
  ApplicationStatus,
  ApplicationSummary,
  Bullet,
  GenerateStartRequest,
  GenerateTurnResponse,
  JobSummary,
  Page,
  RenderJob,
  RenderRequest,
  Resume,
  ResumeCreate,
  ResumeSummary,
  ResumeUpdate,
  ResumeVersion,
  ScoreRequest,
  SkillCount,
  TokenPair,
  UserCreate,
  UserLogin,
  UserPublic,
} from "@resunet/shared";

/**
 * One function per endpoint the UI calls.
 *
 * The value of this file is not the wrapping -- it is that every return type is
 * an alias into the generated OpenAPI types. Rename a field in a Pydantic model
 * and this file is where the build breaks, with the endpoint named, instead of
 * the frontend rendering `undefined` in production.
 *
 * Note the casing split, which is real and comes from the backend: full
 * documents (`Resume`, `Bullet`, `Application`) serialize snake_case because
 * they extend `MongoModel`, while DTOs (`ResumeSummary`, `Page`, `TokenPair`)
 * serialize camelCase because `ApiModel` sets a camel alias generator. Nothing
 * here normalizes that -- see the header of `generated/api.ts` for why, and for
 * where the fix belongs.
 */

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export function registerAccount(payload: UserCreate): Promise<TokenPair> {
  return apiFetch<TokenPair>("/auth/register", {
    method: "POST",
    body: payload,
    authenticated: false,
  });
}

/** Exchange credentials for a token pair. Sends no bearer token by design. */
export function login(credentials: UserLogin): Promise<TokenPair> {
  return apiFetch<TokenPair>("/auth/login", {
    method: "POST",
    body: credentials,
    authenticated: false,
  });
}

export function logout(): Promise<void> {
  return apiFetch<void>("/auth/logout", { method: "POST" });
}

export function currentUser(): Promise<UserPublic> {
  return apiFetch<UserPublic>("/auth/me");
}

// ---------------------------------------------------------------------------
// Resumes
// ---------------------------------------------------------------------------

export function listResumes(
  params: { limit?: number; offset?: number } = {},
): Promise<Page<ResumeSummary>> {
  return apiFetch<Page<ResumeSummary>>("/resumes", {
    query: { limit: params.limit ?? 20, offset: params.offset ?? 0 },
    tags: ["resumes"],
  });
}

export function getResume(resumeId: string): Promise<Resume> {
  return apiFetch<Resume>(`/resumes/${encodeURIComponent(resumeId)}`, {
    tags: ["resumes", `resume:${resumeId}`],
  });
}

export function createResume(payload: ResumeCreate): Promise<Resume> {
  return apiFetch<Resume>("/resumes", { method: "POST", body: payload });
}

export function updateResume(resumeId: string, payload: ResumeUpdate): Promise<Resume> {
  return apiFetch<Resume>(`/resumes/${encodeURIComponent(resumeId)}`, {
    method: "PATCH",
    body: payload,
  });
}

export function archiveResume(resumeId: string): Promise<void> {
  return apiFetch<void>(`/resumes/${encodeURIComponent(resumeId)}`, { method: "DELETE" });
}

export function listBullets(
  resumeId: string,
  params: { limit?: number; offset?: number } = {},
): Promise<Page<Bullet>> {
  return apiFetch<Page<Bullet>>(`/resumes/${encodeURIComponent(resumeId)}/bullets`, {
    query: { limit: params.limit ?? 100, offset: params.offset ?? 0 },
    tags: [`resume:${resumeId}:bullets`],
  });
}

/**
 * All bullets for a resume, across pages.
 *
 * The backend caps a single page at 100 items, so asking for 200 in one
 * request is rejected with a 422. Callers that need the full set (the
 * dashboard bullet counts, the workspace editor) page through with the
 * maximum allowed page size instead of assuming one page holds everything.
 */
export async function listAllBullets(resumeId: string): Promise<Page<Bullet>> {
  const PAGE_SIZE = 100;
  const first = await listBullets(resumeId, { limit: PAGE_SIZE, offset: 0 });
  const items = [...first.items];
  while (items.length < first.total) {
    const page = await listBullets(resumeId, { limit: PAGE_SIZE, offset: items.length });
    if (page.items.length === 0) break;
    items.push(...page.items);
  }
  return { items, total: first.total, limit: items.length, offset: 0 };
}

/**
 * Queue a LaTeX compile. Returns immediately with a job to poll.
 *
 * The API enqueues onto the render worker rather than compiling inline, so a
 * 202 here means "accepted", not "rendered". The editor polls
 * `getRenderJob` until the status leaves `queued`/`rendering`.
 */
export function requestRender(resumeId: string, payload: RenderRequest = {}): Promise<RenderJob> {
  return apiFetch<RenderJob>(`/resumes/${encodeURIComponent(resumeId)}/render`, {
    method: "POST",
    body: payload,
  });
}

export function getRenderJob(jobId: string): Promise<RenderJob> {
  return apiFetch<RenderJob>(`/resumes/render-jobs/${encodeURIComponent(jobId)}`);
}

export function listResumeVersions(
  resumeId: string,
  params: { limit?: number; offset?: number } = {},
): Promise<Page<ResumeVersion>> {
  return apiFetch<Page<ResumeVersion>>(
    `/resumes/${encodeURIComponent(resumeId)}/versions`,
    {
      query: { limit: params.limit ?? 20, offset: params.offset ?? 0 },
      tags: [`resume:${resumeId}`],
    },
  );
}

export function streamResumePdf(resumeId: string, version?: number): Promise<Response> {
  return apiFetchBinary(`/resumes/${encodeURIComponent(resumeId)}/pdf`, {
    query: { v: version },
  });
}

// ---------------------------------------------------------------------------
// Generation agent
// ---------------------------------------------------------------------------

export function startGeneration(payload: GenerateStartRequest): Promise<GenerateTurnResponse> {
  return apiFetch<GenerateTurnResponse>("/resumes/generate/start", {
    method: "POST",
    body: payload,
    // The first turn runs several LangGraph nodes and at least one model call,
    // so it needs a longer deadline than a plain read.
    timeoutMs: 60_000,
  });
}

export function sendGenerationTurn(
  threadId: string,
  message: string,
): Promise<GenerateTurnResponse> {
  return apiFetch<GenerateTurnResponse>(
    `/resumes/generate/${encodeURIComponent(threadId)}/turn`,
    { method: "POST", body: { message }, timeoutMs: 60_000 },
  );
}

// ---------------------------------------------------------------------------
// ATS
// ---------------------------------------------------------------------------

export function scoreVersion(payload: ScoreRequest): Promise<ATSScore> {
  return apiFetch<ATSScore>("/ats/score", { method: "POST", body: payload });
}

// ---------------------------------------------------------------------------
// Applications
// ---------------------------------------------------------------------------

export function listApplications(
  params: { status?: ApplicationStatus; limit?: number; offset?: number } = {},
): Promise<Page<ApplicationSummary>> {
  return apiFetch<Page<ApplicationSummary>>("/applications", {
    query: {
      status: params.status,
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
    tags: ["applications"],
  });
}

export function getApplication(applicationId: string): Promise<Application> {
  return apiFetch<Application>(`/applications/${encodeURIComponent(applicationId)}`, {
    tags: ["applications", `application:${applicationId}`],
  });
}

/**
 * Approve a paused submission.
 *
 * This is the human gate in the automation flow: the worker filled the form,
 * captured a screenshot, closed the browser, and stopped. Approving enqueues a
 * fresh job that replays from navigation with `approved=true`. The worker
 * re-checks the form's field fingerprint before clicking, so an approval is
 * void if the form changed in the meantime.
 */
export function approveApplication(applicationId: string): Promise<Application> {
  return apiFetch<Application>(`/applications/${encodeURIComponent(applicationId)}/approve`, {
    method: "POST",
  });
}

export function retryApplication(applicationId: string): Promise<Application> {
  return apiFetch<Application>(`/applications/${encodeURIComponent(applicationId)}/retry`, {
    method: "POST",
  });
}

/** Counts per pipeline stage, for the dashboard funnel. */
export function funnelStats(): Promise<Record<string, number>> {
  return apiFetch<Record<string, number>>("/applications/stats/funnel", {
    tags: ["applications"],
  });
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

export function listJobs(
  params: { limit?: number; offset?: number } = {},
): Promise<Page<JobSummary>> {
  return apiFetch<Page<JobSummary>>("/jobs", {
    query: { limit: params.limit ?? 20, offset: params.offset ?? 0 },
    tags: ["jobs"],
  });
}

export function topRecommendations(): Promise<JobSummary[]> {
  return apiFetch<JobSummary[]>("/jobs/recommendations/top", { tags: ["jobs"] });
}

export function listProfileSkills(): Promise<SkillCount[]> {
  return apiFetch<SkillCount[]>("/profile/skills", { tags: ["profile"] });
}
