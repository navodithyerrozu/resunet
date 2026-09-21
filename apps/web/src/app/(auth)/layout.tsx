import { redirect } from "next/navigation";

import { hasSession } from "@/lib/api/session";

import type { ReactNode } from "react";

/**
 * The unauthenticated shell. No nav, because there is nowhere else to go.
 *
 * Bouncing an already-signed-in visitor to the dashboard is a convenience, not a
 * guarantee -- the cookie could be a stale refresh token, in which case the
 * `(app)` layout sends them straight back here. That loop terminates because
 * this check reads the cookie and that one validates it.
 */
export default async function AuthLayout({ children }: { children: ReactNode }) {
  if (await hasSession()) redirect("/dashboard");

  return <main className="flex min-h-screen items-center justify-center px-6 py-12">{children}</main>;
}
