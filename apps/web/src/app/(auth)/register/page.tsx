import Link from "next/link";

import { RegisterForm } from "@/components/auth/RegisterForm";

import type { Metadata } from "next";

export const metadata: Metadata = { title: "Create account" };

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

export default function RegisterPage() {
  return (
    <div className="w-full max-w-4xl">
      <div className="grid gap-12 md:grid-cols-[1fr_20rem] md:gap-16">
        <div>
          <p className="font-mono text-xs font-600 uppercase tracking-[0.14em]">
            Resu<span className="text-accent">Net</span>
          </p>

          <h1 className="mt-6 max-w-md text-display">
            Start with a number, not a slogan.
          </h1>

          <p className="mt-4 max-w-md text-sm text-muted">
            Create an account to draft XYZ bullets, compile an ATS-safe PDF, and keep every
            application in one tracker.
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
          <RegisterForm />
          <p className="mt-6 text-xs text-faint">
            Already have an account?{" "}
            <Link href="/login" className="text-muted hover:text-ink">
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
