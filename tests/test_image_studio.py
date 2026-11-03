"""Smart image studio: presets, prompt preparation and deterministic checks."""

from __future__ import annotations

import struct
import zlib

from modules import image_studio


def make_png(path, width: int = 1024, height: int = 1024, seed: int = 0):
    raw = b"".join(
        b"\x00" + bytes((x * 3 + y * 5 + seed) % 256 for x in range(width * 3))
        for y in range(height)
    )

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    payload = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(payload)
    return path


def test_aspect_presets_use_expected_dimensions():
    assert image_studio.resolve_aspect("1:1")["width"] == image_studio.resolve_aspect("1:1")["height"]
    portrait = image_studio.resolve_aspect("4:5")
    assert portrait["height"] > portrait["width"]
    wide = image_studio.resolve_aspect("16:9")
    assert wide["width"] > wide["height"]
    assert image_studio.resolve_aspect("nonsense")["aspect"] == "1:1"


def test_prepare_appends_style_suffix_and_dimensions():
    prepared = image_studio.prepare(
        "a cleanroom with machines", style="editorial_illustration", aspect="4:5",
        category="technology", extra_negative="news photo",
    )

    assert "editorial magazine illustration" in prepared["prompt"]
    assert "news photo" in prepared["negative_prompt"]
    assert prepared["width"] == 1024 and prepared["height"] == 1280
    assert prepared["aspect"] == "4:5"
    assert prepared["style"] == "editorial_illustration"


def test_auto_style_follows_the_category():
    finance = image_studio.prepare("gold bars", style="auto", category="finance")
    lifestyle = image_studio.prepare("family walking", style="auto", category="lifestyle")

    assert finance["style"] == image_studio.STYLE_INFOGRAPHIC
    assert lifestyle["style"] == image_studio.STYLE_PHOTO
    assert finance["style_source"] == "auto"


def test_recommendation_matches_category():
    result = image_studio.recommend("business", "business_impact")
    assert result["style"] == image_studio.STYLE_INFOGRAPHIC
    assert result["aspect"] == "16:9"
    assert result["kind"] == "rule_based"


def test_text_risk_is_higher_for_infographics_and_text_prompts():
    quiet = image_studio.text_artifact_risk("a calm forest at dawn", "editorial_illustration")
    risky = image_studio.text_artifact_risk(
        'infographic with the title "GDP" and labels, logo', "infographic"
    )

    assert quiet["risk"] == "low"
    assert risky["risk"] in ("medium", "high")
    assert risky["kind"] == "heuristic"
    assert risky["cues"]


def test_check_image_reads_dimensions_and_scores_clean_image(tmp_path):
    path = make_png(tmp_path / "clean.png", 1024, 1024)
    result = image_studio.check_image(
        str(path), prompt="editorial illustration of a data center",
        aspect="1:1", style="editorial_illustration",
    )

    assert result["width"] == 1024 and result["height"] == 1024
    assert result["verdict"] == "ok"
    assert result["score"] >= 90
    assert result["duplicate"]["is_duplicate"] is False
    statuses = {check["key"]: check["status"] for check in result["checks"]}
    assert statuses["file"] == "ok"
    assert statuses["dimensions"] == "ok"
    assert statuses["aspect"] == "ok"


def test_check_image_flags_wrong_aspect(tmp_path):
    path = make_png(tmp_path / "square.png", 800, 800)
    result = image_studio.check_image(str(path), aspect="16:9", prompt="clean shapes")

    statuses = {check["key"]: check["status"] for check in result["checks"]}
    assert statuses["aspect"] == "warn"
    assert result["verdict"] == "review"


def test_duplicate_detection_uses_content_hash(tmp_path):
    first = make_png(tmp_path / "a.png", 512, 512, seed=1)
    second = make_png(tmp_path / "b.png", 512, 512, seed=1)
    digest = image_studio.sha256_of(str(first))

    result = image_studio.check_image(
        str(second), aspect="1:1", known_hashes={digest: "ảnh #1"}
    )

    assert result["duplicate"]["is_duplicate"] is True
    assert result["duplicate"]["of"] == "ảnh #1"
    assert result["verdict"] == "reject"
    assert result["vision_recommended"] is True


def test_missing_file_is_reported_not_crashing():
    result = image_studio.check_image("output/images/does-not-exist.png")
    assert result["exists"] is False
    assert result["verdict"] == "reject"
    assert any(check["status"] == "fail" for check in result["checks"])


def test_hash_map_skips_the_image_being_checked(tmp_path):
    path = make_png(tmp_path / "c.png", 64, 64)
    images = [
        {"id": 1, "path": str(path), "headline": "Bài A", "meta": {}},
        {"id": 2, "path": str(path), "headline": "Bài B", "meta": {}},
    ]

    mapping = image_studio.hash_map(images, skip_id=2)
    assert len(mapping) == 1
    assert "Bài A" in next(iter(mapping.values()))
