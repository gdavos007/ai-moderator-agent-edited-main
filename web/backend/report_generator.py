"""Generate a structured focus-group report from a transcript using OpenAI.

Entry point is ``generate_report(session_id, transcript_payload)`` — a coroutine
that calls ``AsyncOpenAI`` with ``response_format=json_schema`` (strict mode)
so the LLM is forced to emit valid JSON matching our contract.

This module is deliberately decoupled from FastAPI: it neither reads request
state nor writes to disk.  The caller (``server.py`` background task) does:

    from web.backend import report_generator, report_store
    report = await report_generator.generate_report(session_id, payload)
    report_store.save_report(session_id, report)

That lets us test the generator in isolation with a hand-crafted payload.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "report_system.md"
_DEFAULT_MODEL = os.environ.get("REPORT_MODEL", "gpt-4o-2024-08-06")
# NB: gpt-4o-2024-08-06 and newer support strict json_schema.  The generic
# "gpt-4o" alias also works at the time of writing, but pinning to a dated
# version keeps reports reproducible.

MIN_RESPONSES_FOR_FULL_REPORT = 1


# --------------------------------------------------------------------------- #
# JSON Schema for structured output
# --------------------------------------------------------------------------- #
# Matches the user-supplied target schema verbatim.  Passed to OpenAI with
# strict=True so unknown or missing properties cause a retry server-side.
# Note: OpenAI strict mode requires additionalProperties=false on all object
# types and every property to appear in "required".
_QUOTE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["speaker", "quote", "timestamp"],
    "properties": {
        "speaker": {"type": "string"},
        "quote": {"type": "string"},
        "timestamp": {"type": "string"},
    },
}

REPORT_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["session", "synopsis", "key_findings", "question_analysis", "transcript"],
    "properties": {
        "session": {
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "title", "date", "duration_minutes", "participant_count"],
            "properties": {
                "session_id": {"type": "string"},
                "title": {"type": "string"},
                "date": {"type": "string", "description": "ISO-8601 datetime or date"},
                "duration_minutes": {"type": "number"},
                "participant_count": {"type": "integer"},
            },
        },
        "synopsis": {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "overall_sentiment", "top_takeaways"],
            "properties": {
                "summary": {"type": "string"},
                "overall_sentiment": {
                    "type": "string",
                    "enum": ["positive", "neutral", "mixed", "negative"],
                },
                "top_takeaways": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        "key_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "description", "supporting_quotes"],
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "supporting_quotes": {"type": "array", "items": _QUOTE_SCHEMA},
                },
            },
        },
        "question_analysis": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["question_id", "question_text", "summary", "themes", "quotes"],
                "properties": {
                    "question_id": {"type": "string"},
                    "question_text": {"type": "string"},
                    "summary": {"type": "string"},
                    "themes": {"type": "array", "items": {"type": "string"}},
                    "quotes": {"type": "array", "items": _QUOTE_SCHEMA},
                },
            },
        },
        "transcript": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["speaker", "timestamp", "text"],
                "properties": {
                    "speaker": {"type": "string"},
                    "timestamp": {"type": "string"},
                    "text": {"type": "string"},
                },
            },
        },
    },
}


# --------------------------------------------------------------------------- #
# Prompt assembly
# --------------------------------------------------------------------------- #
def _read_system_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error(f"System prompt missing at {_PROMPT_PATH}; using inline fallback")
        return "You are a senior qualitative researcher. Output a focus group report as JSON per the provided schema."


def _duration_minutes(started_at: Optional[str], ended_at: Optional[str]) -> float:
    if not started_at or not ended_at:
        return 0.0
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
        return round(max((end - start).total_seconds() / 60.0, 0.0), 1)
    except (ValueError, TypeError):
        return 0.0


def _count_responses(conversation: List[Dict[str, Any]]) -> int:
    return sum(1 for t in conversation if t.get("type") == "response")


def strip_provisional(conversation: List[Dict[str, Any]]) -> tuple:
    """Remove unvalidated speech before the transcript reaches the report LLM.

    Reports quote CONFIRMED WORDS ONLY. Deepgram retracts interims — session
    RM_Aq5EeHDjAozN t=481.2 emitted a 49-char interim that became a 30-char
    final, meaning '. It probably needs' was text the participant had not yet
    said. Attributing words like that to a named research participant in a
    client deliverable is a worse failure than the truncation Defect F fixed,
    so `trailing_text` is stripped here rather than left to an instruction in
    report_system.md that the model may or may not honour.

    `trailing_text` remains in the stored transcript — this strips it only from
    the report payload.

    Returns (sanitised_conversation, n_trimmed, n_chars_removed, n_dropped).
      trimmed = entry had confirmed words; unvalidated tail removed
      dropped = entry had NO confirmed words at all; nothing quotable remains
    """
    sanitised: List[Dict[str, Any]] = []
    n_trimmed = n_chars = n_dropped = 0

    for entry in conversation:
        # Pre-Defect-F transcripts have no trailing_text — pass through.
        if entry.get("type") != "response" or "trailing_text" not in entry:
            sanitised.append(entry)
            continue

        trailing = entry.get("trailing_text") or ""
        finals = entry.get("finals_text") or ""
        clean = {k: v for k, v in entry.items()
                 if k not in ("trailing_text", "finals_text", "is_provisional")}

        if not trailing:
            sanitised.append(clean)
            continue

        if not finals:
            # Turn was cut before Deepgram confirmed anything. Nothing can be
            # quoted; keeping an empty entry invites the model to invent one.
            #
            # This removes a participant's contribution from a client report, so
            # it must be discoverable from a log rather than from the client.
            # Never observed on real data (46 turns across both sessions all had
            # confirmed text) — exercised by a synthetic fixture, see
            # tests/fixtures/report_payload_synthetic.json.
            n_dropped += 1
            n_chars += len(trailing)
            logger.warning(
                "📎 PROVISIONAL DROP: removed a participant turn from the report "
                "payload entirely — speaker=%r timestamp=%s q=%s, %d unvalidated "
                "chars, ZERO confirmed words. Nothing quotable remained. "
                "Dropped text (NOT for client use): %r",
                entry.get("speaker", "?"), entry.get("timestamp", "?"),
                entry.get("question_number", "?"), len(trailing), trailing[:200],
            )
            continue

        clean["text"] = finals
        n_trimmed += 1
        n_chars += len(trailing)
        sanitised.append(clean)

    return sanitised, n_trimmed, n_chars, n_dropped


def _build_user_content(payload: Dict[str, Any]) -> str:
    """Render the transcript + discussion guide as a single user message.

    We pass it as JSON inside a text block so the model can read structure
    but not confuse it with its own response format.
    """
    transcript = payload.get("transcript", {}) or {}
    conversation, n_trimmed, n_chars, n_dropped = strip_provisional(
        transcript.get("conversation", []) or []
    )
    if n_trimmed or n_dropped:
        # Counter for the loosening decision: how often would provisional text
        # have reached a client quote? Measure before relaxing this policy.
        logger.warning(
            "📎 PROVISIONAL STRIPPED from report payload: %d entries trimmed, "
            "%d entries dropped (no confirmed words), %d unvalidated chars withheld.",
            n_trimmed, n_dropped, n_chars,
        )
    context = {
        "session_id": payload.get("session_id", ""),
        "title": payload.get("title", ""),
        "started_at": payload.get("started_at") or transcript.get("session_start", ""),
        "ended_at": payload.get("ended_at") or transcript.get("session_end", ""),
        "participants": payload.get("participants") or transcript.get("participants", []),
        "questions": payload.get("questions", []),
        "conversation": conversation,
    }
    return (
        "Here is the focus group session data. Produce the structured report.\n\n"
        "```json\n" + json.dumps(context, ensure_ascii=False, indent=2) + "\n```"
    )


def _minimal_report(session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a valid report with no findings for near-empty transcripts."""
    transcript = payload.get("transcript", {}) or {}
    conversation = transcript.get("conversation", [])
    participants = payload.get("participants") or transcript.get("participants", [])
    started_at = payload.get("started_at") or transcript.get("session_start", "")
    ended_at = payload.get("ended_at") or transcript.get("session_end", "")

    echoed_transcript = [
        {
            "speaker": t.get("speaker", ""),
            "timestamp": t.get("timestamp", ""),
            "text": t.get("text", ""),
        }
        for t in conversation
        if t.get("type") in {"question", "response", "greeting", "closing"}
    ]

    # question_analysis stub from discussion guide, empty quotes
    qa = [
        {
            "question_id": q.get("id", ""),
            "question_text": q.get("question", q.get("text", "")),
            "summary": "No substantive response captured for this question.",
            "themes": [],
            "quotes": [],
        }
        for q in payload.get("questions", [])
    ]

    return {
        "session": {
            "session_id": session_id,
            "title": payload.get("title", ""),
            "date": started_at,
            "duration_minutes": _duration_minutes(started_at, ended_at),
            "participant_count": len(participants),
        },
        "synopsis": {
            "summary": (
                "Session ended before substantive discussion; insufficient data "
                "for full thematic analysis."
            ),
            "overall_sentiment": "neutral",
            "top_takeaways": [],
        },
        "key_findings": [],
        "question_analysis": qa,
        "transcript": echoed_transcript,
    }


# --------------------------------------------------------------------------- #
# OpenAI call
# --------------------------------------------------------------------------- #
async def _call_openai(system_prompt: str, user_content: str) -> Dict[str, Any]:
    """Call OpenAI chat.completions with strict json_schema response format."""
    import openai as openai_client  # local import so the module is importable without the SDK

    client = openai_client.AsyncOpenAI()
    resp = await client.chat.completions.create(
        model=_DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "focus_group_report",
                "strict": True,
                "schema": REPORT_JSON_SCHEMA,
            },
        },
        temperature=0.2,
    )
    content = resp.choices[0].message.content or ""
    if not content:
        raise RuntimeError("OpenAI returned empty content")
    return json.loads(content)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
async def generate_report(session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a structured report for ``session_id`` from ``payload``.

    ``payload`` is the agent's upload JSON (session_id, title, started_at,
    participants, questions, transcript).  Returns a dict conforming to
    ``REPORT_JSON_SCHEMA``.
    """
    transcript = payload.get("transcript", {}) or {}
    conversation = transcript.get("conversation", []) or []

    if _count_responses(conversation) < MIN_RESPONSES_FOR_FULL_REPORT:
        logger.info(
            f"Session {session_id}: only {_count_responses(conversation)} responses, "
            "returning minimal report"
        )
        return _minimal_report(session_id, payload)

    system_prompt = _read_system_prompt()
    user_content = _build_user_content(payload)

    report = await _call_openai(system_prompt, user_content)

    # Ensure session.session_id is correct regardless of what the model returned.
    # (Strict mode guarantees the field exists; we just overwrite to the truth.)
    report.setdefault("session", {})["session_id"] = session_id
    return report


# --------------------------------------------------------------------------- #
# Hand-crafted test fixture — run with:
#     python -m web.backend.report_generator
# --------------------------------------------------------------------------- #
SAMPLE_PAYLOAD: Dict[str, Any] = {
    "session_id": "test_session_20260421_000000",
    "title": "Coffee Shop Concept Focus Group",
    "started_at": "2026-04-21T00:00:00+00:00",
    "ended_at": "2026-04-21T00:12:00+00:00",
    "participants": ["alice", "bob", "charlie"],
    "questions": [
        {"id": "W1", "question": "Please tell me where you live and what you do for a living."},
        {"id": "Q1", "question": "How often do you visit coffee shops in a typical week?"},
        {"id": "Q2", "question": "What's the single biggest reason you choose one coffee shop over another?"},
        {"id": "Q3", "question": "How do you feel about subscription-based coffee services?"},
    ],
    "transcript": {
        "session_id": "test_session_20260421_000000",
        "session_start": "2026-04-21T00:00:00+00:00",
        "session_end": "2026-04-21T00:12:00+00:00",
        "participants": ["alice", "bob", "charlie"],
        "conversation": [
            {"type": "greeting", "timestamp": "2026-04-21T00:00:10+00:00", "speaker": "agent",
             "text": "Welcome everyone. Let's get started."},
            {"type": "question", "timestamp": "2026-04-21T00:00:30+00:00", "speaker": "agent",
             "directed_to": "alice", "question_id": "Q1", "text": "Alice, how often do you visit coffee shops?"},
            {"type": "response", "timestamp": "2026-04-21T00:00:45+00:00", "speaker": "alice",
             "text": "I'd say about four or five times a week. Usually before work."},
            {"type": "question", "timestamp": "2026-04-21T00:01:00+00:00", "speaker": "agent",
             "directed_to": "bob", "question_id": "Q1", "text": "Bob, what about you?"},
            {"type": "response", "timestamp": "2026-04-21T00:01:15+00:00", "speaker": "bob",
             "text": "Almost every day. Sometimes twice a day, honestly."},
            {"type": "question", "timestamp": "2026-04-21T00:01:30+00:00", "speaker": "agent",
             "directed_to": "charlie", "question_id": "Q1", "text": "Charlie, what about you?"},
            {"type": "response", "timestamp": "2026-04-21T00:01:45+00:00", "speaker": "charlie",
             "text": "Probably just twice a week. I make most of mine at home."},
            {"type": "question", "timestamp": "2026-04-21T00:02:00+00:00", "speaker": "agent",
             "directed_to": "alice", "question_id": "Q2",
             "text": "Alice, what's the single biggest reason you choose one coffee shop over another?"},
            {"type": "response", "timestamp": "2026-04-21T00:02:30+00:00", "speaker": "alice",
             "text": "Honestly it's speed. If there's a line of more than three people I just leave."},
            {"type": "question", "timestamp": "2026-04-21T00:02:50+00:00", "speaker": "agent",
             "directed_to": "bob", "question_id": "Q2", "text": "Bob, what do you think?"},
            {"type": "response", "timestamp": "2026-04-21T00:03:10+00:00", "speaker": "bob",
             "text": "For me it's the baristas. I want them to remember my name and my order."},
            {"type": "question", "timestamp": "2026-04-21T00:03:30+00:00", "speaker": "agent",
             "directed_to": "charlie", "question_id": "Q2", "text": "How about you, Charlie?"},
            {"type": "response", "timestamp": "2026-04-21T00:03:50+00:00", "speaker": "charlie",
             "text": "Price. I won't pay more than five dollars for coffee, period."},
            {"type": "question", "timestamp": "2026-04-21T00:04:10+00:00", "speaker": "agent",
             "directed_to": "alice", "question_id": "Q3",
             "text": "Alice, how do you feel about subscription-based coffee services?"},
            {"type": "response", "timestamp": "2026-04-21T00:04:40+00:00", "speaker": "alice",
             "text": "I'd sign up in a heartbeat if it saved me time at the counter."},
            {"type": "question", "timestamp": "2026-04-21T00:05:00+00:00", "speaker": "agent",
             "directed_to": "bob", "question_id": "Q3", "text": "And Bob, what's your take?"},
            {"type": "response", "timestamp": "2026-04-21T00:05:20+00:00", "speaker": "bob",
             "text": "I don't know, feels impersonal. Part of the joy is chatting with the barista."},
            {"type": "question", "timestamp": "2026-04-21T00:05:40+00:00", "speaker": "agent",
             "directed_to": "charlie", "question_id": "Q3", "text": "Charlie, what do you think?"},
            {"type": "response", "timestamp": "2026-04-21T00:06:00+00:00", "speaker": "charlie",
             "text": "I'd try it if the math worked out, but most subscriptions are worse than just paying."},
            {"type": "closing", "timestamp": "2026-04-21T00:12:00+00:00", "speaker": "agent",
             "text": "Thank you all for participating."},
        ],
    },
}


def _self_test_print_schema() -> None:
    """Sanity-check the schema by walking it.  Not for production."""
    # Flatten the schema to count required fields — this catches typos in
    # schema construction (e.g. a missing "required" on a nested object
    # which would make strict mode fail).
    def walk(node: Any, path: str = "") -> int:
        if isinstance(node, dict):
            missing = []
            if node.get("type") == "object":
                props = node.get("properties", {})
                req = node.get("required", [])
                missing = [p for p in props if p not in req]
                if missing:
                    print(f"  WARN {path}: properties not in required: {missing}")
            total = 1
            for k, v in node.items():
                total += walk(v, f"{path}.{k}")
            return total
        if isinstance(node, list):
            return sum(walk(v, f"{path}[{i}]") for i, v in enumerate(node))
        return 0

    print("Schema sanity check:")
    walk(REPORT_JSON_SCHEMA)
    print("Done.")


if __name__ == "__main__":  # pragma: no cover
    import asyncio

    async def _main():
        _self_test_print_schema()
        if not os.environ.get("OPENAI_API_KEY"):
            print("\nOPENAI_API_KEY not set — skipping live LLM test.")
            print("To run a real test:\n  OPENAI_API_KEY=... python -m web.backend.report_generator")
            return
        print("\nCalling OpenAI with SAMPLE_PAYLOAD...")
        report = await generate_report(SAMPLE_PAYLOAD["session_id"], SAMPLE_PAYLOAD)
        print(json.dumps(report, indent=2)[:2000])
        print(f"\n... truncated. Total keys in report: {list(report.keys())}")

    asyncio.run(_main())
