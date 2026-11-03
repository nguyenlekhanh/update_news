"""RSS / Google News / Google Trends collectors (public feeds only)."""

from __future__ import annotations

import html
import re
from datetime import timedelta

import feedparser
import httpx

from .config import settings
from .source_manager import USER_AGENT
from .trend_models import Candidate
from .utils import canonical_url, content_tokens, is_vietnamese, parse_datetime, strip_accents, truncate, utcnow

# Single generic words that appear in Google Trends but make meaningless topics.
TREND_KEYWORD_BLOCKLIST = {
    "dang", "tai", "xem", "video", "tin", "moi", "hot", "hom nay", "truc tiep",
    "la gi", "review", "download", "cai dat", "tuyen dung", "viec lam", "gia vang",
    "ty gia", "lich thi dau", "ket qua", "truyen hinh", "phim", "nhac", "game",
}


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


class RSSCollector:
    """Fetches configured public RSS feeds and converts entries into candidates."""

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    def _fetch(self, url: str) -> tuple[bytes | None, str | None]:
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.timeout),
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/rss+xml,application/xml,text/xml,*/*",
                },
                follow_redirects=True,
            ) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.content, None
        except httpx.HTTPError as exc:
            return None, f"{type(exc).__name__}: {exc}"

    def collect_source(
        self,
        source: dict,
        lookback_hours: int | None = None,
        limit: int = 40,
    ) -> tuple[list[Candidate], str | None]:
        lookback = int(lookback_hours or settings.get("trends.lookback_hours", 36))
        cutoff = utcnow() - timedelta(hours=lookback)
        raw, error = self._fetch(source.get("url", ""))
        if raw is None:
            return [], error or "Không tải được feed"
        feed = feedparser.parse(raw)
        if getattr(feed, "bozo", 0) and not feed.entries:
            return [], "Feed không hợp lệ hoặc rỗng"
        candidates: list[Candidate] = []
        undated = 0
        for entry in feed.entries:
            title = _clean(entry.get("title") or "")
            link = entry.get("link") or ""
            if not title or not link or len(title) < 12:
                continue
            published = parse_datetime(
                entry.get("published") or entry.get("updated") or entry.get("created")
            )
            published_iso = published.isoformat() if published else None
            date_missing = published is None
            if published and published < cutoff:
                continue
            if date_missing:
                undated += 1
                if undated > 12:
                    continue
            summary = _clean(
                entry.get("summary")
                or entry.get("description")
                or (entry.get("content") or [{}])[0].get("value", "")
            )
            language = source.get("language") or ("vi" if is_vietnamese(title + summary) else "en")
            entry_source = entry.get("source") or {}
            publisher_name = entry_source.get("title") if isinstance(entry_source, dict) else ""
            publisher_url = entry_source.get("href") if isinstance(entry_source, dict) else ""
            if publisher_name:
                # Google News appends " - Publisher" to every headline.
                title = re.sub(
                    r"\s*[-–|]\s*(?:Báo\s+)?" + re.escape(publisher_name.strip()) + r"\s*$",
                    "", title, flags=re.IGNORECASE,
                ).strip() or title
            candidates.append(
                Candidate(
                    title=truncate(title, 300, ""),
                    url=link,
                    summary=truncate(summary, 1200),
                    published_at=published_iso,
                    language=language,
                    category=source.get("category") or "general",
                    source_name=publisher_name or source.get("name") or source.get("source_key", ""),
                    source_key=source.get("source_key", ""),
                    kind="rss",
                    weight=float(source.get("weight") or 1.0),
                    date_missing=date_missing,
                    meta={"publisher_url": publisher_url or "", "feed": source.get("name", "")},
                )
            )
            if len(candidates) >= limit:
                break
        return candidates, None


class GoogleTrendsCollector(RSSCollector):
    """Daily trending searches with verified approximate traffic."""

    url_template = "https://trends.google.com/trending/rss?geo={geo}"

    @staticmethod
    def _parse_traffic(value) -> int | None:
        if value is None:
            return None
        digits = re.sub(r"[^\d]", "", str(value))
        return int(digits) if digits else None

    def collect(self, geo: str = "VN", limit: int = 20) -> tuple[list[Candidate], str | None]:
        url = self.url_template.format(geo=geo)
        raw, error = self._fetch(url)
        if raw is None:
            return [], error or "Không tải được Google Trends"
        feed = feedparser.parse(raw)
        candidates: list[Candidate] = []
        for entry in feed.entries[:limit]:
            title = _clean(entry.get("title") or "")
            if not title or len(title.strip()) < 4:
                continue
            folded = strip_accents(title).strip().lower()
            if folded in TREND_KEYWORD_BLOCKLIST or not content_tokens(title):
                continue
            traffic = self._parse_traffic(
                entry.get("ht_approx_traffic")
                or (entry.get("ht_approx_traffic") if isinstance(entry, dict) else None)
            )
            news_items = entry.get("ht_news_item") or []
            if isinstance(news_items, dict):
                news_items = [news_items]
            articles: list[dict] = []
            for item in news_items or []:
                item_url = (item.get("ht_news_item_url") or "").strip()
                item_title = _clean(item.get("ht_news_item_title") or title)
                if not item_url:
                    continue
                articles.append(
                    {
                        "title": item_title,
                        "url": canonical_url(item_url),
                        "published_at": None,
                        "source_name": _clean(item.get("ht_news_item_source") or ""),
                        "kind": "google_trends",
                    }
                )
            primary = articles[0]["url"] if articles else f"https://trends.google.com/trends/explore?geo={geo}&q={title}"
            candidates.append(
                Candidate(
                    title=title,
                    url=primary,
                    summary="",
                    published_at=None,
                    language="vi" if geo == "VN" else "en",
                    category="general",
                    source_name=f"Google Trends {geo}",
                    source_key=f"google-trends-{geo.lower()}",
                    kind="trend_keyword",
                    weight=1.25,
                    approx_traffic=traffic,
                    date_missing=True,
                    articles=articles,
                )
            )
        return candidates, None
