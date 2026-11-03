"""Export reviewed Facebook content to files. Publishing is always manual."""

from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path

from .config import EXPORT_DIR
from .database import Database, db as default_db
from .utils import utcnow_iso

MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
}

DISCLOSURE = (
    "Ảnh minh họa được tạo bởi AI (ComfyUI) theo phong cách biên tập, "
    "không phải ảnh tư liệu về sự kiện hay nhân vật thật."
)


class Exporter:
    def __init__(self, database: Database | None = None):
        self.db = database or default_db

    def export(self, fmt: str = "md", status: str | None = "approved",
               post_ids: list[int] | None = None,
               mark_exported: bool = False) -> dict:
        fmt = (fmt or "md").lower()
        if fmt not in MEDIA_TYPES:
            raise ValueError(f"Định dạng không hỗ trợ: {fmt}")
        posts = self._collect(status, post_ids)
        if not posts:
            raise ValueError("Không có bài viết nào để xuất với bộ lọc hiện tại")
        payload = self._render(fmt, posts)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        label = status or ("selected" if post_ids else "all")
        filename = f"facebook_content_{label}_{stamp}.{fmt}"
        path = EXPORT_DIR / filename
        if fmt == "csv":
            path.write_bytes(payload.encode("utf-8-sig"))
        else:
            path.write_text(payload, encoding="utf-8")
        if mark_exported:
            for post in posts:
                self.db.update_post(post["id"], {"status": "exported"})
        self.db.add_log(
            f"Xuất {len(posts)} bài viết định dạng {fmt}: {filename}", "info", "export"
        )
        return {
            "path": str(path),
            "filename": filename,
            "media_type": MEDIA_TYPES[fmt],
            "count": len(posts),
        }

    def _collect(self, status: str | None, post_ids: list[int] | None) -> list[dict]:
        if post_ids:
            posts = [self.db.get_post(pid) for pid in post_ids]
            return [p for p in posts if p]
        records = self.db.list_posts(status=status, limit=500)
        return [self.db.get_post(row["id"]) for row in records]

    def _render(self, fmt: str, posts: list[dict]) -> str:
        if fmt == "json":
            return json.dumps(
                {
                    "exported_at": utcnow_iso(),
                    "disclosure": DISCLOSURE,
                    "count": len(posts),
                    "posts": [self._as_dict(p) for p in posts],
                },
                ensure_ascii=False,
                indent=2,
            )
        if fmt == "csv":
            return self._render_csv(posts)
        if fmt == "txt":
            return self._render_txt(posts)
        return self._render_md(posts)

    @staticmethod
    def _as_dict(post: dict) -> dict:
        images = post.get("images") or []
        return {
            "id": post.get("id"),
            "topic": post.get("topic_title"),
            "headline": post.get("headline"),
            "alt_headlines": post.get("alt_headlines") or [],
            "main_post": post.get("main_post"),
            "short_post": post.get("short_post"),
            "long_post": post.get("long_post"),
            "hook": post.get("hook"),
            "closing_question": post.get("closing_question"),
            "hashtags": post.get("hashtags") or [],
            "status": post.get("status"),
            "style": post.get("style"),
            "tone": post.get("tone"),
            "audience": post.get("audience"),
            "category": post.get("category"),
            "length": post.get("length"),
            "image": (images[0].get("path") if images else None),
            "image_disclosure": DISCLOSURE,
            "sources": post.get("sources") or [],
            "ai_flags": post.get("ai_flags") or [],
            "topic_score": post.get("topic_score"),
            "created_at": post.get("created_at"),
            "approved_at": post.get("approved_at"),
            "exported_at": utcnow_iso(),
        }

    def _render_md(self, posts: list[dict]) -> str:
        lines = [
            "# Nội dung Facebook – AI Viral News Studio",
            f"_Xuất lúc {utcnow_iso()}_",
            "",
            f"> {DISCLOSURE}",
            "> Nội dung chưa được đăng. Hãy kiểm chứng và đăng thủ công.",
            "",
        ]
        for index, post in enumerate(posts, start=1):
            images = post.get("images") or []
            research = ((post.get("topic") or {}).get("research") or {}).get("data") or {}
            confidence = (
                f"{research.get('confidence')}% ({research.get('confidence_label')})"
                if research else "chưa có"
            )
            lines += [
                f"## {index}. {post.get('headline') or '(chưa có tiêu đề)'}",
                "",
                f"- Chủ đề: {post.get('topic_title') or '—'}",
                f"- Trạng thái: {post.get('status')}",
                f"- Điểm xu hướng: {post.get('topic_score') or '—'}",
                f"- Độ tin cậy nghiên cứu: {confidence}",
                f"- Phong cách: {post.get('style')} · {post.get('length')}",
                f"- Ảnh: {images[0].get('path') if images else 'chưa có'}",
                "",
                "### Bài chính",
                "",
                (post.get("main_post") or "").strip(),
                "",
                "### Bản ngắn",
                "",
                (post.get("short_post") or "").strip() or "—",
                "",
                "### Bản dài",
                "",
                (post.get("long_post") or "").strip() or "—",
                "",
                "### Tiêu đề thay thế",
                "",
            ]
            alt = post.get("alt_headlines") or []
            lines += [f"- {item}" for item in alt] or ["- —"]
            lines += [
                "",
                f"**Hashtags:** {' '.join(post.get('hashtags') or [])}",
                "",
                f"**Câu hỏi kết bài:** {post.get('closing_question') or '—'}",
                "",
                "### Nguồn tham khảo",
                "",
            ]
            sources = post.get("sources") or []
            lines += [f"- [{s.get('domain')}]({s.get('url')}) – {s.get('title')}" for s in sources] or ["- —"]
            flags = post.get("ai_flags") or []
            if flags:
                lines += ["", "### Cảnh báo cần kiểm tra", ""]
                lines += [f"- {f.get('detail') or f.get('type')}" for f in flags]
            lines += ["", "---", ""]
        return "\n".join(lines)

    def _render_txt(self, posts: list[dict]) -> str:
        blocks = []
        for index, post in enumerate(posts, start=1):
            images = post.get("images") or []
            sources = "\n".join(
                f"  - {s.get('domain')}: {s.get('url')}" for s in (post.get("sources") or [])
            )
            blocks.append(
                f"{index}. {post.get('headline')}\n"
                f"TRẠNG THÁI: {post.get('status')}\n"
                f"ẢNH: {images[0].get('path') if images else 'chưa có'}\n\n"
                f"{post.get('main_post')}\n\n"
                f"HASHTAGS: {' '.join(post.get('hashtags') or [])}\n"
                f"HỎI KẾT: {post.get('closing_question') or ''}\n"
                f"NGUỒN:\n{sources or '  -'}\n"
                f"{DISCLOSURE}\n"
                + "=" * 70
            )
        return f"AI Viral News Studio – xuất {utcnow_iso()}\n\n" + "\n\n".join(blocks)

    def _render_csv(self, posts: list[dict]) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "id", "headline", "main_post", "short_post", "long_post", "hashtags",
            "hook", "closing_question", "image", "status", "topic", "topic_score",
            "research_confidence", "sources", "style", "audience", "created_at",
        ])
        for post in posts:
            images = post.get("images") or []
            research = ((post.get("topic") or {}).get("research") or {}).get("data") or {}
            writer.writerow([
                post.get("id"),
                post.get("headline"),
                post.get("main_post"),
                post.get("short_post"),
                post.get("long_post"),
                " ".join(post.get("hashtags") or []),
                post.get("hook"),
                post.get("closing_question"),
                images[0].get("path") if images else "",
                post.get("status"),
                post.get("topic_title"),
                post.get("topic_score"),
                research.get("confidence"),
                " | ".join(s.get("url") or "" for s in (post.get("sources") or [])),
                post.get("style"),
                post.get("audience"),
                post.get("created_at"),
            ])
        return buffer.getvalue()


exporter = Exporter()
