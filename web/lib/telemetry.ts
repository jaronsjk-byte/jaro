import type { ErrorCode } from "./errors";

/**
 * One structured record per answer attempt. These four fields are the ones the
 * operator asked to be alerted on: latency, tokens, error rate, cost/answer.
 */
export interface AnswerMetrics {
  event: "answer";
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  /** Real dollars, read from the Agent API's usage.cost.total_cost. */
  cost_usd: number;
  error_code: ErrorCode | null;
  fallback_used: boolean;
  citation_count: number;
  instructions_version: string;
  model: string | null;
  kb_hit: boolean;
  safety_escalation: boolean;
  breaker_state: "open" | "closed";
  retries: number;
}

export type SafetyEvent = {
  event: "safety_escalation";
  context_hash: string;
};

type Emitted = AnswerMetrics | SafetyEvent;

/** Test hook: captures emitted records without touching the network. */
const sink: Emitted[] = [];
export function __drainMetrics(): Emitted[] {
  return sink.splice(0, sink.length);
}

export function emit(record: Emitted): void {
  sink.push(record);
  // Structured stdout line — picked up by Vercel logs regardless of Datadog.
  console.log(JSON.stringify({ ...record, ts: new Date().toISOString() }));
  void shipToDatadog(record);
}

/**
 * Datadog is not connected yet. This no-ops cleanly when DATADOG_API_KEY is
 * absent so the feature never depends on monitoring being wired up.
 */
async function shipToDatadog(record: Emitted): Promise<void> {
  const key = process.env.DATADOG_API_KEY;
  if (!key) return;
  const site = process.env.DATADOG_SITE || "datadoghq.com";
  try {
    await fetch(`https://http-intake.logs.${site}/api/v2/logs`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "DD-API-KEY": key },
      body: JSON.stringify([
        { ddsource: "chillmind", service: "ai-answers", message: JSON.stringify(record) },
      ]),
    });
  } catch {
    // Never let telemetry failures affect the user-facing answer.
  }
}

/** Parses cost defensively — a missing usage block must not throw. */
export function readCost(usage: unknown): number {
  const u = usage as { cost?: { total_cost?: unknown } } | null | undefined;
  const v = u?.cost?.total_cost;
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}
