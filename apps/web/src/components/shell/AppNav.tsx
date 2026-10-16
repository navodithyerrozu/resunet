"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, useTransition } from "react";

/**
 * The app's only chrome.
 *
 * A client component because the active section comes from `usePathname` and
 * signing out is a fetch. It takes the user's email as a prop rather than
 * fetching it, so the layout stays the single place that talks to `/auth/me`.
 *
 * Sections are labelled by the noun the user thinks in -- resumes, applications
 * -- not by the subsystem behind them. There is no "agent" or "worker" tab even
 * though both exist; the user asks for a tailored resume, they do not ask to run
 * a LangGraph thread.
 */

const SECTIONS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/profile", label: "Profile" },
  { href: "/jobs", label: "Jobs" },
  { href: "/resumes", label: "Resumes" },
  { href: "/applications", label: "Applications" },
] as const;

function isActive(pathname: string, href: string): boolean {
  // Exact match or a child route, so /resumes/abc/edit still lights up Resumes.
  return pathname === href || pathname.startsWith(`${href}/`);
}

function SignOutButton() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [failed, setFailed] = useState(false);

  async function signOut() {
    setFailed(false);
    try {
      // DELETE, not GET. The handler clears cookies in a `finally`, so even a
      // dead backend leaves the browser signed out rather than showing an app
      // shell backed by a session the API has already rejected.
      const response = await fetch("/api/session", { method: "DELETE" });
      if (response.status === 503) {
        startTransition(() => {
          router.replace("/login?reason=logout-revocation-failed");
          router.refresh();
        });
        return;
      }
      if (!response.ok) throw new Error(`sign out failed: ${response.status}`);
      startTransition(() => {
        router.replace("/login");
        // Drops every cached server-component payload. Without this, hitting
        // Back would paint the previous user's dashboard from the router cache.
        router.refresh();
      });
    } catch {
      setFailed(true);
    }
  }

  return (
    <div className="flex items-center gap-2">
      {failed ? (
        <span className="text-xs text-danger">Could not sign out. Try again.</span>
      ) : null}
      <button
        type="button"
        onClick={() => void signOut()}
        disabled={pending}
        className="rounded-chip px-1.5 py-0.5 text-xs text-muted transition-colors hover:text-ink disabled:opacity-50"
      >
        {pending ? "Signing out…" : "Sign out"}
      </button>
    </div>
  );
}

export function AppNav({ email }: { email: string }) {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-10 border-b border-line bg-paper/85 backdrop-blur-sm">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-8 px-6">
        <Link
          href="/dashboard"
          className="font-mono text-xs font-600 uppercase tracking-[0.14em] text-ink"
        >
          Resu<span className="text-accent">Net</span>
        </Link>

        <nav aria-label="Sections" className="flex items-stretch gap-1 self-stretch">
          {SECTIONS.map((section) => {
            const active = isActive(pathname, section.href);
            return (
              <Link
                key={section.href}
                href={section.href}
                aria-current={active ? "page" : undefined}
                className={`flex items-center border-b-2 px-2.5 text-sm transition-colors ${
                  active
                    ? "border-accent font-500 text-ink"
                    : "border-transparent text-muted hover:text-ink"
                }`}
              >
                {section.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <span className="hidden font-mono text-xs text-faint sm:inline">{email}</span>
          <SignOutButton />
        </div>
      </div>
    </header>
  );
}
