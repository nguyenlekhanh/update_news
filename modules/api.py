"""FastAPI application: dashboard API, SSE streaming test, static dashboard."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
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
    return post


def _topic_row(topic: dict) -> dict:
    topic["score_breakdown"] = topic.get("score_breakdown") or topic.get("metrics") or []
    topic.pop("metrics", None)
    return topic


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
        },
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
    return {"items": [_topic_row(row) for row in rows], "count": len(rows)}


@app.get("/api/trends/{topic_id}")
def get_trend(topic_id: int) -> dict:
    topic = db.get_topic(topic_id)
    if not topic:
        raise APIError("Không tìm thấy chủ đề", 404)
    return _topic_row(topic)


@app.delete("/api/trends/{topic_id}")
def delete_trend(topic_id: int) -> dict:
    if not db.get_topic(topic_id):
        raise APIError("Không tìm thấy chủ đề", 404)
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
    job_id = job_queue.enqueue(
        "write",
        {"topic_id": topic_id, "options": payload.get("options") or {},
         "research_first": bool(payload.get("research_first", True))},
        topic_id=topic_id,
    )
    return {"ok": True, "job_id": job_id}


@app.post("/api/topics/{topic_id}/pipeline")
def topic_pipeline(topic_id: int, payload: dict = Body(default={})) -> dict:
    if not db.get_topic(topic_id):
        raise APIError("Không tìm thấy chủ đề", 404)
    job_id = job_queue.enqueue(
        "full",
        {
            "topic_id": topic_id,
            "options": payload.get("options") or {},
            "force_research": bool(payload.get("force_research")),
            "make_image": payload.get("make_image", settings.get("pipeline.auto_image", True)),
            "variations": int(payload.get("variations") or 1),
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
        },
        priority=6,
    )
    return {"ok": True, "job_id": job_id}


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
    if not db.get_post(post_id):
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
    return _decorate_post(db.get_post(post_id))


@app.post("/api/posts/{post_id}/approve")
def approve_post(post_id: int) -> dict:
    if not db.get_post(post_id):
        raise APIError("Không tìm thấy bài viết", 404)
    db.update_post(post_id, {"status": "approved", "approved_at": utcnow_iso()})
    db.add_log(f"Duyệt bài viết #{post_id}", "info", "review")
    return _decorate_post(db.get_post(post_id))


@app.post("/api/posts/{post_id}/reject")
def reject_post(post_id: int, payload: dict = Body(default={})) -> dict:
    if not db.get_post(post_id):
        raise APIError("Không tìm thấy bài viết", 404)
    db.update_post(
        post_id,
        {
            "status": "rejected",
            "rejected_at": utcnow_iso(),
            "notes": str(payload.get("reason") or "")[:500],
        },
    )
    db.add_log(f"Từ chối bài viết #{post_id}", "info", "review")
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
    job_id = job_queue.enqueue(
        "image",
        {
            "post_id": post_id,
            "variations": int(payload.get("variations") or 1),
            "seed": payload.get("seed"),
            "prompt": payload.get("prompt"),
        },
        post_id=post_id,
    )
    return {"ok": True, "job_id": job_id}


@app.post("/api/posts/{post_id}/select-image/{image_id}")
def select_image(post_id: int, image_id: int) -> dict:
    images = db.list_images(post_id)
    if not any(img["id"] == image_id for img in images):
        raise APIError("Ảnh không thuộc bài viết này", 404)
    db.select_image(post_id, image_id)
    return _decorate_post(db.get_post(post_id))


@app.delete("/api/posts/{post_id}")
def delete_post(post_id: int) -> dict:
    db.delete_post(post_id)
    return {"ok": True}


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
