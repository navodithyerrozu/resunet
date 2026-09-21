import { NextResponse } from "next/server";

import { login, logout } from "@/lib/api/endpoints";
import { clearSession, writeSession } from "@/lib/api/session";
import { ApiError, ApiTimeoutError, UnauthorizedError } from "@/lib/api/errors";

/**
 * The login/logout boundary.
 *
 * This handler exists so credentials cross exactly one trust boundary and the
 * resulting tokens never enter JavaScript. The browser posts an email and
 * password here; this route talks to FastAPI, writes httpOnly cookies, and
 * returns nothing. There is no token in the response body on purpose -- adding
 * one "for convenience" would undo the entire arrangement.
 */

type LoginBody = { email?: unknown; password?: unknown };

export async function POST(request: Request): Promise<NextResponse> {
  let body: LoginBody;
  try {
    body = (await request.json()) as LoginBody;
  } catch {
    return NextResponse.json({ detail: "Expected a JSON body." }, { status: 400 });
  }

  const { email, password } = body;
  if (typeof email !== "string" || typeof password !== "string") {
    return NextResponse.json(
      { detail: "Enter both an email address and a password." },
      { status: 400 },
    );
  }

  try {
    const pair = await login({ email, password });
    await writeSession({
      accessToken: pair.accessToken,
      refreshToken: pair.refreshToken,
      expiresIn: pair.expiresIn,
    });
    return new NextResponse(null, { status: 204 });
  } catch (error) {
    // The message is deliberately identical for "no such account" and "wrong
    // password". Distinguishing them turns this endpoint into an account
    // enumeration oracle.
    if (error instanceof UnauthorizedError) {
      return NextResponse.json({ detail: "That email and password do not match." }, { status: 401 });
    }
    if (error instanceof ApiTimeoutError) {
      return NextResponse.json(
        { detail: "The server did not respond. Try again." },
        { status: 504 },
      );
    }
    if (error instanceof ApiError) {
      return NextResponse.json({ detail: error.detail }, { status: error.status });
    }
    throw error;
  }
}

/**
 * Log out.
 *
 * The backend call bumps `token_version`, which invalidates every outstanding
 * refresh token for the account rather than just this browser's. If that call
 * fails the cookies are still cleared -- leaving a user apparently logged in
 * because the API was briefly unreachable is the worse outcome, and the tokens
 * they keep are the ones they already had.
 */
export async function DELETE(): Promise<NextResponse> {
  try {
    await logout();
  } catch (error) {
    if (!(error instanceof ApiError) && !(error instanceof ApiTimeoutError)) throw error;
  } finally {
    await clearSession();
  }
  return new NextResponse(null, { status: 204 });
}
