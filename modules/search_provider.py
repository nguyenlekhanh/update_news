"""Modular search providers used to cross-check topics across independent outlets.

Providers never circumvent authentication: they use public RSS endpoints or
documented paid APIs with the user's own key.
"""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from urllib.parse import quote_plus

import feedparser
import httpx

from .config import get_secret, settings
from .utils import canonical_url, domain_of, parse_datetime, strip_accents

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 AIViralStudio/1.0"


@dataclass
class SearchResult:
    title: str
    url: str
    domain: str
    published_at: str | None = None
    summary: str = ""
    source_name: str = ""
    provider: str = ""
    language: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class SearchError(RuntimeError):
    pass


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


class SearchProvider:
    name = "base"
    requires_key = False

    @property
    def available(self) -> bool:
        return True

    def search(self, query: str, language: str = "vi", limit: int = 8) -> list[SearchResult]:
        raise NotImplementedError


class GoogleNewsRSSProvider(SearchProvider):
    """Keyless public Google News RSS search."""

    name = "google_news_rss"

    def search(self, query: str, language: str = "vi", limit: int = 8) -> list[SearchResult]:
        if language == "vi":
            params = "hl=vi&gl=VN&ceid=VN:vi"
        else:
            params = "hl=en-US&gl=US&ceid=US:en"
        url = f"https://news.google.com/rss/search?q={quote_plus(query)}&{params}"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(15.0),
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            ) as client:
                resp = client.get(url)
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
        except (httpx.HTTPError, ValueError) as exc:
            raise SearchError(f"Google News RSS không phản hồi: {exc}") from exc
        results: list[SearchResult] = []
        for entry in feed.entries[: max(limit * 2, limit)]:
            link = entry.get("link") or ""
            if not link:
                continue
            published = parse_datetime(entry.get("published") or entry.get("updated"))
            source = entry.get("source") or {}
            source_name = source.get("title") if isinstance(source, dict) else ""
            results.append(
                SearchResult(
                    title=_clean(entry.get("title") or ""),
                    url=link,
                    domain=domain_of(link),
                    published_at=published.isoformat() if isinstance(published, datetime) else None,
                    summary=_clean(entry.get("summary") or "")[:600],
                    source_name=source_name or "Google News",
                    provider=self.name,
                    language=language,
                )
            )
        return results[:limit]


class SerperNewsProvider(SearchProvider):
    name = "serper"
    requires_key = True

    @property
    def available(self) -> bool:
        return bool(get_secret("serper"))

    def search(self, query: str, language: str = "vi", limit: int = 8) -> list[SearchResult]:
        if not self.available:
            raise SearchError("Chưa cấu hình SERPER_API_KEY")
        payload = {
            "q": query,
            "gl": "vn" if language == "vi" else "us",
            "hl": "vi" if language == "vi" else "en",
            "num": max(1, min(limit, 20)),
        }
        try:
            with httpx.Client(timeout=httpx.Timeout(20.0)) as client:
                resp = client.post(
                    "https://google.serper.dev/news",
                    json=payload,
                    headers={
                        "X-API-KEY": get_secret("serper"),
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise SearchError(f"Serper API lỗi: {exc}") from exc
        results = []
        for item in (data.get("news") or [])[:limit]:
            link = item.get("link") or ""
            results.append(
                SearchResult(
                    title=_clean(item.get("title") or ""),
                    url=link,
                    domain=domain_of(link),
                    published_at=item.get("date"),
                    summary=_clean(item.get("snippet") or "")[:600],
                    source_name=item.get("source") or "",
                    provider=self.name,
                    language=language,
                )
            )
        return results


class BraveNewsProvider(SearchProvider):
    name = "brave"
    requires_key = True

    @property
    def available(self) -> bool:
        return bool(get_secret("brave"))

    def search(self, query: str, language: str = "vi", limit: int = 8) -> list[SearchResult]:
        if not self.available:
            raise SearchError("Chưa cấu hình BRAVE_SEARCH_API_KEY")
        params = {
            "q": query,
            "count": max(1, min(limit, 20)),
            "country": "vn" if language == "vi" else "us",
            "search_lang": "vi" if language == "vi" else "en",
        }
        try:
            with httpx.Client(timeout=httpx.Timeout(20.0)) as client:
                resp = client.get(
                    "https://api.search.brave.com/res/v1/news/search",
                    params=params,
                    headers={
                        "X-Subscription-Token": get_secret("brave"),
                        "Accept": "application/json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise SearchError(f"Brave Search lỗi: {exc}") from exc
        results = []
        for item in (data.get("results") or [])[:limit]:
            link = item.get("url") or ""
            results.append(
                SearchResult(
                    title=_clean(item.get("title") or ""),
                    url=link,
                    domain=domain_of(link),
                    published_at=item.get("age") or item.get("page_age"),
                    summary=_clean(item.get("description") or "")[:600],
                    source_name=(item.get("meta_url") or {}).get("hostname", ""),
                    provider=self.name,
                    language=language,
                )
            )
        return results


class SearchService:
    """Picks the best available provider; Google News RSS is always available."""

    def __init__(self):
        self.providers: list[SearchProvider] = [
            SerperNewsProvider(),
            BraveNewsProvider(),
            GoogleNewsRSSProvider(),
        ]

    def status(self) -> dict:
        return {
            "providers": [
                {"name": p.name, "available": p.available, "requires_key": p.requires_key}
                for p in self.providers
            ],
            "active": self.active_provider().name,
        }

    def active_provider(self) -> SearchProvider:
        for provider in self.providers:
            if provider.available:
                return provider
        return self.providers[-1]

    def search(self, query: str, language: str | None = None, limit: int = 8) -> list[SearchResult]:
        if not query.strip():
            return []
        lang = language or settings.get("writer.language", "vi")
        provider = self.active_provider()
        try:
            results = provider.search(query, language=lang, limit=limit)
        except SearchError:
            fallback = GoogleNewsRSSProvider()
            if provider.name == fallback.name:
                raise
            results = fallback.search(query, language=lang, limit=limit)
        # Normalise & de-duplicate
        seen: set[str] = set()
        unique: list[SearchResult] = []
        for item in results:
            key = canonical_url(item.url) or item.url
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    def find_related(self, topic_title: str, language: str | None = None,
                     limit: int = 6) -> list[SearchResult]:
        lang = language or settings.get("writer.language", "vi")
        query = re.sub(r"\s+", " ", strip_accents(topic_title).strip() if lang == "en" else topic_title.strip())
        query = query[:180]
        try:
            return self.search(query, language=lang, limit=limit)
        except SearchError:
            return []


search_service = SearchService()
