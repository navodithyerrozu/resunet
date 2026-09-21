import { VIOLATION_LABELS } from "@resunet/shared";

import { Chip } from "@/components/ui/primitives";

import type { XYZViolation, XYZViolationCode } from "@resunet/shared";

/**
 * A bullet, shown as its three components rather than as a finished sentence.
 *
 * This is the one place the interface is emphatic, and it mirrors the schema
 * exactly: the backend stores `action`, `metric`, and `method` as separate
 * required fields and derives the display line from them. Rendering the derived
 * sentence here would throw that away and make a missing metric look like prose
 * that happens to read a bit weakly. Rendering the parts makes the gap visible.
 *
 * The metric is set in mono because it is a measurement. That also makes it the
 * odd one out in the stack, which is the intended effect -- it is the component
 * the product refuses to compromise on.
 */

/** Accepts both a persisted `Bullet` and an unsaved `BulletProposal`. */
export type XyzParts = {
  action: string;
  metric: string;
  method: string;
  violations: XYZViolation[];
  skills?: string[];
};

const ROLE_COPY = {
  action: { letter: "X", hint: "what you did" },
  metric: { letter: "Y", hint: "what it measured" },
  method: { letter: "Z", hint: "how you did it" },
} as const;

type Role = keyof typeof ROLE_COPY;

const METRIC_CODES: ReadonlySet<XYZViolationCode> = new Set<XYZViolationCode>([
  "missing_metric",
  "vague_metric",
]);

function violationsForRole(role: Role, violations: XYZViolation[]): XYZViolation[] {
  return violations.filter((v) => {
    switch (role) {
      case "metric":
        return METRIC_CODES.has(v.code);
      case "method":
        return v.code === "missing_method";
      case "action":
        return (
          v.code === "weak_verb" || v.code === "first_person" || v.code === "past_tense_required"
        );
      default:
        return false;
    }
  });
}

function Slot({
  role,
  text,
  violations,
}: {
  role: Role;
  text: string;
  violations: XYZViolation[];
}) {
  const problems = violationsForRole(role, violations);
  const invalid = problems.length > 0;
  const { letter, hint } = ROLE_COPY[role];

  return (
    <div className="xyz-slot py-1" data-role={role} data-invalid={invalid ? "true" : "false"}>
      <div className="flex items-baseline gap-2">
        <span
          className="font-mono text-[0.625rem] font-600 uppercase tracking-wider"
          style={{ color: `var(--color-role-${role === "action" ? "x" : role === "metric" ? "y" : "z"})` }}
          // The letter is the label; the plain-language hint is what a first-time
          // user actually needs, so it is not hidden behind a tooltip.
          aria-label={`${letter}: ${hint}`}
        >
          {letter}
        </span>
        <span className="text-[0.625rem] uppercase tracking-wider text-faint">{hint}</span>
      </div>

      <p
        className={`mt-0.5 text-sm ${
          role === "metric" ? "font-mono text-[0.8125rem] tabular-nums" : ""
        } ${invalid ? "text-muted" : ""}`}
        data-numeric={role === "metric" ? "" : undefined}
      >
        {text.length > 0 ? text : <span className="text-faint italic">Not written yet</span>}
      </p>

      {problems.length > 0 ? (
        <ul className="mt-1.5 space-y-1">
          {problems.map((problem) => (
            <li key={problem.code} className="flex flex-wrap items-baseline gap-1.5">
              <Chip tone={problem.code === "missing_metric" ? "danger" : "warn"}>
                {VIOLATION_LABELS[problem.code]}
              </Chip>
              {/* The backend writes `message` and `hint` against this specific
                  bullet, so they are shown verbatim rather than replaced with
                  generic copy. */}
              <span className="text-xs text-muted">{problem.message}</span>
              {problem.hint !== null ? (
                <span className="text-xs text-faint">{problem.hint}</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function XyzBullet({ bullet }: { bullet: XyzParts }) {
  const compliant = bullet.violations.length === 0;

  return (
    <article className="space-y-2">
      <div className="space-y-2">
        <Slot role="action" text={bullet.action} violations={bullet.violations} />
        <Slot role="metric" text={bullet.metric} violations={bullet.violations} />
        <Slot role="method" text={bullet.method} violations={bullet.violations} />
      </div>

      {bullet.skills !== undefined && bullet.skills.length > 0 ? (
        <div className="flex flex-wrap gap-1 pl-3.5">
          {bullet.skills.map((skill) => (
            <Chip key={skill}>{skill}</Chip>
          ))}
        </div>
      ) : null}

      {compliant ? (
        <p className="pl-3.5 text-xs text-ok">Passes XYZ. Ready to include in a render.</p>
      ) : null}
    </article>
  );
}
