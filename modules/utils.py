"""Small shared helpers used across the content studio modules."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

UTC = timezone.utc

# Query parameters that never change the identity of an article.
_TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "utm_name",
    "ref",
    "ref_src",
    "ref_url",
    "spm",
    "share",
    "output",
    "cmpid",
    "icid",
    "s_kwcid",
    "at_medium",
    "at_campaign",
    "ito",
    "ns_mchannel",
    "ns_source",
    "ns_campaign",
}

_VIETNAMESE_DIACRITICS = set(
    "ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ"
)

_STOPWORDS = {
    # Vietnamese
    "và", "của", "có", "cho", "với", "khi", "thì", "là", "các", "những", "một",
    "được", "trong", "người", "này", "đã", "sẽ", "từ", "để", "về", "ra", "vào",
    "theo", "như", "nhưng", "hay", "cũng", "rất", "đang", "bị", "bởi", "vì",
    "nên", "ở", "tới", "sau", "trước", "hơn", "nhiều", "không", "tại", "lại",
    "việt", "nam",
    # English
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is",
    "are", "was", "were", "be", "been", "that", "this", "these", "those", "as",
    "at", "by", "from", "it", "its", "his", "her", "their", "your", "our",
    "after", "before", "over", "under", "says", "said", "will", "would", "new",
    "you", "how", "why", "what", "who", "when", "where", "not", "but", "has",
    "have", "had", "into", "about", "more", "than", "can", "could", "up", "out",
    "we", "they", "he", "she", "i", "am", "do", "does", "did", "just", "now",
}

# Tokens are accent-folded before comparison, so the stopword list must be folded
# too; otherwise Vietnamese entries such as "của" or "và" never match.
_STOPWORDS = {
    "".join(
        ch
        for ch in unicodedata.normalize("NFD", word.replace("đ", "d").replace("Đ", "D"))
        if unicodedata.category(ch) != "Mn"
    )
    for word in _STOPWORDS
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def utcnow_iso() -> str:
    return utcnow().replace(microsecond=0).isoformat()


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(microsecond=0).isoformat()


def parse_datetime(value) -> datetime | None:
    """Best-effort parsing of ISO strings, RFC822 dates and unix timestamps."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    # feedparser gives struct_time
    if isinstance(value, (tuple, list)) and len(value) >= 6:
        try:
            return datetime(*value[:6], tzinfo=UTC)
        except (TypeError, ValueError):
            return None
    # 2024-05-01T10:20:30Z / 2024-05-01 10:20:30
    m = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?"
        r"(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?$",
        text,
    )
    if m:
        year, month, day, hour, minute = (int(m.group(i)) for i in range(1, 6))
        second = int(m.group(6) or 0)
        tz = m.group(7)
        try:
            dt = datetime(year, month, day, hour, minute, second, tzinfo=UTC)
        except ValueError:
            return None
        if tz and tz != "Z":
            sign = 1 if tz[0] == "+" else -1
            tz_digits = tz[1:].replace(":", "")
            offset = timedelta(hours=int(tz_digits[:2]), minutes=int(tz_digits[2:4]))
            dt = dt - sign * offset
        return dt
    # RFC 822 e.g. "Wed, 01 May 2024 10:20:30 GMT"
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z",
                "%d %b %Y %H:%M:%S %Z", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def age_hours(value) -> float | None:
    dt = parse_datetime(value)
    if not dt:
        return None
    return max(0.0, (utcnow() - dt).total_seconds() / 3600.0)


def canonical_url(url: str) -> str:
    """Strip tracking parameters and normalise the host/path of a URL."""
    if not url:
        return ""
    url = url.strip()
    try:
        parts = urlparse(url)
    except ValueError:
        return url
    if not parts.scheme:
        return url
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    path = re.sub(r"/+$", "", parts.path or "/") or "/"
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS
    ]
    return urlunparse(("https" if parts.scheme in ("http", "https") else parts.scheme,
                       host, path, "", urlencode(query), ""))


def domain_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def strip_accents(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn"
    )


def is_vietnamese(text: str) -> bool:
    lowered = (text or "").lower()
    return any(ch in _VIETNAMESE_DIACRITICS for ch in lowered)


def normalize_title(title: str) -> str:
    text = strip_accents((title or "").lower())
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def content_tokens(text: str) -> set[str]:
    tokens = set(normalize_title(text).split())
    return {t for t in tokens if len(t) > 1 and t not in _STOPWORDS}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def title_similarity(a: str, b: str) -> float:
    """Similarity tuned for news headlines (token Jaccard + containment)."""
    ta, tb = content_tokens(a), content_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    jac = inter / len(ta | tb)
    containment = inter / min(len(ta), len(tb))
    return round(max(jac, containment * 0.92), 4)


def dedupe_key(title: str) -> str:
    tokens = sorted(content_tokens(title))
    digest = hashlib.sha1(" ".join(tokens).encode("utf-8")).hexdigest()
    return digest[:24]


def sha1_short(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", "ignore")).hexdigest()[:16]


def slugify(text: str, max_len: int = 60) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", strip_accents((text or "").lower())).strip("-")
    return (base[:max_len] or "item").strip("-")


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    text = (text or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: limit - len(suffix)].rstrip() + suffix


def json_loads_safe(raw, default):
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


_VI_CHAR_MAP = str.maketrans(
    "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ",
    "aaaaaaaaaaaaaaaaaeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyyd",
)


def ascii_fold(text: str) -> str:
    return (text or "").lower().translate(_VI_CHAR_MAP)


def count_words(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text or "", flags=re.UNICODE))
