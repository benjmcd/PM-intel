from __future__ import annotations

from pathlib import Path


def _item(
    short_id: str,
    market: str,
    fired_at: str,
    *,
    venue: str = "kalshi",
    label: str = "fp",
    category: str | None = None,
    rule: str = "volume_spike_v1",
    outcome: str = "yes",
) -> dict[str, object]:
    return {
        "short_id": short_id,
        "id": short_id,
        "venue": venue,
        "venue_code": venue,
        "market": market,
        "venue_market_id": market,
        "rule": rule,
        "rule_key": rule,
        "outcome_key": outcome,
        "fired_at": fired_at,
        "label": label,
        "category": category,
    }


def _by_ids(groups: list[dict[str, object]]) -> list[list[str]]:
    return [
        [str(leg["short_id"]) for leg in group["legs"]]  # type: ignore[index]
        for group in groups
    ]


def test_derive_event_ticker_matches_real_kalshi_ticker_table():
    from pmfi.pipeline.cofire import derive_event_ticker

    cases = {
        "KXWCGAME-26JUN20GERCIV-GER": "KXWCGAME-26JUN20GERCIV",
        "KXWC1HTOTAL-26JUN20GERCIV-1": "KXWC1HTOTAL-26JUN20GERCIV",
        "KXWCCORNERS-26JUN20GERCIV-10": "KXWCCORNERS-26JUN20GERCIV",
        "KXBTC15M-26JUN201630-30": "KXBTC15M-26JUN201630",
        "KXBTC15M-26JUN201930-30": "KXBTC15M-26JUN201930",
        "KXBTCD-26JUN1817-T63249.99": "KXBTCD-26JUN1817",
        "KXT20MATCH-26JUN202030NEWWAS-WAS": "KXT20MATCH-26JUN202030NEWWAS",
        "KXMVESPORTSMULTIGAMEEXTENDED-S202696F8015AEB8-7EFA06F5922": (
            "KXMVESPORTSMULTIGAMEEXTENDED-S202696F8015AEB8"
        ),
        "KXWCGAME-26JUN18MEXKOR-KOR": "KXWCGAME-26JUN18MEXKOR",
    }

    for ticker, event_ticker in cases.items():
        assert derive_event_ticker(ticker, "kalshi") == event_ticker

    assert derive_event_ticker("KXBTCD-26JUN1817", "kalshi") is None
    assert derive_event_ticker("KXWCGAME-26JUN20GERCIV-GER", "polymarket") is None


def test_group_cofire_uses_same_event_pairwise_radius_not_single_leg_window():
    from pmfi.pipeline.cofire import group_cofire

    groups = group_cofire(
        [
            _item(
                "623164c5",
                "KXWCGAME-26JUN20GERCIV-CIV",
                "2026-06-20T20:35:55.341493+00:00",
                category="cross_market_hedge",
            ),
            _item(
                "39bd1f35",
                "KXWCGAME-26JUN20GERCIV-GER",
                "2026-06-20T20:48:01.193793+00:00",
                category="cross_market_hedge",
            ),
            _item(
                "late",
                "KXWCGAME-26JUN20GERCIV-TIE",
                "2026-06-20T21:03:03.000000+00:00",
            ),
            _item(
                "36cdc737",
                "KXT20MATCH-26JUN202030NEWWAS-WAS",
                "2026-06-21T00:12:36.594312+00:00",
                label="tp",
            ),
        ],
        window_s=900,
    )

    assert _by_ids(groups) == [
        ["623164c5", "39bd1f35"],
        ["late"],
        ["36cdc737"],
    ]
    assert all(not key.startswith("_") for group in groups for key in group)


def test_group_cofire_keeps_polymarket_as_noop_singletons():
    from pmfi.pipeline.cofire import group_cofire

    groups = group_cofire(
        [
            _item("poly-a", "0xabc", "2026-06-22T01:20:06+00:00", venue="polymarket"),
            _item("poly-b", "0xabc", "2026-06-22T01:21:06+00:00", venue="polymarket"),
        ]
    )

    assert _by_ids(groups) == [["poly-a"], ["poly-b"]]
    assert [group["event_ticker"] for group in groups] == [None, None]


def test_group_cofire_retains_every_leg_and_label_in_mixed_group():
    from pmfi.pipeline.cofire import group_cofire

    groups = group_cofire(
        [
            _item(
                "034a26f6",
                "KXWC1HTOTAL-26JUN20GERCIV-1",
                "2026-06-20T20:22:00+00:00",
                label="tp",
                rule="momentum_v1",
            ),
            _item(
                "34b0e9ce",
                "KXWC1HTOTAL-26JUN20GERCIV-2",
                "2026-06-20T20:23:00+00:00",
                label="noise",
                rule="directional_cluster_v1",
            ),
        ]
    )

    assert len(groups) == 1
    group = groups[0]
    assert group["event_ticker"] == "KXWC1HTOTAL-26JUN20GERCIV"
    assert "label" not in group
    assert [
        (leg["short_id"], leg["rule"], leg["market"], leg["outcome_key"], leg["label"])
        for leg in group["legs"]  # type: ignore[index]
    ] == [
        ("034a26f6", "momentum_v1", "KXWC1HTOTAL-26JUN20GERCIV-1", "yes", "tp"),
        (
            "34b0e9ce",
            "directional_cluster_v1",
            "KXWC1HTOTAL-26JUN20GERCIV-2",
            "yes",
            "noise",
        ),
    ]


def test_live_emission_paths_do_not_import_cofire():
    root = Path(__file__).resolve().parents[1]

    for rel_path in [
        "src/pmfi/pipeline/runner.py",
        "src/pmfi/pipeline/engine.py",
    ]:
        text = (root / rel_path).read_text(encoding="utf-8")
        assert "cofire" not in text.lower()
