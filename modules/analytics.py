"""Trend and workflow analytics for the dashboard.

All figures come from stored rows (topics, history snapshots, posts, research);
nothing is estimated. Missing days are reported as zero so the charts have a
complete, comparable timeline.
"""

from __future__ import annotations

from datetime import timedelta

from .database import Database, db as default_db
from .utils import parse_datetime, utcnow

STATUS_ORDER = ["new", "researched", "drafted", "ready"]


def _day(iso_value) -> str | None:
    parsed = parse_datetime(iso_value)
    return parsed.date().isoformat() if parsed else None


def _days(days: int) -> list[str]:
    today = utcnow().date()
    return [(today - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


class Analytics:
    def __init__(self, database: Database | None = None):
        self.db = database or default_db

    def overview(self, days: int = 14) -> dict:
        days = max(2, min(90, int(days or 14)))
        window = _days(days)
        window_set = set(window)

        topics = self.db.query("SELECT * FROM topics")
        posts = self.db.query(
            "SELECT p.*, t.category AS topic_category FROM posts p "
            "LEFT JOIN topics t ON t.id = p.topic_id"
        )
        research = self.db.query("SELECT id, topic_id, confidence, confidence_label, created_at FROM research_reports")
        history = self.db.query("SELECT * FROM topic_history ORDER BY topic_id, id")

        series = {
            "discovered": {day: 0 for day in window},
            "researched": {day: 0 for day in window},
            "drafted": {day: 0 for day in window},
            "approved": {day: 0 for day in window},
            "rejected": {day: 0 for day in window},
        }

        researched_topics: dict[int, str] = {}
        for row in research:
            day = _day(row.get("created_at"))
            if day in window_set:
                series["researched"][day] += 1
            if row.get("topic_id") and row["topic_id"] not in researched_topics:
                researched_topics[row["topic_id"]] = day or ""

        for topic in topics:
            day = _day(topic.get("created_at"))
            if day in window_set:
                series["discovered"][day] += 1
        for post in posts:
            created_day = _day(post.get("created_at"))
            if created_day in window_set:
                series["drafted"][created_day] += 1
            approved_day = _day(post.get("approved_at"))
            if approved_day in window_set:
                series["approved"][approved_day] += 1
            rejected_day = _day(post.get("rejected_at"))
            if rejected_day in window_set:
                series["rejected"][rejected_day] += 1

        funnel = {
            "discovered": len(topics),
            "researched": len({r["topic_id"] for r in research if r.get("topic_id")}),
            "drafted": len(posts),
            "approved": sum(1 for p in posts if p.get("status") in ("approved", "exported")),
            "rejected": sum(1 for p in posts if p.get("status") == "rejected"),
            "exported": sum(1 for p in posts if p.get("status") == "exported"),
            "images": self.db.query_one("SELECT COUNT(*) AS c FROM images WHERE status = 'done'")["c"],
        }

        categories: dict[str, dict] = {}
        for topic in topics:
            key = topic.get("category") or "general"
            bucket = categories.setdefault(
                key, {"category": key, "topics": 0, "posts": 0, "approved": 0, "rejected": 0}
            )
            bucket["topics"] += 1
        for post in posts:
            key = post.get("topic_category") or "general"
            bucket = categories.setdefault(
                key, {"category": key, "topics": 0, "posts": 0, "approved": 0, "rejected": 0}
            )
            bucket["posts"] += 1
            if post.get("status") in ("approved", "exported"):
                bucket["approved"] += 1
            elif post.get("status") == "rejected":
                bucket["rejected"] += 1
        for bucket in categories.values():
            decided = bucket["approved"] + bucket["rejected"]
            bucket["approval_rate"] = round(bucket["approved"] / decided * 100, 1) if decided else None
        category_rows = sorted(
            categories.values(), key=lambda row: (row["approved"], row["posts"]), reverse=True
        )

        snapshots_per_topic: dict[int, list[dict]] = {}
        for row in history:
            snapshots_per_topic.setdefault(row["topic_id"], []).append(row)
        by_id = {t["id"]: t for t in topics}
        recurring = []
        for topic_id, rows in snapshots_per_topic.items():
            topic = by_id.get(topic_id)
            if not topic or len(rows) < 2:
                continue
            scores = [float(r["score"] or 0) for r in rows]
            recurring.append(
                {
                    "topic_id": topic_id,
                    "title": topic.get("title"),
                    "category": topic.get("category"),
                    "appearances": len(rows),
                    "first_seen_at": rows[0].get("captured_at"),
                    "last_seen_at": rows[-1].get("captured_at"),
                    "score": scores[-1],
                    "score_change": round(scores[-1] - scores[0], 1),
                    "peak_score": max(scores),
                    "source_count": topic.get("source_count"),
                }
            )
        recurring.sort(key=lambda row: (row["appearances"], row["score"]), reverse=True)

        confidences = [float(r["confidence"]) for r in research if r.get("confidence") is not None]
        confidence_stats = {
            "average": round(sum(confidences) / len(confidences), 1) if confidences else None,
            "low": sum(1 for c in confidences if c < 45),
            "moderate": sum(1 for c in confidences if 45 <= c < 75),
            "high": sum(1 for c in confidences if c >= 75),
        }

        return {
            "window_days": days,
            "generated_at": utcnow().isoformat(),
            "funnel": funnel,
            "series": [
                {
                    "date": day,
                    **{key: values[day] for key, values in series.items()},
                }
                for day in window
            ],
            "totals": {key: sum(values.values()) for key, values in series.items()},
            "categories": category_rows,
            "recurring": recurring[:12],
            "confidence": confidence_stats,
            "status_counts": self._status_counts(topics),
        }

    @staticmethod
    def _status_counts(topics: list[dict]) -> dict:
        counts = {status: 0 for status in STATUS_ORDER}
        for topic in topics:
            status = topic.get("status") or "new"
            counts[status] = counts.get(status, 0) + 1
        return counts

    def topic_timeline(self, topic_id: int, limit: int = 60) -> dict:
        rows = self.db.list_history(topic_id, limit)
        return {
            "topic_id": topic_id,
            "points": [
                {
                    "score": row.get("score"),
                    "source_count": row.get("source_count"),
                    "independent_domains": row.get("independent_domains"),
                    "phase": row.get("phase"),
                    "captured_at": row.get("captured_at"),
                }
                for row in rows
            ],
        }


service = Analytics()
