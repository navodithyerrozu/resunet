import { revalidateTag } from "next/cache";
import { NextResponse } from "next/server";

import { withFreshSession } from "@/lib/api/client";
import { decideGenerationProposal } from "@/lib/api/endpoints";
import { apiErrorResponse } from "@/lib/api/respond";

import type { ProposalDecision } from "@resunet/shared";

type Params = { params: Promise<{ threadId: string; bulletId: string }> };

export async function POST(request: Request, { params }: Params): Promise<NextResponse> {
  const { threadId, bulletId } = await params;
  const resumeId = new URL(request.url).searchParams.get("resumeId");
  let payload: ProposalDecision;
  try {
    payload = (await request.json()) as ProposalDecision;
  } catch {
    return NextResponse.json({ detail: "Expected a JSON body." }, { status: 400 });
  }
  try {
    const bullet = await withFreshSession(() =>
      decideGenerationProposal(threadId, bulletId, payload),
    );
    revalidateTag("resumes");
    revalidateTag("resumes");
    if (resumeId) revalidateTag(`resume:${resumeId}`);
    return NextResponse.json(bullet);
  } catch (error) {
    return apiErrorResponse(error);
  }
}
