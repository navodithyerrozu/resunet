import { NextResponse } from "next/server";

import { withFreshSession } from "@/lib/api/client";
import { streamResumePdf } from "@/lib/api/endpoints";
import { apiErrorResponse } from "@/lib/api/respond";

type Params = { params: Promise<{ resumeId: string }> };

export async function GET(request: Request, { params }: Params): Promise<NextResponse> {
  const { resumeId } = await params;
  const versionParam = new URL(request.url).searchParams.get("v");
  const version =
    versionParam !== null && versionParam.length > 0 ? Number(versionParam) : undefined;
  const versionNumber = version !== undefined && Number.isFinite(version) ? version : undefined;

  try {
    const upstream = await withFreshSession(() => streamResumePdf(resumeId, versionNumber));
    const bytes = await upstream.arrayBuffer();
    return new NextResponse(bytes, {
      status: 200,
      headers: {
        "Content-Type": "application/pdf",
        "Content-Disposition": "inline",
        "Cache-Control": "private, max-age=0, must-revalidate",
      },
    });
  } catch (error) {
    return apiErrorResponse(error);
  }
}
