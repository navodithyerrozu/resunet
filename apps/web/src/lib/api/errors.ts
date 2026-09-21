/**
 * Error types shared by server and client code.
 *
 * Deliberately free of `server-only` so a client component can narrow on these
 * when rendering a failed mutation. Nothing here touches cookies or env vars.
 */

/** A non-2xx response from the FastAPI backend. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  /** Present when FastAPI returned a 422 with per-field validation errors. */
  readonly fieldErrors: Record<string, string> | undefined;

  constructor(
    status: number,
    detail: string,
    fieldErrors?: Record<string, string> | undefined,
  ) {
    super(`${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.fieldErrors = fieldErrors;
  }

  /** True for the statuses a retry could plausibly fix. */
  get isRetryable(): boolean {
    return this.status === 429 || this.status === 502 || this.status === 503 || this.status === 504;
  }
}

/**
 * The access token was missing, expired, or rejected.
 *
 * Split from `ApiError` because the caller's response is structurally
 * different: every other error is shown to the user, this one triggers a
 * refresh attempt or a redirect to login.
 */
export class UnauthorizedError extends ApiError {
  constructor(detail = "not authenticated") {
    super(401, detail);
    this.name = "UnauthorizedError";
  }
}

/** The backend did not answer within the client's deadline. */
export class ApiTimeoutError extends Error {
  readonly timeoutMs: number;

  constructor(timeoutMs: number) {
    super(`backend did not respond within ${timeoutMs}ms`);
    this.name = "ApiTimeoutError";
    this.timeoutMs = timeoutMs;
  }
}

/**
 * Turn a FastAPI error body into a flat field map.
 *
 * FastAPI 422s arrive as `{detail: [{loc: ["body", "email"], msg: "..."}]}`.
 * The `loc` prefix ("body", "query") is dropped so the key matches the form
 * field name the UI actually rendered.
 */
export function parseFieldErrors(body: unknown): Record<string, string> | undefined {
  if (typeof body !== "object" || body === null) return undefined;
  const detail = (body as { detail?: unknown }).detail;
  if (!Array.isArray(detail)) return undefined;

  const out: Record<string, string> = {};
  for (const item of detail) {
    if (typeof item !== "object" || item === null) continue;
    const { loc, msg } = item as { loc?: unknown; msg?: unknown };
    if (!Array.isArray(loc) || typeof msg !== "string") continue;
    const field = loc.filter((p) => p !== "body" && p !== "query" && p !== "path").join(".");
    if (field) out[field] = msg;
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

/** Extract a human-readable message from an arbitrary error body. */
export function detailFrom(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null) {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: unknown };
      if (typeof first?.msg === "string") return first.msg;
    }
  }
  return fallback;
}
