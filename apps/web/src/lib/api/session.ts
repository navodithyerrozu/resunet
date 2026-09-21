import "server-only";

import { cookies } from "next/headers";

/**
 * The session boundary.
 *
 * SECURITY -- why tokens live in httpOnly cookies and not in the browser
 * ---------------------------------------------------------------------
 * FastAPI issues an access token and a refresh token as JSON. The obvious thing
 * is to keep them in `localStorage` and attach the access token from client
 * code. That is also the arrangement that turns any XSS -- a bad dependency, a
 * bad `dangerouslySetInnerHTML` -- into full account takeover, because the
 * refresh token is long-lived and stealing it grants indefinite access that
 * survives a password change.
 *
 * So the tokens never reach JavaScript. This module writes them as httpOnly
 * cookies from a route handler, and the browser talks only to this app's own
 * route handlers and server components. The bearer token is attached
 * server-side, in `client.ts`.
 *
 * What this does NOT protect against: CSRF. httpOnly cookies are sent
 * automatically, so a cross-site form post to one of our route handlers would
 * carry them. `SameSite=Lax` blocks the cross-site POST case, which is the one
 * that matters here, and every mutating route handler must stay POST/PATCH --
 * a mutation exposed over GET would be reachable by a plain `<img>` tag and
 * SameSite=Lax would not stop it.
 */

const ACCESS_COOKIE = "resunet_at";
const REFRESH_COOKIE = "resunet_rt";

/** Cookie attributes shared by both tokens. */
function baseCookieOptions() {
  return {
    httpOnly: true,
    // Secure is skipped on plain-HTTP localhost because the browser drops
    // Secure cookies there and login would silently never persist.
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    path: "/",
  };
}

export type SessionTokens = {
  accessToken: string;
  refreshToken: string;
  /** Access-token lifetime in seconds, as reported by the backend. */
  expiresIn: number;
};

/**
 * Persist a freshly issued token pair.
 *
 * The access cookie expires slightly before the token does, so a request is
 * never made with a token the server will certainly reject -- the 30s of slack
 * covers clock skew between this process and the API.
 */
export async function writeSession(tokens: SessionTokens): Promise<void> {
  const jar = await cookies();
  const accessMaxAge = Math.max(tokens.expiresIn - 30, 30);

  jar.set(ACCESS_COOKIE, tokens.accessToken, {
    ...baseCookieOptions(),
    maxAge: accessMaxAge,
  });

  jar.set(REFRESH_COOKIE, tokens.refreshToken, {
    ...baseCookieOptions(),
    // 30 days. The backend's `token_version` is the real revocation mechanism;
    // this bound just stops an abandoned browser holding a valid refresh token
    // indefinitely.
    maxAge: 60 * 60 * 24 * 30,
  });
}

/** Remove both cookies. Called on logout and whenever a refresh is rejected. */
export async function clearSession(): Promise<void> {
  const jar = await cookies();
  jar.delete(ACCESS_COOKIE);
  jar.delete(REFRESH_COOKIE);
}

export async function readAccessToken(): Promise<string | null> {
  const jar = await cookies();
  return jar.get(ACCESS_COOKIE)?.value ?? null;
}

export async function readRefreshToken(): Promise<string | null> {
  const jar = await cookies();
  return jar.get(REFRESH_COOKIE)?.value ?? null;
}

/**
 * Whether a session plausibly exists.
 *
 * "Plausibly" is doing real work: a present cookie is not a valid token. This
 * is only for deciding whether to render the app shell or redirect to login,
 * and the API remains the authority on every actual request.
 */
export async function hasSession(): Promise<boolean> {
  return (await readRefreshToken()) !== null;
}
