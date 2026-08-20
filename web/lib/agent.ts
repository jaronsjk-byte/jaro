import {
  ConfigError,
  MalformedResponseError,
  TimeoutError,
  UpstreamError,
} from "./errors";

/**
 * Perplexity Agent API client.
 *
 * Endpoint: POST https://api.perplexity.ai/v1/agent
 * Docs: https://docs.perplexity.ai/docs/agent-api/quickstart
 *
 * Web tools are intentionally NOT enabled for support deflection: answers are
 * grounded on ChillMind's own help articles, which keeps cost at roughly
 * $0.0013/answer and prevents off-brand answers sourced from the open web.
 */

export const AGENT_URL = "https://api.perplexity.ai/v1/agent";
export const DEFAULT_MODEL_CHAIN = ["openai/gpt-5.6-luna", "perplexity/sonar"];

export const TIMEOUT_MS = 30_000;
export const MAX_RETRIES = 2;

export interface Citation {
  url: string;
  title: string;
  snippet?: string;
}

export interface AgentResult {
  text: string;
  citations: Citation[];
  model: string;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
  retries: number;
}

function apiKey(): string {
  const key = process.env.PERPLEXITY_API_KEY;
  // Never log or echo the value — only the variable name.
  if (!key || key.trim() === "") throw new ConfigError("PERPLEXITY_API_KEY");
  return key;
}

function jitteredBackoff(attempt: number): number {
  const base = 300 * 2 ** attempt;
  return base + Math.floor(Math.random() * 200);
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Extracts assistant text and search citations from the Agent API `output` array. */
export function parseAgentResponse(payload: unknown): {
  text: string;
  citations: Citation[];
  model: string;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
} {
  if (!payload || typeof payload !== "object") {
    throw new MalformedResponseError("body is not an object");
  }
  const body = payload as Record<string, any>;

  if (body.error) throw new MalformedResponseError("response carried an error field");
  if (!Array.isArray(body.output)) throw new MalformedResponseError("missing output array");

  let text = "";
  const citations: Citation[] = [];

  for (const item of body.output) {
    if (!item || typeof item !== "object") continue;
    if (item.type === "message" && Array.isArray(item.content)) {
      for (const part of item.content) {
        if (part?.type === "output_text" && typeof part.text === "string") {
          text += part.text;
        }
      }
    }
    if (item.type === "search_results" && Array.isArray(item.results)) {
      for (const r of item.results) {
        if (r?.url) {
          citations.push({ url: r.url, title: r.title ?? r.url, snippet: r.snippet });
        }
      }
    }
  }

  if (text.trim() === "") throw new MalformedResponseError("no assistant text in output");

  const usage = (body.usage ?? {}) as Record<string, any>;
  const cost = (usage.cost ?? {}) as Record<string, any>;

  return {
    text,
    citations,
    model: typeof body.model === "string" ? body.model : "unknown",
    inputTokens: Number(usage.input_tokens ?? 0) || 0,
    outputTokens: Number(usage.output_tokens ?? 0) || 0,
    costUsd: typeof cost.total_cost === "number" ? cost.total_cost : 0,
  };
}

async function callOnce(
  model: string,
  input: string,
  instructions: string,
): Promise<ReturnType<typeof parseAgentResponse>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(AGENT_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey()}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model,
        input,
        instructions,
        max_output_tokens: 700,
        max_steps: 1,
        tools: [],
      }),
      signal: controller.signal,
    });

    if (!res.ok) {
      const ra = Number(res.headers?.get?.("Retry-After") ?? "");
      throw new UpstreamError(res.status, Number.isFinite(ra) && ra > 0 ? ra : undefined);
    }

    let json: unknown;
    try {
      json = await res.json();
    } catch {
      throw new MalformedResponseError("body was not valid JSON");
    }
    return parseAgentResponse(json);
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") throw new TimeoutError(TIMEOUT_MS);
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Calls the Agent API with bounded retries and a model fallback chain.
 * Retries only on 429, 5xx and timeouts. 4xx are caller errors and fail fast.
 */
export async function askAgent(
  input: string,
  instructions: string,
  modelChain: string[] = DEFAULT_MODEL_CHAIN,
): Promise<AgentResult> {
  let retries = 0;
  let lastErr: unknown;

  for (const model of modelChain) {
    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
      try {
        const parsed = await callOnce(model, input, instructions);
        return { ...parsed, retries };
      } catch (err) {
        lastErr = err;

        const isUpstream = err instanceof UpstreamError;
        const retryable =
          err instanceof TimeoutError || (isUpstream && (err as UpstreamError).retryable);

        if (!retryable) break; // e.g. 400/401/403/404, or a malformed body

        if (attempt < MAX_RETRIES) {
          retries++;
          const retryAfter = isUpstream ? (err as UpstreamError).retryAfter : undefined;
          await sleep(retryAfter ? retryAfter * 1000 : jitteredBackoff(attempt));
          continue;
        }
      }
    }
    // Exhausted this model — try the next in the chain.
  }

  throw lastErr;
}
