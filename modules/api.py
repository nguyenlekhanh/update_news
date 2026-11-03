"""FastAPI application: dashboard API, SSE streaming test, static dashboard."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from . import config
from .ai_writer import STYLE_GUIDES, TONE_GUIDES, LENGTH_GUIDES, AUDIENCE_GUIDES
from .comfyui_client import ComfyUIError, comfyui
from .config import IMAGES_DIR, OUTPUT_DIR, PROJECT_ROOT, WEB_DIR, settings, secret_status
from .database import db
from .exporter import exporter
from .job_queue import job_queue
from .news_researcher import CONFIDENCE_LABELS
from .ollama_client import OllamaError, ollama
from .resource_manager import resource_manager
from .search_provider import search_service
from .facebook_public_source import facebook_source
from .trend_finder import CATEGORY_OPTIONS
from . import (
    content_calendar, facebook_metrics, image_studio, learning, prediction,
    quality, strategy, viral,
)
from .analytics import service as analytics
from .reports import service as reports
from .utils import json_dumps, utcnow_iso

POST_STATUSES = {"draft", "approved", "rejected", "exported"}


class APIError(HTTPException):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(status_code=status_code, detail=message)


def _validate_settings_patch(patch: dict) -> dict:
    defaults = config.DEFAULT_SETTINGS
    if not isinstance(patch, dict) or not patch:
        raise APIError("Dữ liệu cài đặt phải là object không rỗng")
    unknown = set(patch) - set(defaults)
    if unknown:
        raise APIError(f"Không hỗ trợ nhóm cài đặt: {', '.join(sorted(unknown))}")
    cleaned: dict[str, Any] = {}
    for section, values in patch.items():
        if not isinstance(values, dict):
            raise APIError(f"Nhóm cài đặt '{section}' phải là object")
        allowed = defaults[section]
        bad = set(values) - set(allowed)
        if bad:
            raise APIError(f"Khóa không hợp lệ trong '{section}': {', '.join(sorted(bad))}")
        cleaned[section] = {}
        for key, value in values.items():
            default = allowed[key]
            if isinstance(default, bool):
                cleaned[section][key] = bool(value)
            elif isinstance(default, (int, float)) and not isinstance(default, bool):
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    raise APIError(f"'{section}.{key}' phải là số") from None
                cleaned[section][key] = int(number) if isinstance(default, int) else number
            elif isinstance(default, list):
                if not isinstance(value, list):
                    raise APIError(f"'{section}.{key}' phải là danh sách")
                cleaned[section][key] = [str(v).strip() for v in value if str(v).strip()]
            elif isinstance(default, dict):
                if not isinstance(value, dict):
                    raise APIError(f"'{section}.{key}' phải là object")
                cleaned[section][key] = {
                    str(k): str(v) for k, v in value.items() if v not in (None, "")
                }
            else:
                cleaned[section][key] = value
    return cleaned


def _image_url(path: str | None) -> str | None:
    if not path:
        return None
    normalized = path.replace("\\", "/")
    if normalized.startswith("output/"):
        return "/media/" + normalized[len("output/"):]
    return "/media/" + normalized.lstrip("/")


def _decorate_post(post: dict) -> dict:
    for image in post.get("images") or []:
        image["url"] = _image_url(image.get("path"))
        image["selected"] = bool(image.get("selected"))
    if post.get("thumb"):
        post["thumb_url"] = _image_url(post["thumb"])
    try:
        post["quality"] = quality.evaluate(post)
    except Exception as exc:  # noqa: BLE001 - quality must never break the editor
        db.add_log(f"Không tính được điểm chất lượng cho bài #{post.get('id')}: {exc}",
                   "warning", "quality")
        post["quality"] = None
    topic = post.get("topic") or {}
    if topic.get("id"):
        try:
            history = db.list_history(topic["id"])
            intelligence = viral.assess(topic, topic.get("articles") or [], history)
            post["strategy"] = strategy.resolve(
                topic, topic.get("research"), intelligence,
                db.get_strategy(topic["id"]),
            )
            post["prediction"] = prediction.analyze(
                topic, history, intelligence.get("freshness_hours"),
                intelligence.get("independent_domains"),
            )
            post["image_recommendation"] = image_studio.recommend(
                topic.get("category"), post["strategy"].get("effective_key")
            )
        except Exception as exc:  # noqa: BLE001 - editor must stay usable
            db.add_log(f"Không tính được chiến lược cho bài #{post.get('id')}: {exc}",
                       "warning", "strategy")
    try:
        post["metrics"] = db.list_metrics(post_id=post["id"])
    except Exception:  # noqa: BLE001
        post["metrics"] = []
    return post


def _topic_row(topic: dict, history: list[dict] | None = None,
               research: dict | None = None) -> dict:
    topic["score_breakdown"] = topic.get("score_breakdown") or topic.get("metrics") or []
    topic.pop("metrics", None)
    history = history or []
    intelligence = viral.assess(topic, topic.get("articles") or [], history)
    topic["intelligence"] = intelligence
    topic["phase"] = intelligence["phase"]
    topic["viral"] = intelligence["viral"]
    topic["prediction"] = prediction.analyze(
        topic, history, intelligence.get("freshness_hours"),
        intelligence.get("independent_domains"),
    )
    resolved = strategy.resolve(
        topic,
        research if research is not None else topic.get("research"),
        intelligence,
        db.get_strategy(topic["id"]),
    )
    topic["strategy"] = resolved
    return topic


def _learning() -> learning.EditorialLearning:
    """Learning service bound to the API's database (never a stray global)."""
    return learning.EditorialLearning(db)


def _strategy_options(topic_id: int, options: dict | None) -> dict:
    """Resolve the editorial strategy into writer options."""
    options = dict(options or {})
    if not settings.get("strategy.enabled", True):
        return options
    if not settings.get("strategy.apply_to_writing", True):
        return options
    topic = db.get_topic(topic_id)
    if not topic:
        return options
    intelligence = viral.assess(
        topic, topic.get("articles") or [], db.list_history(topic_id)
    )
    chosen = strategy.resolve(
        topic, topic.get("research"), intelligence, db.get_strategy(topic_id)
    )
    merged = strategy.apply_options(chosen, options)
    merged.setdefault("strategy_key", chosen.get("effective_key"))
    return merged


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.add_log("Ứng dụng khởi động", "info", "app")
    job_queue.start()
    try:
        yield
    finally:
        job_queue.stop()


app = FastAPI(
    title="AI Viral News & Facebook Content Studio",
    version="1.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
app.mount("/media", StaticFiles(directory=str(OUTPUT_DIR)), name="media")


@app.exception_handler(APIError)
def _api_error_handler(request: Request, exc: APIError):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.exception_handler(ComfyUIError)
def _comfy_error_handler(request: Request, exc: ComfyUIError):
    return JSONResponse(status_code=502, content={"error": str(exc)})


@app.exception_handler(OllamaError)
def _ollama_error_handler(request: Request, exc: OllamaError):
    return JSONResponse(status_code=502, content={"error": str(exc)})


# --------------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    path = WEB_DIR / "index.html"
    if not path.exists():
        return HTMLResponse("<h1>Dashboard chưa được cài đặt</h1>", status_code=500)
    return HTMLResponse(path.read_text(encoding="utf-8"))


# -------------------------------------------------------------------- system
@app.get("/api/health")
def health() -> dict:
    ollama_health = ollama.health()
    comfy_health = comfyui.health()
    return {
        "app": {"ok": True, "version": app.version, "time": utcnow_iso()},
        "ollama": ollama_health,
        "comfyui": comfy_health,
        "gpu": resource_manager.vram(),
        "secrets": secret_status(),
        "paths": {
            "project": str(PROJECT_ROOT),
            "database": str(config.DB_PATH),
            "images": str(IMAGES_DIR),
            "exports": str(config.EXPORT_DIR),
        },
    }


@app.get("/api/system")
def system_info() -> dict:
    return {
        "version": app.version,
        "time": utcnow_iso(),
        "paths": {
            "project": str(PROJECT_ROOT),
            "database": str(config.DB_PATH),
            "images": str(IMAGES_DIR),
        },
        "gpu": resource_manager.status(),
        "queue": job_queue.status(),
        "search": search_service.status(),
        "facebook": facebook_source.status(),
        "secret_status": secret_status(),
        "options": {
            "styles": list(STYLE_GUIDES),
            "tones": list(TONE_GUIDES),
            "lengths": list(LENGTH_GUIDES),
            "audiences": list(AUDIENCE_GUIDES),
            "categories": CATEGORY_OPTIONS,
            "confidence_labels": CONFIDENCE_LABELS,
            "post_statuses": sorted(POST_STATUSES),
            "strategies": strategy.options(),
            "image_styles": image_studio.options()["styles"],
            "aspects": image_studio.options()["aspects"],
            "report_kinds": reports.catalog(),
        },
        "learning": _learning().summary()["privacy"],
    }


@app.get("/api/stats")
def stats() -> dict:
    return {
        "counts": db.stats(),
        "gpu": resource_manager.vram(),
        "jobs": job_queue.status(),
        "recent_jobs": db.list_jobs(limit=6),
        "storage": {
            "images": len(list(IMAGES_DIR.rglob("*.png"))) + len(list(IMAGES_DIR.rglob("*.jpg"))),
            "exports": len(list(config.EXPORT_DIR.glob("*"))),
        },
    }


@app.get("/api/logs")
def logs(limit: int = Query(80, ge=1, le=500)) -> dict:
    return {"items": db.list_logs(limit=limit)}


# ------------------------------------------------------------------ settings
@app.get("/api/settings")
def get_settings() -> dict:
    return {
        "settings": settings.all(),
        "secrets": secret_status(),
        "options": {
            "styles": list(STYLE_GUIDES),
            "tones": list(TONE_GUIDES),
            "lengths": list(LENGTH_GUIDES),
            "audiences": list(AUDIENCE_GUIDES),
            "categories": CATEGORY_OPTIONS,
        },
    }


@app.put("/api/settings")
def update_settings(patch: dict = Body(...)) -> dict:
    cleaned = _validate_settings_patch(patch)
    updated = settings.update(cleaned)
    db.add_log("Đã cập nhật cài đặt", "info", "settings", cleaned)
    return {"settings": updated, "secrets": secret_status()}


@app.post("/api/settings/reset")
def reset_settings() -> dict:
    return {"settings": settings.reset()}


# -------------------------------------------------------------------- ollama
@app.get("/api/ollama/status")
def ollama_status() -> dict:
    return ollama.health()


@app.post("/api/ollama/unload")
def ollama_unload() -> dict:
    return ollama.unload()


@app.post("/api/ollama/stream")
def ollama_stream(payload: dict = Body(...)) -> StreamingResponse:
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise APIError("Thiếu prompt")
    system = str(payload.get("system") or "") or None

    def generate():
        try:
            for chunk in ollama.stream(prompt, system=system):
                yield f"data: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except OllamaError as exc:
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------------------- comfyui
@app.get("/api/comfyui/status")
def comfyui_status() -> dict:
    return {"health": comfyui.health(), "workflows": comfyui.list_workflows()}


@app.get("/api/comfyui/workflows")
def comfyui_workflows() -> dict:
    return {"items": comfyui.list_workflows()}


@app.post("/api/comfyui/validate")
def comfyui_validate(payload: dict = Body(default={})) -> dict:
    path = payload.get("path") or settings.get("comfyui.workflow_path")
    overrides = payload.get("node_map") or settings.get("comfyui.node_map") or {}
    return comfyui.validate_workflow(path, overrides)


@app.post("/api/comfyui/free")
def comfyui_free() -> dict:
    return comfyui.free_memory(unload_models=True)


# ------------------------------------------------------------------- sources
@app.get("/api/sources")
def list_sources() -> dict:
    return {
        "items": db.list_sources(),
        "facebook": facebook_source.status(),
        "search": search_service.status(),
    }


@app.post("/api/sources")
def add_source(payload: dict = Body(...)) -> dict:
    url = str(payload.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise APIError("URL nguồn không hợp lệ")
    name = str(payload.get("name") or url).strip()
    source_id = db.add_source({**payload, "name": name, "url": url})
    if not source_id:
        raise APIError("Nguồn đã tồn tại")
    return {"ok": True, "id": source_id}


@app.patch("/api/sources/{source_id}")
def update_source(source_id: int, payload: dict = Body(...)) -> dict:
    if not db.query_one("SELECT id FROM sources WHERE id = ?", (source_id,)):
        raise APIError("Không tìm thấy nguồn", 404)
    db.update_source(source_id, payload)
    return {"ok": True}


@app.delete("/api/sources/{source_id}")
def delete_source(source_id: int) -> dict:
    db.delete_source(source_id)
    return {"ok": True}


# -------------------------------------------------------------------- trends
@app.get("/api/trends")
def list_trends(
    status: str | None = None,
    category: str | None = None,
    search: str | None = None,
    min_score: float | None = None,
    limit: int = Query(60, ge=1, le=300),
    offset: int = Query(0, ge=0),
) -> dict:
    rows = db.list_topics(
        status=status, category=category, search=search,
        min_score=min_score, limit=limit, offset=offset,
    )
    history = db.recent_history_map()
    return {
        "items": [_topic_row(row, history.get(row["id"], [])) for row in rows],
        "count": len(rows),
    }


@app.get("/api/trends/{topic_id}")
def get_trend(topic_id: int) -> dict:
    topic = db.get_topic(topic_id)
    if not topic:
        raise APIError("Không tìm thấy chủ đề", 404)
    row = _topic_row(topic, db.list_history(topic_id), topic.get("research"))
    row["versions"] = db.list_versions(topic_id)
    return row


@app.get("/api/trends/{topic_id}/prediction")
def trend_prediction(topic_id: int) -> dict:
    topic = db.get_topic(topic_id)
    if not topic:
        raise APIError("Không tìm thấy chủ đề", 404)
    history = db.list_history(topic_id)
    intelligence = viral.assess(topic, topic.get("articles") or [], history)
    result = prediction.analyze(
        topic, history, intelligence.get("freshness_hours"),
        intelligence.get("independent_domains"),
    )
    return {
        "topic_id": topic_id,
        "prediction": result,
        "compare": prediction.history_compare(history),
    }


@app.get("/api/trends/{topic_id}/history")
def trend_history(topic_id: int, limit: int = Query(60, ge=1, le=200)) -> dict:
    if not db.get_topic(topic_id):
        raise APIError("Không tìm thấy chủ đề", 404)
    return {"topic_id": topic_id, "items": db.list_history(topic_id, limit)}


@app.get("/api/trends/{topic_id}/strategy")
def get_topic_strategy(topic_id: int) -> dict:
    topic = db.get_topic(topic_id)
    if not topic:
        raise APIError("Không tìm thấy chủ đề", 404)
    intelligence = viral.assess(
        topic, topic.get("articles") or [], db.list_history(topic_id)
    )
    stored = db.get_strategy(topic_id) or {}
    resolved = strategy.resolve(topic, topic.get("research"), intelligence, stored)
    return {
        "topic_id": topic_id,
        "recommended": strategy.recommend(topic, topic.get("research"), intelligence),
        "effective": resolved,
        "stored": stored,
        "options": strategy.options(),
        "allow_override": bool(settings.get("strategy.allow_override", True)),
    }


@app.put("/api/trends/{topic_id}/strategy")
def set_topic_strategy(topic_id: int, payload: dict = Body(...)) -> dict:
    if not db.get_topic(topic_id):
        raise APIError("Không tìm thấy chủ đề", 404)
    if not settings.get("strategy.allow_override", True):
        raise APIError("Tính năng ghi đè chiến lược đang tắt trong cài đặt")
    override = str(payload.get("strategy") or "").strip() or None
    if override and override not in strategy.STRATEGIES:
        raise APIError(f"Chiến lược không hợp lệ: {override}")
    note = str(payload.get("note") or "")[:240]
    stored = db.get_strategy(topic_id) or {}
    recommended = stored.get("recommended") or (
        get_topic_strategy(topic_id)["recommended"]["key"]
    )
    if override == recommended:
        override = None
    record = db.set_strategy(topic_id, recommended, override, note)
    db.add_log(
        f"Chiến lược chủ đề #{topic_id}: " + (override or f"theo gợi ý ({recommended})"),
        "info", "strategy",
    )
    return get_topic_strategy(topic_id) | {"stored": record}


@app.delete("/api/trends/{topic_id}/strategy")
def reset_topic_strategy(topic_id: int) -> dict:
    if not db.get_topic(topic_id):
        raise APIError("Không tìm thấy chủ đề", 404)
    db.clear_strategy_override(topic_id)
    return get_topic_strategy(topic_id)


@app.delete("/api/trends/{topic_id}")
def delete_trend(topic_id: int) -> dict:
    topic = db.get_topic(topic_id)
    if not topic:
        raise APIError("Không tìm thấy chủ đề", 404)
    try:
        _learning().record(
            learning.KIND_TOPIC_REJECT,
            topic_id=topic_id, category=topic.get("category") or "",
            signal="topic_deleted",
            detail={"title": topic.get("title"), "score": topic.get("score")},
        )
    except Exception as exc:  # noqa: BLE001 - learning must not block deletion
        db.add_log(f"Không ghi phản hồi xóa chủ đề #{topic_id}: {exc}", "warning", "learning")
    db.delete_topic(topic_id)
    return {"ok": True}


@app.post("/api/trends/discover")
def discover(payload: dict = Body(default={})) -> dict:
    if payload.get("use_llm") is not None:
        settings.update({"trends": {"llm_editorial": bool(payload["use_llm"])}})
    job_id = job_queue.enqueue(
        "discover",
        {
            "max_topics": payload.get("max_topics"),
            "lookback_hours": payload.get("lookback_hours"),
            "use_llm": payload.get("use_llm"),
        },
        priority=3,
    )
    return {"ok": True, "job_id": job_id}


# ------------------------------------------------------------------ pipeline
@app.post("/api/topics/{topic_id}/research")
def topic_research(topic_id: int, payload: dict = Body(default={})) -> dict:
    if not db.get_topic(topic_id):
        raise APIError("Không tìm thấy chủ đề", 404)
    job_id = job_queue.enqueue(
        "research", {"topic_id": topic_id, "force": bool(payload.get("force"))}, topic_id=topic_id
    )
    return {"ok": True, "job_id": job_id}


@app.post("/api/topics/{topic_id}/write")
def topic_write(topic_id: int, payload: dict = Body(default={})) -> dict:
    if not db.get_topic(topic_id):
        raise APIError("Không tìm thấy chủ đề", 404)
    options = _strategy_options(topic_id, payload.get("options") or {})
    job_id = job_queue.enqueue(
        "write",
        {"topic_id": topic_id, "options": options,
         "research_first": bool(payload.get("research_first", True))},
        topic_id=topic_id,
    )
    return {"ok": True, "job_id": job_id, "strategy": options.get("strategy_key")}


@app.post("/api/topics/{topic_id}/pipeline")
def topic_pipeline(topic_id: int, payload: dict = Body(default={})) -> dict:
    if not db.get_topic(topic_id):
        raise APIError("Không tìm thấy chủ đề", 404)
    job_id = job_queue.enqueue(
        "full",
        {
            "topic_id": topic_id,
            "options": _strategy_options(topic_id, payload.get("options") or {}),
            "force_research": bool(payload.get("force_research")),
            "make_image": payload.get("make_image", settings.get("pipeline.auto_image", True)),
            "variations": int(payload.get("variations") or 1),
            "style": payload.get("style"),
            "aspect": payload.get("aspect"),
        },
        topic_id=topic_id,
    )
    return {"ok": True, "job_id": job_id}


@app.post("/api/batch")
def batch(payload: dict = Body(default={})) -> dict:
    job_id = job_queue.enqueue(
        "batch",
        {
            "topic_ids": payload.get("topic_ids") or [],
            "top_n": payload.get("top_n") or 3,
            "min_score": payload.get("min_score"),
            "make_image": payload.get("make_image", settings.get("pipeline.auto_image", True)),
            "variations": int(payload.get("variations") or 1),
            "options": payload.get("options") or {},
            "style": payload.get("style"),
            "aspect": payload.get("aspect"),
        },
        priority=6,
    )
    return {"ok": True, "job_id": job_id}


# ------------------------------------------------------------------ versions
@app.post("/api/topics/{topic_id}/versions")
def topic_versions(topic_id: int, payload: dict = Body(default={})) -> dict:
    if not db.get_topic(topic_id):
        raise APIError("Không tìm thấy chủ đề", 404)
    job_id = job_queue.enqueue(
        "versions",
        {
            "topic_id": topic_id,
            "options": _strategy_options(topic_id, payload.get("options") or {}),
            "research_first": bool(payload.get("research_first", True)),
        },
        topic_id=topic_id,
    )
    return {"ok": True, "job_id": job_id}


@app.get("/api/topics/{topic_id}/versions")
def list_topic_versions(topic_id: int) -> dict:
    if not db.get_topic(topic_id):
        raise APIError("Không tìm thấy chủ đề", 404)
    return {"topic_id": topic_id, "items": db.list_versions(topic_id)}


@app.post("/api/versions/{version_id}/apply")
def apply_version(version_id: int) -> dict:
    version = db.get_version(version_id)
    if not version:
        raise APIError("Không tìm thấy phiên bản", 404)
    topic = db.get_topic(version["topic_id"])
    if not topic:
        raise APIError("Chủ đề của phiên bản không còn tồn tại", 404)
    research = topic.get("research") or {}
    sources = ((research.get("data") or {}).get("sources")) or []
    post_id = db.create_post(
        {
            "topic_id": topic["id"],
            "research_id": version.get("research_id") or research.get("id"),
            "headline": version.get("headline"),
            "alt_headlines": [],
            "main_post": version.get("main_post"),
            "short_post": version.get("short_post"),
            "long_post": "",
            "hashtags": version.get("hashtags") or [],
            "hook": version.get("hook"),
            "closing_question": version.get("closing_question"),
            "image_prompt": version.get("image_prompt"),
            "image_negative_prompt": settings.get("comfyui.negative_prompt", ""),
            "image_concept": version.get("image_concept"),
            "style": version.get("style"),
            "tone": settings.get("writer.tone"),
            "audience": settings.get("writer.audience"),
            "category": topic.get("category"),
            "language": "vi",
            "length": settings.get("writer.length"),
            "status": "draft",
            "sources": sources,
            "ai_flags": version.get("flags") or [],
            "revision": len(topic.get("posts") or []) + 1,
        }
    )
    db.select_version(topic["id"], version_id)
    db.update_topic(topic["id"], {"status": "drafted"})
    db.add_log(
        f"Dùng phiên bản {version.get('variant')} (bài #{post_id}) cho chủ đề #{topic['id']}",
        "info", "review",
    )
    try:
        rejected = [v for v in db.list_versions(topic["id"]) if v["id"] != version_id]
        _learning().record_version_choice(topic, version, rejected)
    except Exception as exc:  # noqa: BLE001
        db.add_log(f"Không ghi phản hồi chọn phiên bản: {exc}", "warning", "learning")
    return _decorate_post(db.get_post(post_id))


# --------------------------------------------------------------------- posts
@app.get("/api/posts")
def list_posts(
    status: str | None = None,
    search: str | None = None,
    limit: int = Query(60, ge=1, le=300),
    offset: int = Query(0, ge=0),
) -> dict:
    rows = db.list_posts(status=status, search=search, limit=limit, offset=offset)
    for row in rows:
        row["thumb_url"] = _image_url(row.get("thumb"))
    return {"items": rows, "count": len(rows)}


@app.get("/api/posts/{post_id}")
def get_post(post_id: int) -> dict:
    post = db.get_post(post_id)
    if not post:
        raise APIError("Không tìm thấy bài viết", 404)
    return _decorate_post(post)


@app.patch("/api/posts/{post_id}")
def update_post(post_id: int, payload: dict = Body(...)) -> dict:
    existing = db.get_post(post_id)
    if not existing:
        raise APIError("Không tìm thấy bài viết", 404)
    patch = dict(payload)
    if "status" in patch:
        status = str(patch["status"]).lower()
        if status not in POST_STATUSES:
            raise APIError(f"Trạng thái không hợp lệ: {status}")
        if status == "approved":
            patch["approved_at"] = utcnow_iso()
        elif status == "rejected":
            patch["rejected_at"] = utcnow_iso()
    db.update_post(post_id, patch)
    try:
        before = {key: existing.get(key) for key in ("headline", "main_post")}
        afterwards = {
            "headline": patch.get("headline", existing.get("headline")),
            "main_post": patch.get("main_post", existing.get("main_post")),
        }
        _learning().record_edit(existing, before, afterwards)
    except Exception as exc:  # noqa: BLE001
        db.add_log(f"Không ghi nhận chỉnh sửa bài #{post_id}: {exc}", "warning", "learning")
    return _decorate_post(db.get_post(post_id))


@app.post("/api/posts/{post_id}/approve")
def approve_post(post_id: int) -> dict:
    post = db.get_post(post_id)
    if not post:
        raise APIError("Không tìm thấy bài viết", 404)
    db.update_post(post_id, {"status": "approved", "approved_at": utcnow_iso()})
    db.add_log(f"Duyệt bài viết #{post_id}", "info", "review")
    try:
        _learning().record_decision(learning.KIND_APPROVE, post, signal="manual_approve")
    except Exception as exc:  # noqa: BLE001
        db.add_log(f"Không ghi phản hồi duyệt bài #{post_id}: {exc}", "warning", "learning")
    return _decorate_post(db.get_post(post_id))


@app.post("/api/posts/{post_id}/reject")
def reject_post(post_id: int, payload: dict = Body(default={})) -> dict:
    post = db.get_post(post_id)
    if not post:
        raise APIError("Không tìm thấy bài viết", 404)
    reason = str(payload.get("reason") or "")[:500]
    db.update_post(
        post_id,
        {"status": "rejected", "rejected_at": utcnow_iso(), "notes": reason},
    )
    db.add_log(f"Từ chối bài viết #{post_id}", "info", "review")
    try:
        _learning().record(
            learning.KIND_REJECT,
            topic_id=post.get("topic_id"), post_id=post_id,
            category=post.get("topic_category") or post.get("category") or "",
            style=post.get("style") or "", signal="manual_reject",
            detail={"reason": reason, "headline": post.get("headline")},
        )
    except Exception as exc:  # noqa: BLE001
        db.add_log(f"Không ghi phản hồi từ chối bài #{post_id}: {exc}", "warning", "learning")
    return _decorate_post(db.get_post(post_id))


@app.post("/api/posts/{post_id}/regenerate")
def regenerate_post(post_id: int, payload: dict = Body(default={})) -> dict:
    post = db.get_post(post_id)
    if not post:
        raise APIError("Không tìm thấy bài viết", 404)
    if not post.get("topic_id"):
        raise APIError("Bài viết không còn liên kết chủ đề để tạo lại")
    job_id = job_queue.enqueue(
        "write",
        {
            "topic_id": post["topic_id"],
            "options": payload.get("options") or {},
            "research_first": False,
        },
        topic_id=post["topic_id"],
    )
    return {"ok": True, "job_id": job_id}


@app.post("/api/posts/{post_id}/image")
def generate_image(post_id: int, payload: dict = Body(default={})) -> dict:
    post = db.get_post(post_id)
    if not post:
        raise APIError("Không tìm thấy bài viết", 404)
    requested = int(payload.get("variations") or 1)
    variations = max(1, min(3, requested))
    style = payload.get("style")
    aspect = payload.get("aspect")
    if style and style != "auto" and style not in image_studio.STYLES:
        raise APIError(f"Phong cách ảnh không hợp lệ: {style}")
    if aspect and aspect not in image_studio.ASPECTS:
        raise APIError(f"Tỉ lệ ảnh không hợp lệ: {aspect}")
    job_id = job_queue.enqueue(
        "image",
        {
            "post_id": post_id,
            "variations": variations,
            "seed": payload.get("seed"),
            "prompt": payload.get("prompt"),
            "style": style,
            "aspect": aspect,
        },
        post_id=post_id,
    )
    return {"ok": True, "job_id": job_id, "variations": variations,
            "capped": requested > variations,
            "style": style or settings.get("images.style", "auto"),
            "aspect": aspect or settings.get("images.aspect", "1:1")}


@app.get("/api/posts/{post_id}/quality")
def post_quality(post_id: int) -> dict:
    post = db.get_post(post_id)
    if not post:
        raise APIError("Không tìm thấy bài viết", 404)
    return quality.evaluate(post)


@app.post("/api/posts/{post_id}/assess-image")
def assess_image(post_id: int, payload: dict = Body(default={})) -> dict:
    post = db.get_post(post_id)
    if not post:
        raise APIError("Không tìm thấy bài viết", 404)
    if not post.get("images"):
        raise APIError("Bài viết chưa có ảnh để đánh giá")
    job_id = job_queue.enqueue(
        "assess_image",
        {"post_id": post_id, "image_id": payload.get("image_id")},
        post_id=post_id,
        priority=4,
    )
    return {"ok": True, "job_id": job_id}


@app.post("/api/posts/{post_id}/select-image/{image_id}")
def select_image(post_id: int, image_id: int) -> dict:
    images = db.list_images(post_id)
    if not any(img["id"] == image_id for img in images):
        raise APIError("Ảnh không thuộc bài viết này", 404)
    db.select_image(post_id, image_id)
    try:
        post = db.get_post(post_id)
        image = next((img for img in images if img["id"] == image_id), None)
        _learning().record_image_choice(post or {}, image or {})
    except Exception as exc:  # noqa: BLE001
        db.add_log(f"Không ghi phản hồi chọn ảnh #{image_id}: {exc}", "warning", "learning")
    return _decorate_post(db.get_post(post_id))


@app.delete("/api/posts/{post_id}")
def delete_post(post_id: int) -> dict:
    db.delete_post(post_id)
    return {"ok": True}


# --------------------------------------------------------------- image studio
@app.get("/api/image-studio/options")
def image_studio_options() -> dict:
    return image_studio.options()


@app.post("/api/posts/{post_id}/image-check")
def post_image_check(post_id: int, payload: dict = Body(default={})) -> dict:
    post = db.get_post(post_id)
    if not post:
        raise APIError("Không tìm thấy bài viết", 404)
    images = post.get("images") or []
    if not images:
        raise APIError("Bài viết chưa có ảnh để kiểm tra")
    image_id = payload.get("image_id")
    target = next((img for img in images if image_id and img["id"] == int(image_id)), None)
    if target is None:
        target = next((img for img in images if img.get("selected")), images[0])
    meta = target.get("meta") or {}
    known = image_studio.hash_map(images, skip_id=target["id"])
    result = image_studio.check_image(
        target.get("path") or "",
        prompt=target.get("prompt") or post.get("image_prompt") or "",
        aspect=meta.get("aspect") or settings.get("images.aspect"),
        style=meta.get("image_style"),
        known_hashes=known,
    )
    updated = dict(meta)
    updated["checks"] = result
    updated["sha256"] = result.get("sha256")
    updated["text_risk"] = result["text_artifacts"]["risk"]
    db.update_image(target["id"], {"meta": updated})
    db.add_log(
        f"Kiểm tra ảnh #{target['id']} của bài #{post_id}: {result['score']:.0f}/100 "
        f"({result['verdict']})", "info", "image_studio",
    )
    return {"post_id": post_id, "image_id": target["id"], **result}


# ------------------------------------------------------------- editorial learning
@app.get("/api/learning")
def learning_summary() -> dict:
    return _learning().summary()


@app.delete("/api/learning")
def learning_reset(kind: str | None = None, category: str | None = None) -> dict:
    try:
        return _learning().reset(kind=kind, category=category)
    except ValueError as exc:
        raise APIError(str(exc)) from None


@app.post("/api/posts/{post_id}/rating")
def rate_post(post_id: int, payload: dict = Body(...)) -> dict:
    post = db.get_post(post_id)
    if not post:
        raise APIError("Không tìm thấy bài viết", 404)
    try:
        rating = int(payload.get("rating"))
    except (TypeError, ValueError):
        raise APIError("Điểm đánh giá phải là số từ 1 đến 5") from None
    if not 1 <= rating <= 5:
        raise APIError("Điểm đánh giá phải từ 1 đến 5")
    note = str(payload.get("note") or "")[:300]
    _learning().record(
        learning.KIND_RATING,
        topic_id=post.get("topic_id"), post_id=post_id,
        category=post.get("topic_category") or post.get("category") or "",
        style=post.get("style") or "", signal="user_rating", rating=rating,
        detail={"note": note},
    )
    db.add_log(f"Đánh giá bài #{post_id}: {rating}/5", "info", "learning")
    return {"ok": True, "post_id": post_id, "rating": rating}


# ---------------------------------------------------------------- calendar
@app.get("/api/calendar")
def get_calendar(days: int = Query(7, ge=1, le=28)) -> dict:
    return content_calendar.service.plan(days)


@app.get("/api/calendar/options")
def calendar_options() -> dict:
    return content_calendar.service.options()


@app.post("/api/calendar/entries")
def create_calendar_entry(payload: dict = Body(...)) -> dict:
    try:
        post_id = int(payload.get("post_id"))
    except (TypeError, ValueError):
        raise APIError("Thiếu post_id hợp lệ") from None
    try:
        entry = content_calendar.service.schedule(
            post_id, str(payload.get("scheduled_at") or ""), str(payload.get("note") or "")
        )
    except ValueError as exc:
        raise APIError(str(exc)) from None
    return {"ok": True, **entry}


@app.patch("/api/calendar/entries/{entry_id}")
def update_calendar_entry(entry_id: int, payload: dict = Body(...)) -> dict:
    scheduled_at = payload.get("scheduled_at")
    if not scheduled_at:
        raise APIError("Thiếu thời điểm mới")
    try:
        return content_calendar.service.reschedule(
            entry_id, str(scheduled_at), payload.get("note")
        )
    except ValueError as exc:
        raise APIError(str(exc)) from None


@app.delete("/api/calendar/entries/{entry_id}")
def delete_calendar_entry(entry_id: int) -> dict:
    return content_calendar.service.remove(entry_id)


# ------------------------------------------------------------ facebook metrics
@app.get("/api/facebook/metrics/status")
def facebook_metrics_status() -> dict:
    return facebook_metrics.service.status()


@app.get("/api/facebook/metrics/analysis")
def facebook_metrics_analysis(days: int = Query(90, ge=7, le=365)) -> dict:
    return facebook_metrics.service.analysis(days)


@app.post("/api/facebook/metrics/import")
def facebook_metrics_import(payload: dict = Body(...)) -> dict:
    text = payload.get("csv")
    rows = payload.get("rows")
    if not text and not rows:
        raise APIError("Cần dữ liệu CSV hoặc danh sách dòng")
    source = str(payload.get("source") or "manual")
    if source != "manual":
        raise APIError("Chỉ hỗ trợ nhập thủ công cho endpoint này")
    try:
        if text:
            return facebook_metrics.service.import_csv(
                str(text), payload.get("published_at")
            )
        return facebook_metrics.service.import_rows(
            rows, "manual", payload.get("published_at")
        )
    except ValueError as exc:
        raise APIError(str(exc)) from None


@app.get("/api/posts/{post_id}/metrics")
def post_metrics(post_id: int) -> dict:
    if not db.get_post(post_id):
        raise APIError("Không tìm thấy bài viết", 404)
    return facebook_metrics.service.post_metrics(post_id)


@app.post("/api/posts/{post_id}/metrics/fetch")
def fetch_post_metrics(post_id: int, payload: dict = Body(...)) -> dict:
    if not db.get_post(post_id):
        raise APIError("Không tìm thấy bài viết", 404)
    try:
        return facebook_metrics.service.fetch_graph(
            post_id, str(payload.get("external_id") or ""), payload.get("published_at")
        )
    except ValueError as exc:
        raise APIError(str(exc)) from None


# -------------------------------------------------------------------- reports
@app.get("/api/reports")
def list_reports() -> dict:
    return {"items": reports.catalog()}


@app.get("/api/reports/{kind}", response_model=None)
def get_report(kind: str, days: int = Query(7, ge=1, le=90),
               format: str = Query("json")):
    try:
        report = reports.build(kind, days)
    except ValueError as exc:
        raise APIError(str(exc)) from None
    if format.lower() == "md":
        return PlainTextResponse(
            report["markdown"], media_type="text/markdown; charset=utf-8"
        )
    return report


# ------------------------------------------------------------------ analytics
@app.get("/api/analytics")
def get_analytics(days: int = Query(14, ge=2, le=90)) -> dict:
    data = analytics.overview(days)
    data["trend_shift"] = prediction.trend_shift(
        db.query("SELECT * FROM topic_history ORDER BY id"),
        db.query("SELECT id, title FROM topics"),
        window_hours=min(48, days * 12),
    )
    return data


# ---------------------------------------------------------------------- jobs
@app.get("/api/jobs")
def list_jobs(status: str | None = None, limit: int = Query(40, ge=1, le=200)) -> dict:
    return {"items": db.list_jobs(status=status, limit=limit), "status": job_queue.status()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int) -> dict:
    job = db.get_job(job_id)
    if not job:
        raise APIError("Không tìm thấy công việc", 404)
    return job


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int) -> dict:
    result = job_queue.cancel(job_id)
    if not result.get("ok"):
        raise APIError(result.get("error") or "Không thể hủy", 400)
    return result


# -------------------------------------------------------------------- export
@app.get("/api/export")
def export_content(
    format: str = Query("md"),
    status: str | None = Query("approved"),
    ids: str | None = Query(None, description="Comma separated post ids"),
    mark_exported: bool = Query(False),
):
    post_ids = [int(value) for value in ids.split(",") if value.strip().isdigit()] if ids else None
    try:
        result = exporter.export(format, status, post_ids, mark_exported)
    except ValueError as exc:
        raise APIError(str(exc)) from None
    path = Path(result["path"])
    if not path.exists():
        raise APIError("File xuất không tồn tại", 500)
    return FileResponse(
        path, media_type=result["media_type"], filename=result["filename"]
    )


# ------------------------------------------------------------------- research
@app.get("/api/research/{research_id}")
def get_research(research_id: int) -> dict:
    row = db.query_one("SELECT * FROM research_reports WHERE id = ?", (research_id,))
    if not row:
        raise APIError("Không tìm thấy báo cáo", 404)
    row["fact_checks"] = db.query(
        "SELECT * FROM fact_checks WHERE research_id = ? ORDER BY id", (research_id,)
    )
    return row
