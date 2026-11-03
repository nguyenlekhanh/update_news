"""GPU-aware arbitration between Ollama and ComfyUI on a single RTX 3060."""

from __future__ import annotations

import subprocess
import threading
import time
from contextlib import contextmanager

from .comfyui_client import ComfyUIClient, comfyui
from .config import settings
from .ollama_client import OllamaClient, ollama


class ResourceManager:
    """Serialises GPU-heavy phases and frees VRAM before switching engines."""

    def __init__(self, ollama_client: OllamaClient | None = None,
                 comfy_client: ComfyUIClient | None = None):
        self.ollama = ollama_client or ollama
        self.comfy = comfy_client or comfyui
        self.gpu_lock = threading.Lock()
        self.last_event: str = ""

    # ------------------------------------------------------------------- VRAM
    @staticmethod
    def vram() -> dict | None:
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,memory.free,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=6,
                check=False,
            )
            line = (result.stdout or "").strip().splitlines()[0]
            name, total, free, util = [part.strip() for part in line.split(",")[:4]]
            return {
                "name": name,
                "total_mb": int(float(total)),
                "free_mb": int(float(free)),
                "used_mb": int(float(total)) - int(float(free)),
                "utilization": int(float(util)),
            }
        except (IndexError, ValueError, OSError, subprocess.SubprocessError):
            return None

    def wait_for_free_vram(self, minimum_mb: int, timeout: int) -> bool:
        deadline = time.time() + max(1, timeout)
        while time.time() < deadline:
            info = self.vram()
            if not info or info["free_mb"] >= minimum_mb:
                return True
            time.sleep(1.0)
        return False

    # ---------------------------------------------------------------- phases
    @contextmanager
    def gpu_phase(self, phase: str):
        """Serialise GPU work; research/network work runs outside the lock."""
        if not settings.get("resource.serialize_gpu", True):
            self.prepare(phase)
            yield
            return
        with self.gpu_lock:
            self.prepare(phase)
            try:
                yield
            finally:
                self.last_event = f"{phase}:done"

    def prepare(self, phase: str) -> None:
        minimum = int(settings.get("resource.min_free_vram_mb", 1500))
        if phase == "llm":
            info = self.vram()
            if info and info["free_mb"] < minimum and settings.get(
                "resource.free_comfy_before_llm", True
            ):
                self.comfy.free_memory(unload_models=True)
                self.last_event = "Đã giải phóng model ComfyUI khỏi VRAM"
                self.wait_for_free_vram(minimum, int(settings.get("resource.gpu_wait_seconds", 45)))
        elif phase == "image":
            if settings.get("resource.unload_ollama_before_image", True):
                self.ollama.unload()
                self.last_event = "Đã yêu cầu Ollama giải phóng VRAM"
                self.wait_for_free_vram(
                    minimum, int(settings.get("resource.gpu_wait_seconds", 45))
                )
            # Never call ComfyUI /free automatically: on this build it can wedge
            # the server event loop. ComfyUI manages its own model cache.

    # ---------------------------------------------------------------- status
    def status(self) -> dict:
        return {
            "vram": self.vram(),
            "gpu_busy": self.gpu_lock.locked(),
            "last_event": self.last_event,
            "ollama_running": [m.get("name") for m in self.ollama.running_models()],
            "settings": settings.section("resource"),
        }


resource_manager = ResourceManager()
