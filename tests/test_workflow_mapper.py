"""Workflow mapping must work on the real ComfyUI API workflow without hardcoded ids."""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.workflow_mapper import WorkflowError, WorkflowMapper, is_api_format, load_workflow

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKFLOW = PROJECT_ROOT / "config" / "workflows" / "default_sdxl_text2img.json"


def _mapper() -> WorkflowMapper:
    return WorkflowMapper(load_workflow(DEFAULT_WORKFLOW))


def test_default_workflow_is_api_format_and_detects_roles() -> None:
    workflow = load_workflow(DEFAULT_WORKFLOW)
    assert is_api_format(workflow)

    summary = _mapper().summary()
    assert summary["ok"] is True
    assert summary["missing"] == []
    for role in ("positive", "negative", "sampler", "save"):
        assert role in summary["roles"]
        assert summary["roles"][role]["node_id"] in workflow
    assert summary["models"], "workflow should expose at least one model field"


def test_apply_writes_prompt_seed_and_size() -> None:
    mapper = _mapper()
    workflow, applied = mapper.apply(
        {
            "prompt": "editorial illustration of a chip fabrication cleanroom",
            "negative_prompt": "text, watermark, photorealistic news photo",
            "seed": 4242,
            "width": 832,
            "height": 1216,
            "steps": 24,
            "cfg": 5.5,
        }
    )

    assert applied["seed"] == 4242
    pos_text = str(workflow[mapper.roles["positive"]])
    assert "cleanroom" in pos_text
    assert "watermark" in str(workflow[mapper.roles["negative"]])
    assert workflow[mapper.roles["sampler"]]["inputs"]["seed"] == 4242
    assert workflow[mapper.roles["latent"]]["inputs"]["width"] == 832
    assert workflow[mapper.roles["latent"]]["inputs"]["height"] == 1216


def test_apply_does_not_mutate_the_source_workflow() -> None:
    mapper = _mapper()
    original = str(load_workflow(DEFAULT_WORKFLOW))
    mapper.apply({"prompt": "something else", "seed": 7})
    assert str(mapper.workflow) == original


def test_ui_format_workflow_is_rejected_with_a_clear_message() -> None:
    with pytest.raises(WorkflowError) as excinfo:
        WorkflowMapper({"nodes": [], "links": [], "version": 0.4})
    assert "API" in str(excinfo.value)


def test_unknown_node_override_is_reported() -> None:
    with pytest.raises(WorkflowError):
        WorkflowMapper(load_workflow(DEFAULT_WORKFLOW), {"sampler": "does-not-exist"})
