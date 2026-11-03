"""Shared exceptions for pipeline stages."""

from __future__ import annotations


class StudioError(RuntimeError):
    """Base error for user-facing failures."""


class CancelledError(StudioError):
    """Raised when the user cancels a running job."""


class StageError(StudioError):
    """Raised when a pipeline stage cannot continue with the available inputs."""
