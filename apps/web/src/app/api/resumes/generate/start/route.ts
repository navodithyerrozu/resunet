import { revalidateTag } from "next/cache";
import { NextResponse } from "next/server";

import { withFreshSession } from "@/lib/api/client";
import { startGeneration } from "@/lib/api/endpoints";
import { apiErrorResponse } from "@/lib/api/respond";

import type { GenerateStartRequest } from "@resunet/shared";

export async function POST(request: Request): Promise<NextResponse> {
  let payload: GenerateStartRequest;
  try {
    payload = (await request.json()) as GenerateStartRequest;
  } catch {
    return NextResponse.json({ detail: "Expected a JSON body." }, { status: 400 });
  }

  try {
    const turn = await withFreshSession(() => startGeneration(payload));
    if (typeof payload.resumeId === "string" && payload.resumeId.length > 0) {
      revalidateTag(`resume:${payload.resumeId}`);
      revalidateTag(`resume:${payload.resumeId}:bullets`);
    }
    return NextResponse.json(turn, { status: 200 });
  } catch (error) {
    return apiErrorResponse(error);
  }
}
