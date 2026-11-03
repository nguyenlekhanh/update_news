"""Vietnamese Facebook content writer powered by local Ollama.

Every post is generated strictly from the stored research report. The writer
audits numbers, quotes, clickbait and copied sentences afterwards so the editor
can see exactly what needs a human check before publishing.
"""

from __future__ import annotations

import re
import time
from typing import Callable

from .config import settings
from .database import Database, db as default_db
from .errors import CancelledError, StageError
from .fact_checker import _contains_number, _fold, _numbers
from .ollama_client import OllamaClient, OllamaError, ollama
from .utils import count_words, truncate, utcnow_iso

WRITER_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "alt_headlines": {"type": "array", "items": {"type": "string"}},
        "main_post": {"type": "string"},
        "short_post": {"type": "string"},
        "long_post": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "hook": {"type": "string"},
        "closing_question": {"type": "string"},
        "image_prompt": {"type": "string"},
        "image_negative_prompt": {"type": "string"},
        "image_concept": {"type": "string"},
    },
    "required": [
        "headline", "alt_headlines", "main_post", "short_post", "long_post",
        "hashtags", "hook", "closing_question", "image_prompt", "image_concept",
    ],
}

ALT_HEADLINE_SCHEMA = {
    "type": "object",
    "properties": {"headlines": {"type": "array", "items": {"type": "string"}}},
    "required": ["headlines"],
}

LONG_POST_SCHEMA = {
    "type": "object",
    "properties": {"long_post": {"type": "string"}},
    "required": ["long_post"],
}

IMAGE_PROMPT_SCHEMA = {
    "type": "object",
    "properties": {
        "image_prompt": {"type": "string"},
        "negative_prompt": {"type": "string"},
        "concept_vi": {"type": "string"},
    },
    "required": ["image_prompt", "negative_prompt", "concept_vi"],
}

# Characters that only appear in Vietnamese text (SDXL ignores them).
_VI_DIACRITIC_RE = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯàáảãạằẳẵắặầẩẫấậèéẻẽẹềểễếệìíỉĩịòóỏõọồổỗốộờởỡớợùúủũụừửữứựỳýỷỹỵ]"
)

WRITER_SYSTEM = (
    "Bạn là biên tập viên mạng xã hội người Việt, viết bài Facebook tự nhiên, có giọng người thật. "
    "Bạn chỉ được dùng dữ kiện trong BÁO CÁO NGHIÊN CỨU được cung cấp. "
    "Không bịa số liệu, ngày tháng, câu nói, tên người hay sự kiện. "
    "Không biến thông tin chưa xác nhận thành sự thật; nếu dùng, phải nói rõ là chưa được xác nhận. "
    "Không sao chép nguyên câu từ bài báo nguồn; hãy diễn đạt lại bằng ngôn ngữ của bạn. "
    "Không viết mở bài sáo rỗng kiểu 'Trong thế giới ngày nay' hay 'Hãy cùng khám phá'."
)

STYLE_GUIDES = {
    "professional_journalism": "Giọng nhà báo chuyên nghiệp: thông tin trước, cảm xúc sau; câu gọn, dẫn dắt rõ ràng, không giật gân.",
    "friendly_storytelling": "Kể chuyện thân thiện như đang nói với bạn bè: mở đầu bằng một tình huống cụ thể, giọng gần gũi, có nhịp kể.",
    "informative": "Tập trung thông tin hữu ích: giải thích bối cảnh, con số, ý nghĩa; câu rõ ràng, dễ hiểu.",
    "entertaining": "Giọng vui vẻ, hóm hỉnh vừa phải, giữ đúng sự thật; được dùng ví von nhẹ nhưng không bịa chi tiết.",
    "emotional_storytelling": "Giọng cảm xúc, chạm vào con người và hệ quả; không kịch tính hóa quá mức, không thêm tình tiết không có trong nguồn.",
    "analytical": "Giọng phân tích: bóc tách nguyên nhân - hệ quả, so sánh các mặt, nêu rõ đâu là dữ kiện đã xác nhận.",
    "conversational": "Giọng trò chuyện trực tiếp với người đọc, dùng 'bạn'/'mình' tự nhiên, câu ngắn, nhịp nhanh.",
    "viral_short": "Ngắn, mạnh, đi thẳng vào điểm đáng chú ý nhất; không clickbait sai sự thật, không viết hoa quá nhiều.",
}

TONE_GUIDES = {
    "engaging": "cuốn hút nhưng tiết chế",
    "neutral": "trung tính, khách quan",
    "warm": "ấm áp, đồng cảm",
    "serious": "nghiêm túc, cẩn trọng",
    "humorous": "hài hước nhẹ, không đùa về nỗi đau của người khác",
}

AUDIENCE_GUIDES = {
    "general": "công chúng Facebook phổ thông 25-45 tuổi",
    "young": "người trẻ 18-30 tuổi, quan tâm xu hướng",
    "professionals": "người đi làm, quan tâm kinh tế - công nghệ",
    "parents": "phụ huynh, quan tâm giáo dục và gia đình",
    "tech": "người quan tâm công nghệ và AI",
    "business": "người làm kinh doanh, quản lý",
}

LENGTH_GUIDES = {
    "short": "khoảng 80-130 từ cho bài chính",
    "medium": "khoảng 150-230 từ cho bài chính",
    "long": "khoảng 280-420 từ cho bài chính",
    "very_long": "khoảng 450-650 từ cho bài chính",
}

# Three fixed versions offered for every story (Phase 2).
VERSION_PLAN = (
    {
        "variant": "A", "style": "professional_journalism", "length": "medium",
        "include_sources": True, "label_vi": "Tin tức chuyên nghiệp",
        "label_en": "Professional news",
    },
    {
        "variant": "B", "style": "conversational", "length": "medium",
        "include_sources": True, "label_vi": "Trò chuyện cuốn hút",
        "label_en": "Engaging conversation",
    },
    {
        "variant": "C", "style": "viral_short", "length": "short",
        "include_sources": False, "label_vi": "Ngắn, mạnh cho mạng xã hội",
        "label_en": "Short and punchy",
    },
)

CLICKBAIT_PATTERNS = [
    r"sốc\s*!", r"chấn động", r"không thể tin", r"bạn sẽ không tin",
    r"gây bão", r"dậy sóng", r"cực sốc", r"phải xem ngay", r"100%\s*thật",
]


class AIWriter:
    def __init__(self, database: Database | None = None, client: OllamaClient | None = None):
        self.db = database or default_db
        self.ollama = client or ollama

    # ------------------------------------------------------------------ public
    def write(
        self,
        topic_id: int,
        research_id: int | None = None,
        options: dict | None = None,
        should_cancel: Callable[[], bool] | None = None,
        progress: Callable[[int, str], None] | None = None,
    ) -> dict:
        started = time.time()
        topic = self.db.get_topic(topic_id)
        if not topic:
            raise StageError(f"Không tìm thấy chủ đề #{topic_id}")
        research = (
            self.db.query_one("SELECT * FROM research_reports WHERE id = ?", (research_id,))
            if research_id
            else topic.get("research")
        )
        if not research:
            raise StageError("Chủ đề chưa có báo cáo nghiên cứu. Hãy chạy nghiên cứu trước khi viết bài.")

        def report(pct: int, message: str) -> None:
            if progress:
                progress(max(0, min(100, pct)), message)

        def check_cancel() -> None:
            if should_cancel and should_cancel():
                raise CancelledError("Đã hủy theo yêu cầu người dùng")

        opts = self._resolve_options(options)
        report(15, "Đang xây dựng dàn ý từ báo cáo nghiên cứu…")
        report_data = research.get("data") or {}
        prompt = self._build_prompt(topic, report_data, opts, research)
        check_cancel()
        report(35, f"Qwen đang viết bản nháp ({opts['style']})…")
        try:
            raw = self.ollama.generate_json(
                prompt,
                system=WRITER_SYSTEM,
                schema=WRITER_SCHEMA,
                temperature=float(settings.get("ollama.temperature", 0.8)),
                num_predict=3500,
            )
        except OllamaError as exc:
            raise StageError(f"AI không tạo được bài viết: {exc}") from exc
        check_cancel()
        report(70, "Đang kiểm tra số liệu, hashtag và dấu hiệu sao chép…")

        sources = report_data.get("sources") or []
        main_post, flags, hashtags = self._finalize(raw, report_data, opts, sources)
        short_post = self._clean_variant(raw.get("short_post"))
        long_post = self._ensure_long_post(raw.get("long_post"), main_post, report_data, flags)
        if not short_post:
            short_post = truncate(self._body_of(main_post), 320)
        headline = str(raw.get("headline") or topic["title"]).strip()
        alt = [str(h).strip() for h in (raw.get("alt_headlines") or []) if str(h).strip()]
        alt = self._ensure_alt_headlines(alt, headline, report_data)
        if len(alt) < 3:
            flags.append({
                "type": "missing_variants", "severity": "info",
                "detail": f"AI chỉ tạo {len(alt)}/3 tiêu đề thay thế",
            })
        image_prompt, image_negative, image_concept, image_flags = self._ensure_image_prompt(
            raw, headline, report_data.get("summary") or ""
        )
        flags.extend(image_flags)

        post_id = self.db.create_post(
            {
                "topic_id": topic_id,
                "research_id": research["id"],
                "headline": headline,
                "alt_headlines": alt,
                "main_post": main_post,
                "short_post": short_post,
                "long_post": long_post,
                "hashtags": hashtags,
                "hook": str(raw.get("hook") or "").strip(),
                "closing_question": str(raw.get("closing_question") or "").strip(),
                "image_prompt": image_prompt,
                "image_negative_prompt": image_negative
                or settings.get("comfyui.negative_prompt", ""),
                "image_concept": image_concept,
                "style": opts["style"],
                "tone": opts["tone"],
                "audience": opts["audience"],
                "category": opts["category"],
                "language": opts["language"],
                "length": opts["length"],
                "status": "draft",
                "sources": sources,
                "ai_flags": flags,
                "revision": len(topic.get("posts") or []) + 1,
            }
        )
        self.db.update_topic(topic_id, {"status": "drafted"})
        self.db.add_log(
            f"Đã viết bài cho chủ đề #{topic_id} (post #{post_id}, {count_words(main_post)} từ, "
            f"{len(flags)} cảnh báo)",
            "info", "writer",
            {"model": raw.get("_meta", {}).get("model"), "flags": flags[:5]},
        )
        report(100, "Hoàn tất bài viết")
        post = self.db.get_post(post_id)
        post["_meta"] = {
            "duration_seconds": round(time.time() - started, 1),
            "model": raw.get("_meta", {}).get("model") or self.ollama.model,
        }
        return post

    # ------------------------------------------------------------- versions
    def generate_versions(
        self,
        topic_id: int,
        research_id: int | None = None,
        options: dict | None = None,
        should_cancel: Callable[[], bool] | None = None,
        progress: Callable[[int, str], None] | None = None,
    ) -> dict:
        """Generate the three comparison versions (A/B/C) for one story."""
        from .quality import content_score

        topic = self.db.get_topic(topic_id)
        if not topic:
            raise StageError(f"Không tìm thấy chủ đề #{topic_id}")
        research = (
            self.db.query_one("SELECT * FROM research_reports WHERE id = ?", (research_id,))
            if research_id
            else topic.get("research")
        )
        if not research:
            raise StageError("Chủ đề chưa có báo cáo nghiên cứu. Hãy chạy nghiên cứu trước.")

        def report(pct: int, message: str) -> None:
            if progress:
                progress(max(0, min(100, pct)), message)

        def check_cancel() -> None:
            if should_cancel and should_cancel():
                raise CancelledError("Đã hủy theo yêu cầu người dùng")

        report_data = research.get("data") or {}
        sources = report_data.get("sources") or []
        base_opts = self._resolve_options(options)
        self.db.delete_versions(topic_id)

        image_prompt = image_negative = image_concept = ""
        created: list[dict] = []
        for index, plan in enumerate(VERSION_PLAN):
            check_cancel()
            report(
                int(index * 100 / len(VERSION_PLAN)) + 4,
                f"Qwen đang viết bản {plan['variant']} ({plan['label_vi']})…",
            )
            opts = dict(base_opts)
            opts.update(
                style=plan["style"],
                length=plan["length"],
                include_sources=plan["include_sources"],
                language=base_opts["language"],
            )
            prompt = self._build_prompt(topic, report_data, opts, research)
            try:
                raw = self.ollama.generate_json(
                    prompt,
                    system=WRITER_SYSTEM,
                    schema=WRITER_SCHEMA,
                    temperature=float(settings.get("ollama.temperature", 0.8)),
                    num_predict=3500,
                )
            except OllamaError as exc:
                raise StageError(f"AI không tạo được bản {plan['variant']}: {exc}") from exc
            check_cancel()

            main_post, flags, hashtags = self._finalize(raw, report_data, opts, sources)
            headline = str(raw.get("headline") or topic["title"]).strip()
            short_post = self._clean_variant(raw.get("short_post"))
            if not short_post:
                short_post = truncate(self._body_of(main_post), 320)
            if plan["variant"] == "A":
                image_prompt, image_negative, image_concept, image_flags = self._ensure_image_prompt(
                    raw, headline, report_data.get("summary") or ""
                )
                flags.extend(image_flags)
            quality = content_score(
                {
                    "main_post": main_post, "hook": raw.get("hook"),
                    "closing_question": raw.get("closing_question"),
                    "hashtags": hashtags, "sources": sources,
                    "ai_flags": flags,
                }
            )
            version_id = self.db.create_version(
                {
                    "topic_id": topic_id,
                    "research_id": research["id"],
                    "variant": plan["variant"],
                    "style": plan["style"],
                    "headline": headline,
                    "hook": str(raw.get("hook") or "").strip(),
                    "main_post": main_post,
                    "short_post": short_post,
                    "closing_question": str(raw.get("closing_question") or "").strip(),
                    "hashtags": hashtags,
                    "image_prompt": image_prompt,
                    "image_concept": image_concept,
                    "flags": flags,
                    "quality": {
                        **quality,
                        "variant_label_vi": plan["label_vi"],
                        "variant_label_en": plan["label_en"],
                    },
                    "source_count": len(sources),
                }
            )
            created.append({
                "id": version_id, "variant": plan["variant"], "style": plan["style"],
                "headline": headline, "words": count_words(self._body_of(main_post)),
                "quality": quality["score"], "flags": len(flags),
            })

        self.db.update_topic(topic_id, {"status": "drafted"})
        self.db.add_log(
            f"Đã tạo {len(created)} phiên bản nội dung cho chủ đề #{topic_id}",
            "info", "writer", {"versions": created},
        )
        report(100, "Đã tạo xong 3 phiên bản")
        return {"topic_id": topic_id, "versions": created, "image_prompt": image_prompt}

    # --------------------------------------------------------------- internal
    def _resolve_options(self, options: dict | None) -> dict:
        cfg = settings.section("writer")
        merged = {
            "style": cfg.get("style", "friendly_storytelling"),
            "tone": cfg.get("tone", "engaging"),
            "audience": cfg.get("audience", "general"),
            "length": cfg.get("length", "medium"),
            "hashtag_count": int(cfg.get("hashtag_count", 6)),
            "category": cfg.get("category", "auto"),
            "language": cfg.get("language", "vi"),
            "include_sources": bool(cfg.get("include_sources", True)),
            "avoid_clickbait": bool(cfg.get("avoid_clickbait", True)),
        }
        for key, value in (options or {}).items():
            if key in merged and value not in (None, ""):
                merged[key] = value
        if options and options.get("strategy_key"):
            merged["strategy_key"] = str(options["strategy_key"])
        merged["hashtag_count"] = max(0, min(15, int(merged["hashtag_count"])))
        if merged["style"] not in STYLE_GUIDES:
            merged["style"] = "friendly_storytelling"
        if merged["length"] not in LENGTH_GUIDES:
            merged["length"] = "medium"
        return merged

    def _build_prompt(self, topic: dict, report: dict, opts: dict, research: dict) -> str:
        facts = report.get("key_facts") or []
        fact_lines = []
        for item in facts:
            if isinstance(item, dict):
                verdict = item.get("verdict") or ""
                fact_lines.append(f"- {item.get('fact')} [{verdict}]")
            else:
                fact_lines.append(f"- {item}")
        if not fact_lines:
            fact_lines = [f"- {report.get('summary') or topic.get('summary') or topic['title']}"]

        sources = report.get("sources") or []
        source_lines = [
            f"[{s.get('id')}] {s.get('domain')}: {s.get('title')} — {s.get('url')}"
            for s in sources[:8]
        ]
        quotes = report.get("quotes") or []
        quote_lines = [
            f"- “{q.get('text')}” — {q.get('speaker') or 'không rõ'}"
            for q in quotes[:4]
        ]
        timeline = [
            f"- {t.get('date') or '?'}: {t.get('event')}" for t in (report.get("timeline") or [])[:6]
        ]
        headline_seed = report.get("topic_title") or topic["title"]
        category = opts["category"] if opts["category"] != "auto" else (
            report.get("category") or topic.get("category") or "general"
        )
        guidance = self._editorial_guidance(opts, category)

        return (
            f"CHỦ ĐỀ: {headline_seed}\n"
            f"NHÓM NỘI DUNG: {category}\n\n"
            f"TÓM TẮT NGHIÊN CỨU:\n{truncate(report.get('summary') or '', 1400)}\n\n"
            "DỮ KIỆN ĐÃ KIỂM CHỨNG / GHI CHÚ:\n" + "\n".join(fact_lines[:10]) + "\n\n"
            "MỐC THỜI GIAN:\n" + ("\n".join(timeline) if timeline else "(không có)") + "\n\n"
            "TRÍCH DẪN NGUYÊN VĂN (nếu dùng phải giữ đúng):\n"
            + ("\n".join(quote_lines) if quote_lines else "(không có)") + "\n\n"
            "THÔNG TIN CHƯA XÁC NHẬN / MÂU THUẪN (phải nói rõ khi nhắc tới):\n"
            + ("\n".join(f"- {x}" for x in (report.get('unconfirmed') or [])[:5]) or "(không có)")
            + "\n"
            + ("\n".join(f"- {x.get('claim')}" for x in (report.get('conflicting') or [])[:4])
               if report.get("conflicting") else "")
            + "\n\nNGUỒN (chỉ để tham chiếu, không chép nguyên văn):\n"
            + "\n".join(source_lines)
            + "\n\n"
            f"ĐỘ TIN CẬY NGHIÊN CỨU: {research.get('confidence', 'n/a')}% "
            f"({research.get('confidence_label', '')})\n\n"
            + (f"{guidance}\n\n" if guidance else "")
            + "YÊU CẦU VIẾT:\n"
            f"- Phong cách: {STYLE_GUIDES[opts['style']]}\n"
            f"- Giọng điệu: {TONE_GUIDES.get(opts['tone'], opts['tone'])}.\n"
            f"- Khán giả: {AUDIENCE_GUIDES.get(opts['audience'], opts['audience'])}.\n"
            f"- Độ dài: {LENGTH_GUIDES[opts['length']]}.\n"
            f"- Hashtag: đúng {opts['hashtag_count']} hashtag tiếng Việt không dấu cách, mỗi hashtag bắt đầu bằng #. "
            "Hashtag phải bám sát nội dung bài, không thêm sự kiện hay hành động không có trong báo cáo "
            "(ví dụ không dùng 'khởi công' nếu chỉ là chạy thử).\n"
            "- main_post: bài Facebook hoàn chỉnh, có mở bài tự nhiên, thân bài rõ ý, kết bài mở thảo luận. "
            "Xuống dòng hợp lý, không dùng markdown, không chèn link và KHÔNG chèn hashtag trong thân bài "
            "(hashtag trả riêng ở trường hashtags).\n"
            "- short_post: bản ngắn 2-4 câu cho story/reels caption.\n"
            "- long_post: bản kể chuyện dài hơn, vẫn chỉ dùng dữ kiện đã có.\n"
            "- alt_headlines: đúng 3 tiêu đề khác nhau, không giật gân sai sự thật, "
            "giữ đúng sự kiện và động từ của sự kiện (không đổi 'chạy thử' thành 'khai trương/khởi công').\n"
            "- hook: 1 câu mở đầu thu hút (không clickbait sai sự thật).\n"
            "- closing_question: 1 câu hỏi mời thảo luận.\n"
            "- image_prompt: mô tả HOÀN TOÀN BẰNG TIẾNG ANH (không một từ tiếng Việt) cho ảnh minh họa biên tập (editorial illustration) mang tính ẩn dụ, "
            "KHÔNG mô tả chân dung người thật, không mô phỏng lại sự kiện như ảnh tư liệu, không chữ, không logo. "
            "Nêu bối cảnh, biểu tượng, ánh sáng, màu sắc, phong cách (ví dụ: editorial illustration, flat vector, "
            "cinematic lighting). Không dùng tên riêng của người thật.\n"
            "- image_negative_prompt: TIẾNG ANH, các yếu tố cần tránh cho ảnh.\n"
            "- image_concept: 1-2 câu tiếng Việt giải thích ý tưởng hình ảnh.\n\n"
            "TUYỆT ĐỐI KHÔNG thêm số liệu, ngày tháng, câu nói hay sự kiện không có trong báo cáo trên. "
            "Trả về JSON đúng schema."
        )

    def _editorial_guidance(self, opts: dict, category: str) -> str:
        """Strategy + locally learned preferences added to the prompt.

        Both blocks are optional and never add facts: they only shape tone and
        structure. Failures are ignored so generation keeps working.
        """
        blocks: list[str] = []
        strategy_key = opts.get("strategy_key")
        if strategy_key and settings.get("strategy.enabled", True):
            try:
                from .strategy import prompt_block

                block = prompt_block(strategy_key)
                if block:
                    blocks.append(block)
            except Exception:  # noqa: BLE001 - guidance must never block writing
                pass
        try:
            from .learning import EditorialLearning

            hint = EditorialLearning(self.db).style_hint(category)
            if hint:
                blocks.append(hint)
        except Exception:  # noqa: BLE001 - learning is advisory only
            pass
        return "\n\n".join(blocks)

    def _finalize(self, raw: dict, report: dict, opts: dict, sources: list[dict]):
        flags: list[dict] = []
        main_post = str(raw.get("main_post") or "").strip()
        if not main_post:
            raise StageError("AI trả về bài viết rỗng")

        hashtags = self._normalise_hashtags(raw.get("hashtags"), opts["hashtag_count"])
        main_post = self._strip_markdown(main_post)
        # Hashtags are stored separately; drop any block the model left in the body.
        main_post = re.sub(r"(?:\s*#[^\s#]+)+\s*$", "", main_post).strip()

        # Audit BEFORE appending source URLs so IDs are not mistaken for facts.
        evidence_text = self._evidence_text(report)
        main_post, number_flags = self._audit_numbers(main_post, evidence_text)
        flags.extend(number_flags)

        copied = self._copied_sentences(main_post, evidence_text)
        if copied:
            flags.append({
                "type": "copied_text", "severity": "warning",
                "detail": "Câu gần giống nguyên văn nguồn: " + truncate(copied[0], 120),
            })
        if opts["avoid_clickbait"]:
            hits = [p for p in CLICKBAIT_PATTERNS if re.search(p, main_post, re.IGNORECASE)]
            if hits:
                flags.append({
                    "type": "clickbait", "severity": "info",
                    "detail": "Có cụm từ giật gân cần cân nhắc: " + ", ".join(hits[:3]),
                })
        if opts["include_sources"] and sources:
            domain_line = " · ".join(
                dict.fromkeys(s.get("domain") for s in sources if s.get("domain"))
            )
            urls = [s.get("url") for s in sources[:3] if s.get("url")]
            if urls:
                main_post = (
                    main_post.rstrip()
                    + "\n\nNguồn tham khảo: "
                    + " · ".join(urls)
                    + (f"\n(Tổng hợp từ {domain_line})" if domain_line else "")
                )

        if report.get("warning"):
            flags.append({"type": "low_corroboration", "severity": "warning",
                          "detail": report["warning"]})
        low_conf = float(report.get("confidence") or 0) < 60
        if low_conf:
            flags.append({
                "type": "low_confidence", "severity": "warning",
                "detail": f"Độ tin cậy nghiên cứu thấp ({report.get('confidence')}%). Nên kiểm chứng lại trước khi đăng.",
            })

        return main_post, flags, hashtags

    @staticmethod
    def _body_of(main_post: str) -> str:
        """Main post text without the appended source block."""
        return re.split(r"\n\nNguồn tham khảo:", main_post or "")[0].strip()

    @classmethod
    def _clean_variant(cls, value) -> str:
        text = cls._strip_markdown(str(value or "").strip())
        return re.sub(r"(?:\s*#[^\s#]+)+\s*$", "", text).strip()

    def _ensure_long_post(self, value, main_post: str, report: dict,
                          flags: list[dict]) -> str:
        """The storytelling version must really be longer than the main post."""
        long_post = self._clean_variant(value)
        body = self._body_of(main_post)
        body_words = count_words(body)
        # A "longer version" must be meaningfully longer, not one word longer.
        min_words = max(body_words + 40, round(body_words * 1.35))
        target_words = max(body_words + 60, round(body_words * 1.5))
        if count_words(long_post) >= min_words:
            return long_post
        prompt = (
            f"BÀI CHÍNH (vừa viết):\n{truncate(body, 2500)}\n\n"
            f"DỮ KIỆN ĐÃ KIỂM CHỨNG:\n" + "\n".join(
                f"- {item.get('fact', '') if isinstance(item, dict) else item}"
                for item in (report.get("key_facts") or [])[:10]
            ) + "\n\n"
            f"Hãy viết lại thành một bản KỂ CHUYỆN DÀI HƠN bài chính (khoảng {target_words} từ, "
            f"tối thiểu {min_words} từ), "
            "giữ nguyên mọi dữ kiện, con số và sắc thái sự kiện của bài chính, "
            "có thể đi sâu hơn vào bối cảnh, ý nghĩa và triển vọng đã nêu trong dữ kiện. "
            "Không thêm sự kiện, số liệu, câu nói hay nhân vật mới. Không markdown, không hashtag, không link. "
            'Trả về JSON: {"long_post": "..."}.'
        )
        try:
            data = self.ollama.generate_json(
                prompt,
                system=WRITER_SYSTEM,
                schema=LONG_POST_SCHEMA,
                temperature=0.8,
                num_predict=1800,
            )
            expanded = self._clean_variant(data.get("long_post"))
            if count_words(expanded) >= min_words:
                return expanded
        except OllamaError:
            pass
        flags.append({
            "type": "long_post_fallback", "severity": "info",
            "detail": "AI không tạo được bản dài hơn; bản dài đang dùng nội dung bài chính.",
        })
        return body

    @staticmethod
    def _is_english_prompt(text: str) -> bool:
        if not text or len(text.strip()) < 30:
            return False
        if _VI_DIACRITIC_RE.search(text):
            return False
        letters = [ch for ch in text if ch.isalpha()]
        if not letters:
            return False
        ascii_letters = [ch for ch in letters if ch.isascii()]
        return len(ascii_letters) / len(letters) >= 0.97

    def _ensure_image_prompt(self, raw: dict, headline: str, summary: str):
        prompt = str(raw.get("image_prompt") or "").strip()
        negative = str(raw.get("image_negative_prompt") or "").strip()
        concept = str(raw.get("image_concept") or "").strip()
        flags: list[dict] = []
        if self._is_english_prompt(prompt):
            return prompt, negative, concept, flags
        request = (
            f"Headline: {headline}\nSummary: {truncate(summary, 700)}\n"
            f"Current concept (Vietnamese): {concept}\n\n"
            "Write a detailed prompt in ENGLISH for an SDXL editorial illustration about this story. "
            "Use symbolic, conceptual imagery (objects, environments, abstract shapes); "
            "do not depict real identifiable people, real photos or news footage. "
            "Describe subject, setting, composition, colour palette, lighting and art style in 40-80 words. "
            "negative_prompt must be ENGLISH. concept_vi is 1-2 Vietnamese sentences. "
            "Return JSON only."
        )
        try:
            data = self.ollama.generate_json(
                request,
                system=WRITER_SYSTEM,
                schema=IMAGE_PROMPT_SCHEMA,
                temperature=0.7,
                num_predict=600,
            )
            new_prompt = str(data.get("image_prompt") or "").strip()
            if self._is_english_prompt(new_prompt):
                return (
                    new_prompt,
                    str(data.get("negative_prompt") or negative).strip(),
                    str(data.get("concept_vi") or concept).strip(),
                    flags,
                )
        except OllamaError:
            pass
        flags.append({
            "type": "image_prompt_language", "severity": "warning",
            "detail": "Prompt ảnh chưa phải tiếng Anh hoàn toàn; ComfyUI có thể tạo ảnh lệch chủ đề. Nên sửa prompt trước khi tạo ảnh.",
        })
        return prompt, negative, concept, flags

    def _ensure_alt_headlines(self, existing: list[str], headline: str,
                              report: dict) -> list[str]:
        alt = [h for h in dict.fromkeys(existing) if h and h != headline][:3]
        if len(alt) >= 3:
            return alt
        prompt = (
            f"Tiêu đề: {headline}\n"
            f"Tóm tắt: {truncate(report.get('summary') or '', 700)}\n\n"
            f"Hãy viết thêm {3 - len(alt)} tiêu đề Facebook tiếng Việt khác biệt, ngắn gọn, "
            "đúng sự thật, không clickbait, không thêm dữ kiện mới. "
            "Giữ nguyên sự kiện và động từ chính của tiêu đề gốc: không đổi 'chạy thử/vận hành thử nghiệm' "
            "thành 'khai trương/khởi công/hoàn thành', không đổi mức độ hay phạm vi sự việc. "
            "Trả về JSON: {\"headlines\": [\"...\"]}."
        )
        try:
            data = self.ollama.generate_json(
                prompt,
                system=WRITER_SYSTEM,
                schema=ALT_HEADLINE_SCHEMA,
                temperature=0.85,
                num_predict=400,
            )
            for item in data.get("headlines") or []:
                clean = str(item).strip()
                if clean and clean not in alt and clean != headline:
                    alt.append(clean)
        except OllamaError:
            pass
        return alt[:3]

    @staticmethod
    def _strip_markdown(text: str) -> str:
        text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    @staticmethod
    def _normalise_hashtags(value, limit: int) -> list[str]:
        items = value if isinstance(value, list) else re.split(r"[\s,]+", str(value or ""))
        result: list[str] = []
        for item in items:
            tag = str(item or "").strip().lstrip("#").strip()
            tag = re.sub(r"[^\w\u00C0-\u1EF9]", "", tag)
            if not tag:
                continue
            tag = "#" + tag
            if tag.lower() not in {t.lower() for t in result}:
                result.append(tag)
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _evidence_text(report: dict) -> str:
        parts = [report.get("summary") or ""]
        for item in report.get("key_facts") or []:
            parts.append(item.get("fact", "") if isinstance(item, dict) else str(item))
        for item in report.get("timeline") or []:
            parts.append(f"{item.get('date', '')} {item.get('event', '')}")
        parts.extend(report.get("confirmed") or [])
        parts.extend(report.get("unconfirmed") or [])
        for quote in report.get("quotes") or []:
            parts.append(quote.get("text", "") if isinstance(quote, dict) else str(quote))
        for source in report.get("sources") or []:
            parts.append(source.get("title") or "")
        return "\n".join(str(p) for p in parts if p)

    def _audit_numbers(self, text: str, evidence: str) -> tuple[str, list[dict]]:
        flags: list[dict] = []
        for number in _numbers(text):
            if not _contains_number(evidence, number):
                flags.append({
                    "type": "unverified_number", "severity": "warning",
                    "detail": f"Số liệu không có trong báo cáo nghiên cứu: {number}",
                })
        return text, flags

    @staticmethod
    def _copied_sentences(text: str, evidence: str) -> list[str]:
        copied = []
        evidence_folded = _fold(evidence)
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            clean = sentence.strip()
            if len(clean.split()) < 14:
                continue
            if _fold(clean)[:120] in evidence_folded:
                copied.append(clean)
        return copied


ai_writer = AIWriter()
