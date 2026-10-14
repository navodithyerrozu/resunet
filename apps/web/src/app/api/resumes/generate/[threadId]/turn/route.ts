import { revalidateTag } from "next/cache";
import { NextResponse } from "next/server";

import { withFreshSession } from "@/lib/api/client";
import { sendGenerationTurn } from "@/lib/api/endpoints";
import { apiErrorResponse } from "@/lib/api/respond";

type Params = { params: Promise<{ threadId: string }> };

type TurnBody = { message?: unknown; resumeId?: unknown };

export async function POST(request: Request, { params }: Params): Promise<NextResponse> {
  const { threadId } = await params;

  let body: TurnBody;
  try {
    body = (await request.json()) as TurnBody;
  } catch {
    return NextResponse.json({ detail: "Expected a JSON body." }, { status: 400 });
  }

  if (typeof body.message !== "string") {
    return NextResponse.json({ detail: "Expected {\"message\": string}." }, { status: 400 });
  }

  try {
    const turn = await withFreshSession(() => sendGenerationTurn(threadId, body.message as string));
    if (typeof body.resumeId === "string" && body.resumeId.length > 0) {
      revalidateTag(`resume:${body.resumeId}`);
      revalidateTag(`resume:${body.resumeId}:bullets`);
    }
    return NextResponse.json(turn, { status: 200 });
  } catch (error) {
    return apiErrorResponse(error);
  }
}
