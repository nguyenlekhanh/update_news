"""Reports: every report builds, aggregates real rows and exports Markdown."""

from __future__ import annotations

import modules.api as api
from modules.learning import EditorialLearning, KIND_APPROVE
from modules.reports import REPORT_KINDS, ReportService


def seed(db) -> int:
    topic_id = db.create_topic(
        {"title": "FPT mở nhà máy chip", "category": "technology", "score": 78.0}
    )
    post_id = db.create_post(
        {"topic_id": topic_id, "headline": "FPT mở nhà máy chip", "main_post": "Nội dung",
         "style": "analytical", "category": "technology", "status": "approved"}
    )
    db.add_history(topic_id, {"score": 78, "source_count": 2, "independent_domains": 2,
                              "phase": "rising"})
    db.add_image(
        {"post_id": post_id, "path": "output/images/post_1/a.png", "status": "done",
         "meta": {"image_style": "conceptual", "aspect": "1:1", "text_risk": "low",
                  "checks": {"score": 92}}}
    )
    db.add_metrics(
        {"post_id": post_id, "reach": 1000, "engagement": 120,
         "published_at": "2026-10-20T03:00:00+00:00"}
    )
    return post_id


def test_every_report_builds_with_sections_and_markdown(temp_db):
    seed(temp_db)
    service = ReportService(temp_db)

    for kind in REPORT_KINDS:
        report = service.build(kind, 14)
        assert report["kind"] == kind
        assert report["sections"]
        assert report["markdown"].startswith("# ")
        assert report["window_days"] == 14
        assert report["generated_at"]


def test_daily_trend_report_uses_stored_history(temp_db):
    seed(temp_db)
    report = ReportService(temp_db).build("daily_trend", 7)
    phases = next(section for section in report["sections"] if section["key"] == "phases")

    assert any(row["phase"] == "rising" for row in phases["rows"])
    assert any(item["label_vi"] == "Tổng chủ đề đã lưu" for item in report["summary"])


def test_approval_report_counts_statuses(temp_db):
    post_id = seed(temp_db)
    second = temp_db.create_post({"headline": "Nháp", "main_post": "x", "status": "draft"})
    report = ReportService(temp_db).build("approval", 30)
    statuses = next(section for section in report["sections"] if section["key"] == "status")

    assert {"status": "Đã duyệt", "count": 1} in statuses["rows"]
    assert any(row["post_id"] == second for row in
               next(section for section in report["sections"] if section["key"] == "pending")["rows"])
    assert any(item["label_vi"] == "Tỉ lệ duyệt" for item in report["summary"])
    assert post_id


def test_category_report_joins_imported_metrics(temp_db):
    seed(temp_db)
    report = ReportService(temp_db).build("category_performance", 30)
    section = report["sections"][0]
    technology = next(row for row in section["rows"] if row["category"] == "technology")

    assert technology["measured_posts"] == 1
    assert technology["reach"] == 1000
    assert technology["engagement"] == 120
    assert technology["engagement_rate"] == 12.0


def test_feedback_report_reads_learning_history(temp_db):
    learning = EditorialLearning(temp_db)
    for _ in range(3):
        learning.record(KIND_APPROVE, category="technology", style="analytical")

    report = ReportService(temp_db, learning=learning).build("editorial_feedback", 7)
    styles = next(section for section in report["sections"] if section["key"] == "styles")

    assert report["summary"][0]["value"] == 3
    assert any(row["style"] == "analytical" and row["samples"] == 3 for row in styles["rows"])
    assert "SQLite" in " ".join(report["notes"])


def test_image_report_flags_quality_data(temp_db):
    seed(temp_db)
    report = ReportService(temp_db).build("image_quality", 30)
    section = next(section for section in report["sections"] if section["key"] == "images")

    assert section["rows"]
    assert section["rows"][0]["score"] == 92
    assert any(item["label_vi"] == "Ảnh có điểm kiểm tra" for item in report["summary"])


def test_markdown_contains_tables_and_notes(temp_db):
    seed(temp_db)
    markdown = ReportService(temp_db).build("weekly_editorial", 7)["markdown"]

    assert "## Tóm tắt" in markdown
    assert "| Ngày |" in markdown
    assert "## Ghi chú" in markdown


def test_report_endpoint_returns_json_and_markdown(temp_db, monkeypatch):
    seed(temp_db)
    monkeypatch.setattr(api, "reports", ReportService(temp_db))

    data = api.get_report("approval", 7, "json")
    assert data["kind"] == "approval"

    response = api.get_report("approval", 7, "md")
    assert response.media_type.startswith("text/markdown")
    assert "# Báo cáo duyệt nội dung" in response.body.decode("utf-8")


def test_unknown_report_kind_is_rejected(temp_db, monkeypatch):
    import pytest

    monkeypatch.setattr(api, "reports", ReportService(temp_db))
    with pytest.raises(api.APIError):
        api.get_report("made_up", 7, "json")
