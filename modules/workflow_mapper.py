"""ComfyUI workflow parsing with graph-based node role detection.

Node IDs are never hardcoded: roles (positive prompt, negative prompt, latent
size, sampler, image saver, checkpoint) are discovered by walking the API graph
from the sampler that feeds the saved image. Manual overrides are supported for
unusual graphs.
"""

from __future__ import annotations

import copy
import json
import random
import re
from pathlib import Path

from .config import resolve_path

SAMPLER_TYPES = {
    "KSampler",
    "KSamplerAdvanced",
    "KSamplerSelect",
    "SamplerCustom",
    "SamplerCustomAdvanced",
    "FluxSampler",
}

SAVE_TYPES = {"SaveImage", "Image Save", "SaveImageWebsocket", "SaveImageWithMetadata"}

CHECKPOINT_TYPES = {"CheckpointLoaderSimple", "CheckpointLoader"}

TEXT_INPUT_KEYS = ("text", "text_g", "text_l", "prompt")

ROLE_LABELS = {
    "positive": "Prompt tích cực",
    "negative": "Prompt tiêu cực",
    "latent": "Kích thước ảnh",
    "sampler": "Bộ lấy mẫu",
    "save": "Lưu ảnh",
    "checkpoint": "Checkpoint",
}


class WorkflowError(ValueError):
    pass


def load_workflow(path: str | Path) -> dict:
    resolved = resolve_path(path)
    if not resolved.exists():
        raise WorkflowError(f"Không tìm thấy file workflow: {resolved}")
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkflowError(f"File workflow không phải JSON hợp lệ: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkflowError("Workflow phải là object JSON")
    return data


def is_api_format(workflow: dict) -> bool:
    if not workflow:
        return False
    if "nodes" in workflow and isinstance(workflow.get("nodes"), list):
        return False
    for value in workflow.values():
        if not isinstance(value, dict) or "class_type" not in value:
            return False
    return True


def _ref_id(value) -> str | None:
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], (str, int)):
        return str(value[0])
    return None


def _ancestors(workflow: dict, node_id: str) -> list[str]:
    """Breadth-first upstream traversal, nearest nodes first."""
    order: list[str] = []
    queue = [node_id]
    seen = set()
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        node = workflow.get(current)
        if not isinstance(node, dict):
            continue
        order.append(current)
        for value in (node.get("inputs") or {}).values():
            ref = _ref_id(value)
            if ref and ref not in seen:
                queue.append(ref)
    return order


def _set_text(node: dict, value: str) -> bool:
    inputs = node.get("inputs") or {}
    changed = False
    if "text" in inputs:
        inputs["text"] = value
        changed = True
    if "text_g" in inputs:
        inputs["text_g"] = value
        changed = True
    if "text_l" in inputs:
        inputs["text_l"] = value
        changed = True
    if not changed and "prompt" in inputs:
        inputs["prompt"] = value
        changed = True
    return changed


def _text_input_name(node: dict) -> str | None:
    inputs = node.get("inputs") or {}
    for key in TEXT_INPUT_KEYS:
        if key in inputs:
            return key
    return None


class WorkflowMapper:
    def __init__(self, workflow: dict, overrides: dict | None = None):
        if not is_api_format(workflow):
            raise WorkflowError(
                "Workflow không đúng định dạng API. Trong ComfyUI, hãy dùng "
                "'Save (API Format)' / 'Export (API)' thay vì lưu workflow giao diện."
            )
        self.workflow = workflow
        self.overrides = {str(k): str(v) for k, v in (overrides or {}).items() if v}
        self.roles: dict[str, str] = {}
        self.notes: list[str] = []
        self._detect()

    # ------------------------------------------------------------- detection
    def _find_save_node(self) -> str | None:
        saves = [nid for nid, node in self.workflow.items()
                 if node.get("class_type") in SAVE_TYPES]
        for save_id in saves:
            for ancestor in _ancestors(self.workflow, save_id):
                if self.workflow[ancestor].get("class_type") in SAMPLER_TYPES:
                    return save_id
        return saves[0] if saves else None

    def _find_sampler(self, save_id: str | None) -> str | None:
        samplers = [nid for nid, node in self.workflow.items()
                    if node.get("class_type") in SAMPLER_TYPES]
        if not samplers:
            return None
        if save_id:
            for ancestor in _ancestors(self.workflow, save_id):
                if ancestor in samplers and "positive" in (self.workflow[ancestor].get("inputs") or {}):
                    return ancestor
        for sampler in samplers:
            inputs = self.workflow[sampler].get("inputs") or {}
            if "positive" in inputs and "negative" in inputs:
                return sampler
        return samplers[0]

    def _detect(self) -> None:
        save_id = self.overrides.get("save") or self._find_save_node()
        if save_id and save_id not in self.workflow:
            raise WorkflowError(f"Node 'save' được chỉ định không tồn tại: {save_id}")
        sampler_id = self.overrides.get("sampler") or self._find_sampler(save_id)
        if sampler_id and sampler_id not in self.workflow:
            raise WorkflowError(f"Node 'sampler' được chỉ định không tồn tại: {sampler_id}")
        if not sampler_id:
            types = sorted({n.get("class_type", "?") for n in self.workflow.values()})
            raise WorkflowError(
                "Không tìm thấy node sampler (KSampler/KSamplerAdvanced). "
                f"Các node hiện có: {', '.join(types[:15])}"
            )
        sampler = self.workflow[sampler_id]
        sampler_inputs = sampler.get("inputs") or {}

        if save_id:
            self.roles["save"] = save_id
        self.roles["sampler"] = sampler_id

        # Positive / negative prompt nodes
        for role in ("positive", "negative"):
            override = self.overrides.get(role)
            if override:
                if override not in self.workflow:
                    raise WorkflowError(f"Node '{role}' được chỉ định không tồn tại: {override}")
                self.roles[role] = override
                continue
            ref = _ref_id(sampler_inputs.get(role))
            candidates = [ref] if ref else []
            if not candidates:
                for ancestor in _ancestors(self.workflow, sampler_id):
                    node = self.workflow[ancestor]
                    if _text_input_name(node):
                        candidates.append(ancestor)
            for candidate in candidates:
                node = self.workflow.get(candidate, {})
                if _text_input_name(node):
                    self.roles[role] = candidate
                    break
            if role not in self.roles:
                self.notes.append(
                    f"Không tìm thấy node {ROLE_LABELS[role].lower()} có input text."
                )

        # Latent / size node
        latent_override = self.overrides.get("latent")
        if latent_override:
            if latent_override not in self.workflow:
                raise WorkflowError(f"Node 'latent' được chỉ định không tồn tại: {latent_override}")
            self.roles["latent"] = latent_override
        else:
            latent_ref = _ref_id(sampler_inputs.get("latent_image"))
            search = [latent_ref] if latent_ref else []
            search += [a for a in _ancestors(self.workflow, sampler_id) if a not in search]
            for candidate in search:
                inputs = (self.workflow.get(candidate) or {}).get("inputs") or {}
                if "width" in inputs and "height" in inputs:
                    self.roles["latent"] = candidate
                    break
            if "latent" not in self.roles:
                self.notes.append("Không tìm thấy node kích thước (width/height); giữ nguyên kích thước workflow.")

        # Checkpoint
        checkpoint_override = self.overrides.get("checkpoint")
        if checkpoint_override:
            if checkpoint_override not in self.workflow:
                raise WorkflowError(f"Node 'checkpoint' được chỉ định không tồn tại: {checkpoint_override}")
            self.roles["checkpoint"] = checkpoint_override
        else:
            for nid, node in self.workflow.items():
                if node.get("class_type") in CHECKPOINT_TYPES:
                    self.roles["checkpoint"] = nid
                    break

        if "save" not in self.roles:
            previews = [nid for nid, node in self.workflow.items()
                        if node.get("class_type") == "PreviewImage"]
            if previews:
                self.roles["save"] = previews[0]
                self.notes.append("Workflow dùng PreviewImage; ảnh sẽ được tải qua API /view.")

        if "save" not in self.roles:
            self.notes.append("Không tìm thấy node lưu ảnh (SaveImage).")

    # --------------------------------------------------------------- applying
    def summary(self) -> dict:
        roles = {}
        for role, node_id in self.roles.items():
            node = self.workflow.get(node_id, {})
            roles[role] = {
                "node_id": node_id,
                "class_type": node.get("class_type"),
                "label": ROLE_LABELS.get(role, role),
            }
        models = []
        for node in self.workflow.values():
            inputs = node.get("inputs") or {}
            for key in ("ckpt_name", "unet_name", "vae_name", "lora_name"):
                if isinstance(inputs.get(key), str):
                    models.append({"node": node.get("class_type"), "field": key, "value": inputs[key]})
        return {
            "ok": "positive" in self.roles and "sampler" in self.roles,
            "node_count": len(self.workflow),
            "roles": roles,
            "models": models,
            "notes": self.notes,
            "missing": [r for r in ("positive", "sampler", "save") if r not in self.roles],
        }

    def apply(self, params: dict) -> tuple[dict, dict]:
        """Return a mutated copy of the workflow plus details of what changed."""
        workflow = copy.deepcopy(self.workflow)
        applied: dict = {}

        def node(role: str) -> dict | None:
            node_id = self.roles.get(role)
            return workflow.get(node_id) if node_id else None

        positive = node("positive")
        if positive is not None and params.get("prompt") is not None:
            if _set_text(positive, str(params["prompt"])):
                applied["positive"] = self.roles["positive"]
        negative = node("negative")
        if negative is not None and params.get("negative_prompt") is not None:
            if _set_text(negative, str(params["negative_prompt"])):
                applied["negative"] = self.roles["negative"]

        latent = node("latent")
        if latent is not None:
            inputs = latent.setdefault("inputs", {})
            if "width" in inputs and params.get("width"):
                inputs["width"] = self._align(int(params["width"]))
                applied["width"] = inputs["width"]
            if "height" in inputs and params.get("height"):
                inputs["height"] = self._align(int(params["height"]))
                applied["height"] = inputs["height"]
            if "batch_size" in inputs and params.get("batch_size"):
                inputs["batch_size"] = max(1, min(8, int(params["batch_size"])))
                applied["batch_size"] = inputs["batch_size"]

        sampler = node("sampler")
        if sampler is not None:
            inputs = sampler.setdefault("inputs", {})
            seed = params.get("seed")
            if seed is None or int(seed) < 0:
                seed = random.randint(1, 2**31 - 1)
            for key in ("seed", "noise_seed"):
                if key in inputs:
                    inputs[key] = int(seed)
                    break
            applied["seed"] = int(seed)
            if params.get("steps") and "steps" in inputs:
                inputs["steps"] = max(1, min(150, int(params["steps"])))
                applied["steps"] = inputs["steps"]
            if params.get("cfg") is not None and "cfg" in inputs:
                inputs["cfg"] = float(params["cfg"])
                applied["cfg"] = inputs["cfg"]
            if params.get("denoise") is not None and "denoise" in inputs:
                inputs["denoise"] = max(0.0, min(1.0, float(params["denoise"])))
            if params.get("sampler") and "sampler_name" in inputs:
                inputs["sampler_name"] = str(params["sampler"])
                applied["sampler_name"] = inputs["sampler_name"]
            if params.get("scheduler") and "scheduler" in inputs:
                inputs["scheduler"] = str(params["scheduler"])
                applied["scheduler"] = inputs["scheduler"]

        save = node("save")
        if save is not None:
            inputs = save.setdefault("inputs", {})
            if params.get("filename_prefix") and "filename_prefix" in inputs:
                prefix = re.sub(r"[^A-Za-z0-9_\-]", "_", str(params["filename_prefix"]))
                inputs["filename_prefix"] = prefix
                applied["filename_prefix"] = prefix
            if params.get("save_node_output") is not None:
                inputs["images"] = params["save_node_output"]

        checkpoint = node("checkpoint")
        if checkpoint is not None and params.get("checkpoint"):
            inputs = checkpoint.setdefault("inputs", {})
            if "ckpt_name" in inputs:
                inputs["ckpt_name"] = str(params["checkpoint"])
                applied["checkpoint"] = inputs["ckpt_name"]

        return workflow, applied

    @staticmethod
    def _align(value: int) -> int:
        return max(256, min(2048, int(round(value / 8.0) * 8)))

    def image_output_node(self) -> str | None:
        return self.roles.get("save")
