"""Dependency-free editorial checks shared by drafting and publication.

These helpers apply to scheduled editorial content, not TP-hit updates/cards.
"""

from __future__ import annotations

import re
from typing import Any


class SignalContentError(ValueError):
    """A draft contains placeholder or too-short public prose."""


def word_count(value: Any) -> int:
    return len(re.findall(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?", str(value or "")))


def validate_reason(value: Any, *, field: str) -> str:
    """Require real prose, not a copied schema value or an empty draft."""

    if not isinstance(value, str):
        raise SignalContentError(f"{field} must be a complete sentence, not a non-text value")
    text = " ".join(value.split())
    normalized = re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()
    placeholders = {
        "trigger", "1h trigger", "1h trigger unavailable", "invalidation level",
        "structure confluence", "structure  confluence", "one sentence",
        "write one sentence", "chart annotation", "concise readout",
    }
    instructions = (
        "one complete sentence of at least", "write one complete",
        "a concise measured condition", "one concise sentence describing",
        "one sentence 12 to 25 words", "the measured reason this setup exists now",
    )
    missing_feed = any(marker in text.lower() for marker in (
        "unavailable", "n/a", "not fetched", "no live feed", "not available", "insufficient data",
    ))
    if word_count(text) < 6 or normalized in placeholders or missing_feed or any(
        instruction in normalized for instruction in instructions
    ):
        raise SignalContentError(f"{field} is missing, too short or a prompt placeholder; regenerate it")
    return text


def clip_text(value: Any, limit: int, *, ellipsis: bool = True) -> str:
    """Bound prose at whitespace, never inside a word or price token."""

    text = " ".join(str(value or "").split())
    limit = max(0, int(limit))
    if len(text) <= limit:
        return text
    suffix = "…" if ellipsis else ""
    room = limit - len(suffix)
    if room <= 0:
        return ""
    boundary = text[: room + 1].rfind(" ")
    if boundary <= 0:
        return ""
    clipped = text[:boundary].rstrip(" ,;:–—-")
    return clipped + suffix if clipped else ""


def clip_sentence(value: Any, limit: int) -> str:
    """Prefer a complete sentence; otherwise stop at a whole token."""

    text = " ".join(str(value or "").split())
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    ends = [match.start() for match in re.finditer(r"(?<=[.!?;])\s", text[: limit + 1])]
    if ends and ends[-1] >= limit * 0.45:
        return text[: ends[-1]].rstrip()
    return clip_text(text, limit)


def sanitize_hook(value: Any, *, max_chars: int = 70) -> str:
    """Clip at a word boundary and remove dangling numbers/prepositions.

    Numeric/price tokens are atomic. Even a complete trailing number is dropped
    on a clipped hook, so a fragment such as 'pressure at $9' cannot masquerade
    as a different measured price. Already short, complete hooks are retained.
    """

    text = " ".join(str(value or "").split())
    limit = min(70, max(0, int(max_chars)))
    if not text or limit == 0:
        return ""
    if len(text) <= limit:
        return text
    clipped = clip_text(text, limit, ellipsis=False)
    numeric = re.compile(r"^\$?[+-]?\d[\d,]*(?:\.\d+)?(?:[KMBkmb]|%)?$")
    dangling = {
        "a", "an", "and", "at", "above", "around", "as", "below", "by", "for",
        "from", "in", "into", "near", "of", "on", "or", "the", "to", "with",
    }
    while clipped:
        token = clipped.rsplit(None, 1)[-1].rstrip(".,;:")
        if numeric.fullmatch(token) or token.lower() in dangling:
            clipped = clipped.rsplit(None, 1)[0].rstrip() if " " in clipped else ""
        else:
            break
    return clipped.rstrip(" ,;:-–—")
