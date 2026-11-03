"""Source fetching, article extraction, URL canonicalisation and reliability scoring.

The module only retrieves publicly reachable pages, honours robots.txt and never
attempts to bypass authentication, paywalls or rate limits.
"""

from __future__ import annotations

import re
import threading
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura
from bs4 import BeautifulSoup

from .config import settings
from .utils import (
    canonical_url,
    domain_of,
    is_vietnamese,
    parse_datetime,
    truncate,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 AIViralStudio/1.0"
)

# Editorial trust priors (0..1). Used only as a heuristic signal, clearly labelled.
DOMAIN_RELIABILITY: dict[str, float] = {
    "reuters.com": 0.96, "apnews.com": 0.96, "bbc.com": 0.94, "bbc.co.uk": 0.94,
    "nytimes.com": 0.92, "theguardian.com": 0.9, "npr.org": 0.9, "aljazeera.com": 0.86,
    "cnn.com": 0.82, "washingtonpost.com": 0.9, "economist.com": 0.92,
    "bloomberg.com": 0.9, "ft.com": 0.92, "wallstreetjournal.com": 0.9,
    "vnexpress.net": 0.9, "tuoitre.vn": 0.88, "thanhnien.vn": 0.86,
    "vietnamnet.vn": 0.84, "dantri.com.vn": 0.8, "nhandan.vn": 0.9,
    "vietnamplus.vn": 0.86, "tienphong.vn": 0.82, "zingnews.vn": 0.8,
    "laodong.vn": 0.8, "sggp.org.vn": 0.84, "baochinhphu.vn": 0.9,
    "kenh14.vn": 0.7, "24h.com.vn": 0.65, "genk.vn": 0.74, "gamek.vn": 0.7,
    "techcrunch.com": 0.78, "theverge.com": 0.78, "arstechnica.com": 0.82,
    "wired.com": 0.82, "technologyreview.com": 0.88, "nature.com": 0.96,
    "science.org": 0.95, "sciencedaily.com": 0.8, "space.com": 0.78,
    "espn.com": 0.8, "variety.com": 0.78, "hollywoodreporter.com": 0.8,
    "news.google.com": 0.68, "hnrss.org": 0.55, "ycombinator.com": 0.6,
    "medium.com": 0.5, "facebook.com": 0.45, "x.com": 0.4, "twitter.com": 0.4,
    "youtube.com": 0.45, "tiktok.com": 0.35, "reddit.com": 0.45,
}

# Domains that aggregate other outlets rather than report themselves.
AGGREGATOR_DOMAINS = {"news.google.com"}

_VI_STOP_FETCH = ("video.", "podcast", "live.", "shop.", "afamily.", "eva.vn")


class SourceManager:
    def __init__(self):
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_lock = threading.Lock()
        self._last_request: dict[str, float] = {}
        self._polite_delay = 0.6

    # ------------------------------------------------------------- utilities
    @staticmethod
    def domain(url: str) -> str:
        return domain_of(url)

    @staticmethod
    def canonical(url: str) -> str:
        return canonical_url(url)

    @staticmethod
    def is_aggregator(url: str) -> bool:
        return domain_of(url) in AGGREGATOR_DOMAINS

    def reliability(self, url: str) -> float:
        host = domain_of(url)
        if not host:
            return 0.5
        if host in DOMAIN_RELIABILITY:
            return DOMAIN_RELIABILITY[host]
        parts = host.split(".")
        for i in range(1, len(parts) - 1):
            candidate = ".".join(parts[i:])
            if candidate in DOMAIN_RELIABILITY:
                return DOMAIN_RELIABILITY[candidate]
        if host.endswith(".gov") or host.endswith(".gov.vn"):
            return 0.9
        if host.endswith(".edu") or host.endswith(".edu.vn"):
            return 0.85
        return 0.5

    def _robots_allowed(self, url: str) -> bool:
        if not settings.get("research.respect_robots", True):
            return True
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return True
        host_key = f"{parsed.scheme}://{parsed.netloc}"
        with self._robots_lock:
            if host_key in self._robots:
                parser = self._robots[host_key]
            else:
                parser = None
                try:
                    resp = httpx.get(
                        urljoin(host_key, "/robots.txt"),
                        timeout=httpx.Timeout(6.0),
                        headers={"User-Agent": USER_AGENT},
                        follow_redirects=True,
                    )
                    if resp.status_code == 200 and resp.text.strip():
                        parser = RobotFileParser()
                        parser.parse(resp.text.splitlines())
                    elif resp.status_code in (401, 403):
                        parser = RobotFileParser()
                        parser.disallow_all = True
                except httpx.HTTPError:
                    parser = None
                self._robots[host_key] = parser
        if parser is None:
            return True
        try:
            return parser.can_fetch(USER_AGENT, url)
        except Exception:  # noqa: BLE001
            return True

    def _throttle(self, host: str) -> None:
        last = self._last_request.get(host)
        now = time.monotonic()
        if last and now - last < self._polite_delay:
            time.sleep(self._polite_delay - (now - last))
        self._last_request[host] = time.monotonic()

    def fetch(self, url: str, timeout: float | None = None,
              max_bytes: int = 2_500_000) -> httpx.Response | None:
        if not url or not url.startswith(("http://", "https://")):
            return None
        if not self._robots_allowed(url):
            return None
        host = domain_of(url)
        self._throttle(host)
        timeout_value = timeout or float(settings.get("research.fetch_timeout_seconds", 25))
        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=httpx.Timeout(timeout_value),
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "vi,en;q=0.8",
                },
            ) as client:
                with client.stream("GET", url) as resp:
                    if resp.status_code >= 400:
                        return None
                    chunks, total = [], 0
                    for chunk in resp.iter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total > max_bytes:
                            break
                    body = b"".join(chunks)
                    # The body is already decompressed by httpx; drop transfer
                    # headers so .text does not try to gunzip it a second time.
                    headers = {
                        key: value for key, value in resp.headers.items()
                        if key.lower() not in (
                            "content-encoding", "content-length", "transfer-encoding"
                        )
                    }
                    return httpx.Response(
                        resp.status_code,
                        headers=headers,
                        content=body,
                        request=resp.request,
                    )
        except (httpx.HTTPError, OSError):
            return None

    # ------------------------------------------------------- google news urls
    def resolve_aggregator_url(self, url: str) -> str:
        """Best-effort resolution of a Google News redirect to the publisher URL."""
        if not self.is_aggregator(url):
            return url
        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=httpx.Timeout(12.0),
                headers={"User-Agent": USER_AGENT},
            ) as client:
                resp = client.get(url)
                final = str(resp.url)
                if not self.is_aggregator(final):
                    return final
                match = re.search(r'data-n-au="(https?://[^"]+)"', resp.text or "")
                if match:
                    return match.group(1)
                match = re.search(
                    r'<a[^>]+href="(https?://(?!news\.google)[^"]+)"[^>]*>', resp.text or ""
                )
                if match:
                    return match.group(1)
        except (httpx.HTTPError, OSError):
            return url
        return url

    # ------------------------------------------------------------- extraction
    @staticmethod
    def _meta(html: str, *names: str) -> str:
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:  # noqa: BLE001
            return ""
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find(
                "meta", attrs={"name": name}
            )
            if tag and tag.get("content"):
                return str(tag["content"]).strip()
        return ""

    def extract_article(self, url: str, max_chars: int | None = None) -> dict:
        """Download and extract the readable body of a public article."""
        limit = int(max_chars or settings.get("research.max_chars_per_article", 5000))
        result = {
            "url": url,
            "canonical_url": canonical_url(url),
            "domain": domain_of(url),
            "title": "",
            "text": "",
            "published_at": None,
            "language": "",
            "ok": False,
            "error": "",
        }
        resolved = self.resolve_aggregator_url(url)
        if resolved and resolved != url:
            result["resolved_url"] = resolved
            result["canonical_url"] = canonical_url(resolved)
            result["domain"] = domain_of(resolved)
        response = self.fetch(resolved)
        if response is None:
            result["error"] = "Không tải được bài viết (bị chặn, lỗi mạng hoặc robots.txt)"
            return result
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type and "xml" not in content_type:
            result["error"] = f"Định dạng không hỗ trợ: {content_type}"
            return result
        html = response.text
        extracted = None
        try:
            extracted = trafilatura.extract(
                html,
                url=resolved,
                include_comments=False,
                include_tables=False,
                favor_precision=True,
                no_fallback=False,
            )
        except Exception:  # noqa: BLE001
            extracted = None
        if not extracted or len(extracted) < 200:
            try:
                soup = BeautifulSoup(html, "lxml")
                for tag in soup(["script", "style", "nav", "footer", "aside", "form", "noscript"]):
                    tag.decompose()
                paragraphs = [
                    p.get_text(" ", strip=True)
                    for p in soup.find_all("p")
                    if len(p.get_text(strip=True)) > 40
                ]
                fallback = "\n\n".join(paragraphs)
                if len(fallback) > len(extracted or ""):
                    extracted = fallback
            except Exception:  # noqa: BLE001
                pass
        if not extracted:
            result["error"] = "Không trích xuất được nội dung bài viết"
            return result

        title = self._meta(html, "og:title", "twitter:title") or ""
        if not title:
            try:
                soup = BeautifulSoup(html, "lxml")
                if soup.title and soup.title.string:
                    title = soup.title.string.strip()
            except Exception:  # noqa: BLE001
                title = ""
        published = (
            self._meta(html, "article:published_time", "datePublished", "pubdate", "publish-date")
            or ""
        )
        if not published:
            try:
                soup = BeautifulSoup(html, "lxml")
                time_tag = soup.find("time")
                if time_tag and (time_tag.get("datetime") or time_tag.get_text(strip=True)):
                    published = time_tag.get("datetime") or time_tag.get_text(strip=True)
            except Exception:  # noqa: BLE001
                published = ""

        result.update(
            title=title.strip(),
            text=truncate(extracted.strip(), limit, suffix=""),
            published_at=(parse_datetime(published).isoformat() if parse_datetime(published) else None),
            language="vi" if is_vietnamese(extracted[:400]) else "en",
            ok=True,
        )
        result["chars"] = len(result["text"])
        return result


source_manager = SourceManager()
