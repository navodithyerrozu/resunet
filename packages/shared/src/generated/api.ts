/**
 * GENERATED FILE -- DO NOT EDIT THE SCHEMA BLOCK BY HAND.
 *
 * PROVENANCE, stated plainly because it is easy to get wrong
 * ---------------------------------------------------------
 * The block between the BEGIN/END markers below is emitted by
 * `scripts/gen_types_offline.py`, which reads `apps/api/app/models/*.py` with
 * `ast` and derives these shapes statically. It has never been through the real
 * generator. `scripts/gen-types.sh` -- which imports `app.main:app`, calls
 * `.openapi()`, and pipes the result through `openapi-typescript` -- is the
 * authority, and the first time it runs its output supersedes this. Expect it to
 * differ: FastAPI splits Pydantic v2 models into `-Input`/`-Output` schemas when
 * defaults make the shapes diverge, and the offline emitter deliberately
 * collapses that (see its docstring).
 *
 * Everything outside the markers -- `paths`, the error envelope, the `Page_*_`
 * instantiations -- is hand-maintained. `Page[T]` is a generic that OpenAPI can
 * only express as one concrete schema per instantiation, and the error envelope
 * is assembled in `app/core/errors.py` rather than by a Pydantic model, so
 * neither has a class for the emitter to read.
 *
 * The file is committed on purpose: a fresh clone typechecks without a running
 * API, and CI runs `gen_types_offline.py --check` to turn model drift into a
 * build failure.
 *
 * WART 1: the wire format is not uniformly cased
 * ---------------------------------------------
 * Three base models back the responses, and they disagree.
 *
 *   ApiModel   -> `alias_generator=to_camel`, so DTOs serialize camelCase:
 *                 ResumeSummary, ApplicationSummary, TokenPair, Page, ...
 *   MongoModel -> no alias generator; its docstring calls these fields "the
 *                 stored shape", so documents serialize snake_case:
 *                 Resume, Bullet, Application, ATSScore, RenderJob.
 *   AppModel   -> also no alias generator, so *nested value objects* are
 *                 snake_case too: ContactBlock, ResumeSection, DimensionScore,
 *                 KeywordAnalysis, SubmissionAttempt, ApplicationEvent.
 *
 * The third tier is the one that bites: `ATSScore.keyword_analysis` is
 * snake_case inside a snake_case document, but `GenerateTurnResponse.proposals`
 * is camelCase containing a `draft` whose own fields are camelCase and a
 * `violations` list that is camelCase -- while a `Resume.sections[].entry_ids`
 * two calls away is snake. FastAPI defaults to `response_model_by_alias=True`,
 * so all three go out as written.
 *
 * The client does NOT normalize this. A casing shim would hide the split and
 * make these types a lie about what arrives on the wire. The fix belongs on the
 * backend: give AppModel the camel alias generator and have MongoModel override
 * only its storage serialization, so one rule covers the whole API.
 *
 * WART 2: the error envelope has two shapes
 * -----------------------------------------
 * `app/core/errors.py` builds `{"error": {"code", "message", "details"?}}` and
 * its docstring says the frontend should switch on `code`, never on prose. But
 * FastAPI's own `HTTPException` and request-validation failures emit
 * `{"detail": ...}`, and those paths are still reachable. `ErrorBody` below is
 * the union of both, and `lib/api/errors.ts` parses either.
 */

export type paths = {
  "/api/v1/auth/register": {
    post: {
      requestBody: { content: { "application/json": components["schemas"]["UserCreate"] } };
      responses: {
        201: { content: { "application/json": components["schemas"]["TokenPair"] } };
        409: { content: { "application/json": components["schemas"]["ErrorBody"] } };
      };
    };
  };
  "/api/v1/auth/login": {
    post: {
      requestBody: { content: { "application/json": components["schemas"]["UserLogin"] } };
      responses: {
        200: { content: { "application/json": components["schemas"]["TokenPair"] } };
        401: { content: { "application/json": components["schemas"]["ErrorBody"] } };
      };
    };
  };
  "/api/v1/auth/refresh": {
    post: {
      requestBody: { content: { "application/json": { refresh_token: string } } };
      responses: {
        200: { content: { "application/json": components["schemas"]["TokenPair"] } };
        401: { content: { "application/json": components["schemas"]["ErrorBody"] } };
      };
    };
  };
  "/api/v1/auth/logout": {
    post: { responses: { 204: { content: never } } };
  };
  "/api/v1/auth/me": {
    get: {
      responses: { 200: { content: { "application/json": components["schemas"]["UserPublic"] } } };
    };
  };
  "/api/v1/resumes": {
    get: {
      parameters: { query?: { limit?: number; offset?: number } };
      responses: {
        200: { content: { "application/json": components["schemas"]["Page_ResumeSummary_"] } };
      };
    };
    post: {
      requestBody: { content: { "application/json": components["schemas"]["ResumeCreate"] } };
      responses: {
        201: { content: { "application/json": components["schemas"]["Resume"] } };
      };
    };
  };
  "/api/v1/resumes/{resume_id}": {
    get: {
      parameters: { path: { resume_id: string } };
      responses: { 200: { content: { "application/json": components["schemas"]["Resume"] } } };
    };
    patch: {
      parameters: { path: { resume_id: string } };
      requestBody: { content: { "application/json": components["schemas"]["ResumeUpdate"] } };
      responses: { 200: { content: { "application/json": components["schemas"]["Resume"] } } };
    };
    delete: {
      parameters: { path: { resume_id: string } };
      responses: { 204: { content: never } };
    };
  };
  "/api/v1/resumes/{resume_id}/bullets": {
    get: {
      parameters: { path: { resume_id: string }; query?: { limit?: number; offset?: number } };
      responses: {
        200: { content: { "application/json": components["schemas"]["Page_Bullet_"] } };
      };
    };
  };
  "/api/v1/resumes/{resume_id}/pdf": {
    get: {
      parameters: { path: { resume_id: string }; query?: { v?: number } };
      responses: { 200: { content: { "application/pdf": string } } };
    };
  };
  "/api/v1/resumes/{resume_id}/versions/{version_id}/pdf": {
    get: {
      parameters: { path: { resume_id: string; version_id: string } };
      responses: { 200: { content: { "application/pdf": string } } };
    };
  };
  "/api/v1/resumes/{resume_id}/render": {
    post: {
      parameters: { path: { resume_id: string } };
      requestBody: { content: { "application/json": components["schemas"]["RenderRequest"] } };
      responses: { 202: { content: { "application/json": components["schemas"]["RenderJob"] } } };
    };
  };
  "/api/v1/resumes/render-jobs/{job_id}": {
    get: {
      parameters: { path: { job_id: string } };
      responses: { 200: { content: { "application/json": components["schemas"]["RenderJob"] } } };
    };
  };
  "/api/v1/resumes/generate/start": {
    post: {
      requestBody: { content: { "application/json": components["schemas"]["GenerateStartRequest"] } };
      responses: {
        200: { content: { "application/json": components["schemas"]["GenerateTurnResponse"] } };
      };
    };
  };
  "/api/v1/resumes/generate/{thread_id}/turn": {
    post: {
      parameters: { path: { thread_id: string } };
      requestBody: { content: { "application/json": components["schemas"]["GenerateTurnRequest"] } };
      responses: {
        200: { content: { "application/json": components["schemas"]["GenerateTurnResponse"] } };
      };
    };
  };
  "/api/v1/ats/score": {
    post: {
      requestBody: { content: { "application/json": components["schemas"]["ScoreRequest"] } };
      responses: { 200: { content: { "application/json": components["schemas"]["ATSScore"] } } };
    };
  };
  "/api/v1/applications": {
    get: {
      parameters: {
        query?: {
          status?: components["schemas"]["ApplicationStatus"];
          limit?: number;
          offset?: number;
        };
      };
      responses: {
        200: {
          content: { "application/json": components["schemas"]["Page_ApplicationSummary_"] };
        };
      };
    };
  };
  "/api/v1/applications/{application_id}": {
    get: {
      parameters: { path: { application_id: string } };
      responses: {
        200: { content: { "application/json": components["schemas"]["Application"] } };
      };
    };
  };
  "/api/v1/applications/{application_id}/approve": {
    post: {
      parameters: { path: { application_id: string } };
      responses: {
        200: { content: { "application/json": components["schemas"]["Application"] } };
      };
    };
  };
  "/api/v1/applications/{application_id}/retry": {
    post: {
      parameters: { path: { application_id: string } };
      responses: {
        200: { content: { "application/json": components["schemas"]["Application"] } };
      };
    };
  };
  "/api/v1/applications/stats/funnel": {
    get: {
      responses: { 200: { content: { "application/json": Record<string, number> } } };
    };
  };
  "/api/v1/profile/skills": {
    get: {
      responses: {
        200: { content: { "application/json": components["schemas"]["SkillCount"][] } };
      };
    };
  };
  "/api/v1/jobs": {
    get: {
      parameters: { query?: { limit?: number; offset?: number } };
      responses: {
        200: { content: { "application/json": components["schemas"]["Page_JobSummary_"] } };
      };
    };
  };
  "/api/v1/jobs/recommendations/top": {
    get: {
      responses: {
        200: { content: { "application/json": components["schemas"]["JobSummary"][] } };
      };
    };
  };
};

export type components = {
  schemas: {
    /*
     * Hand-maintained entries. These have no single Pydantic class behind them,
     * so `gen_types_offline.py` cannot emit them and leaves them alone.
     */

    /**
     * Either error shape the API can return. Narrow with `"error" in body`.
     * `details` carries per-field messages on a 422 from `ValidationFailedError`;
     * `detail` is FastAPI's own shape, an array only for request-validation
     * failures.
     */
    ErrorBody:
      | { error: { code: string; message: string; details?: Record<string, unknown> } }
      | { detail: string | { loc: (string | number)[]; msg: string; type: string }[] };

    Page_ResumeSummary_: {
      items: components["schemas"]["ResumeSummary"][];
      total: number;
      limit: number;
      offset: number;
    };
    Page_ApplicationSummary_: {
      items: components["schemas"]["ApplicationSummary"][];
      total: number;
      limit: number;
      offset: number;
    };
    Page_JobSummary_: {
      items: components["schemas"]["JobSummary"][];
      total: number;
      limit: number;
      offset: number;
    };
    Page_Bullet_: {
      items: components["schemas"]["Bullet"][];
      total: number;
      limit: number;
      offset: number;
    };
    Page_ResumeVersion_: {
      items: components["schemas"]["ResumeVersion"][];
      total: number;
      limit: number;
      offset: number;
    };

    /* BEGIN GENERATED SCHEMAS */

    ATSFinding: {
      dimension: components["schemas"]["ScoreDimension"];
      severity: components["schemas"]["FindingSeverity"];
      code: string;
      message: string;
      suggestion: string | null;
      targetBulletId: string | null;
      autoFixable: boolean;
    };

    ATSScore: {
      id: string | null;
      created_at: string;
      updated_at: string;
      user_id: string;
      resume_version_id: string;
      job_id: string | null;
      overall: number;
      dimensions: components["schemas"]["DimensionScore"][];
      findings: components["schemas"]["ATSFinding"][];
      keyword_analysis: components["schemas"]["KeywordAnalysis"] | null;
      extracted_text: string | null;
      extraction_confidence: number | null;
      engine_version: string;
      computed_at: string;
      grade: string;
      critical_count: number;
    };

    AgentThread: {
      id: string | null;
      created_at: string;
      updated_at: string;
      user_id: string;
      kind: components["schemas"]["ThreadKind"];
      thread_id: string;
      resume_id: string | null;
      profile_entry_id: string | null;
      status: components["schemas"]["ThreadStatus"];
      title: string;
      message_count: number;
      last_message_at: string | null;
      bullets_produced: number;
      bullets_flagged: number;
      total_tokens: number;
    };

    Application: {
      id: string | null;
      created_at: string;
      updated_at: string;
      user_id: string;
      job_id: string;
      resume_version_id: string;
      cover_letter_id: string | null;
      status: components["schemas"]["ApplicationStatus"];
      method: components["schemas"]["SubmissionMethod"];
      attempts: components["schemas"]["SubmissionAttempt"][];
      arq_job_id: string | null;
      idempotency_key: string | null;
      submitted_at: string | null;
      confirmation_id: string | null;
      events: components["schemas"]["ApplicationEvent"][];
      answers: Record<string, string>;
      notes: string | null;
      follow_up_at: string | null;
    };

    ApplicationCreate: {
      jobId: string;
      resumeVersionId: string;
      coverLetterId?: string | null;
      method?: components["schemas"]["SubmissionMethod"];
      answers?: Record<string, string>;
      idempotencyKey?: string | null;
    };

    ApplicationEvent: {
      at: string;
      kind: string;
      summary: string;
      metadata: Record<string, string>;
    };

    ApplicationStatus:
      | "draft"
      | "queued"
      | "submitting"
      | "awaiting_approval"
      | "submitted"
      | "failed"
      | "acknowledged"
      | "screening"
      | "interviewing"
      | "offer"
      | "rejected"
      | "withdrawn"
      | "ghosted";

    ApplicationStatusUpdate: {
      status: components["schemas"]["ApplicationStatus"];
      note?: string | null;
    };

    ApplicationSummary: {
      id: string;
      jobId: string;
      companyName: string;
      jobTitle: string;
      status: components["schemas"]["ApplicationStatus"];
      method: components["schemas"]["SubmissionMethod"];
      attemptCount: number;
      submittedAt: string | null;
      followUpAt: string | null;
      updatedAt: string;
    };

    AttemptOutcome:
      | "success"
      | "retryable_failure"
      | "permanent_failure"
      | "blocked"
      | "needs_human";

    BlockReason:
      | "captcha"
      | "cloudflare"
      | "rate_limited"
      | "login_required"
      | "mfa_required"
      | "ip_blocked"
      | "unknown";

    BoardCredential: {
      id: string | null;
      created_at: string;
      updated_at: string;
      user_id: string;
      board: string;
      username_hint: string;
      encrypted_blob: string;
      key_version: number;
      last_used_at: string | null;
      last_verified_at: string | null;
      is_valid: boolean;
    };

    Bullet: {
      id: string | null;
      created_at: string;
      updated_at: string;
      user_id: string;
      profile_entry_id: string;
      action: string;
      metric: string;
      method: string;
      skills: string[];
      status: components["schemas"]["BulletStatus"];
      source: components["schemas"]["BulletSource"];
      violations: components["schemas"]["XYZViolation"][];
      thread_id: string | null;
      repair_attempts: number;
      embedding: number[] | null;
      times_used: number;
      text: string;
    };

    BulletDraft: {
      action: string;
      metric: string;
      method: string;
      skills: string[];
      text: string;
    };

    BulletProposal: {
      draft: components["schemas"]["BulletDraft"];
      text: string;
      violations: components["schemas"]["XYZViolation"][];
      repairAttempts: number;
      accepted: boolean;
    };

    BulletSource: "user_authored" | "agent_generated" | "agent_rewritten" | "imported";

    BulletStatus: "draft" | "needs_review" | "approved" | "archived";

    CareerProfile: {
      id: string | null;
      created_at: string;
      updated_at: string;
      user_id: string;
      contact: components["schemas"]["ContactBlock"];
      headline: string | null;
      summary: string | null;
      experiences: components["schemas"]["ExperienceEntry"][];
      projects: components["schemas"]["ProjectEntry"][];
      education: components["schemas"]["EducationEntry"][];
      skills: components["schemas"]["Skill"][];
      certifications: components["schemas"]["Certification"][];
      completeness: number;
    };

    Certification: {
      entry_id: string;
      name: string;
      issuer: string | null;
      issued_on: string | null;
      expires_on: string | null;
      credential_url: unknown | null;
    };

    ChatMessage: {
      role: components["schemas"]["MessageRole"];
      content: string;
      at: string | null;
    };

    Compensation: {
      min_amount: number | null;
      max_amount: number | null;
      currency: string;
      period: string;
      equity: boolean;
    };

    ContactBlock: {
      full_name: string;
      email: string;
      phone: string | null;
      location: string | null;
      linkedin_url: unknown | null;
      github_url: unknown | null;
      portfolio_url: unknown | null;
    };

    DateRange: {
      start: string;
      end: string | null;
    };

    DimensionScore: {
      dimension: components["schemas"]["ScoreDimension"];
      score: number;
      weight: number;
      detail: string | null;
    };

    EducationEntry: {
      entry_id: string;
      institution: string;
      degree: string | null;
      field_of_study: string | null;
      dates: components["schemas"]["DateRange"] | null;
      gpa: number | null;
      honors: string[];
      coursework: string[];
    };

    EmploymentType:
      | "full_time"
      | "part_time"
      | "contract"
      | "internship"
      | "freelance"
      | "volunteer";

    ExperienceEntry: {
      entry_id: string;
      company: string;
      title: string;
      location: string | null;
      employment_type: components["schemas"]["EmploymentType"];
      dates: components["schemas"]["DateRange"];
      tech_stack: string[];
      raw_notes: string;
    };

    FieldMapping: {
      selector: string;
      label: string | null;
      value_preview: string;
      strategy: string;
      confidence: number;
    };

    FindingSeverity: "critical" | "warning" | "info";

    GenerateStartRequest: {
      resumeId?: string | null;
      profileEntryId?: string | null;
      targetJobId?: string | null;
      seedNotes?: string | null;
    };

    GenerateTurnRequest: {
      message?: string;
    };

    GenerateTurnResponse: {
      threadId: string;
      status: components["schemas"]["ThreadStatus"];
      assistantMessage: string;
      question: string | null;
      proposals: components["schemas"]["BulletProposal"][];
      needsInput: boolean;
    };

    Job: {
      id: string | null;
      created_at: string;
      updated_at: string;
      source: components["schemas"]["JobSource"];
      company_name: string;
      company_domain: string | null;
      title: string;
      location: string | null;
      remote_type: components["schemas"]["RemoteType"];
      compensation: components["schemas"]["Compensation"] | null;
      description_raw: string;
      parsed: components["schemas"]["ParsedJob"] | null;
      embedding: number[] | null;
      status: components["schemas"]["JobStatus"];
      posted_at: string | null;
      closes_at: string | null;
      last_seen_at: string | null;
      supports_automation: boolean;
    };

    JobBoard:
      | "greenhouse"
      | "lever"
      | "workday"
      | "ashby"
      | "linkedin"
      | "indeed"
      | "company_site"
      | "manual";

    JobIngestRequest: {
      url?: unknown | null;
      descriptionRaw?: string | null;
      companyName?: string | null;
      title?: string | null;
    };

    JobMatch: {
      id: string | null;
      created_at: string;
      updated_at: string;
      user_id: string;
      job_id: string;
      score: number;
      components: components["schemas"]["MatchComponents"];
      matched_keywords: string[];
      missing_keywords: string[];
      rationale: string | null;
      engine_version: string;
      computed_at: string;
    };

    JobSource: {
      board: components["schemas"]["JobBoard"];
      url: unknown;
      external_id: string | null;
      scraped_at: string | null;
    };

    JobStatus: "open" | "closed" | "unknown";

    JobSummary: {
      id: string;
      companyName: string;
      title: string;
      location: string | null;
      remoteType: components["schemas"]["RemoteType"];
      status: components["schemas"]["JobStatus"];
      supportsAutomation: boolean;
      postedAt: string | null;
      matchScore: number | null;
    };

    KeywordAnalysis: {
      required_present: string[];
      required_missing: string[];
      preferred_present: string[];
      preferred_missing: string[];
      stuffing_warnings: string[];
      required_coverage: number;
    };

    KeywordWeight: {
      term: string;
      weight: number;
      required: boolean;
      occurrences: number;
    };

    MatchComponents: {
      skill_overlap: number;
      keyword_coverage: number;
      seniority_fit: number;
      semantic_similarity: number;
      location_fit: number;
    };

    MessageRole: "user" | "assistant" | "system";

    ParsedJob: {
      responsibilities: string[];
      requirements: string[];
      preferred: string[];
      keywords: components["schemas"]["KeywordWeight"][];
      seniority: components["schemas"]["Seniority"];
      min_years_experience: number | null;
      parser_version: string;
    };

    ProjectEntry: {
      entry_id: string;
      name: string;
      role: string | null;
      dates: components["schemas"]["DateRange"] | null;
      url: unknown | null;
      repo_url: unknown | null;
      tech_stack: string[];
      raw_notes: string;
    };

    RemoteType: "onsite" | "hybrid" | "remote" | "unknown";

    RenderJob: {
      id: string | null;
      created_at: string;
      updated_at: string;
      user_id: string;
      resume_id: string;
      version_id: string;
      arq_job_id: string | null;
      status: components["schemas"]["RenderJobStatus"];
      attempts: number;
      error_message: string | null;
      latex_log_tail: string | null;
      started_at: string | null;
      finished_at: string | null;
      duration_ms: number | null;
    };

    RenderJobStatus: "queued" | "rendering" | "succeeded" | "failed";

    RenderRequest: {
      template?: components["schemas"]["ResumeTemplate"] | null;
      changeNote?: string | null;
    };

    RenderedBullet: {
      bullet_id: string;
      text: string;
      skills: string[];
    };

    RenderedEntry: {
      entry_id: string;
      heading: string;
      subheading: string | null;
      date_label: string;
      location: string | null;
      bullets: components["schemas"]["RenderedBullet"][];
    };

    RenderedSection: {
      kind: components["schemas"]["SectionKind"];
      title: string;
      order: number;
      entries: components["schemas"]["RenderedEntry"][];
    };

    Resume: {
      id: string | null;
      created_at: string;
      updated_at: string;
      user_id: string;
      title: string;
      status: components["schemas"]["ResumeStatus"];
      template: components["schemas"]["ResumeTemplate"];
      target_job_id: string | null;
      target_role: string | null;
      contact: components["schemas"]["ContactBlock"];
      summary: string | null;
      sections: components["schemas"]["ResumeSection"][];
      thread_id: string | null;
      current_version: number;
      latest_ats_score: number | null;
    };

    ResumeCreate: {
      title?: string;
      template?: components["schemas"]["ResumeTemplate"];
      targetJobId?: string | null;
      targetRole?: string | null;
    };

    ResumeSection: {
      kind: components["schemas"]["SectionKind"];
      title: string;
      order: number;
      visible: boolean;
      entry_ids: string[];
      bullet_ids: string[];
    };

    ResumeStatus: "draft" | "generating" | "ready" | "archived";

    ResumeSummary: {
      id: string;
      title: string;
      status: components["schemas"]["ResumeStatus"];
      template: components["schemas"]["ResumeTemplate"];
      targetRole: string | null;
      currentVersion: number;
      latestAtsScore: number | null;
      updatedAt: string;
    };

    ResumeTemplate: "ats_classic" | "ats_compact" | "ats_technical";

    ResumeUpdate: {
      title?: string | null;
      status?: components["schemas"]["ResumeStatus"] | null;
      template?: components["schemas"]["ResumeTemplate"] | null;
      summary?: string | null;
      sections?: components["schemas"]["ResumeSection"][] | null;
    };

    ResumeVersion: {
      id: string | null;
      created_at: string;
      updated_at: string;
      resume_id: string;
      user_id: string;
      version: number;
      template: components["schemas"]["ResumeTemplate"];
      contact: components["schemas"]["ContactBlock"];
      summary: string | null;
      sections: components["schemas"]["RenderedSection"][];
      skills: components["schemas"]["Skill"][];
      education: components["schemas"]["EducationEntry"][];
      certifications: components["schemas"]["Certification"][];
      pdf_path: string | null;
      pdf_sha256: string | null;
      latex_source: string | null;
      page_count: number | null;
      rendered_at: string | null;
      change_note: string | null;
    };

    ScoreComparison: {
      baselineVersionId: string;
      candidateVersionId: string;
      baselineOverall: number;
      candidateOverall: number;
      resolvedFindings: string[];
      newFindings: string[];
      delta: number;
    };

    ScoreDimension:
      | "keyword_match"
      | "parseability"
      | "impact_quantification"
      | "section_structure"
      | "length_density"
      | "contact_completeness"
      | "date_consistency";

    ScoreRequest: {
      resumeVersionId: string;
      jobId?: string | null;
      includeExtractedText?: boolean;
    };

    SectionKind:
      | "summary"
      | "experience"
      | "projects"
      | "education"
      | "skills"
      | "certifications";

    Seniority:
      | "intern"
      | "entry"
      | "mid"
      | "senior"
      | "staff"
      | "principal"
      | "manager"
      | "unknown";

    Skill: {
      name: string;
      category: string | null;
      proficiency: components["schemas"]["SkillProficiency"];
      years: number | null;
    };

    SkillCount: {
      name: string;
      count: number;
    };

    SkillProficiency: "familiar" | "proficient" | "advanced" | "expert";

    SortDirection: "asc" | "desc";

    SubmissionAttempt: {
      attempt_number: number;
      started_at: string;
      finished_at: string | null;
      outcome: components["schemas"]["AttemptOutcome"] | null;
      block_reason: components["schemas"]["BlockReason"] | null;
      error_message: string | null;
      failed_step: string | null;
      final_url: unknown | null;
      confirmation_text: string | null;
      screenshot_path: string | null;
      trace_path: string | null;
      field_mappings: components["schemas"]["FieldMapping"][];
      duration_ms: number | null;
    };

    SubmissionMethod: "automated" | "manual" | "assisted";

    ThreadKind:
      | "resume_generation"
      | "bullet_repair"
      | "resume_tailoring"
      | "cover_letter";

    ThreadStatus: "active" | "awaiting_user" | "completed" | "abandoned" | "failed";

    TokenPair: {
      accessToken: string;
      refreshToken: string;
      tokenType: string;
      expiresIn: number;
    };

    TokenPayload: {
      sub: string;
      tokenVersion: number;
      roles: components["schemas"]["UserRole"][];
      exp: number;
      iat: number;
      typ: string;
    };

    User: {
      id: string | null;
      created_at: string;
      updated_at: string;
      email: unknown;
      password_hash: string;
      full_name: string;
      status: components["schemas"]["UserStatus"];
      roles: components["schemas"]["UserRole"][];
      email_verified_at: string | null;
      last_login_at: string | null;
      token_version: number;
    };

    UserCreate: {
      email: unknown;
      password?: string;
      fullName?: string;
    };

    UserLogin: {
      email: unknown;
      password: string;
    };

    UserPublic: {
      id: string;
      email: unknown;
      fullName: string;
      status: components["schemas"]["UserStatus"];
      roles: components["schemas"]["UserRole"][];
      createdAt: string;
    };

    UserRole: "user" | "admin";

    UserStatus: "pending_verification" | "active" | "suspended" | "deleted";

    XYZViolation: {
      code: components["schemas"]["XYZViolationCode"];
      message: string;
      hint: string | null;
    };

    XYZViolationCode:
      | "missing_metric"
      | "vague_metric"
      | "missing_method"
      | "weak_verb"
      | "first_person"
      | "too_long"
      | "past_tense_required";

    /* END GENERATED SCHEMAS */
  };
};
