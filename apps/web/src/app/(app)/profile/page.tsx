import { ProfileEditor } from "@/components/profile/ProfileEditor";
import { withFreshSession } from "@/lib/api/client";
import { getCareerProfile } from "@/lib/api/endpoints";

import type { Metadata } from "next";

export const metadata: Metadata = { title: "Career profile" };

export default async function ProfilePage() {
  const profile = await withFreshSession(() => getCareerProfile());
  return <ProfileEditor initialProfile={profile} />;
}
