"""Three-version generation, applying a version, and GPU guardrails for images."""

from __future__ import annotations

import pytest

import modules.api as api
from modules.ai_writer import AIWriter, VERSION_PLAN
from modules.errors import StageError
from modules.job_queue import JOB_KINDS
from modules.pipeline import MAX_IMAGES_PER_POST, Pipeline


class FakeOllama:
    """Deterministic stand-in for the local model (no GPU, no network)."""

    model = "fake:8b"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate_json(self, prompt, system=None, schema=None, temperature=0.0, num_predict=0):
        self.prompts.append(prompt)
        return {
            "headline": "FPT chạy thử nhà máy chip tại Bắc Ninh",
            "alt_headlines": ["Tiêu đề phụ một", "Tiêu đề phụ hai", "Tiêu đề phụ ba"],
            "main_post": (
                "FPT vừa chạy thử nhà máy kiểm thử chip tại Bắc Ninh sau nhiều tháng chuẩn bị.\n\n"
                "Nhà máy có quy mô 1.600 mét vuông và công suất 300 triệu chip mỗi năm, "
                "tuân thủ các tiêu chuẩn quốc tế trong quá trình vận hành thử.\n\n"
                "Bạn nghĩ sao về bước tiến này của ngành bán dẫn Việt Nam?"
            ),
            "short_post": "FPT chạy thử nhà máy chip tại Bắc Ninh, công suất 300 triệu chip/năm.",
            "long_post": "",
            "hashtags": ["#FPT", "#Chip", "#BacNinh"],
            "hook": "Bạn đã biết nhà máy chip này chưa?",
            "closing_question": "Bạn nghĩ sao về bước tiến này?",
            "image_prompt": (
                "editorial illustration of a semiconductor cleanroom with testing machines, "
                "blue palette, soft cinematic lighting, flat vector style"
            ),
            "image_negative_prompt": "text, watermark, real person, news photo",
            "image_concept": "Nhà máy chip hiện đại, sạch sẽ và tự động hóa.",
        }


@pytest.fixture()
def topic_with_research(temp_db):
    topic_id = temp_db.create_topic(
        {"title": "FPT vận hành thử nghiệm nhà máy chip", "category": "technology",
         "score": 78.0, "source_count": 2, "primary_url": "https://vnexpress.net/a"}
    )
    research_id = temp_db.create_research(
        {
            "topic_id": topic_id,
            "confidence": 72.0,
            "confidence_label": "moderate",
            "source_count": 2,
            "data": {
                "summary": "FPT chạy thử nhà máy chip tại Bắc Ninh.",
                "confidence": 72.0,
                "key_facts": [
                    {"fact": "Nhà máy tại Bắc Ninh", "verdict": "supported", "source_ids": [1, 2]},
                ],
                "quotes": [],
                "timeline": [],
                "entities": {"people": [], "organizations": ["FPT"]},
                "confirmed": ["Nhà máy tại Bắc Ninh"],
                "unconfirmed": [],
                "conflicting": [],
                "sources": [
                    {"id": 1, "domain": "vnexpress.net", "reliability": 0.9,
                     "title": "FPT chạy thử", "url": "https://vnexpress.net/a"},
                    {"id": 2, "domain": "tuoitre.vn", "reliability": 0.8,
                     "title": "Nhà máy chip", "url": "https://tuoitre.vn/b"},
                ],
            },
        }
    )
    return topic_id, research_id


def test_three_versions_are_generated_with_distinct_styles(temp_db, topic_with_research):
    topic_id, research_id = topic_with_research
    writer = AIWriter(database=temp_db, client=FakeOllama())
    result = writer.generate_versions(topic_id, research_id=research_id)

    versions = temp_db.list_versions(topic_id)
    assert [v["variant"] for v in versions] == ["A", "B", "C"]
    assert [v["style"] for v in versions] == [plan["style"] for plan in VERSION_PLAN]
    assert len(result["versions"]) == 3
    assert all(v["headline"] for v in versions)
    assert all((v["main_post"] or "").strip() for v in versions)
    assert temp_db.get_topic(topic_id)["status"] == "drafted"


def test_versions_keep_sources_and_share_the_image_prompt(temp_db, topic_with_research):
    topic_id, research_id = topic_with_research
    writer = AIWriter(database=temp_db, client=FakeOllama())
    writer.generate_versions(topic_id, research_id=research_id)

    versions = {v["variant"]: v for v in temp_db.list_versions(topic_id)}
    assert "https://vnexpress.net/a" in versions["A"]["main_post"]
    assert "https://vnexpress.net/a" not in versions["C"]["main_post"]
    assert versions["A"]["image_prompt"] and versions["B"]["image_prompt"] == versions["A"]["image_prompt"]
    assert versions["C"]["quality"]["score"] > 0
    assert all(v["source_count"] == 2 for v in versions.values())


def test_regenerating_replaces_previous_versions(temp_db, topic_with_research):
    topic_id, research_id = topic_with_research
    writer = AIWriter(database=temp_db, client=FakeOllama())
    writer.generate_versions(topic_id, research_id=research_id)
    writer.generate_versions(topic_id, research_id=research_id)
    assert len(temp_db.list_versions(topic_id)) == 3


def test_apply_version_creates_a_reviewable_post(temp_db, topic_with_research, monkeypatch):
    topic_id, research_id = topic_with_research
    writer = AIWriter(database=temp_db, client=FakeOllama())
    writer.generate_versions(topic_id, research_id=research_id)
    version = next(v for v in temp_db.list_versions(topic_id) if v["variant"] == "B")

    monkeypatch.setattr(api, "db", temp_db)
    post = api.apply_version(version["id"])

    assert post["status"] == "draft"
    assert post["headline"] == version["headline"]
    assert post["source_count"] if "source_count" in post else True
    assert post["sources"], "post must keep research sources"
    assert post["research_id"] == research_id
    assert temp_db.list_versions(topic_id)[0]["selected"] in (0, 1)
    selected = [v for v in temp_db.list_versions(topic_id) if v["selected"]]
    assert len(selected) == 1 and selected[0]["id"] == version["id"]


def test_phase2_job_kinds_are_queued() -> None:
    assert "versions" in JOB_KINDS
    assert "assess_image" in JOB_KINDS


def test_image_request_is_capped_at_three(temp_db, monkeypatch):
    topic_id = temp_db.create_topic({"title": "Chủ đề ảnh", "score": 60.0})
    post_id = temp_db.create_post({"topic_id": topic_id, "headline": "Bài", "main_post": "x"})
    monkeypatch.setattr(api, "db", temp_db)

    captured: dict = {}

    def fake_enqueue(kind, payload, **kwargs):
        captured.update({"kind": kind, "payload": payload})
        return 99

    monkeypatch.setattr(api.job_queue, "enqueue", fake_enqueue)
    result = api.generate_image(post_id, {"variations": 5})

    assert captured["payload"]["variations"] == 3
    assert result["variations"] == 3
    assert result["capped"] is True


def test_version_and_assessment_endpoints_enqueue_jobs(temp_db, monkeypatch):
    topic_id = temp_db.create_topic({"title": "Chủ đề", "score": 60.0})
    post_id = temp_db.create_post({"topic_id": topic_id, "headline": "Bài", "main_post": "x"})
    temp_db.add_image({"post_id": post_id, "path": "output/images/post/x.png"})
    monkeypatch.setattr(api, "db", temp_db)

    calls: list[tuple] = []

    def fake_enqueue(kind, payload, **kwargs):
        calls.append((kind, payload))
        return 100 + len(calls)

    monkeypatch.setattr(api.job_queue, "enqueue", fake_enqueue)

    versions = api.topic_versions(topic_id, {})
    assert versions["ok"] is True
    assert calls[0][0] == "versions" and calls[0][1]["topic_id"] == topic_id

    assessment = api.assess_image(post_id, {"image_id": 1})
    assert assessment["ok"] is True
    assert calls[1][0] == "assess_image"
    assert calls[1][1]["image_id"] == 1


def test_pipeline_refuses_more_images_after_the_limit(temp_db):
    topic_id = temp_db.create_topic({"title": "Chủ đề ảnh", "score": 60.0})
    post_id = temp_db.create_post({"topic_id": topic_id, "headline": "Bài", "main_post": "x"})
    for _ in range(MAX_IMAGES_PER_POST):
        temp_db.add_image({"post_id": post_id, "path": "output/images/x.png"})

    pipeline = Pipeline(database=temp_db)
    with pytest.raises(StageError) as excinfo:
        pipeline.generate_image(post_id, variations=2)
    assert "giới hạn" in str(excinfo.value).lower()
