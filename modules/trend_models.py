"""Shared data structures for the trend discovery pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .source_manager import source_manager
from .utils import canonical_url, domain_of


@dataclass
class Candidate:
    title: str
    url: str
    summary: str = ""
    published_at: str | None = None
    language: str = "vi"
    category: str = "general"
    source_name: str = ""
    source_key: str = ""
    kind: str = "rss"
    weight: float = 1.0
    domain: str = ""
    approx_traffic: int | None = None
    engagement: dict | None = None
    date_missing: bool = False
    articles: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.domain:
            self.domain = domain_of(self.url)
        if self.url:
            self.url = canonical_url(self.url)

    @property
    def is_aggregator(self) -> bool:
        return source_manager.is_aggregator(self.url or "")

    def article_payload(self) -> dict:
        publisher_url = (self.meta or {}).get("publisher_url") or ""
        identity_url = publisher_url if publisher_url and self.is_aggregator else self.url
        return {
            "title": self.title,
            "url": self.url,
            "canonical_url": canonical_url(self.url),
            "domain": domain_of(identity_url),
            "published_at": self.published_at,
            "excerpt": self.summary[:1200],
            "content": "",
            "content_chars": 0,
            "kind": self.kind,
            "language": self.language,
            "reliability": source_manager.reliability(identity_url),
            "meta": {
                "source_name": self.source_name,
                "source_key": self.source_key,
                "publisher_url": publisher_url,
            },
        }

    def to_dict(self) -> dict:
        return asdict(self)
