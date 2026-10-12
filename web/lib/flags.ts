/**
 * Feature flags. Every flag defaults to OFF so that an unconfigured
 * environment behaves exactly like ChillMind did before this feature existed.
 */
function boolEnv(name: string, dflt = false): boolean {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return dflt;
  return ["1", "true", "yes", "on"].includes(raw.trim().toLowerCase());
}

export function aiAnswersEnabled(): boolean {
  return boolEnv("AI_ANSWERS_ENABLED", false);
}

/** Rollout percentage 0-100, applied per contextId. */
export function rolloutPercent(): number {
  const n = Number(process.env.AI_ANSWERS_ROLLOUT_PCT ?? "100");
  if (!Number.isFinite(n)) return 0;
  return Math.min(100, Math.max(0, n));
}

/** Stable per-user bucketing so a user's experience doesn't flap. */
export function inRollout(contextId: string): boolean {
  const pct = rolloutPercent();
  if (pct >= 100) return true;
  if (pct <= 0) return false;
  let h = 2166136261;
  for (let i = 0; i < contextId.length; i++) {
    h ^= contextId.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (Math.abs(h) % 100) < pct;
}
