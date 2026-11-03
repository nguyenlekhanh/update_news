"""Content quality report: measured components, honest labels, verdicts."""

from __future__ import annotations

from datetime import timedelta

from modules import quality
from modules.utils import utcnow


def _research(**overrides) -> dict:
    data = {
        "summary": "FPT chạy thử nhà máy chip tại Bắc Ninh.",
        "confidence": 72.0,
        "confidence_label": "moderate",
        "key_facts": [
            {"fact": "Nhà máy đặt tại Bắc Ninh", "verdict": "supported", "source_ids": [1, 2]},
            {"fact": "Công suất 300 triệu chip/năm", "verdict": "single_source", "source_ids": [1]},
        ],
        "conflicting": [],
        "sources": [
            {"id": 1, "domain": "vnexpress.net", "reliability": 0.9,
             "published_at": (utcnow() - timedelta(hours=2)).isoformat()},
            {"id": 2, "domain": "tuoitre.vn", "reliability": 0.8,
             "published_at": (utcnow() - timedelta(hours=5)).isoformat()},
        ],
    }
    data.update(overrides)
    return {"id": 1, "data": data, "confidence": data.get("confidence")}


def _post(**overrides) -> dict:
    body = "\n\n".join([
        "Mở bài: FPT vừa chạy thử nhà máy chip tại Bắc Ninh sau nhiều tháng chuẩn bị.",
        " ".join(["Thân bài mô tả quy mô nhà máy và tiến độ thử nghiệm."] * 8),
        "Kết bài: câu hỏi mở cho người đọc.\n\nNguồn tham khảo: https://vnexpress.net/a",
    ])
    post = {
        "id": 7,
        "topic_id": 10,
        "headline": "FPT chạy thử nhà máy chip",
        "main_post": body,
        "hook": "Bạn đã biết?",
        "closing_question": "Bạn nghĩ sao?",
        "hashtags": ["#FPT", "#Chip", "#BacNinh"],
        "sources": [{"url": "https://vnexpress.net/a"}],
        "ai_flags": [],
        "images": [],
        "topic": {
            "last_seen_at": (utcnow() - timedelta(hours=3)).isoformat(),
            "research": _research(),
        },
    }
    post.update(overrides)
    return post


def test_components_are_labelled_and_weighted() -> None:
    report = quality.evaluate(_post())
    assert report["overall"] is not None
    assert report["components"]["source_reliability"]["kind"] == "verified"
    assert report["components"]["content"]["kind"] == "heuristic"
    assert 0 <= report["components"]["source_reliability"]["score"] <= 100
    assert report["verdict"] in {"ready", "review", "weak"}
    assert report["coverage"] > 0


def test_source_reliability_reflects_domains() -> None:
    component = quality.evaluate(_post())["components"]["source_reliability"]
    assert component["score"] == 85.0
    assert component["domains"] == ["tuoitre.vn", "vnexpress.net"]
    assert component["label"] in {"high", "medium"}


def test_unsupported_claims_are_surfaced_and_weaken_verdict() -> None:
    research = _research(
        key_facts=[
            {"fact": "Nhà máy có 5.000 công nhân", "verdict": "unsupported", "source_ids": [1]},
        ],
        confidence=70.0,
    )
    post = _post(topic={"last_seen_at": utcnow().isoformat(), "research": research})
    report = quality.evaluate(post)
    assert report["unsupported_claims"][0]["claim"] == "Nhà máy có 5.000 công nhân"
    assert report["verdict"] == "weak"
    assert any("chưa được nguồn ủng hộ" in reason for reason in report["verdict_reasons"])


def test_low_confidence_forces_review() -> None:
    research = _research(confidence=38.0)
    report = quality.evaluate(_post(topic={"last_seen_at": utcnow().isoformat(), "research": research}))
    assert report["verdict"] in {"review", "weak"}
    assert any("Độ tin cậy" in reason for reason in report["verdict_reasons"])


def test_content_score_rewards_complete_posts() -> None:
    good = quality.evaluate(_post())["components"]["content"]
    weak = quality.evaluate(_post(main_post="Quá ngắn.", hook="", closing_question="",
                                  hashtags=[]))["components"]["content"]
    assert good["score"] > weak["score"]
    assert good["score"] > 60


def test_ai_warning_flags_lower_the_content_score() -> None:
    flagged = _post(ai_flags=[{"type": "copied_text", "severity": "warning", "detail": "x"},
                              {"type": "unverified_number", "severity": "warning", "detail": "y"}])
    assert quality.evaluate(flagged)["components"]["content"]["score"] < \
        quality.evaluate(_post())["components"]["content"]["score"]


def test_image_relevance_states_when_it_is_unassessed() -> None:
    unassessed = quality.evaluate(_post(
        images=[{"id": 3, "path": "output/images/post_7/b.png", "selected": 1, "meta": {}}],
        image_prompt="editorial illustration of a chip cleanroom, "
                     "blue palette, cinematic lighting, flat vector",
    ))
    assert unassessed["components"]["image"]["kind"] == "heuristic"
    assert unassessed["components"]["image"]["assessed"] is False
    assert "vision" in unassessed["components"]["image"]["detail"]

    assessed_post = _post(
        images=[{
            "id": 4, "path": "output/images/post_7/a.png", "selected": 1,
            "meta": {"relevance": {"score": 82.0, "note": "Ảnh sạch sẽ đúng chủ đề",
                                   "model": "qwen2.5vl:7b", "matches_story": True,
                                   "assessed_at": utcnow().isoformat()}},
        }]
    )
    assessed = quality.evaluate(assessed_post)["components"]["image"]
    assert assessed["kind"] == "ai_assessed"
    assert assessed["score"] == 82.0

    missing = quality.evaluate(_post(images=[]))["components"]["image"]
    assert missing["score"] is None and missing["kind"] == "unavailable"


def test_freshness_decays_with_time() -> None:
    fresh = quality.evaluate(_post())["components"]["freshness"]
    assert fresh["kind"] == "verified"
    assert fresh["score"] > 70
    assert fresh["hours"] <= 3

    old_research = _research(sources=[
        {"id": 1, "domain": "vnexpress.net", "reliability": 0.9,
         "published_at": (utcnow() - timedelta(hours=60)).isoformat()},
    ])
    old = quality.evaluate(_post(topic={"last_seen_at": (utcnow() - timedelta(hours=60)).isoformat(),
                                        "research": old_research}))["components"]["freshness"]
    assert old["score"] < 30


def test_vision_model_candidates_prefer_configured_then_vl_families() -> None:
    health = {"models": [
        {"name": "llama3.2-vision:latest", "capabilities": ["vision"]},
        {"name": "qwen2.5vl:7b", "capabilities": ["vision"]},
        {"name": "qwen3:8b", "capabilities": ["completion"]},
    ]}
    assert quality.vision_model_candidates(health, "qwen2.5vl:7b")[0] == "qwen2.5vl:7b"
    order = quality.vision_model_candidates(health, None)
    assert order[0] == "qwen2.5vl:7b"
    assert "qwen3:8b" not in order
    assert quality.pick_vision_model({"models": []}) is None


def test_missing_research_is_reported_not_guessed() -> None:
    report = quality.evaluate(_post(topic={"last_seen_at": utcnow().isoformat()}, images=[]))
    assert report["components"]["source_reliability"]["score"] is None
    assert report["components"]["fact_check"]["score"] is None
    assert report["coverage"] < 1
