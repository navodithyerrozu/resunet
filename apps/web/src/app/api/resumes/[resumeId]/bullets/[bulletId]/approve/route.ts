import { revalidateTag } from "next/cache";
import { NextResponse } from "next/server";

import { withFreshSession } from "@/lib/api/client";
import { approveBullet } from "@/lib/api/endpoints";
import { apiErrorResponse } from "@/lib/api/respond";

type Params = { params: Promise<{ resumeId: string; bulletId: string }> };

export async function POST(_request: Request, { params }: Params): Promise<NextResponse> {
  const { resumeId, bulletId } = await params;

  try {
    const bullet = await withFreshSession(() => approveBullet(resumeId, bulletId));
    revalidateTag(`resume:${resumeId}:bullets`);
    revalidateTag(`resume:${resumeId}`);
    return NextResponse.json(bullet, { status: 200 });
  } catch (error) {
    return apiErrorResponse(error);
  }
}
