"""SQLite persistence layer for topics, research, posts, images, jobs and logs."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterable

from .config import DB_PATH, SOURCES_SEED_PATH, settings
from .utils import json_dumps, json_loads_safe, utcnow_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT UNIQUE,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'rss',
    language TEXT DEFAULT 'vi',
    category TEXT DEFAULT 'general',
    weight REAL DEFAULT 1.0,
    enabled INTEGER DEFAULT 1,
    last_fetch_at TEXT,
    last_status TEXT,
    last_error TEXT,
    items_seen INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    title_norm TEXT,
    summary TEXT,
    category TEXT DEFAULT 'general',
    language TEXT,
    score REAL DEFAULT 0,
    score_breakdown TEXT,
    metrics TEXT,
    rationale TEXT,
    status TEXT DEFAULT 'new',
    source_count INTEGER DEFAULT 0,
    primary_url TEXT,
    dedupe_key TEXT UNIQUE,
    first_seen_at TEXT,
    last_seen_at TEXT,
    created_at TEXT,
    updated_at TEXT,
    meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_topics_status ON topics(status);
CREATE INDEX IF NOT EXISTS idx_topics_score ON topics(score DESC);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    title TEXT,
    url TEXT,
    canonical_url TEXT,
    domain TEXT,
    published_at TEXT,
    excerpt TEXT,
    content TEXT,
    content_chars INTEGER DEFAULT 0,
    fetched_at TEXT,
    kind TEXT,
    language TEXT,
    reliability REAL,
    meta TEXT,
    UNIQUE(topic_id, canonical_url)
);
CREATE INDEX IF NOT EXISTS idx_articles_topic ON articles(topic_id);

CREATE TABLE IF NOT EXISTS research_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    status TEXT DEFAULT 'complete',
    confidence REAL DEFAULT 0,
    confidence_label TEXT,
    data TEXT,
    model TEXT,
    source_count INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_topic ON research_reports(topic_id);

CREATE TABLE IF NOT EXISTS fact_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    research_id INTEGER REFERENCES research_reports(id) ON DELETE CASCADE,
    claim TEXT,
    verdict TEXT,
    confidence REAL,
    notes TEXT,
    source_urls TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL,
    research_id INTEGER REFERENCES research_reports(id) ON DELETE SET NULL,
    headline TEXT,
    alt_headlines TEXT,
    main_post TEXT,
    short_post TEXT,
    long_post TEXT,
    hashtags TEXT,
    hook TEXT,
    closing_question TEXT,
    image_prompt TEXT,
    image_negative_prompt TEXT,
    image_concept TEXT,
    style TEXT,
    tone TEXT,
    audience TEXT,
    category TEXT,
    language TEXT DEFAULT 'vi',
    length TEXT,
    status TEXT DEFAULT 'draft',
    sources TEXT,
    ai_flags TEXT,
    notes TEXT,
    revision INTEGER DEFAULT 1,
    created_at TEXT,
    updated_at TEXT,
    approved_at TEXT,
    rejected_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
CREATE INDEX IF NOT EXISTS idx_posts_topic ON posts(topic_id);

CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    path TEXT,
    prompt TEXT,
    negative_prompt TEXT,
    seed INTEGER,
    width INTEGER,
    height INTEGER,
    steps INTEGER,
    cfg REAL,
    workflow TEXT,
    status TEXT DEFAULT 'done',
    selected INTEGER DEFAULT 0,
    error TEXT,
    created_at TEXT,
    meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_images_post ON images(post_id);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    status TEXT DEFAULT 'queued',
    stage TEXT,
    progress INTEGER DEFAULT 0,
    message TEXT,
    error TEXT,
    payload TEXT,
    result TEXT,
    topic_id INTEGER,
    post_id INTEGER,
    priority INTEGER DEFAULT 5,
    attempts INTEGER DEFAULT 0,
    cancel_requested INTEGER DEFAULT 0,
    created_at TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT,
    scope TEXT,
    message TEXT,
    detail TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_created ON logs(id DESC);
"""

JSON_FIELDS = {
    "topics": {"score_breakdown", "metrics", "meta"},
    "articles": {"meta"},
    "research_reports": {"data"},
    "fact_checks": {"source_urls"},
    "posts": {"alt_headlines", "hashtags", "sources", "ai_flags"},
    "images": {"meta"},
    "jobs": {"payload", "result"},
}


def _now() -> str:
    return utcnow_iso()


class Database:
    def __init__(self, path=DB_PATH):
        self.path = str(path)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self.init_schema()
        self.seed_sources()

    # ------------------------------------------------------------------ core
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=15000")
            self._local.conn = conn
        return conn

    @contextmanager
    def tx(self):
        with self._write_lock:
            conn = self._conn()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def init_schema(self) -> None:
        with self.tx() as conn:
            conn.executescript(SCHEMA)

    def query(self, sql: str, params: Iterable = ()) -> list[dict]:
        cur = self._conn().execute(sql, tuple(params))
        return [self._decode_row(cur, row) for row in cur.fetchall()]

    def query_one(self, sql: str, params: Iterable = ()) -> dict | None:
        cur = self._conn().execute(sql, tuple(params))
        row = cur.fetchone()
        return self._decode_row(cur, row) if row else None

    def execute(self, sql: str, params: Iterable = ()) -> int:
        with self.tx() as conn:
            cur = conn.execute(sql, tuple(params))
            return cur.lastrowid or cur.rowcount

    def _decode_row(self, cur: sqlite3.Cursor, row: sqlite3.Row) -> dict:
        data = dict(row)
        for keys in JSON_FIELDS.values():
            for key in keys & data.keys():
                if isinstance(data[key], str):
                    data[key] = json_loads_safe(data[key], None)
        return data

    # --------------------------------------------------------------- sources
    def seed_sources(self) -> None:
        if not SOURCES_SEED_PATH.exists():
            return
        try:
            seed = json.loads(SOURCES_SEED_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        existing = {
            row["source_key"]: row
            for row in self.query("SELECT * FROM sources")
        }
        rows: list[tuple] = []
        healed: list[tuple] = []
        for feed in list(seed.get("feeds", [])) + list(seed.get("google_news_topics", [])):
            key = feed.get("id")
            if not key:
                continue
            stored = existing.get(key)
            if stored:
                # Self-heal seeded feeds whose URL has died, without touching
                # feeds the user has customised while they still work.
                seeded_url = feed.get("url") or ""
                if seeded_url and stored.get("last_status") in ("error", None) \
                        and stored.get("url") != seeded_url:
                    healed.append((seeded_url, feed.get("name") or key,
                                   feed.get("category", stored.get("category")), key))
                continue
            rows.append(
                (
                    key, feed.get("name") or key, feed.get("url") or "",
                    feed.get("kind", "rss"), feed.get("language", "vi"),
                    feed.get("category", "general"), float(feed.get("weight", 1.0)),
                    1 if feed.get("enabled", True) else 0, _now(),
                )
            )
        if rows:
            with self.tx() as conn:
                conn.executemany(
                    """INSERT OR IGNORE INTO sources
                       (source_key, name, url, kind, language, category, weight, enabled, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    rows,
                )
        if healed:
            with self.tx() as conn:
                conn.executemany(
                    "UPDATE sources SET url = ?, name = ?, category = ? WHERE source_key = ?",
                    healed,
                )

    def list_sources(self, enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM sources"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY kind, category, name"
        return self.query(sql)

    def add_source(self, data: dict) -> int:
        row = (
            data.get("source_key") or f"custom-{_now()}",
            data.get("name") or "Custom source",
            data.get("url") or "",
            data.get("kind") or "rss",
            data.get("language") or "vi",
            data.get("category") or "general",
            float(data.get("weight") or 1.0),
            1 if data.get("enabled", True) else 0,
            _now(),
        )
        return self.execute(
            """INSERT OR IGNORE INTO sources
               (source_key, name, url, kind, language, category, weight, enabled, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            row,
        )

    def update_source(self, source_id: int, patch: dict) -> None:
        allowed = {"name", "url", "language", "category", "weight", "enabled", "kind"}
        sets = [f"{key} = ?" for key in patch if key in allowed]
        if not sets:
            return
        params = [patch[key] for key in patch if key in allowed]
        self.execute(f"UPDATE sources SET {', '.join(sets)} WHERE id = ?", params + [source_id])

    def delete_source(self, source_id: int) -> None:
        self.execute("DELETE FROM sources WHERE id = ?", (source_id,))

    def mark_source_fetch(self, source_id: int, status: str, error: str | None, seen: int) -> None:
        self.execute(
            """UPDATE sources SET last_fetch_at = ?, last_status = ?, last_error = ?,
               items_seen = items_seen + ? WHERE id = ?""",
            (_now(), status, error, seen, source_id),
        )

    # ---------------------------------------------------------------- topics
    def find_topic_by_dedupe(self, key: str) -> dict | None:
        return self.query_one("SELECT * FROM topics WHERE dedupe_key = ?", (key,))

    def recent_topics(self, limit: int = 300) -> list[dict]:
        return self.query(
            "SELECT * FROM topics ORDER BY last_seen_at DESC LIMIT ?", (limit,)
        )

    def create_topic(self, data: dict) -> int:
        payload = {
            "title": data.get("title", ""),
            "title_norm": data.get("title_norm", ""),
            "summary": data.get("summary", ""),
            "category": data.get("category", "general"),
            "language": data.get("language", "vi"),
            "score": float(data.get("score", 0) or 0),
            "score_breakdown": json_dumps(data.get("score_breakdown") or []),
            "metrics": json_dumps(data.get("metrics") or []),
            "rationale": data.get("rationale", ""),
            "status": data.get("status", "new"),
            "source_count": int(data.get("source_count", 0) or 0),
            "primary_url": data.get("primary_url", ""),
            "dedupe_key": data.get("dedupe_key"),
            "first_seen_at": data.get("first_seen_at") or _now(),
            "last_seen_at": data.get("last_seen_at") or _now(),
            "created_at": _now(),
            "updated_at": _now(),
            "meta": json_dumps(data.get("meta") or {}),
        }
        cols = ", ".join(payload)
        marks = ", ".join("?" for _ in payload)
        return self.execute(
            f"INSERT INTO topics ({cols}) VALUES ({marks})", list(payload.values())
        )

    def update_topic(self, topic_id: int, patch: dict) -> None:
        allowed = {
            "title", "summary", "category", "language", "score", "score_breakdown",
            "metrics", "rationale", "status", "source_count", "primary_url",
            "last_seen_at", "meta",
        }
        sets, params = [], []
        for key, value in patch.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = ?")
            if key in ("score_breakdown", "metrics", "meta") and not isinstance(value, str):
                value = json_dumps(value)
            params.append(value)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(topic_id)
        self.execute(f"UPDATE topics SET {', '.join(sets)} WHERE id = ?", params)

    def get_topic(self, topic_id: int) -> dict | None:
        topic = self.query_one("SELECT * FROM topics WHERE id = ?", (topic_id,))
        if topic:
            topic["articles"] = self.list_articles(topic_id)
            topic["research"] = self.get_latest_research(topic_id)
            topic["posts"] = self.list_posts(topic_id=topic_id)
        return topic

    def list_topics(
        self,
        status: str | None = None,
        category: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
        min_score: float | None = None,
    ) -> list[dict]:
        where, params = [], []
        if status:
            where.append("status = ?")
            params.append(status)
        if category:
            where.append("category = ?")
            params.append(category)
        if search:
            where.append("(title LIKE ? OR summary LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        if min_score is not None:
            where.append("score >= ?")
            params.append(min_score)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self.query(
            f"""SELECT * FROM topics {clause}
                ORDER BY score DESC, last_seen_at DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )
        for row in rows:
            row["article_count"] = self.query_one(
                "SELECT COUNT(*) AS c FROM articles WHERE topic_id = ?", (row["id"],)
            )["c"]
            row["post_count"] = self.query_one(
                "SELECT COUNT(*) AS c FROM posts WHERE topic_id = ?", (row["id"],)
            )["c"]
        return rows

    def delete_topic(self, topic_id: int) -> None:
        self.execute("DELETE FROM topics WHERE id = ?", (topic_id,))

    # -------------------------------------------------------------- articles
    def add_article(self, topic_id: int, data: dict) -> int | None:
        try:
            return self.execute(
                """INSERT OR IGNORE INTO articles
                   (topic_id, title, url, canonical_url, domain, published_at, excerpt,
                    content, content_chars, fetched_at, kind, language, reliability, meta)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    topic_id, data.get("title", ""), data.get("url", ""),
                    data.get("canonical_url", ""), data.get("domain", ""),
                    data.get("published_at"), data.get("excerpt", ""),
                    data.get("content", ""), int(data.get("content_chars", 0) or 0),
                    data.get("fetched_at") or _now(), data.get("kind", "rss"),
                    data.get("language", ""), float(data.get("reliability", 0.5) or 0.5),
                    json_dumps(data.get("meta") or {}),
                ),
            )
        except sqlite3.IntegrityError:
            return None

    def list_articles(self, topic_id: int) -> list[dict]:
        return self.query(
            "SELECT * FROM articles WHERE topic_id = ? ORDER BY published_at DESC",
            (topic_id,),
        )

    def update_article(self, article_id: int, patch: dict) -> None:
        allowed = {"content", "content_chars", "title", "published_at", "excerpt", "reliability", "meta"}
        sets, params = [], []
        for key, value in patch.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = ?")
            if key == "meta" and not isinstance(value, str):
                value = json_dumps(value)
            params.append(value)
        if sets:
            params.append(article_id)
            self.execute(f"UPDATE articles SET {', '.join(sets)} WHERE id = ?", params)

    def article_urls(self, topic_id: int) -> set[str]:
        rows = self.query(
            "SELECT canonical_url FROM articles WHERE topic_id = ?", (topic_id,)
        )
        return {row["canonical_url"] for row in rows if row.get("canonical_url")}

    # -------------------------------------------------------------- research
    def create_research(self, data: dict) -> int:
        return self.execute(
            """INSERT INTO research_reports
               (topic_id, status, confidence, confidence_label, data, model, source_count, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                data["topic_id"], data.get("status", "complete"),
                float(data.get("confidence", 0) or 0), data.get("confidence_label", ""),
                json_dumps(data.get("data") or {}), data.get("model", ""),
                int(data.get("source_count", 0) or 0), _now(), _now(),
            ),
        )

    def get_latest_research(self, topic_id: int) -> dict | None:
        report = self.query_one(
            "SELECT * FROM research_reports WHERE topic_id = ? ORDER BY id DESC LIMIT 1",
            (topic_id,),
        )
        if report:
            report["fact_checks"] = self.query(
                "SELECT * FROM fact_checks WHERE research_id = ? ORDER BY id", (report["id"],)
            )
        return report

    def add_fact_check(self, research_id: int, data: dict) -> int:
        return self.execute(
            """INSERT INTO fact_checks
               (research_id, claim, verdict, confidence, notes, source_urls, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                research_id, data.get("claim", ""), data.get("verdict", "unverified"),
                float(data.get("confidence", 0) or 0), data.get("notes", ""),
                json_dumps(data.get("source_urls") or []), _now(),
            ),
        )

    # ----------------------------------------------------------------- posts
    def create_post(self, data: dict) -> int:
        post_id = self.execute(
            """INSERT INTO posts
               (topic_id, research_id, headline, alt_headlines, main_post, short_post, long_post,
                hashtags, hook, closing_question, image_prompt, image_negative_prompt, image_concept,
                style, tone, audience, category, language, length, status, sources, ai_flags,
                notes, revision, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data.get("topic_id"), data.get("research_id"), data.get("headline", ""),
                json_dumps(data.get("alt_headlines") or []),
                data.get("main_post", ""), data.get("short_post", ""),
                data.get("long_post", ""), json_dumps(data.get("hashtags") or []),
                data.get("hook", ""), data.get("closing_question", ""),
                data.get("image_prompt", ""), data.get("image_negative_prompt", ""),
                data.get("image_concept", ""), data.get("style", ""),
                data.get("tone", ""), data.get("audience", ""),
                data.get("category", ""), data.get("language", "vi"),
                data.get("length", ""), data.get("status", "draft"),
                json_dumps(data.get("sources") or []),
                json_dumps(data.get("ai_flags") or []),
                data.get("notes", ""), int(data.get("revision", 1) or 1),
                _now(), _now(),
            ),
        )
        return post_id

    def update_post(self, post_id: int, patch: dict) -> None:
        json_fields = {"alt_headlines", "hashtags", "sources", "ai_flags"}
        allowed = {
            "headline", "alt_headlines", "main_post", "short_post", "long_post",
            "hashtags", "hook", "closing_question", "image_prompt",
            "image_negative_prompt", "image_concept", "style", "tone", "audience",
            "category", "language", "length", "status", "sources", "ai_flags",
            "notes", "revision", "approved_at", "rejected_at",
        }
        sets, params = [], []
        for key, value in patch.items():
            if key not in allowed:
                continue
            if key in json_fields and not isinstance(value, str):
                value = json_dumps(value)
            sets.append(f"{key} = ?")
            params.append(value)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(post_id)
        self.execute(f"UPDATE posts SET {', '.join(sets)} WHERE id = ?", params)

    def get_post(self, post_id: int) -> dict | None:
        post = self.query_one(
            """SELECT p.*, t.title AS topic_title, t.score AS topic_score, t.primary_url AS topic_url,
                      t.category AS topic_category, t.source_count AS topic_source_count
               FROM posts p LEFT JOIN topics t ON t.id = p.topic_id WHERE p.id = ?""",
            (post_id,),
        )
        if post:
            post["images"] = self.list_images(post_id)
            topic = self.get_topic(post["topic_id"]) if post.get("topic_id") else None
            post["topic"] = (
                {
                    "id": topic["id"], "title": topic["title"], "summary": topic.get("summary"),
                    "score": topic.get("score"), "category": topic.get("category"),
                    "articles": topic.get("articles", []),
                    "research": topic.get("research"),
                }
                if topic
                else None
            )
        return post

    def list_posts(
        self,
        status: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
        topic_id: int | None = None,
    ) -> list[dict]:
        where, params = [], []
        if status:
            where.append("p.status = ?")
            params.append(status)
        if search:
            where.append("(p.headline LIKE ? OR p.main_post LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        if topic_id:
            where.append("p.topic_id = ?")
            params.append(topic_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self.query(
            f"""SELECT p.*, t.title AS topic_title, t.score AS topic_score,
                       (SELECT path FROM images i WHERE i.post_id = p.id AND i.status='done'
                        ORDER BY i.selected DESC, i.id DESC LIMIT 1) AS thumb
                FROM posts p LEFT JOIN topics t ON t.id = p.topic_id
                {clause} ORDER BY p.id DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )
        return rows

    def delete_post(self, post_id: int) -> None:
        self.execute("DELETE FROM posts WHERE id = ?", (post_id,))

    # ---------------------------------------------------------------- images
    def add_image(self, data: dict) -> int:
        return self.execute(
            """INSERT INTO images
               (post_id, path, prompt, negative_prompt, seed, width, height, steps, cfg,
                workflow, status, selected, error, created_at, meta)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["post_id"], data.get("path", ""), data.get("prompt", ""),
                data.get("negative_prompt", ""), data.get("seed"),
                data.get("width"), data.get("height"), data.get("steps"),
                data.get("cfg"), data.get("workflow", ""),
                data.get("status", "done"), 1 if data.get("selected") else 0,
                data.get("error", ""), _now(), json_dumps(data.get("meta") or {}),
            ),
        )

    def list_images(self, post_id: int) -> list[dict]:
        return self.query(
            "SELECT * FROM images WHERE post_id = ? ORDER BY selected DESC, id DESC",
            (post_id,),
        )

    def select_image(self, post_id: int, image_id: int) -> None:
        with self.tx() as conn:
            conn.execute("UPDATE images SET selected = 0 WHERE post_id = ?", (post_id,))
            conn.execute(
                "UPDATE images SET selected = 1 WHERE id = ? AND post_id = ?",
                (image_id, post_id),
            )

    def delete_image(self, image_id: int) -> None:
        self.execute("DELETE FROM images WHERE id = ?", (image_id,))

    # ------------------------------------------------------------------ jobs
    def create_job(self, kind: str, payload: dict, topic_id=None, post_id=None, priority=5) -> int:
        return self.execute(
            """INSERT INTO jobs
               (kind, status, stage, progress, message, payload, topic_id, post_id, priority, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                kind, "queued", "queued", 0, "", json_dumps(payload or {}),
                topic_id, post_id, priority, _now(),
            ),
        )

    def update_job(self, job_id: int, patch: dict) -> None:
        json_fields = {"payload", "result"}
        allowed = {
            "status", "stage", "progress", "message", "error", "result", "started_at",
            "finished_at", "attempts", "cancel_requested",
        }
        sets, params = [], []
        for key, value in patch.items():
            if key not in allowed:
                continue
            if key in json_fields and not isinstance(value, str):
                value = json_dumps(value)
            sets.append(f"{key} = ?")
            params.append(value)
        if sets:
            params.append(job_id)
            self.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params)

    def get_job(self, job_id: int) -> dict | None:
        return self.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))

    def list_jobs(self, status: str | None = None, limit: int = 50) -> list[dict]:
        if status:
            return self.query(
                "SELECT * FROM jobs WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit)
            )
        return self.query("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))

    def next_queued_job(self) -> dict | None:
        return self.query_one(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY priority ASC, id ASC LIMIT 1"
        )

    def is_cancel_requested(self, job_id: int) -> bool:
        row = self.query_one("SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,))
        return bool(row and row["cancel_requested"])

    def recover_jobs(self) -> int:
        count = self.query_one("SELECT COUNT(*) AS c FROM jobs WHERE status = 'running'")["c"]
        if count:
            self.execute(
                """UPDATE jobs SET status='failed', error=?, finished_at=?
                   WHERE status='running'""",
                ("Ứng dụng đã dừng khi công việc đang chạy", _now()),
            )
        return count

    # ------------------------------------------------------------------ logs
    def add_log(self, message: str, level: str = "info", scope: str = "app", detail=None) -> None:
        try:
            self.execute(
                "INSERT INTO logs (level, scope, message, detail, created_at) VALUES (?,?,?,?,?)",
                (level, scope, message, json_dumps(detail) if detail is not None else "", _now()),
            )
        except sqlite3.Error:
            pass

    def list_logs(self, limit: int = 100) -> list[dict]:
        return self.query("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,))

    # ----------------------------------------------------------------- stats
    def stats(self) -> dict:
        def count(table: str, where: str = "", params=()):
            row = self.query_one(f"SELECT COUNT(*) AS c FROM {table} {where}", params)
            return row["c"] if row else 0

        return {
            "topics": count("topics"),
            "topics_new": count("topics", "WHERE status = 'new'"),
            "research": count("research_reports"),
            "posts": count("posts"),
            "posts_draft": count("posts", "WHERE status = 'draft'"),
            "posts_approved": count("posts", "WHERE status = 'approved'"),
            "posts_rejected": count("posts", "WHERE status = 'rejected'"),
            "posts_exported": count("posts", "WHERE status = 'exported'"),
            "images": count("images", "WHERE status = 'done'"),
            "jobs_running": count("jobs", "WHERE status = 'running'"),
            "jobs_queued": count("jobs", "WHERE status = 'queued'"),
            "sources": count("sources", "WHERE enabled = 1"),
        }


db = Database()
