# AI Answers — operator guide

In-app support deflection for ChillMind, built on the
[Perplexity Agent API](https://docs.perplexity.ai/docs/agent-api/quickstart).

A student asks a how-to question ("where are my notifications?", "how do I reset my streak?"). The
answer is generated **only** from ChillMind's own help articles in `docs/help/`, and cites the article
id it used. If the articles don't cover the question, the assistant declines and offers a human handoff
rather than guessing.

## Shape

```
Streamlit (proo.py)
  └─ chillmind/ai/answers.py        HTTP client, no API key
       └─ POST /api/answers         Vercel serverless route (web/)
            ├─ safety gate          crisis phrasing → static helpline, model never called
            ├─ rate limit           10/hour, 50/day per user
            ├─ circuit breaker      5 consecutive failures → open 60s
            ├─ KB retrieval         docs/help/*.md, top 4 articles
            └─ Agent API            openai/gpt-5.6-luna → perplexity/sonar fallback
```

The Perplexity key lives only in the Vercel environment. Streamlit never sees it.

## Environment variables

| Variable | Where | Notes |
|---|---|---|
| `PERPLEXITY_API_KEY` | Vercel | Required. Route returns 503 if absent. Never committed. |
| `AI_ANSWERS_ENABLED` | Vercel + Streamlit | Defaults **false**. Route returns 404 while off. |
| `AI_ANSWERS_ROLLOUT_PCT` | Vercel | 0–100. Stable per-user bucketing. |
| `CHILLMIND_ANSWERS_URL` | Streamlit | Full URL of the deployed route. |
| `GOOGLE_API_KEY` | Streamlit | Existing Mentor chat. **Rotate the old committed key.** |
| `DATADOG_API_KEY` | Vercel | Optional. Telemetry no-ops cleanly without it. |

## Turning it on and off

Enable: set `AI_ANSWERS_ENABLED=true` in the Vercel environment and redeploy, then set the same
variable plus `CHILLMIND_ANSWERS_URL` wherever Streamlit runs.

Ramp: `AI_ANSWERS_ROLLOUT_PCT=10`, then 50, then 100. Bucketing is stable per user, so nobody flaps
in and out.

**Roll back:** set `AI_ANSWERS_ENABLED=false`. The route starts returning 404 and Streamlit falls
straight back to the existing `intents.json` replies. No deploy and no revert needed. This is the
first thing to try if answer quality or cost looks wrong.

## Metrics

One structured JSON line per answer, in Vercel logs and shipped to Datadog when configured.

| Field | Meaning |
|---|---|
| `latency_ms` | End-to-end route time, including retries |
| `input_tokens` / `output_tokens` | Exact counts from the API response |
| `cost_usd` | Real dollars, from the response's `usage.cost.total_cost` — not an estimate |
| `error_code` | `null` on success, else `timeout` / `rate_limited` / `upstream` / `malformed` / `breaker_open` / `config` |
| `fallback_used` | True when we declined and handed off instead of answering |
| `citation_count` | Help articles cited. A sustained zero means the KB has gaps |
| `kb_hit` | Whether retrieval found anything at all |
| `safety_escalation` | Crisis path taken. Watch for spikes |
| `instructions_version` | Attribute a quality change to a prompt change |
| `retries` | Upstream retries needed |

Expected steady state: `cost_usd` around $0.0013/answer, `error_code` null, `citation_count` 1–4.

## Alert thresholds

Currently intentionally loose, to be tightened once the preview environment produces real p95 and cost
data:

| Metric | Warn | Page |
|---|---|---|
| p95 latency | > 10s / 15 min | > 20s / 5 min |
| Error rate | > 10% / 30 min | > 25% / 10 min |
| Cost per answer | > $0.02 rolling hour | > $0.05 |
| Daily spend | > $50 | > $150 |
| Fallback rate | > 25% | > 50% |
| Safety escalations | any spike vs 7-day baseline | — |

## Editing the knowledge base

`docs/help/*.md` **is** the product behaviour. After editing, run `npm run build:kb` in `web/` and
commit the regenerated `web/lib/kb-data.ts`. CI fails if the two drift apart, so the corpus can never
silently diverge from what's deployed.

Rising `fallback_used` with `kb_hit: false` is the signal to add an article.

## Known limitations

- **Rate limiting is in-memory**, so it is per serverless instance. It is a cost guardrail, not a
  security control. Move `web/lib/ratelimit.ts` to Upstash Redis or Vercel KV before real traffic.
- **The upstream call is not streamed.** Perplexity's streaming event schema for `/v1/agent` is not
  published yet, so the route gets a complete answer and re-chunks it as SSE. The client contract is
  already streaming, so switching later needs no client change. Cost: slower first token.
- **Answers are not persisted.** There is no database yet, so `cost_usd` and citations live in logs
  only. Standing up Supabase and writing an `answers` table is the next step.
