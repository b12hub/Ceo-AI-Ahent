"""
agent/llm.py

Resilient dual-LLM client layer for CEO_AI_test_bot.

Primary provider : Groq (llama-3.3-70b-versatile)
Fallback provider: Google GenAI (gemini-2.5-flash)

Exposes a single unified entrypoint:

    get_llm_response(messages, tools=None) -> LLMResponse

`messages` and `tools` follow the OpenAI chat-completion shape (the same
shape Groq's SDK uses natively). Google GenAI has a different function-
calling schema, so this module transparently translates requests/responses
so callers (agent/loop.py, agent/tools/registry.py) never need to know
which provider actually served the call.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional
from dotenv import load_dotenv
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

load_dotenv()
logger = logging.getLogger("agent.llm")
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")

GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
GOOGLE_MODEL = os.environ.get("GOOGLE_MODEL", "gemini-2.5-flash")

GROQ_MAX_RETRIES = int(os.environ.get("GROQ_MAX_RETRIES", "3"))
GOOGLE_MAX_RETRIES = int(os.environ.get("GOOGLE_MAX_RETRIES", "2"))


# ---------------------------------------------------------------------------
# Normalized response types
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: Optional[str]
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    raw: Any = None

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


# ---------------------------------------------------------------------------
# Retryable-error detection
# ---------------------------------------------------------------------------

class RetryableLLMError(Exception):
    """Raised for 429 / 5xx style errors that should trigger backoff."""


class ProviderExhaustedError(Exception):
    """Raised when a provider has exhausted its retry budget."""
    pass


def _is_retryable_status(status_code: Optional[int]) -> bool:
    if status_code is None:
        return False
    return status_code == 429 or 500 <= status_code < 600


def _extract_status_code(exc: Exception) -> Optional[int]:
    # Groq/OpenAI-style SDK exceptions expose `.status_code` or `.response.status_code`
    for attr in ("status_code",):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    response = getattr(exc, "response", None)
    if response is not None:
        val = getattr(response, "status_code", None)
        if isinstance(val, int):
            return val
    return None


def _should_retry(exc: Exception) -> bool:
    status = _extract_status_code(exc)
    return _is_retryable_status(status)


# ---------------------------------------------------------------------------
# Groq client
# ---------------------------------------------------------------------------

def _get_groq_client():
    from groq import Groq  # local import: keep module importable without the dep installed

    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not set")
    return Groq(api_key=GROQ_API_KEY)


@retry(
    reraise=True,
    stop=stop_after_attempt(GROQ_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    retry=retry_if_exception(_should_retry),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
def _call_groq(messages: list[dict], tools: Optional[list[dict]]) -> LLMResponse:
    client = _get_groq_client()

    kwargs: dict[str, Any] = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.2,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    completion = client.chat.completions.create(**kwargs)
    choice = completion.choices[0]
    msg = choice.message

    tool_calls: list[ToolCall] = []
    for tc in (msg.tool_calls or []):
        try:
            args = json.loads(tc.function.arguments) if tc.function.arguments else {}
        except json.JSONDecodeError:
            logger.warning("Groq returned non-JSON tool arguments: %r", tc.function.arguments)
            args = {}
        tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

    return LLMResponse(
        content=msg.content,
        tool_calls=tool_calls,
        provider="groq",
        model=GROQ_MODEL,
        raw=completion,
    )


# ---------------------------------------------------------------------------
# Google GenAI client (fallback)
# ---------------------------------------------------------------------------

def _openai_tools_to_google(tools: Optional[list[dict]]):
    """Translate OpenAI-style tool schemas into google-genai FunctionDeclarations."""
    if not tools:
        return None

    from google.genai import types as gtypes

    declarations = []
    for t in tools:
        fn = t.get("function", t)  # tolerate either wrapped or bare schema
        declarations.append(
            gtypes.FunctionDeclaration(
                name=fn["name"],
                description=fn.get("description", ""),
                parameters=fn.get("parameters", {"type": "object", "properties": {}}),
            )
        )
    return [gtypes.Tool(function_declarations=declarations)]


def _openai_messages_to_google(messages: list[dict]):
    """
    Translate OpenAI-style chat messages into google-genai `contents`.
    System messages are concatenated and passed back as a system_instruction
    string by the caller; this helper only handles user/assistant/tool turns.
    """
    from google.genai import types as gtypes

    contents = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue  # handled separately via system_instruction
        if role == "user":
            contents.append(gtypes.Content(role="user", parts=[gtypes.Part(text=m.get("content", ""))]))
        elif role == "assistant":
            if m.get("tool_calls"):
                parts = []
                for tc in m["tool_calls"]:
                    fn = tc.get("function", tc)
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        args = {}
                    parts.append(gtypes.Part(function_call=gtypes.FunctionCall(name=fn["name"], args=args)))
                contents.append(gtypes.Content(role="model", parts=parts))
            else:
                contents.append(gtypes.Content(role="model", parts=[gtypes.Part(text=m.get("content") or "")]))
        elif role == "tool":
            contents.append(
                gtypes.Content(
                    role="user",
                    parts=[
                        gtypes.Part(
                            function_response=gtypes.FunctionResponse(
                                name=m.get("name", "tool"),
                                response={"result": m.get("content", "")},
                            )
                        )
                    ],
                )
            )
    return contents


def _system_prompt_from_messages(messages: list[dict]) -> Optional[str]:
    system_parts = [m["content"] for m in messages if m.get("role") == "system" and m.get("content")]
    return "\n\n".join(system_parts) if system_parts else None


@retry(
    reraise=True,
    stop=stop_after_attempt(GOOGLE_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=15),
    retry=retry_if_exception(_should_retry),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
def _call_google(messages: list[dict], tools: Optional[list[dict]]) -> LLMResponse:
    from google import genai
    from google.genai import types as gtypes

    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY / GEMINI_API_KEY is not set")

    client = genai.Client(api_key=GOOGLE_API_KEY)

    contents = _openai_messages_to_google(messages)
    google_tools = _openai_tools_to_google(tools)
    system_instruction = _system_prompt_from_messages(messages)

    config = gtypes.GenerateContentConfig(
        temperature=0.2,
        tools=google_tools,
        system_instruction=system_instruction,
    )

    response = client.models.generate_content(
        model=GOOGLE_MODEL,
        contents=contents,
        config=config,
    )

    tool_calls: list[ToolCall] = []
    text_parts: list[str] = []

    candidate = response.candidates[0] if response.candidates else None
    if candidate and candidate.content and candidate.content.parts:
        for i, part in enumerate(candidate.content.parts):
            if getattr(part, "function_call", None):
                fc = part.function_call
                tool_calls.append(
                    ToolCall(id=f"google-call-{i}", name=fc.name, arguments=dict(fc.args or {}))
                )
            elif getattr(part, "text", None):
                text_parts.append(part.text)

    return LLMResponse(
        content="\n".join(text_parts) if text_parts else None,
        tool_calls=tool_calls,
        provider="google",
        model=GOOGLE_MODEL,
        raw=response,
    )


# ---------------------------------------------------------------------------
# Unified public entrypoint
# ---------------------------------------------------------------------------

def get_llm_response(messages: list[dict], tools: Optional[list[dict]] = None) -> LLMResponse:
    """
    Unified chat-completion entrypoint used by the rest of the agent.

    Tries Groq first (with retry/backoff on 429 / 5xx). If Groq's retry
    budget is exhausted, or Groq raises a non-retryable error, transparently
    falls back to Google GenAI. Raises ProviderExhaustedError only if both
    providers fail.
    """
    groq_error: Optional[Exception] = None
    try:
        return _call_groq(messages, tools)
    except Exception as exc:  # noqa: BLE001
        groq_error = exc
        logger.warning("Groq call failed (%s). Falling back to Google GenAI.", exc)

    try:
        return _call_google(messages, tools)
    except Exception as google_exc:  # noqa: BLE001
        logger.error("Google GenAI fallback also failed: %s", google_exc)
        raise ProviderExhaustedError(
            f"Both providers failed. Groq error: {groq_error!r}; Google error: {google_exc!r}"
        ) from google_exc


def generate_agent_response(
    prompt: str | list[dict],
    tools: Optional[list[dict]] = None
) -> LLMResponse:
    """
    Public entrypoint for generating agent responses with Groq primary + Google GenAI fallback.
    Accepts either a string prompt or an OpenAI-style message list.
    """
    if isinstance(prompt, str):
        messages = [{"role": "user", "content": prompt}]
    else:
        messages = prompt
    return get_llm_response(messages, tools=tools)


completion_with_backoff = get_llm_response


if __name__ == "__main__":
    test_messages = [
        {"role": "system", "content": "You are a terse test assistant. Reply in one short sentence."},
        {"role": "user", "content": "Say 'pong' and nothing else."},
    ]
    result = get_llm_response(test_messages)
    print(f"[provider={result.provider} model={result.model}]")
    print(result.content)