import { revalidateTag } from "next/cache";
import { NextResponse } from "next/server";

import { withFreshSession } from "@/lib/api/client";
import { updateBullet } from "@/lib/api/endpoints";
import { apiErrorResponse } from "@/lib/api/respond";

import type { BulletUpdate } from "@resunet/shared";

type Params = { params: Promise<{ resumeId: string; bulletId: string }> };

export async function PATCH(request: Request, { params }: Params): Promise<NextResponse> {
  const { resumeId, bulletId } = await params;

  let payload: BulletUpdate;
  try {
    payload = (await request.json()) as BulletUpdate;
  } catch {
    return NextResponse.json({ detail: "Expected a JSON body." }, { status: 400 });
  }

  try {
    const bullet = await withFreshSession(() => updateBullet(resumeId, bulletId, payload));
    revalidateTag(`resume:${resumeId}:bullets`);
    revalidateTag(`resume:${resumeId}`);
    return NextResponse.json(bullet, { status: 200 });
  } catch (error) {
    return apiErrorResponse(error);
  }
}
