import { revalidateTag } from "next/cache";
import { NextResponse } from "next/server";

import { withFreshSession } from "@/lib/api/client";
import { scoreVersion } from "@/lib/api/endpoints";
import { apiErrorResponse } from "@/lib/api/respond";

import type { ScoreRequest } from "@resunet/shared";

export async function POST(request: Request): Promise<NextResponse> {
  let payload: ScoreRequest;
  try {
    payload = (await request.json()) as ScoreRequest;
  } catch {
    return NextResponse.json({ detail: "Expected a JSON body." }, { status: 400 });
  }

  try {
    const score = await withFreshSession(() => scoreVersion(payload));
    revalidateTag("resumes");
    return NextResponse.json(score, { status: 200 });
  } catch (error) {
    return apiErrorResponse(error);
  }
}
