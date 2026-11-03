"""Trend prediction: momentum, confidence, editorial opportunity and forecast.

This module answers "where is this topic going?" using the score snapshots that
discovery stores in ``topic_history``. Everything that is a measurement keeps the
``measured`` kind; anything projected forward is labelled ``estimated`` and
carries a disclaimer. With too little history the honest answer is "unknown",
so the functions return that instead of inventing a direction.
"""

from __future__ import annotations

import math
from datetime import datetime
from statistics import median
from typing import Iterable

from . import viral
from .utils import age_hours, parse_datetime, utcnow

MOMENTUM_ACCELERATING = "accelerating"
MOMENTUM_RISING = "rising"
MOMENTUM_STEADY = "steady"
MOMENTUM_FADING = "fading"
MOMENTUM_UNKNOWN = "unknown"

MOMENTUMS = (
    MOMENTUM_ACCELERATING, MOMENTUM_RISING, MOMENTUM_STEADY,
    MOMENTUM_FADING, MOMENTUM_UNKNOWN,
)

CONFIDENCE_LABELS = ("low", "medium", "high")

# Slope thresholds expressed as score points per 6 hours.
ACCELERATE_PER_6H = 2.0
RISE_PER_6H = 0.6
FADE_PER_6H = -0.6
DECLINE_PER_6H = -2.0

FORECAST_DAMPING = 0.6
MIN_FORECAST_POINTS = 3
MIN_FORECAST_CONFIDENCE = 40.0

DISCLAIMER = (
    "Momentum, confidence, editorial opportunity và dự báo được tính từ lịch sử "
    "điểm của chính chủ đề này; đây là ước tính, không phải số liệu đo được từ Facebook."
)


def _points(history: Iterable[dict], now: datetime | None = None) -> list[dict]:
    now = now or utcnow()
    points: list[dict] = []
    for row in history or []:
        if row.get("score") is None:
            continue
        captured = parse_datetime(row.get("captured_at"))
        if not captured:
            continue
        points.append(
            {
                "score": float(row["score"]),
                "captured_at": row.get("captured_at"),
                "hours_ago": max(0.0, (now - captured).total_seconds() / 3600.0),
                "source_count": row.get("source_count"),
                "independent_domains": row.get("independent_domains"),
                "phase": row.get("phase"),
            }
        )
    points.sort(key=lambda point: -point["hours_ago"])
    return points


def _slope(points: list[dict]) -> tuple[float | None, float]:
    """Least-squares slope in score points per hour, plus R².

    x is hours before "now", negated so that time runs forward.
    """
    if len(points) < 2:
        return None, 0.0
    xs = [-point["hours_ago"] for point in points]
    ys = [point["score"] for point in points]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    variance = sum((x - mean_x) ** 2 for x in xs)
    if variance <= 0:
        return None, 0.0
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = covariance / variance
    predicted = [mean_y + slope * (x - mean_x) for x in xs]
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, predicted))
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    r2 = 0.0 if ss_tot <= 0 else max(0.0, min(1.0, 1 - ss_res / ss_tot))
    return slope, r2


def _span_hours(points: list[dict]) -> float:
    if len(points) < 2:
        return 0.0
    return round(max(point["hours_ago"] for point in points)
                 - min(point["hours_ago"] for point in points), 2)


def momentum(history: Iterable[dict], now: datetime | None = None) -> dict:
    """Measured direction of the score across discovery runs."""
    points = _points(history, now)
    if len(points) < 2:
        return {
            "label": MOMENTUM_UNKNOWN,
            "value": None,
            "per_6h": None,
            "per_run": None,
            "kind": "unavailable",
            "snapshots": len(points),
            "span_hours": _span_hours(points),
            "reason": "Cần ít nhất hai lần khám phá để đo momentum.",
        }

    slope, r2 = _slope(points)
    per_run = round(points[-1]["score"] - points[-2]["score"], 1)
    span = _span_hours(points)

    # Two runs minutes apart would turn a tiny delta into a huge per-hour slope.
    # In that case report the per-run change only, and say so.
    if span < 1.0:
        if per_run >= 1.5:
            label, reason = MOMENTUM_RISING, (
                f"Hai lần ghi nhận cách nhau dưới 1 giờ; điểm tăng {per_run:+.1f} mỗi lần chạy."
            )
        elif per_run <= -1.5:
            label, reason = MOMENTUM_FADING, (
                f"Hai lần ghi nhận cách nhau dưới 1 giờ; điểm giảm {per_run:+.1f} mỗi lần chạy."
            )
        else:
            label, reason = MOMENTUM_STEADY, (
                f"Hai lần ghi nhận cách nhau dưới 1 giờ; điểm gần như không đổi ({per_run:+.1f})."
            )
        return {
            "label": label,
            "value": None,
            "per_6h": None,
            "per_run": per_run,
            "kind": "measured",
            "snapshots": len(points),
            "span_hours": round(span, 2),
            "window": "short",
            "fit": None,
            "reason": reason,
        }

    per_6h = round(max(-60.0, min(60.0, (slope or 0.0) * 6)), 2)

    half = max(1, len(points) // 2)
    early_slope, _ = _slope(points[: half + 1]) if len(points[: half + 1]) >= 2 else (None, 0.0)
    late_slope, _ = _slope(points[half:]) if len(points[half:]) >= 2 else (None, 0.0)
    speeding_up = bool(
        early_slope is not None and late_slope is not None
        and late_slope > early_slope and late_slope > 0
    )

    if per_6h >= ACCELERATE_PER_6H and speeding_up:
        label = MOMENTUM_ACCELERATING
        reason = f"Tốc độ tăng nhanh dần ({per_6h:+.1f} điểm/6 giờ, {len(points)} lần ghi nhận)."
    elif per_6h >= RISE_PER_6H:
        label = MOMENTUM_RISING
        reason = f"Điểm đang tăng đều ({per_6h:+.1f} điểm/6 giờ)."
    elif per_6h <= DECLINE_PER_6H:
        label = MOMENTUM_FADING
        reason = f"Điểm giảm nhanh ({per_6h:+.1f} điểm/6 giờ)."
    elif per_6h <= FADE_PER_6H:
        label = MOMENTUM_STEADY if abs(per_run) < 1.0 else MOMENTUM_FADING
        reason = f"Điểm giảm nhẹ ({per_6h:+.1f} điểm/6 giờ)."
    else:
        label = MOMENTUM_STEADY
        reason = f"Điểm gần như đi ngang ({per_6h:+.1f} điểm/6 giờ)."

    return {
        "label": label,
        "value": round(slope or 0.0, 4),
        "per_6h": per_6h,
        "per_run": per_run,
        "kind": "measured",
        "snapshots": len(points),
        "span_hours": span,
        "window": "long",
        "fit": round(r2, 2),
        "reason": reason,
    }


def confidence(history: Iterable[dict], freshness_hours: float | None = None,
               now: datetime | None = None) -> dict:
    """How much the stored history can be trusted for a prediction."""
    points = _points(history, now)
    factors: list[dict] = []
    if len(points) < 2:
        return {
            "value": 10.0 if points else 0.0,
            "label": "low",
            "kind": "estimated",
            "snapshots": len(points),
            "factors": [{"key": "samples", "score": 10.0 if points else 0.0,
                         "detail": f"{len(points)} lần ghi nhận"}],
            "reason": "Chưa đủ lịch sử để dự báo đáng tin cậy.",
        }

    sample_score = min(1.0, len(points) / 5) * 45
    span = _span_hours(points)
    span_score = min(1.0, span / 12) * 20
    # R² is meaningless on two points; only reward a real fit from three on.
    _, r2 = _slope(points)
    fit_score = r2 * 20 if len(points) >= 3 else 0.0
    last_seen = points[-1]["hours_ago"]
    recency_score = 15 if last_seen <= 3 else 10 if last_seen <= 12 else 4 if last_seen <= 36 else 0
    factors.extend(
        [
            {"key": "samples", "score": round(sample_score, 1),
             "detail": f"{len(points)} lần ghi nhận"},
            {"key": "span", "score": round(span_score, 1),
             "detail": f"Trải dài {span:.1f} giờ"},
            {"key": "fit", "score": round(fit_score, 1),
             "detail": f"Độ khớp xu hướng R²={r2:.2f}"},
            {"key": "recency", "score": float(recency_score),
             "detail": f"Lần ghi nhận gần nhất {last_seen:.1f} giờ trước"},
        ]
    )
    value = round(max(0.0, min(100.0, sample_score + span_score + fit_score + recency_score)), 1)
    # Two readings can look consistent by accident; keep them out of "high".
    if len(points) < 3:
        value = min(value, 35.0)
    elif len(points) < 4:
        value = min(value, 60.0)
    label = "high" if value >= 70 else "medium" if value >= 40 else "low"
    return {
        "value": value,
        "label": label,
        "kind": "estimated",
        "snapshots": len(points),
        "factors": factors,
        "reason": (
            f"Độ tin cậy {label} từ {len(points)} lần ghi nhận trong {span:.1f} giờ; "
            "cần thêm lần khám phá để chắc chắn hơn."
        ),
    }


def _freshness_multiplier(freshness_hours: float | None) -> tuple[float, str]:
    if freshness_hours is None:
        return 0.9, "Không rõ độ mới của nguồn"
    if freshness_hours <= 6:
        return 1.15, f"Tin rất mới ({freshness_hours:.1f} giờ)"
    if freshness_hours <= 24:
        return 1.0, f"Tin trong ngày ({freshness_hours:.1f} giờ)"
    if freshness_hours <= 48:
        return 0.85, f"Tin đã {freshness_hours:.1f} giờ"
    return 0.7, f"Tin đã {freshness_hours:.0f} giờ, ít cơ hội"


def _momentum_multiplier(label: str) -> tuple[float, str]:
    table = {
        MOMENTUM_ACCELERATING: (1.2, "Đang tăng tốc"),
        MOMENTUM_RISING: (1.1, "Đang đi lên"),
        MOMENTUM_STEADY: (1.0, "Đi ngang"),
        MOMENTUM_FADING: (0.75, "Đang hạ nhiệt"),
        MOMENTUM_UNKNOWN: (0.9, "Chưa đủ dữ liệu xu hướng"),
    }
    return table.get(label, (0.9, "Chưa rõ xu hướng"))


def opportunity(topic: dict, history: Iterable[dict], momentum_info: dict,
                freshness_hours: float | None = None,
                independent_domains: int | None = None,
                confidence_info: dict | None = None) -> dict:
    """Editorial opportunity: how worth writing about this is *right now*."""
    points = _points(history)
    current = float(points[-1]["score"] if points else (topic.get("score") or 0))
    fresh_mult, fresh_note = _freshness_multiplier(freshness_hours)
    momentum_mult, momentum_note = _momentum_multiplier(momentum_info.get("label", MOMENTUM_UNKNOWN))
    domains = int(independent_domains if independent_domains is not None
                  else (topic.get("source_count") or 0))
    coverage_bonus = 5.0 if domains >= 3 else 0.0
    # With almost no history the opportunity must not look certain.
    conf_value = (confidence_info or {}).get("value")
    if conf_value is None:
        conf_mult, conf_note = 0.85, "Chưa rõ độ tin cậy lịch sử"
    elif conf_value < 40:
        conf_mult, conf_note = 0.75, f"Độ tin cậy thấp ({conf_value:.0f}%) giảm điểm cơ hội"
    elif conf_value < 70:
        conf_mult, conf_note = 0.9, f"Độ tin cậy trung bình ({conf_value:.0f}%)"
    else:
        conf_mult, conf_note = 1.0, f"Độ tin cậy cao ({conf_value:.0f}%)"

    raw = current * fresh_mult * momentum_mult * conf_mult + coverage_bonus
    value = round(max(0.0, min(100.0, raw)), 1)

    phase = viral.phase_of(history, freshness_hours)["phase"]
    window = {
        viral.PHASE_EMERGING: 12,
        viral.PHASE_RISING: 12,
        viral.PHASE_PEAK: 6,
        viral.PHASE_DECLINING: 3,
        viral.PHASE_STEADY: 24,
        viral.PHASE_UNKNOWN: 24,
    }.get(phase, 24)

    label = "high" if value >= 70 else "medium" if value >= 45 else "low"
    components = [
        {"key": "score", "value": round(current, 1), "kind": "measured",
         "detail": "Điểm xu hướng hiện tại"},
        {"key": "freshness", "value": round(fresh_mult, 2), "kind": "measured",
         "detail": fresh_note},
        {"key": "momentum", "value": round(momentum_mult, 2), "kind": "estimated",
         "detail": momentum_note},
        {"key": "coverage", "value": coverage_bonus, "kind": "measured",
         "detail": f"{domains} tên miền độc lập"},
        {"key": "confidence", "value": round(conf_mult, 2), "kind": "estimated",
         "detail": conf_note},
    ]
    return {
        "score": value,
        "label": label,
        "kind": "estimated",
        "components": components,
        "publish_window_hours": window,
        "reason": (
            f"Điểm {current:.1f} × độ mới ×{fresh_mult:.2f} × momentum ×{momentum_mult:.2f} "
            f"× độ tin cậy ×{conf_mult:.2f}. "
            f"Nên đăng trong khoảng {window} giờ tới nếu muốn đón xu hướng."
        ),
    }


def forecast(history: Iterable[dict], confidence_info: dict,
             now: datetime | None = None) -> dict:
    """Damped linear projection for the *next* discovery run."""
    points = _points(history, now)
    if len(points) < MIN_FORECAST_POINTS:
        return {
            "available": False,
            "kind": "estimated",
            "next_score": None,
            "reason": f"Cần ít nhất {MIN_FORECAST_POINTS} lần ghi nhận để dự báo.",
        }
    if (confidence_info.get("value") or 0) < MIN_FORECAST_CONFIDENCE:
        return {
            "available": False,
            "kind": "estimated",
            "next_score": None,
            "reason": "Độ tin cậy lịch sử còn thấp nên chưa dự báo.",
        }
    slope, _ = _slope(points)
    if slope is None:
        return {
            "available": False, "kind": "estimated", "next_score": None,
            "reason": "Các lần ghi nhận trùng thời điểm nên không tính được xu hướng.",
        }
    gaps = [
        points[i + 1]["hours_ago"] - points[i]["hours_ago"]
        for i in range(len(points) - 1)
        if points[i + 1]["hours_ago"] > points[i]["hours_ago"]
    ]
    interval = max(1.0, min(72.0, median(gaps) if gaps else 6.0))
    projected = points[-1]["score"] + slope * interval * FORECAST_DAMPING
    next_score = round(max(0.0, min(100.0, projected)), 1)
    delta = round(next_score - points[-1]["score"], 1)
    direction = "up" if delta >= 1.0 else "down" if delta <= -1.0 else "flat"
    return {
        "available": True,
        "kind": "estimated",
        "next_score": next_score,
        "current_score": points[-1]["score"],
        "delta": delta,
        "direction": direction,
        "horizon_hours": round(interval, 1),
        "reason": (
            f"Ước tính theo xu hướng {slope * 6:+.1f} điểm/6 giờ, giảm tốc "
            f"{int((1 - FORECAST_DAMPING) * 100)}% để tránh ngoại suy quá đà."
        ),
    }


def history_compare(history: Iterable[dict], now: datetime | None = None) -> dict:
    """Compare the latest reading against this topic's own past."""
    points = _points(history, now)
    if not points:
        return {
            "runs": 0, "first_score": None, "peak_score": None,
            "peak_at": None, "current_vs_peak": None, "total_change": None,
            "trend": "unknown",
        }
    scores = [point["score"] for point in points]
    peak = max(scores)
    peak_point = next(point for point in points if point["score"] == peak)
    current = scores[-1]
    total = round(current - scores[0], 1)
    return {
        "runs": len(points),
        "first_score": round(scores[0], 1),
        "peak_score": round(peak, 1),
        "peak_at": peak_point["captured_at"],
        "current_vs_peak": round(current - peak, 1),
        "total_change": total,
        "trend": "up" if total >= 1 else "down" if total <= -1 else "flat",
        "first_seen_hours": round(points[0]["hours_ago"], 1),
    }


def analyze(topic: dict, history: Iterable[dict] | None = None,
            freshness_hours: float | None = None,
            independent_domains: int | None = None) -> dict:
    """Full prediction bundle for one topic."""
    history = list(history or [])
    points = _points(history)
    current = points[-1]["score"] if points else float(topic.get("score") or 0)
    previous = points[-2]["score"] if len(points) >= 2 else None
    change = round(current - previous, 1) if previous is not None else None

    momentum_info = momentum(history)
    confidence_info = confidence(history, freshness_hours)
    phase = viral.phase_of(history, freshness_hours)

    return {
        "current_score": round(current, 1),
        "previous_score": round(previous, 1) if previous is not None else None,
        "score_change": change,
        "momentum": momentum_info,
        "confidence": confidence_info,
        "lifecycle": {
            "phase": phase["phase"],
            "reason": phase["reason"],
            "accelerating": momentum_info["label"] == MOMENTUM_ACCELERATING,
        },
        "opportunity": opportunity(
            topic, history, momentum_info, freshness_hours, independent_domains,
            confidence_info,
        ),
        "forecast": forecast(history, confidence_info),
        "history": history_compare(history),
        "series": points[-12:],
        "disclaimer": DISCLAIMER,
    }


def trend_shift(history_rows: Iterable[dict], topics: Iterable[dict],
                window_hours: float = 24.0, now: datetime | None = None) -> dict:
    """Compare the current trend set with the previous window.

    Uses only stored snapshots: how many were captured now vs. before, and how
    each topic's latest score compares with its previous reading.
    """
    now = now or utcnow()
    rows = list(history_rows or [])
    by_topic: dict[int, list[dict]] = {}
    for row in rows:
        by_topic.setdefault(int(row.get("topic_id") or 0), []).append(row)
    for values in by_topic.values():
        values.sort(key=lambda item: item.get("captured_at") or "")

    rising = falling = stable = 0
    deltas: list[float] = []
    ranked: list[dict] = []
    titles = {int(t["id"]): t.get("title") for t in topics or [] if t.get("id")}
    for topic_id, values in by_topic.items():
        scores = [float(v["score"]) for v in values if v.get("score") is not None]
        if len(scores) < 2:
            continue
        delta = round(scores[-1] - scores[-2], 1)
        deltas.append(delta)
        if delta >= 1.0:
            rising += 1
        elif delta <= -1.0:
            falling += 1
        else:
            stable += 1
        ranked.append({
            "topic_id": topic_id,
            "title": titles.get(topic_id),
            "score": scores[-1],
            "score_change": delta,
        })
    ranked.sort(key=lambda item: item["score_change"], reverse=True)

    recent_count = previous_count = 0
    for row in rows:
        captured = parse_datetime(row.get("captured_at"))
        if not captured:
            continue
        hours = max(0.0, (now - captured).total_seconds() / 3600.0)
        if hours <= window_hours:
            recent_count += 1
        elif hours <= window_hours * 2:
            previous_count += 1

    latest_scores = [
        float(values[-1]["score"]) for values in by_topic.values()
        if values and values[-1].get("score") is not None
    ]
    earlier_scores = [
        float(values[-2]["score"]) for values in by_topic.values()
        if len(values) >= 2 and values[-2].get("score") is not None
    ]
    average_now = round(sum(latest_scores) / len(latest_scores), 2) if latest_scores else None
    average_before = round(sum(earlier_scores) / len(earlier_scores), 2) if earlier_scores else None

    return {
        "window_hours": window_hours,
        "snapshots_recent": recent_count,
        "snapshots_previous": previous_count,
        "rising": rising,
        "falling": falling,
        "stable": stable,
        "average_delta": round(sum(deltas) / len(deltas), 2) if deltas else None,
        "average_score_now": average_now,
        "average_score_before": average_before,
        "top_rising": ranked[:5],
        "top_falling": ranked[-5:][::-1] if ranked else [],
        "kind": "measured",
    }
