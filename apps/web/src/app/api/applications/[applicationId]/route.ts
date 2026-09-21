import { revalidateTag } from "next/cache";
import { NextResponse } from "next/server";

import { withFreshSession } from "@/lib/api/client";
import { approveApplication, retryApplication } from "@/lib/api/endpoints";
import { ApiError, ApiTimeoutError, UnauthorizedError } from "@/lib/api/errors";

/**
 * Approve or retry a paused application submission.
 *
 * This is the pattern for every client-driven mutation in the app: a POST-only
 * route handler that wraps the typed endpoint in `withFreshSession`, then
 * revalidates the tags the affected server components read from.
 *
 * POST-only is a security property, not a style preference. These cookies are
 * httpOnly and `SameSite=Lax`, which stops cross-site POSTs but does NOT stop a
 * cross-site GET -- a mutation reachable by `<img src="...">` would fire with
 * the user's session attached. Approving a job application is irreversible, so
 * it must never be reachable that way.
 */

type Params = { params: Promise<{ applicationId: string }> };

type Action = "approve" | "retry";

function parseAction(value: unknown): Action | null {
  return value === "approve" || value === "retry" ? value : null;
}

export async function POST(request: Request, { params }: Params): Promise<NextResponse> {
  const { applicationId } = await params;

  let action: Action | null = null;
  try {
    const body = (await request.json()) as { action?: unknown };
    action = parseAction(body.action);
  } catch {
    action = null;
  }

  if (action === null) {
    return NextResponse.json(
      { detail: 'Expected {"action": "approve"} or {"action": "retry"}.' },
      { status: 400 },
    );
  }

  try {
    const application = await withFreshSession(() =>
      action === "approve" ? approveApplication(applicationId) : retryApplication(applicationId),
    );

    // The tracker and the dashboard both read these, and an approval that does
    // not visibly change the row reads as a no-op to the user.
    revalidateTag("applications");
    revalidateTag(`application:${applicationId}`);

    return NextResponse.json(application, { status: 200 });
  } catch (error) {
    if (error instanceof UnauthorizedError) {
      return NextResponse.json({ detail: "Your session expired. Sign in again." }, { status: 401 });
    }
    if (error instanceof ApiTimeoutError) {
      // Deliberately not a retry prompt. The submission may or may not have
      // been accepted, and re-approving could file a second application. The
      // user is told to look rather than to click again.
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
}
