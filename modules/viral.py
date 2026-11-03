"""Viral intelligence: freshness phases and honest popularity labels.

Two separate questions are answered here:

* **Phase** – where a topic sits in its lifecycle (emerging / rising / peak /
  declining / steady), derived from the score history captured at every discovery
  run. Nothing is invented: with a single snapshot the phase is reported as
  ``unknown`` (or ``emerging`` when the story is still very fresh).

* **Viral label** – whether the available *verified* signals justify calling a
  topic viral. Ordinary news with two articles is never presented as verified
  viral content; the missing signals are listed explicitly instead.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .utils import age_hours, domain_of, parse_datetime, utcnow

PHASE_EMERGING = "emerging"
PHASE_RISING = "rising"
PHASE_PEAK = "peak"
PHASE_DECLINING = "declining"
PHASE_STEADY = "steady"
PHASE_UNKNOWN = "unknown"

PHASES = (PHASE_EMERGING, PHASE_RISING, PHASE_PEAK, PHASE_DECLINING, PHASE_STEADY, PHASE_UNKNOWN)

FRESH_EMERGING_HOURS = 12.0
PEAK_WINDOW_HOURS = 18.0
DECLINE_RATIO = 0.88
AGGREGATOR_DOMAINS = {"news.google.com", "trends.google.com", "hnrss.org"}

# Verified popularity thresholds (measured signals only).
VIRAL_SEARCH_TRAFFIC = 20_000
VIRAL_ENGAGEMENT = 500
VIRAL_MIN_DOMAINS = 3
WIDE_COVERAGE_DOMAINS = 3


def _hours(value) -> float | None:
    return age_hours(value) if value else None


def phase_of(history: Iterable[dict], freshness_hours: float | None = None,
             now: datetime | None = None) -> dict:
    """Classify the lifecycle phase from the captured score history."""
    now = now or utcnow()
    points = [row for row in history or [] if row.get("score") is not None]
    scores = [float(row["score"]) for row in points]
    if not scores:
        return {"phase": PHASE_UNKNOWN, "reason": "Chưa có dữ liệu điểm để xác định xu hướng"}

    last = scores[-1]
    last_seen_hours = _hours(points[-1].get("captured_at")) if points[-1].get("captured_at") else None

    if len(scores) == 1:
        if freshness_hours is not None and freshness_hours <= FRESH_EMERGING_HOURS:
            return {
                "phase": PHASE_EMERGING,
                "reason": f"Mới xuất hiện {freshness_hours:.1f} giờ trước, chưa đủ lịch sử để xác nhận xu hướng",
            }
        return {"phase": PHASE_UNKNOWN, "reason": "Chỉ có một lần ghi nhận điểm, chưa đủ để xác định xu hướng"}

    previous = scores[-2]
    delta = round(last - previous, 1)
    total_delta = round(last - scores[0], 1)
    peak = max(scores)
    peak_age = None
    for row in reversed(points):
        if float(row["score"]) == peak:
            peak_age = _hours(row.get("captured_at"))
            break

    if last < peak * DECLINE_RATIO and delta <= 0:
        return {
            "phase": PHASE_DECLINING,
            "reason": f"Điểm giảm {abs(delta):.1f} so với lần trước và thấp hơn đỉnh {peak:.1f}",
        }
    # A peak needs at least three snapshots: it is a plateau after a rise, not a
    # single high reading (which would otherwise label any fresh story as "peak").
    if len(scores) >= 3 and last >= peak * 0.97 and abs(delta) <= 1 \
            and (peak_age is None or peak_age <= PEAK_WINDOW_HOURS):
        return {
            "phase": PHASE_PEAK,
            "reason": f"Đang ở mức cao nhất ({last:.1f}) và giữ ổn định",
        }
    if delta >= 1.5 or total_delta >= 4:
        return {
            "phase": PHASE_RISING,
            "reason": f"Điểm tăng {delta:+.1f} so với lần trước, {total_delta:+.1f} so với lần đầu",
        }
    if delta <= -1.5:
        return {
            "phase": PHASE_DECLINING,
            "reason": f"Điểm giảm {abs(delta):.1f} so với lần trước",
        }
    if freshness_hours is not None and freshness_hours <= FRESH_EMERGING_HOURS and last_seen_hours is not None:
        return {
            "phase": PHASE_EMERGING,
            "reason": f"Tin mới {freshness_hours:.1f} giờ, điểm chưa biến động rõ",
        }
    return {"phase": PHASE_STEADY, "reason": "Điểm ổn định giữa các lần ghi nhận"}


def _independent_domains(articles: Iterable[dict]) -> set[str]:
    domains = set()
    for article in articles or []:
        domain = article.get("domain") or domain_of(article.get("url", ""))
        if domain and domain not in AGGREGATOR_DOMAINS:
            domains.add(domain)
    return domains


def assess(topic: dict, articles: Iterable[dict] | None = None,
           history: Iterable[dict] | None = None) -> dict:
    """Build the phase + viral assessment shown in the dashboard."""
    meta = topic.get("meta") or {}
    articles = list(articles if articles is not None else (topic.get("articles") or []))
    domains = _independent_domains(articles)
    if not domains and topic.get("primary_url"):
        domains = {domain_of(topic["primary_url"])} - {""}
    # List rows may not carry articles; discovery stores the independent-domain count.
    # A single primary_url must not understate a story covered by several domains.
    domain_count = max(len(domains), int(topic.get("source_count") or 0))
    traffic = meta.get("traffic")
    engagement = meta.get("engagement") if isinstance(meta.get("engagement"), dict) else None
    engagement_total = 0
    if engagement and engagement.get("verified"):
        engagement_total = (
            int(engagement.get("reactions") or 0)
            + int(engagement.get("comments") or 0)
            + int(engagement.get("shares") or 0)
        )

    published = [
        parse_datetime(a.get("published_at")) for a in articles if a.get("published_at")
    ]
    published = [d for d in published if d]
    newest = max(published) if published else parse_datetime(topic.get("last_seen_at"))
    freshness_hours = age_hours(newest) if newest else None

    history = list(history or [])
    phase = phase_of(history, freshness_hours)
    scores = [float(row["score"]) for row in history if row.get("score") is not None]
    delta = round(scores[-1] - scores[-2], 1) if len(scores) >= 2 else None

    verified: list[dict] = []
    missing: list[str] = []
    if traffic:
        verified.append({
            "key": "search_traffic", "kind": "verified",
            "detail": f"Google Trends: khoảng {int(traffic):,} lượt tìm kiếm".replace(",", "."),
            "value": int(traffic),
        })
    else:
        missing.append("không có số liệu tìm kiếm đo được (Google Trends)")
    if engagement_total:
        verified.append({
            "key": "engagement", "kind": "verified",
            "detail": f"{engagement_total} tương tác công khai đã xác thực (Graph API)",
            "value": engagement_total,
        })
    else:
        missing.append("không có số liệu tương tác công khai đã xác thực")
    verified.append({
        "key": "independent_domains", "kind": "verified",
        "detail": f"{domain_count} tên miền độc lập đưa tin",
        "value": domain_count,
    })

    # Viral score uses measured signals only, normalised to 0-100.
    domain_part = min(100.0, domain_count * 22.0)
    traffic_part = min(100.0, (traffic or 0) / VIRAL_SEARCH_TRAFFIC * 100)
    engagement_part = min(100.0, engagement_total / VIRAL_ENGAGEMENT * 100)
    weights = [(domain_part, 0.55)]
    if traffic:
        weights.append((traffic_part, 0.30))
    if engagement_total:
        weights.append((engagement_part, 0.35))
    total_weight = sum(w for _, w in weights)
    viral_score = round(sum(v * w for v, w in weights) / total_weight, 1) if total_weight else 0.0

    search_viral = bool(traffic and int(traffic) >= VIRAL_SEARCH_TRAFFIC and domain_count >= VIRAL_MIN_DOMAINS)
    engagement_viral = bool(
        engagement_total >= VIRAL_ENGAGEMENT and domain_count >= 2
    )
    if search_viral or engagement_viral:
        label = "viral_candidate"
        explanation = "Có tín hiệu lan truyền đã đo được (tìm kiếm và/hoặc tương tác) cùng nhiều nguồn độc lập."
    elif domain_count >= WIDE_COVERAGE_DOMAINS:
        label = "wide_coverage"
        explanation = (
            "Nhiều nguồn cùng đưa tin nhưng chưa có số liệu lan truyền đã xác thực; "
            "đây là tin có độ phủ rộng, chưa thể gọi là viral."
        )
    elif domain_count >= 2:
        label = "reported"
        explanation = (
            "Sự việc đang được báo chí đưa tin nhưng chưa có bằng chứng lan truyền trên mạng xã hội."
        )
    else:
        label = "insufficient"
        explanation = (
            "Chưa đủ tín hiệu đã xác thực để đánh giá mức độ lan truyền. "
            "Không nên trình bày như nội dung viral."
        )

    if label != "viral_candidate":
        missing_note = "Thiếu: " + "; ".join(missing) if missing else ""
        explanation = (explanation + " " + missing_note).strip()

    return {
        "phase": phase["phase"],
        "phase_reason": phase["reason"],
        "score": float(topic.get("score") or 0),
        "score_delta": delta,
        "freshness_hours": round(freshness_hours, 1) if freshness_hours is not None else None,
        "independent_domains": domain_count,
        "domains": sorted(domains),
        "history": [
            {
                "score": row.get("score"),
                "source_count": row.get("source_count"),
                "captured_at": row.get("captured_at"),
            }
            for row in history[-12:]
        ],
        "viral": {
            "label": label,
            "score": viral_score,
            "verified_signals": verified,
            "missing_signals": missing,
            "explanation": explanation,
            "disclaimer": (
                "Nhãn này chỉ dựa trên tín hiệu đo được (số nguồn độc lập, Google Trends, "
                "tương tác công khai). Ứng dụng không tự nhận nội dung là viral khi thiếu dữ liệu."
            ),
        },
    }


def snapshot(topic: dict, articles: Iterable[dict] | None = None) -> dict:
    """Payload written to ``topic_history`` after each discovery run."""
    assessment = assess(topic, articles)
    meta = topic.get("meta") or {}
    engagement = meta.get("engagement") if isinstance(meta.get("engagement"), dict) else None
    engagement_total = 0
    if engagement and engagement.get("verified"):
        engagement_total = (
            int(engagement.get("reactions") or 0)
            + int(engagement.get("comments") or 0)
            + int(engagement.get("shares") or 0)
        )
    return {
        "score": float(topic.get("score") or 0),
        "source_count": int(topic.get("source_count") or 0),
        "independent_domains": assessment["independent_domains"],
        "traffic": meta.get("traffic"),
        "engagement": engagement_total or None,
        "phase": assessment["phase"],
        "meta": {"viral_label": assessment["viral"]["label"]},
    }
