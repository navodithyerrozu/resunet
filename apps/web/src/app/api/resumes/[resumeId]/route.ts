import { revalidateTag } from "next/cache";
import { NextResponse } from "next/server";

import { withFreshSession } from "@/lib/api/client";
import { archiveResume, updateResume } from "@/lib/api/endpoints";
import { apiErrorResponse } from "@/lib/api/respond";

import type { ResumeUpdate } from "@resunet/shared";

type Params = { params: Promise<{ resumeId: string }> };

export async function PATCH(request: Request, { params }: Params): Promise<NextResponse> {
  const { resumeId } = await params;

  let payload: ResumeUpdate;
  try {
    payload = (await request.json()) as ResumeUpdate;
  } catch {
    return NextResponse.json({ detail: "Expected a JSON body." }, { status: 400 });
  }

  try {
    const resume = await withFreshSession(() => updateResume(resumeId, payload));
    revalidateTag("resumes");
    revalidateTag(`resume:${resumeId}`);
    return NextResponse.json(resume, { status: 200 });
  } catch (error) {
    return apiErrorResponse(error);
  }
}

export async function DELETE(_request: Request, { params }: Params): Promise<NextResponse> {
  const { resumeId } = await params;

  try {
    await withFreshSession(() => archiveResume(resumeId));
    revalidateTag("resumes");
    revalidateTag(`resume:${resumeId}`);
    return new NextResponse(null, { status: 204 });
  } catch (error) {
    return apiErrorResponse(error);
  }
}
