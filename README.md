# AI Viral News & Facebook Content Studio

Local AI content studio for Vietnamese Facebook posts: it discovers trending news, researches and
fact-checks each topic with multiple sources, writes original Vietnamese posts with a local
**Ollama Qwen3 8B**, draws editorial illustrations with your **existing ComfyUI** install, and keeps
everything in SQLite behind a bilingual (VI/EN) dashboard.

**The app never publishes to Facebook.** Every post is a draft you review, edit, approve and export
manually.

---

## 1. Requirements

| Component | Verified on this machine |
| --- | --- |
| OS | Windows 11 64-bit |
| Python | 3.12 (`py -3.12`), project venv in `.venv` |
| LLM runtime | Ollama on `http://127.0.0.1:11434`, model `qwen3:8b` |
| Image runtime | Existing ComfyUI Portable on `http://127.0.0.1:8188` (v0.34.0) |
| GPU | NVIDIA RTX 3060 12 GB |

The project reuses the existing ComfyUI installation at
`D:\AI\image\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable`.
No second ComfyUI is installed, and no models or workflows are modified.

## 2. Quick start

```bat
run.bat
```

`run.bat` creates `.venv` with Python 3.12 on first run, installs `requirements.txt`, warns when
Ollama or ComfyUI is not answering, then serves the studio at **http://127.0.0.1:8765**.

Manual start:

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

Environment overrides (optional, copy `.env.example` to `.env`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8765` | Dashboard bind address |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `qwen3:8b` | Writer/researcher model |
| `COMFYUI_BASE_URL` | `http://127.0.0.1:8188` | ComfyUI endpoint |
| `SERPER_API_KEY` / `BRAVE_SEARCH_API_KEY` | – | Extra news search providers (optional) |
| `FACEBOOK_ACCESS_TOKEN` | – | Enables the optional public-page Graph API source |

Everything else (style, lengths, workflow, node map, image size, queue, auto-discovery interval…)
lives in `data/settings.json` and is editable from the **Cài đặt / Settings** view.

## 3. Workflow in the dashboard

1. **Xu hướng / Trends → Khám phá xu hướng** – collects RSS + Google News + Google Trends, clusters
   duplicate stories and scores each topic 0–100.
2. Pick a topic → **Nghiên cứu** (research + fact check) → **Viết bài** (Vietnamese draft) or
   **Chạy quy trình** (research → write → image in one queued job).
3. **Bài viết / Posts** – edit headline, body, short/long versions, hashtags, hook, image prompt;
   inspect research evidence, fact-check verdicts, sources and AI warning flags; generate or
   regenerate images and select the variation; approve or reject.
4. **Xuất bản** – export approved posts to Markdown, TXT, JSON or CSV (`output/exports/`).
   Copy/paste to Facebook yourself; nothing is posted automatically.
5. **Hàng đợi / Jobs** – persistent queue with progress, retries and cancellation.

## 4. Architecture

```
app.py                     entrypoint (uvicorn, 127.0.0.1:8765)
modules/api.py             FastAPI routes + lifespan (queue worker, scheduler)
modules/job_queue.py       persistent SQLite job queue, cancel, crash recovery, auto-discovery
modules/pipeline.py        research → write → image stages
modules/resource_manager.py GPU arbitration between Ollama and ComfyUI

modules/trend_finder.py    discovery orchestration, clustering, editorial scoring, dedupe
modules/rss_collector.py   RSS feeds + Google Trends trending searches
modules/search_provider.py Google News RSS / Serper / Brave search providers
modules/facebook_public_source.py optional Graph API public-page source (off by default)
modules/trend_scorer.py    transparent 0–100 scoring with metric provenance
modules/source_manager.py  HTTP fetching, robots.txt, extraction (trafilatura), domain reliability

modules/news_researcher.py source collection + grounded AI research report
modules/fact_checker.py    deterministic verification of the AI report
modules/ai_writer.py       Vietnamese Facebook writer, variants, audits
modules/ollama_client.py   health, model discovery, JSON/streaming generation

modules/workflow_mapper.py ComfyUI API-workflow role detection and parameter injection
modules/comfyui_client.py  submit, websocket progress, retrieval, PNG integrity validation
modules/exporter.py        Markdown / TXT / JSON / CSV export with disclosure
modules/database.py        SQLite schema and repositories
modules/config.py          defaults, `data/settings.json`, secrets from env
```

Data lives in `data/` (SQLite `studio.db`, settings, logs), generated images in `output/images/`,
exports in `output/exports/`.

### Trend scoring without invented numbers

Every score component is labelled:

* **verified** – measured (publication dates, independent domains, Google Trends traffic, Graph API
  engagement counts).
* **estimated** – derived from measurable data (source reliability).
* **editorial** – AI/heuristic judgement (audience fit, uniqueness), shown as such.

Missing signals are dropped and weights renormalised. The score panel explains each metric and lists
caveats such as *“không có số liệu tương tác công khai đã xác thực”*.

### Research and fact checking

The AI writes a structured report (summary, key facts, timeline, entities, confirmed/unconfirmed/
conflicting claims, quotes, missing information, sources). `FactChecker` then verifies it:

* source ids must exist; numbers must literally occur in the cited article;
* quotes are kept only when found verbatim in a cited source;
* corroboration counts **independent domains**, not links;
* confidence is capped when only one domain is available, and the UI labels the report accordingly.

The writer receives only the verified report and never the raw article text to copy.

### ComfyUI integration

* Import any workflow saved with **ComfyUI → Save (API Format)**.
* The mapper derives node roles (positive/negative prompt, latent, sampler, save, checkpoint) by
  graph traversal – no hardcoded node ids. Unknown graphs can be overridden per role in settings.
* `POST /api/comfyui/validate` reports detected roles, models and missing pieces before generating.
* Generation streams live step progress over the ComfyUI websocket, retries uploads, verifies PNG
  integrity, and saves unique filenames under `output/images/post_<id>/`.
* The shipped `config/workflows/default_sdxl_text2img.json` uses SDXL/RealVisXL with the `sdxl_vae`.

### GPU arbitration

`ResourceManager` serialises GPU work by default: Ollama generation runs first, the model is
unloaded (`keep_alive: 0`) before ComfyUI takes the GPU, then image work proceeds. Queue workers can
still handle CPU/network stages in parallel. `/free` is **never** called automatically – it wedged
ComfyUI's event loop during testing (see Troubleshooting).

## 5. API (selected)

```
GET  /api/health                 app, Ollama, ComfyUI, GPU status
GET  /api/stats                  dashboard counters
GET  /api/trends                 topics with scores, metrics, posts
POST /api/trends/discover        start discovery job
POST /api/topics/{id}/research   research job
POST /api/topics/{id}/write      write job (options: style, tone, audience, length…)
POST /api/topics/{id}/pipeline   full research → write → image job
POST /api/batch                  top-N topics in one batch
GET  /api/posts/{id}             post + research + images + flags
PATCH/api/posts/{id}             edit fields
POST /api/posts/{id}/approve|reject|image|select-image/{image_id}
GET  /api/jobs/{id}              status, stage, progress, result
GET  /api/export?fmt=md|txt|json|csv&status=approved
```

Interactive docs: http://127.0.0.1:8765/docs

## 6. Tests

```bat
.venv\Scripts\python.exe -m pytest -q
```

`tests/` covers the deterministic logic: fact checking (invented numbers/quotes, single-source caps,
confidence), trend scoring provenance, workflow role detection and parameter injection, writer
guards (hashtags, markdown, English image prompts, number auditing, copy detection), the SQLite
repositories, exports and settings persistence. 35 tests, all passing.

## 7. Troubleshooting

| Symptom | Fix |
| --- | --- |
| Dashboard shows Ollama offline | Start `ollama serve`, check `ollama list` contains `qwen3:8b` |
| ComfyUI offline | Launch `run_nvidia_gpu.bat` in the ComfyUI Portable folder |
| ComfyUI stops responding after a job | Do not enable `resource.free_comfy_before_llm`; `/free` can wedge ComfyUI 0.34's event loop. Restart ComfyUI only. |
| Workflow rejected | Export with **Save (API Format)**, not the UI workflow |
| Generated image unrelated to the story | Keep `image_prompt` English (the writer enforces this); the model may rephrase the prompt |
| Research confidence stuck at 47 % | Only one or two domains carried content; add sources or open the article links |
| Vietnamese text garbled in a console | Set `PYTHONIOENCODING=utf-8` |

## 8. Limits and safety

* Discovery uses public RSS, Google News/Trends feeds and optional official APIs only – no login
  bypass, no private profiles or groups, robots.txt respected, no invented engagement figures.
* AI output is a draft: the UI surfaces unverified numbers, low corroboration and low confidence
  before you approve anything.
* Images are editorial illustrations with an explicit AI disclosure in exports; no photorealistic
  depictions of real, identifiable people.
* There is no Facebook publishing code path.
