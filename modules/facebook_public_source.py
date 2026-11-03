"""Optional, authorized Facebook public-page collection via the official Graph API.

No login bypass, scraping or private-content access is implemented. The source
stays disabled until the user provides their own token with the right scopes.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from .config import get_secret, settings
from .trend_models import Candidate
from .utils import canonical_url, parse_datetime, truncate

GRAPH_VERSION = "v23.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"

_FIELDS = (
    "id,message,created_time,permalink_url,shares,"
    "reactions.summary(total_count).limit(0),comments.summary(total_count).limit(0)"
)


class FacebookPublicSource:
    name = "facebook_graph"

    @property
    def token(self) -> str:
        return get_secret("facebook")

    @property
    def page_ids(self) -> list[str]:
        cfg = settings.get("facebook.page_ids", []) or []
        return [str(pid).strip() for pid in cfg if str(pid).strip()]

    def status(self) -> dict:
        token = bool(self.token)
        reason = ""
        if not token:
            reason = (
                "Chưa cấu hình FACEBOOK_ACCESS_TOKEN. Nguồn Facebook ở trạng thái tắt; "
                "ứng dụng không đăng nhập hay thu thập dữ liệu trái phép."
            )
        elif not self.page_ids:
            reason = "Đã có token nhưng chưa khai báo Page ID công khai nào."
        return {
            "configured": token and bool(self.page_ids),
            "token_present": token,
            "page_count": len(self.page_ids),
            "reason": reason,
            "mode": "official Graph API (authorized public pages only)",
        }

    def collect(self, limit_per_page: int = 5) -> list[Candidate]:
        status = self.status()
        if not status["configured"]:
            return []
        candidates: list[Candidate] = []
        for page_id in self.page_ids:
            params = {
                "fields": _FIELDS,
                "limit": max(1, min(limit_per_page, 25)),
                "access_token": self.token,
            }
            try:
                with httpx.Client(timeout=httpx.Timeout(20.0)) as client:
                    resp = client.get(f"{GRAPH_BASE}/{quote(page_id)}/posts", params=params)
                    if resp.status_code >= 400:
                        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                        err = (body.get("error") or {}) if isinstance(body, dict) else {}
                        raise RuntimeError(
                            f"Graph API {resp.status_code}: {err.get('message', 'không xác định')}"
                        )
                    data = resp.json()
            except (httpx.HTTPError, ValueError, RuntimeError):
                continue
            for post in data.get("data", []):
                message = (post.get("message") or "").strip()
                if len(message) < 40:
                    continue
                published = parse_datetime(post.get("created_time"))
                reactions = ((post.get("reactions") or {}).get("summary") or {}).get("total_count")
                comments = ((post.get("comments") or {}).get("summary") or {}).get("total_count")
                shares = (post.get("shares") or {}).get("count")
                engagement = {
                    "reactions": int(reactions or 0),
                    "comments": int(comments or 0),
                    "shares": int(shares or 0),
                    "source": "facebook_graph_api",
                    "verified": True,
                }
                title = truncate(message.split("\n")[0].strip(), 200, "") or truncate(message, 120, "")
                candidates.append(
                    Candidate(
                        title=title,
                        url=post.get("permalink_url") or f"https://www.facebook.com/{post.get('id')}",
                        summary=truncate(message, 1200),
                        published_at=published.isoformat() if published else None,
                        language="vi",
                        category="social",
                        source_name=f"Facebook Page {page_id}",
                        source_key="facebook-public",
                        kind="facebook",
                        weight=0.7,
                        engagement=engagement,
                        date_missing=published is None,
                        meta={"page_id": page_id, "post_id": post.get("id")},
                    )
                )
        return candidates


facebook_source = FacebookPublicSource()
