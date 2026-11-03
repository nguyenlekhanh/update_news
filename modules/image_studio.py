"""Smart image studio: style presets, aspect ratios and deterministic checks.

The ComfyUI integration stays exactly as it is; this module decides *what* to ask
it for (style + aspect + prompt suffix) and then inspects the result. Checks that
can be measured (dimensions, aspect, byte size, duplicate hash) are labelled
``verified``. Text artefacts and prompt-level risk can only be estimated without
a vision model, so they are labelled ``heuristic`` and the UI offers an optional
vision assessment instead of pretending to have read the image.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterable

from .config import PROJECT_ROOT, settings
from .utils import truncate

STYLE_EDITORIAL = "editorial_illustration"
STYLE_PHOTO = "photorealistic_generic"
STYLE_INFOGRAPHIC = "infographic"
STYLE_CONCEPTUAL = "conceptual"
STYLE_SOCIAL = "social_graphic"

STYLE_ORDER = (
    STYLE_EDITORIAL, STYLE_PHOTO, STYLE_INFOGRAPHIC, STYLE_CONCEPTUAL, STYLE_SOCIAL,
)

STYLES: dict[str, dict] = {
    STYLE_EDITORIAL: {
        "label_vi": "Minh họa biên tập",
        "label_en": "Editorial illustration",
        "suffix": (
            "editorial magazine illustration, conceptual composition, clean shapes, "
            "soft cinematic lighting, high detail, clearly illustrative"
        ),
        "negative": "photorealistic news photograph, documentary photo",
        "text_risk": "low",
        "note_vi": "An toàn cho tin có người thật: rõ ràng là hình minh họa.",
    },
    STYLE_PHOTO: {
        "label_vi": "Ảnh đời thường (không người cụ thể)",
        "label_en": "Generic photorealistic scene",
        "suffix": (
            "photorealistic generic environment, natural daylight, shallow depth of field, "
            "anonymous figures only, no identifiable faces, editorial photography"
        ),
        "negative": (
            "real identifiable person, celebrity likeness, portrait of a specific person, "
            "fake documentary evidence"
        ),
        "text_risk": "low",
        "note_vi": "Chỉ dùng cho bối cảnh chung; không tạo chân dung người thật.",
    },
    STYLE_INFOGRAPHIC: {
        "label_vi": "Infographic",
        "label_en": "Infographic",
        "suffix": (
            "clean editorial infographic, simple geometric icons, abstract chart shapes, "
            "generous whitespace, limited palette, no readable text or numbers"
        ),
        "negative": "readable text, gibberish letters, numbers, logo, watermark",
        "text_risk": "high",
        "note_vi": "Infographic dễ sinh chữ rác — nên kiểm tra ảnh trước khi dùng.",
    },
    STYLE_CONCEPTUAL: {
        "label_vi": "Ý niệm trừu tượng",
        "label_en": "Conceptual artwork",
        "suffix": (
            "surreal conceptual artwork, symbolic objects, dramatic rim light, "
            "deep shadows, fine art editorial mood"
        ),
        "negative": "text, watermark, real identifiable person",
        "text_risk": "low",
        "note_vi": "Phù hợp tin công nghệ, khoa học, phân tích.",
    },
    STYLE_SOCIAL: {
        "label_vi": "Đồ họa mạng xã hội",
        "label_en": "Social media graphic",
        "suffix": (
            "bold social media graphic design, gradient background, geometric composition, "
            "high contrast, modern editorial layout, no text"
        ),
        "negative": "text, letters, logo, watermark, cluttered layout",
        "text_risk": "medium",
        "note_vi": "Dùng cho nội dung văn hóa mạng, xu hướng trẻ.",
    },
}

ASPECTS: dict[str, dict] = {
    "1:1": {"label_vi": "Vuông 1:1", "label_en": "Square 1:1", "width": 1024, "height": 1024},
    "4:5": {"label_vi": "Dọc 4:5", "label_en": "Portrait 4:5", "width": 1024, "height": 1280},
    "16:9": {"label_vi": "Ngang 16:9", "label_en": "Landscape 16:9", "width": 1280, "height": 720},
}

DEFAULT_STYLE = STYLE_EDITORIAL
DEFAULT_ASPECT = "1:1"

CATEGORY_STYLE = {
    "business": STYLE_INFOGRAPHIC,
    "finance": STYLE_INFOGRAPHIC,
    "ai": STYLE_CONCEPTUAL,
    "technology": STYLE_CONCEPTUAL,
    "science": STYLE_CONCEPTUAL,
    "lifestyle": STYLE_PHOTO,
    "human_interest": STYLE_PHOTO,
    "education": STYLE_PHOTO,
    "internet_culture": STYLE_SOCIAL,
    "discovery": STYLE_SOCIAL,
    "entertainment": STYLE_SOCIAL,
    "celebrity": STYLE_SOCIAL,
}

CATEGORY_ASPECT = {
    "lifestyle": "4:5",
    "human_interest": "4:5",
    "celebrity": "4:5",
    "business": "16:9",
    "finance": "16:9",
}

TEXT_CUES = (
    r"\btext\b", r"\bwords?\b", r"\bletters?\b", r"\blogo\b", r"\bwatermark\b",
    r"\btitle\b", r"\bcaption\b", r"\bsignage\b", r"\bchữ\b", r"\bbiểu ngữ\b",
    r"\bslogan\b", r'"[^"]{2,}"', r"“[^”]{2,}”",
)

ASPECT_TOLERANCE = 0.02
MIN_FILE_BYTES = 12_000
_HASH_CACHE: dict[str, tuple[float, str]] = {}


def normalise_style(value: str | None) -> str:
    return value if value in STYLES else DEFAULT_STYLE


def normalise_aspect(value: str | None) -> str:
    return value if value in ASPECTS else DEFAULT_ASPECT


def options() -> dict:
    return {
        "styles": [
            {
                "key": key,
                "label_vi": STYLES[key]["label_vi"],
                "label_en": STYLES[key]["label_en"],
                "text_risk": STYLES[key]["text_risk"],
                "note_vi": STYLES[key]["note_vi"],
            }
            for key in STYLE_ORDER
        ],
        "aspects": [
            {
                "key": key,
                "label_vi": ASPECTS[key]["label_vi"],
                "label_en": ASPECTS[key]["label_en"],
                "width": ASPECTS[key]["width"],
                "height": ASPECTS[key]["height"],
            }
            for key in ASPECTS
        ],
        "default_style": settings.get("images.style", "auto"),
        "default_aspect": settings.get("images.aspect", DEFAULT_ASPECT),
    }


def recommend(category: str | None, strategy_key: str | None = None) -> dict:
    """Suggest a visual style and aspect ratio from the story's category."""
    key = (category or "general").lower()
    if strategy_key in ("business_impact", "tech_explainer"):
        style = STYLE_INFOGRAPHIC if strategy_key == "business_impact" else STYLE_CONCEPTUAL
    else:
        style = CATEGORY_STYLE.get(key, STYLE_EDITORIAL)
    aspect = CATEGORY_ASPECT.get(key, DEFAULT_ASPECT)
    return {
        "style": style,
        "aspect": aspect,
        "label_vi": STYLES[style]["label_vi"],
        "reason": (
            f"Nhóm '{key}' thường phù hợp {STYLES[style]['label_vi'].lower()} "
            f"với tỉ lệ {aspect}. {STYLES[style]['note_vi']}"
        ),
        "kind": "rule_based",
    }


def resolve_style(style: str | None, category: str | None = None,
                  strategy_key: str | None = None) -> dict:
    """Resolve 'auto' to a concrete recommended style."""
    chosen = (style or "").strip()
    if chosen in STYLES:
        return {"style": chosen, "source": "user", "recommendation": None}
    recommendation = recommend(category, strategy_key)
    if chosen == "auto" or not chosen:
        return {
            "style": recommendation["style"],
            "source": "auto",
            "recommendation": recommendation,
        }
    fallback = settings.get("images.style", "auto")
    if fallback in STYLES:
        return {"style": fallback, "source": "settings", "recommendation": None}
    return {
        "style": recommendation["style"],
        "source": "auto",
        "recommendation": recommendation,
    }


def resolve_aspect(aspect: str | None) -> dict:
    key = normalise_aspect(aspect or settings.get("images.aspect", DEFAULT_ASPECT))
    return {"aspect": key, **ASPECTS[key]}


def prepare(base_prompt: str, style: str | None = None, aspect: str | None = None,
            category: str | None = None, strategy_key: str | None = None,
            extra_negative: str = "") -> dict:
    """Turn a base prompt into a styled ComfyUI request."""
    resolved = resolve_style(style, category, strategy_key)
    style_key = resolved["style"]
    size = resolve_aspect(aspect)
    definition = STYLES[style_key]

    prompt = (base_prompt or "").strip().rstrip(".")
    suffix = definition["suffix"]
    if suffix and suffix.split(",")[0].strip().lower() not in prompt.lower():
        prompt = f"{prompt}, {suffix}" if prompt else suffix

    negative = ", ".join(
        part for part in (
            extra_negative.strip(),
            definition.get("negative", ""),
            "text, letters, watermark, logo, signature",
        ) if part
    )
    return {
        "prompt": prompt,
        "negative_prompt": negative,
        "style": style_key,
        "style_source": resolved["source"],
        "style_recommendation": resolved["recommendation"],
        "aspect": size["aspect"],
        "width": size["width"],
        "height": size["height"],
        "text_risk": definition["text_risk"],
    }


def _open_path(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def sha256_of(path: str) -> str | None:
    file_path = _open_path(path)
    try:
        stat = file_path.stat()
    except OSError:
        return None
    cached = _HASH_CACHE.get(str(file_path))
    if cached and cached[0] == stat.st_mtime:
        return cached[1]
    digest = hashlib.sha256()
    try:
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    value = digest.hexdigest()
    _HASH_CACHE[str(file_path)] = (stat.st_mtime, value)
    return value


def image_size(path: str) -> tuple[int, int] | None:
    """Read PNG / JPEG dimensions without extra dependencies."""
    file_path = _open_path(path)
    try:
        data = file_path.read_bytes()
    except OSError:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:2] == b"\xff\xd8":
        index = 2
        length = len(data)
        while index + 9 < length:
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                height = int.from_bytes(data[index + 5:index + 7], "big")
                width = int.from_bytes(data[index + 7:index + 9], "big")
                return width, height
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                index += 2
                continue
            segment = int.from_bytes(data[index + 2:index + 4], "big")
            if segment <= 0:
                break
            index += 2 + segment
    return None


def text_artifact_risk(prompt: str, style: str | None = None) -> dict:
    """Heuristic: does the prompt invite rendered text?"""
    prompt_text = prompt or ""
    cues = []
    for pattern in TEXT_CUES:
        for match in re.findall(pattern, prompt_text, flags=re.IGNORECASE):
            value = match if isinstance(match, str) else match[0]
            if value and value.lower() not in {c.lower() for c in cues}:
                cues.append(truncate(value, 40))
    base = STYLES.get(normalise_style(style), STYLES[DEFAULT_STYLE])["text_risk"]
    score = {"low": 15, "medium": 40, "high": 65}.get(base, 15)
    score += min(30, len(cues) * 8)
    score = min(100, score)
    level = "high" if score >= 55 else "medium" if score >= 30 else "low"
    return {
        "risk": level,
        "score": score,
        "cues": cues[:6],
        "kind": "heuristic",
        "note": (
            "Ước tính từ prompt và phong cách ảnh; cần vision model để xác nhận chữ trong ảnh."
            if level != "low"
            else "Prompt không chứa từ khóa dễ sinh chữ."
        ),
    }


def check_image(path: str, prompt: str = "", aspect: str | None = None,
                style: str | None = None,
                known_hashes: dict[str, str] | None = None) -> dict:
    """Deterministic quality report for one generated image."""
    file_path = _open_path(path)
    checks: list[dict] = []
    exists = file_path.exists()
    data: bytes = b""
    if exists:
        try:
            data = file_path.read_bytes()
        except OSError:
            exists = False

    size = image_size(path) if exists else None
    width, height = size if size else (None, None)
    expected = resolve_aspect(aspect or settings.get("images.aspect", DEFAULT_ASPECT))
    min_side = int(settings.get("images.min_short_side", 640))

    checks.append({
        "key": "file", "status": "ok" if exists else "fail", "kind": "verified",
        "detail": f"{len(data) / 1024:.0f} KB" if exists else "Không tìm thấy tệp ảnh",
    })
    if width and height:
        short_side = min(width, height)
        checks.append({
            "key": "dimensions", "status": "ok" if short_side >= min_side else "warn",
            "kind": "verified",
            "detail": f"{width}×{height}px" + (
                "" if short_side >= min_side else f" (cạnh ngắn dưới {min_side}px)"
            ),
        })
        actual = width / height if height else 0
        target = expected["width"] / expected["height"]
        matches = abs(actual - target) <= ASPECT_TOLERANCE
        checks.append({
            "key": "aspect", "status": "ok" if matches else "warn", "kind": "verified",
            "detail": (
                f"{expected['aspect']} khớp" if matches
                else f"Ảnh {width}×{height} lệch tỉ lệ {expected['aspect']}"
            ),
        })
    else:
        checks.append({
            "key": "dimensions", "status": "fail", "kind": "verified",
            "detail": "Không đọc được kích thước ảnh",
        })
        checks.append({
            "key": "aspect", "status": "fail", "kind": "verified",
            "detail": f"Không xác định được tỉ lệ {expected['aspect']}",
        })

    digest = sha256_of(path) if exists else None
    duplicate_of = (known_hashes or {}).get(digest) if digest else None
    checks.append({
        "key": "duplicate",
        "status": "fail" if duplicate_of else "ok",
        "kind": "verified",
        "detail": f"Trùng với {duplicate_of}" if duplicate_of else "Không trùng ảnh đã có",
    })

    if len(data) < MIN_FILE_BYTES and exists:
        checks.append({
            "key": "bytes", "status": "warn", "kind": "verified",
            "detail": f"Tệp nhỏ bất thường ({len(data) / 1024:.0f} KB)",
        })

    text_risk = text_artifact_risk(prompt, style)
    checks.append({
        "key": "text",
        "status": "warn" if text_risk["risk"] != "low" else "ok",
        "kind": "heuristic",
        "detail": (
            f"Nguy cơ chữ trong ảnh: {text_risk['risk']}"
            + (f" ({', '.join(text_risk['cues'][:3])})" if text_risk["cues"] else "")
        ),
    })

    score = 100.0
    for check in checks:
        if check["status"] == "fail":
            score -= 35 if check["key"] != "duplicate" else 45
        elif check["status"] == "warn":
            score -= 12
    score = max(0.0, min(100.0, score))
    failures = [c for c in checks if c["status"] == "fail"]
    warnings = [c for c in checks if c["status"] == "warn"]
    verdict = "reject" if failures else "review" if warnings else "ok"

    return {
        "path": str(path),
        "exists": exists,
        "bytes": len(data),
        "sha256": digest,
        "width": width,
        "height": height,
        "aspect": expected["aspect"],
        "expected": {"width": expected["width"], "height": expected["height"]},
        "checks": checks,
        "score": round(score, 1),
        "verdict": verdict,
        "text_artifacts": text_risk,
        "duplicate": {
            "is_duplicate": bool(duplicate_of),
            "of": duplicate_of,
            "kind": "verified",
        },
        "vision_recommended": bool(text_risk["risk"] != "low" or duplicate_of),
        "disclaimer": (
            "Kiểm tra tự động chỉ đo kích thước, tỉ lệ, dung lượng, hash trùng lặp và "
            "rủi ro chữ từ prompt. Muốn đánh giá nội dung ảnh cần vision model (tùy chọn)."
        ),
    }


def hash_map(images: Iterable[dict], skip_id: int | None = None) -> dict[str, str]:
    """Map sha256 → label for duplicate detection across stored images."""
    mapping: dict[str, str] = {}
    for image in images or []:
        if skip_id is not None and image.get("id") == skip_id:
            continue
        digest = (image.get("meta") or {}).get("sha256") or sha256_of(image.get("path") or "")
        if not digest:
            continue
        label = image.get("headline") or f"ảnh #{image.get('id')}"
        mapping.setdefault(digest, label)
    return mapping
