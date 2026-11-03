"""Editorial strategy engine.

Recommends how a story should be written: breaking-news alert, tech explainer,
business-impact piece, lifestyle story, balanced piece on a contested topic, or a
straight news post. The recommendation is rule-based over signals the pipeline
already measured (category, lifecycle phase, source spread, research conflicts),
so it can be explained and overridden — no hidden model guessing.

The user's override is stored in ``topic_strategy`` and wins over the rule.
"""

from __future__ import annotations

from typing import Iterable

from . import viral

STRATEGY_BREAKING = "breaking_news"
STRATEGY_TECH = "tech_explainer"
STRATEGY_BUSINESS = "business_impact"
STRATEGY_LIFESTYLE = "lifestyle_story"
STRATEGY_SENSITIVE = "sensitive_balanced"
STRATEGY_CULTURE = "culture_explainer"
STRATEGY_GENERAL = "general_news"

STRATEGY_ORDER = (
    STRATEGY_BREAKING, STRATEGY_TECH, STRATEGY_BUSINESS, STRATEGY_LIFESTYLE,
    STRATEGY_SENSITIVE, STRATEGY_CULTURE, STRATEGY_GENERAL,
)

BREAKING_FRESHNESS_HOURS = 12.0
SENSITIVE_CONFLICTS = 1
SENSITIVE_UNCONFIRMED = 2

STRATEGIES: dict[str, dict] = {
    STRATEGY_BREAKING: {
        "label_vi": "Tin nóng – đưa tin nhanh",
        "label_en": "Breaking news – fast and factual",
        "style": "professional_journalism",
        "tone": "serious",
        "length": "short",
        "audience": "general",
        "image_style": "editorial_illustration",
        "summary_vi": (
            "Sự việc còn rất mới: ưu tiên đăng sớm, tiêu đề rõ, chỉ nêu dữ kiện đã "
            "kiểm chứng và nói rõ phần nào chưa xác nhận."
        ),
        "key_points": [
            "Mở bằng thông tin quan trọng nhất, không dẫn dắt dài dòng",
            "Nêu mốc thời gian và địa điểm đã xác nhận",
            "Ghi rõ nguồn cho từng con số",
        ],
        "avoid": [
            "Số liệu chưa có trong báo cáo nghiên cứu",
            "Suy đoán nguyên nhân khi nguồn chưa xác nhận",
            "Tiêu đề giật gân hoặc viết hoa toàn bộ",
        ],
        "checklist": [
            "Tiêu đề nêu đúng sự việc chính",
            "Có ít nhất một nguồn độc lập",
            "Câu hỏi kết bài mời thảo luận, không kích động",
        ],
    },
    STRATEGY_TECH: {
        "label_vi": "Công nghệ – giải thích dễ hiểu",
        "label_en": "Technology – explain it simply",
        "style": "informative",
        "tone": "neutral",
        "length": "medium",
        "audience": "tech",
        "image_style": "conceptual",
        "summary_vi": (
            "Giải thích công nghệ bằng ngôn ngữ phổ thông, nêu hệ quả thực tế cho "
            "người dùng Việt Nam và tránh hứa hẹn tính năng chưa có."
        ),
        "key_points": [
            "Công nghệ này giải quyết vấn đề gì",
            "Ai được hưởng lợi, ai chịu ảnh hưởng",
            "Điều kiện áp dụng thực tế tại Việt Nam (nếu nguồn có nêu)",
        ],
        "avoid": [
            "Dự đoán tương lai như thể đã xảy ra",
            "So sánh với sản phẩm khác khi nguồn không nêu",
            "Thuật ngữ không giải thích",
        ],
        "checklist": [
            "Có ít nhất một ví dụ hoặc hệ quả cụ thể",
            "Không có số liệu ngoài báo cáo",
            "Giải thích được thuật ngữ chính trong một câu",
        ],
    },
    STRATEGY_BUSINESS: {
        "label_vi": "Kinh tế – tác động thị trường",
        "label_en": "Business – market impact",
        "style": "analytical",
        "tone": "neutral",
        "length": "medium",
        "audience": "business",
        "image_style": "infographic",
        "summary_vi": (
            "Tập trung vào tác động kinh tế đã được nguồn nêu, giải thích con số và "
            "tuyệt đối không đưa ra khuyến nghị đầu tư."
        ),
        "key_points": [
            "Con số và bối cảnh thị trường có trong nguồn",
            "Ai bị ảnh hưởng: doanh nghiệp, người tiêu dùng, nhà đầu tư",
            "Đâu là dữ kiện, đâu là nhận định của chuyên gia",
        ],
        "avoid": [
            "Lời khuyên mua/bán, dự báo giá",
            "Nhân lên lợi nhuận hoặc mức tăng trưởng",
            "Gán nhận định cho chuyên gia khi nguồn không trích dẫn",
        ],
        "checklist": [
            "Mọi con số khớp với báo cáo nghiên cứu",
            "Có câu nêu rõ đây không phải lời khuyên đầu tư",
            "Không suy diễn giá trị cổ phiếu",
        ],
    },
    STRATEGY_LIFESTYLE: {
        "label_vi": "Đời sống – kể chuyện gần gũi",
        "label_en": "Lifestyle – human storytelling",
        "style": "friendly_storytelling",
        "tone": "warm",
        "length": "medium",
        "audience": "general",
        "image_style": "photorealistic_generic",
        "summary_vi": (
            "Kể bằng tình huống cụ thể và ví dụ gần gũi, giữ nguyên dữ kiện, không "
            "thêm tình tiết cảm động không có trong nguồn."
        ),
        "key_points": [
            "Mở bằng một hoàn cảnh cụ thể có thật trong nguồn",
            "Liên hệ tới trải nghiệm quen thuộc của người đọc",
            "Kết bằng câu hỏi mời chia sẻ",
        ],
        "avoid": [
            "Thêm lời thoại hoặc cảm xúc không có trong nguồn",
            "Kịch tính hóa nỗi đau của người khác",
            "Ảnh chân dung người thật",
        ],
        "checklist": [
            "Không có chi tiết hư cấu",
            "Giọng ấm, không phán xét",
            "Có câu hỏi kết bài tự nhiên",
        ],
    },
    STRATEGY_SENSITIVE: {
        "label_vi": "Chủ đề nhạy cảm – đa góc nhìn",
        "label_en": "Sensitive – multiple perspectives",
        "style": "analytical",
        "tone": "serious",
        "length": "long",
        "audience": "general",
        "image_style": "editorial_illustration",
        "summary_vi": (
            "Chủ đề có thông tin xung đột hoặc dễ gây tranh cãi: trình bày nhiều góc "
            "nhìn, tách rõ dữ kiện – ý kiến – thông tin chưa xác nhận, không kích động."
        ),
        "key_points": [
            "Nêu phần đã xác nhận trước",
            "Trình bày các quan điểm khác nhau mà nguồn ghi nhận",
            "Nói rõ điều gì còn chưa xác nhận",
        ],
        "avoid": [
            "Quy kết động cơ hoặc tội lỗi cho cá nhân",
            "Ngôn từ kích động, miệt thị",
            "Biến tin đồn thành tiêu đề khẳng định",
        ],
        "checklist": [
            "Có câu tách bạch dữ kiện và ý kiến",
            "Không có kết luận vượt quá nguồn",
            "Có ghi chú về thông tin chưa xác nhận",
        ],
    },
    STRATEGY_CULTURE: {
        "label_vi": "Văn hóa mạng – giải thích xu hướng",
        "label_en": "Internet culture – explain the trend",
        "style": "conversational",
        "tone": "engaging",
        "length": "medium",
        "audience": "young",
        "image_style": "social_graphic",
        "summary_vi": (
            "Giải thích vì sao câu chuyện này lan nhanh, ai đang tham gia và ý nghĩa "
            "thực sự phía sau, tránh cổ xúy nội dung độc hại."
        ),
        "key_points": [
            "Xu hướng bắt đầu từ đâu (nếu nguồn nêu)",
            "Điều gì khiến nó được chia sẻ",
            "Ảnh hưởng tích cực và tiêu cực",
        ],
        "avoid": [
            "Cổ xúy hoặc hướng dẫn nội dung gây hại",
            "Gán nhãn tiêu cực cho nhóm người",
            "Bịa số liệu tương tác",
        ],
        "checklist": [
            "Không có số liệu tương tác bịa đặt",
            "Giọng vui nhưng tôn trọng",
            "Có góc nhìn phê bình nhẹ nhàng",
        ],
    },
    STRATEGY_GENERAL: {
        "label_vi": "Tin tổng hợp – cân bằng",
        "label_en": "General news – balanced",
        "style": "professional_journalism",
        "tone": "neutral",
        "length": "medium",
        "audience": "general",
        "image_style": "editorial_illustration",
        "summary_vi": (
            "Đưa tin cân bằng theo cấu trúc thông tin – bối cảnh – hệ quả, chỉ dùng "
            "dữ kiện đã có trong báo cáo nghiên cứu."
        ),
        "key_points": [
            "Thông tin chính trong đoạn đầu",
            "Bối cảnh ngắn gọn",
            "Hệ quả hoặc bước tiếp theo đã được nêu",
        ],
        "avoid": [
            "Thêm dữ kiện ngoài báo cáo",
            "Tiêu đề chung chung không có thông tin",
        ],
        "checklist": [
            "Tiêu đề có thông tin cụ thể",
            "Có nguồn tham khảo",
            "Không có tuyên bố chưa kiểm chứng",
        ],
    },
}

CATEGORY_MAP = {
    "ai": STRATEGY_TECH,
    "technology": STRATEGY_TECH,
    "science": STRATEGY_TECH,
    "business": STRATEGY_BUSINESS,
    "finance": STRATEGY_BUSINESS,
    "lifestyle": STRATEGY_LIFESTYLE,
    "human_interest": STRATEGY_LIFESTYLE,
    "education": STRATEGY_LIFESTYLE,
    "entertainment": STRATEGY_LIFESTYLE,
    "celebrity": STRATEGY_LIFESTYLE,
    "internet_culture": STRATEGY_CULTURE,
    "discovery": STRATEGY_CULTURE,
    "social": STRATEGY_SENSITIVE,
}


def _research_signals(research: dict | None) -> dict:
    data = (research or {}).get("data") or {}
    facts = [f for f in (data.get("key_facts") or []) if isinstance(f, dict)]
    unsupported = [
        f for f in facts
        if str(f.get("verdict") or "").lower() == "unsupported"
    ]
    single_source = [
        f for f in facts
        if str(f.get("verdict") or "").lower() in {"single_source", "unverified"}
    ]
    return {
        "conflicts": [c for c in (data.get("conflicting") or []) if c],
        "unconfirmed": [u for u in (data.get("unconfirmed") or []) if u],
        "unsupported": unsupported,
        "single_source": single_source,
        "confidence": data.get("confidence"),
        "has_research": bool((research or {}).get("status") == "complete"),
    }


def _signals(topic: dict, research: dict | None, intelligence: dict | None) -> dict:
    research_info = _research_signals(research)
    intelligence = intelligence or {}
    viral_info = intelligence.get("viral") or {}
    return {
        "category": (topic.get("category") or "general"),
        "phase": intelligence.get("phase") or "unknown",
        "freshness_hours": intelligence.get("freshness_hours"),
        "independent_domains": intelligence.get("independent_domains"),
        "viral_label": viral_info.get("label"),
        "score": float(topic.get("score") or 0),
        **research_info,
    }


def choose(signals: dict) -> tuple[str, list[str]]:
    """Pick a strategy key and the reasons that led to it."""
    reasons: list[str] = []
    category = signals["category"]
    phase = signals["phase"]
    freshness = signals["freshness_hours"]
    conflicts = signals["conflicts"]
    unconfirmed = signals["unconfirmed"]

    if len(conflicts) >= SENSITIVE_CONFLICTS or len(unconfirmed) >= SENSITIVE_UNCONFIRMED:
        reasons.append(
            f"Có {len(conflicts)} điểm xung đột và {len(unconfirmed)} thông tin chưa xác nhận"
        )
        return STRATEGY_SENSITIVE, reasons

    if (
        freshness is not None
        and freshness <= BREAKING_FRESHNESS_HOURS
        and phase in (viral.PHASE_EMERGING, viral.PHASE_RISING, viral.PHASE_PEAK)
        and category in ("general", "vietnam", "international")
    ):
        reasons.append(
            f"Tin mới {freshness:.1f} giờ và đang ở pha {phase}"
        )
        return STRATEGY_BREAKING, reasons

    if category in CATEGORY_MAP:
        mapped = CATEGORY_MAP[category]
        reasons.append(f"Nhóm nội dung '{category}' phù hợp chiến lược {mapped}")
        if mapped == STRATEGY_SENSITIVE:
            reasons.append("Nhóm xã hội mặc định trình bày đa góc nhìn")
        return mapped, reasons

    reasons.append("Không có tín hiệu đặc biệt, dùng chiến lược tin tổng hợp")
    return STRATEGY_GENERAL, reasons


def recommend(topic: dict, research: dict | None = None,
              intelligence: dict | None = None) -> dict:
    """Build the recommendation object shown in the dashboard."""
    signals = _signals(topic, research, intelligence)
    key, reasons = choose(signals)
    definition = STRATEGIES[key]
    confidence = (
        "high" if signals["has_research"] and (signals["confidence"] or 0) >= 70
        else "medium" if signals["has_research"]
        else "low"
    )
    return {
        "key": key,
        "label_vi": definition["label_vi"],
        "label_en": definition["label_en"],
        "style": definition["style"],
        "tone": definition["tone"],
        "length": definition["length"],
        "audience": definition["audience"],
        "image_style": definition["image_style"],
        "summary": definition["summary_vi"],
        "key_points": definition["key_points"],
        "avoid": definition["avoid"],
        "checklist": definition["checklist"],
        "reasons": reasons,
        "signals": [
            {"key": "category", "value": signals["category"], "kind": "verified"},
            {"key": "phase", "value": signals["phase"], "kind": "measured"},
            {"key": "freshness_hours", "value": signals["freshness_hours"], "kind": "measured"},
            {"key": "independent_domains", "value": signals["independent_domains"],
             "kind": "verified"},
            {"key": "conflicts", "value": len(signals["conflicts"]), "kind": "verified"},
            {"key": "unconfirmed", "value": len(signals["unconfirmed"]), "kind": "verified"},
        ],
        "confidence": confidence,
        "engine": "rule_based",
        "disclaimer": (
            "Chiến lược do hệ thống gợi ý từ dữ kiện đã lưu (nhóm nội dung, pha xu hướng, "
            "xung đột trong nghiên cứu). Bạn có thể đổi chiến lược bất cứ lúc nào."
        ),
    }


def overridden(recommendation: dict, override_key: str | None,
               note: str = "") -> dict:
    """Apply a user's override on top of the recommendation."""
    result = dict(recommendation)
    if not override_key or override_key == recommendation.get("key"):
        result["effective_key"] = recommendation.get("key")
        result["is_override"] = False
        return result
    definition = STRATEGIES.get(override_key)
    if not definition:
        result["effective_key"] = recommendation.get("key")
        result["is_override"] = False
        return result
    result.update(
        {
            "key": override_key,
            "label_vi": definition["label_vi"],
            "label_en": definition["label_en"],
            "style": definition["style"],
            "tone": definition["tone"],
            "length": definition["length"],
            "audience": definition["audience"],
            "image_style": definition["image_style"],
            "summary": definition["summary_vi"],
            "key_points": definition["key_points"],
            "avoid": definition["avoid"],
            "checklist": definition["checklist"],
            "effective_key": override_key,
            "is_override": True,
            "override_note": note,
            "recommended_key": recommendation.get("key"),
            "recommended_label_vi": recommendation.get("label_vi"),
        }
    )
    return result


def apply_options(strategy: dict, options: dict | None = None) -> dict:
    """Merge strategy choices into writer options without overriding explicit ones."""
    merged = dict(options or {})
    mapping = {
        "style": strategy.get("style"),
        "tone": strategy.get("tone"),
        "length": strategy.get("length"),
        "audience": strategy.get("audience"),
    }
    for key, value in mapping.items():
        if value and not merged.get(key):
            merged[key] = value
    merged.setdefault("strategy_key", strategy.get("effective_key") or strategy.get("key"))
    return merged


def prompt_block(strategy_key: str | None) -> str:
    """Guidance injected into the writer prompt for the chosen strategy."""
    definition = STRATEGIES.get(strategy_key or "")
    if not definition:
        return ""
    lines = [f"CHIẾN LƯỢC BIÊN TẬP: {definition['label_vi']} — {definition['summary_vi']}"]
    lines.append("Trọng tâm: " + "; ".join(definition["key_points"]))
    lines.append("Tránh: " + "; ".join(definition["avoid"]))
    return "\n".join(lines)


def options() -> list[dict]:
    """Selectable strategies for the dashboard."""
    return [
        {
            "key": key,
            "label_vi": STRATEGIES[key]["label_vi"],
            "label_en": STRATEGIES[key]["label_en"],
            "style": STRATEGIES[key]["style"],
            "image_style": STRATEGIES[key]["image_style"],
        }
        for key in STRATEGY_ORDER
    ]


def resolve(topic: dict, research: dict | None = None,
            intelligence: dict | None = None,
            stored: dict | None = None) -> dict:
    """Recommendation + stored override in one call."""
    base = recommend(topic, research, intelligence)
    stored = stored or {}
    return overridden(base, stored.get("override"), stored.get("note") or "")
