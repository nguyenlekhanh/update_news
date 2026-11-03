"""Storage and export: schema round-trips plus export files with source traceability."""

from __future__ import annotations

import csv
import io
import json

import pytest

import modules.exporter as exporter_module
from modules.config import DEFAULT_SETTINGS, Settings
from modules.exporter import DISCLOSURE, Exporter
from modules.utils import dedupe_key


def _topic(db) -> int:
    return db.create_topic(
        {
            "title": "FPT vận hành thử nghiệm nhà máy chip đầu tiên",
            "title_norm": dedupe_key("FPT vận hành thử nghiệm nhà máy chip đầu tiên"),
            "category": "technology",
            "score": 82.5,
            "primary_url": "https://vnexpress.net/fpt-nha-may-chip",
            "source_count": 2,
            "rationale": "2 nguồn độc lập, cập nhật 3 giờ trước",
        }
    )


def _post(db, topic_id: int, status: str = "approved") -> int:
    return db.create_post(
        {
            "topic_id": topic_id,
            "headline": "FPT chạy thử nhà máy chip đầu tiên do người Việt làm chủ",
            "alt_headlines": ["Nhà máy chip của FPT đi vào thử nghiệm"],
            "main_post": "Nội dung bài đăng chính về nhà máy chip.",
            "short_post": "Bản ngắn.",
            "long_post": "Bản dài.",
            "hashtags": ["#FPT", "#Chip"],
            "status": status,
            "sources": [
                {"title": "FPT khởi công nhà máy chip", "url": "https://vnexpress.net/a"},
                {"title": "Vận hành thử nghiệm", "url": "https://tuoitre.vn/b"},
            ],
        }
    )


def test_topic_post_and_image_round_trip(temp_db) -> None:
    topic_id = _topic(temp_db)
    post_id = _post(temp_db, topic_id)

    topic = temp_db.get_topic(topic_id)
    assert topic["title"].startswith("FPT")
    assert topic["score"] == 82.5
    assert topic["posts"][0]["id"] == post_id

    image_id = temp_db.add_image(
        {"post_id": post_id, "path": "output/images/x.png", "prompt": "cleanroom", "seed": 1}
    )
    temp_db.select_image(post_id, image_id)
    images = temp_db.list_images(post_id)
    assert len(images) == 1 and images[0]["selected"] == 1

    temp_db.update_post(post_id, {"status": "rejected", "notes": "Cần kiểm chứng thêm"})
    post = temp_db.get_post(post_id)
    assert post["status"] == "rejected"
    assert post["topic_title"].startswith("FPT")
    assert [s["url"] for s in post["sources"]][0].endswith("/a")


def test_delete_post_removes_images(temp_db) -> None:
    topic_id = _topic(temp_db)
    post_id = _post(temp_db, topic_id)
    temp_db.add_image({"post_id": post_id, "path": "output/images/y.png"})
    temp_db.delete_post(post_id)
    assert temp_db.get_post(post_id) is None
    assert temp_db.list_images(post_id) == []


def test_export_markdown_contains_sources_and_disclosure(temp_db, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(exporter_module, "EXPORT_DIR", tmp_path)
    topic_id = _topic(temp_db)
    _post(temp_db, topic_id)

    result = Exporter(temp_db).export("md", status="approved")
    assert result["count"] == 1
    content = (tmp_path / result["filename"]).read_text(encoding="utf-8")
    assert "FPT chạy thử nhà máy chip" in content
    assert "vnexpress.net/a" in content
    assert DISCLOSURE.split(".")[0][:20] in content


def test_export_json_and_csv_are_structured(temp_db, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(exporter_module, "EXPORT_DIR", tmp_path)
    topic_id = _topic(temp_db)
    _post(temp_db, topic_id)

    exporter = Exporter(temp_db)
    json_result = exporter.export("json", status="approved")
    payload = json.loads((tmp_path / json_result["filename"]).read_text(encoding="utf-8"))
    assert payload["count"] == 1
    assert payload["posts"][0]["topic"].startswith("FPT")
    assert payload["disclosure"] == DISCLOSURE

    csv_result = exporter.export("csv", status="approved")
    rows = list(csv.DictReader(io.StringIO((tmp_path / csv_result["filename"])
                                          .read_text(encoding="utf-8-sig"))))
    assert len(rows) == 1
    assert "FPT" in rows[0]["headline"]


def test_export_without_matching_posts_raises(temp_db, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(exporter_module, "EXPORT_DIR", tmp_path)
    with pytest.raises(ValueError):
        Exporter(temp_db).export("md", status="approved")
    with pytest.raises(ValueError):
        Exporter(temp_db).export("pdf", status=None)


def test_mark_exported_updates_status(temp_db, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(exporter_module, "EXPORT_DIR", tmp_path)
    topic_id = _topic(temp_db)
    post_id = _post(temp_db, topic_id)

    Exporter(temp_db).export("txt", status="approved", mark_exported=True)
    assert temp_db.get_post(post_id)["status"] == "exported"


def test_settings_persist_and_reset(tmp_path) -> None:
    path = tmp_path / "settings.json"
    settings = Settings(path)
    settings.update({"writer": {"style": "analytical"}, "pipeline": {"image_variations": 3}})

    reloaded = Settings(path)
    assert reloaded.get("writer.style") == "analytical"
    assert reloaded.get("pipeline.image_variations") == 3

    reloaded.reset()
    assert reloaded.get("writer.style") == DEFAULT_SETTINGS["writer"]["style"]
