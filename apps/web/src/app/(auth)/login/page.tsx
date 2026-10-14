import Link from "next/link";

import { LoginForm } from "@/components/auth/LoginForm";

import type { Metadata } from "next";

export const metadata: Metadata = { title: "Sign in" };

/**
 * The only page an unauthenticated visitor can reach.
 *
 * The sample bullet is not decoration -- it is the shortest possible statement of
 * what the product does differently, in the same visual vocabulary the user will
 * meet inside. Someone signing in for the first time learns the rule before they
 * learn the interface.
 *
 * It is hand-marked-up rather than rendered through `<XyzBullet>` on purpose:
 * that component's job is to display real bullet data alongside its validation
 * state, and there is no bullet, no violations, and no backend here.
 */

const SAMPLE = [
  { role: "action", letter: "X", hint: "what you did", text: "Cut checkout latency" },
  { role: "metric", letter: "Y", hint: "what it measured", text: "from 840ms to 210ms at p95" },
  {
    role: "method",
    letter: "Z",
    hint: "how you did it",
    text: "by batching the pricing calls and caching tax lookups",
  },
] as const;

type SearchParams = Promise<{ next?: string; reason?: string }>;

export default async function LoginPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;

  return (
    <div className="w-full max-w-4xl">
      <div className="grid gap-12 md:grid-cols-[1fr_20rem] md:gap-16">
        <div>
          <p className="font-mono text-xs font-600 uppercase tracking-[0.14em]">
            Resu<span className="text-accent">Net</span>
          </p>

          <h1 className="mt-6 max-w-md text-display">
            Every bullet carries a number, or it doesn&rsquo;t ship.
          </h1>

          <p className="mt-4 max-w-md text-sm text-muted">
            ResuNet writes each achievement as three separate things &mdash; what you did, what it
            measured, how you did it &mdash; then scores the result the way an applicant tracking
            system will read it.
          </p>

          <div className="mt-10 max-w-md space-y-2.5 border-t border-line pt-8">
            {SAMPLE.map((part) => (
              <div key={part.role} className="xyz-slot" data-role={part.role}>
                <div className="flex items-baseline gap-2">
                  <span
                    className="font-mono text-[0.625rem] font-600 uppercase tracking-wider"
                    style={{
                      color: `var(--color-role-${
                        part.role === "action" ? "x" : part.role === "metric" ? "y" : "z"
                      })`,
                    }}
                  >
                    {part.letter}
                  </span>
                  <span className="text-[0.625rem] uppercase tracking-wider text-faint">
                    {part.hint}
                  </span>
                </div>
                <p
                  className={`mt-0.5 text-sm ${
                    part.role === "metric" ? "font-mono text-[0.8125rem] tabular-nums" : ""
                  }`}
                >
                  {part.text}
                </p>
              </div>
            ))}
          </div>
        </div>

        <div className="md:pt-16">
          <LoginForm
            next={params.next ?? null}
            expired={params.reason === "expired"}
          />
          <p className="mt-6 text-xs text-faint">
            No account?{" "}
            <Link href="/register" className="text-muted hover:text-ink">
              Create one
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
