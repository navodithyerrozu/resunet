import { NextResponse } from "next/server";

import { registerAccount } from "@/lib/api/endpoints";
import { ApiError, ApiTimeoutError } from "@/lib/api/errors";
import { writeSession } from "@/lib/api/session";

/**
 * Self-serve sign-up. Tokens stay in httpOnly cookies; the browser never sees them.
 */

type RegisterBody = { email?: unknown; password?: unknown; fullName?: unknown };

export async function POST(request: Request): Promise<NextResponse> {
  let body: RegisterBody;
  try {
    body = (await request.json()) as RegisterBody;
  } catch {
    return NextResponse.json({ detail: "Expected a JSON body." }, { status: 400 });
  }

  const { email, password, fullName } = body;
  if (typeof email !== "string" || typeof password !== "string" || typeof fullName !== "string") {
    return NextResponse.json(
      { detail: "Enter a name, email address, and password." },
      { status: 400 },
    );
  }

  try {
    const pair = await registerAccount({ email, password, fullName });
    await writeSession({
      accessToken: pair.accessToken,
      refreshToken: pair.refreshToken,
      expiresIn: pair.expiresIn,
    });
    return new NextResponse(null, { status: 204 });
  } catch (error) {
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
