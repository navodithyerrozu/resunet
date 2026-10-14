import { revalidateTag } from "next/cache";
import { NextResponse } from "next/server";

import { withFreshSession } from "@/lib/api/client";
import { requestRender } from "@/lib/api/endpoints";
import { apiErrorResponse } from "@/lib/api/respond";

import type { RenderRequest } from "@resunet/shared";

type Params = { params: Promise<{ resumeId: string }> };

export async function POST(request: Request, { params }: Params): Promise<NextResponse> {
  const { resumeId } = await params;

  let payload: RenderRequest = {};
  try {
    const text = await request.text();
    if (text.length > 0) payload = JSON.parse(text) as RenderRequest;
  } catch {
    return NextResponse.json({ detail: "Expected a JSON body." }, { status: 400 });
  }

  try {
    const job = await withFreshSession(() => requestRender(resumeId, payload));
    revalidateTag(`resume:${resumeId}`);
    return NextResponse.json(job, { status: 202 });
  } catch (error) {
    return apiErrorResponse(error);
  }
}
