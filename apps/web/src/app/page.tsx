import { redirect } from "next/navigation";

import { hasSession } from "@/lib/api/session";

/**
 * Entry point. There is no marketing page -- every route is behind a session,
 * so `/` is a fork rather than a destination.
 *
 * `hasSession` only checks for a refresh cookie. That is enough to decide which
 * way to send someone; the API still authorizes every request that follows, and
 * a stale cookie ends up back here via the layout's redirect.
 */
export default async function RootPage(): Promise<never> {
  redirect((await hasSession()) ? "/dashboard" : "/login");
}
