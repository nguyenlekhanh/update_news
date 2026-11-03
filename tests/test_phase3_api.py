"""Phase 3 API surface: prediction/strategy on trends, image checks, calendar,
learning, analytics and the deliberate absence of any publishing endpoint."""

from __future__ import annotations

import struct
import zlib
from datetime import timedelta

import pytest

import modules.api as api
from modules.analytics import Analytics
from modules.content_calendar import ContentCalendar
from modules.learning import EditorialLearning
from modules.utils import utcnow


def make_png(path, width: int = 1024, height: int = 1024):
    raw = b"".join(
        b"\x00" + bytes((x * 5 + y * 3) % 256 for x in range(width * 3))
        for y in range(height)
    )

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )
    return path


def seed_topic(db, title: str = "Chủ đề", category: str = "technology", score: float = 75.0):
    topic_id = db.create_topic(
        {"title": title, "category": category, "score": score, "source_count": 3,
         "primary_url": "https://vnexpress.net/a"}
    )
    now = utcnow()
    for index, value in enumerate((60.0, 66.0, 75.0)):
        db.add_history(
            topic_id,
            {
                "score": value,
                "source_count": 3,
                "independent_domains": 3,
                "captured_at": (now - timedelta(hours=(2 - index) * 6)).isoformat(),
            },
        )
    return topic_id


def test_trend_rows_carry_prediction_and_strategy(temp_db, monkeypatch):
    seed_topic(temp_db)
    monkeypatch.setattr(api, "db", temp_db)

    data = api.list_trends(status=None, category=None, search=None, min_score=None,
                           limit=10, offset=0)
    row = data["items"][0]

    assert row["prediction"]["current_score"] == 75.0
    assert row["prediction"]["previous_score"] == 66.0
    assert row["prediction"]["forecast"]["kind"] == "estimated"
    assert row["strategy"]["effective_key"]
    assert row["strategy"]["is_override"] is False
    assert row["strategy"]["engine"] == "rule_based"


def test_prediction_endpoint_adds_history_compare(temp_db, monkeypatch):
    topic_id = seed_topic(temp_db)
    monkeypatch.setattr(api, "db", temp_db)

    result = api.trend_prediction(topic_id)

    assert result["prediction"]["momentum"]["label"] in ("rising", "steady", "accelerating")
    assert result["compare"]["runs"] == 3
    assert result["compare"]["peak_score"] == 75.0


def test_writer_job_receives_strategy_options(temp_db, monkeypatch):
    topic_id = seed_topic(temp_db, category="finance")
    monkeypatch.setattr(api, "db", temp_db)
    captured: dict = {}

    def fake_enqueue(kind, payload, **kwargs):
        captured.update({"kind": kind, "payload": payload})
        return 10

    monkeypatch.setattr(api.job_queue, "enqueue", fake_enqueue)
    api.topic_write(topic_id, {})

    assert captured["kind"] == "write"
    assert captured["payload"]["options"]["strategy_key"] == "business_impact"
    assert captured["payload"]["options"]["style"] == "analytical"


def test_image_check_endpoint_stores_checks(temp_db, tmp_path, monkeypatch):
    topic_id = seed_topic(temp_db)
    post_id = temp_db.create_post(
        {"topic_id": topic_id, "headline": "Bài", "main_post": "x",
         "image_prompt": "editorial illustration of a cleanroom"}
    )
    path = make_png(tmp_path / "generated.png")
    image_id = temp_db.add_image(
        {"post_id": post_id, "path": str(path), "prompt": "editorial illustration",
         "meta": {"image_style": "editorial_illustration", "aspect": "1:1"}}
    )
    monkeypatch.setattr(api, "db", temp_db)

    result = api.post_image_check(post_id, {"image_id": image_id})

    assert result["width"] == 1024
    assert result["verdict"] == "ok"
    stored = temp_db.query_one("SELECT meta FROM images WHERE id = ?", (image_id,))
    assert stored["meta"]["checks"]["score"] >= 90
    assert stored["meta"]["sha256"]


def test_image_request_validates_style_and_aspect(temp_db, monkeypatch):
    topic_id = seed_topic(temp_db)
    post_id = temp_db.create_post({"topic_id": topic_id, "headline": "Bài", "main_post": "x"})
    monkeypatch.setattr(api, "db", temp_db)

    with pytest.raises(api.APIError):
        api.generate_image(post_id, {"style": "oil_painting"})
    with pytest.raises(api.APIError):
        api.generate_image(post_id, {"aspect": "3:7"})


def test_calendar_endpoints_schedule_and_validate(temp_db, monkeypatch):
    topic_id = seed_topic(temp_db)
    post_id = temp_db.create_post(
        {"topic_id": topic_id, "headline": "Bài lịch", "main_post": "x", "status": "draft"}
    )
    calendar = ContentCalendar(temp_db)
    monkeypatch.setattr(api, "db", temp_db)
    monkeypatch.setattr(api.content_calendar, "service", calendar)

    slot = next(
        item["scheduled_at"] for item in calendar.slots(7)
        if item["scheduled_at"] > utcnow().isoformat()
    )
    created = api.create_calendar_entry({"post_id": post_id, "scheduled_at": slot})
    assert created["ok"] is True
    assert temp_db.list_calendar_entries()

    plan = api.get_calendar(7)
    assert "plan" in plan and "reminders" in plan

    with pytest.raises(api.APIError):
        api.create_calendar_entry({"scheduled_at": slot})
    with pytest.raises(api.APIError):
        api.create_calendar_entry({"post_id": post_id, "scheduled_at": "not-a-date"})

    entry_id = temp_db.list_calendar_entries()[0]["id"]
    removed = api.delete_calendar_entry(entry_id)
    assert removed["ok"] is True


def test_learning_endpoints_expose_and_clear_history(temp_db, monkeypatch):
    learning = EditorialLearning(temp_db)
    learning.record("approve", category="technology", style="analytical")
    monkeypatch.setattr(api, "db", temp_db)

    summary = api.learning_summary()
    assert summary["samples"] == 1
    assert "SQLite" in summary["privacy"]

    removed = api.learning_reset()
    assert removed["deleted"] == 1
    assert api.learning_summary()["samples"] == 0


def test_analytics_endpoint_adds_trend_shift(temp_db, monkeypatch):
    seed_topic(temp_db)
    monkeypatch.setattr(api, "db", temp_db)
    monkeypatch.setattr(api, "analytics", Analytics(temp_db))

    data = api.get_analytics(14)

    assert "trend_shift" in data
    assert data["trend_shift"]["kind"] == "measured"
    assert "funnel" in data  # existing payload preserved


def test_no_publishing_or_engagement_automation_routes_exist():
    paths = [getattr(route, "path", "") for route in api.app.routes]
    forbidden = ("publish", "auto_post", "auto-like", "auto_comment", "scrape")

    assert not [
        path for path in paths
        if any(word in path.lower() for word in forbidden)
    ]


def test_facebook_endpoints_are_read_or_import_only():
    methods = {}
    for route in api.app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/facebook"):
            methods[path] = set(getattr(route, "methods", set()))
    assert methods
    for path, verbs in methods.items():
        assert verbs <= {"GET", "POST"}, path
        assert "DELETE" not in verbs or path.endswith("metrics")
