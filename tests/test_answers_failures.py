"""
Failure-path tests for the Python side of the AI answers feature.

The contract these lock in: when anything goes wrong, ChillMind must fall back to
its existing behaviour silently rather than surfacing an error to a student.
"""
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chillmind.ai import answers  # noqa: E402
from chillmind.flags import ai_answers_enabled  # noqa: E402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("AI_ANSWERS_ENABLED", raising=False)
    monkeypatch.delenv("CHILLMIND_ANSWERS_URL", raising=False)
    answers.reset_idempotency()
    yield
    answers.reset_idempotency()


# --------------------------------------------------------------------------
# feature flag
# --------------------------------------------------------------------------
def test_flag_defaults_off():
    assert ai_answers_enabled() is False


@pytest.mark.parametrize("value", ["", "false", "0", "no", "off", "nonsense"])
def test_flag_stays_off_for_non_truthy_values(monkeypatch, value):
    monkeypatch.setenv("AI_ANSWERS_ENABLED", value)
    assert ai_answers_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_flag_on_for_truthy_values(monkeypatch, value):
    monkeypatch.setenv("AI_ANSWERS_ENABLED", value)
    assert ai_answers_enabled() is True


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------
def test_unconfigured_url_raises_unavailable():
    with pytest.raises(answers.AnswersUnavailable):
        answers.answer_stream("how do i reset my streak", "u1")


# --------------------------------------------------------------------------
# transport failures
# --------------------------------------------------------------------------
def test_connection_error_raises_unavailable(monkeypatch):
    monkeypatch.setenv("CHILLMIND_ANSWERS_URL", "https://example.invalid/api/answers")

    def boom(*_a, **_kw):
        raise requests.ConnectionError("dns failure")

    monkeypatch.setattr(answers.requests, "post", boom)
    with pytest.raises(answers.AnswersUnavailable):
        answers.answer_stream("how do i reset my streak", "u1")


def test_timeout_raises_unavailable(monkeypatch):
    monkeypatch.setenv("CHILLMIND_ANSWERS_URL", "https://example.invalid/api/answers")

    def boom(*_a, **_kw):
        raise requests.Timeout("read timed out")

    monkeypatch.setattr(answers.requests, "post", boom)
    with pytest.raises(answers.AnswersUnavailable):
        answers.answer_stream("how do i reset my streak", "u1")


class FakeResponse:
    def __init__(self, status_code=200, lines=()):
        self.status_code = status_code
        self._lines = list(lines)
        self.closed = False

    def iter_lines(self, decode_unicode=False):  # noqa: ARG002
        yield from self._lines

    def close(self):
        self.closed = True


def _post_returning(monkeypatch, response):
    monkeypatch.setenv("CHILLMIND_ANSWERS_URL", "https://example.test/api/answers")
    monkeypatch.setattr(answers.requests, "post", lambda *_a, **_kw: response)


@pytest.mark.parametrize("status", [400, 403, 404, 429, 500, 503])
def test_non_200_raises_unavailable(monkeypatch, status):
    _post_returning(monkeypatch, FakeResponse(status_code=status))
    with pytest.raises(answers.AnswersUnavailable):
        answers.answer_stream("how do i reset my streak", "u1")


# --------------------------------------------------------------------------
# stream parsing
# --------------------------------------------------------------------------
def test_happy_path_yields_text_and_citations(monkeypatch):
    _post_returning(
        monkeypatch,
        FakeResponse(
            lines=[
                'data: {"type":"token","text":"Open "}',
                'data: {"type":"token","text":"Notifications."}',
                'data: {"type":"citations","items":[{"id":"notifications"}]}',
                'data: {"type":"done","meta":{"citations":[{"id":"notifications"}]}}',
            ]
        ),
    )
    chunks, citations = answers.answer_stream("where are my notifications", "u1")
    assert "".join(chunks) == "Open Notifications."
    assert citations == [{"id": "notifications"}]


def test_malformed_sse_lines_are_skipped(monkeypatch):
    _post_returning(
        monkeypatch,
        FakeResponse(
            lines=[
                "",
                "garbage without prefix",
                "data: {not valid json",
                'data: {"type":"token","text":"ok"}',
                'data: {"type":"done","meta":{}}',
            ]
        ),
    )
    chunks, _ = answers.answer_stream("how do i reset my streak", "u1")
    assert "".join(chunks) == "ok"


def test_error_event_raises_unavailable(monkeypatch):
    _post_returning(
        monkeypatch,
        FakeResponse(lines=['data: {"type":"error","code":"upstream"}']),
    )
    chunks, _ = answers.answer_stream("how do i reset my streak", "u1")
    with pytest.raises(answers.AnswersUnavailable):
        "".join(chunks)


def test_response_is_closed_after_streaming(monkeypatch):
    response = FakeResponse(lines=['data: {"type":"done","meta":{}}'])
    _post_returning(monkeypatch, response)
    chunks, _ = answers.answer_stream("how do i reset my streak", "u1")
    "".join(chunks)
    assert response.closed is True


# --------------------------------------------------------------------------
# idempotency: Streamlit reruns the whole script on every interaction
# --------------------------------------------------------------------------
def test_idempotency_key_is_stable_and_user_scoped():
    a = answers.idempotency_key("alice", "How do I reset my streak?")
    b = answers.idempotency_key("alice", "how do i reset my streak? ")
    c = answers.idempotency_key("bob", "How do I reset my streak?")
    assert a == b
    assert a != c


def test_second_identical_submission_is_suppressed():
    key = answers.idempotency_key("alice", "how do i reset my streak")
    assert answers.already_answered(key) is False
    assert answers.already_answered(key) is True


def test_idempotency_entry_expires():
    key = answers.idempotency_key("alice", "how do i reset my streak")
    assert answers.already_answered(key, now=1_000.0) is False
    assert answers.already_answered(key, now=1_000.0 + 301) is False


# --------------------------------------------------------------------------
# routing: only support questions are sent to the service
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "question",
    [
        "how do i reset my streak",
        "where are my notifications",
        "how to turn off notifications",
        "why is my focus score not working",
        "can i delete my data",
    ],
)
def test_support_questions_are_routed(question):
    assert answers.looks_like_support_question(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "i'm really stressed about my exams",
        "give me a study plan for chemistry",
        "how do i stay motivated",  # no app surface mentioned
        "",
    ],
)
def test_non_support_questions_stay_with_the_mentor(question):
    assert answers.looks_like_support_question(question) is False
