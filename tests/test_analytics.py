"""Trend analytics: funnel, series, categories, recurring topics."""

from __future__ import annotations

from datetime import timedelta

import modules.api as api
from modules.analytics import Analytics, service as default_analytics
from modules.utils import utcnow, utcnow_iso


def _seed(db):
    tech_id = db.create_topic({"title": "FPT nhà máy chip", "category": "technology", "score": 80.0})
    vn_id = db.create_topic({"title": "Giá vàng hôm nay", "category": "vietnam", "score": 70.0})
    other_id = db.create_topic({"title": "Tin khác", "category": "international", "score": 50.0})

    db.create_research(
        {
            "topic_id": tech_id, "confidence": 72.0, "confidence_label": "moderate",
            "data": {"summary": "x", "sources": []},
        }
    )
    approved = db.create_post({"topic_id": tech_id, "headline": "A", "main_post": "x",
                               "status": "approved"})
    db.update_post(approved, {"approved_at": utcnow_iso()})
    rejected = db.create_post({"topic_id": vn_id, "headline": "B", "main_post": "y",
                               "status": "rejected"})
    db.update_post(rejected, {"rejected_at": utcnow_iso()})
    db.create_post({"topic_id": other_id, "headline": "C", "main_post": "z", "status": "draft"})

    for score in (62.0, 70.0, 78.0):
        db.add_history(tech_id, {
            "score": score, "source_count": 2, "independent_domains": 2,
            "captured_at": (utcnow() - timedelta(hours=24 - score / 10)).isoformat(),
            "phase": "rising",
        })
    return tech_id, vn_id, other_id


def test_overview_counts_the_funnel(temp_db) -> None:
    tech_id, _, _ = _seed(temp_db)
    report = Analytics(temp_db).overview(days=7)

    assert report["funnel"]["discovered"] == 3
    assert report["funnel"]["researched"] == 1
    assert report["funnel"]["drafted"] == 3
    assert report["funnel"]["approved"] == 1
    assert report["funnel"]["rejected"] == 1
    assert report["window_days"] == 7
    assert len(report["series"]) == 7
    assert sum(row["discovered"] for row in report["series"]) == 3
    assert sum(row["approved"] for row in report["series"]) == 1
    assert tech_id


def test_category_approval_rates(temp_db) -> None:
    _seed(temp_db)
    report = Analytics(temp_db).overview(days=7)
    categories = {row["category"]: row for row in report["categories"]}

    assert categories["technology"]["approved"] == 1
    assert categories["technology"]["approval_rate"] == 100.0
    assert categories["vietnam"]["rejected"] == 1
    assert categories["vietnam"]["approval_rate"] == 0.0
    assert categories["international"]["approval_rate"] is None


def test_recurring_topics_are_detected_from_history(temp_db) -> None:
    tech_id, _, _ = _seed(temp_db)
    report = Analytics(temp_db).overview(days=7)
    recurring = {row["topic_id"]: row for row in report["recurring"]}

    assert tech_id in recurring
    row = recurring[tech_id]
    assert row["appearances"] == 3
    assert row["score_change"] == 16.0
    assert row["peak_score"] == 78.0


def test_confidence_buckets_and_status_counts(temp_db) -> None:
    _seed(temp_db)
    report = Analytics(temp_db).overview(days=7)
    assert report["confidence"]["average"] == 72.0
    assert report["confidence"]["moderate"] == 1
    assert sum(report["status_counts"].values()) == 3
    assert report["status_counts"]["new"] == 3


def test_topic_timeline_is_chronological(temp_db) -> None:
    tech_id, _, _ = _seed(temp_db)
    timeline = Analytics(temp_db).topic_timeline(tech_id)
    scores = [point["score"] for point in timeline["points"]]
    assert scores == [62.0, 70.0, 78.0]


def test_history_is_removed_with_the_topic(temp_db) -> None:
    tech_id, _, _ = _seed(temp_db)
    temp_db.delete_topic(tech_id)
    assert temp_db.list_history(tech_id) == []


def test_api_analytics_endpoint_uses_the_store(temp_db, monkeypatch) -> None:
    _seed(temp_db)
    monkeypatch.setattr(api, "analytics", Analytics(temp_db))
    payload = api.get_analytics(days=5)
    assert payload["funnel"]["discovered"] == 3
    assert len(payload["series"]) == 5
    assert default_analytics is not None
