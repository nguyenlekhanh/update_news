"""Small utilities that the collectors and the writer depend on."""

from __future__ import annotations

import pytest

from modules.ollama_client import _extract_json
from modules.rss_collector import _clean
from modules.utils import (
    canonical_url,
    content_tokens,
    dedupe_key,
    domain_of,
    is_vietnamese,
    parse_datetime,
    slugify,
    strip_accents,
    title_similarity,
    truncate,
)


def test_canonical_url_drops_tracking_parameters() -> None:
    url = "http://www.VnExpress.net/bai-viet/?utm_source=fb&fbclid=abc&id=7"
    assert canonical_url(url) == "https://vnexpress.net/bai-viet?id=7"
    assert domain_of(url) == "vnexpress.net"


def test_truncate_and_slugify() -> None:
    assert truncate("một hai ba bốn", 9).endswith("…")
    assert truncate("ngắn", 50) == "ngắn"
    assert slugify("FPT vận hành nhà máy chip!") == "fpt-van-hanh-nha-may-chip"


def test_vietnamese_detection_and_folding() -> None:
    assert is_vietnamese("Nhà máy chip tại Bắc Ninh")
    assert not is_vietnamese("Chip factory in Bac Ninh")
    assert strip_accents("Đường phố Hà Nội") == "Duong pho Ha Noi"


def test_titles_from_multiple_sources_are_similar() -> None:
    a = "FPT vận hành thử nghiệm nhà máy chip đầu tiên do người Việt làm chủ"
    b = "FPT chạy thử nghiệm nhà máy chip đầu tiên của người Việt"
    assert title_similarity(a, b) > 0.4
    assert title_similarity(a, "Giá vàng hôm nay tăng mạnh") < 0.2
    assert dedupe_key(a) == dedupe_key(a)
    assert dedupe_key(a) != dedupe_key(b)


def test_content_tokens_drop_stopwords() -> None:
    tokens = content_tokens("Nhà máy chip của FPT và người Việt")
    assert "chip" in tokens and "fpt" in tokens
    assert "cua" not in tokens and "va" not in tokens


def test_parse_datetime_handles_common_formats() -> None:
    assert parse_datetime("2026-11-01T02:00:00Z").year == 2026
    assert parse_datetime("Wed, 01 Nov 2026 10:20:30 GMT").month == 11
    assert parse_datetime("not a date") is None
    assert parse_datetime("") is None


def test_rss_clean_strips_html_and_entities() -> None:
    assert _clean("<p>Tin&nbsp;mới &amp; nóng</p>") == "Tin mới & nóng"


def test_extract_json_handles_code_fences_and_chatter() -> None:
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('Kết quả: {"a": [1, 2]} mong muốn') == {"a": [1, 2]}
    with pytest.raises(ValueError):
        _extract_json("không có json")
