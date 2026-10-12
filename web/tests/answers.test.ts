import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  askAgent,
  parseAgentResponse,
  MAX_RETRIES,
} from "../lib/agent";
import * as breaker from "../lib/breaker";
import { ConfigError, MalformedResponseError, TimeoutError, UpstreamError } from "../lib/errors";
import { aiAnswersEnabled, inRollout } from "../lib/flags";
import { retrieve } from "../lib/kb";
import { checkSafety, CRISIS_MESSAGE } from "../lib/safety";
import { consume, LIMITS, __resetRateLimit } from "../lib/ratelimit";
import { __drainMetrics, readCost } from "../lib/telemetry";

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

const KEY = "PERPLEXITY_API_KEY";

function okPayload(text = "Open Notifications from the sidebar. [notifications]") {
  return {
    id: "resp_1",
    status: "completed",
    error: null,
    model: "openai/gpt-5.6-luna",
    output: [
      {
        type: "search_results",
        results: [{ url: "https://help.chillmind.app/notifications", title: "Notifications" }],
      },
      {
        type: "message",
        role: "assistant",
        content: [{ type: "output_text", text }],
      },
    ],
    usage: {
      input_tokens: 2100,
      output_tokens: 180,
      cost: { currency: "USD", total_cost: 0.00135 },
    },
  };
}

function httpOk(body: unknown) {
  return {
    ok: true,
    status: 200,
    headers: { get: () => null },
    json: async () => body,
  } as unknown as Response;
}

function httpErr(status: number, retryAfter?: string) {
  return {
    ok: false,
    status,
    headers: { get: (h: string) => (h === "Retry-After" ? retryAfter ?? null : null) },
    json: async () => ({}),
  } as unknown as Response;
}

function abortError() {
  const e = new Error("aborted");
  e.name = "AbortError";
  return e;
}

/**
 * The route reads flags and the API key per request, so a single import is
 * enough — and it keeps the route sharing this file's telemetry module instance
 * so emitted metrics are observable.
 */
async function loadRoute() {
  const mod = await import("../app/api/answers/route");
  return mod.POST;
}

function req(body: unknown) {
  return { json: async () => body } as unknown as Request;
}

async function readSse(res: Response): Promise<any[]> {
  const text = await res.text();
  return text
    .split("\n\n")
    .filter((c) => c.startsWith("data: "))
    .map((c) => JSON.parse(c.slice(6)));
}

beforeEach(() => {
  vi.useRealTimers();
  __resetRateLimit();
  breaker.__resetBreaker();
  __drainMetrics();
  process.env[KEY] = "test-key-not-real";
  process.env.AI_ANSWERS_ENABLED = "true";
  process.env.AI_ANSWERS_ROLLOUT_PCT = "100";
  delete process.env.DATADOG_API_KEY;
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// 1. upstream timeout
// ---------------------------------------------------------------------------
describe("failure path: timeout", () => {
  it("surfaces a timeout as a client error and signals fallback, without crashing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abortError()));

    const POST = await loadRoute();
    const res = await POST(req({ question: "how do i reset my streak", contextId: "u1" }));

    expect(res.status).toBe(504);
    const body = await res.json();
    expect(body.code).toBe("timeout");
    expect(body.fallback).toBe(true);
    // No stack trace or internal detail leaks to the student.
    expect(JSON.stringify(body)).not.toMatch(/AbortError|api\.perplexity/i);
  });

  it("askAgent throws TimeoutError on abort", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abortError()));
    await expect(askAgent("q", "i", ["openai/gpt-5.6-luna"])).rejects.toBeInstanceOf(TimeoutError);
  });
});

// ---------------------------------------------------------------------------
// 2. 429 with Retry-After
// ---------------------------------------------------------------------------
describe("failure path: upstream 429", () => {
  it("respects Retry-After, retries, then succeeds", async () => {
    const sleeps: number[] = [];
    vi.spyOn(global, "setTimeout").mockImplementation(((fn: any, ms?: number) => {
      if (ms && ms > 100) sleeps.push(ms);
      fn();
      return 0 as any;
    }) as any);

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(httpErr(429, "2"))
      .mockResolvedValueOnce(httpOk(okPayload()));
    vi.stubGlobal("fetch", fetchMock);

    const result = await askAgent("q", "i", ["openai/gpt-5.6-luna"]);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.retries).toBe(1);
    // Retry-After: 2 must be honoured as 2000ms, not the default backoff.
    expect(sleeps).toContain(2000);
  });

  it("surfaces a persistent 429 as rate limited upstream", async () => {
    vi.spyOn(global, "setTimeout").mockImplementation(((fn: any) => {
      fn();
      return 0 as any;
    }) as any);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(httpErr(429, "1")));

    await expect(askAgent("q", "i", ["openai/gpt-5.6-luna"])).rejects.toBeInstanceOf(UpstreamError);
  });
});

// ---------------------------------------------------------------------------
// 3. 500 retried twice then failed
// ---------------------------------------------------------------------------
describe("failure path: upstream 500", () => {
  it("retries exactly MAX_RETRIES times per model then gives up", async () => {
    vi.spyOn(global, "setTimeout").mockImplementation(((fn: any) => {
      fn();
      return 0 as any;
    }) as any);
    const fetchMock = vi.fn().mockResolvedValue(httpErr(500));
    vi.stubGlobal("fetch", fetchMock);

    await expect(askAgent("q", "i", ["openai/gpt-5.6-luna"])).rejects.toBeInstanceOf(UpstreamError);
    expect(fetchMock).toHaveBeenCalledTimes(MAX_RETRIES + 1);
  });
});

// ---------------------------------------------------------------------------
// 4. 4xx is never retried
// ---------------------------------------------------------------------------
describe("failure path: client 4xx", () => {
  it.each([400, 401, 403, 404, 422])("does not retry %i", async (status) => {
    const fetchMock = vi.fn().mockResolvedValue(httpErr(status));
    vi.stubGlobal("fetch", fetchMock);

    await expect(askAgent("q", "i", ["openai/gpt-5.6-luna"])).rejects.toBeInstanceOf(UpstreamError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// 5. malformed responses
// ---------------------------------------------------------------------------
describe("failure path: malformed response", () => {
  it("rejects a non-JSON body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        headers: { get: () => null },
        json: async () => {
          throw new SyntaxError("Unexpected token <");
        },
      } as unknown as Response),
    );
    await expect(askAgent("q", "i", ["openai/gpt-5.6-luna"])).rejects.toBeInstanceOf(
      MalformedResponseError,
    );
  });

  it.each([
    ["null body", null],
    ["missing output", { id: "x", usage: {} }],
    ["output not an array", { output: "nope" }],
    ["message with no text", { output: [{ type: "message", content: [] }] }],
    ["error field set", { error: { message: "boom" }, output: [] }],
  ])("rejects %s", (_label, payload) => {
    expect(() => parseAgentResponse(payload)).toThrow(MalformedResponseError);
  });

  it("is not retried, since a bad body will not fix itself", async () => {
    const fetchMock = vi.fn().mockResolvedValue(httpOk({ output: "nope" }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(askAgent("q", "i", ["openai/gpt-5.6-luna"])).rejects.toBeInstanceOf(
      MalformedResponseError,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// 6. zero citations
// ---------------------------------------------------------------------------
describe("edge case: no search_results in output", () => {
  it("still returns an answer with an empty citation list", async () => {
    const payload = okPayload();
    payload.output = payload.output.filter((o: any) => o.type !== "search_results");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(httpOk(payload)));

    const result = await askAgent("q", "i", ["openai/gpt-5.6-luna"]);
    expect(result.citations).toEqual([]);
    expect(result.text).not.toBe("");
  });
});

// ---------------------------------------------------------------------------
// 7. missing API key
// ---------------------------------------------------------------------------
describe("failure path: missing PERPLEXITY_API_KEY", () => {
  it("throws ConfigError naming the variable, never the value", async () => {
    delete process.env[KEY];
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(askAgent("q", "i", ["openai/gpt-5.6-luna"])).rejects.toBeInstanceOf(ConfigError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("route returns 503 and the key never appears in the response or logs", async () => {
    const secret = "super-secret-key-value";
    process.env[KEY] = secret;
    const logs: string[] = [];
    vi.spyOn(console, "log").mockImplementation((...a: unknown[]) => {
      logs.push(a.map(String).join(" "));
    });
    // Force a ConfigError path by blanking the key at call time.
    process.env[KEY] = "";
    vi.stubGlobal("fetch", vi.fn());

    const POST = await loadRoute();
    const res = await POST(req({ question: "how do i turn off notifications", contextId: "u2" }));
    const text = await res.text();

    expect(res.status).toBe(503);
    expect(text).not.toContain(secret);
    expect(logs.join("\n")).not.toContain(secret);
  });
});

// ---------------------------------------------------------------------------
// 8. circuit breaker
// ---------------------------------------------------------------------------
describe("failure path: circuit breaker", () => {
  it("opens after 5 consecutive failures and short-circuits without calling upstream", async () => {
    for (let i = 0; i < breaker.FAILURE_THRESHOLD; i++) breaker.recordFailure();
    expect(breaker.isOpen()).toBe(true);

    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const POST = await loadRoute();
    const res = await POST(req({ question: "where are my notifications", contextId: "u3" }));

    expect(res.status).toBe(503);
    const body = await res.json();
    expect(body.code).toBe("breaker_open");
    expect(body.fallback).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("closes again after the open window elapses", () => {
    const t0 = 1_000_000;
    for (let i = 0; i < breaker.FAILURE_THRESHOLD; i++) breaker.recordFailure(t0);
    expect(breaker.isOpen(t0 + 1_000)).toBe(true);
    expect(breaker.isOpen(t0 + breaker.OPEN_MS + 1)).toBe(false);
  });

  it("a success resets the failure count", () => {
    breaker.recordFailure();
    breaker.recordFailure();
    breaker.recordSuccess();
    for (let i = 0; i < breaker.FAILURE_THRESHOLD - 1; i++) breaker.recordFailure();
    expect(breaker.isOpen()).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 9. per-user rate limit
// ---------------------------------------------------------------------------
describe("failure path: per-user rate limit", () => {
  it(`rejects call ${LIMITS.perHour + 1} within the hour with Retry-After`, () => {
    const now = 1_700_000_000_000;
    for (let i = 0; i < LIMITS.perHour; i++) consume("heavy-user", now + i);
    try {
      consume("heavy-user", now + LIMITS.perHour);
      throw new Error("expected a rate limit rejection");
    } catch (err: any) {
      expect(err.code).toBe("rate_limited");
      expect(err.status).toBe(429);
      expect(err.retryAfter).toBeGreaterThan(0);
    }
  });

  it("isolates users from each other", () => {
    const now = 1_700_000_000_000;
    for (let i = 0; i < LIMITS.perHour; i++) consume("user-a", now + i);
    expect(() => consume("user-b", now)).not.toThrow();
  });

  it("route returns 429 with a Retry-After header and never calls upstream", async () => {
    const fetchMock = vi.fn().mockResolvedValue(httpOk(okPayload()));
    vi.stubGlobal("fetch", fetchMock);
    const POST = await loadRoute();

    let res!: Response;
    for (let i = 0; i < LIMITS.perHour + 1; i++) {
      res = await POST(req({ question: "how do i reset my streak", contextId: "burst" }));
    }

    expect(res.status).toBe(429);
    expect(res.headers.get("Retry-After")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(LIMITS.perHour);
  });
});

// ---------------------------------------------------------------------------
// 10. crisis input never reaches the model
// ---------------------------------------------------------------------------
describe("safety: crisis phrasing", () => {
  it.each([
    "i want to kill myself",
    "I've been thinking about suicide",
    "how do i stop hurting myself",
    "i want to die",
  ])("blocks %j before any API call", (question) => {
    expect(checkSafety(question).blocked).toBe(true);
  });

  it("does not block ordinary support questions", () => {
    expect(checkSafety("how do i reset my breathing streak").blocked).toBe(false);
    expect(checkSafety("the focus drill is broken").blocked).toBe(false);
  });

  it("route returns the static helpline message and NEVER calls the model", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const POST = await loadRoute();
    const res = await POST(req({ question: "i want to kill myself", contextId: "u9" }));
    const events = await readSse(res);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(res.status).toBe(200);
    const answer = events.filter((e) => e.type === "token").map((e) => e.text).join("");
    expect(answer).toBe(CRISIS_MESSAGE);
    expect(answer).toContain("14416");

    const metrics = __drainMetrics();
    expect(metrics.some((m: any) => m.event === "safety_escalation")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 11. feature flag off
// ---------------------------------------------------------------------------
describe("feature flag", () => {
  it("defaults to off when the env var is unset", () => {
    delete process.env.AI_ANSWERS_ENABLED;
    expect(aiAnswersEnabled()).toBe(false);
  });

  it("route returns 404 while the flag is off, without calling upstream", async () => {
    process.env.AI_ANSWERS_ENABLED = "false";
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const POST = await loadRoute();
    const res = await POST(req({ question: "how do i reset my streak", contextId: "u4" }));

    expect(res.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rollout bucketing is stable and respects 0 percent", () => {
    process.env.AI_ANSWERS_ROLLOUT_PCT = "0";
    expect(inRollout("someone")).toBe(false);
    process.env.AI_ANSWERS_ROLLOUT_PCT = "100";
    expect(inRollout("someone")).toBe(true);
    process.env.AI_ANSWERS_ROLLOUT_PCT = "50";
    expect(inRollout("stable-id")).toBe(inRollout("stable-id"));
  });
});

// ---------------------------------------------------------------------------
// 12. model fallback chain
// ---------------------------------------------------------------------------
describe("model fallback chain", () => {
  it("falls through to the second model when the first exhausts its retries", async () => {
    vi.spyOn(global, "setTimeout").mockImplementation(((fn: any) => {
      fn();
      return 0 as any;
    }) as any);

    const bodies: string[] = [];
    const fetchMock = vi.fn().mockImplementation((_url: string, init: any) => {
      bodies.push(init.body);
      const model = JSON.parse(init.body).model;
      if (model === "openai/gpt-5.6-luna") return Promise.resolve(httpErr(500));
      return Promise.resolve(httpOk(okPayload()));
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await askAgent("q", "i", ["openai/gpt-5.6-luna", "perplexity/sonar"]);

    expect(result.text).not.toBe("");
    expect(bodies.some((b) => b.includes("perplexity/sonar"))).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(MAX_RETRIES + 2);
  });
});

// ---------------------------------------------------------------------------
// 13. empty KB retrieval must not hallucinate
// ---------------------------------------------------------------------------
describe("grounding: KB miss", () => {
  it("retrieval returns nothing for an unrelated question", () => {
    expect(retrieve("what is the capital of Mongolia")).toHaveLength(0);
  });

  it("retrieval finds the right article for a real support question", () => {
    const hits = retrieve("how do i clear all my notifications");
    expect(hits.length).toBeGreaterThan(0);
    expect(hits.map((h) => h.id)).toContain("notifications");
  });

  it("route declines with a handoff and never calls the model on a KB miss", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const POST = await loadRoute();
    const res = await POST(
      req({ question: "who won the 1998 world cup final", contextId: "u5" }),
    );
    const events = await readSse(res);
    const answer = events.filter((e) => e.type === "token").map((e) => e.text).join("");

    expect(fetchMock).not.toHaveBeenCalled();
    expect(answer).toContain("couldn't find that");
    expect(events.at(-1).meta.handoff).toBe(true);
  });

  it("converts the model's NOT_COVERED marker into a handoff", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(httpOk(okPayload("NOT_COVERED"))));

    const POST = await loadRoute();
    const res = await POST(req({ question: "how do i reset my streak", contextId: "u6" }));
    const events = await readSse(res);
    const answer = events.filter((e) => e.type === "token").map((e) => e.text).join("");

    expect(answer).toContain("support@chillmind.app");
    const metrics = __drainMetrics().filter((m: any) => m.event === "answer");
    expect((metrics.at(-1) as any).fallback_used).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 14. telemetry
// ---------------------------------------------------------------------------
describe("telemetry", () => {
  it("reads cost_usd from usage.cost.total_cost", () => {
    expect(readCost({ cost: { total_cost: 0.00135 } })).toBe(0.00135);
  });

  it("defaults cost to 0 rather than throwing on a missing usage block", () => {
    expect(readCost(undefined)).toBe(0);
    expect(readCost({})).toBe(0);
    expect(readCost({ cost: {} })).toBe(0);
    expect(readCost({ cost: { total_cost: "1.00" } })).toBe(0);
  });

  it("emits latency, tokens, cost and citation count for a served answer", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(httpOk(okPayload())));

    const POST = await loadRoute();
    const res = await POST(req({ question: "how do i clear all notifications", contextId: "u7" }));
    expect(res.status).toBe(200);
    await res.text();

    const m = __drainMetrics().filter((x: any) => x.event === "answer").at(-1) as any;
    expect(m.cost_usd).toBe(0.00135);
    expect(m.input_tokens).toBe(2100);
    expect(m.output_tokens).toBe(180);
    expect(m.error_code).toBeNull();
    expect(m.citation_count).toBeGreaterThan(0);
    expect(m.instructions_version).toBe("v1");
    expect(m.latency_ms).toBeGreaterThanOrEqual(0);
  });

  it("records an error_code and fallback_used when upstream fails", async () => {
    vi.spyOn(global, "setTimeout").mockImplementation(((fn: any) => {
      fn();
      return 0 as any;
    }) as any);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(httpErr(500)));

    const POST = await loadRoute();
    const res = await POST(req({ question: "how do i reset my streak", contextId: "u8" }));
    await res.json();

    const m = __drainMetrics().filter((x: any) => x.event === "answer").at(-1) as any;
    expect(m.error_code).toBe("upstream");
    expect(m.fallback_used).toBe(true);
    expect(m.cost_usd).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// request validation
// ---------------------------------------------------------------------------
describe("request validation", () => {
  it.each([
    ["empty question", { question: "" }],
    ["missing question", { contextId: "x" }],
    ["wrong type", { question: 42 }],
  ])("rejects %s with 400", async (_label, body) => {
    vi.stubGlobal("fetch", vi.fn());
    const POST = await loadRoute();
    const res = await POST(req(body));
    expect(res.status).toBe(400);
  });

  it("rejects an unparseable body", async () => {
    vi.stubGlobal("fetch", vi.fn());
    const POST = await loadRoute();
    const res = await POST({
      json: async () => {
        throw new SyntaxError("bad");
      },
    } as unknown as Request);
    expect(res.status).toBe(400);
  });
});
