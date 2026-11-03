"""Fact checking and trend scoring: the parts that must never invent facts."""

from __future__ import annotations

from datetime import timedelta

from modules.fact_checker import FactChecker, VERDICT_SINGLE, VERDICT_SUPPORTED, VERDICT_UNSUPPORTED
from modules.trend_finder import same_story
from modules.trend_scorer import TrendScorer
from modules.utils import utcnow

SOURCES = [
    {
        "id": 1,
        "title": "FPT khởi công nhà máy chip tại Bắc Ninh",
        "url": "https://vnexpress.net/fpt-nha-may-chip",
        "domain": "vnexpress.net",
        "published_at": "2026-11-01T02:00:00+00:00",
        "reliability": 0.9,
        "content": (
            "Nhà máy chip của FPT được khởi công tại Bắc Ninh với tổng vốn 1.000 tỷ đồng. "
            "Ông Nguyễn Văn A cho biết dự án sẽ hoàn thành trong năm 2027."
        ),
        "excerpt": "FPT khởi công nhà máy chip tại Bắc Ninh",
        "content_chars": 160,
    },
    {
        "id": 2,
        "title": "Nhà máy chip FPT: giai đoạn thử nghiệm",
        "url": "https://tuoitre.vn/fpt-chip",
        "domain": "tuoitre.vn",
        "published_at": "2026-11-01T05:00:00+00:00",
        "reliability": 0.85,
        "content": "FPT đang vận hành thử nghiệm nhà máy chip tại Bắc Ninh trước khi mở rộng.",
        "excerpt": "FPT vận hành thử nghiệm nhà máy chip",
        "content_chars": 80,
    },
]


def test_facts_without_matching_numbers_are_demoted() -> None:
    report = {
        "summary": "FPT khởi công nhà máy chip tại Bắc Ninh.",
        "key_facts": [
            {"fact": "Nhà máy có vốn đầu tư 1.000 tỷ đồng", "source_ids": [1]},
            {"fact": "Nhà máy sẽ tuyển 5.000 công nhân", "source_ids": [1]},
        ],
        "quotes": [],
        "timeline": [],
        "entities": {},
    }
    result = FactChecker().validate(report, SOURCES, "FPT nhà máy chip")

    verdicts = [fact["verdict"] for fact in result["report"]["key_facts"]]
    assert verdicts == [VERDICT_SINGLE, VERDICT_UNSUPPORTED]
    assert any("Số liệu chưa kiểm chứng" in issue for issue in result["issues"])
    assert result["report"]["key_facts"][1]["fact"] not in result["report"]["confirmed"]


def test_only_two_independent_domains_mark_a_fact_as_supported() -> None:
    report = {
        "summary": "Nhà máy chip FPT đặt tại Bắc Ninh.",
        "key_facts": [{"fact": "Nhà máy chip FPT đặt tại Bắc Ninh", "source_ids": [1, 2]}],
        "quotes": [],
        "timeline": [],
        "entities": {},
    }
    result = FactChecker().validate(report, SOURCES, "FPT nhà máy chip")
    assert result["report"]["key_facts"][0]["verdict"] == VERDICT_SUPPORTED
    assert result["report"]["corroboration"]["independent_domain_count"] == 2
    assert result["confidence"] >= 55


def test_single_source_research_is_capped_and_labelled() -> None:
    report = {
        "summary": "Chỉ một bài báo đưa tin.",
        "key_facts": [{"fact": "FPT khởi công nhà máy chip", "source_ids": [1]}],
        "quotes": [],
        "timeline": [],
        "entities": {},
    }
    result = FactChecker().validate(report, SOURCES[:1], "FPT nhà máy chip")
    assert result["confidence"] <= 55
    assert result["confidence_label"] in {"insufficient", "low"}
    assert result["report"]["key_facts"][0]["verdict"] == VERDICT_SINGLE


def test_quotes_must_appear_verbatim_in_a_cited_source() -> None:
    report = {
        "summary": "FPT khởi công nhà máy chip.",
        "key_facts": [],
        "quotes": [
            {
                "text": "Ông Nguyễn Văn A cho biết dự án sẽ hoàn thành trong năm 2027",
                "speaker": "Nguyễn Văn A",
                "source_id": 1,
            },
            {"text": "Chúng tôi sẽ dẫn đầu thị trường bán dẫn châu Á", "source_id": 1},
        ],
        "timeline": [],
        "entities": {},
    }
    result = FactChecker().validate(report, SOURCES, "FPT nhà máy chip")
    kept = [quote["text"] for quote in result["report"]["quotes"]]
    assert len(kept) == 1
    assert kept[0].startswith("Ông Nguyễn Văn A")
    assert any("Loại trích dẫn" in issue for issue in result["issues"])


def _cluster() -> dict:
    recent = (utcnow() - timedelta(hours=4)).isoformat()
    older = (utcnow() - timedelta(hours=10)).isoformat()
    return {
        "title": "FPT vận hành thử nghiệm nhà máy chip đầu tiên do người Việt làm chủ",
        "category": "technology",
        "traffic": 50_000,
        "articles": [
            {"url": "https://vnexpress.net/a", "domain": "vnexpress.net",
             "published_at": recent, "reliability": 0.9},
            {"url": "https://tuoitre.vn/b", "domain": "tuoitre.vn",
             "published_at": older, "reliability": 0.85},
        ],
    }


def test_score_is_bounded_and_metrics_are_labelled() -> None:
    result = TrendScorer().score(_cluster())
    assert 0 <= result["score"] <= 100
    assert result["metrics"]
    assert {m["kind"] for m in result["metrics"]} <= {"verified", "estimated", "editorial"}
    keys = {m["key"]: m for m in result["metrics"]}
    assert keys["source_diversity"]["kind"] == "verified"
    assert keys["source_diversity"]["value"] > 40
    assert "source_diversity" in result["verified_metrics"]


def test_engagement_is_never_invented() -> None:
    result = TrendScorer().score(_cluster())
    assert "engagement" not in {m["key"] for m in result["metrics"]}
    assert any("tương tác công khai" in caveat for caveat in result["caveats"])

    unverified = dict(_cluster(), engagement={"reactions": 900, "comments": 80, "verified": False})
    result2 = TrendScorer().score(unverified)
    assert "engagement" not in {m["key"] for m in result2["metrics"]}

    verified = dict(_cluster(), engagement={"reactions": 900, "comments": 80, "shares": 20,
                                           "verified": True})
    result3 = TrendScorer().score(verified)
    assert "engagement" in result3["verified_metrics"]


def test_same_story_merges_rewritten_headlines() -> None:
    assert same_story(
        "Tủ lạnh siêu thị khắp châu Âu tăng nhiệt lên 50°C vì bị tin tặc tấn công",
        "Hacker tăng nhiệt tủ lạnh đến 50 độ C tại hàng loạt siêu thị châu Âu",
    )
    assert same_story(
        "Chủ tịch Quốc hội Trần Thanh Mẫn chủ trì lễ đón Chủ tịch Quốc hội Lào",
        "Lễ đón Chủ tịch Quốc hội Lào và Phu nhân thăm chính thức Việt Nam",
    )


def test_same_story_keeps_distinct_stories_apart() -> None:
    assert not same_story(
        "Giá vàng ngày 3/11: Vàng miếng SJC tăng mạnh",
        "Nhà máy giày hơn 1.600 công nhân ở TP HCM giải thể",
    )
    assert not same_story(
        "Giá vàng ngày 3/11: Vàng miếng SJC tăng mạnh",
        "Giá vàng hôm nay: SJC tăng 500.000 đồng mỗi lượng",
    )


def test_editorial_judgement_is_labelled_editorial() -> None:
    result = TrendScorer().score(
        _cluster(),
        editorial={"audience_fit": 90, "uniqueness": 88, "reason": "Chủ đề công nghệ Việt Nam"},
    )
    metrics = {m["key"]: m for m in result["metrics"]}
    assert metrics["audience_fit"]["kind"] == "editorial"
    assert metrics["audience_fit"]["value"] == 90
    assert "Chủ đề công nghệ Việt Nam" in result["rationale"]
