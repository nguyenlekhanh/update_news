"""Persistent background job queue with cancellable workers and optional scheduler."""

from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime, timedelta

from .config import settings
from .database import Database, db as default_db
from .errors import CancelledError
from .pipeline import Pipeline, pipeline as default_pipeline
from .utils import parse_datetime, truncate, utcnow, utcnow_iso

JOB_KINDS = ("discover", "research", "write", "image", "full", "batch")


class JobQueue:
    def __init__(self, database: Database | None = None, pipeline: Pipeline | None = None,
                 workers: int | None = None):
        self.db = database or default_db
        self.pipeline = pipeline or default_pipeline
        self.workers = max(1, int(workers or settings.get("pipeline.workers", 2)))
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._claim_lock = threading.Lock()
        self._scheduler_thread: threading.Thread | None = None
        self._last_auto_discover: datetime | None = None
        self._started = False

    # ------------------------------------------------------------------ start
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        recovered = self.db.recover_jobs()
        if recovered:
            self.db.add_log(
                f"Đánh dấu {recovered} công việc bị gián đoạn là thất bại sau khi khởi động lại",
                "warning", "jobs",
            )
        for index in range(self.workers):
            thread = threading.Thread(
                target=self._worker_loop, name=f"studio-worker-{index + 1}", daemon=True
            )
            thread.start()
            self._threads.append(thread)
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop, name="studio-scheduler", daemon=True
        )
        self._scheduler_thread.start()
        self.db.add_log(
            f"Hàng đợi khởi động với {self.workers} worker", "info", "jobs"
        )

    def stop(self) -> None:
        self._stop.set()

    # --------------------------------------------------------------- enqueue
    def enqueue(self, kind: str, payload: dict | None = None, topic_id: int | None = None,
                post_id: int | None = None, priority: int = 5) -> int:
        if kind not in JOB_KINDS:
            raise ValueError(f"Loại công việc không hợp lệ: {kind}")
        job_id = self.db.create_job(kind, payload or {}, topic_id=topic_id,
                                    post_id=post_id, priority=priority)
        self.db.add_log(f"Đã xếp hàng công việc #{job_id} ({kind})", "info", "jobs")
        return job_id

    def cancel(self, job_id: int) -> dict:
        job = self.db.get_job(job_id)
        if not job:
            return {"ok": False, "error": "Không tìm thấy công việc"}
        if job["status"] == "queued":
            self.db.update_job(job_id, {"status": "cancelled", "finished_at": utcnow_iso(),
                                        "message": "Đã hủy trước khi chạy"})
            return {"ok": True, "status": "cancelled"}
        if job["status"] == "running":
            self.db.update_job(job_id, {"cancel_requested": 1, "message": "Đang yêu cầu hủy…"})
            return {"ok": True, "status": "cancelling"}
        return {"ok": False, "error": f"Công việc đang ở trạng thái {job['status']}"}

    # ---------------------------------------------------------------- workers
    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            job = None
            with self._claim_lock:
                job = self.db.next_queued_job()
                if job:
                    self.db.update_job(
                        job["id"],
                        {
                            "status": "running",
                            "started_at": utcnow_iso(),
                            "stage": "starting",
                            "progress": 1,
                            "message": "Bắt đầu…",
                            "attempts": int(job.get("attempts") or 0) + 1,
                        },
                    )
            if not job:
                self._stop.wait(0.8)
                continue
            self._execute(job)
        self._started = False

    def _execute(self, job: dict) -> None:
        job_id = job["id"]
        last = {"pct": -1, "at": 0.0}

        def progress(pct: int, message: str = "") -> None:
            pct = max(0, min(100, int(pct)))
            now = time.time()
            if pct == last["pct"] and now - last["at"] < 1.0:
                return
            last.update(pct=pct, at=now)
            self.db.update_job(
                job_id,
                {
                    "progress": pct,
                    "message": truncate(message or "", 300),
                    "stage": truncate(message or job.get("kind") or "", 80),
                },
            )

        cancel_state = {"checked": 0.0, "value": False}

        def should_cancel() -> bool:
            now = time.time()
            if now - cancel_state["checked"] > 0.6:
                cancel_state["value"] = self.db.is_cancel_requested(job_id)
                cancel_state["checked"] = now
            return bool(cancel_state["value"])

        started = time.time()
        try:
            result = self.pipeline.run_job(job, progress, should_cancel)
            elapsed = round(time.time() - started, 1)
            self.db.update_job(
                job_id,
                {
                    "status": "done",
                    "progress": 100,
                    "stage": "completed",
                    "message": f"Hoàn tất trong {elapsed}s",
                    "result": result,
                    "finished_at": utcnow_iso(),
                },
            )
            self.db.add_log(
                f"Công việc #{job_id} ({job['kind']}) hoàn tất trong {elapsed}s",
                "info", "jobs",
            )
        except CancelledError as exc:
            self.db.update_job(
                job_id,
                {
                    "status": "cancelled",
                    "message": str(exc),
                    "finished_at": utcnow_iso(),
                },
            )
            self.db.add_log(f"Đã hủy công việc #{job_id}", "warning", "jobs")
        except Exception as exc:  # noqa: BLE001 - keep the worker alive
            detail = traceback.format_exc(limit=6)
            self.db.update_job(
                job_id,
                {
                    "status": "failed",
                    "error": truncate(str(exc), 1500),
                    "message": truncate(f"Lỗi: {exc}", 300),
                    "finished_at": utcnow_iso(),
                },
            )
            self.db.add_log(
                f"Công việc #{job_id} ({job['kind']}) thất bại: {exc}",
                "error", "jobs", {"traceback": detail},
            )

    # -------------------------------------------------------------- scheduler
    def _scheduler_loop(self) -> None:
        while not self._stop.wait(30):
            try:
                self._maybe_auto_discover()
            except Exception:  # noqa: BLE001 - scheduler must not die
                self.db.add_log("Lập lịch khám phá tự động gặp lỗi", "error", "jobs")

    def _maybe_auto_discover(self) -> None:
        if not settings.get("trends.auto_discover", False):
            return
        interval = max(15, int(settings.get("trends.discover_interval_minutes", 90)))
        now = utcnow()
        if self._last_auto_discover is None:
            recent = self.db.query_one(
                "SELECT created_at FROM jobs WHERE kind = 'discover' ORDER BY id DESC LIMIT 1"
            )
            self._last_auto_discover = parse_datetime((recent or {}).get("created_at")) or (
                now - timedelta(minutes=interval)
            )
        if now - self._last_auto_discover < timedelta(minutes=interval):
            return
        active = self.db.query_one(
            "SELECT id FROM jobs WHERE kind='discover' AND status IN ('queued','running') LIMIT 1"
        )
        if active:
            return
        self._last_auto_discover = now
        self.enqueue("discover", {"source": "scheduler"}, priority=7)
        self.db.add_log(
            f"Tự động khám phá xu hướng theo lịch {interval} phút", "info", "jobs"
        )

    # ----------------------------------------------------------------- status
    def status(self) -> dict:
        running = self.db.list_jobs(status="running", limit=10)
        queued = self.db.list_jobs(status="queued", limit=10)
        return {
            "workers": self.workers,
            "started": self._started,
            "running": running,
            "queued": queued,
            "auto_discover": settings.get("trends.auto_discover", False),
        }


job_queue = JobQueue()
