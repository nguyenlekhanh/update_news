"""Trend Discovery Engine: collect public sources, cluster, enrich, score and persist."""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from typing import Callable

from .config import settings
from .database import Database, db as default_db
from .errors import CancelledError, StageError
from .facebook_public_source import facebook_source
from .ollama_client import OllamaClient, OllamaError, ollama
from .rss_collector import GoogleTrendsCollector, RSSCollector
from .search_provider import SearchError, search_service
from .source_manager import source_manager
from .trend_models import Candidate
from .trend_scorer import TrendScorer
from .fact_checker import _numbers
from .utils import (
    canonical_url,
    content_tokens,
    dedupe_key,
    domain_of,
    json_dumps,
    parse_datetime,
    title_similarity,
    truncate,
    utcnow,
    utcnow_iso,
)

CATEGORY_OPTIONS = [
    "general", "vietnam", "international", "ai", "technology", "business",
    "finance", "entertainment", "celebrity", "lifestyle", "science", "education",
    "social", "discovery", "human_interest", "internet_culture",
]

def _proper_tokens(title: str) -> set[str]:
    """Capitalised words of a headline, accent-folded and without stopwords."""
    proper: set[str] = set()
    for word in re.findall(r"[^\s:,;.!?\"'()|–-]+", title or ""):
        if len(word) > 1 and word[:1].isupper():
            proper |= content_tokens(word)
    return proper


def same_story(title_a: str, title_b: str) -> bool:
    """Detect the same event reported with different wording.

    Headline tokens alone miss rewrites ("Tủ lạnh siêu thị khắp châu Âu tăng nhiệt..." vs
    "Hacker tăng nhiệt tủ lạnh..."), so a second pass looks at how much of the shorter
    headline is covered, shared proper nouns and shared numbers. Conflicting numbers mean
    two different updates of the same subject (e.g. a gold price on another day), which are
    kept apart.
    """
    if title_similarity(title_a, title_b) >= 0.62:
        return True
    tokens_a, tokens_b = content_tokens(title_a), content_tokens(title_b)
    if not tokens_a or not tokens_b:
        return False
    shared = tokens_a & tokens_b
    containment = len(shared) / min(len(tokens_a), len(tokens_b))
    numbers_a, numbers_b = set(_numbers(title_a)), set(_numbers(title_b))
    shared_numbers = numbers_a & numbers_b
    if numbers_a and numbers_b and not shared_numbers:
        return False
    if shared_numbers and containment >= 0.5 and len(shared) >= 3:
        return True
    if len(_proper_tokens(title_a) & _proper_tokens(title_b)) >= 2 and containment >= 0.45:
        return True
    return containment >= 0.8 and len(shared) >= 4


EDITORIAL_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "audience_fit": {"type": "integer"},
                    "uniqueness": {"type": "integer"},
                    "category": {"type": "string"},
                    "summary_vi": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "audience_fit", "uniqueness", "category", "summary_vi", "reason"],
            },
        }
    },
    "required": ["items"],
}

EDITORIAL_SYSTEM = (
    "Bạn là biên tập viên tin tức người Việt với 15 năm kinh nghiệm chọn chủ đề cho Facebook. "
    "Bạn chỉ đánh giá dựa trên tiêu đề, mô tả và nguồn được cung cấp. "
    "Tuyệt đối không bịa thêm dữ kiện, số liệu hay tên người. "
    "Nếu thông tin quá mơ hồ, hãy cho điểm thấp và nói rõ trong 'reason'."
)


class TrendFinder:
    def __init__(self, database: Database | None = None, client: OllamaClient | None = None):
        self.db = database or default_db
        self.ollama = client or ollama
        self.rss = RSSCollector()
        self.trends = GoogleTrendsCollector()
        self.scorer = TrendScorer()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public
    def discover(
        self,
        max_topics: int | None = None,
        lookback_hours: int | None = None,
        should_cancel: Callable[[], bool] | None = None,
        progress: Callable[[int, str], None] | None = None,
        use_llm: bool | None = None,
    ) -> dict:
        started = time.time()
        cfg = settings.section("trends")
        lookback = int(lookback_hours or cfg.get("lookback_hours", 36))
        limit_topics = int(max_topics or cfg.get("max_topics", 12))
        min_score = float(cfg.get("min_score", 40))
        target_lang = settings.get("writer.language", "vi")
        use_llm = cfg.get("llm_editorial", True) if use_llm is None else use_llm

        def report(pct: int, message: str) -> None:
            if progress:
                progress(max(0, min(100, pct)), message)

        def check_cancel() -> None:
            if should_cancel and should_cancel():
                raise CancelledError("Đã hủy theo yêu cầu người dùng")

        report(3, "Đang thu thập tin từ các nguồn công khai…")
        candidates, source_stats = self._collect_sources(lookback, should_cancel)
        check_cancel()
        if not candidates:
            self.db.add_log("Không thu thập được tin nào từ các nguồn đang bật", "warning", "trends")
            raise StageError(
                "Không thu thập được tin nào. Hãy kiểm tra kết nối mạng và bật nguồn dữ liệu trong Cài đặt."
            )
        report(32, f"Đã thu thập {len(candidates)} tin từ {source_stats['sources_ok']} nguồn…")

        clusters = self._cluster(candidates)
        report(45, f"Gom được {len(clusters)} chủ đề ứng viên, đang tìm thêm nguồn chéo…")
        self._enrich_clusters(clusters, target_lang, should_cancel)
        check_cancel()
        report(62, "Đang đánh giá mức độ hấp dẫn bằng AI…")

        editorials: dict[int, dict] = {}
        if use_llm:
            try:
                editorials = self._editorial_scores(clusters)
            except (OllamaError, ValueError) as exc:
                self.db.add_log(
                    f"Bỏ qua đánh giá biên tập bằng AI: {exc}", "warning", "trends"
                )
        check_cancel()
        report(76, "Đang tính điểm xu hướng…")

        scored: list[dict] = []
        for index, cluster in enumerate(clusters):
            editorial = editorials.get(index)
            if editorial:
                cluster["category"] = editorial.get("category") or cluster["category"]
                cluster["editorial_summary"] = editorial.get("summary_vi") or ""
            result = self.scorer.score(cluster, editorial)
            cluster.update(
                score=result["score"],
                metrics=result["metrics"],
                rationale=result["rationale"],
                caveats=result["caveats"],
            )
            scored.append(cluster)
        scored.sort(key=lambda c: c["score"], reverse=True)
        eligible = [c for c in scored if c["score"] >= min_score][:limit_topics]

        report(88, f"Đang lưu {len(eligible)} chủ đề vào cơ sở dữ liệu…")
        saved = self._persist(eligible, target_lang)

        duration = round(time.time() - started, 1)
        summary = {
            "topics_found": len(scored),
            "topics_saved": len(saved["topics"]),
            "created": saved["created"],
            "merged": saved["merged"],
            "sources_ok": source_stats["sources_ok"],
            "sources_failed": source_stats["sources_failed"],
            "candidates": len(candidates),
            "clusters": len(clusters),
            "editorial_used": bool(editorials),
            "errors": source_stats["errors"][:12],
            "duration_seconds": duration,
            "topic_ids": [t["id"] for t in saved["topics"]],
            "topics": saved["topics"],
        }
        self.db.add_log(
            f"Khám phá xu hướng: {len(saved['topics'])} chủ đề (mới {saved['created']}, gộp {saved['merged']}) "
            f"từ {source_stats['sources_ok']} nguồn trong {duration}s",
            "info",
            "trends",
            {"errors": source_stats["errors"][:5]},
        )
        report(100, "Hoàn tất khám phá xu hướng")
        return summary

    # ---------------------------------------------------------------- collect
    def _collect_sources(
        self, lookback_hours: int, should_cancel: Callable[[], bool] | None
    ) -> tuple[list[Candidate], dict]:
        sources = self.db.list_sources(enabled_only=True)
        cfg = settings.section("trends")
        allowed_categories = set(cfg.get("categories") or CATEGORY_OPTIONS)
        candidates: list[Candidate] = []
        errors: list[str] = []
        stats = {"sources_ok": 0, "sources_failed": 0, "errors": errors}

        def allowed(candidate: Candidate) -> bool:
            return candidate.category in allowed_categories or candidate.category == "general"

        def run_source(source: dict):
            items, error = self.rss.collect_source(source, lookback_hours, limit=30)
            return source, items, error

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(run_source, source) for source in sources]
            for future in as_completed(futures):
                if should_cancel and should_cancel():
                    raise CancelledError("Đã hủy theo yêu cầu người dùng")
                source, items, error = future.result()
                self.db.mark_source_fetch(
                    source["id"], "ok" if not error else "error", error, len(items)
                )
                if error:
                    stats["sources_failed"] += 1
                    errors.append(f"{source['name']}: {truncate(error, 160)}")
                    continue
                stats["sources_ok"] += 1
                candidates.extend(item for item in items if allowed(item))

        # Google Trends (verified search interest) -----------------------------
        geo = cfg.get("geo", "VN")
        seed_trends = self._seed_section("google_trends")
        geos: list[str] = []
        for candidate_geo in [geo] + list(seed_trends.get("geos") or []):
            if candidate_geo and candidate_geo not in geos:
                geos.append(candidate_geo)
        trends_enabled = bool(seed_trends.get("enabled", True))
        if trends_enabled:
            for trend_geo in geos:
                try:
                    items, error = self.trends.collect(trend_geo, limit=15)
                    if error:
                        stats["sources_failed"] += 1
                        errors.append(f"Google Trends {trend_geo}: {truncate(error, 120)}")
                    else:
                        stats["sources_ok"] += 1
                        candidates.extend(item for item in items if allowed(item))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"Google Trends {trend_geo}: {exc}")

        # Optional Facebook public pages via official API ----------------------
        if settings.get("facebook.enabled", False) and facebook_source.status()["configured"]:
            try:
                items = facebook_source.collect(limit_per_page=5)
                if items:
                    stats["sources_ok"] += 1
                    candidates.extend(items)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Facebook: {exc}")

        # Enrich bare Google Trends keywords with real article links -----------
        self._enrich_trend_keywords(candidates, geo, limit=8)

        # Global de-duplication by canonical URL (keep richer/higher-weight copy)
        deduped: dict[str, Candidate] = {}
        for candidate in candidates:
            key = canonical_url(candidate.url) or candidate.url
            if not key:
                continue
            existing = deduped.get(key)
            if existing is None or candidate.weight > existing.weight:
                deduped[key] = candidate
        max_candidates = int(cfg.get("max_candidates", 400))
        items = list(deduped.values())
        items.sort(
            key=lambda c: (parse_datetime(c.published_at) or utcnow(), c.weight),
            reverse=True,
        )
        return items[:max_candidates], stats

    def _seed_section(self, section: str) -> dict:
        try:
            import json

            from .config import SOURCES_SEED_PATH

            seed = json.loads(SOURCES_SEED_PATH.read_text(encoding="utf-8"))
            return seed.get(section, {}) or {}
        except (OSError, ValueError):
            return {}

    def _enrich_trend_keywords(self, candidates: list[Candidate], geo: str,
                               limit: int = 8) -> None:
        keywords = [c for c in candidates if c.kind == "trend_keyword"][:limit]
        if not keywords:
            return

        def enrich(candidate: Candidate) -> None:
            if candidate.articles:
                return
            try:
                results = search_service.search(candidate.title, language="vi", limit=4)
            except SearchError:
                return
            for result in results:
                resolved = result.url
                if source_manager.is_aggregator(resolved):
                    resolved = source_manager.resolve_aggregator_url(resolved)
                if source_manager.is_aggregator(resolved):
                    continue
                candidate.articles.append(
                    {
                        "title": result.title,
                        "url": resolved,
                        "published_at": result.published_at,
                        "source_name": result.source_name or domain_of(resolved),
                        "kind": "search",
                    }
                )

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(enrich, keywords))
        for candidate in keywords:
            if candidate.articles:
                primary = candidate.articles[0]
                candidate.url = primary["url"]
                candidate.domain = domain_of(primary["url"])
                candidate.summary = primary["title"]

    # ---------------------------------------------------------------- cluster
    def _cluster(self, candidates: list[Candidate]) -> list[dict]:
        clusters: list[dict] = []
        for candidate in candidates:
            best_index, best_similarity = -1, 0.0
            for index, cluster in enumerate(clusters):
                similarity = title_similarity(candidate.title, cluster["title"])
                if similarity > best_similarity:
                    best_index, best_similarity = index, similarity
            if best_index >= 0 and best_similarity >= 0.55:
                self._add_member(clusters[best_index], candidate)
            else:
                cluster = {
                    "title": candidate.title,
                    "language": candidate.language,
                    "category": candidate.category,
                    "summary": candidate.summary,
                    "articles": [],
                    "members": [],
                    "traffic": candidate.approx_traffic,
                    "engagement": candidate.engagement,
                    "weight": candidate.weight,
                    "date_missing": candidate.date_missing,
                }
                self._add_member(cluster, candidate)
                clusters.append(cluster)
        return self._merge_overlapping(clusters)

    def _add_member(self, cluster: dict, candidate: Candidate) -> None:
        payload = candidate.article_payload()
        key = payload["canonical_url"]
        if key and key not in {a["canonical_url"] for a in cluster["articles"]}:
            cluster["articles"].append(payload)
        cluster["members"].append(candidate.to_dict())
        if candidate.approx_traffic:
            cluster["traffic"] = max(cluster["traffic"] or 0, candidate.approx_traffic)
        if candidate.engagement and candidate.engagement.get("verified"):
            cluster["engagement"] = candidate.engagement
        if candidate.weight >= cluster["weight"] and candidate.language == cluster["language"]:
            cluster["title"] = candidate.title
        elif candidate.language == "vi" and cluster["language"] != "vi" and candidate.weight >= 0.9:
            cluster["title"] = candidate.title
            cluster["language"] = "vi"
        if candidate.summary and len(candidate.summary) > len(cluster.get("summary") or ""):
            cluster["summary"] = candidate.summary
        if candidate.category != "general" and cluster["category"] == "general":
            cluster["category"] = candidate.category
        cluster["weight"] = max(cluster["weight"], candidate.weight)
        cluster["date_missing"] = all(
            parse_datetime(a.get("published_at")) is None for a in cluster["articles"]
        )

    def _merge_overlapping(self, clusters: list[dict]) -> list[dict]:
        url_owner: dict[str, int] = {}
        merged: list[dict] = []
        for cluster in clusters:
            owner = None
            for article in cluster["articles"]:
                key = article["canonical_url"]
                if key in url_owner:
                    owner = url_owner[key]
                    break
            if owner is None:
                merged.append(cluster)
                index = len(merged) - 1
            else:
                index = owner
                target = merged[index]
                existing = {a["canonical_url"] for a in target["articles"]}
                for article in cluster["articles"]:
                    if article["canonical_url"] not in existing:
                        target["articles"].append(article)
                target["members"].extend(cluster.get("members", []))
                target["traffic"] = max(target.get("traffic") or 0, cluster.get("traffic") or 0)
                if cluster.get("engagement"):
                    target["engagement"] = cluster["engagement"]
                if cluster["category"] != "general" and target["category"] == "general":
                    target["category"] = cluster["category"]
            for article in merged[index]["articles"]:
                url_owner[article["canonical_url"]] = index
        return merged

    def _unique_domains(self, cluster: dict) -> set[str]:
        domains = set()
        for article in cluster["articles"]:
            domain = article.get("domain") or domain_of(article.get("url", ""))
            if domain and domain not in ("news.google.com", "trends.google.com", "hnrss.org"):
                domains.add(domain)
        return domains

    # ------------------------------------------------------------- enrichment
    def _enrich_clusters(self, clusters: list[dict], target_lang: str,
                         should_cancel: Callable[[], bool] | None) -> None:
        if not settings.get("research.search_more_sources", True):
            return
        ranked = sorted(
            clusters,
            key=lambda c: (
                len(self._unique_domains(c)),
                parse_datetime(max((a.get("published_at") or "" for a in c["articles"]), default=""))
                or utcnow(),
            ),
        )
        targets = [c for c in ranked if len(self._unique_domains(c)) < 4][:10]

        def enrich(cluster: dict) -> None:
            try:
                results = search_service.find_related(cluster["title"], target_lang, limit=5)
            except SearchError:
                return
            existing_urls = {a["canonical_url"] for a in cluster["articles"]}
            added = 0
            for result in results:
                if added >= 3:
                    break
                url = result.url
                if source_manager.is_aggregator(url):
                    url = source_manager.resolve_aggregator_url(url)
                if not url or source_manager.is_aggregator(url):
                    continue
                key = canonical_url(url)
                if key in existing_urls:
                    continue
                if title_similarity(result.title, cluster["title"]) < 0.28:
                    continue
                cluster["articles"].append(
                    {
                        "title": result.title,
                        "url": url,
                        "canonical_url": key,
                        "domain": domain_of(url),
                        "published_at": result.published_at,
                        "excerpt": result.summary,
                        "content": "",
                        "content_chars": 0,
                        "kind": "search",
                        "language": result.language,
                        "reliability": source_manager.reliability(url),
                        "meta": {"source_name": result.source_name, "provider": result.provider},
                    }
                )
                existing_urls.add(key)
                added += 1
            if not cluster.get("summary"):
                cluster["summary"] = truncate(cluster["articles"][0].get("excerpt", ""), 300)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(enrich, cluster) for cluster in targets]
            for future in as_completed(futures):
                if should_cancel and should_cancel():
                    raise CancelledError("Đã hủy theo yêu cầu người dùng")
                try:
                    future.result()
                except Exception:  # noqa: BLE001 - enrichment is best-effort
                    continue

    # ---------------------------------------------------------------- editorial
    def _editorial_scores(self, clusters: list[dict], limit: int = 30) -> dict[int, dict]:
        targets = sorted(
            enumerate(clusters),
            key=lambda pair: (len(self._unique_domains(pair[1])), pair[1].get("weight", 1)),
            reverse=True,
        )[:limit]
        if not targets:
            return {}
        lines = []
        for index, cluster in targets:
            age = None
            dates = [parse_datetime(a.get("published_at")) for a in cluster["articles"]]
            dates = [d for d in dates if d]
            if dates:
                age = round((utcnow() - max(dates)).total_seconds() / 3600, 1)
            lines.append(
                json_dumps(
                    {
                        "id": index,
                        "title": truncate(cluster["title"], 220),
                        "summary": truncate(cluster.get("summary") or "", 260),
                        "sources": sorted(self._unique_domains(cluster))[:6],
                        "category_hint": cluster["category"],
                        "age_hours": age,
                        "google_trends_traffic": cluster.get("traffic"),
                    }
                )
            )
        prompt = (
            "Dưới đây là các chủ đề tin tức vừa thu thập từ nguồn công khai.\n"
            "Hãy đánh giá cho khán giả Facebook Việt Nam.\n\n"
            "Yêu cầu cho từng chủ đề (theo id):\n"
            "- audience_fit: 0-100, mức độ hấp dẫn với công chúng Việt Nam.\n"
            "- uniqueness: 0-100, mức độ khác biệt so với tin thường ngày.\n"
            f"- category: chọn 1 trong {', '.join(CATEGORY_OPTIONS)}.\n"
            "- summary_vi: 1-2 câu tóm tắt trung tính bằng tiếng Việt, CHỈ dùng thông tin trong tiêu đề/mô tả, "
            "không thêm số liệu hay tên mới.\n"
            "- reason: tối đa 20 từ, vì sao đáng chú ý.\n\n"
            f"Danh sách:\n" + "\n".join(lines) + "\n\nTrả về JSON theo schema."
        )
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                result = self.ollama.generate_json(
                    prompt,
                    system=EDITORIAL_SYSTEM,
                    schema=EDITORIAL_SCHEMA,
                    temperature=0.3,
                    num_predict=3000,
                )
                out: dict[int, dict] = {}
                valid_ids = {index for index, _ in targets}
                for item in result.get("items", []):
                    try:
                        item_id = int(item.get("id"))
                    except (TypeError, ValueError):
                        continue
                    if item_id not in valid_ids:
                        continue
                    category = str(item.get("category") or "general").strip().lower()
                    if category not in CATEGORY_OPTIONS:
                        category = "general"
                    out[item_id] = {
                        "audience_fit": max(0, min(100, int(item.get("audience_fit") or 50))),
                        "uniqueness": max(0, min(100, int(item.get("uniqueness") or 50))),
                        "category": category,
                        "summary_vi": truncate(str(item.get("summary_vi") or ""), 400),
                        "reason": truncate(str(item.get("reason") or ""), 160),
                    }
                if out:
                    return out
                raise ValueError("AI không trả về mục hợp lệ nào")
            except (OllamaError, ValueError) as exc:
                last_error = exc
                if attempt == 0 and isinstance(exc, OllamaError) and "kết nối" in str(exc).lower():
                    break
        if last_error:
            raise last_error
        return {}

    # ---------------------------------------------------------------- persist
    def _persist(self, clusters: list[dict], target_lang: str) -> dict:
        recent = self.db.recent_topics(limit=600)
        cutoff = utcnow() - timedelta(days=10)
        recent = [
            topic for topic in recent
            if (parse_datetime(topic.get("last_seen_at")) or cutoff) >= cutoff
        ]
        created, merged = 0, 0
        saved_topics: list[dict] = []
        for cluster in clusters:
            domain_count = len(self._unique_domains(cluster)) or 1
            match = None
            for topic in recent:
                if cluster["title"] and same_story(topic["title"], cluster["title"]):
                    match = topic
                    break
            if match:
                existing_urls = self.db.article_urls(match["id"])
                for article in cluster["articles"]:
                    if article["canonical_url"] in existing_urls:
                        continue
                    self.db.add_article(match["id"], article)
                self.db.update_topic(
                    match["id"],
                    {
                        "score": cluster["score"],
                        "score_breakdown": cluster["metrics"],
                        "metrics": cluster["metrics"],
                        "rationale": cluster["rationale"],
                        "source_count": max(match.get("source_count") or 0, domain_count),
                        "last_seen_at": utcnow_iso(),
                        "primary_url": match.get("primary_url") or (
                            cluster["articles"][0]["url"] if cluster["articles"] else ""
                        ),
                    },
                )
                merged += 1
                saved_topics.append(self._topic_summary(match["id"]))
                continue
            if created >= len(clusters):
                continue
            summary = cluster.get("editorial_summary") or truncate(
                cluster.get("summary") or (cluster["articles"][0].get("excerpt", "") if cluster["articles"] else ""),
                500,
            )
            topic_id = self.db.create_topic(
                {
                    "title": truncate(cluster["title"], 300),
                    "title_norm": cluster["title"].lower(),
                    "summary": summary,
                    "category": cluster["category"],
                    "language": cluster.get("language") or target_lang,
                    "score": cluster["score"],
                    "score_breakdown": cluster["metrics"],
                    "metrics": cluster["metrics"],
                    "rationale": cluster["rationale"],
                    "status": "new",
                    "source_count": domain_count,
                    "primary_url": cluster["articles"][0]["url"] if cluster["articles"] else "",
                    "dedupe_key": dedupe_key(cluster["title"]),
                    "meta": {
                        "caveats": cluster.get("caveats") or [],
                        "traffic": cluster.get("traffic"),
                        "engagement": cluster.get("engagement"),
                        "member_count": len(cluster.get("members") or []),
                    },
                }
            )
            for article in cluster["articles"]:
                self.db.add_article(topic_id, article)
            created += 1
            saved_topics.append(self._topic_summary(topic_id))
            recent.append(self.db.query_one("SELECT * FROM topics WHERE id = ?", (topic_id,)))
        return {"created": created, "merged": merged, "topics": [t for t in saved_topics if t]}

    def _topic_summary(self, topic_id: int) -> dict:
        row = self.db.query_one(
            "SELECT id, title, score, category, source_count, status, language FROM topics WHERE id = ?",
            (topic_id,),
        )
        return row or {}


trend_finder = TrendFinder()
