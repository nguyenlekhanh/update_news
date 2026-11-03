"""Viral intelligence: lifecycle phases and honest popularity labels."""

from __future__ import annotations

from datetime import timedelta

from modules.viral import assess, phase_of, snapshot
from modules.utils import utcnow


def _history(scores: list[float], hours_ago: list[float] | None = None) -> list[dict]:
    hours_ago = hours_ago or [len(scores) * 2 - i * 2 for i in range(len(scores))]
    return [
        {
            "score": score,
            "source_count": 2,
            "captured_at": (utcnow() - timedelta(hours=hours_ago[i])).isoformat(),
        }
        for i, score in enumerate(scores)
    ]


def test_single_snapshot_is_unknown_unless_story_is_fresh() -> None:
    fresh = phase_of(_history([70.0]), freshness_hours=3)
    assert fresh["phase"] == "emerging"

    old = phase_of(_history([70.0]), freshness_hours=40)
    assert old["phase"] == "unknown"
    assert "một lần" in old["reason"]


def test_rising_phase_from_score_increase() -> None:
    result = phase_of(_history([60.0, 66.0, 71.0]), freshness_hours=6)
    assert result["phase"] == "rising"
    assert "+" in result["reason"]


def test_peak_when_at_maximum_and_stable() -> None:
    result = phase_of(_history([70.0, 78.0, 78.5]), freshness_hours=4)
    assert result["phase"] == "peak"


def test_declining_when_falling_off_the_peak() -> None:
    result = phase_of(_history([80.0, 78.0, 68.0]), freshness_hours=8)
    assert result["phase"] == "declining"


def test_steady_when_movement_is_within_noise() -> None:
    result = phase_of(_history([70.0, 70.4]), freshness_hours=30)
    assert result["phase"] == "steady"


def _topic(score: float = 70.0, source_count: int = 2, traffic=None, engagement=None) -> dict:
    return {
        "id": 1,
        "title": "Chủ đề thử nghiệm",
        "score": score,
        "source_count": source_count,
        "primary_url": "https://vnexpress.net/a",
        "last_seen_at": (utcnow() - timedelta(hours=3)).isoformat(),
        "meta": {"traffic": traffic, "engagement": engagement},
    }


def _articles(domains: list[str]) -> list[dict]:
    return [
        {
            "domain": domain,
            "url": f"https://{domain}/story",
            "published_at": (utcnow() - timedelta(hours=2)).isoformat(),
        }
        for domain in domains
    ]


def test_ordinary_news_is_not_labelled_viral() -> None:
    result = assess(_topic(), _articles(["vnexpress.net", "tuoitre.vn"]), _history([70.0]))
    assert result["viral"]["label"] == "reported"
    assert "chưa có bằng chứng lan truyền" in result["viral"]["explanation"]


def test_wide_coverage_without_measured_traffic_is_not_viral() -> None:
    result = assess(
        _topic(source_count=4),
        _articles(["vnexpress.net", "tuoitre.vn", "thanhnien.vn", "nhandan.vn"]),
        _history([70.0, 72.0]),
    )
    assert result["viral"]["label"] == "wide_coverage"
    assert "chưa thể gọi là viral" in result["viral"]["explanation"]


def test_viral_candidate_requires_verified_traffic_or_engagement() -> None:
    by_traffic = assess(
        _topic(traffic=45_000, source_count=3),
        _articles(["vnexpress.net", "tuoitre.vn", "thanhnien.vn"]),
        _history([70.0, 74.0]),
    )
    assert by_traffic["viral"]["label"] == "viral_candidate"

    by_engagement = assess(
        _topic(source_count=2, engagement={"verified": True, "reactions": 500, "comments": 80,
                                          "shares": 40}),
        _articles(["vnexpress.net", "tuoitre.vn"]),
        _history([70.0, 71.0]),
    )
    assert by_engagement["viral"]["label"] == "viral_candidate"


def test_unverified_engagement_is_ignored() -> None:
    result = assess(
        _topic(source_count=2, engagement={"verified": False, "reactions": 9000}),
        _articles(["vnexpress.net", "tuoitre.vn"]),
        _history([70.0]),
    )
    assert result["viral"]["label"] != "viral_candidate"
    assert any("tương tác" in item for item in result["viral"]["missing_signals"])


def test_single_source_has_insufficient_signals() -> None:
    result = assess(_topic(source_count=1), _articles(["vnexpress.net"]), _history([55.0]))
    assert result["viral"]["label"] == "insufficient"
    assert result["viral"]["disclaimer"]


def test_assessment_exposes_history_and_delta() -> None:
    history = _history([60.0, 66.0])
    result = assess(_topic(), _articles(["vnexpress.net", "tuoitre.vn"]), history)
    assert result["score_delta"] == 6.0
    assert len(result["history"]) == 2
    assert result["phase"] in {"rising", "peak", "emerging", "steady"}


def test_snapshot_payload_contains_phase_and_label() -> None:
    payload = snapshot(_topic(traffic=30_000, source_count=3),
                       _articles(["vnexpress.net", "tuoitre.vn", "thanhnien.vn"]))
    assert payload["phase"] in {"emerging", "rising", "peak", "declining", "steady", "unknown"}
    assert payload["meta"]["viral_label"] in {"viral_candidate", "wide_coverage", "reported", "insufficient"}
    assert payload["score"] == 70.0
