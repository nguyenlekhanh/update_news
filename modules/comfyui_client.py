"""Client for the existing local ComfyUI Portable API (submit, monitor, fetch images)."""

from __future__ import annotations

import json
import mimetypes
import struct
import time
import uuid
import zlib
from pathlib import Path
from typing import Callable

import httpx

from .config import IMAGES_DIR, PROJECT_ROOT, settings
from .errors import CancelledError
from .workflow_mapper import (
    SAVE_TYPES,
    WorkflowError,
    WorkflowMapper,
    load_workflow,
)
from .utils import slugify


class ComfyUIError(RuntimeError):
    pass


class ComfyUIClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self._base_url = base_url
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        return (self._base_url or settings.get("comfyui.base_url", "http://127.0.0.1:8188")).rstrip("/")

    @property
    def timeout(self) -> float:
        return float(self._timeout or settings.get("comfyui.timeout_seconds", 420))

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=5.0, read=timeout or 30.0, write=60.0, pool=10.0),
        )

    # ----------------------------------------------------------------- health
    def health(self) -> dict:
        started = time.perf_counter()
        info = {
            "base_url": self.base_url,
            "available": False,
            "version": None,
            "gpu": None,
            "ram_free_gb": None,
            "queue_running": 0,
            "queue_pending": 0,
            "latency_ms": None,
            "error": None,
        }
        try:
            with self._client(timeout=8.0) as client:
                stats = client.get("/system_stats")
                stats.raise_for_status()
                data = stats.json()
                system = data.get("system") or {}
                devices = data.get("devices") or []
                if devices:
                    device = devices[0]
                    total = device.get("vram_total") or 0
                    free = device.get("vram_free") or 0
                    info["gpu"] = {
                        "name": device.get("name"),
                        "vram_total_mb": round(total / (1024 * 1024)),
                        "vram_free_mb": round(free / (1024 * 1024)),
                    }
                info["version"] = system.get("comfyui_version")
                info["ram_free_gb"] = round((system.get("ram_free") or 0) / (1024**3), 1)
                queue = client.get("/queue").json()
                info["queue_running"] = len(queue.get("queue_running") or [])
                info["queue_pending"] = len(queue.get("queue_pending") or [])
            info["available"] = True
            info["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        except (httpx.HTTPError, ValueError) as exc:
            info["error"] = (
                f"Không kết nối được ComfyUI tại {self.base_url}. "
                f"Hãy chạy run_nvidia_gpu.bat của bản ComfyUI Portable hiện có. Chi tiết: {exc}"
            )
        return info

    def object_info(self, node_type: str) -> dict | None:
        try:
            with self._client(timeout=10.0) as client:
                resp = client.get(f"/object_info/{node_type}")
                resp.raise_for_status()
                data = resp.json()
                return data.get(node_type)
        except (httpx.HTTPError, ValueError):
            return None

    def queue_status(self) -> dict:
        try:
            with self._client(timeout=8.0) as client:
                data = client.get("/queue").json()
                return {
                    "running": len(data.get("queue_running") or []),
                    "pending": len(data.get("queue_pending") or []),
                }
        except (httpx.HTTPError, ValueError):
            return {"running": 0, "pending": 0}

    def free_memory(self, unload_models: bool = True) -> dict:
        try:
            with self._client(timeout=15.0) as client:
                resp = client.post(
                    "/free",
                    json={"unload_models": bool(unload_models), "free_memory": True},
                )
                return {"ok": resp.status_code < 400, "status": resp.status_code}
        except httpx.HTTPError as exc:
            return {"ok": False, "error": str(exc)}

    def interrupt(self) -> dict:
        try:
            with self._client(timeout=10.0) as client:
                resp = client.post("/interrupt", json={})
                return {"ok": resp.status_code < 400}
        except httpx.HTTPError as exc:
            return {"ok": False, "error": str(exc)}

    # ---------------------------------------------------------------- workflow
    def validate_workflow(self, path: str | None = None,
                          overrides: dict | None = None) -> dict:
        workflow_path = path or settings.get("comfyui.workflow_path")
        result = {
            "ok": False,
            "path": str(workflow_path),
            "warnings": [],
            "errors": [],
            "summary": None,
            "comfyui": None,
        }
        try:
            workflow = load_workflow(workflow_path)
            mapper = WorkflowMapper(workflow, overrides or settings.get("comfyui.node_map", {}))
            summary = mapper.summary()
            result["summary"] = summary
            if not summary["ok"]:
                result["errors"].append(
                    "Workflow thiếu node prompt hoặc sampler. Hãy kiểm tra lại file API JSON."
                )
            result["warnings"].extend(summary["notes"])
            if summary["missing"]:
                result["errors"].append("Thiếu vai trò node: " + ", ".join(summary["missing"]))

            # Model availability check against the live server.
            health = self.health()
            result["comfyui"] = health
            if health["available"]:
                ckpt_node = summary["roles"].get("checkpoint", {}).get("node_id")
                if ckpt_node:
                    node = workflow.get(ckpt_node, {})
                    inputs = node.get("inputs") or {}
                    ckpt = inputs.get("ckpt_name")
                    if ckpt:
                        info = self.object_info(node.get("class_type", "CheckpointLoaderSimple"))
                        options = (
                            ((info or {}).get("input") or {}).get("required", {})
                            .get("ckpt_name", [None])[0]
                        )
                        if isinstance(options, list) and ckpt not in options:
                            result["warnings"].append(
                                f"Checkpoint '{ckpt}' chưa có trong ComfyUI models/checkpoints."
                            )
                sampler_node = summary["roles"].get("sampler", {}).get("node_id")
                if sampler_node:
                    sampler_inputs = workflow.get(sampler_node, {}).get("inputs") or {}
                    info = self.object_info("KSampler")
                    required = ((info or {}).get("input") or {}).get("required", {})
                    for key, settings_key in (("sampler_name", "sampler"), ("scheduler", "scheduler")):
                        chosen = sampler_inputs.get(key)
                        allowed = required.get(key, [None])[0]
                        if chosen and isinstance(allowed, list) and chosen not in allowed:
                            result["warnings"].append(
                                f"Giá trị {key} '{chosen}' không có trong danh sách của ComfyUI."
                            )
            else:
                result["warnings"].append(health["error"])
            result["ok"] = not result["errors"]
        except (WorkflowError, OSError) as exc:
            result["errors"].append(str(exc))
        return result

    def list_workflows(self) -> list[dict]:
        folder = PROJECT_ROOT / "config" / "workflows"
        items = []
        if folder.exists():
            for path in sorted(folder.glob("*.json")):
                items.append({
                    "name": path.name,
                    "path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                    "size": path.stat().st_size,
                })
        return items

    # ---------------------------------------------------------------- generate
    def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int | None = None,
        height: int | None = None,
        seed: int | None = None,
        batch_size: int = 1,
        steps: int | None = None,
        cfg: float | None = None,
        sampler: str | None = None,
        scheduler: str | None = None,
        workflow_path: str | None = None,
        overrides: dict | None = None,
        filename_prefix: str = "aiviral",
        dest_dir: Path | None = None,
        should_cancel: Callable[[], bool] | None = None,
        on_progress: Callable[[int, str], None] | None = None,
    ) -> dict:
        cfg_settings = settings.section("comfyui")
        workflow_path = workflow_path or cfg_settings.get("workflow_path")
        workflow = load_workflow(workflow_path)
        mapper = WorkflowMapper(workflow, overrides or cfg_settings.get("node_map") or {})
        summary = mapper.summary()
        if not summary["ok"]:
            raise ComfyUIError(
                "Workflow không có prompt hoặc sampler phù hợp. "
                + "; ".join(summary["notes"])
            )
        params = {
            "prompt": prompt,
            "negative_prompt": negative_prompt or cfg_settings.get("negative_prompt", ""),
            "width": width or cfg_settings.get("width", 1024),
            "height": height or cfg_settings.get("height", 1024),
            "seed": cfg_settings.get("seed", -1) if seed is None else seed,
            "batch_size": batch_size or cfg_settings.get("batch_size", 1),
            "steps": steps or cfg_settings.get("steps", 28),
            "cfg": cfg if cfg is not None else cfg_settings.get("cfg", 7.0),
            "sampler": sampler or cfg_settings.get("sampler"),
            "scheduler": scheduler or cfg_settings.get("scheduler"),
            "filename_prefix": filename_prefix or cfg_settings.get("filename_prefix", "aiviral"),
        }
        if cfg_settings.get("seed", -1) is None:
            params["seed"] = -1
        applied_workflow, applied = mapper.apply(params)
        client_id = uuid.uuid4().hex
        prompt_id = self._submit(applied_workflow, client_id)
        started = time.time()
        if on_progress:
            on_progress(5, "Đã gửi workflow tới ComfyUI…")
        history = self._wait(prompt_id, client_id, should_cancel, on_progress)
        elapsed = round(time.time() - started, 1)
        if on_progress:
            on_progress(88, "Đang tải ảnh kết quả…")
        images = self._collect_images(history, applied.get("seed"), applied, dest_dir, prompt_id)
        if not images:
            raise ComfyUIError(
                "ComfyUI chạy xong nhưng không trả về ảnh. Kiểm tra node SaveImage trong workflow."
            )
        if on_progress:
            on_progress(100, f"Đã nhận {len(images)} ảnh")
        return {
            "prompt_id": prompt_id,
            "images": images,
            "applied": applied,
            "seed": applied.get("seed"),
            "duration_seconds": elapsed,
            "workflow_path": str(workflow_path),
            "roles": summary["roles"],
        }

    def _submit(self, workflow: dict, client_id: str) -> str:
        try:
            with self._client(timeout=20.0) as client:
                resp = client.post("/prompt", json={"prompt": workflow, "client_id": client_id})
                if resp.status_code >= 400:
                    raise ComfyUIError(self._format_prompt_error(resp))
                data = resp.json()
                prompt_id = data.get("prompt_id")
                if not prompt_id:
                    raise ComfyUIError("ComfyUI không trả về prompt_id")
                if data.get("node_errors"):
                    raise ComfyUIError(self._format_node_errors(data["node_errors"]))
                return prompt_id
        except httpx.HTTPError as exc:
            raise ComfyUIError(
                f"Không gửi được workflow tới ComfyUI: {exc}. Kiểm tra ComfyUI đang chạy tại {self.base_url}."
            ) from exc

    @staticmethod
    def _format_prompt_error(resp: httpx.Response) -> str:
        try:
            body = resp.json()
        except ValueError:
            return f"ComfyUI trả về HTTP {resp.status_code}: {resp.text[:300]}"
        error = body.get("error") or {}
        message = error.get("message") if isinstance(error, dict) else str(error)
        node_errors = body.get("node_errors") or {}
        details = ComfyUIClient._format_node_errors(node_errors) if node_errors else ""
        return f"ComfyUI từ chối workflow: {message or 'lỗi không xác định'}. {details}".strip()

    @staticmethod
    def _format_node_errors(node_errors: dict) -> str:
        parts = []
        for node_id, payload in (node_errors or {}).items():
            if not isinstance(payload, dict):
                continue
            class_type = payload.get("class_type") or ""
            for err in (payload.get("errors") or [])[:2]:
                parts.append(
                    f"node {node_id} ({class_type}): {err.get('message') or err.get('details') or err}"
                )
        return "; ".join(parts)[:600]

    def _history(self, prompt_id: str) -> dict | None:
        try:
            with self._client(timeout=10.0) as client:
                data = client.get(f"/history/{prompt_id}").json()
                entry = data.get(prompt_id) if isinstance(data, dict) else None
                if entry and (entry.get("status") or {}).get("completed"):
                    return entry
                if entry and (entry.get("status") or {}).get("status_str") == "error":
                    return entry
                return entry if entry and entry.get("outputs") else None
        except (httpx.HTTPError, ValueError):
            return None

    def _wait(self, prompt_id: str, client_id: str,
              should_cancel: Callable[[], bool] | None,
              on_progress: Callable[[int, str], None] | None) -> dict:
        deadline = time.time() + self.timeout
        last_progress = 5
        last_poll = 0.0
        websocket = None
        try:
            from websockets.sync.client import connect  # type: ignore

            ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
            websocket = connect(f"{ws_url}/ws?clientId={client_id}", open_timeout=5, max_size=None)
        except Exception:  # noqa: BLE001 - websocket progress is optional
            websocket = None

        try:
            while True:
                if should_cancel and should_cancel():
                    self.interrupt()
                    raise CancelledError("Đã hủy tạo ảnh theo yêu cầu")
                if time.time() > deadline:
                    self.interrupt()
                    raise ComfyUIError(
                        f"Quá thời gian chờ ComfyUI ({self.timeout:.0f}s). "
                        "Hãy giảm kích thước/steps hoặc tăng timeout."
                    )
                message = None
                if websocket is not None:
                    try:
                        raw = websocket.recv(timeout=0.5)
                        message = json.loads(raw)
                    except TimeoutError:
                        message = None
                    except Exception:  # noqa: BLE001
                        websocket = None
                if message:
                    msg_type = message.get("type")
                    data = message.get("data") or {}
                    if msg_type == "progress":
                        maximum = max(1, int(data.get("max") or 1))
                        value = int(data.get("value") or 0)
                        percent = 8 + int(75 * value / maximum)
                        if percent > last_progress:
                            last_progress = percent
                            if on_progress:
                                on_progress(percent, f"ComfyUI đang lấy mẫu {value}/{maximum}")
                    elif msg_type == "execution_start":
                        if on_progress:
                            on_progress(max(last_progress, 8), "ComfyUI bắt đầu xử lý…")
                    elif msg_type == "executing" and data.get("node") is None:
                        last_progress = max(last_progress, 84)
                    elif msg_type == "execution_error":
                        detail = data.get("exception_message") or "lỗi thực thi"
                        raise ComfyUIError(f"ComfyUI gặp lỗi khi chạy workflow: {detail}")

                now = time.time()
                if now - last_poll >= 0.8:
                    last_poll = now
                    entry = self._history(prompt_id)
                    if entry:
                        status = entry.get("status") or {}
                        if status.get("status_str") == "error":
                            raise ComfyUIError(self._format_execution_error(status))
                        if status.get("completed") or entry.get("outputs"):
                            return entry
                if websocket is None:
                    time.sleep(0.4)
                    if on_progress:
                        queue = self.queue_status()
                        percent = min(75, last_progress + 1)
                        last_progress = percent
                        on_progress(percent, f"Đang chờ ComfyUI (hàng đợi: {queue['pending']})")
        finally:
            if websocket is not None:
                try:
                    websocket.close()
                except Exception:  # noqa: BLE001
                    pass

    @staticmethod
    def _image_bytes_valid(content: bytes) -> bool:
        if not content:
            return False
        if content.startswith(b"\xff\xd8\xff"):
            return True
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            return False
        position, idat, saw_end = 8, [], False
        length_total = len(content)
        while position + 8 <= length_total:
            length = struct.unpack(">I", content[position:position + 4])[0]
            chunk_type = content[position + 4:position + 8]
            end = position + 12 + length
            if length > length_total or end > length_total:
                return False
            if chunk_type == b"IDAT":
                idat.append(content[position + 8:position + 8 + length])
            elif chunk_type == b"IEND":
                saw_end = True
                break
            position = end
        if not saw_end or not idat:
            return False
        try:
            zlib.decompress(b"".join(idat))
        except zlib.error:
            return False
        return True

    @staticmethod
    def _format_execution_error(status: dict) -> str:
        messages = []
        for message in status.get("messages") or []:
            if isinstance(message, list) and len(message) == 2:
                kind, payload = message
                if kind == "execution_error" and isinstance(payload, dict):
                    messages.append(
                        payload.get("exception_message") or payload.get("exception_type") or ""
                    )
        return "ComfyUI lỗi khi chạy workflow: " + ("; ".join(m for m in messages if m)[:500] or "không rõ")

    def _collect_images(self, history: dict, seed: int | None, applied: dict,
                        dest_dir: Path | None, prompt_id: str) -> list[dict]:
        outputs = history.get("outputs") or {}
        save_node = None
        for role_node in [settings.get("comfyui.node_map", {}).get("save")]:
            if role_node and str(role_node) in outputs:
                save_node = str(role_node)
                break
        candidates: list[dict] = []
        if save_node:
            candidates = outputs[save_node].get("images") or []
        if not candidates:
            for node_output in outputs.values():
                for image in (node_output.get("images") or []):
                    candidates.append(image)
        if not candidates:
            return []

        target_dir = Path(dest_dir) if dest_dir else IMAGES_DIR / "unassigned"
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        saved: list[dict] = []
        for index, image in enumerate(candidates, start=1):
            filename = image.get("filename") or f"{prompt_id}_{index}.png"
            subfolder = image.get("subfolder") or ""
            image_type = image.get("type") or "output"
            content = b""
            for attempt in range(3):
                try:
                    with self._client(timeout=60.0) as client:
                        resp = client.get(
                            "/view",
                            params={"filename": filename, "subfolder": subfolder, "type": image_type},
                        )
                        resp.raise_for_status()
                        content = resp.content
                except httpx.HTTPError as exc:
                    raise ComfyUIError(f"Không tải được ảnh {filename} từ ComfyUI: {exc}") from exc
                if self._image_bytes_valid(content):
                    break
                # File may still be flushing; retry before giving up.
                time.sleep(1.5)
            if not self._image_bytes_valid(content):
                raise ComfyUIError(
                    f"Tệp ảnh {filename} từ ComfyUI bị hỏng/thiếu dữ liệu PNG. "
                    "Hãy kiểm tra ổ đĩa và thử tạo lại ảnh."
                )
            suffix = Path(filename).suffix or ".png"
            out_name = (
                f"{slugify(applied.get('filename_prefix') or 'aiviral', 24)}_"
                f"{timestamp}_{seed or 's'}_{index}{suffix}"
            )
            out_path = target_dir / out_name
            out_path.write_bytes(content)
            try:
                rel = out_path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
            except ValueError:
                rel = str(out_path)
            saved.append({
                "path": rel,
                "abs_path": str(out_path),
                "filename": out_path.name,
                "seed": seed,
                "width": applied.get("width"),
                "height": applied.get("height"),
                "subfolder": subfolder,
                "source": image_type,
                "bytes": len(content),
                "content_type": mimetypes.guess_type(out_name)[0],
            })
        return saved


comfyui = ComfyUIClient()
