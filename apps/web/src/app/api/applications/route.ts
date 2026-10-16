import { revalidateTag } from "next/cache";
import { NextResponse } from "next/server";

import { withFreshSession } from "@/lib/api/client";
import { createApplication } from "@/lib/api/endpoints";
import { apiErrorResponse } from "@/lib/api/respond";

import type { ApplicationCreate } from "@resunet/shared";

export async function POST(request: Request): Promise<NextResponse> {
  let payload: ApplicationCreate;
  try {
    payload = (await request.json()) as ApplicationCreate;
  } catch {
    return NextResponse.json({ detail: "Expected a JSON body." }, { status: 400 });
  }
  try {
    const application = await withFreshSession(() => createApplication(payload));
    revalidateTag("applications");
    return NextResponse.json(application, { status: 201 });
  } catch (error) {
    return apiErrorResponse(error);
  }
}
