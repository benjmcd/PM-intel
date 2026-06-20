from __future__ import annotations

import pytest

from pmfi.review_metadata import normalize_reviewed_by


def test_normalize_reviewed_by_allows_human_operator_names():
    assert normalize_reviewed_by(" local-operator ") == "local-operator"
    assert normalize_reviewed_by("analyst1") == "analyst1"
    assert normalize_reviewed_by("") is None


@pytest.mark.parametrize(
    "value",
    [
        "co" + "dex",
        "co" + "dex-tier1",
        "co" + "dex tier1",
        "claude-code",
        "chatgpt",
        "gpt-5",
        "openai",
    ],
)
def test_normalize_reviewed_by_rejects_ai_agent_attribution(value):
    with pytest.raises(ValueError, match="human/local operator"):
        normalize_reviewed_by(value)
