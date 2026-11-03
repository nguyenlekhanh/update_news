"""Configuration handling: defaults, .env secrets, persisted dashboard settings."""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
IMAGES_DIR = OUTPUT_DIR / "images"
CONFIG_DIR = PROJECT_ROOT / "config"
WEB_DIR = PROJECT_ROOT / "web"
EXPORT_DIR = OUTPUT_DIR / "exports"
DB_PATH = DATA_DIR / "studio.db"
SETTINGS_PATH = DATA_DIR / "settings.json"
SOURCES_SEED_PATH = CONFIG_DIR / "sources.json"
LOG_DIR = DATA_DIR / "logs"

for folder in (DATA_DIR, OUTPUT_DIR, IMAGES_DIR, EXPORT_DIR, LOG_DIR):
    folder.mkdir(parents=True, exist_ok=True)

load_dotenv(PROJECT_ROOT / ".env")


def _env(key: str, default: str = "") -> str:
    value = os.getenv(key)
    return value.strip() if isinstance(value, str) and value.strip() else default


DEFAULT_SETTINGS: dict[str, Any] = {
    "ui": {
        "language": "vi",
    },
    "ollama": {
        "base_url": _env("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        "model": _env("OLLAMA_MODEL", "qwen3:8b"),
        "temperature": 0.8,
        "top_p": 0.9,
        "num_ctx": 8192,
        "timeout_seconds": 240,
        "keep_alive": "10m",
        "think": False,
    },
    "comfyui": {
        "base_url": _env("COMFYUI_BASE_URL", "http://127.0.0.1:8188"),
        "workflow_path": "config/workflows/default_sdxl_text2img.json",
        "node_map": {},
        "width": 1024,
        "height": 1024,
        "steps": 28,
        "cfg": 7.0,
        "sampler": "dpmpp_2m",
        "scheduler": "karras",
        "seed": -1,
        "batch_size": 1,
        "timeout_seconds": 420,
        "filename_prefix": "aiviral",
        "negative_prompt": (
            "text, watermark, logo, signature, low quality, blurry, deformed hands, "
            "extra fingers, distorted face, photorealistic depiction of real identifiable people, "
            "fake news screenshot, fake documentary photo"
        ),
    },
    "writer": {
        "language": "vi",
        "style": "friendly_storytelling",
        "tone": "engaging",
        "audience": "general",
        "length": "medium",
        "hashtag_count": 6,
        "category": "auto",
        "include_sources": True,
        "avoid_clickbait": True,
    },
    "trends": {
        "max_topics": 12,
        "lookback_hours": 36,
        "max_candidates": 400,
        "min_score": 40.0,
        "geo": "VN",
        "categories": [
            "general", "vietnam", "international", "ai", "technology", "business",
            "finance", "entertainment", "celebrity", "lifestyle", "science",
            "education", "social", "discovery", "human_interest", "internet_culture",
        ],
        "auto_discover": False,
        "discover_interval_minutes": 90,
        "llm_editorial": True,
    },
    "facebook": {
        "enabled": False,
        "page_ids": [],
    },
    "research": {
        "max_articles": 6,
        "min_sources": 2,
        "max_chars_per_article": 5000,
        "fetch_timeout_seconds": 25,
        "max_concurrency": 5,
        "respect_robots": True,
        "search_more_sources": True,
    },
    "quality": {
        "vision_model": "qwen2.5vl:7b",
        "auto_assess_images": False,
    },
    "strategy": {
        "enabled": True,
        "apply_to_writing": True,
        "allow_override": True,
    },
    "learning": {
        "enabled": True,
        "use_in_prompts": True,
        "min_samples": 3,
    },
    "images": {
        "style": "auto",
        "aspect": "1:1",
        "detect_duplicates": True,
        "text_risk_check": True,
        "min_short_side": 640,
    },
    "calendar": {
        "enabled": True,
        "utc_offset_hours": 7,
        "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        "time_windows": ["08:00-09:00", "12:00-13:00", "20:00-21:30"],
        "max_per_day": 2,
        "categories": [],
        "reminder_hours": 24,
    },
    "facebook_metrics": {
        "enabled": False,
        "graph_version": "v21.0",
        "utc_offset_hours": 7,
    },
    "pipeline": {
        "auto_image": True,
        "image_variations": 1,
        "auto_research_on_discover": False,
        "workers": 2,
    },
    "resource": {
        "serialize_gpu": True,
        "unload_ollama_before_image": True,
        "free_comfy_before_llm": False,
        "min_free_vram_mb": 1500,
        "gpu_wait_seconds": 45,
        "max_concurrent_image_jobs": 1,
    },
}

_SECRET_ENV_KEYS = {
    "facebook": "FACEBOOK_ACCESS_TOKEN",
    "serper": "SERPER_API_KEY",
    "brave": "BRAVE_SEARCH_API_KEY",
}


def _deep_merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def resolve_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (PROJECT_ROOT / p)


def secret_status() -> dict[str, bool]:
    return {name: bool(_env(env_key)) for name, env_key in _SECRET_ENV_KEYS.items()}


def get_secret(name: str) -> str:
    env_key = _SECRET_ENV_KEYS.get(name)
    return _env(env_key) if env_key else ""


class Settings:
    """Thread-safe JSON-backed settings store merged over defaults."""

    def __init__(self, path: Path = SETTINGS_PATH):
        self._path = path
        self._lock = threading.RLock()
        self._data = copy.deepcopy(DEFAULT_SETTINGS)
        self.reload()

    def reload(self) -> None:
        with self._lock:
            self._data = copy.deepcopy(DEFAULT_SETTINGS)
            if self._path.exists():
                try:
                    stored = json.loads(self._path.read_text(encoding="utf-8"))
                    if isinstance(stored, dict):
                        self._data = _deep_merge(self._data, stored)
                except (OSError, ValueError):
                    pass
            # Environment variables win for service endpoints.
            if _env("OLLAMA_BASE_URL"):
                self._data["ollama"]["base_url"] = _env("OLLAMA_BASE_URL")
            if _env("OLLAMA_MODEL"):
                self._data["ollama"]["model"] = _env("OLLAMA_MODEL")
            if _env("COMFYUI_BASE_URL"):
                self._data["comfyui"]["base_url"] = _env("COMFYUI_BASE_URL")
            if not self._path.exists():
                self._persist()

    def _persist(self) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    def all(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def section(self, name: str) -> dict:
        with self._lock:
            return copy.deepcopy(self._data.get(name, {}))

    def get(self, dotted: str, default=None):
        with self._lock:
            node: Any = self._data
            for part in dotted.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def update(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise ValueError("settings patch must be an object")
        with self._lock:
            self._data = _deep_merge(self._data, patch)
            self._persist()
            return self.all()

    def reset(self) -> dict:
        with self._lock:
            self._data = copy.deepcopy(DEFAULT_SETTINGS)
            self._persist()
            return self.all()


settings = Settings()
