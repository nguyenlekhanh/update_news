"""Content quality report for a drafted post.

Every number reported here is either measured from stored data or explicitly
labelled as a heuristic / AI assessment. Components that cannot be evaluated
(such as image relevance before a vision check) are reported as unavailable
instead of being filled with a guess.
"""

from __future__ import annotations

import base64
from pathlib import Path

from .config import PROJECT_ROOT, settings
from .utils import age_hours, parse_datetime, utcnow, utcnow_iso

VERDICT_READY = "ready"
VERDICT_REVIEW = "review"
VERDICT_WEAK = "weak"

FACT_VERDICTS_UNSUPPORTED = {"unsupported"}
FRESHNESS_HALF_LIFE_HOURS = 24.0

WEIGHTS = {
    "source_reliability": 0.20,
    "fact_check": 0.25,
    "content": 0.25,
    "image": 0.15,
    "freshness": 0.15,
}

RELEVANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance": {"type": "integer"},
        "matches_story": {"type": "boolean"},
        "note": {"type": "string"},
        "concerns": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["relevance", "note"],
}


def _component(score, kind: str, detail: str, **extra) -> dict:
    return {"score": None if score is None else round(float(score), 1),
            "kind": kind, "detail": detail, **extra}


def _sources(research: dict | None) -> list[dict]:
    data = (research or {}).get("data") or {}
    return [s for s in (data.get("sources") or []) if isinstance(s, dict)]


def _source_reliability(research: dict | None) -> dict:
    sources = _sources(research)
    values = [float(s["reliability"]) for s in sources if s.get("reliability") is not None]
    if not values:
        return _component(None, "unavailable", "Không có dữ liệu độ tin cậy nguồn")
    average = sum(values) / len(values) * 100
    domains = sorted({s.get("domain") for s in sources if s.get("domain")})
    label = "high" if average >= 80 else "medium" if average >= 60 else "low"
    return _component(
        average, "verified",
        f"{len(values)} nguồn · trung bình {average:.0f}/100 ({label})",
        label=label, domains=domains, items=[
            {"domain": s.get("domain"), "reliability": round(float(s.get("reliability") or 0) * 100, 1)}
            for s in sources if s.get("reliability") is not None
        ],
    )


def _fact_check(research: dict | None, flags: list[dict]) -> dict:
    data = (research or {}).get("data") or {}
    confidence = data.get("confidence")
    facts = data.get("key_facts") or []
    unsupported = [
        f for f in facts
        if isinstance(f, dict) and str(f.get("verdict") or "").lower() in FACT_VERDICTS_UNSUPPORTED
    ]
    unconfirmed = [
        f for f in facts
        if isinstance(f, dict) and str(f.get("verdict") or "").lower() in {"single_source", "unverified"}
    ]
    conflicts = data.get("conflicting") or []
    warnings = [f for f in flags if str(f.get("severity")) == "warning"]

    if confidence is None and not facts:
        base = None
    else:
        base = float(confidence or 0)
        base -= 8 * len(unsupported)
        base -= 4 * len(warnings)
        base -= 5 * len(conflicts)
        base = max(0.0, min(100.0, base))
    detail_parts = []
    if confidence is not None:
        detail_parts.append(f"Độ tin cậy nghiên cứu {float(confidence):.0f}%")
    detail_parts.append(f"{len(unsupported)} tuyên bố không được nguồn ủng hộ")
    detail_parts.append(f"{len(unconfirmed)} thông tin một nguồn/chưa xác nhận")
    if warnings:
        detail_parts.append(f"{len(warnings)} cảnh báo trên bài viết")
    return _component(
        base, "verified" if confidence is not None else "unavailable",
        " · ".join(detail_parts),
        confidence=confidence,
        unsupported_claims=[
            {"claim": f.get("fact") or f.get("claim"), "notes": f.get("notes") or ""}
            for f in unsupported
        ],
        unconfirmed_claims=[f.get("fact") for f in unconfirmed if f.get("fact")],
        conflicts=conflicts,
        warnings=[w.get("detail") for w in warnings if w.get("detail")],
    )


def _content_quality(post: dict, flags: list[dict]) -> dict:
    body = (post.get("main_post") or "").strip()
    words = len(body.split())
    checks: list[dict] = []

    def check(key: str, earned: float, maximum: float, note: str) -> None:
        checks.append({"key": key, "earned": round(earned, 1), "max": maximum, "note": note})

    length_score = 25.0 if words >= 150 else 18.0 if words >= 100 else 10.0 if words >= 60 else 3.0
    check("length", length_score, 25, f"{words} từ")

    paragraphs = [p for p in body.split("\n\n") if p.strip()]
    check("structure", 15.0 if len(paragraphs) >= 3 else 10.0 if len(paragraphs) == 2 else 4.0,
          15, f"{len(paragraphs)} đoạn")

    check("hook", 10.0 if (post.get("hook") or "").strip() else 0.0, 10,
          "Có câu mở đầu" if post.get("hook") else "Thiếu câu mở đầu")
    check("closing", 10.0 if (post.get("closing_question") or "").strip().endswith("?") else 0.0,
          10, "Có câu hỏi kết bài" if (post.get("closing_question") or "").strip().endswith("?")
          else "Thiếu câu hỏi kết bài")

    hashtags = post.get("hashtags") or []
    hashtag_score = 10.0 if 3 <= len(hashtags) <= 8 else 5.0 if hashtags else 0.0
    check("hashtags", hashtag_score, 10, f"{len(hashtags)} hashtag")

    check("sources", 10.0 if "@" not in body and "http" in body else 5.0 if post.get("sources") else 0.0,
          10, "Có khối nguồn tham khảo" if "http" in body else "Chưa chèn nguồn tham khảo")

    flag_types = {f.get("type") for f in flags}
    check("copy", 10.0 if "copied_text" not in flag_types else 2.0, 10,
          "Không phát hiện câu sao chép" if "copied_text" not in flag_types else "Có câu gần nguyên văn nguồn")
    check("numbers", 10.0 if "unverified_number" not in flag_types else 3.0, 10,
          "Mọi số liệu đều có trong nghiên cứu" if "unverified_number" not in flag_types
          else "Có số liệu chưa kiểm chứng")

    earned = sum(c["earned"] for c in checks)
    maximum = sum(c["max"] for c in checks)
    score = earned / maximum * 100 if maximum else 0.0
    return _component(score, "heuristic", f"{earned:.0f}/{maximum:.0f} điểm cấu trúc – nội dung",
                      checks=checks)


def _image_relevance(post: dict) -> dict:
    images = post.get("images") or []
    selected = next((img for img in images if img.get("selected")), images[0] if images else None)
    if not images:
        return _component(None, "unavailable", "Chưa có ảnh minh họa")
    relevance = ((selected or {}).get("meta") or {}).get("relevance") if selected else None
    if isinstance(relevance, dict) and relevance.get("score") is not None:
        return _component(
            relevance["score"], "ai_assessed",
            f"{relevance.get('model', 'vision model')}: {relevance.get('note', '')}".strip(),
            image_id=(selected or {}).get("id"),
            matches_story=relevance.get("matches_story"),
            concerns=relevance.get("concerns") or [],
            assessed_at=relevance.get("assessed_at"),
        )
    prompt = (post.get("image_prompt") or "").strip()
    prompt_words = len(prompt.split())
    score = min(100.0, prompt_words * 2.2) if prompt else 0.0
    return _component(
        score, "heuristic",
        "Chưa đánh giá bằng vision model; điểm tạm tính theo độ chi tiết của prompt",
        image_id=(selected or {}).get("id"), assessed=False,
    )


def _freshness(post: dict, research: dict | None) -> dict:
    dates = []
    for source in _sources(research):
        parsed = parse_datetime(source.get("published_at"))
        if parsed:
            dates.append(parsed)
    topic = post.get("topic") or {}
    for key in ("last_seen_at", "updated_at", "created_at"):
        parsed = parse_datetime(topic.get(key)) if topic.get(key) else None
        if parsed:
            dates.append(parsed)
            break
    if not dates:
        return _component(None, "unavailable", "Không có mốc thời gian để đo độ mới")
    newest = max(dates)
    hours = age_hours(newest) or 0.0
    import math

    score = 100 * math.exp(-math.log(2) * hours / FRESHNESS_HALF_LIFE_HOURS)
    label = "fresh" if hours <= 12 else "recent" if hours <= 36 else "stale"
    return _component(score, "verified", f"Nguồn mới nhất {hours:.1f} giờ trước",
                      hours=round(hours, 1), label=label,
                      captured_at=newest.isoformat())


def content_score(post: dict) -> dict:
    """Lightweight content-only score used for generated versions."""
    component = _content_quality(post, post.get("ai_flags") or [])
    return {
        "score": component["score"],
        "kind": component["kind"],
        "checks": component.get("checks") or [],
        "words": len((post.get("main_post") or "").split()),
    }


def evaluate(post: dict) -> dict:
    """Full quality report for a post (post must include topic/research/images)."""
    research = post.get("research") or ((post.get("topic") or {}).get("research"))
    flags = post.get("ai_flags") or []
    components = {
        "source_reliability": _source_reliability(research),
        "fact_check": _fact_check(research, flags),
        "content": _content_quality(post, flags),
        "image": _image_relevance(post),
        "freshness": _freshness(post, research),
    }
    available = {k: v for k, v in components.items() if v["score"] is not None}
    weight_total = sum(WEIGHTS[k] for k in available)
    overall = (
        sum(available[k]["score"] * WEIGHTS[k] for k in available) / weight_total
        if weight_total else None
    )
    coverage = round(weight_total / sum(WEIGHTS.values()), 2)

    fact = components["fact_check"]
    reasons: list[str] = []
    if fact.get("unsupported_claims"):
        reasons.append(f"{len(fact['unsupported_claims'])} tuyên bố chưa được nguồn ủng hộ")
    if fact.get("warnings"):
        reasons.append(f"{len(fact['warnings'])} cảnh báo AI cần người duyệt xem")
    confidence = fact.get("confidence")
    if confidence is not None and float(confidence) < 45:
        reasons.append(f"Độ tin cậy nghiên cứu thấp ({float(confidence):.0f}%)")
    if components["image"]["kind"] == "unavailable":
        reasons.append("Chưa có ảnh minh họa")
    if coverage < 0.6:
        reasons.append("Thiếu dữ liệu để đánh giá đầy đủ")

    if any(k in fact for k in ("unsupported_claims",)) and fact.get("unsupported_claims"):
        verdict = VERDICT_WEAK
    elif reasons:
        verdict = VERDICT_REVIEW
    elif overall is not None and overall >= 70:
        verdict = VERDICT_READY
    else:
        verdict = VERDICT_REVIEW
    if overall is not None and overall < 45:
        verdict = VERDICT_WEAK

    return {
        "overall": round(overall, 1) if overall is not None else None,
        "verdict": verdict,
        "verdict_reasons": reasons,
        "coverage": coverage,
        "components": components,
        "weights": WEIGHTS,
        "unsupported_claims": fact.get("unsupported_claims") or [],
        "generated_at": utcnow_iso(),
    }


# ------------------------------------------------------------------ image check
# Some Ollama builds list models they cannot actually load (e.g. mllama).
# Try the smaller VL families first and keep the configured model at the front.
VISION_PREFERENCE = ("qwen3-vl", "qwen2.5vl", "qwen2.5-vl", "llava", "llama3.2-vision")


def vision_model_candidates(health: dict | None = None,
                            preferred: str | None = None) -> list[str]:
    """Installed vision models ordered by the configured choice, then by preference."""
    models = (health or {}).get("models") or []
    names = [m.get("name") for m in models
             if "vision" in (m.get("capabilities") or []) and m.get("name")]
    ordered: list[str] = []
    if preferred:
        if preferred in names:
            ordered.append(preferred)
        else:
            base = preferred.split(":")[0]
            ordered.extend(name for name in names if name.split(":")[0] == base)
    for family in VISION_PREFERENCE:
        ordered.extend(name for name in names
                       if family in name.lower() and name not in ordered)
    ordered.extend(name for name in names if name not in ordered)
    return ordered


def pick_vision_model(health: dict | None = None,
                      preferred: str | None = None) -> str | None:
    """Choose an installed vision-capable model, preferring the configured one."""
    candidates = vision_model_candidates(health, preferred)
    return candidates[0] if candidates else None


def _encode_image(path: str) -> bytes:
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = PROJECT_ROOT / file_path
    return base64.b64encode(file_path.read_bytes())


def assess_image_relevance(image_path: str, headline: str, context: str = "",
                           client=None) -> dict:
    """Ask the local vision model how relevant an illustration is to the story."""
    from .ollama_client import OllamaError, ollama as default_client

    client = client or default_client
    configured = settings.get("quality.vision_model", "") or None
    candidates = vision_model_candidates(client.health(), configured)
    if not candidates:
        raise OllamaError(
            "Không tìm thấy model vision đã cài (ví dụ qwen2.5vl:7b). "
            "Hãy cài model vision trong Ollama để đánh giá ảnh."
        )
    model = candidates[0]
    prompt = (
        f"Tiêu đề tin: {headline}\n"
        f"Tóm tắt: {context[:600]}\n\n"
        "Ảnh trên là minh họa biên tập do AI tạo cho bài viết này. "
        "Hãy đánh giá mức độ liên quan của ảnh với nội dung (0-100). "
        "Chỉ trả về JSON: {\"relevance\": <số>, \"matches_story\": <true|false>, "
        "\"note\": \"1 câu tiếng Việt\", \"concerns\": [\"...\"]}. "
        "Nếu ảnh có chữ, logo, người thật hoặc gợi ý ảnh tư liệu thì nêu trong concerns."
    )
    image_b64 = _encode_image(image_path).decode("ascii")
    from .ollama_client import _extract_json

    errors: list[str] = []
    data = None
    for candidate in candidates:
        try:
            result = client.vision_json(
                prompt,
                images=[image_b64],
                model=candidate,
                json_schema=RELEVANCE_SCHEMA,
                temperature=0.1,
                num_predict=350,
            )
            parsed = _extract_json(result.text)
            if isinstance(parsed, dict) and parsed.get("relevance") is not None:
                data, model = parsed, candidate
                break
            errors.append(f"{candidate}: phản hồi không hợp lệ")
        except OllamaError as exc:
            errors.append(f"{candidate}: {str(exc)[:140]}")
            continue
    if data is None:
        raise OllamaError(
            "Không model vision nào đánh giá được ảnh. " + " | ".join(errors[-2:])
        )
    try:
        score = max(0.0, min(100.0, float(data.get("relevance"))))
    except (TypeError, ValueError):
        raise OllamaError("Vision model trả về điểm không hợp lệ") from None
    return {
        "score": round(score, 1),
        "matches_story": bool(data.get("matches_story")),
        "note": str(data.get("note") or "").strip()[:300],
        "concerns": [str(c)[:160] for c in (data.get("concerns") or [])][:5],
        "model": model,
        "assessed_at": utcnow_iso(),
        "headline": headline,
    }
