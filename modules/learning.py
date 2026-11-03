"""Editorial learning from the user's own review behaviour.

Every approve, reject, version choice, edit, image pick and rating is recorded
locally. Those records are aggregated into preferences which are used only as a
short guidance block in future writing prompts — no model is fine-tuned, no
private Facebook data is read, and the whole history can be wiped from the
dashboard at any time.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Iterable

from .config import settings
from .database import Database, db as default_db
from .utils import truncate

KIND_APPROVE = "approve"
KIND_REJECT = "reject"
KIND_VERSION_SELECT = "version_select"
KIND_EDIT = "edit"
KIND_IMAGE_SELECT = "image_select"
KIND_RATING = "rating"
KIND_TOPIC_REJECT = "topic_reject"

KINDS = (
    KIND_APPROVE, KIND_REJECT, KIND_VERSION_SELECT, KIND_EDIT,
    KIND_IMAGE_SELECT, KIND_RATING, KIND_TOPIC_REJECT,
)

POSITIVE_KINDS = {KIND_APPROVE, KIND_VERSION_SELECT, KIND_IMAGE_SELECT}
NEGATIVE_KINDS = {KIND_REJECT, KIND_TOPIC_REJECT}


def _ratio(before: str, after: str) -> float:
    before = (before or "").strip()
    after = (after or "").strip()
    if not before and not after:
        return 0.0
    if not before or not after:
        return 1.0
    matcher = SequenceMatcher(None, truncate(before, 4000), truncate(after, 4000))
    return round(1 - matcher.ratio(), 3)


class EditorialLearning:
    def __init__(self, database: Database | None = None):
        self.db = database or default_db

    # ------------------------------------------------------------------ record
    def record(self, kind: str, topic_id: int | None = None, post_id: int | None = None,
               version_id: int | None = None, category: str = "", style: str = "",
               signal: str = "", rating: int | None = None,
               detail: dict | None = None) -> int | None:
        if kind not in KINDS:
            raise ValueError(f"Loại phản hồi không hợp lệ: {kind}")
        if not settings.get("learning.enabled", True):
            return None
        if rating is not None:
            rating = max(1, min(5, int(rating)))
        return self.db.add_feedback(
            {
                "kind": kind, "topic_id": topic_id, "post_id": post_id,
                "version_id": version_id, "category": category or "",
                "style": style or "", "signal": signal or "",
                "rating": rating, "detail": detail or {},
            }
        )

    def record_decision(self, kind: str, post: dict, signal: str = "",
                        detail: dict | None = None) -> int | None:
        """Record approve/reject for a stored post row."""
        if not post:
            return None
        return self.record(
            kind,
            topic_id=post.get("topic_id"),
            post_id=post.get("id"),
            category=post.get("topic_category") or post.get("category") or "",
            style=post.get("style") or "",
            signal=signal,
            detail=detail,
        )

    def record_edit(self, post: dict, before: dict, after: dict) -> int | None:
        """Compare the editor submission with the stored post."""
        headline_change = _ratio(before.get("headline"), after.get("headline"))
        body_change = _ratio(before.get("main_post"), after.get("main_post"))
        if headline_change < 0.02 and body_change < 0.02:
            return None
        return self.record(
            KIND_EDIT,
            topic_id=post.get("topic_id"),
            post_id=post.get("id"),
            category=post.get("topic_category") or post.get("category") or "",
            style=post.get("style") or "",
            signal="manual_edit",
            detail={
                "headline_change": headline_change,
                "body_change": body_change,
                "before_words": len((before.get("main_post") or "").split()),
                "after_words": len((after.get("main_post") or "").split()),
            },
        )

    def record_version_choice(self, topic: dict, version: dict,
                              rejected: Iterable[dict] | None = None) -> int | None:
        rejected = list(rejected or [])
        return self.record(
            KIND_VERSION_SELECT,
            topic_id=topic.get("id") if topic else None,
            version_id=version.get("id") if version else None,
            category=(topic or {}).get("category") or "",
            style=(version or {}).get("style") or "",
            signal=f"variant_{((version or {}).get('variant') or '?').lower()}",
            detail={
                "variant": (version or {}).get("variant"),
                "rejected_variants": [v.get("variant") for v in rejected],
                "rejected_styles": [v.get("style") for v in rejected],
            },
        )

    def record_image_choice(self, post: dict, image: dict,
                            variation_index: int | None = None) -> int | None:
        meta = (image or {}).get("meta") or {}
        return self.record(
            KIND_IMAGE_SELECT,
            topic_id=(post or {}).get("topic_id"),
            post_id=(post or {}).get("id"),
            category=(post or {}).get("topic_category") or (post or {}).get("category") or "",
            style=(post or {}).get("style") or "",
            signal=meta.get("image_style") or "",
            detail={
                "image_id": (image or {}).get("id"),
                "variation_index": variation_index,
                "aspect": meta.get("aspect"),
                "relevance": (meta.get("relevance") or {}).get("score"),
            },
        )

    # ------------------------------------------------------------- aggregation
    def preferences(self, category: str | None = None) -> dict:
        rows = self.db.list_feedback(category=category or None, limit=2000)
        styles: dict[str, dict] = {}
        categories: dict[str, dict] = {}
        ratings: list[int] = []
        headline_changes: list[float] = []
        body_changes: list[float] = []
        reject_reasons: list[str] = []

        def bucket(store: dict, key: str) -> dict:
            return store.setdefault(
                key or "unknown",
                {"key": key or "unknown", "samples": 0, "approved": 0,
                 "rejected": 0, "edits": 0, "rating_sum": 0, "rating_count": 0},
            )

        for row in rows:
            kind = row.get("kind")
            style = bucket(styles, row.get("style") or "unknown")
            cat = bucket(categories, row.get("category") or "unknown")
            style["samples"] += 1
            cat["samples"] += 1
            if kind == KIND_APPROVE:
                style["approved"] += 1
                cat["approved"] += 1
            elif kind == KIND_REJECT:
                style["rejected"] += 1
                cat["rejected"] += 1
                detail = row.get("detail") or {}
                if detail.get("reason"):
                    reject_reasons.append(truncate(str(detail["reason"]), 160))
            elif kind == KIND_EDIT:
                style["edits"] += 1
                detail = row.get("detail") or {}
                if detail.get("headline_change") is not None:
                    headline_changes.append(float(detail["headline_change"]))
                if detail.get("body_change") is not None:
                    body_changes.append(float(detail["body_change"]))
            elif kind == KIND_VERSION_SELECT:
                style["approved"] += 0.5
                cat["approved"] += 0.5
            elif kind == KIND_IMAGE_SELECT:
                if row.get("signal"):
                    styles.setdefault(
                        row["signal"],
                        {"key": row["signal"], "samples": 0, "approved": 0,
                         "rejected": 0, "edits": 0, "rating_sum": 0, "rating_count": 0},
                    )
            if row.get("rating"):
                ratings.append(int(row["rating"]))
                style["rating_sum"] += int(row["rating"])
                style["rating_count"] += 1

        def finalise(store: dict) -> list[dict]:
            items = list(store.values())
            for item in items:
                decided = item["approved"] + item["rejected"]
                item["approval_rate"] = (
                    round(item["approved"] / decided * 100, 1) if decided else None
                )
                item["average_rating"] = (
                    round(item["rating_sum"] / item["rating_count"], 2)
                    if item["rating_count"] else None
                )
                item.pop("rating_sum", None)
            items.sort(
                key=lambda item: (item["approval_rate"] or 0, item["samples"]),
                reverse=True,
            )
            return items

        style_rows = finalise(styles)
        category_rows = finalise(categories)
        min_samples = max(1, int(settings.get("learning.min_samples", 3)))
        reliable = [row for row in style_rows if row["samples"] >= min_samples]
        return {
            "category": category,
            "samples": len(rows),
            "min_samples": min_samples,
            "styles": style_rows,
            "categories": category_rows,
            "preferred_styles": [row["key"] for row in reliable[:3]],
            "ratings": {
                "count": len(ratings),
                "average": round(sum(ratings) / len(ratings), 2) if ratings else None,
            },
            "edit_behaviour": {
                "headline_change": round(sum(headline_changes) / len(headline_changes), 3)
                if headline_changes else None,
                "body_change": round(sum(body_changes) / len(body_changes), 3)
                if body_changes else None,
                "edits": len(headline_changes),
            },
            "reject_reasons": reject_reasons[:5],
            "counts": self._counts(rows),
        }

    @staticmethod
    def _counts(rows: list[dict]) -> dict:
        counts = {kind: 0 for kind in KINDS}
        for row in rows:
            counts[row.get("kind")] = counts.get(row.get("kind"), 0) + 1
        return counts

    # ------------------------------------------------------------------- hints
    def style_hint(self, category: str | None = None) -> str:
        """Short Vietnamese guidance for the writer prompt, or '' when unsure."""
        if not settings.get("learning.enabled", True):
            return ""
        if not settings.get("learning.use_in_prompts", True):
            return ""
        prefs = self.preferences(category)
        min_samples = prefs["min_samples"]
        if prefs["samples"] < min_samples or not prefs["preferred_styles"]:
            return ""
        best = next(
            (row for row in prefs["styles"] if row["samples"] >= min_samples
             and (row["approval_rate"] or 0) >= 50),
            None,
        )
        if not best:
            return ""
        from .ai_writer import STYLE_GUIDES

        editors = STYLE_GUIDES.get(best["key"], "")
        label = best["key"]
        lines = [
            "KINH NGHIỆM BIÊN TẬP CỦA NGƯỜI DUYỆT (dữ liệu cục bộ):",
            f"- Kiểu viết '{label}' có tỉ lệ được duyệt {best['approval_rate']:.0f}% "
            f"trên {best['samples']} mẫu" + (f" ({editors})" if editors else ""),
        ]
        edits = prefs["edit_behaviour"]
        if edits["headline_change"] is not None and edits["headline_change"] >= 0.2:
            lines.append(
                f"- Người duyệt thường sửa tiêu đề (mức thay đổi trung bình "
                f"{edits['headline_change'] * 100:.0f}%): hãy viết tiêu đề cụ thể, sát dữ kiện."
            )
        if edits["body_change"] is not None and edits["body_change"] >= 0.25:
            lines.append(
                f"- Thân bài thường bị chỉnh ({edits['body_change'] * 100:.0f}%): "
                "giữ câu ngắn, tránh lan man."
            )
        if prefs["reject_reasons"]:
            lines.append("- Lý do bị từ chối gần đây: " + "; ".join(prefs["reject_reasons"][:3]))
        lines.append(
            "- Đây chỉ là gợi ý từ lịch sử duyệt bài; vẫn phải bám đúng báo cáo nghiên cứu."
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------- reset
    def reset(self, kind: str | None = None, category: str | None = None) -> dict:
        if kind and kind not in KINDS:
            raise ValueError(f"Loại phản hồi không hợp lệ: {kind}")
        deleted = self.db.delete_feedback(kind=kind, category=category)
        self.db.add_log(
            f"Đã xóa {deleted} bản ghi lịch sử biên tập"
            + (f" (loại {kind})" if kind else "")
            + (f" (nhóm {category})" if category else ""),
            "warning", "learning",
        )
        return {"deleted": deleted, "kind": kind, "category": category}

    def summary(self) -> dict:
        all_prefs = self.preferences()
        return {
            "samples": all_prefs["samples"],
            "counts": all_prefs["counts"],
            "styles": all_prefs["styles"][:6],
            "categories": all_prefs["categories"][:6],
            "preferred_styles": all_prefs["preferred_styles"],
            "ratings": all_prefs["ratings"],
            "edit_behaviour": all_prefs["edit_behaviour"],
            "reject_reasons": all_prefs["reject_reasons"],
            "min_samples": all_prefs["min_samples"],
            "recent": self.db.list_feedback(limit=20),
            "privacy": (
                "Dữ liệu học tập chỉ nằm trong SQLite cục bộ. Không huấn luyện lại model, "
                "không đọc dữ liệu Facebook riêng tư."
            ),
        }


service = EditorialLearning()
