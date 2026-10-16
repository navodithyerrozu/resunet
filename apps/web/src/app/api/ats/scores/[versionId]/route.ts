import { NextResponse } from "next/server";

import { withFreshSession } from "@/lib/api/client";
import { listVersionScores } from "@/lib/api/endpoints";
import { apiErrorResponse } from "@/lib/api/respond";

type Params = { params: Promise<{ versionId: string }> };

export async function GET(request: Request, { params }: Params): Promise<NextResponse> {
  const { versionId } = await params;
  const search = new URL(request.url).searchParams;
  const limit = Number(search.get("limit") ?? 20);
  const offset = Number(search.get("offset") ?? 0);
  const jobId = search.get("jobId");

  try {
    const page = await withFreshSession(() =>
      listVersionScores(versionId, { limit, offset, jobId }),
    );
    return NextResponse.json(page, { status: 200 });
  } catch (error) {
    return apiErrorResponse(error);
  }
}
