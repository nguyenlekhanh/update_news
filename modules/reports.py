"""Editorial reports: daily trends, weekly review, approvals, categories, feedback,
image quality.

Every report is generated from stored rows and carries the window it covers plus a
note about what is not measured. Reports can be rendered as Markdown for export.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Callable

from . import prediction
from .analytics import Analytics, service as analytics_service
from .database import Database, db as default_db
from .image_studio import sha256_of
from .learning import EditorialLearning
from .utils import parse_datetime, truncate, utcnow, utcnow_iso

REPORT_KINDS = (
    "daily_trend",
    "weekly_editorial",
    "approval",
    "category_performance",
    "editorial_feedback",
    "image_quality",
)

TITLES = {
    "daily_trend": ("Báo cáo xu hướng trong ngày", "Daily trend report"),
    "weekly_editorial": ("Báo cáo biên tập tuần", "Weekly editorial report"),
    "approval": ("Báo cáo duyệt nội dung", "Content approval report"),
    "category_performance": ("Hiệu quả theo nhóm nội dung", "Category performance"),
    "editorial_feedback": ("Báo cáo phản hồi biên tập", "Editorial feedback report"),
    "image_quality": ("Báo cáo chất lượng ảnh", "Image quality report"),
}

STATUS_LABELS = {
    "draft": "Bản nháp",
    "approved": "Đã duyệt",
    "rejected": "Từ chối",
    "exported": "Đã xuất",
}


def _in_window(value, days: int) -> bool:
    parsed = parse_datetime(value)
    if not parsed:
        return False
    return parsed >= utcnow() - timedelta(days=days)


def _section(key: str, title_vi: str, title_en: str, columns: list[tuple[str, str]],
             rows: list[dict]) -> dict:
    return {
        "key": key,
        "title_vi": title_vi,
        "title_en": title_en,
        "columns": [{"key": col, "label_vi": label} for col, label in columns],
        "rows": rows,
    }


def _markdown(title: str, summary: list[dict], sections: list[dict],
              notes: list[str], window: str) -> str:
    lines = [f"# {title}", "", f"_{window}_", ""]
    if summary:
        lines.append("## Tóm tắt")
        for item in summary:
            lines.append(f"- **{item['label_vi']}**: {item['value']}")
        lines.append("")
    for section in sections:
        lines.append(f"## {section['title_vi']}")
        columns = [column["label_vi"] for column in section["columns"]]
        if not section["rows"]:
            lines.extend(["_(không có dữ liệu)_", ""])
            continue
        lines.append("| " + " | ".join(columns) + " |")
        lines.append("| " + " | ".join("---" for _ in columns) + " |")
        for row in section["rows"]:
            cells = [str(row.get(column["key"], "")) for column in section["columns"]]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    if notes:
        lines.append("## Ghi chú")
        for note in notes:
            lines.append(f"- {note}")
        lines.append("")
    return "\n".join(lines)


class ReportService:
    def __init__(self, database: Database | None = None,
                 analytics: Analytics | None = None,
                 learning: EditorialLearning | None = None):
        self.db = database or default_db
        self.analytics = analytics or analytics_service
        self.learning = learning or EditorialLearning(self.db)

    # ----------------------------------------------------------------- catalog
    def catalog(self) -> list[dict]:
        return [
            {
                "kind": kind,
                "title_vi": TITLES[kind][0],
                "title_en": TITLES[kind][1],
            }
            for kind in REPORT_KINDS
        ]

    def build(self, kind: str, days: int = 7) -> dict:
        if kind not in REPORT_KINDS:
            raise ValueError(f"Loại báo cáo không hợp lệ: {kind}")
        days = max(1, min(90, int(days or 7)))
        builder: Callable[[int], dict] = {
            "daily_trend": self._daily_trend,
            "weekly_editorial": self._weekly_editorial,
            "approval": self._approval,
            "category_performance": self._category_performance,
            "editorial_feedback": self._editorial_feedback,
            "image_quality": self._image_quality,
        }[kind]
        title_vi, title_en = TITLES[kind]
        payload = builder(days)
        window = f"{days} ngày gần nhất · tạo lúc {utcnow_iso()}"
        report = {
            "kind": kind,
            "title_vi": title_vi,
            "title_en": title_en,
            "window_days": days,
            "generated_at": utcnow_iso(),
            "summary": payload["summary"],
            "sections": payload["sections"],
            "notes": payload["notes"],
            "markdown": "",
        }
        report["markdown"] = _markdown(
            title_vi, report["summary"], report["sections"], report["notes"], window
        )
        return report

    # ------------------------------------------------------------ daily trends
    def _daily_trend(self, days: int) -> dict:
        topics = self.db.query("SELECT * FROM topics")
        recent = [t for t in topics if _in_window(t.get("created_at"), days)]
        history = self.db.query("SELECT * FROM topic_history ORDER BY id")
        shift = prediction.trend_shift(history, topics, window_hours=days * 24)
        phases: dict[str, int] = {}
        for topic in topics:
            phase = (self.db.latest_history(topic["id"]) or {}).get("phase") or "unknown"
            phases[phase] = phases.get(phase, 0) + 1

        top = sorted(recent, key=lambda t: float(t.get("score") or 0), reverse=True)[:10]
        rows = [
            {
                "title": truncate(topic.get("title"), 80),
                "category": topic.get("category"),
                "score": round(float(topic.get("score") or 0), 1),
                "sources": topic.get("source_count"),
                "status": topic.get("status"),
            }
            for topic in top
        ]
        phase_rows = [
            {"phase": key, "topics": value} for key, value in
            sorted(phases.items(), key=lambda kv: -kv[1])
        ]
        return {
            "summary": [
                {"label_vi": "Chủ đề mới trong khoảng", "value": len(recent)},
                {"label_vi": "Tổng chủ đề đã lưu", "value": len(topics)},
                {"label_vi": "Điểm trung bình hiện tại", "value": shift["average_score_now"] or "—"},
                {"label_vi": "Đang tăng / giảm", "value": f"{shift['rising']} / {shift['falling']}"},
            ],
            "sections": [
                _section("top", "Chủ đề nổi bật", "Top topics",
                         [("title", "Chủ đề"), ("category", "Nhóm"), ("score", "Điểm"),
                          ("sources", "Nguồn"), ("status", "Trạng thái")], rows),
                _section("phases", "Phân bố pha xu hướng", "Lifecycle phases",
                         [("phase", "Pha"), ("topics", "Số chủ đề")], phase_rows),
                _section("rising", "Đang tăng nhanh", "Rising",
                         [("title", "Chủ đề"), ("score", "Điểm"), ("score_change", "Thay đổi")],
                         shift["top_rising"]),
                _section("falling", "Đang hạ nhiệt", "Falling",
                         [("title", "Chủ đề"), ("score", "Điểm"), ("score_change", "Thay đổi")],
                         shift["top_falling"]),
            ],
            "notes": [
                "Pha xu hướng và thay đổi điểm lấy từ lịch sử khám phá thật.",
                "Không có số liệu tương tác Facebook trong báo cáo này.",
            ],
        }

    # ------------------------------------------------------- weekly editorial
    def _weekly_editorial(self, days: int) -> dict:
        data = self.analytics.overview(days)
        posts = self.db.query(
            "SELECT p.*, t.category AS topic_category FROM posts p "
            "LEFT JOIN topics t ON t.id = p.topic_id"
        )
        window_posts = [p for p in posts if _in_window(p.get("created_at"), days)]
        approved = [p for p in window_posts if p.get("status") in ("approved", "exported")]
        rejected = [p for p in window_posts if p.get("status") == "rejected"]
        waiting = [p for p in posts if p.get("status") == "draft"]
        confidence = data.get("confidence") or {}

        serial = [
            {"date": row["date"], "discovered": row["discovered"], "researched": row["researched"],
             "drafted": row["drafted"], "approved": row["approved"], "rejected": row["rejected"]}
            for row in data.get("series", [])
        ]
        category_rows = [
            {
                "category": row["category"], "topics": row["topics"], "posts": row["posts"],
                "approved": row["approved"],
                "approval_rate": row["approval_rate"] if row["approval_rate"] is not None else "—",
            }
            for row in data.get("categories", [])[:12]
        ]
        return {
            "summary": [
                {"label_vi": "Bài tạo mới", "value": len(window_posts)},
                {"label_vi": "Bài đã duyệt", "value": len(approved)},
                {"label_vi": "Bài bị từ chối", "value": len(rejected)},
                {"label_vi": "Đang chờ duyệt", "value": len(waiting)},
                {"label_vi": "Độ tin cậy nghiên cứu TB", "value": confidence.get("average") or "—"},
            ],
            "sections": [
                _section("series", "Diễn biến theo ngày", "Daily activity",
                         [("date", "Ngày"), ("discovered", "Khám phá"),
                          ("researched", "Nghiên cứu"), ("drafted", "Viết"),
                          ("approved", "Duyệt"), ("rejected", "Từ chối")], serial),
                _section("categories", "Nhóm nội dung", "Categories",
                         [("category", "Nhóm"), ("topics", "Chủ đề"), ("posts", "Bài"),
                          ("approved", "Duyệt"), ("approval_rate", "Tỉ lệ duyệt %")],
                         category_rows),
            ],
            "notes": [
                "Tỉ lệ duyệt chỉ tính trên các bài đã có quyết định duyệt/từ chối.",
                "Đây là báo cáo biên tập nội bộ, không phải số liệu tiếp cận Facebook.",
            ],
        }

    # -------------------------------------------------------------- approval
    def _approval(self, days: int) -> dict:
        posts = self.db.query(
            """SELECT p.*, t.category AS topic_category FROM posts p
               LEFT JOIN topics t ON t.id = p.topic_id ORDER BY p.id DESC"""
        )
        counts = {status: 0 for status in STATUS_LABELS}
        for post in posts:
            counts[post.get("status") or "draft"] = counts.get(post.get("status") or "draft", 0) + 1
        decided = counts.get("approved", 0) + counts.get("exported", 0) + counts.get("rejected", 0)
        approval_rate = (
            round((counts.get("approved", 0) + counts.get("exported", 0)) / decided * 100, 1)
            if decided else None
        )
        latencies = []
        for post in posts:
            if post.get("status") in ("approved", "exported") and post.get("approved_at") and post.get("created_at"):
                created = parse_datetime(post["created_at"])
                approved = parse_datetime(post["approved_at"])
                if created and approved and approved >= created:
                    latencies.append((approved - created).total_seconds() / 3600)

        pending = [p for p in posts if p.get("status") == "draft"]
        pending_rows = [
            {
                "post_id": post["id"],
                "headline": truncate(post.get("headline"), 70),
                "category": post.get("topic_category"),
                "age_hours": round(max(0.0, (utcnow() - parse_datetime(post.get("created_at"))).total_seconds() / 3600), 1)
                if parse_datetime(post.get("created_at")) else "—",
            }
            for post in pending[:20]
        ]
        status_rows = [
            {"status": STATUS_LABELS.get(key, key), "count": value}
            for key, value in counts.items()
        ]
        return {
            "summary": [
                {"label_vi": "Tổng bài viết", "value": len(posts)},
                {"label_vi": "Tỉ lệ duyệt", "value": f"{approval_rate}%" if approval_rate is not None else "—"},
                {"label_vi": "Thời gian duyệt TB (giờ)", "value": round(sum(latencies) / len(latencies), 1) if latencies else "—"},
                {"label_vi": "Chờ duyệt", "value": len(pending)},
            ],
            "sections": [
                _section("status", "Theo trạng thái", "By status",
                         [("status", "Trạng thái"), ("count", "Số bài")], status_rows),
                _section("pending", "Bài đang chờ duyệt", "Waiting for approval",
                         [("post_id", "#"), ("headline", "Tiêu đề"), ("category", "Nhóm"),
                          ("age_hours", "Tuổi (giờ)")], pending_rows),
            ],
            "notes": [
                "Duyệt vẫn là thao tác thủ công; báo cáo chỉ tổng hợp trạng thái.",
                "Bài đã xuất được tính là đã duyệt.",
            ],
        }

    # ---------------------------------------------------- category performance
    def _category_performance(self, days: int) -> dict:
        data = self.analytics.overview(days)
        metrics = self.db.list_metrics(days=max(days, 30))
        metrics_by_category: dict[str, dict] = {}
        for row in metrics:
            key = row.get("category") or "general"
            bucket = metrics_by_category.setdefault(key, {"category": key, "posts": 0, "reach": 0, "engagement": 0})
            bucket["posts"] += 1
            bucket["reach"] += int(row.get("reach") or 0)
            bucket["engagement"] += int(row.get("engagement") or 0)
        for bucket in metrics_by_category.values():
            bucket["engagement_rate"] = (
                round(bucket["engagement"] / bucket["reach"] * 100, 2) if bucket["reach"] else None
            )
        rows = []
        for row in data.get("categories", []):
            metrics_row = metrics_by_category.get(row["category"], {})
            rows.append(
                {
                    "category": row["category"],
                    "topics": row["topics"],
                    "posts": row["posts"],
                    "approved": row["approved"],
                    "rejected": row["rejected"],
                    "approval_rate": row["approval_rate"] if row["approval_rate"] is not None else "—",
                    "measured_posts": metrics_row.get("posts", 0),
                    "reach": metrics_row.get("reach", 0),
                    "engagement": metrics_row.get("engagement", 0),
                    "engagement_rate": metrics_row.get("engagement_rate")
                    if metrics_row.get("engagement_rate") is not None else "—",
                }
            )
        return {
            "summary": [
                {"label_vi": "Nhóm có bài duyệt", "value": sum(1 for row in rows if row["approved"])},
                {"label_vi": "Nhóm có số liệu Facebook", "value": len(metrics_by_category)},
                {"label_vi": "Bài có số liệu đo được", "value": len(metrics)},
            ],
            "sections": [
                _section("categories", "Hiệu quả theo nhóm", "Category performance",
                         [("category", "Nhóm"), ("topics", "Chủ đề"), ("posts", "Bài"),
                          ("approved", "Duyệt"), ("rejected", "Từ chối"),
                          ("approval_rate", "Tỉ lệ duyệt %"), ("measured_posts", "Có số liệu"),
                          ("reach", "Tiếp cận"), ("engagement", "Tương tác"),
                          ("engagement_rate", "Tỉ lệ tương tác %")], rows),
            ],
            "notes": [
                "Các cột tiếp cận/tương tác chỉ có số khi bạn đã nhập số liệu Facebook.",
                "Không có số liệu thì để trống thay vì ước lượng.",
            ],
        }

    # -------------------------------------------------------- editorial feedback
    def _editorial_feedback(self, days: int) -> dict:
        summary = self.learning.summary()
        styles = [
            {
                "style": row["key"],
                "samples": row["samples"],
                "approved": row["approved"],
                "rejected": row["rejected"],
                "edits": row["edits"],
                "approval_rate": row["approval_rate"] if row["approval_rate"] is not None else "—",
                "average_rating": row["average_rating"] if row["average_rating"] is not None else "—",
            }
            for row in summary["styles"]
        ]
        recent = [
            {
                "kind": row.get("kind"),
                "style": row.get("style") or "—",
                "category": row.get("category") or "—",
                "rating": row.get("rating") or "—",
                "signal": row.get("signal") or "—",
                "created_at": (row.get("created_at") or "")[:16].replace("T", " "),
            }
            for row in summary["recent"][:15]
        ]
        counts = summary["counts"]
        return {
            "summary": [
                {"label_vi": "Tổng phản hồi", "value": summary["samples"]},
                {"label_vi": "Đã duyệt", "value": counts.get("approve", 0)},
                {"label_vi": "Từ chối", "value": counts.get("reject", 0)},
                {"label_vi": "Lượt sửa bài", "value": counts.get("edit", 0)},
                {"label_vi": "Điểm đánh giá TB", "value": summary["ratings"]["average"] or "—"},
            ],
            "sections": [
                _section("styles", "Kiểu viết theo phản hồi", "Styles",
                         [("style", "Kiểu"), ("samples", "Mẫu"), ("approved", "Duyệt"),
                          ("rejected", "Từ chối"), ("edits", "Sửa"),
                          ("approval_rate", "Tỉ lệ duyệt %"), ("average_rating", "Điểm TB")],
                         styles),
                _section("recent", "Phản hồi gần đây", "Recent feedback",
                         [("kind", "Loại"), ("style", "Kiểu"), ("category", "Nhóm"),
                          ("rating", "Điểm"), ("signal", "Tín hiệu"), ("created_at", "Thời điểm")],
                         recent),
            ],
            "notes": [
                summary["privacy"],
                f"Cần tối thiểu {summary['min_samples']} mẫu mỗi kiểu trước khi dùng làm gợi ý.",
            ],
        }

    # --------------------------------------------------------- image quality
    def _image_quality(self, days: int) -> dict:
        rows = self.db.query(
            """SELECT i.*, p.headline AS headline, p.category AS category
               FROM images i LEFT JOIN posts p ON p.id = i.post_id
               WHERE i.status = 'done' ORDER BY i.id DESC"""
        )
        window_rows = [row for row in rows if _in_window(row.get("created_at"), days)] or rows
        hashes: dict[str, list[int]] = {}
        scored, text_risky, duplicates = 0, 0, 0
        table = []
        for row in window_rows:
            meta = row.get("meta") or {}
            digest = meta.get("sha256") or sha256_of(row.get("path") or "")
            if digest:
                hashes.setdefault(digest, []).append(row["id"])
            check = meta.get("checks") or {}
            if check.get("score") is not None:
                scored += 1
            if (meta.get("text_risk") or meta.get("style_text_risk")) in ("medium", "high"):
                text_risky += 1
            table.append(
                {
                    "image_id": row["id"],
                    "post_id": row.get("post_id"),
                    "style": meta.get("image_style") or "—",
                    "aspect": meta.get("aspect") or "—",
                    "score": check.get("score") if check.get("score") is not None else (
                        (meta.get("relevance") or {}).get("score") if meta.get("relevance") else "—"
                    ),
                    "relevance": (meta.get("relevance") or {}).get("score", "—"),
                    "text_risk": meta.get("text_risk") or "—",
                }
            )
        duplicates = sum(len(ids) - 1 for ids in hashes.values() if len(ids) > 1)
        styles: dict[str, int] = {}
        for row in window_rows:
            style = (row.get("meta") or {}).get("image_style") or "unknown"
            styles[style] = styles.get(style, 0) + 1
        style_rows = [{"style": key, "images": value} for key, value in sorted(styles.items(), key=lambda kv: -kv[1])]
        return {
            "summary": [
                {"label_vi": "Ảnh đã tạo (khoảng)", "value": len(window_rows)},
                {"label_vi": "Ảnh có điểm kiểm tra", "value": scored},
                {"label_vi": "Ảnh trùng lặp", "value": duplicates},
                {"label_vi": "Ảnh có nguy cơ chữ", "value": text_risky},
            ],
            "sections": [
                _section("styles", "Theo phong cách", "By style",
                         [("style", "Phong cách"), ("images", "Số ảnh")], style_rows),
                _section("images", "Ảnh gần đây", "Recent images",
                         [("image_id", "#"), ("post_id", "Bài"), ("style", "Phong cách"),
                          ("aspect", "Tỉ lệ"), ("score", "Điểm kiểm tra"),
                          ("relevance", "Liên quan"), ("text_risk", "Nguy cơ chữ")],
                         table[:20]),
            ],
            "notes": [
                "Điểm kiểm tra dùng kích thước, tỉ lệ, dung lượng, hash trùng lặp và rủi ro chữ từ prompt.",
                "Đánh giá nội dung ảnh bằng vision model là tùy chọn và mặc định tắt để tiết kiệm VRAM.",
            ],
        }


service = ReportService()
