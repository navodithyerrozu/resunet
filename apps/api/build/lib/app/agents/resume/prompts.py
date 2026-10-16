"""Prompts for the resume-generation agent.

Kept in one module, separate from graph wiring, for a practical reason:
prompts change far more often than graph topology, and mixing them means every
prompt tweak touches control-flow code. It also makes prompts diffable and
eventually versionable for A/B testing.

Prompt design principles applied throughout
-------------------------------------------
1. **Never ask the model to format a bullet.** It emits X, Y, Z as separate
   fields via structured output; assembly happens in Python. This removes an
   entire class of "it wrote a nice paragraph instead" failures.

2. **Repair prompts name the specific defect.** Generic "improve this" retries
   converge slowly. Handing back the exact violation code, its message, and its
   hint converges fast.

3. **Never invent metrics.** The single most dangerous failure mode here is a
   plausible fabricated number on a document someone submits to an employer.
   The instruction to ask rather than guess appears in every prompt that could
   produce a metric, and the graph gives the model a legitimate escape hatch --
   interrupt and ask the user -- so it never has to choose between fabricating
   and failing.
"""

from __future__ import annotations

MINING_SYSTEM = """\
You extract distinct, resume-worthy achievements from a candidate's informal \
notes about a job or project.

An achievement is something with an outcome -- something that changed because \
the candidate did it. Ignore routine duties ("attended standups", "wrote \
tests") unless the notes attach a specific result to them.

For each achievement, decide whether the notes already contain a concrete \
number, percentage, scale, or duration that quantifies the outcome. Be strict: \
"a lot", "significantly", "much faster" are NOT metrics. "40% faster", "from \
4.2% to 0.8%", "1.2M transactions" are.

Do not invent achievements the notes do not support, and do not merge two \
distinct achievements into one.\
"""

MINING_USER = """\
Candidate's notes about this role:
<notes>
{raw_notes}
</notes>

{entry_context}

Extract each distinct achievement. For each one, quote the relevant fragment \
of the notes verbatim and state whether it already contains a real metric.\
"""


DRAFTING_SYSTEM = """\
You write a single resume bullet in strict XYZ form, emitting the three \
components separately.

  X (action) -- what the candidate did. Open with a strong past-tense \
accomplishment verb: Built, Shipped, Reduced, Migrated, Automated, Designed, \
Led, Cut, Scaled. Never open with Responsible for, Worked on, Helped, \
Assisted, or Participated in.

  Y (metric) -- the quantified outcome. This MUST contain a real number, \
percentage, magnitude, or duration drawn from the notes or from the \
candidate's own answers. State what the number measures, and give a baseline \
where one exists ("from 4.2% to 0.8%" beats "by 81%").

  Z (method) -- how it was done, naming concrete tools, systems, or \
techniques. "using idempotency keys and exponential backoff in a Redis-backed \
queue", not "using best practices".

Hard rules:
- NEVER invent, estimate, or extrapolate a metric. If the notes do not contain \
one and the candidate has not supplied one, you must not fabricate a plausible \
number. Fabrication on a document someone submits to an employer is the worst \
possible outcome of this system.
- No first-person pronouns.
- The three components together must read as one natural sentence under \
{max_chars} characters.
- Write nothing but the three fields. Do not assemble them.\
"""

DRAFTING_USER = """\
Achievement to write up:
<achievement>
{achievement_text}
</achievement>

{keyword_guidance}

Produce the X, Y, and Z components.\
"""


REPAIR_SYSTEM = """\
You are revising a resume bullet that failed automated XYZ validation.

You will receive the current X, Y, and Z components and the exact list of \
violations found. Fix precisely those violations and change nothing else -- \
gratuitous rewriting loses detail the candidate supplied and tends to \
introduce new violations.

The same hard rules apply. In particular: if the violation is a missing metric \
and you do not have a real number available, do NOT invent one. Return the \
draft with the metric field unchanged; the system will ask the candidate \
directly. That is the correct behaviour, not a failure.\
"""

REPAIR_USER = """\
Current draft:
  X (action): {action}
  Y (metric): {metric}
  Z (method): {method}

Validation failures to fix:
{violations}

Attempt {attempt} of {max_attempts}. Return corrected components.\
"""


CLARIFY_SYSTEM = """\
You ask a candidate one short, specific question to obtain the missing \
measurement for a resume bullet.

Make it easy to answer. Offer the dimension you are after and, where helpful, \
a concrete example of the shape of answer you need. Prefer "roughly how many \
users did this affect per month?" over "can you quantify the impact?".

Ask exactly one question. Do not preface it with an apology or a summary of \
what you are doing.\
"""

CLARIFY_USER = """\
Achievement lacking a measurable outcome:
<achievement>
{achievement_text}
</achievement>

{prior_attempt}

Ask the candidate one question that would surface a usable metric.\
"""


def format_violations(violations: list[dict[str, object]]) -> str:
    """Render violations for the repair prompt.

    Includes the machine code alongside the prose: the code is stable across
    message wording changes, which makes prompt-effectiveness comparisons
    across versions meaningful.
    """
    if not violations:
        return "  (none)"
    lines: list[str] = []
    for v in violations:
        line = f"  - [{v.get('code')}] {v.get('message')}"
        if hint := v.get("hint"):
            line += f"\n      Fix: {hint}"
        lines.append(line)
    return "\n".join(lines)


def format_keyword_guidance(keywords: list[str]) -> str:
    """Steer wording toward the posting's vocabulary without licensing invention.

    The caveat is doing real work. Handed a bare keyword list, models will
    happily assert experience the candidate never claimed -- which is both a
    fabrication and, in an interview, an obvious one.
    """
    if not keywords:
        return ""
    terms = ", ".join(keywords[:10])
    return (
        f"The target job posting emphasises these terms: {terms}.\n"
        "Where the candidate's notes genuinely support one of these, prefer the "
        "posting's exact wording so the ATS matches it. Do NOT claim experience "
        "with any term the notes do not support."
    )


def format_entry_context(context: dict[str, object]) -> str:
    """Give the miner the role's title, company, and stack for disambiguation."""
    if not context:
        return ""
    parts = [f"{k.replace('_', ' ').title()}: {v}" for k, v in context.items() if v]
    return "Role context:\n" + "\n".join(f"  {p}" for p in parts) if parts else ""
