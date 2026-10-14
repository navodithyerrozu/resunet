import { NextResponse } from "next/server";

import { ApiError, ApiTimeoutError, UnauthorizedError } from "./errors";

/** Shared JSON error mapping for mutating `/api/*` route handlers. */
export function apiErrorResponse(error: unknown): NextResponse {
  if (error instanceof UnauthorizedError) {
    return NextResponse.json({ detail: "Your session expired. Sign in again." }, { status: 401 });
  }
  if (error instanceof ApiTimeoutError) {
    return NextResponse.json(
      { detail: "The server did not respond in time. Refresh to see the current status." },
      { status: 504 },
    );
  }
  if (error instanceof ApiError) {
    return NextResponse.json({ detail: error.detail }, { status: error.status });
  }
  throw error;
}
