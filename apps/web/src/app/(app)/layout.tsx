import { redirect } from "next/navigation";

import { AppNav } from "@/components/shell/AppNav";
import { withFreshSession } from "@/lib/api/client";
import { currentUser } from "@/lib/api/endpoints";
import { UnauthorizedError } from "@/lib/api/errors";
import { hasSession } from "@/lib/api/session";

import type { ReactNode } from "react";
import type { UserPublic } from "@resunet/shared";

/**
 * The authenticated shell.
 *
 * This layout is the single gate for every page in the `(app)` group and the
 * only caller of `/auth/me`. Pages below it can assume a session exists, which
 * keeps the auth check out of every page component -- and out of a middleware
 * file, where it would run on the edge runtime without access to the API client.
 *
 * Note this is a *usability* gate, not the security boundary. The API authorizes
 * each request independently. If this check were the only thing standing between
 * an anonymous visitor and someone's resume, the design would be wrong.
 */

async function loadUser(): Promise<UserPublic | null> {
  try {
    return await withFreshSession(() => currentUser());
  } catch (error) {
    // Only an auth failure is handled here. A 500 or a timeout from the API is
    // a real error and belongs in `error.tsx`, not silently redirected to a
    // login page that will appear to work and then fail again.
    if (error instanceof UnauthorizedError) return null;
    throw error;
  }
}

export default async function AppLayout({ children }: { children: ReactNode }) {
  // Cheap cookie check first, so an anonymous visitor never costs an API call.
  if (!(await hasSession())) redirect("/login");

  const user = await loadUser();

  // `redirect` throws internally, so it has to happen outside the try/catch in
  // `loadUser` -- catching there would swallow the redirect and render the shell
  // for a user who does not exist.
  if (user === null) redirect("/login?reason=expired");

  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-20 focus:rounded-chip focus:bg-surface focus:px-3 focus:py-1.5 focus:text-sm"
      >
        Skip to content
      </a>
      <AppNav email={user.email} />
      <main id="main" className="mx-auto max-w-6xl px-6 py-8">
        {children}
      </main>
    </>
  );
}
