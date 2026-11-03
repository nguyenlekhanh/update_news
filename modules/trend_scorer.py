"""Transparent 0-100 trend scoring with explicit metric provenance.

Every metric is labelled as one of:
  verified  - measured directly from a public source (dates, domains, Trends traffic, API counts)
  estimated - derived heuristic from measurable data
  editorial - AI/heuristic judgement about audience interest or uniqueness

Missing signals are excluded and weights renormalised; engagement is never invented.
"""

from __future__ import annotations

import math
from datetime import datetime

from .utils import age_hours, domain_of, parse_datetime

WEIGHTS = {
    "freshness": 22,
    "source_diversity": 22,
    "search_interest": 14,
    "engagement": 8,
    "audience_fit": 14,
    "uniqueness": 8,
    "reliability": 12,
}

FRESHNESS_HALF_LIFE_HOURS = 18.0

CATEGORY_AUDIENCE_PRIOR = {
    "ai": 78, "technology": 74, "vietnam": 78, "entertainment": 72,
    "celebrity": 70, "human_interest": 76, "internet_culture": 72,
    "international": 72, "business": 68, "finance": 68, "science": 70,
    "lifestyle": 66, "social": 70, "education": 64, "discovery": 72,
    "general": 62,
}

_TRAFFIC_BANDS = [
    (200_000, 100), (100_000, 92), (50_000, 84), (20_000, 74),
    (10_000, 64), (5_000, 54), (2_000, 44), (1_000, 34), (500, 26), (0, 20),
]


def _traffic_score(traffic: int) -> int:
    for threshold, score in _TRAFFIC_BANDS:
        if traffic >= threshold:
            return score
    return 20


def _engagement_score(engagement: dict) -> int:
    total = (
        int(engagement.get("reactions") or 0)
        + int(engagement.get("comments") or 0) * 2
        + int(engagement.get("shares") or 0) * 3
    )
    if total <= 0:
        return 0
    return int(min(100, round(12 * math.log10(total + 1) + 4)))


class TrendScorer:
    def score(self, cluster: dict, editorial: dict | None = None) -> dict:
        articles = cluster.get("articles") or []
        domains: dict[str, float] = {}
        reliabilities: list[float] = []
        dates: list[datetime] = []
        for article in articles:
            domain = article.get("domain") or domain_of(article.get("url", ""))
            if not domain or domain in ("news.google.com", "trends.google.com", "hnrss.org"):
                continue
            domains[domain] = max(domains.get(domain, 0), float(article.get("reliability") or 0.5))
            reliabilities.append(float(article.get("reliability") or 0.5))
            published = parse_datetime(article.get("published_at"))
            if published:
                dates.append(published)

        newest_age = age_hours(max(dates)) if dates else None
        metrics: list[dict] = []

        # Freshness -----------------------------------------------------------
        if newest_age is not None:
            freshness = 100 * math.exp(-math.log(2) * newest_age / FRESHNESS_HALF_LIFE_HOURS)
            freshness = max(5.0, min(100.0, freshness))
            metrics.append({
                "key": "freshness", "weight": WEIGHTS["freshness"],
                "value": round(freshness, 1), "kind": "verified",
                "detail": f"Mới nhất {newest_age:.1f} giờ trước",
            })
        else:
            metrics.append({
                "key": "freshness", "weight": WEIGHTS["freshness"], "value": 45.0,
                "kind": "estimated", "detail": "Nguồn không cung cấp ngày đăng",
            })

        # Source diversity ----------------------------------------------------
        unique_domains = len(domains)
        diversity_value = {
            0: 18, 1: 30, 2: 58, 3: 74, 4: 84, 5: 90, 6: 94
        }.get(unique_domains, 98)
        metrics.append({
            "key": "source_diversity", "weight": WEIGHTS["source_diversity"],
            "value": float(diversity_value), "kind": "verified",
            "detail": f"{unique_domains} tên miền độc lập: {', '.join(sorted(domains)[:5]) or '—'}",
        })

        # Search interest -----------------------------------------------------
        traffic = cluster.get("traffic")
        if traffic:
            metrics.append({
                "key": "search_interest", "weight": WEIGHTS["search_interest"],
                "value": float(_traffic_score(int(traffic))), "kind": "verified",
                "detail": f"Google Trends: ~{int(traffic):,} lượt tìm kiếm".replace(",", "."),
            })

        # Engagement (only when measured through an authorized API) -----------
        engagement = cluster.get("engagement")
        if engagement and engagement.get("verified"):
            metrics.append({
                "key": "engagement", "weight": WEIGHTS["engagement"],
                "value": float(_engagement_score(engagement)), "kind": "verified",
                "detail": (
                    f"{engagement.get('reactions', 0)} cảm xúc, "
                    f"{engagement.get('comments', 0)} bình luận, "
                    f"{engagement.get('shares', 0)} chia sẻ (Graph API)"
                ),
            })

        # Editorial: audience fit & uniqueness --------------------------------
        category = cluster.get("category") or "general"
        audience_value = (
            float(editorial.get("audience_fit"))
            if editorial and editorial.get("audience_fit") is not None
            else float(CATEGORY_AUDIENCE_PRIOR.get(category, 60))
        )
        metrics.append({
            "key": "audience_fit", "weight": WEIGHTS["audience_fit"],
            "value": max(0.0, min(100.0, audience_value)),
            "kind": "editorial" if editorial else "estimated",
            "detail": (editorial or {}).get("reason") or f"Ưu tiên theo nhóm nội dung: {category}",
        })

        uniqueness_value = (
            float(editorial.get("uniqueness"))
            if editorial and editorial.get("uniqueness") is not None
            else self._heuristic_uniqueness(cluster)
        )
        metrics.append({
            "key": "uniqueness", "weight": WEIGHTS["uniqueness"],
            "value": max(0.0, min(100.0, uniqueness_value)),
            "kind": "editorial" if editorial else "estimated",
            "detail": "Đánh giá của AI về mức độ khác biệt so với tin thường ngày",
        })

        # Reliability ---------------------------------------------------------
        reliability_value = (sum(reliabilities) / len(reliabilities) * 100) if reliabilities else 45.0
        metrics.append({
            "key": "reliability", "weight": WEIGHTS["reliability"],
            "value": round(reliability_value, 1), "kind": "estimated",
            "detail": "Trung bình độ tin cậy theo tên miền nguồn (thang nội bộ)",
        })

        total_weight = sum(m["weight"] for m in metrics)
        score = sum(m["value"] * m["weight"] for m in metrics) / total_weight if total_weight else 0
        score = round(max(0.0, min(100.0, score)), 1)

        caveats = []
        if unique_domains < 2:
            caveats.append("chưa đủ 2 nguồn độc lập để chéo kiểm chứng")
        if newest_age is None:
            caveats.append("thiếu ngày đăng để xác nhận độ mới")
        if not any(m["key"] == "engagement" for m in metrics):
            caveats.append("không có số liệu tương tác công khai đã xác thực")

        rationale_parts = [f"Điểm {score:.0f}/100"]
        if unique_domains:
            rationale_parts.append(f"{unique_domains} nguồn độc lập")
        if newest_age is not None:
            rationale_parts.append(f"cập nhật {newest_age:.1f} giờ trước")
        if traffic:
            rationale_parts.append("đang có lượng tìm kiếm đo được trên Google Trends")
        if editorial and editorial.get("reason"):
            rationale_parts.append(str(editorial["reason"]).strip())
        rationale = " · ".join(rationale_parts)
        if caveats:
            rationale += ". Lưu ý: " + "; ".join(caveats) + "."

        return {
            "score": score,
            "metrics": metrics,
            "rationale": rationale,
            "caveats": caveats,
            "verified_metrics": [m["key"] for m in metrics if m["kind"] == "verified"],
        }

    @staticmethod
    def _heuristic_uniqueness(cluster: dict) -> float:
        title = (cluster.get("title") or "").strip()
        words = len(title.split())
        value = 55.0
        if 7 <= words <= 16:
            value += 12
        if any(ch.isdigit() for ch in title):
            value += 8
        if cluster.get("traffic"):
            value += 6
        article_count = len(cluster.get("articles") or [])
        if article_count > 8:
            value -= 10
        return max(20.0, min(90.0, value))
