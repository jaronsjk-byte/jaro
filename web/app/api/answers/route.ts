import { z } from "zod";
import { askAgent } from "../../../lib/agent";
import * as breaker from "../../../lib/breaker";
import { AnswerError, toClientError } from "../../../lib/errors";
import { aiAnswersEnabled, inRollout } from "../../../lib/flags";
import { retrieve } from "../../../lib/kb";
import {
  buildInstructions,
  HANDOFF_MESSAGE,
  INSTRUCTIONS_VERSION,
  NOT_COVERED_MARKER,
} from "../../../lib/prompts";
import { consume } from "../../../lib/ratelimit";
import { checkSafety } from "../../../lib/safety";
import { emit } from "../../../lib/telemetry";

export const runtime = "nodejs";
export const maxDuration = 60;

const BodySchema = z.object({
  question: z.string().min(1).max(2000),
  contextId: z.string().max(128).optional(),
});

/** Opaque, non-identifying key for logs and rate limiting. */
function hash(s: string): string {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = (h * 33) ^ s.charCodeAt(i);
  return (h >>> 0).toString(36);
}

function sse(obj: unknown): string {
  return `data: ${JSON.stringify(obj)}\n\n`;
}

/**
 * Emits a completed answer as progressive SSE chunks.
 *
 * NOTE: the upstream call is non-streaming. Perplexity's streaming event schema
 * for /v1/agent is not yet published, and parsing an unconfirmed event shape is
 * a worse failure mode than a slower first token. The client contract below is
 * already streaming, so switching to upstream SSE later needs no client change.
 * TODO(ai-answers): move to upstream streaming once the event schema is documented.
 */
function streamText(text: string): string[] {
  return text.match(/\S+\s*/g) ?? [text];
}

export async function POST(req: Request): Promise<Response> {
  const started = Date.now();

  // Flag off: the endpoint does not exist at all.
  if (!aiAnswersEnabled()) {
    return new Response(JSON.stringify({ error: "Not found" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    });
  }

  let question: string;
  let contextId: string;
  try {
    const parsed = BodySchema.parse(await req.json());
    question = parsed.question;
    contextId = parsed.contextId ?? "anonymous";
  } catch {
    return new Response(JSON.stringify({ code: "invalid_request", message: "That question couldn't be read." }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  if (!inRollout(contextId)) {
    return new Response(JSON.stringify({ error: "Not found" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    });
  }

  const base = {
    event: "answer" as const,
    instructions_version: INSTRUCTIONS_VERSION,
    breaker_state: breaker.state(),
  };

  // 1. Safety gate — runs before anything is sent upstream.
  const verdict = checkSafety(question);
  if (verdict.blocked) {
    emit({ event: "safety_escalation", context_hash: hash(contextId) });
    emit({
      ...base,
      latency_ms: Date.now() - started,
      input_tokens: 0,
      output_tokens: 0,
      cost_usd: 0,
      error_code: null,
      fallback_used: false,
      citation_count: 0,
      model: null,
      kb_hit: false,
      safety_escalation: true,
      retries: 0,
    });
    return sseResponse([
      sse({ type: "token", text: verdict.message }),
      sse({ type: "done", meta: { safety: true, citations: [] } }),
    ]);
  }

  try {
    // 2. Cost guardrails before we spend anything.
    consume(contextId);
    breaker.assertClosed();

    // 3. Retrieve grounding. No articles means we decline rather than improvise.
    const articles = retrieve(question);
    if (articles.length === 0) {
      emit({
        ...base,
        latency_ms: Date.now() - started,
        input_tokens: 0,
        output_tokens: 0,
        cost_usd: 0,
        error_code: null,
        fallback_used: true,
        citation_count: 0,
        model: null,
        kb_hit: false,
        safety_escalation: false,
        retries: 0,
      });
      return sseResponse([
        sse({ type: "token", text: HANDOFF_MESSAGE }),
        sse({ type: "done", meta: { handoff: true, citations: [] } }),
      ]);
    }

    // 4. Ask.
    const result = await askAgent(question, buildInstructions(articles));
    breaker.recordSuccess();

    const declined = result.text.trim().startsWith(NOT_COVERED_MARKER);
    const answer = declined ? HANDOFF_MESSAGE : result.text;
    const citations = declined
      ? []
      : articles.map((a) => ({ id: a.id, title: a.title }));

    emit({
      ...base,
      latency_ms: Date.now() - started,
      input_tokens: result.inputTokens,
      output_tokens: result.outputTokens,
      cost_usd: result.costUsd,
      error_code: null,
      fallback_used: declined,
      citation_count: citations.length,
      model: result.model,
      kb_hit: true,
      safety_escalation: false,
      retries: result.retries,
    });

    const chunks = streamText(answer).map((t) => sse({ type: "token", text: t }));
    chunks.push(sse({ type: "citations", items: citations }));
    chunks.push(
      sse({
        type: "done",
        meta: {
          citations,
          cost_usd: result.costUsd,
          latency_ms: Date.now() - started,
          model: result.model,
        },
      }),
    );
    return sseResponse(chunks);
  } catch (err) {
    const isRateLimit = err instanceof AnswerError && err.code === "rate_limited";
    const isBreaker = err instanceof AnswerError && err.code === "breaker_open";
    // Rate limiting and an already-open breaker are our own decisions, not
    // upstream failures, so they must not push the breaker further open.
    if (!isRateLimit && !isBreaker) breaker.recordFailure();

    const client = toClientError(err);
    emit({
      ...base,
      latency_ms: Date.now() - started,
      input_tokens: 0,
      output_tokens: 0,
      cost_usd: 0,
      error_code: client.code,
      fallback_used: true,
      citation_count: 0,
      model: null,
      kb_hit: false,
      safety_escalation: false,
      retries: 0,
    });

    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (client.retryAfter) headers["Retry-After"] = String(client.retryAfter);

    // `fallback: true` tells ChillMind to use its existing local intent reply.
    return new Response(
      JSON.stringify({ code: client.code, message: client.message, fallback: true }),
      { status: client.status, headers },
    );
  }
}

function sseResponse(chunks: string[]): Response {
  const stream = new ReadableStream({
    start(controller) {
      const enc = new TextEncoder();
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
