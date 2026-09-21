import type { Bullet } from "@resunet/shared";
import type { SkillWeight } from "@/components/physics/SkillPile";

/**
 * Skill weights, derived from bullets.
 *
 * Only compliant bullets count. That is the point of the figure: the pile shows
 * what the user can *evidence* with a quantified achievement, not what they have
 * claimed somewhere. A skill attached only to a bullet with no metric has not
 * earned a place in it.
 *
 * BACKEND FOLLOW-UP: the dashboard currently derives this from one resume's
 * bullets because there is no aggregate endpoint. That is an N+1 waiting to
 * happen -- a user with twelve resumes would need twelve requests to get a true
 * picture. The right shape is a single `GET /profile/skills` backed by a Mongo
 * aggregation over the `bullets` collection, grouping by `skills` with a
 * `$match` on `violations: []`. Until that exists, treat the pile as
 * "skills in this resume", which is what it honestly shows.
 */
export function skillCounts(bullets: Bullet[], limit = 18): SkillWeight[] {
  const counts = new Map<string, number>();

  for (const bullet of bullets) {
    if (bullet.violations.length > 0) continue;
    for (const raw of bullet.skills) {
      const skill = raw.trim();
      if (skill.length === 0) continue;
      counts.set(skill, (counts.get(skill) ?? 0) + 1);
    }
  }

  return [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    // Count first, then alphabetical, so the order is stable across renders --
    // an unstable sort would reshuffle the pile on every refresh for no reason.
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
    .slice(0, limit);
}
