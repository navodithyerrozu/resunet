import { revalidateTag } from "next/cache";
import { NextResponse } from "next/server";

import { withFreshSession } from "@/lib/api/client";
import { createResume } from "@/lib/api/endpoints";
import { apiErrorResponse } from "@/lib/api/respond";

import type { ResumeCreate } from "@resunet/shared";

export async function POST(request: Request): Promise<NextResponse> {
  let payload: ResumeCreate;
  try {
    payload = (await request.json()) as ResumeCreate;
  } catch {
    return NextResponse.json({ detail: "Expected a JSON body." }, { status: 400 });
  }

  try {
    const resume = await withFreshSession(() => createResume(payload));
    revalidateTag("resumes");
    return NextResponse.json(resume, { status: 201 });
  } catch (error) {
    return apiErrorResponse(error);
  }
}
