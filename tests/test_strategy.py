"""Editorial strategy recommendations, overrides and writer option merging."""

from __future__ import annotations

import pytest

import modules.api as api
from modules import strategy


def intelligence(category: str = "general", phase: str = "rising", fresh: float = 3.0,
                 domains: int = 3, label: str = "wide_coverage") -> dict:
    return {
        "phase": phase,
        "freshness_hours": fresh,
        "independent_domains": domains,
        "viral": {"label": label},
    }


def research(conflicts: int = 0, unconfirmed: int = 0, confidence: float = 72.0) -> dict:
    return {
        "status": "complete",
        "data": {
            "confidence": confidence,
            "conflicting": [{"claim": f"mâu thuẫn {i}"} for i in range(conflicts)],
            "unconfirmed": [f"chưa xác nhận {i}" for i in range(unconfirmed)],
            "key_facts": [{"fact": "dữ kiện", "verdict": "supported"}],
        },
    }


def test_fresh_general_story_gets_breaking_news():
    result = strategy.recommend(
        {"id": 1, "title": "Tin mới", "category": "general", "score": 80},
        research(), intelligence("general"),
    )

    assert result["key"] == strategy.STRATEGY_BREAKING
    assert result["style"] == "professional_journalism"
    assert result["length"] == "short"
    assert result["engine"] == "rule_based"


def test_technology_story_gets_explainer():
    result = strategy.recommend(
        {"id": 1, "title": "Chip mới", "category": "technology", "score": 70},
        research(), intelligence("technology", fresh=30.0),
    )
    assert result["key"] == strategy.STRATEGY_TECH
    assert "giải thích" in result["summary"].lower()


def test_business_story_avoids_investment_advice():
    result = strategy.recommend(
        {"id": 1, "title": "Giá vàng", "category": "finance", "score": 70},
        research(), intelligence("finance", fresh=30.0),
    )
    assert result["key"] == strategy.STRATEGY_BUSINESS
    assert any("dự báo giá" in item for item in result["avoid"])
    assert any("lời khuyên đầu tư" in item for item in result["checklist"])


def test_conflicting_research_forces_balanced_strategy():
    result = strategy.recommend(
        {"id": 1, "title": "Tranh cãi", "category": "technology", "score": 70},
        research(conflicts=2), intelligence("technology", fresh=2.0),
    )
    assert result["key"] == strategy.STRATEGY_SENSITIVE
    assert any("xung đột" in reason for reason in result["reasons"])


def test_user_override_wins_and_keeps_the_recommendation():
    base = strategy.recommend(
        {"id": 1, "title": "Tin", "category": "general", "score": 70},
        research(), intelligence(),
    )
    result = strategy.overridden(base, strategy.STRATEGY_LIFESTYLE, note="muốn kể chuyện")

    assert result["effective_key"] == strategy.STRATEGY_LIFESTYLE
    assert result["is_override"] is True
    assert result["recommended_key"] == strategy.STRATEGY_BREAKING
    assert result["style"] == "friendly_storytelling"
    assert result["override_note"] == "muốn kể chuyện"


def test_invalid_override_falls_back_to_recommendation():
    base = strategy.recommend(
        {"id": 1, "title": "Tin", "category": "general", "score": 70},
        research(), intelligence(),
    )
    result = strategy.overridden(base, "does_not_exist")
    assert result["effective_key"] == base["key"]
    assert result["is_override"] is False


def test_apply_options_respects_explicit_writer_choices():
    base = strategy.recommend(
        {"id": 1, "title": "Tin", "category": "general", "score": 70},
        research(), intelligence(),
    )
    merged = strategy.apply_options(base, {"style": "analytical", "tone": "warm"})

    assert merged["style"] == "analytical"
    assert merged["tone"] == "warm"
    assert merged["length"] == base["length"]
    assert merged["strategy_key"] == base["key"]


def test_prompt_block_contains_focus_and_avoid_lists():
    block = strategy.prompt_block(strategy.STRATEGY_BUSINESS)

    assert "CHIẾN LƯỢC BIÊN TẬP" in block
    assert "Trọng tâm" in block
    assert "Tránh" in block
    assert strategy.prompt_block("nope") == ""


def test_strategy_options_are_listed_for_the_dashboard():
    keys = [item["key"] for item in strategy.options()]
    assert keys == list(strategy.STRATEGY_ORDER)
    assert all(item["label_vi"] for item in strategy.options())


def test_strategy_api_stores_and_clears_override(temp_db, monkeypatch):
    topic_id = temp_db.create_topic({"title": "Chủ đề", "category": "technology", "score": 70})
    monkeypatch.setattr(api, "db", temp_db)

    fetched = api.get_topic_strategy(topic_id)
    assert fetched["effective"]["is_override"] is False
    assert fetched["recommended"]["key"] == strategy.STRATEGY_TECH

    updated = api.set_topic_strategy(
        topic_id, {"strategy": strategy.STRATEGY_LIFESTYLE, "note": "thử"}
    )
    assert updated["effective"]["effective_key"] == strategy.STRATEGY_LIFESTYLE
    assert temp_db.get_strategy(topic_id)["override"] == strategy.STRATEGY_LIFESTYLE

    cleared = api.reset_topic_strategy(topic_id)
    assert cleared["effective"]["is_override"] is False
    assert temp_db.get_strategy(topic_id)["override"] is None


def test_strategy_api_rejects_unknown_key(temp_db, monkeypatch):
    topic_id = temp_db.create_topic({"title": "Chủ đề", "category": "general"})
    monkeypatch.setattr(api, "db", temp_db)

    with pytest.raises(api.APIError) as excinfo:
        api.set_topic_strategy(topic_id, {"strategy": "invented"})
    assert excinfo.value.status_code == 400
