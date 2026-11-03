"""Content calendar: slots, planning, scheduling limits and reminders."""

from __future__ import annotations

from datetime import timedelta

import pytest

from modules.content_calendar import ContentCalendar
from modules.utils import parse_datetime, utcnow


def future_slot(calendar: ContentCalendar, days: int = 7) -> str:
    return next(
        slot["scheduled_at"] for slot in calendar.slots(days)
        if parse_datetime(slot["scheduled_at"]) > utcnow()
    )


def calendar_with_posts(temp_db, statuses=("approved", "draft")):
    topic_id = temp_db.create_topic(
        {"title": "Chủ đề lịch", "category": "technology", "score": 70.0}
    )
    post_ids = []
    for index, status in enumerate(statuses):
        post_id = temp_db.create_post(
            {
                "topic_id": topic_id,
                "headline": f"Bài {index + 1}",
                "main_post": "Nội dung",
                "status": status,
            }
        )
        post_ids.append(post_id)
    return topic_id, post_ids


def test_slots_follow_configured_windows_and_days():
    calendar = ContentCalendar()
    slots = calendar.slots(7)
    config = calendar.config()

    assert len(slots) == len(config["days"]) * len(config["windows"])
    assert all(slot["weekday"] in config["days"] for slot in slots)
    assert slots[0]["scheduled_at"].endswith("+00:00")


def test_plan_prefers_approved_posts_and_flags_drafts(temp_db):
    _, (approved_id, draft_id) = calendar_with_posts(temp_db)
    plan = ContentCalendar(temp_db).plan(7)

    assert [item["post_id"] for item in plan["plan"]][:2] == [approved_id, draft_id]
    first = plan["plan"][0]
    assert first["needs_approval"] is False
    assert plan["plan"][1]["needs_approval"] is True
    assert plan["slots"]
    assert plan["notes"]


def test_plan_does_not_reuse_a_scheduled_slot(temp_db):
    _, post_ids = calendar_with_posts(temp_db, statuses=("approved", "draft"))
    calendar = ContentCalendar(temp_db)
    plan = calendar.plan(7)
    slot = plan["plan"][0]

    calendar.schedule(post_ids[0], slot["scheduled_at"])
    refreshed = calendar.plan(7)

    assert post_ids[0] not in [item["post_id"] for item in refreshed["plan"]]
    assert refreshed["plan"][0]["scheduled_at"] != slot["scheduled_at"]


def test_scheduling_rejects_past_times(temp_db):
    _, (post_id,) = calendar_with_posts(temp_db, statuses=("draft",))
    calendar = ContentCalendar(temp_db)

    with pytest.raises(ValueError) as excinfo:
        calendar.schedule(post_id, (utcnow() - timedelta(days=1)).isoformat())
    assert "đã qua" in str(excinfo.value)


def test_daily_limit_is_enforced(temp_db):
    _, post_ids = calendar_with_posts(temp_db, statuses=("approved", "approved", "approved"))
    calendar = ContentCalendar(temp_db)
    config = calendar.config()
    assert config["max_per_day"] <= 6
    day_slot = future_slot(calendar)

    calendar.schedule(post_ids[0], day_slot)
    second = temp_db.list_calendar_entries()[0]["scheduled_at"]

    with pytest.raises(ValueError) as excinfo:
        for post_id in post_ids[1:]:
            calendar.schedule(post_id, second)
    assert "giới hạn" in str(excinfo.value)


def test_reminders_list_old_drafts(temp_db):
    topic_id, (post_id,) = calendar_with_posts(temp_db, statuses=("draft",))
    old = (utcnow() - timedelta(hours=48)).replace(microsecond=0).isoformat()
    temp_db.execute(
        "UPDATE posts SET created_at = ?, updated_at = ? WHERE id = ?", (old, old, post_id)
    )

    reminders = ContentCalendar(temp_db).reminders()

    assert reminders["pending_count"] >= 1
    assert any(row["id"] == post_id for row in reminders["pending_approval"])
    assert "chờ duyệt" in reminders["message"]


def test_suggested_times_fall_back_to_settings(temp_db):
    times = ContentCalendar(temp_db).suggested_times()
    assert times
    assert all(item["source"] == "settings" for item in times)


def test_suggested_times_use_measured_engagement(temp_db):
    _, (post_id,) = calendar_with_posts(temp_db, statuses=("approved",))
    for index in range(3):
        temp_db.add_metrics(
            {
                "post_id": post_id,
                "source": "manual",
                "reach": 1000,
                "engagement": 120 + index,
                "published_at": f"2026-10-{20 + index:02d}T02:00:00+00:00",
            }
        )

    times = ContentCalendar(temp_db).suggested_times()

    assert times[0]["source"] == "metrics"
    assert times[0]["samples"] == 3
    assert times[0]["local_time"].endswith(":00")


def test_schedule_and_reschedule_round_trip(temp_db):
    _, (post_id,) = calendar_with_posts(temp_db, statuses=("draft",))
    calendar = ContentCalendar(temp_db)
    slot = future_slot(calendar)

    entry = calendar.schedule(post_id, slot, note="thử")
    entries = temp_db.list_calendar_entries()
    assert entries and entries[0]["post_id"] == post_id

    new_slot = next(
        candidate["scheduled_at"] for candidate in calendar.slots(7)
        if parse_datetime(candidate["scheduled_at"]) > parse_datetime(slot)
    )
    calendar.reschedule(entry["id"], new_slot)
    assert temp_db.list_calendar_entries()[0]["scheduled_at"] == new_slot

    calendar.remove(entry["id"])
    assert temp_db.list_calendar_entries() == []
