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

Everything else (style, lengths, workflow, node map, image size, queue, auto-discovery interval,
`quality.vision_model`…) lives in `data/settings.json` and is editable from the **Cài đặt / Settings**
view.

## 2b. Phase 2 · viral intelligence, versions, quality, analytics

**Viral intelligence.** Every discovery run stores a score snapshot per topic (`topic_history`), so
each topic gets a lifecycle phase — *emerging, rising, peak, declining, steady* — plus the delta since
the previous run and a sparkline. Spread labels are honest: `viral_candidate` only when a measured
signal (Google Trends traffic or verified Graph API engagement) accompanies multiple independent
domains; otherwise the dashboard says *wide coverage*, *reported*, or *insufficient signals* and
lists what is missing. Ordinary news is never presented as verified viral content.

**Three content versions.** For any researched topic, *So sánh phiên bản* generates three Vietnamese
versions from the same research report — **A** professional news, **B** engaging conversation,
**C** short and punchy — shows them side by side with word counts, style, content score and AI
warnings, and lets you apply one to the normal editing/approval flow. All three are fact-audited by
the same writer pipeline; image prompts are generated once and shared.

**Image variations.** Generate 1–3 images per run (batch or sequential), select the one to publish,
regenerate with a new prompt/seed, and keep at most 9 images per post. The API and pipeline enforce
these caps before any GPU work, so a stray click cannot queue a large batch.

**Quality dashboard.** Each post shows an overall score with five labelled components:

| Component | How it is computed |
| --- | --- |
| Source reliability | Average stored reliability of research sources (measured) |
| Fact checking | Research confidence minus unsupported claims, conflicts and AI warnings (measured) |
| Content | Length, structure, hook, closing question, hashtags, source block, copy/number flags (rule-based) |
| Image relevance | Vision-model assessment of the selected image (AI assessed) or a clearly labelled prompt heuristic |
| Freshness | Exponential decay of the newest source date (measured) |

Unsupported claims, warnings and missing data are listed explicitly, and the verdict is
*ready / needs review / needs work*.

**Trend analytics.** The *Phân tích* view shows the funnel (discovered → researched → drafted →
approved → rejected), a multi-series timeline, approval rates per category, research confidence
distribution and recurring topics with their peak score and score change.

**Manual approval stays mandatory.** No version, image or analytics action publishes anything.

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
5. **Phân tích / Analytics** – funnel, timeline, category performance and recurring topics.
6. **Hàng đợi / Jobs** – persistent queue with progress, retries and cancellation.

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
modules/viral.py           lifecycle phase + verified spread labels per topic
modules/quality.py         content quality report and vision-based image relevance
modules/analytics.py       funnel, timeline, category and recurring-topic analytics
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
exports in `output/exports/`. Phase 2 adds two tables without touching existing ones:
`topic_history` (score snapshots) and `post_versions` (A/B/C drafts).

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
GET  /api/posts/{id}/quality      five-component quality report
POST /api/posts/{id}/assess-image vision-model image relevance (queued)
POST /api/topics/{id}/versions    generate A/B/C versions (queued)
GET  /api/topics/{id}/versions    stored versions for comparison
POST /api/versions/{id}/apply     create an editable post from a version
GET  /api/trends/{id}/history     score snapshots for the trend chart
GET  /api/analytics?days=14       funnel, series, categories, recurring topics
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
repositories, exports and settings persistence, viral phases and labels, quality components,
analytics aggregation, A/B/C version generation and application, and the image GPU caps.
72 tests, all passing.

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
| Image assessment fails with `unknown model architecture: 'mllama'` | This Ollama build cannot load `llama3.2-vision`. Set `quality.vision_model` to an installed VL model (default `qwen2.5vl:7b`); the app tries candidates in order. |
| Vision assessment is slow | The vision model loads into the same 12 GB of VRAM; it runs as a queued job after image work, never at the same time as ComfyUI. |

## 8. Limits and safety

* Discovery uses public RSS, Google News/Trends feeds and optional official APIs only – no login
  bypass, no private profiles or groups, robots.txt respected, no invented engagement figures.
* AI output is a draft: the UI surfaces unverified numbers, low corroboration and low confidence
  before you approve anything.
* Images are editorial illustrations with an explicit AI disclosure in exports; no photorealistic
  depictions of real, identifiable people.
* There is no Facebook publishing code path.
