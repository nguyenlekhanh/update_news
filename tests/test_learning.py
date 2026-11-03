"""Editorial learning: feedback capture, preferences, prompt hints and reset."""

from __future__ import annotations

import modules.api as api
from modules.ai_writer import AIWriter
from modules.learning import EditorialLearning, KIND_APPROVE, KIND_REJECT


class FakeOllama:
    model = "fake:8b"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate_json(self, prompt, system=None, schema=None, temperature=0.0, num_predict=0):
        self.prompts.append(prompt)
        return {
            "headline": "Tiêu đề thử nghiệm",
            "alt_headlines": ["Một", "Hai", "Ba"],
            "main_post": (
                "FPT chạy thử nhà máy chip tại Bắc Ninh.\n\n"
                "Nhà máy có công suất 300 triệu chip mỗi năm và đang trong giai đoạn chạy thử.\n\n"
                "Bạn nghĩ sao về bước tiến này?"
            ),
            "short_post": "FPT chạy thử nhà máy chip tại Bắc Ninh.",
            "long_post": "",
            "hashtags": ["#FPT", "#Chip"],
            "hook": "Bạn đã biết chưa?",
            "closing_question": "Bạn nghĩ sao?",
            "image_prompt": "editorial illustration of a cleanroom, blue palette",
            "image_negative_prompt": "text, watermark",
            "image_concept": "Nhà máy chip.",
        }


def make_topic_with_research(db) -> tuple[int, int]:
    topic_id = db.create_topic(
        {"title": "FPT chạy thử nhà máy chip", "category": "technology", "score": 70.0}
    )
    research_id = db.create_research(
        {
            "topic_id": topic_id,
            "confidence": 72.0,
            "confidence_label": "moderate",
            "source_count": 1,
            "data": {
                "summary": "FPT chạy thử nhà máy chip tại Bắc Ninh.",
                "confidence": 72.0,
                "key_facts": [{"fact": "Nhà máy ở Bắc Ninh", "verdict": "supported"}],
                "sources": [
                    {"id": 1, "domain": "vnexpress.net", "reliability": 0.9,
                     "title": "FPT", "url": "https://vnexpress.net/a"},
                ],
            },
        }
    )
    return topic_id, research_id


def test_feedback_is_recorded_and_aggregated(temp_db):
    learning = EditorialLearning(temp_db)
    topic_id = temp_db.create_topic({"title": "Chủ đề", "category": "technology"})
    for _ in range(2):
        learning.record(
            KIND_APPROVE, topic_id=topic_id, category="technology",
            style="professional_journalism",
        )
    learning.record(
        KIND_REJECT, topic_id=topic_id, category="technology",
        style="viral_short", detail={"reason": "quá giật gân"},
    )
    learning.record(
        "edit", topic_id=topic_id, category="technology", style="professional_journalism",
        detail={"headline_change": 0.4, "body_change": 0.3},
    )

    prefs = learning.preferences("technology")
    styles = {row["key"]: row for row in prefs["styles"]}

    assert prefs["samples"] == 4
    assert styles["professional_journalism"]["approved"] == 2
    assert styles["professional_journalism"]["approval_rate"] == 100.0
    assert styles["viral_short"]["approval_rate"] == 0.0
    assert prefs["edit_behaviour"]["headline_change"] == 0.4
    assert prefs["reject_reasons"] == ["quá giật gân"]


def test_style_hint_waits_for_minimum_samples(temp_db):
    learning = EditorialLearning(temp_db)
    for _ in range(2):
        learning.record(
            KIND_APPROVE, category="technology", style="professional_journalism"
        )
    assert learning.style_hint("technology") == ""

    learning.record(KIND_APPROVE, category="technology", style="professional_journalism")
    hint = learning.style_hint("technology")
    assert "KINH NGHIỆM BIÊN TẬP" in hint
    assert "professional_journalism" in hint


def test_style_hint_is_scoped_to_the_category(temp_db):
    learning = EditorialLearning(temp_db)
    for _ in range(3):
        learning.record(KIND_APPROVE, category="technology", style="analytical")

    assert "analytical" in learning.style_hint("technology")
    assert learning.style_hint("lifestyle") == ""


def test_reset_deletes_learning_history(temp_db):
    learning = EditorialLearning(temp_db)
    learning.record(KIND_APPROVE, category="technology", style="analytical")
    learning.record(KIND_REJECT, category="business", style="viral_short")

    removed = learning.reset(category="technology")

    assert removed["deleted"] == 1
    assert learning.preferences()["samples"] == 1
    assert learning.reset()["deleted"] == 1
    assert learning.preferences()["samples"] == 0


def test_approve_and_reject_endpoints_write_feedback(temp_db, monkeypatch):
    learning = EditorialLearning(temp_db)
    monkeypatch.setattr(api, "db", temp_db)
    topic_id = temp_db.create_topic({"title": "Chủ đề", "category": "technology"})
    post_id = temp_db.create_post(
        {"topic_id": topic_id, "headline": "Bài", "main_post": "Nội dung",
         "style": "analytical", "category": "technology"}
    )

    api.approve_post(post_id)
    rejected = temp_db.create_post(
        {"topic_id": topic_id, "headline": "Bài 2", "main_post": "Nội dung"}
    )
    api.reject_post(rejected, {"reason": "thiếu nguồn"})

    rows = temp_db.list_feedback()
    kinds = {row["kind"] for row in rows}
    assert kinds == {KIND_APPROVE, KIND_REJECT}
    assert any((row.get("detail") or {}).get("reason") == "thiếu nguồn" for row in rows)


def test_editing_a_post_records_the_change(temp_db, monkeypatch):
    learning = EditorialLearning(temp_db)
    monkeypatch.setattr(api, "db", temp_db)
    topic_id = temp_db.create_topic({"title": "Chủ đề", "category": "technology"})
    post_id = temp_db.create_post(
        {"topic_id": topic_id, "headline": "Tiêu đề cũ",
         "main_post": "Nội dung cũ ngắn.", "style": "analytical"}
    )

    api.update_post(post_id, {"headline": "Tiêu đề mới hoàn toàn khác"})

    rows = [r for r in temp_db.list_feedback() if r["kind"] == "edit"]
    assert rows and rows[0]["detail"]["headline_change"] > 0.2


def test_image_selection_is_recorded(temp_db, monkeypatch):
    learning = EditorialLearning(temp_db)
    monkeypatch.setattr(api, "db", temp_db)
    topic_id = temp_db.create_topic({"title": "Chủ đề", "category": "technology"})
    post_id = temp_db.create_post({"topic_id": topic_id, "headline": "Bài", "main_post": "x"})
    image_id = temp_db.add_image(
        {"post_id": post_id, "path": "output/images/x.png",
         "meta": {"image_style": "infographic", "aspect": "1:1"}}
    )

    api.select_image(post_id, image_id)

    rows = [r for r in temp_db.list_feedback() if r["kind"] == "image_select"]
    assert rows and rows[0]["signal"] == "infographic"


def test_version_choice_is_recorded_with_rejected_variants(temp_db, monkeypatch):
    learning = EditorialLearning(temp_db)
    monkeypatch.setattr(api, "db", temp_db)
    topic_id, research_id = make_topic_with_research(temp_db)
    version_id = temp_db.create_version(
        {"topic_id": topic_id, "research_id": research_id, "variant": "B",
         "style": "conversational", "headline": "B", "main_post": "Nội dung"}
    )
    temp_db.create_version(
        {"topic_id": topic_id, "research_id": research_id, "variant": "A",
         "style": "professional_journalism", "headline": "A", "main_post": "Nội dung"}
    )

    api.apply_version(version_id)

    rows = [r for r in temp_db.list_feedback() if r["kind"] == "version_select"]
    assert rows and rows[0]["detail"]["variant"] == "B"
    assert rows[0]["detail"]["rejected_variants"] == ["A"]


def test_rating_endpoint_validates_range(temp_db, monkeypatch):
    learning = EditorialLearning(temp_db)
    monkeypatch.setattr(api, "db", temp_db)
    post_id = temp_db.create_post({"headline": "Bài", "main_post": "x"})

    api.rate_post(post_id, {"rating": 4, "note": "ổn"})
    rows = [r for r in temp_db.list_feedback() if r["kind"] == "rating"]
    assert rows[0]["rating"] == 4
    assert rows[0]["detail"]["note"] == "ổn"

    import pytest

    with pytest.raises(api.APIError):
        api.rate_post(post_id, {"rating": 9})


def test_writer_prompt_includes_strategy_and_learned_hint(temp_db):
    topic_id, research_id = make_topic_with_research(temp_db)
    learning = EditorialLearning(temp_db)
    for _ in range(3):
        learning.record(
            KIND_APPROVE, category="technology", style="professional_journalism"
        )
    client = FakeOllama()
    writer = AIWriter(database=temp_db, client=client)

    writer.generate_versions(
        topic_id, research_id=research_id,
        options={"strategy_key": "tech_explainer"},
    )

    assert "CHIẾN LƯỢC BIÊN TẬP" in client.prompts[0]
    assert "KINH NGHIỆM BIÊN TẬP" in client.prompts[0]
