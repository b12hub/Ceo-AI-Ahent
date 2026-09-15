"""
bot/formatting.py

Delivery-layer safety net between the agent's answer and Telegram's HTML
parse mode. Prompt discipline (agent/prompt.py) is the primary fix for
Problem 3 — this module exists because LLM instruction-following is
probabilistic, not guaranteed, and a single stray '<' or leftover
'**bold**' will make `bot.send_message(..., parse_mode="HTML")` raise
a 400 from Telegram instead of degrading gracefully.

Deliberately lives in bot/, not agent/: the agent layer should stay
channel-agnostic (loop.py could in principle serve a non-Telegram
surface later), while HTML-escaping rules are specific to Telegram's
Bot API.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# 1. Known meta-disclaimer fragments the model sometimes appends despite
#    rule 4 in the system prompt. Matched case-insensitively.
# --------------------------------------------------------------------------
_DISCLAIMER_PATTERNS = [
    r"\(?\s*information (?:was |is )?(?:sourced|retrieved|pulled) from[^.)\n]*\)?\.?",
    r"\(?\s*(?:this (?:data|information) (?:was|is) )?(?:sourced|retrieved) (?:via|using|from) the [a-zA-Z_]+ tool[^.)\n]*\)?\.?",
    r"\(?\s*source:\s*[a-zA-Z_]+ tool\s*\)?\.?",
    r"\(?\s*\[?tool(?:_call)?(?:\s+result)?\]?\s*\)?\.?",
]
_DISCLAIMER_RE = re.compile("|".join(_DISCLAIMER_PATTERNS), re.IGNORECASE)

# --------------------------------------------------------------------------
# 2. Fallback Markdown -> Telegram-HTML conversion, applied only to
#    whatever Markdown syntax survived the prompt instructions.
#    NOTE: the italic pattern is intentionally conservative (single '*'
#    with no digits/spaces immediately adjacent) to avoid mangling
#    arithmetic like "3 * 4" or emphasis-free asterisks in file globs.
# --------------------------------------------------------------------------
_HEADER_RE = re.compile(r"^#{1,6}\s*(.+)$", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_ITALIC_RE = re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])")
_MD_BULLET_RE = re.compile(r"^\s*[-*]\s+", re.MULTILINE)

# --------------------------------------------------------------------------
# 3. Only these tags survive; anything else gets its angle brackets
#    escaped so Telegram's HTML parser doesn't reject the whole message.
# --------------------------------------------------------------------------
_ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a", "blockquote"}
_TAG_RE = re.compile(r"</?([a-zA-Z0-9]+)(\s+[^>]*)?>")


def _escape_stray_angle_brackets(text: str) -> str:
    out: list[str] = []
    last = 0
    for m in _TAG_RE.finditer(text):
        tag = m.group(1).lower()
        out.append(text[last:m.start()])
        if tag in _ALLOWED_TAGS:
            out.append(m.group(0))
        else:
            out.append(m.group(0).replace("<", "&lt;").replace(">", "&gt;"))
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def sanitize_for_telegram(text: str) -> str:
    """
    Normalize a final LLM answer into something Telegram's HTML parse
    mode will accept and render cleanly. Idempotent — safe to call on
    text that's already clean.
    """
    if not text:
        return text

    cleaned = _DISCLAIMER_RE.sub("", text)

    cleaned = _HEADER_RE.sub(r"<b>\1</b>", cleaned)
    cleaned = _BOLD_RE.sub(r"<b>\1</b>", cleaned)
    cleaned = _INLINE_CODE_RE.sub(r"<code>\1</code>", cleaned)
    cleaned = _ITALIC_RE.sub(r"<i>\1</i>", cleaned)
    cleaned = _MD_BULLET_RE.sub("• ", cleaned)

    cleaned = _escape_stray_angle_brackets(cleaned)

    # Collapse blank-line runs the disclaimer stripping can leave behind.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned