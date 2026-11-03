"""AI Research Engine: collect article bodies, cross-check sources, build a grounded report."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .config import settings
from .database import Database, db as default_db
from .errors import CancelledError, StageError
from .fact_checker import FactChecker
from .ollama_client import OllamaClient, OllamaError, ollama
from .search_provider import SearchError, search_service
from .source_manager import source_manager
from .utils import (
    canonical_url,
    domain_of,
    iso,
    parse_datetime,
    truncate,
    utcnow,
    utcnow_iso,
)

RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "key_facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string"},
                    "source_ids": {"type": "array", "items": {"type": "integer"}},
                    "status": {"type": "string"},
                },
                "required": ["fact", "source_ids", "status"],
            },
        },
        "timeline": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"date": {"type": "string"}, "event": {"type": "string"}},
                "required": ["date", "event"],
            },
        },
        "entities": {
            "type": "object",
            "properties": {
                "people": {"type": "array", "items": {"type": "string"}},
                "organizations": {"type": "array", "items": {"type": "string"}},
                "locations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["people", "organizations", "locations"],
        },
        "confirmed": {"type": "array", "items": {"type": "string"}},
        "unconfirmed": {"type": "array", "items": {"type": "string"}},
        "conflicting": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"claim": {"type": "string"}, "notes": {"type": "string"}},
                "required": ["claim", "notes"],
            },
        },
        "missing_information": {"type": "array", "items": {"type": "string"}},
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "speaker": {"type": "string"},
                    "source_id": {"type": "integer"},
                },
                "required": ["text", "speaker", "source_id"],
            },
        },
        "confidence_notes": {"type": "string"},
    },
    "required": [
        "summary", "key_facts", "timeline", "entities", "confirmed", "unconfirmed",
        "conflicting", "missing_information",
    ],
}

RESEARCH_SYSTEM = (
    "Bạn là nhà nghiên cứu thông tin độc lập, cực kỳ thận trọng và trung lập. "
    "Bạn chỉ được sử dụng thông tin xuất hiện trong các NGUỒN được đánh số. "
    "Tuyệt đối không bịa đặt số liệu, ngày tháng, câu nói, tên người, tổ chức hay sự kiện. "
    "Không biến tin đồn hoặc thông tin một chiều thành sự thật đã xác nhận. "
    "Khi nguồn không nói rõ, hãy ghi vào 'missing_information' thay vì suy đoán."
)

CONFIDENCE_LABELS = {
    "insufficient": "Chưa đủ căn cứ",
    "low": "Thấp",
    "moderate": "Trung bình",
    "high": "Cao",
    "very_high": "Rất cao",
}


class NewsResearcher:
    def __init__(self, database: Database | None = None, client: OllamaClient | None = None):
        self.db = database or default_db
        self.ollama = client or ollama
        self.sources = source_manager
        self.fact_checker = FactChecker()

    # ------------------------------------------------------------------ public
    def research(
        self,
        topic_id: int,
        should_cancel: Callable[[], bool] | None = None,
        progress: Callable[[int, str], None] | None = None,
        force: bool = False,
    ) -> dict:
        started = time.time()
        topic = self.db.get_topic(topic_id)
        if not topic:
            raise StageError(f"Không tìm thấy chủ đề #{topic_id}")

        def report(pct: int, message: str) -> None:
            if progress:
                progress(max(0, min(100, pct)), message)

        def check_cancel() -> None:
            if should_cancel and should_cancel():
                raise CancelledError("Đã hủy theo yêu cầu người dùng")

        if not force and topic.get("research") and topic["research"].get("status") == "complete":
            report(100, "Đã có báo cáo nghiên cứu trước đó")
            return topic["research"]

        cfg = settings.section("research")
        max_articles = int(cfg.get("max_articles", 6))
        min_sources = int(cfg.get("min_sources", 2))
        self.db.update_topic(topic_id, {"status": "researching"})
        report(5, "Đang thu thập bài viết từ các nguồn…")
        articles = self.db.list_articles(topic_id)
        if len(articles) < max_articles:
            self._search_more(topic, articles, max_articles, should_cancel)
            articles = self.db.list_articles(topic_id)

        if not articles:
            self.db.update_topic(topic_id, {"status": "failed"})
            raise StageError("Chủ đề chưa có bài viết nguồn nào để nghiên cứu")

        check_cancel()
        report(20, f"Đang tải nội dung {min(len(articles), max_articles)} bài viết…")
        fetched = self._fetch_contents(articles[:max_articles], should_cancel, report)
        check_cancel()

        with_content = [a for a in fetched if a.get("content")]
        if not with_content:
            self.db.add_log(
                f"Chủ đề #{topic_id}: không tải được toàn văn, chỉ dùng trích đoạn RSS",
                "warning", "research",
            )
        report(52, f"Đã có nội dung {len(with_content)} bài, đang tổng hợp bằng AI…")

        sources_payload = self._build_sources(fetched)
        prompt = self._build_prompt(topic, sources_payload)

        try:
            raw = self.ollama.generate_json(
                prompt,
                system=RESEARCH_SYSTEM,
                schema=RESEARCH_SCHEMA,
                temperature=0.25,
                num_predict=3200,
            )
        except OllamaError as exc:
            self.db.update_topic(topic_id, {"status": "failed"})
            raise StageError(f"AI không tạo được báo cáo nghiên cứu: {exc}") from exc

        check_cancel()
        report(80, "Đang kiểm chứng số liệu và trích dẫn…")
        checked = self.fact_checker.validate(
            raw, sources_payload, topic["title"], min_sources=min_sources
        )
        report_data = checked["report"]
        report_data["generated_at"] = utcnow_iso()
        report_data["model"] = raw.get("_meta", {}).get("model") or self.ollama.model
        report_data["duration_seconds"] = round(time.time() - started, 1)
        report_data["source_requirement"] = min_sources
        if int(report_data["corroboration"].get("independent_domain_count", 0)) < min_sources:
            report_data["warning"] = (
                "Chưa đủ nguồn độc lập để chéo kiểm chứng. "
                "Báo cáo này cần được biên tập viên xác minh thêm trước khi đăng."
            )

        research_id = self.db.create_research(
            {
                "topic_id": topic_id,
                "status": "complete",
                "confidence": checked["confidence"],
                "confidence_label": checked["confidence_label"],
                "data": report_data,
                "model": report_data["model"],
                "source_count": len(sources_payload),
            }
        )
        for fact in checked["facts"]:
            self.db.add_fact_check(research_id, fact)

        summary = report_data.get("summary") or topic.get("summary") or ""
        self.db.update_topic(
            topic_id,
            {
                "status": "researched",
                "summary": truncate(summary, 800),
                "meta": {
                    **(topic.get("meta") or {}),
                    "research_id": research_id,
                    "confidence": checked["confidence"],
                    "confidence_label": checked["confidence_label"],
                },
            },
        )
        report_data["id"] = research_id
        report_data["topic_id"] = topic_id
        self.db.add_log(
            f"Nghiên cứu chủ đề #{topic_id}: {len(sources_payload)} nguồn, "
            f"độ tin cậy {checked['confidence']}% ({checked['confidence_label']})",
            "info", "research",
            {"issues": checked["issues"][:5]},
        )
        report(100, "Hoàn tất nghiên cứu")
        return {"id": research_id, **report_data}

    # --------------------------------------------------------------- internal
    def _search_more(self, topic: dict, articles: list[dict], max_articles: int,
                     should_cancel: Callable[[], bool] | None) -> None:
        if not settings.get("research.search_more_sources", True):
            return
        language = topic.get("language") or settings.get("writer.language", "vi")
        try:
            results = search_service.find_related(
                topic["title"], language=language, limit=max_articles
            )
        except SearchError as exc:
            self.db.add_log(f"Tìm thêm nguồn thất bại: {exc}", "warning", "research")
            return
        existing = self.db.article_urls(topic["id"])
        added = 0
        for result in results:
            if should_cancel and should_cancel():
                raise CancelledError("Đã hủy theo yêu cầu người dùng")
            if added >= max_articles:
                break
            url = result.url
            if source_manager.is_aggregator(url):
                url = source_manager.resolve_aggregator_url(url)
            if not url or source_manager.is_aggregator(url):
                continue
            key = canonical_url(url)
            if key in existing:
                continue
            self.db.add_article(
                topic["id"],
                {
                    "title": result.title,
                    "url": url,
                    "canonical_url": key,
                    "domain": domain_of(url),
                    "published_at": iso(parse_datetime(result.published_at)),
                    "excerpt": truncate(result.summary, 1000),
                    "content": "",
                    "content_chars": 0,
                    "kind": "search",
                    "language": result.language,
                    "reliability": source_manager.reliability(url),
                    "meta": {"source_name": result.source_name, "provider": result.provider},
                },
            )
            existing.add(key)
            added += 1

    def _fetch_contents(self, articles: list[dict], should_cancel, report) -> list[dict]:
        max_chars = int(settings.get("research.max_chars_per_article", 5000))
        total = len(articles) or 1
        done = 0

        def fetch(article: dict) -> dict:
            if article.get("content") and article.get("content_chars", 0) > 300:
                return article
            extraction = self.sources.extract_article(article["url"], max_chars=max_chars)
            if extraction.get("ok"):
                resolved = extraction.get("resolved_url") or article["url"]
                updates = {
                    "content": extraction["text"],
                    "content_chars": extraction.get("chars", len(extraction["text"])),
                    "published_at": iso(parse_datetime(extraction.get("published_at")))
                    or article.get("published_at"),
                    "reliability": source_manager.reliability(resolved),
                }
                if extraction.get("title") and not article.get("title"):
                    updates["title"] = extraction["title"]
                meta = dict(article.get("meta") or {})
                meta["resolved_url"] = resolved
                updates["meta"] = meta
                self.db.update_article(article["id"], updates)
                article = {**article, **updates, "domain": domain_of(resolved),
                           "resolved_url": resolved, "meta": meta}
            else:
                meta = dict(article.get("meta") or {})
                meta["fetch_error"] = extraction.get("error")
                self.db.update_article(article["id"], {"meta": meta})
            return article

        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(fetch, article) for article in articles]
            for future in as_completed(futures):
                if should_cancel and should_cancel():
                    raise CancelledError("Đã hủy theo yêu cầu người dùng")
                results.append(future.result())
                done += 1
                report(20 + int(28 * done / total), f"Đã tải {done}/{total} bài viết…")
        return results

    def _build_sources(self, articles: list[dict]) -> list[dict]:
        ordered = sorted(
            articles,
            key=lambda a: (bool(a.get("content")), a.get("reliability") or 0),
            reverse=True,
        )
        sources = []
        seen_urls: set[str] = set()
        for index, article in enumerate(ordered, start=1):
            content = (article.get("content") or "").strip()
            excerpt = (article.get("excerpt") or "").strip()
            resolved = (article.get("resolved_url")
                        or (article.get("meta") or {}).get("resolved_url")
                        or article.get("url") or "")
            # Never expose a Google News redirect as a citation when the real
            # publisher URL is unknown.
            if source_manager.is_aggregator(resolved):
                continue
            if resolved in seen_urls:
                continue
            seen_urls.add(resolved)
            sources.append(
                {
                    "id": len(sources) + 1,
                    "article_id": article.get("id"),
                    "title": article.get("title") or "",
                    "url": resolved,
                    "domain": article.get("domain") or domain_of(resolved),
                    "published_at": article.get("published_at"),
                    "reliability": article.get("reliability") or 0.5,
                    "excerpt": excerpt,
                    "content": content,
                    "content_chars": len(content),
                    "kind": article.get("kind"),
                }
            )
        return sources

    def _build_prompt(self, topic: dict, sources: list[dict]) -> str:
        # Token budget: keep the evidence pack within ~60% of the context window.
        num_ctx = int(settings.get("ollama.num_ctx", 8192))
        char_budget = int(num_ctx * 1.35)
        per_source = max(600, char_budget // max(1, len(sources)))

        blocks = []
        for source in sources:
            body = source["content"] or source["excerpt"] or "(không có nội dung)"
            age = "không rõ"
            published = parse_datetime(source.get("published_at"))
            if published:
                age = f"{(utcnow() - published).total_seconds() / 3600:.1f} giờ trước"
            blocks.append(
                f"[NGUỒN {source['id']}] {source['title']}\n"
                f"Tên miền: {source['domain']} | Xuất bản: {source['published_at'] or 'không rõ'} ({age})\n"
                f"{truncate(body, per_source, '…')}"
            )

        missing_date = [s for s in sources if not s.get("published_at")]
        notes = ""
        if missing_date:
            notes = (
                f"\nLưu ý: {len(missing_date)} nguồn không có ngày xuất bản rõ ràng; "
                "hãy ghi vào missing_information nếu ảnh hưởng đến kết luận.\n"
            )

        return (
            f"CHỦ ĐỀ CẦN NGHIÊN CỨU: {topic['title']}\n"
            f"Nhóm nội dung gợi ý: {topic.get('category') or 'general'}\n\n"
            f"{len(sources)} NGUỒN BÁO CHÍ CÔNG KHAI (đánh số):\n\n"
            + "\n\n".join(blocks)
            + notes
            + "\nYÊU CẦU ĐẦU RA (JSON đúng schema):\n"
            "1. summary: 3-5 câu tiếng Việt, trung tính, chỉ dùng dữ kiện có trong nguồn.\n"
            "2. key_facts: 4-8 dữ kiện quan trọng nhất. Mỗi dữ kiện phải kèm source_ids (>=1 id ở trên). "
            "status = 'confirmed' nếu có từ 2 tên miền độc lập cùng đưa tin, 'single_source' nếu chỉ 1, "
            "'conflicting' nếu nguồn mâu thuẫn. Không dùng số liệu không xuất hiện trong nguồn.\n"
            "3. timeline: các mốc thời gian nêu đúng như nguồn (nếu không có thì để mảng rỗng).\n"
            "4. entities: chỉ liệt kê tên thật xuất hiện trong nguồn.\n"
            "5. confirmed / unconfirmed / conflicting: phân loại trung thực. Nếu chưa chắc chắn, đưa vào unconfirmed.\n"
            "6. quotes: CHỈ trích nguyên văn câu xuất hiện trong nguồn, kèm source_id. Không có thì để mảng rỗng.\n"
            "7. missing_information: những điểm còn thiếu để kết luận chắc chắn.\n"
            "8. confidence_notes: 1-2 câu giải thích mức độ chắc chắn.\n"
            "Không được bổ sung kiến thức bên ngoài các nguồn trên."
        )


news_researcher = NewsResearcher()
