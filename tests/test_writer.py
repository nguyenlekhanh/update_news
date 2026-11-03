"""Writer guards: hashtags, markdown cleanup, English prompts and number auditing."""

from __future__ import annotations

from modules.ai_writer import AIWriter


def test_hashtags_are_deduplicated_normalised_and_capped() -> None:
    tags = AIWriter._normalise_hashtags(
        ["#Chip", "chip", " Công Nghiệp ", "#Chip!", "", None], limit=3
    )
    assert tags == ["#Chip", "#CôngNghiệp"]
    assert len({tag.lower() for tag in tags}) == len(tags)


def test_hashtags_respect_the_limit() -> None:
    tags = AIWriter._normalise_hashtags("#a #b #c #d #e #f", limit=2)
    assert tags == ["#a", "#b"]


def test_strip_markdown_removes_headings_and_bold() -> None:
    text = "## Tiêu đề\n\n**Đoạn một** nội dung.\n\n\n\nĐoạn hai."
    cleaned = AIWriter._strip_markdown(text)
    assert "##" not in cleaned and "**" not in cleaned
    assert "\n\n\n" not in cleaned
    assert cleaned.startswith("Tiêu đề")


def test_english_prompt_detection() -> None:
    assert AIWriter._is_english_prompt(
        "editorial illustration of a semiconductor cleanroom, blue palette, soft rim light"
    )
    assert not AIWriter._is_english_prompt(
        "hình minh họa nhà máy chip tại Bắc Ninh với công nhân và máy móc công nghệ cao"
    )
    assert not AIWriter._is_english_prompt("too short")


def test_audit_numbers_flags_numbers_not_in_research() -> None:
    writer = AIWriter.__new__(AIWriter)  # helpers below do not touch instance state
    evidence = "Nhà máy có vốn 1.000 tỷ đồng tại Bắc Ninh."
    text = "Nhà máy có vốn 1.000 tỷ đồng. Dự kiến tạo 5.000 việc làm."
    _, flags = writer._audit_numbers(text, evidence)
    assert len(flags) == 1
    assert "5000" in flags[0]["detail"]

    _, clean_flags = writer._audit_numbers("Vốn đầu tư 1.000 tỷ đồng.", evidence)
    assert clean_flags == []


def test_copied_sentences_detects_verbatim_source_text() -> None:
    verbatim = "Tổng vốn đầu tư của dự án là 1.000 tỷ đồng và sẽ hoàn thành trong năm 2027"
    evidence = f"{verbatim}. Một số thông tin khác."
    text = f"{verbatim}. Đây là câu do người viết tự tạo ra cho bài đăng Facebook."
    copied = AIWriter._copied_sentences(text, evidence)
    assert copied == [verbatim + "."]
