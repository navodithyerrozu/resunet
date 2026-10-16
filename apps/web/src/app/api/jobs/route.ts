import { revalidateTag } from "next/cache";
import { NextResponse } from "next/server";

import { withFreshSession } from "@/lib/api/client";
import { listJobs, saveJob } from "@/lib/api/endpoints";
import { apiErrorResponse } from "@/lib/api/respond";

import type { JobIngestRequest } from "@resunet/shared";

export async function GET(request: Request): Promise<NextResponse> {
  const url = new URL(request.url);
  const status = url.searchParams.get("status");
  try {
    const page = await withFreshSession(() =>
      listJobs({
        limit: Number(url.searchParams.get("limit") ?? 20),
        offset: Number(url.searchParams.get("offset") ?? 0),
        ...(status === "open" || status === "closed" || status === "unknown" ? { status } : {}),
      }),
    );
    return NextResponse.json(page);
  } catch (error) {
    return apiErrorResponse(error);
  }
}

export async function POST(request: Request): Promise<NextResponse> {
  let payload: JobIngestRequest;
  try {
    payload = (await request.json()) as JobIngestRequest;
  } catch {
    return NextResponse.json({ detail: "Expected a JSON body." }, { status: 400 });
  }
  try {
    const job = await withFreshSession(() => saveJob(payload));
    revalidateTag("jobs");
    return NextResponse.json(job, { status: 201 });
  } catch (error) {
    return apiErrorResponse(error);
  }
}
