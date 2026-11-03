"""Client for the local Ollama runtime (health checks, models, JSON and stream generation)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

from .config import settings


class OllamaError(RuntimeError):
    """Raised when Ollama is unreachable or returns an unusable response."""


@dataclass
class GenerationResult:
    text: str
    model: str
    done_reason: str = ""
    prompt_tokens: int = 0
    output_tokens: int = 0
    duration_seconds: float = 0.0
    raw: dict = field(default_factory=dict)


def _extract_json(text: str) -> Any:
    """Pull the first complete JSON object/array out of a model response."""
    if not text:
        raise ValueError("empty response")
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except ValueError:
        pass
    start_candidates = [i for i in (cleaned.find("{"), cleaned.find("[")) if i >= 0]
    if not start_candidates:
        raise ValueError("no JSON object found")
    start = min(start_candidates)
    depth = 0
    in_string = False
    escape = False
    opener = cleaned[start]
    closer = "}" if opener == "{" else "]"
    for idx in range(start, len(cleaned)):
        ch = cleaned[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start : idx + 1])
    raise ValueError("unterminated JSON in model response")


class OllamaClient:
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 timeout: float | None = None):
        self._base_url = base_url
        self._model = model
        self._timeout = timeout

    # ------------------------------------------------------------- properies
    @property
    def base_url(self) -> str:
        return (self._base_url or settings.get("ollama.base_url", "http://127.0.0.1:11434")).rstrip("/")

    @property
    def model(self) -> str:
        return self._model or settings.get("ollama.model", "qwen3:8b")

    @property
    def timeout(self) -> float:
        return float(self._timeout or settings.get("ollama.timeout_seconds", 240))

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(connect=5.0, read=self.timeout, write=30.0, pool=10.0),
        )

    # ---------------------------------------------------------------- health
    def health(self) -> dict:
        started = time.perf_counter()
        info: dict[str, Any] = {
            "base_url": self.base_url,
            "selected_model": self.model,
            "available": False,
            "model_available": False,
            "models": [],
            "latency_ms": None,
            "error": None,
            "version": None,
        }
        try:
            with self._client() as client:
                resp = client.get("/api/tags")
                resp.raise_for_status()
                data = resp.json()
            models = [
                {
                    "name": m.get("name"),
                    "size": m.get("size"),
                    "family": (m.get("details") or {}).get("family"),
                    "parameter_size": (m.get("details") or {}).get("parameter_size"),
                    "quantization": (m.get("details") or {}).get("quantization_level"),
                    "capabilities": m.get("capabilities") or [],
                }
                for m in data.get("models", [])
            ]
            info.update(
                available=True,
                models=models,
                model_available=any(m["name"] == self.model for m in models),
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            try:
                info["version"] = client.get("/api/version").json().get("version")
            except Exception:  # noqa: BLE001 - version endpoint is optional
                pass
        except (httpx.HTTPError, ValueError) as exc:
            info["error"] = (
                f"Không kết nối được Ollama tại {self.base_url}. "
                f"Hãy chạy 'ollama serve' và thử lại. Chi tiết: {exc}"
            )
        return info

    def list_models(self) -> list[dict]:
        health = self.health()
        if not health["available"]:
            raise OllamaError(health["error"] or "Ollama không khả dụng")
        return health["models"]

    def running_models(self) -> list[dict]:
        try:
            with self._client() as client:
                resp = client.get("/api/ps")
                resp.raise_for_status()
                return resp.json().get("models", [])
        except (httpx.HTTPError, ValueError):
            return []

    def ensure_model(self, name: str | None = None) -> None:
        target = name or self.model
        names = {m["name"] for m in self.list_models()}
        if target not in names:
            raise OllamaError(
                f"Model '{target}' chưa được cài đặt trong Ollama. "
                f"Hãy chạy: ollama pull {target} (ứng dụng không tự tải model)."
            )

    def unload(self, model: str | None = None) -> dict:
        """Ask Ollama to release a model from VRAM (keep_alive=0)."""
        target = model or self.model
        try:
            with self._client() as client:
                resp = client.post(
                    "/api/generate",
                    json={"model": target, "keep_alive": 0, "prompt": ""},
                    timeout=httpx.Timeout(connect=3.0, read=30.0, write=10.0, pool=5.0),
                )
                return {"ok": resp.status_code < 400, "status": resp.status_code}
        except httpx.HTTPError as exc:
            return {"ok": False, "error": str(exc)}

    # -------------------------------------------------------------- generate
    def _payload(
        self,
        prompt: str,
        system: str | None,
        model: str | None,
        format_value,
        temperature: float | None,
        num_predict: int | None,
        think: bool | None,
        stream: bool,
    ) -> dict:
        cfg = settings.section("ollama")
        payload: dict[str, Any] = {
            "model": model or self.model,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "temperature": cfg.get("temperature", 0.8) if temperature is None else temperature,
                "top_p": cfg.get("top_p", 0.9),
                "num_ctx": int(cfg.get("num_ctx", 8192)),
            },
            "keep_alive": cfg.get("keep_alive", "10m"),
        }
        if system:
            payload["system"] = system
        if format_value is not None:
            payload["format"] = format_value
        if num_predict:
            payload["options"]["num_predict"] = int(num_predict)
        think_value = cfg.get("think") if think is None else think
        if think_value is not None:
            payload["think"] = bool(think_value)
        return payload

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        num_predict: int | None = None,
        think: bool | None = None,
        json_schema: dict | None = None,
    ) -> GenerationResult:
        format_value: Any = json_schema if json_schema else None
        started = time.perf_counter()
        try:
            result = self._generate_once(
                prompt, system, model, format_value, temperature, num_predict, think
            )
        except OllamaError:
            raise
        except httpx.HTTPStatusError as exc:
            # Older runtimes reject schema objects or the "think" flag: retry leniently.
            body = ""
            try:
                body = exc.response.text[:400]
            except Exception:  # noqa: BLE001
                pass
            if exc.response.status_code == 400:
                try:
                    result = self._generate_once(
                        prompt, system, model,
                        "json" if json_schema else None,
                        temperature, num_predict, think=False,
                    )
                except Exception as exc2:  # noqa: BLE001
                    raise OllamaError(
                        f"Ollama từ chối yêu cầu: {body or exc2}"
                    ) from exc2
            else:
                raise OllamaError(f"Ollama trả về lỗi HTTP {exc.response.status_code}: {body}")
        except httpx.HTTPError as exc:
            raise OllamaError(
                f"Không kết nối được Ollama tại {self.base_url}: {exc}. "
                "Hãy kiểm tra dịch vụ Ollama."
            ) from exc
        result.duration_seconds = round(time.perf_counter() - started, 2)
        result.text = (result.text or "").strip()
        return result

    def _generate_once(self, prompt, system, model, format_value, temperature,
                       num_predict, think) -> GenerationResult:
        payload = self._payload(
            prompt, system, model, format_value, temperature, num_predict, think, stream=False
        )
        with self._client() as client:
            resp = client.post("/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
        if data.get("error"):
            raise OllamaError(str(data["error"]))
        return GenerationResult(
            text=data.get("response") or data.get("thinking") or "",
            model=data.get("model") or payload["model"],
            done_reason=data.get("done_reason", ""),
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
            raw=data,
        )

    def vision_json(
        self,
        prompt: str,
        images: list[str],
        model: str,
        system: str | None = None,
        temperature: float = 0.1,
        num_predict: int = 400,
        json_schema: dict | None = None,
    ) -> GenerationResult:
        """Ask a local vision model about one or more images (base64 payloads)."""
        payload = self._payload(
            prompt, system, model, json_schema, temperature, num_predict, False, False
        )
        payload["images"] = images
        payload["keep_alive"] = "5m"
        started = time.perf_counter()
        try:
            with self._client() as client:
                resp = client.post("/api/generate", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            body = ""
            try:
                body = exc.response.text[:300]
            except Exception:  # noqa: BLE001
                pass
            raise OllamaError(f"Ollama từ chối yêu cầu vision: {body or exc}") from exc
        except httpx.HTTPError as exc:
            raise OllamaError(
                f"Không kết nối được Ollama tại {self.base_url}: {exc}"
            ) from exc
        if data.get("error"):
            raise OllamaError(str(data["error"]))
        result = GenerationResult(
            text=data.get("response") or "",
            model=data.get("model") or model,
            done_reason=data.get("done_reason", ""),
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
            raw=data,
        )
        result.duration_seconds = round(time.perf_counter() - started, 2)
        result.text = (result.text or "").strip()
        return result

    def generate_json(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict | None = None,
        model: str | None = None,
        temperature: float = 0.35,
        num_predict: int = 3500,
        attempts: int = 2,
    ) -> dict:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                result = self.generate(
                    prompt,
                    system=system,
                    model=model,
                    temperature=temperature,
                    num_predict=num_predict,
                    json_schema=schema,
                )
                data = _extract_json(result.text)
                if not isinstance(data, dict):
                    raise ValueError("JSON gốc không phải object")
                data.setdefault("_meta", {})
                data["_meta"].update(
                    {
                        "model": result.model,
                        "prompt_tokens": result.prompt_tokens,
                        "output_tokens": result.output_tokens,
                        "duration_seconds": result.duration_seconds,
                        "attempt": attempt + 1,
                    }
                )
                return data
            except (ValueError, OllamaError) as exc:
                last_error = exc
                if isinstance(exc, OllamaError) and "kết nối" in str(exc).lower():
                    break
                time.sleep(0.6)
        raise OllamaError(f"Không nhận được JSON hợp lệ từ model: {last_error}")

    def stream(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> Iterator[str]:
        payload = self._payload(prompt, system, model, None, temperature, None, None, stream=True)
        try:
            with self._client() as client:
                with client.stream("POST", "/api/generate", json=payload) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except ValueError:
                            continue
                        if chunk.get("error"):
                            raise OllamaError(str(chunk["error"]))
                        piece = chunk.get("response") or ""
                        if piece:
                            yield piece
                        if chunk.get("done"):
                            break
        except httpx.HTTPError as exc:
            raise OllamaError(
                f"Không kết nối được Ollama tại {self.base_url}: {exc}"
            ) from exc


ollama = OllamaClient()
