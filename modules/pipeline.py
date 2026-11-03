"""End-to-end content pipeline: research -> Vietnamese post -> ComfyUI illustration."""

from __future__ import annotations

import time
from typing import Callable

from .ai_writer import AIWriter, ai_writer
from .comfyui_client import ComfyUIClient, ComfyUIError, comfyui
from .config import IMAGES_DIR, settings
from .database import Database, db as default_db
from .errors import CancelledError, StageError
from .news_researcher import NewsResearcher, news_researcher
from .resource_manager import ResourceManager, resource_manager
from .trend_finder import TrendFinder, trend_finder
from .utils import utcnow_iso

IMAGE_SUFFIX = (
    "editorial illustration, conceptual magazine artwork, symbolic composition, "
    "clean layout, soft cinematic lighting, high detail, no text, no watermark, "
    "no logos, clearly illustrative rather than a documentary photograph"
)

IMAGE_NEGATIVE_EXTRA = (
    "photorealistic news photograph, fake documentary evidence, real identifiable person, "
    "celebrity likeness, text, letters, watermark, logo, signature, screenshot, UI"
)

PERSON_NAME_BLOCKLIST_NOTE = (
    "Ảnh được tạo bằng AI theo phong cách minh họa biên tập, không phải ảnh tư liệu."
)


class Pipeline:
    def __init__(
        self,
        database: Database | None = None,
        finder: TrendFinder | None = None,
        researcher: NewsResearcher | None = None,
        writer: AIWriter | None = None,
        comfy: ComfyUIClient | None = None,
        resources: ResourceManager | None = None,
    ):
        self.db = database or default_db
        self.finder = finder or trend_finder
        self.researcher = researcher or news_researcher
        self.writer = writer or ai_writer
        self.comfy = comfy or comfyui
        self.resources = resources or resource_manager

    # ------------------------------------------------------------------ entry
    def run_job(self, job: dict, progress: Callable[[int, str], None],
                should_cancel: Callable[[], bool]) -> dict:
        kind = job.get("kind")
        payload = job.get("payload") or {}
        if kind == "discover":
            return self.finder.discover(
                max_topics=payload.get("max_topics"),
                lookback_hours=payload.get("lookback_hours"),
                should_cancel=should_cancel,
                progress=progress,
                use_llm=payload.get("use_llm"),
            )
        if kind == "research":
            return self.research(
                int(payload.get("topic_id") or job.get("topic_id")),
                progress, should_cancel, force=bool(payload.get("force")),
            )
        if kind == "write":
            return self.write(
                int(payload.get("topic_id") or job.get("topic_id")),
                payload.get("options") or {},
                progress, should_cancel,
                research_first=bool(payload.get("research_first", True)),
            )
        if kind == "image":
            return self.generate_image(
                int(payload.get("post_id") or job.get("post_id")),
                progress, should_cancel,
                variations=int(payload.get("variations") or settings.get("pipeline.image_variations", 1)),
                seed=payload.get("seed"),
                prompt=payload.get("prompt"),
            )
        if kind == "full":
            return self.full(
                int(payload.get("topic_id") or job.get("topic_id")),
                progress, should_cancel,
                options=payload.get("options") or {},
                force_research=bool(payload.get("force_research")),
                make_image=payload.get("make_image", settings.get("pipeline.auto_image", True)),
                variations=int(payload.get("variations") or settings.get("pipeline.image_variations", 1)),
            )
        if kind == "batch":
            return self.batch(payload, progress, should_cancel)
        raise StageError(f"Loại công việc không hỗ trợ: {kind}")

    # --------------------------------------------------------------- research
    def research(self, topic_id: int, progress=None, should_cancel=None, force: bool = False) -> dict:
        def report(pct: int, message: str) -> None:
            if progress:
                progress(pct, message)

        report(2, "Chuẩn bị nghiên cứu…")
        with self.resources.gpu_phase("llm"):
            report(30, "Đang tổng hợp và kiểm chứng bằng Qwen…")
            result = self.researcher.research(
                topic_id, should_cancel=should_cancel, progress=progress, force=force
            )
        report(100, "Nghiên cứu hoàn tất")
        summary = {
            "topic_id": topic_id,
            "research_id": result.get("id"),
            "confidence": result.get("confidence"),
            "confidence_label": result.get("confidence_label"),
            "sources": len(result.get("sources") or []),
        }
        return summary

    # ------------------------------------------------------------------ write
    def write(self, topic_id: int, options: dict, progress=None, should_cancel=None,
              research_first: bool = True) -> dict:
        def report(pct: int, message: str) -> None:
            if progress:
                progress(pct, message)

        topic = self.db.get_topic(topic_id)
        if not topic:
            raise StageError(f"Không tìm thấy chủ đề #{topic_id}")
        research = topic.get("research")
        if (not research or research.get("status") != "complete") and research_first:
            report(5, "Chưa có nghiên cứu, đang nghiên cứu trước…")
            with self.resources.gpu_phase("llm"):
                research = self.researcher.research(topic_id, should_cancel=should_cancel,
                                                    progress=progress)
        report(35, "Đang viết bài Facebook bằng Qwen…")
        with self.resources.gpu_phase("llm"):
            post = self.writer.write(
                topic_id,
                research_id=(research or {}).get("id"),
                options=options,
                should_cancel=should_cancel,
                progress=lambda pct, msg: report(35 + int(pct * 0.6), msg),
            )
        report(100, "Đã tạo bài viết")
        return {
            "post_id": post["id"],
            "topic_id": topic_id,
            "headline": post.get("headline"),
            "word_count": len((post.get("main_post") or "").split()),
            "flags": len(post.get("ai_flags") or []),
        }

    # ------------------------------------------------------------------ image
    def generate_image(self, post_id: int, progress=None, should_cancel=None,
                       variations: int = 1, seed: int | None = None,
                       prompt: str | None = None) -> dict:
        def report(pct: int, message: str) -> None:
            if progress:
                progress(pct, message)

        post = self.db.get_post(post_id)
        if not post:
            raise StageError(f"Không tìm thấy bài viết #{post_id}")
        base_prompt = (prompt or post.get("image_prompt") or "").strip()
        if not base_prompt:
            base_prompt = (
                "Conceptual editorial illustration symbolising a news story, "
                "abstract shapes over a subtle world map, warm and cool contrast"
            )
        people = []
        research = (post.get("topic") or {}).get("research") or {}
        entities = (research.get("data") or {}).get("entities") or {}
        people = [str(p) for p in entities.get("people") or [] if p]
        base_prompt, blocked = self._sanitize_prompt(base_prompt, people)

        negative = (post.get("image_negative_prompt") or "").strip()
        negative = ", ".join(filter(None, [negative, IMAGE_NEGATIVE_EXTRA]))
        cfg = settings.section("comfyui")
        batch = max(1, min(4, int(variations or 1)))
        dest_dir = IMAGES_DIR / f"post_{post_id}"
        results = []
        report(3, "Đang chuẩn bị GPU cho ComfyUI…")
        with self.resources.gpu_phase("image"):
            attempts = 1 if self._workflow_supports_batch() else batch
            for index in range(attempts):
                report(8 + int(index * 10 / max(1, attempts)), "Đang tạo ảnh minh họa…")
                try:
                    result = self.comfy.generate(
                        prompt=f"{base_prompt.strip().rstrip('.')}, {IMAGE_SUFFIX}",
                        negative_prompt=negative,
                        width=int(cfg.get("width", 1024)),
                        height=int(cfg.get("height", 1024)),
                        seed=seed,
                        batch_size=batch if attempts == 1 else 1,
                        steps=int(cfg.get("steps", 28)),
                        cfg=float(cfg.get("cfg", 7.0)),
                        workflow_path=cfg.get("workflow_path"),
                        overrides=cfg.get("node_map") or {},                         filename_prefix=f"post{post_id}_{int(time.time())}",
                        dest_dir=dest_dir,
                        should_cancel=should_cancel,
                        on_progress=lambda pct, msg: report(8 + int(pct * 0.7), msg),
                    )
                except ComfyUIError:
                    raise
                for image in result["images"]:
                    image_id = self.db.add_image(
                        {
                            "post_id": post_id,
                            "path": image["path"],
                            "prompt": base_prompt,
                            "negative_prompt": negative,
                            "seed": image.get("seed"),
                            "width": image.get("width"),
                            "height": image.get("height"),
                            "steps": result["applied"].get("steps"),
                            "cfg": result["applied"].get("cfg"),
                            "workflow": str(result.get("workflow_path")),
                            "status": "done",
                            "selected": not post.get("images") and index == 0 and len(results) == 0,
                            "meta": {
                                "prompt_id": result.get("prompt_id"),
                                "applied": result.get("applied"),
                                "duration_seconds": result.get("duration_seconds"),
                                "blocked_names": blocked,
                                "disclosure": PERSON_NAME_BLOCKLIST_NOTE,
                            },
                        }
                    )
                    results.append({"image_id": image_id, "path": image["path"]})
        # Ensure something is selected
        images = self.db.list_images(post_id)
        if images and not any(img.get("selected") for img in images):
            self.db.select_image(post_id, images[0]["id"])
        self.db.update_topic(post["topic_id"], {"status": "ready"}) if post.get("topic_id") else None
        report(100, "Đã tạo ảnh minh họa")
        return {"post_id": post_id, "images": results, "blocked_names": blocked}

    def _workflow_supports_batch(self) -> bool:
        try:
            from .workflow_mapper import WorkflowMapper, load_workflow

            cfg = settings.section("comfyui")
            workflow = load_workflow(cfg.get("workflow_path"))
            mapper = WorkflowMapper(workflow, cfg.get("node_map") or {})
            latent = mapper.roles.get("latent")
            if not latent:
                return False
            return "batch_size" in (workflow[latent].get("inputs") or {})
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _sanitize_prompt(prompt: str, people: list[str]) -> tuple[str, list[str]]:
        blocked = []
        for name in people:
            if len(name) < 3:
                continue
            if name.lower() in prompt.lower():
                prompt = prompt.replace(name, "a symbolic figure")
                blocked.append(name)
        return prompt, blocked

    # ------------------------------------------------------------------- full
    def full(self, topic_id: int, progress=None, should_cancel=None, options: dict | None = None,
             force_research: bool = False, make_image: bool = True,
             variations: int = 1) -> dict:
        def report(pct: int, message: str) -> None:
            if progress:
                progress(pct, message)

        topic = self.db.get_topic(topic_id)
        if not topic:
            raise StageError(f"Không tìm thấy chủ đề #{topic_id}")
        report(2, "Bắt đầu quy trình nội dung…")
        research = topic.get("research")
        if force_research or not research or research.get("status") != "complete":
            with self.resources.gpu_phase("llm"):
                research = self.researcher.research(
                    topic_id,
                    should_cancel=should_cancel,
                    progress=lambda pct, msg: report(2 + int(pct * 0.35), msg),
                )
        else:
            report(35, "Dùng báo cáo nghiên cứu đã có…")
        if should_cancel and should_cancel():
            raise CancelledError("Đã hủy theo yêu cầu người dùng")
        with self.resources.gpu_phase("llm"):
            post = self.writer.write(
                topic_id,
                research_id=(research or {}).get("id"),
                options=options or {},
                should_cancel=should_cancel,
                progress=lambda pct, msg: report(38 + int(pct * 0.32), msg),
            )
        images = []
        if make_image:
            report(72, "Đang tạo ảnh minh họa bằng ComfyUI…")
            image_result = self.generate_image(
                post["id"],
                progress=lambda pct, msg: report(72 + int(pct * 0.26), msg),
                should_cancel=should_cancel,
                variations=variations,
            )
            images = image_result.get("images") or []
        report(100, "Hoàn tất quy trình nội dung")
        return {
            "topic_id": topic_id,
            "research_id": (research or {}).get("id"),
            "post_id": post["id"],
            "images": images,
            "headline": post.get("headline"),
            "confidence": (research or {}).get("confidence"),
        }

    # ------------------------------------------------------------------ batch
    def batch(self, payload: dict, progress=None, should_cancel=None) -> dict:
        topic_ids = [int(t) for t in (payload.get("topic_ids") or []) if str(t).isdigit()]
        if not topic_ids:
            top_n = int(payload.get("top_n") or 3)
            rows = self.db.list_topics(limit=top_n, status="new", min_score=payload.get("min_score"))
            topic_ids = [row["id"] for row in rows]
        if not topic_ids:
            raise StageError("Không có chủ đề nào phù hợp để chạy hàng loạt")
        results, failures = [], []
        total = len(topic_ids)
        for index, topic_id in enumerate(topic_ids):
            if should_cancel and should_cancel():
                raise CancelledError("Đã hủy theo yêu cầu người dùng")
            start = int(index * 100 / total)

            def sub_progress(pct: int, message: str, start=start, total=total, index=index):
                if progress:
                    overall = start + int(pct / total)
                    progress(min(99, overall), f"[{index + 1}/{total}] {message}")

            try:
                result = self.full(
                    topic_id,
                    progress=sub_progress,
                    should_cancel=should_cancel,
                    options=payload.get("options") or {},
                    make_image=payload.get("make_image", settings.get("pipeline.auto_image", True)),
                    variations=int(payload.get("variations") or 1),
                )
                results.append(result)
            except CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - continue with remaining topics
                failures.append({"topic_id": topic_id, "error": str(exc)})
                self.db.add_log(
                    f"Chủ đề #{topic_id} lỗi trong lô: {exc}", "error", "pipeline"
                )
        if progress:
            progress(100, f"Hoàn tất {len(results)}/{total} chủ đề")
        return {
            "requested": total,
            "completed": len(results),
            "failures": failures,
            "results": results,
            "finished_at": utcnow_iso(),
        }


pipeline = Pipeline()
