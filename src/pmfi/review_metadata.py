"""Shared validation for local review metadata."""

from __future__ import annotations

import re
from typing import Final


_BLOCKED_REVIEWER_KEYS: Final = frozenset({
    "ai",
    "ai-agent",
    "chatgpt",
    "claude",
    "codex",
    "gpt",
    "openai",
})


def normalize_reviewed_by(value: str | None) -> str | None:
    """Normalize reviewer metadata and reject AI-agent attribution values."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None

    key = re.sub(r"[\s_]+", "-", text.lower())
    key = re.sub(r"-+", "-", key).strip("-")
    if (
        key in _BLOCKED_REVIEWER_KEYS
        or key.startswith("codex-")
        or key.startswith("claude-")
        or key.startswith("chatgpt-")
        or key.startswith("gpt-")
    ):
        raise ValueError("reviewed_by must identify a human/local operator, not an AI agent")
    return text
