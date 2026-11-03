"""Content calendar: daily/weekly planning, scheduling and approval reminders.

This is a *planning* feature. It proposes slots and stores a schedule, but it
never publishes anything: approval stays manual and the calendar only reminds the
user which posts still need a decision.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from .config import settings
from .database import Database, db as default_db
from .utils import parse_datetime, utcnow, utcnow_iso

UTC = timezone.utc

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_INDEX = {name: index for index, name in enumerate(WEEKDAYS)}  # Mon=0

MIN_WINDOW_MINUTES = 15
DEFAULT_WINDOW = "08:00-09:00"


def _tz() -> timezone:
    offset = float(settings.get("calendar.utc_offset_hours", 7) or 0)
    return timezone(timedelta(hours=offset))


def _parse_window(value: str) -> tuple[int, int] | None:
    try:
        start, end = str(value).split("-", 1)
        sh, sm = [int(part) for part in start.strip().split(":")]
        eh, em = [int(part) for part in end.strip().split(":")]
    except (ValueError, AttributeError):
        return None
    start_minutes, end_minutes = sh * 60 + sm, eh * 60 + em
    if not (0 <= start_minutes < 24 * 60 and 0 <= end_minutes <= 24 * 60):
        return None
    if end_minutes - start_minutes < MIN_WINDOW_MINUTES:
        return None
    return start_minutes, end_minutes


def _minutes_to_label(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class ContentCalendar:
    def __init__(self, database: Database | None = None):
        self.db = database or default_db

    # ------------------------------------------------------------------ config
    def config(self) -> dict:
        cfg = settings.section("calendar")
        raw_days = cfg.get("days") or list(WEEKDAYS)
        days = [day for day in raw_days if day in WEEKDAY_INDEX]
        raw_windows = cfg.get("time_windows") or [DEFAULT_WINDOW]
        windows = [window for window in (_parse_window(w) for w in raw_windows) if window]
        return {
            "enabled": bool(cfg.get("enabled", True)),
            "offset_hours": float(cfg.get("utc_offset_hours", 7) or 0),
            "days": days or list(WEEKDAYS),
            "windows": windows or [_parse_window(DEFAULT_WINDOW)],
            "max_per_day": max(1, int(cfg.get("max_per_day", 2) or 2)),
            "categories": [c for c in (cfg.get("categories") or []) if c],
            "reminder_hours": max(1, int(cfg.get("reminder_hours", 24) or 24)),
        }

    def options(self) -> dict:
        return {
            "weekdays": list(WEEKDAYS),
            "config": self.config(),
            "limits": {"max_per_day": 6, "horizon_days": 28},
            "note": (
                "Lịch chỉ nhắc và sắp xếp; bạn vẫn phải duyệt và tự đăng bài trên Facebook."
            ),
        }

    # ------------------------------------------------------------------- slots
    def slots(self, days: int = 7, start: datetime | None = None) -> list[dict]:
        config = self.config()
        tz = _tz()
        begin = (start or utcnow()).astimezone(tz)
        out: list[dict] = []
        for offset in range(max(1, min(28, int(days)))):
            day = begin + timedelta(days=offset)
            weekday = WEEKDAYS[day.weekday()]
            if weekday not in config["days"]:
                continue
            for start_minutes, end_minutes in config["windows"]:
                local = day.replace(
                    hour=start_minutes // 60, minute=start_minutes % 60,
                    second=0, microsecond=0,
                )
                out.append(
                    {
                        "date": local.date().isoformat(),
                        "weekday": weekday,
                        "window": f"{_minutes_to_label(start_minutes)}-"
                                  f"{_minutes_to_label(end_minutes)}",
                        "local_time": _minutes_to_label(start_minutes),
                        "scheduled_at": local.astimezone(UTC).replace(microsecond=0).isoformat(),
                        "local_label": local.strftime("%d/%m %H:%M"),
                    }
                )
        return out

    # ---------------------------------------------------------------- planning
    def _candidates(self) -> list[dict]:
        return self.db.query(
            """SELECT p.*, t.category AS topic_category, t.score AS topic_score,
                      t.title AS topic_title,
                      (SELECT path FROM images i WHERE i.post_id = p.id AND i.status='done'
                       ORDER BY i.selected DESC, i.id DESC LIMIT 1) AS thumb
               FROM posts p LEFT JOIN topics t ON t.id = p.topic_id
               WHERE p.status IN ('draft', 'approved')
               ORDER BY CASE p.status WHEN 'approved' THEN 0 ELSE 1 END,
                        COALESCE(t.score, 0) DESC, p.id DESC"""
        )

    def _existing(self) -> list[dict]:
        return self.db.list_calendar_entries()

    def plan(self, days: int = 7) -> dict:
        config = self.config()
        days = max(1, min(28, int(days or 7)))
        horizon_start = utcnow().replace(microsecond=0)
        horizon_end = horizon_start + timedelta(days=days)
        # Never propose a slot that has already passed (today's morning window, etc.).
        slots = [
            slot for slot in self.slots(days, horizon_start)
            if (parse_datetime(slot["scheduled_at"]) or horizon_start) > horizon_start
        ]
        entries = [
            entry for entry in self._existing()
            if (parse_datetime(entry.get("scheduled_at")) or horizon_start) <= horizon_end
        ]
        taken = {entry.get("scheduled_at") for entry in entries}
        scheduled_posts = {entry.get("post_id") for entry in entries}
        per_day: dict[str, int] = {}
        for entry in entries:
            parsed = parse_datetime(entry.get("scheduled_at"))
            if not parsed:
                continue
            date = parsed.astimezone(_tz()).date().isoformat()
            per_day[date] = per_day.get(date, 0) + 1

        free = [
            slot for slot in slots
            if slot["scheduled_at"] not in taken
            and per_day.get(slot["scheduled_at"][:10], 0) < config["max_per_day"]
        ]
        categories = self._candidates()
        preferred = set(config["categories"])

        def priority(post: dict) -> tuple:
            category = post.get("topic_category") or "general"
            return (
                0 if post.get("status") == "approved" else 1,
                0 if preferred and category in preferred else 1,
                -(float(post.get("topic_score") or 0)),
                -int(post.get("id") or 0),
            )

        candidates = [
            post for post in categories
            if post.get("id") not in scheduled_posts
        ]
        candidates.sort(key=priority)

        planned: list[dict] = []
        for post in candidates:
            if not free:
                break
            slot = free.pop(0)
            date = slot["scheduled_at"][:10]
            if per_day.get(date, 0) >= config["max_per_day"]:
                continue
            per_day[date] = per_day.get(date, 0) + 1
            category = post.get("topic_category") or "general"
            reasons = []
            if post.get("status") == "approved":
                reasons.append("Đã duyệt, sẵn sàng chờ đăng")
            else:
                reasons.append("Bản nháp — cần duyệt trước khi đăng")
            if preferred:
                reasons.append(
                    "Thuộc nhóm ưu tiên" if category in preferred
                    else "Ngoài nhóm ưu tiên (xếp sau)"
                )
            reasons.append(f"Slot {slot['window']} ngày {slot['date']}")
            planned.append(
                {
                    "post_id": post.get("id"),
                    "headline": post.get("headline"),
                    "status": post.get("status"),
                    "category": category,
                    "topic_id": post.get("topic_id"),
                    "topic_title": post.get("topic_title"),
                    "thumb": post.get("thumb"),
                    "scheduled_at": slot["scheduled_at"],
                    "local_time": slot["local_time"],
                    "local_label": slot["local_label"],
                    "slot": slot["window"],
                    "needs_approval": post.get("status") != "approved",
                    "reason": " · ".join(reasons),
                    "persisted": False,
                }
            )

        unscheduled = [
            {
                "post_id": post.get("id"),
                "headline": post.get("headline"),
                "status": post.get("status"),
                "category": post.get("topic_category") or "general",
            }
            for post in candidates
            if post.get("id") not in {item["post_id"] for item in planned}
        ]

        distribution = self._distribution(planned)
        reminders = self.reminders(planned=planned)
        return {
            "horizon_days": days,
            "generated_at": utcnow_iso(),
            "config": config,
            "slots": slots,
            "plan": planned,
            "entries": entries,
            "unscheduled": unscheduled,
            "distribution": distribution,
            "reminders": reminders,
            "suggested_times": self.suggested_times(),
            "notes": [
                "Đây là kế hoạch và nhắc nhở. Ứng dụng không tự đăng Facebook.",
                "Slot đã bị chiếm bởi lịch đã lưu sẽ không được gợi ý lại.",
            ],
        }

    @staticmethod
    def _distribution(items: Iterable[dict]) -> dict:
        categories: dict[str, int] = {}
        days: dict[str, int] = {}
        for item in items:
            categories[item["category"]] = categories.get(item["category"], 0) + 1
            date = (item.get("scheduled_at") or "")[:10]
            days[date] = days.get(date, 0) + 1
        return {
            "categories": [
                {"category": key, "count": value}
                for key, value in sorted(categories.items(), key=lambda kv: -kv[1])
            ],
            "days": [
                {"date": key, "count": value} for key, value in sorted(days.items())
            ],
        }

    # -------------------------------------------------------------- scheduling
    def schedule(self, post_id: int, scheduled_at: str, note: str = "") -> dict:
        post = self.db.get_post(post_id)
        if not post:
            raise ValueError("Không tìm thấy bài viết")
        parsed = parse_datetime(scheduled_at)
        if not parsed:
            raise ValueError("Thời điểm không hợp lệ (cần ISO 8601)")
        if parsed < utcnow() - timedelta(minutes=5):
            raise ValueError("Không thể lên lịch cho thời điểm đã qua")
        config = self.config()
        day_key = parsed.astimezone(_tz()).date().isoformat()
        same_day = []
        for entry in self._existing():
            existing_at = parse_datetime(entry.get("scheduled_at"))
            if not existing_at or entry.get("post_id") == post_id:
                continue
            if existing_at.astimezone(_tz()).date().isoformat() == day_key:
                same_day.append(entry)
        if len(same_day) >= config["max_per_day"]:
            raise ValueError(
                f"Ngày {day_key} đã có {len(same_day)} bài, giới hạn là "
                f"{config['max_per_day']} bài/ngày."
            )
        entry_id = self.db.add_calendar_entry(
            {
                "post_id": post_id,
                "scheduled_at": parse_datetime(scheduled_at).astimezone(UTC)
                .replace(microsecond=0).isoformat(),
                "slot": parsed.astimezone(_tz()).strftime("%H:%M"),
                "status": "planned",
                "note": note,
            }
        )
        self.db.add_log(
            f"Lên lịch bài #{post_id} lúc {scheduled_at} (chỉ nhắc, không tự đăng)",
            "info", "calendar",
        )
        return {"id": entry_id, **self.db.list_calendar_entries()[-1]} if entry_id else {}

    def reschedule(self, entry_id: int, scheduled_at: str, note: str | None = None) -> dict:
        parsed = parse_datetime(scheduled_at)
        if not parsed:
            raise ValueError("Thời điểm không hợp lệ (cần ISO 8601)")
        patch = {
            "scheduled_at": parsed.astimezone(UTC).replace(microsecond=0).isoformat(),
            "slot": parsed.astimezone(_tz()).strftime("%H:%M"),
        }
        if note is not None:
            patch["note"] = note
        self.db.update_calendar_entry(entry_id, patch)
        return {"ok": True, "entry_id": entry_id}

    def remove(self, entry_id: int) -> dict:
        self.db.delete_calendar_entry(entry_id)
        return {"ok": True, "entry_id": entry_id}

    # --------------------------------------------------------------- reminders
    def reminders(self, planned: Iterable[dict] | None = None) -> dict:
        config = self.config()
        now = utcnow()
        threshold = now - timedelta(hours=config["reminder_hours"])
        pending = self.db.query(
            "SELECT id, headline, topic_id, created_at FROM posts "
            "WHERE status = 'draft' AND COALESCE(updated_at, created_at) <= ? "
            "ORDER BY id DESC LIMIT 20",
            (threshold.replace(microsecond=0).isoformat(),),
        )
        overdue = []
        for entry in self._existing():
            scheduled = parse_datetime(entry.get("scheduled_at"))
            if not scheduled or scheduled > now:
                continue
            if entry.get("post_status") in ("approved", "exported"):
                continue
            overdue.append(entry)
        upcoming = [
            item for item in (planned or [])
            if parse_datetime(item.get("scheduled_at"))
            and parse_datetime(item["scheduled_at"]) <= now + timedelta(hours=config["reminder_hours"])
        ]
        return {
            "pending_approval": pending,
            "pending_count": len(pending),
            "overdue": overdue,
            "upcoming": upcoming,
            "reminder_hours": config["reminder_hours"],
            "message": (
                f"{len(pending)} bài chờ duyệt quá {config['reminder_hours']} giờ"
                + (f", {len(overdue)} bài đã qua giờ hẹn mà chưa duyệt" if overdue else "")
            ),
        }

    # ---------------------------------------------------------- time of day
    def suggested_times(self) -> list[dict]:
        """Prefer measured engagement by hour when a manual import exists."""
        rows = self.db.list_metrics(days=120)
        offset = float(settings.get("facebook_metrics.utc_offset_hours", 7) or 0)
        tz = timezone(timedelta(hours=offset))
        buckets: dict[int, list[float]] = {}
        for row in rows:
            published = parse_datetime(row.get("published_at") or row.get("imported_at"))
            if not published:
                continue
            hour = published.astimezone(tz).hour
            engagement = float(row.get("engagement") or 0) or (
                float(row.get("reactions") or 0) + float(row.get("comments") or 0)
                + float(row.get("shares") or 0)
            )
            buckets.setdefault(hour, []).append(engagement)
        measured = [
            {
                "hour": hour,
                "local_time": f"{hour:02d}:00",
                "samples": len(values),
                "average_engagement": round(sum(values) / len(values), 1),
                "source": "metrics",
            }
            for hour, values in buckets.items()
            if len(values) >= 3
        ]
        measured.sort(key=lambda item: -item["average_engagement"])
        if measured:
            return measured[:3]
        config = self.config()
        return [
            {
                "hour": start // 60,
                "local_time": _minutes_to_label(start),
                "samples": 0,
                "average_engagement": None,
                "source": "settings",
            }
            for start, _ in config["windows"]
        ]


service = ContentCalendar()
