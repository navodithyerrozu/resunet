import { APPLICATION_STAGES } from "@resunet/shared";

import { Chip } from "@/components/ui/primitives";

import type { ApplicationStatus } from "@resunet/shared";
import type { ChipTone } from "@/components/ui/primitives";

/**
 * An application's status as a chip.
 *
 * The tone map is an exhaustive `Record` over the backend enum, matching
 * `APPLICATION_STAGES` in the shared package. Adding a status to the Python enum
 * breaks this file at compile time, which is the intent -- a new status silently
 * rendering as a grey pill is how a "blocked_by_captcha" state ends up invisible
 * to the person who needs to act on it.
 *
 * `failed` is danger while `rejected` is neutral, which looks inconsistent and is
 * not: a failed submission is our problem and the user can retry it, whereas a
 * rejection is an outcome. Colouring rejections red would turn the tracker into a
 * wall of alarm about things the user cannot change.
 */
const STATUS_TONE: Record<ApplicationStatus, ChipTone> = {
  draft: "neutral",
  queued: "neutral",
  submitting: "accent",
  awaiting_approval: "warn",
  submitted: "ok",
  acknowledged: "ok",
  screening: "ok",
  interviewing: "ok",
  offer: "ok",
  failed: "danger",
  blocked: "danger",
  needs_review: "warn",
  rejected: "neutral",
  withdrawn: "neutral",
  ghosted: "neutral",
};

export function StatusChip({ status }: { status: ApplicationStatus }) {
  return <Chip tone={STATUS_TONE[status]}>{APPLICATION_STAGES[status].label}</Chip>;
}
