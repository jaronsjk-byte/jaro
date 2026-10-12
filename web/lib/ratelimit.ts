import { RateLimitedError } from "./errors";

/**
 * Per-user token buckets: 10 answers/hour and 50 answers/day.
 *
 * IMPORTANT: this store is in-memory and therefore PER SERVERLESS INSTANCE.
 * It is a cost guardrail, not a security control — a user spread across many
 * cold instances can exceed these numbers. Before real production traffic,
 * swap `store` for Upstash Redis (or Vercel KV) so the counters are shared.
 * The interface below is deliberately narrow to make that swap mechanical.
 */

const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;

export const LIMITS = { perHour: 10, perDay: 50 };

interface Bucket {
  hourStamps: number[];
  dayStamps: number[];
}

const store = new Map<string, Bucket>();

/** Test-only reset so suites don't leak state into each other. */
export function __resetRateLimit(): void {
  store.clear();
}

function prune(stamps: number[], now: number, window: number): number[] {
  return stamps.filter((t) => now - t < window);
}

/**
 * Records an attempt. Throws RateLimitedError when over budget.
 * Called BEFORE the upstream request so users cannot burn our quota.
 */
export function consume(contextId: string, now = Date.now()): void {
  const key = contextId || "anonymous";
  const bucket = store.get(key) ?? { hourStamps: [], dayStamps: [] };

  bucket.hourStamps = prune(bucket.hourStamps, now, HOUR_MS);
  bucket.dayStamps = prune(bucket.dayStamps, now, DAY_MS);

  if (bucket.hourStamps.length >= LIMITS.perHour) {
    const oldest = Math.min(...bucket.hourStamps);
    store.set(key, bucket);
    throw new RateLimitedError(Math.ceil((HOUR_MS - (now - oldest)) / 1000));
  }
  if (bucket.dayStamps.length >= LIMITS.perDay) {
    const oldest = Math.min(...bucket.dayStamps);
    store.set(key, bucket);
    throw new RateLimitedError(Math.ceil((DAY_MS - (now - oldest)) / 1000));
  }

  bucket.hourStamps.push(now);
  bucket.dayStamps.push(now);
  store.set(key, bucket);
}
