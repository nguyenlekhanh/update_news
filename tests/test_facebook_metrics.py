"""Facebook performance: manual import, validation, optional Graph access, analysis."""

from __future__ import annotations

import pytest

import modules.api as api
from modules.facebook_metrics import FacebookMetrics


def make_post(db, headline: str = "Bài thử", style: str = "analytical") -> tuple[int, int]:
    topic_id = db.create_topic({"title": "Chủ đề", "category": "business", "score": 60.0})
    post_id = db.create_post(
        {"topic_id": topic_id, "headline": headline, "main_post": "x", "style": style}
    )
    return topic_id, post_id


def test_csv_import_stores_metrics(temp_db):
    _, post_id = make_post(temp_db)
    csv_text = (
        "post_id,reach,engagement,reactions,comments,shares,link_clicks,published_at\n"
        f"{post_id},1200,150,120,20,10,5,2026-10-20T10:00:00+00:00\n"
    )

    result = FacebookMetrics(temp_db).import_csv(csv_text)

    assert result["stored"] == 1
    rows = temp_db.list_metrics()
    assert rows[0]["reach"] == 1200
    assert rows[0]["shares"] == 10


def test_import_rejects_bad_numbers_and_unknown_posts(temp_db):
    _, post_id = make_post(temp_db)
    rows = [
        {"post_id": post_id, "reach": "abc"},
        {"post_id": 9999, "reach": 10},
        {"reach": 10},
    ]

    result = FacebookMetrics(temp_db).import_rows(rows)

    assert result["stored"] == 0
    assert len(result["errors"]) == 3


def test_import_is_idempotent_for_same_timestamp(temp_db):
    _, post_id = make_post(temp_db)
    rows = [{"post_id": post_id, "reach": 100, "published_at": "2026-10-20T10:00:00+00:00"}]
    service = FacebookMetrics(temp_db)

    service.import_rows(rows)
    service.import_rows(rows)

    assert len(temp_db.list_metrics()) == 1


def test_graph_fetch_without_token_explains_the_alternative(temp_db):
    _, post_id = make_post(temp_db)

    with pytest.raises(ValueError) as excinfo:
        FacebookMetrics(temp_db).fetch_graph(post_id, "123_456")

    message = str(excinfo.value)
    assert "FACEBOOK_ACCESS_TOKEN" in message
    assert "CSV" in message


def test_analysis_groups_by_style_and_hour(temp_db):
    _, first = make_post(temp_db, "Bài A", "analytical")
    _, second = make_post(temp_db, "Bài B", "friendly_storytelling")
    service = FacebookMetrics(temp_db)
    service.import_rows(
        [
            {"post_id": first, "reach": 1000, "engagement": 100,
             "published_at": "2026-10-20T02:00:00+00:00"},
            {"post_id": second, "reach": 1000, "engagement": 40,
             "published_at": "2026-10-20T02:30:00+00:00"},
            {"post_id": first, "reach": 500, "engagement": 50,
             "published_at": "2026-10-21T02:00:00+00:00"},
        ]
    )

    analysis = service.analysis(365)

    assert analysis["sample_size"] == 3
    assert analysis["totals"]["reach"] == 2500
    styles = {row["key"]: row for row in analysis["best"]["styles"]}
    assert styles["analytical"]["posts"] == 2
    assert styles["analytical"]["engagement"] == 150
    assert analysis["best_hours"]
    assert analysis["kind"] == "measured"


def test_analysis_without_data_is_labelled_unavailable(temp_db):
    analysis = FacebookMetrics(temp_db).analysis(90)
    assert analysis["sample_size"] == 0
    assert analysis["kind"] == "unavailable"
    assert analysis["averages"]["reach"] is None


def test_status_reports_manual_import_availability(temp_db):
    status = FacebookMetrics(temp_db).status()
    assert status["manual_import"] is True
    assert "FACEBOOK_ACCESS_TOKEN" in status["reason"]


def test_import_endpoint_and_post_metrics_endpoint(temp_db, monkeypatch):
    _, post_id = make_post(temp_db)
    monkeypatch.setattr(api, "db", temp_db)
    monkeypatch.setattr(api.facebook_metrics, "service", FacebookMetrics(temp_db))

    payload = {"rows": [{"post_id": post_id, "reach": 300, "comments": 4}]}
    result = api.facebook_metrics_import(payload)
    assert result["stored"] == 1

    metrics = api.post_metrics(post_id)
    assert metrics["totals"]["reach"] == 300
    assert metrics["totals"]["comments"] == 4


def test_import_endpoint_requires_payload(temp_db, monkeypatch):
    monkeypatch.setattr(api, "db", temp_db)
    with pytest.raises(api.APIError):
        api.facebook_metrics_import({})


def test_fetch_endpoint_reports_missing_configuration(temp_db, monkeypatch):
    _, post_id = make_post(temp_db)
    monkeypatch.setattr(api, "db", temp_db)
    monkeypatch.setattr(api.facebook_metrics, "service", FacebookMetrics(temp_db))

    with pytest.raises(api.APIError) as excinfo:
        api.fetch_post_metrics(post_id, {"external_id": "1_2"})
    assert "FACEBOOK_ACCESS_TOKEN" in str(excinfo.value)
