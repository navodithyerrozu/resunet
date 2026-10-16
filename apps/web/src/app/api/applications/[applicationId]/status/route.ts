import { revalidateTag } from "next/cache";
import { NextResponse } from "next/server";

import { withFreshSession } from "@/lib/api/client";
import { updateApplicationStatus } from "@/lib/api/endpoints";
import { apiErrorResponse } from "@/lib/api/respond";

import type { ApplicationStatus } from "@resunet/shared";

type Params = { params: Promise<{ applicationId: string }> };

export async function PATCH(request: Request, { params }: Params): Promise<NextResponse> {
  const { applicationId } = await params;
  let body: { status?: unknown; note?: unknown };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ detail: "Expected a JSON body." }, { status: 400 });
  }
  if (typeof body.status !== "string") {
    return NextResponse.json({ detail: "Choose a status." }, { status: 400 });
  }
  try {
    const application = await withFreshSession(() =>
      updateApplicationStatus(
        applicationId,
        body.status as ApplicationStatus,
        typeof body.note === "string" ? body.note : undefined,
      ),
    );
    revalidateTag("applications");
    revalidateTag(`application:${applicationId}`);
    return NextResponse.json(application);
  } catch (error) {
    return apiErrorResponse(error);
  }
}
