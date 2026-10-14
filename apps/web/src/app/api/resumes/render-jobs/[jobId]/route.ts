import { NextResponse } from "next/server";

import { withFreshSession } from "@/lib/api/client";
import { getRenderJob } from "@/lib/api/endpoints";
import { apiErrorResponse } from "@/lib/api/respond";

type Params = { params: Promise<{ jobId: string }> };

export async function GET(_request: Request, { params }: Params): Promise<NextResponse> {
  const { jobId } = await params;

  try {
    const job = await withFreshSession(() => getRenderJob(jobId));
    return NextResponse.json(job, { status: 200 });
  } catch (error) {
    return apiErrorResponse(error);
  }
}
