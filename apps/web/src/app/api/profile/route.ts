import { revalidateTag } from "next/cache";
import { NextResponse } from "next/server";

import { withFreshSession } from "@/lib/api/client";
import { getCareerProfile, saveCareerProfile } from "@/lib/api/endpoints";
import { apiErrorResponse } from "@/lib/api/respond";

import type { CareerProfile } from "@resunet/shared";

export async function GET(): Promise<NextResponse> {
  try {
    const profile = await withFreshSession(() => getCareerProfile());
    return NextResponse.json(profile);
  } catch (error) {
    return apiErrorResponse(error);
  }
}

export async function PUT(request: Request): Promise<NextResponse> {
  let profile: CareerProfile;
  try {
    profile = (await request.json()) as CareerProfile;
  } catch {
    return NextResponse.json({ detail: "Expected a JSON body." }, { status: 400 });
  }

  try {
    const saved = await withFreshSession(() => saveCareerProfile(profile));
    revalidateTag("profile");
    return NextResponse.json(saved);
  } catch (error) {
    return apiErrorResponse(error);
  }
}
