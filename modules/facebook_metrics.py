"""Facebook Page performance: optional official Graph API + manual import.

Two ways in, both user-controlled:

* **Manual import** — paste CSV/JSON collected from Facebook Page Insights. This
  always works, even with no API permission.
* **Official Graph API** — only with an authorized Page access token from the
  environment (``FACEBOOK_ACCESS_TOKEN``). Nothing is scraped, no private data is
  read, and likes/comments/shares are never automated.

Nothing here publishes, schedules or interacts with Facebook.
"""

from __future__ import annotations

import csv
import io
from datetime import timedelta, timezone
from typing import Iterable

import httpx

from .config import get_secret, settings, secret_status
from .database import Database, db as default_db
from .utils import parse_datetime, truncate, utcnow, utcnow_iso

GRAPH_BASE = "https://graph.facebook.com"
NUMERIC_FIELDS = ("reach", "engagement", "reactions", "comments", "shares", "link_clicks")
FIELD_ALIASES = {
    "post_id": {"post_id", "postid", "id", "bài viết", "post"},
    "reach": {"reach", "post_impressions_unique", "impressions", "tiếp cận"},
    "engagement": {"engagement", "engaged_users", "post_engaged_users", "tương tác"},
    "reactions": {"reactions", "post_reactions_by_type_total", "cảm xúc"},
    "comments": {"comments", "comment", "bình luận"},
    "shares": {"shares", "share", "chia sẻ"},
    "link_clicks": {"link_clicks", "clicks", "post_clicks", "click liên kết"},
    "published_at": {"published_at", "published", "date", "ngày đăng", "time"},
}
METRIC_MAP = {
    "post_impressions_unique": "reach",
    "post_engaged_users": "engagement",
    "post_reactions_by_type_total": "reactions",
    "post_clicks": "link_clicks",
}
MAX_IMPORT_ROWS = 500


def _empty_totals() -> dict:
    return {field: 0 for field in NUMERIC_FIELDS}


class FacebookMetrics:
    def __init__(self, database: Database | None = None):
        self.db = database or default_db

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        token = bool(get_secret("facebook"))
        page_ids = settings.get("facebook.page_ids", []) or []
        enabled = bool(settings.get("facebook_metrics.enabled", False))
        return {
            "enabled": enabled,
            "token_present": token,
            "page_ids": page_ids,
            "graph_version": settings.get("facebook_metrics.graph_version", "v21.0"),
            "configured": bool(token and page_ids),
            "api_available": bool(token and page_ids and enabled),
            "manual_import": True,
            "reason": (
                "Chưa có FACEBOOK_ACCESS_TOKEN cho Page đã cấp quyền; dùng nhập CSV thủ công."
                if not token else
                "Đã có token nhưng chưa cấu hình Page ID."
                if not page_ids else
                "Đã cấu hình; chỉ đọc chỉ số Page được cấp quyền."
            ),
            "privacy": (
                "Không đọc dữ liệu Facebook riêng tư, không scrape, không tự động thích/"
                "bình luận/chia sẻ."
            ),
        }

    # ------------------------------------------------------------------ import
    @staticmethod
    def _normalise_row(row: dict) -> dict:
        lowered = {
            str(key).strip().lower(): value
            for key, value in (row or {}).items()
            if key is not None
        }
        out: dict = {}
        for field, aliases in FIELD_ALIASES.items():
            for alias in aliases:
                if alias in lowered and str(lowered[alias]).strip() != "":
                    out[field] = lowered[alias]
                    break
        return out

    @staticmethod
    def _to_int(value, field: str) -> int:
        if value in (None, ""):
            return 0
        text = str(value).strip().replace(",", "").replace(".", "")
        try:
            number = int(float(text))
        except (TypeError, ValueError):
            raise ValueError(f"Giá trị '{field}' không phải số: {value}") from None
        if number < 0:
            raise ValueError(f"Giá trị '{field}' không được âm")
        return number

    def import_rows(self, rows: Iterable[dict], source: str = "manual",
                    default_published_at: str | None = None) -> dict:
        if source not in ("manual", "graph"):
            raise ValueError("Nguồn số liệu phải là 'manual' hoặc 'graph'")
        stored, skipped, errors = [], [], []
        rows = list(rows or [])
        if not rows:
            raise ValueError("Không có dòng dữ liệu nào để nhập")
        if len(rows) > MAX_IMPORT_ROWS:
            raise ValueError(f"Tối đa {MAX_IMPORT_ROWS} dòng mỗi lần nhập")

        for index, raw in enumerate(rows, start=1):
            row = self._normalise_row(raw)
            post_id = str(row.get("post_id") or "").strip()
            if not post_id.isdigit():
                errors.append(f"Dòng {index}: thiếu post_id hợp lệ")
                continue
            post = self.db.get_post(int(post_id))
            if not post:
                errors.append(f"Dòng {index}: không tìm thấy bài #{post_id}")
                continue
            try:
                payload = {
                    "post_id": int(post_id),
                    "source": source,
                    **{field: self._to_int(row.get(field), field) for field in NUMERIC_FIELDS},
                }
            except ValueError as exc:
                errors.append(f"Dòng {index}: {exc}")
                continue
            published = row.get("published_at") or default_published_at
            parsed = parse_datetime(published)
            payload["published_at"] = (
                parsed.replace(microsecond=0).isoformat()
                if parsed
                else (post.get("approved_at") or post.get("created_at") or utcnow_iso())
            )
            payload["meta"] = {"imported_from": source, "post_headline": post.get("headline")}
            metrics_id = self.db.add_metrics(payload)
            stored.append({"id": metrics_id, **payload})

        if errors:
            self.db.add_log(
                f"Nhập số liệu Facebook: {len(errors)} dòng lỗi", "warning", "metrics",
                {"errors": errors[:10]},
            )
        if stored:
            self.db.add_log(
                f"Đã nhập {len(stored)} dòng số liệu Facebook ({source})",
                "info", "metrics",
            )
        return {
            "stored": len(stored),
            "errors": errors,
            "skipped": skipped,
            "source": source,
            "items": stored,
        }

    def import_csv(self, text: str, default_published_at: str | None = None) -> dict:
        content = (text or "").strip()
        if not content:
            raise ValueError("Nội dung CSV rỗng")
        try:
            reader = csv.DictReader(io.StringIO(content))
            rows = list(reader)
        except csv.Error as exc:
            raise ValueError(f"CSV không hợp lệ: {exc}") from None
        if not rows:
            raise ValueError("CSV không có dòng dữ liệu")
        return self.import_rows(rows, "manual", default_published_at)

    # ------------------------------------------------------------------- graph
    def fetch_graph(self, post_id: int, external_id: str, published_at: str | None = None) -> dict:
        """Read official Page post insights for one authorized post."""
        status = self.status()
        if not status["token_present"]:
            raise ValueError(
                "Chưa cấu hình FACEBOOK_ACCESS_TOKEN. Hãy nhập CSV thủ công hoặc "
                "thêm token Page đã được cấp quyền read_insights."
            )
        if not status["page_ids"]:
            raise ValueError("Chưa cấu hình Page ID trong cài đặt Facebook.")
        if not status["enabled"]:
            raise ValueError(
                "Tính năng số liệu Facebook đang tắt (facebook_metrics.enabled)."
            )
        post = self.db.get_post(post_id)
        if not post:
            raise ValueError("Không tìm thấy bài viết")
        external_id = (external_id or "").strip()
        if not external_id:
            raise ValueError("Thiếu ID bài viết trên Facebook")

        version = settings.get("facebook_metrics.graph_version", "v21.0")
        token = get_secret("facebook")
        params = {
            "metric": ",".join(METRIC_MAP),
            "access_token": token,
        }
        url = f"{GRAPH_BASE}/{version}/{external_id}/insights"
        try:
            with httpx.Client(timeout=25.0) as client:
                response = client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ValueError(f"Không kết nối được Graph API: {exc}") from None
        if response.status_code >= 400:
            message = "Graph API từ chối yêu cầu"
            try:
                error = response.json().get("error") or {}
                message = error.get("message") or message
            except ValueError:
                pass
            raise ValueError(f"{message} (HTTP {response.status_code})")

        payload = {}
        try:
            data = response.json().get("data") or []
        except ValueError:
            raise ValueError("Graph API trả về dữ liệu không hợp lệ") from None
        values: dict[str, int] = {}
        for item in data:
            name = item.get("name")
            field = METRIC_MAP.get(name)
            if not field:
                continue
            entries = item.get("values") or []
            if not entries:
                continue
            value = entries[-1].get("value")
            if isinstance(value, dict):
                values[field] = sum(int(v or 0) for v in value.values())
            else:
                try:
                    values[field] = int(value or 0)
                except (TypeError, ValueError):
                    continue
        if not values:
            raise ValueError("Graph API không trả về chỉ số nào cho bài viết này")

        row = {
            "post_id": post_id,
            **{field: values.get(field, 0) for field in NUMERIC_FIELDS},
            "published_at": published_at
            or post.get("approved_at") or post.get("created_at") or utcnow_iso(),
            "meta": {"imported_from": "graph", "external_id": external_id},
        }
        result = self.import_rows([row], "graph")
        return {
            **result,
            "external_id": external_id,
            "metrics_received": sorted(values),
            "requested_from": url,
        }

    # ---------------------------------------------------------------- analysis
    def analysis(self, days: int = 90) -> dict:
        days = max(7, min(365, int(days or 90)))
        rows = self.db.list_metrics(days=days)
        offset = float(settings.get("facebook_metrics.utc_offset_hours", 7) or 0)
        tz = timezone(timedelta(hours=offset))

        totals = _empty_totals()
        for row in rows:
            for field in NUMERIC_FIELDS:
                totals[field] += int(row.get(field) or 0)

        def engagement_of(row: dict) -> float:
            direct = float(row.get("engagement") or 0)
            derived = float(row.get("reactions") or 0) + float(row.get("comments") or 0) \
                + float(row.get("shares") or 0)
            return direct or derived

        def rate_of(row: dict) -> float | None:
            reach = float(row.get("reach") or 0)
            if reach <= 0:
                return None
            return round(engagement_of(row) / reach * 100, 2)

        def group(key_fn) -> list[dict]:
            buckets: dict[str, dict] = {}
            for row in rows:
                key = key_fn(row) or "—"
                bucket = buckets.setdefault(
                    key, {"key": key, "posts": 0, "reach": 0, "engagement": 0,
                          "reactions": 0, "comments": 0, "shares": 0, "clicks": 0}
                )
                bucket["posts"] += 1
                bucket["reach"] += int(row.get("reach") or 0)
                bucket["engagement"] += int(engagement_of(row))
                bucket["reactions"] += int(row.get("reactions") or 0)
                bucket["comments"] += int(row.get("comments") or 0)
                bucket["shares"] += int(row.get("shares") or 0)
                bucket["clicks"] += int(row.get("link_clicks") or 0)
            items = list(buckets.values())
            for item in items:
                item["engagement_rate"] = (
                    round(item["engagement"] / item["reach"] * 100, 2)
                    if item["reach"] else None
                )
            items.sort(key=lambda item: (item["engagement"], item["reach"]), reverse=True)
            return items

        def hour_of(row: dict) -> str | None:
            published = parse_datetime(row.get("published_at") or row.get("imported_at"))
            return f"{published.astimezone(tz).hour:02d}:00" if published else None

        best = {
            "topics": group(lambda row: row.get("topic_title") or row.get("headline"))[:8],
            "headlines": group(lambda row: truncate(row.get("headline") or "", 60))[:8],
            "styles": group(lambda row: row.get("style") or "unknown")[:8],
            "categories": group(lambda row: row.get("category") or "general")[:8],
            "images": group(lambda row: "có ảnh" if row.get("thumb") else "không ảnh")[:4],
            "hours": sorted(group(hour_of), key=lambda item: item["key"])[:24],
        }

        sample = len(rows)
        return {
            "window_days": days,
            "generated_at": utcnow_iso(),
            "sample_size": sample,
            "totals": totals,
            "averages": {
                field: round(value / sample, 1) if sample else None
                for field, value in totals.items()
            },
            "average_engagement_rate": (
                round(sum(rate_of(row) or 0 for row in rows) / sample, 2) if sample else None
            ),
            "best": best,
            "best_hours": [
                item for item in sorted(
                    best["hours"], key=lambda entry: entry["engagement"], reverse=True
                )[:3]
            ],
            "recent": rows[:20],
            "kind": "measured" if sample else "unavailable",
            "disclaimer": (
                "Chỉ số lấy từ dữ liệu bạn nhập tay hoặc Graph API chính thức của Page đã "
                "cấp quyền. Không suy đoán khi thiếu dữ liệu."
            ),
            "status": self.status(),
        }

    def post_metrics(self, post_id: int) -> dict:
        rows = self.db.list_metrics(post_id=post_id)
        return {
            "post_id": post_id,
            "items": rows,
            "totals": {
                field: sum(int(row.get(field) or 0) for row in rows)
                for field in NUMERIC_FIELDS
            },
        }


service = FacebookMetrics()
