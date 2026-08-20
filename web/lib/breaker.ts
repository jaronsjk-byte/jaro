import { BreakerOpenError } from "./errors";

/**
 * Circuit breaker: 5 consecutive failures opens the circuit for 60s.
 * While open we never call the upstream — callers fall back to ChillMind's
 * existing local intent responses instead of showing users an error.
 */

export const FAILURE_THRESHOLD = 5;
export const OPEN_MS = 60_000;

let consecutiveFailures = 0;
let openedAt: number | null = null;

export function __resetBreaker(): void {
  consecutiveFailures = 0;
  openedAt = null;
}

export function isOpen(now = Date.now()): boolean {
  if (openedAt === null) return false;
  if (now - openedAt >= OPEN_MS) {
    // Half-open: allow one probe through.
    openedAt = null;
    consecutiveFailures = 0;
    return false;
  }
  return true;
}

/** Throws if the circuit is open. Call immediately before the upstream request. */
export function assertClosed(now = Date.now()): void {
  if (isOpen(now)) throw new BreakerOpenError();
}

export function recordSuccess(): void {
  consecutiveFailures = 0;
  openedAt = null;
}

export function recordFailure(now = Date.now()): void {
  consecutiveFailures += 1;
  if (consecutiveFailures >= FAILURE_THRESHOLD) {
    openedAt = now;
  }
}

export function state(now = Date.now()): "open" | "closed" {
  return isOpen(now) ? "open" : "closed";
}
