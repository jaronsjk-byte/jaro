"""
Client for the ChillMind AI answers service.

Talks to the Vercel route (POST /api/answers), which owns the API key, the
prompt, rate limiting and telemetry. Streamlit never sees the Perplexity key.

Every failure path here is silent by design: the caller falls back to
ChillMind's existing local intent replies rather than showing an error.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date
from typing import Dict, Iterator, List, Optional, Tuple

import requests

TOTAL_TIMEOUT_S = 60
IDLE_TIMEOUT_S = 10
CONNECT_TIMEOUT_S = 5

# Streamlit reruns the whole script on every interaction, so the same question
# can be submitted repeatedly. This guard keeps us from paying twice.
_seen: Dict[str, float] = {}
_IDEMPOTENCY_TTL_S = 300


def _service_url() -> Optional[str]:
    url = os.environ.get("CHILLMIND_ANSWERS_URL", "").strip()
    return url or None


def idempotency_key(context_id: str, question: str) -> str:
    raw = f"{context_id}|{question.strip().lower()}|{date.today().isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def already_answered(key: str, now: Optional[float] = None) -> bool:
    """True if this exact question was just answered — a Streamlit rerun."""
    now = time.time() if now is None else now
    for k, seen_at in list(_seen.items()):
        if now - seen_at > _IDEMPOTENCY_TTL_S:
            _seen.pop(k, None)
    if key in _seen:
        return True
    _seen[key] = now
    return False


def reset_idempotency() -> None:
    _seen.clear()


def looks_like_support_question(text: str) -> bool:
    """
    Only support/how-to questions are routed to the answers service. Anything
    else stays with the existing Mentor behaviour.
    """
    t = (text or "").lower()
    triggers = (
        "how do i", "how to", "where is", "where are", "where do i",
        "can i", "why does", "why is", "what happens", "reset",
        "turn off", "turn on", "delete", "not working", "broken",
    )
    surfaces = (
        "streak", "notification", "calendar", "schedule", "goal", "profile",
        "breathing", "focus", "jumble", "memory", "survey", "login",
        "sign in", "data", "privacy", "app", "score",
    )
    return any(x in t for x in triggers) and any(s in t for s in surfaces)


def answer_stream(
    question: str, context_id: str = "anonymous"
) -> Tuple[Iterator[str], List[dict]]:
    """
    Yields answer text chunks and returns collected citations.

    Raises AnswersUnavailable on any failure so the caller can fall back.
    """
    url = _service_url()
    if not url:
        raise AnswersUnavailable("CHILLMIND_ANSWERS_URL is not configured")

    citations: List[dict] = []

    try:
        response = requests.post(
            url,
            json={"question": question, "contextId": context_id},
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=(CONNECT_TIMEOUT_S, IDLE_TIMEOUT_S),
        )
    except requests.RequestException as exc:
        raise AnswersUnavailable(f"request failed: {exc}") from exc

    if response.status_code != 200:
        raise AnswersUnavailable(f"service returned {response.status_code}")

    def chunks() -> Iterator[str]:
        started = time.time()
        try:
            for line in response.iter_lines(decode_unicode=True):
                if time.time() - started > TOTAL_TIMEOUT_S:
                    raise AnswersUnavailable("exceeded total timeout")
                if not line or not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except (ValueError, TypeError):
                    continue
                kind = event.get("type")
                if kind == "token":
                    yield event.get("text", "")
                elif kind == "citations":
                    citations.extend(event.get("items", []))
                elif kind == "error":
                    raise AnswersUnavailable(event.get("code", "unknown"))
                elif kind == "done":
                    meta = event.get("meta") or {}
                    citations.extend(
                        c for c in (meta.get("citations") or []) if c not in citations
                    )
                    return
        finally:
            response.close()

    return chunks(), citations


class AnswersUnavailable(RuntimeError):
    """The answers service could not serve this request. Caller must fall back."""
