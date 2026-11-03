"""Entry point: python app.py (or uvicorn modules.api:app)."""

from __future__ import annotations

import os

import uvicorn

from modules.api import app  # noqa: F401  (re-exported for uvicorn)

if __name__ == "__main__":
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "8765"))
    print(f"AI Viral News & Facebook Content Studio → http://{host}:{port}")
    uvicorn.run("modules.api:app", host=host, port=port, log_level="info", reload=False)
