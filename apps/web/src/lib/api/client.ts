import "server-only";

import { ApiError, ApiTimeoutError, UnauthorizedError, detailFrom, parseFieldErrors } from "./errors";
import {
  bindAccessToken,
  clearAccessTokenOverride,
  clearSession,
  readAccessToken,
  readRefreshToken,
  writeSession,
} from "./session";

import type { TokenPair } from "@resunet/shared";

/**
 * The typed HTTP layer between this app's server and FastAPI.
 *
 * `import "server-only"` at the top is load-bearing, not decorative. It makes
 * the bundler fail the build if any client component imports this file, which is
 * the only reliable guard against someone shipping the API base URL and a
 * bearer token to the browser. A comment saying "server only" does not survive
 * a refactor; this does.
 *
 * Browser code reaches the API through this app's route handlers under
 * `/api/*`, never directly.
 */

const DEFAULT_TIMEOUT_MS = 15_000;

function apiBaseUrl(): string {
  // Not NEXT_PUBLIC_*: the browser has no business knowing where FastAPI lives.
  const raw = process.env.API_BASE_URL ?? "http://localhost:8000";
  return raw.replace(/\/+$/, "");
}

type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  /** Serialized as JSON. Omit for GET. */
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  /** Defaults to no caching -- see the note in `apiFetch`. */
  cache?: RequestCache;
  /** Next.js tag for on-demand revalidation. */
  tags?: string[];
  timeoutMs?: number;
  /** Set false for endpoints that must not carry credentials (e.g. login). */
  authenticated?: boolean;
};

function buildUrl(path: string, query: RequestOptions["query"]): string {
  const url = new URL(`${apiBaseUrl()}/api/v1${path.startsWith("/") ? path : `/${path}`}`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null || value === "") continue;
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

/**
 * Issue one request. Does not retry and does not refresh.
 *
 * WHY NO AUTOMATIC REFRESH HERE
 * -----------------------------
 * The tempting design is a module-level `let refreshPromise` so that several
 * concurrent 401s share one refresh call. On a Node server that is a
 * cross-user data leak: module scope is per *process*, not per request, so two
 * users hitting an expired token at the same moment would await the same
 * promise and one would be handed the other's access token.
 *
 * Refresh is therefore explicit and request-scoped -- see `withFreshSession`,
 * which is driven by a single request's own cookies.
 */
export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const {
    method = "GET",
    body,
    query,
    cache,
    tags,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    authenticated = true,
  } = options;

  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  if (authenticated) {
    const token = await readAccessToken();
    // A missing cookie is indistinguishable from an expired one as far as the
    // caller is concerned, so both surface as UnauthorizedError and both are
    // handled by the same refresh-or-redirect path.
    if (!token) throw new UnauthorizedError("no access token in session");
    headers["Authorization"] = `Bearer ${token}`;
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      // Default to no-store. Per-user data behind a bearer token must never
      // land in a shared cache -- a cached resume served to the wrong account
      // is the worst bug this app could have. Callers opt into caching
      // explicitly, per endpoint.
      cache: cache ?? "no-store",
      ...(tags && tags.length > 0 ? { next: { tags } } : {}),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new ApiTimeoutError(timeoutMs);
    }
    throw error;
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload: unknown = text.length > 0 ? safeJsonParse(text) : null;

  if (!response.ok) {
    if (response.status === 401) {
      throw new UnauthorizedError(detailFrom(payload, "not authenticated"));
    }
    throw new ApiError(
      response.status,
      detailFrom(payload, response.statusText || "request failed"),
      parseFieldErrors(payload),
    );
  }

  return payload as T;
}

function safeJsonParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    // A non-JSON body from a JSON API means a proxy or gateway answered, not
    // FastAPI. Surfacing the raw text is more useful than a parse error.
    return { detail: text.slice(0, 500) };
  }
}

/**
 * Exchange the refresh cookie for a new token pair and persist it.
 *
 * Returns false when the refresh token is itself rejected, which means the
 * session is genuinely over -- revoked, expired, or `token_version` bumped
 * server-side. The cookies are cleared in that case so the next request
 * redirects to login instead of retrying forever.
 *
 * Only callable from a route handler or server action. Server components cannot
 * set cookies during render, which is exactly why refresh does not live inside
 * `apiFetch`.
 */
export async function refreshSession(): Promise<boolean> {
  const refreshToken = await readRefreshToken();
  if (!refreshToken) return false;

  try {
    const pair = await apiFetch<TokenPair>("/auth/refresh", {
      method: "POST",
      body: { refresh_token: refreshToken },
      authenticated: false,
    });
    // Always bind the new access token for the rest of this request first.
    // Server Components cannot write cookies during render; without the
    // override, `apiFetch` would retry with the same expired cookie and 401 loop.
    bindAccessToken(pair.accessToken);
    try {
      await writeSession({
        accessToken: pair.accessToken,
        refreshToken: pair.refreshToken,
        expiresIn: pair.expiresIn,
      });
    } catch (error) {
      if (!isCookieMutationError(error)) throw error;
    }
    return true;
  } catch (error) {
    if (error instanceof UnauthorizedError) {
      clearAccessTokenOverride();
      try {
        await clearSession();
      } catch (cookieError) {
        if (!isCookieMutationError(cookieError)) throw cookieError;
      }
      return false;
    }
    // A timeout or a 503 is not proof the session is invalid, so the cookies
    // are left alone and the caller can try again.
    throw error;
  }
}

/** Next.js 15 throws if `cookies().set()` runs while rendering a Server Component. */
function isCookieMutationError(error: unknown): boolean {
  return (
    error instanceof Error &&
    error.message.includes("Cookies can only be modified in a Server Action or Route Handler")
  );
}

/**
 * Authenticated fetch that returns the raw `Response` (PDF bytes, etc.).
 *
 * `apiFetch` always JSON-parses; compiled resumes are `application/pdf`.
 */
export async function apiFetchBinary(path: string, options: RequestOptions = {}): Promise<Response> {
  const {
    method = "GET",
    body,
    query,
    cache,
    tags,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    authenticated = true,
  } = options;

  const headers: Record<string, string> = { Accept: "application/pdf, application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  if (authenticated) {
    const token = await readAccessToken();
    if (!token) throw new UnauthorizedError("no access token in session");
    headers["Authorization"] = `Bearer ${token}`;
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      cache: cache ?? "no-store",
      ...(tags && tags.length > 0 ? { next: { tags } } : {}),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new ApiTimeoutError(timeoutMs);
    }
    throw error;
  }

  if (response.ok) return response;

  const text = await response.text();
  const payload: unknown = text.length > 0 ? safeJsonParse(text) : null;
  if (response.status === 401) {
    throw new UnauthorizedError(detailFrom(payload, "not authenticated"));
  }
  throw new ApiError(
    response.status,
    detailFrom(payload, response.statusText || "request failed"),
    parseFieldErrors(payload),
  );
}

/**
 * Run an authenticated call, refreshing once if the access token has expired.
 *
 * For use in route handlers and server actions. The refresh is scoped to this
 * one request's cookies, so there is no shared state and no cross-user hazard.
 */
export async function withFreshSession<T>(run: () => Promise<T>): Promise<T> {
  try {
    return await run();
  } catch (error) {
    if (!(error instanceof UnauthorizedError)) throw error;
    const refreshed = await refreshSession();
    if (!refreshed) throw error;
    // Exactly one retry. If the freshly minted token is also rejected, the
    // problem is not staleness and looping would just turn a broken session
    // into a hot loop against the auth endpoint.
    return await run();
  }
}
