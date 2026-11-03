/* AI Viral News & Facebook Content Studio — dashboard logic (part 1) */
'use strict';

const I18N = window.STUDIO_I18N;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const CATEGORIES = [
  'general', 'vietnam', 'international', 'ai', 'technology', 'business', 'finance',
  'entertainment', 'celebrity', 'lifestyle', 'science', 'education', 'social',
  'discovery', 'human_interest', 'internet_culture'
];

const state = {
  lang: localStorage.getItem('studio.lang') || 'vi',
  view: 'dashboard',
  health: null,
  system: null,
  settings: null,
  secrets: null,
  options: null,
  workflows: [],
  stats: null,
  trends: [],
  posts: [],
  postDetail: null,
  currentPostId: null,
  selectedTopics: new Set(),
  editorTab: 'main',
  filters: { category: '', status: '', minScore: 0 },
  postFilter: { status: '', search: '' },
  jobs: [],
  logs: [],
  jobsPolling: false,
  focusTopic: null,
  savingSettings: false
};

/* --------------------------------------------------------------- utilities */
function t(key, fallback) {
  const dict = I18N[state.lang] || I18N.vi;
  if (dict[key] !== undefined) return dict[key];
  if (I18N.vi[key] !== undefined) return I18N.vi[key];
  return fallback !== undefined ? fallback : key;
}

function fill(template, vars) {
  return String(template).replace(/\{(\w+)\}/g, (m, key) =>
    vars[key] !== undefined && vars[key] !== null ? vars[key] : m);
}

function esc(value) {
  return String(value === undefined || value === null ? '' : value).replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

function getPath(obj, path) {
  return path.split('.').reduce((node, key) =>
    (node && typeof node === 'object' ? node[key] : undefined), obj);
}

function setPath(obj, path, value) {
  const parts = path.split('.');
  let node = obj;
  parts.slice(0, -1).forEach(part => {
    if (!node[part] || typeof node[part] !== 'object') node[part] = {};
    node = node[part];
  });
  node[parts[parts.length - 1]] = value;
}

function fmtNum(value) {
  if (value === null || value === undefined || value === '') return '—';
  return Number(value).toLocaleString(state.lang === 'vi' ? 'vi-VN' : 'en-US');
}

function fmtDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  try {
    return new Intl.DateTimeFormat(state.lang === 'vi' ? 'vi-VN' : 'en-US', {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
    }).format(date);
  } catch (err) {
    return date.toLocaleString();
  }
}

function timeAgo(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const diff = Math.max(0, Date.now() - date.getTime());
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'vừa xong';
  if (minutes < 60) return `${minutes}′`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  const opts = Object.assign({}, options, { headers });
  if (opts.body && typeof opts.body !== 'string') {
    headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  let response;
  try {
    response = await fetch(path, opts);
  } catch (err) {
    throw new Error(`${t('common.error')}: ${err.message}`);
  }
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const data = await response.json();
      message = data.detail || data.error || message;
    } catch (err) { /* non JSON error */ }
    throw new Error(message);
  }
  const type = response.headers.get('content-type') || '';
  if (type.includes('application/json')) return response.json();
  return response.text();
}

function toast(message, type = 'info', ms = 5200) {
  const host = $('#toastHost');
  if (!host) return;
  const node = document.createElement('div');
  node.className = `toast ${type}`;
  node.textContent = message;
  host.appendChild(node);
  window.setTimeout(() => node.remove(), ms);
}

/* -------------------------------------------------------------------- i18n */
function applyStaticI18n() {
  document.documentElement.lang = state.lang;
  $$('[data-i18n]').forEach(node => {
    const key = node.getAttribute('data-i18n');
    if (key) node.textContent = t(key);
  });
  $$('[data-i18n-placeholder]').forEach(node => {
    const key = node.getAttribute('data-i18n-placeholder');
    if (key) node.setAttribute('placeholder', t(key));
  });
  $$('.lang-btn').forEach(btn => btn.classList.toggle('is-active', btn.dataset.lang === state.lang));
}

function setLang(lang) {
  if (!I18N[lang] || lang === state.lang) return;
  state.lang = lang;
  localStorage.setItem('studio.lang', lang);
  applyStaticI18n();
  renderTopActions();
  setView(state.view, { skipLoad: true });
  refreshCurrentView();
}

/* ------------------------------------------------------------------ modals */
function closeModal() {
  const root = $('#modalRoot');
  if (root) root.innerHTML = '';
}

function openModal(config) {
  const root = $('#modalRoot');
  if (!root) return null;
  const buttons = (config.actions || []).map((action, index) =>
    `<button class="btn ${action.kind || 'ghost'}" data-modal-index="${index}">${esc(action.label)}</button>`
  ).join('');
  root.innerHTML = `
    <div class="modal-backdrop">
      <div class="modal" role="dialog" aria-modal="true">
        <h3>${esc(config.title || '')}</h3>
        ${config.description ? `<p>${esc(config.description)}</p>` : ''}
        <div class="modal-body">${config.body || ''}</div>
        <div class="modal-actions">${buttons}</div>
      </div>
    </div>`;
  const backdrop = root.firstElementChild;
  backdrop.addEventListener('click', event => {
    if (event.target === backdrop) closeModal();
  });
  (config.actions || []).forEach((action, index) => {
    const btn = root.querySelector(`[data-modal-index="${index}"]`);
    if (!btn) return;
    btn.addEventListener('click', async () => {
      try {
        const result = await action.onClick(root);
        if (result !== false) closeModal();
      } catch (err) {
        toast(err.message || t('common.error'), 'error');
      }
    });
  });
  if (typeof config.onMount === 'function') config.onMount(root);
  return root;
}

function confirmDialog(title, message, confirmLabel) {
  return new Promise(resolve => {
    let settled = false;
    openModal({
      title,
      body: `<p>${esc(message)}</p>`,
      actions: [
        { label: t('common.cancel'), kind: 'ghost', onClick: () => { settled = true; resolve(false); } },
        {
          label: confirmLabel || t('common.delete'), kind: 'danger',
          onClick: () => { settled = true; resolve(true); }
        }
      ]
    });
    const observer = new MutationObserver(() => {
      if (!$('#modalRoot').firstElementChild && !settled) {
        settled = true;
        observer.disconnect();
        resolve(false);
      }
    });
    observer.observe($('#modalRoot'), { childList: true });
  });
}

/* ---------------------------------------------------------------- job utils */
async function waitForJob(jobId) {
  const started = Date.now();
  while (Date.now() - started < 20 * 60 * 1000) {
    const job = await api(`/api/jobs/${jobId}`);
    if (['done', 'failed', 'cancelled'].includes(job.status)) return job;
    await sleep(1600);
  }
  throw new Error(t('jobs.timeout'));
}

function summarizeJob(job) {
  const result = job.result || {};
  switch (job.kind) {
    case 'discover':
      return fill(t('toast.discovered'), {
        n: result.topics_saved || 0, created: result.created || 0, merged: result.merged || 0
      });
    case 'batch':
      return fill(t('toast.batchDone'), { n: result.completed || 0 });
    case 'image':
      return fill(t('toast.imageDone'), { n: (result.images || []).length });
    case 'full':
      return result.headline || (job.message || '');
    case 'write':
      return result.headline || (job.message || '');
    default:
      return job.message || 'OK';
  }
}

async function awaitJob(jobId, options = {}) {
  toast(fill(t('toast.jobStarted'), { id: jobId }));
  const job = await waitForJob(jobId);
  if (job.status === 'done') {
    toast(summarizeJob(job), 'success', 7000);
  } else if (job.status === 'failed') {
    toast(`${t('toast.jobFailed')}: ${job.error || ''}`, 'error', 10000);
  } else if (job.status === 'cancelled') {
    toast(t('jobStatus.cancelled'), 'info');
  }
  if (options.refresh !== false) await refreshCurrentView();
  refreshJobBadge();
  return job;
}

async function refreshJobBadge() {
  try {
    const data = await api('/api/jobs?limit=40');
    const active = (data.items || []).filter(job => ['queued', 'running'].includes(job.status));
    const badge = $('#jobsBadge');
    if (badge) {
      badge.textContent = String(active.length);
      badge.hidden = active.length === 0;
    }
    if (state.view === 'jobs') {
      state.jobs = data.items || [];
      renderJobs();
    }
  } catch (err) { /* badge failures are silent */ }
}

/* ------------------------------------------------------------------ health */
async function refreshHealth() {
  try {
    state.health = await api('/api/health');
    renderHealthChips();
    if (state.view === 'dashboard') renderServiceCards();
  } catch (err) {
    const dot = $('#dotOllama');
    if (dot) dot.className = 'dot bad';
  }
}

function renderHealthChips() {
  const health = state.health || {};
  const ollama = health.ollama || {};
  const comfy = health.comfyui || {};
  const gpu = health.gpu || (comfy.gpu ? {
    free_mb: comfy.gpu.vram_free_mb, total_mb: comfy.gpu.vram_total_mb
  } : null);

  const ollamaDot = $('#dotOllama');
  const ollamaText = $('#txtOllama');
  if (ollamaDot) {
    ollamaDot.className = 'dot ' + (!ollama.available ? 'bad' : (ollama.model_available ? 'ok' : 'warn'));
    ollamaText.textContent = !ollama.available ? t('service.offline')
      : (ollama.model_available ? ollama.selected_model : t('service.noModel'));
  }
  const comfyDot = $('#dotComfy');
  const comfyText = $('#txtComfy');
  if (comfyDot) {
    comfyDot.className = 'dot ' + (comfy.available ? 'ok' : 'bad');
    comfyText.textContent = comfy.available ? `v${comfy.version || '?'}` : t('service.offline');
  }
  const gpuDot = $('#dotGpu');
  const gpuText = $('#txtGpu');
  if (gpuDot) {
    gpuDot.className = 'dot ' + (gpu ? 'ok' : 'warn');
    gpuText.textContent = gpu ? `${fmtNum(gpu.free_mb)} MB` : '—';
  }
}

/* -------------------------------------------------------------------- nav */
function renderTopActions() {
  const host = $('#topActions');
  if (!host) return;
  const actions = {
    dashboard: [
      { action: 'discover', label: t('trends.discover'), kind: 'primary' },
      { action: 'batch', label: t('trends.batch'), kind: 'ghost' }
    ],
    trends: [
      { action: 'discover', label: t('trends.discover'), kind: 'primary' },
      { action: 'batch', label: t('trends.batch'), kind: 'ghost' }
    ],
    posts: [
      { action: 'export-approved', label: t('export.approved'), kind: 'primary' }
    ],
    jobs: [
      { action: 'refresh-jobs', label: t('common.refresh'), kind: 'ghost' }
    ],
    settings: [
      { action: 'save-settings', label: t('common.save'), kind: 'primary' }
    ]
  }[state.view] || [];
  host.innerHTML = actions.map(item =>
    `<button class="btn ${item.kind}" data-action="${item.action}">${esc(item.label)}</button>`
  ).join('');
}

function setView(view, options = {}) {
  state.view = view;
  $$('.nav-item').forEach(btn => btn.classList.toggle('is-active', btn.dataset.view === view));
  $$('.view').forEach(section => section.classList.toggle('is-active', section.id === `view-${view}`));
  const title = $('#pageTitle');
  const subtitle = $('#pageSubtitle');
  if (title) title.textContent = t(`page.${view}.title`);
  if (subtitle) subtitle.textContent = t(`page.${view}.subtitle`);
  renderTopActions();
  if (!options.skipLoad) refreshCurrentView();
}

async function refreshCurrentView() {
  try {
    if (state.view === 'dashboard') await loadDashboard();
    else if (state.view === 'trends') await loadTrends();
    else if (state.view === 'posts') await loadPosts();
    else if (state.view === 'jobs') await loadJobs();
    else if (state.view === 'settings') await loadSettings();
  } catch (err) {
    toast(err.message || t('toast.loadFailed'), 'error');
  }
}

/* --------------------------------------------------------------- dashboard */
async function loadDashboard() {
  const [stats, trends] = await Promise.all([
    api('/api/stats'),
    api('/api/trends?limit=6')
  ]);
  state.stats = stats;
  renderStatCards();
  renderServiceCards();
  renderRecentJobs();
  renderDashboardTrends(trends.items || []);
}

function renderStatCards() {
  const counts = (state.stats && state.stats.counts) || {};
  const cards = [
    {
      label: t('stats.topics'), value: fmtNum(counts.topics || 0),
      foot: fill(t('stats.topicsFoot'), { n: counts.topics_new || 0 })
    },
    { label: t('stats.drafts'), value: fmtNum(counts.posts_draft || 0), foot: t('stats.draftsFoot') },
    { label: t('stats.approved'), value: fmtNum(counts.posts_approved || 0), foot: t('stats.approvedFoot') },
    { label: t('stats.images'), value: fmtNum(counts.images || 0), foot: t('stats.imagesFoot') },
    { label: t('stats.sources'), value: fmtNum(counts.sources || 0), foot: t('stats.sourcesFoot') }
  ];
  $('#statGrid').innerHTML = cards.map(card => `
    <div class="stat-card">
      <span class="stat-label">${esc(card.label)}</span>
      <span class="stat-value">${esc(card.value)}</span>
      <span class="stat-foot">${esc(card.foot)}</span>
    </div>`).join('');
}

function renderServiceCards() {
  const host = $('#serviceCards');
  if (!host) return;
  const health = state.health || {};
  const ollama = health.ollama || {};
  const comfy = health.comfyui || {};
  const gpu = health.gpu || (comfy.gpu ? {
    name: comfy.gpu.name, free_mb: comfy.gpu.vram_free_mb,
    total_mb: comfy.gpu.vram_total_mb, utilization: null
  } : null);
  const facebook = (state.system && state.system.facebook) || {};

  const ollamaMeta = !ollama.available
    ? `<span class="mono">${esc(ollama.error || t('service.offline'))}</span>`
    : `${esc(ollama.selected_model || '')}${ollama.model_available ? '' : ` · ${esc(t('service.noModel'))}`}
       <br><span class="muted">${fill(t('service.models'), { n: (ollama.models || []).length })}
       ${ollama.version ? ` · v${esc(ollama.version)}` : ''}</span>`;
  const comfyMeta = comfy.available
    ? `${fill(t('service.version'), { v: comfy.version || '?' })}<br>${fill(t('service.queue'), {
        running: comfy.queue_running || 0, pending: comfy.queue_pending || 0
      })}`
    : esc(comfy.error || t('service.offline'));
  const gpuMeta = gpu
    ? `${esc(gpu.name || 'GPU')}<br>${fill(t('service.vram'), {
        free: fmtNum(gpu.free_mb), total: fmtNum(gpu.total_mb)
      })}${gpu.utilization !== null && gpu.utilization !== undefined ? ` · ${gpu.utilization}%` : ''}`
    : '—';
  const facebookMeta = facebook.configured
    ? esc(facebook.mode || '')
    : esc(facebook.reason || t('service.facebookOff'));

  const cards = [
    { name: t('service.ollama'), ok: ollama.available && ollama.model_available, meta: ollamaMeta },
    { name: t('service.comfyui'), ok: comfy.available, meta: comfyMeta },
    { name: t('service.gpu'), ok: Boolean(gpu), meta: gpuMeta },
    { name: t('service.facebook'), ok: Boolean(facebook.configured), meta: facebookMeta }
  ];
  host.innerHTML = cards.map(card => `
    <div class="service-card">
      <span class="name"><span class="dot ${card.ok ? 'ok' : 'warn'}"></span>${esc(card.name)}</span>
      <span class="meta">${card.meta || ''}</span>
    </div>`).join('');
}

function renderRecentJobs() {
  const host = $('#recentJobs');
  if (!host) return;
  const jobs = (state.stats && state.stats.recent_jobs) || [];
  if (!jobs.length) {
    host.innerHTML = `<div class="empty-state">${esc(t('dash.emptyJobs'))}</div>`;
    return;
  }
  host.innerHTML = jobs.map(job => `
    <div class="mini-row" data-goto="jobs">
      <span class="chip">${esc(t('jobKind.' + job.kind))}</span>
      <span class="grow">${esc(job.message || job.stage || '')}</span>
      <span class="status-pill ${esc(job.status)}">${esc(t('jobStatus.' + job.status))}</span>
      <span class="time">${esc(fmtDate(job.created_at))}</span>
    </div>`).join('');
}

function renderDashboardTrends(trends) {
  const host = $('#dashboardTrends');
  if (!host) return;
  if (!trends.length) {
    host.innerHTML = `<div class="empty-state">${esc(t('dash.emptyTrends'))}</div>`;
    return;
  }
  host.innerHTML = trends.map(topic => `
    <div class="mini-trend" data-action="open-trend" data-id="${topic.id}">
      <span class="score">${Math.round(topic.score)}</span>
      <span>
        <span class="text">${esc(topic.title)}</span>
        <span class="cat">${esc(t('cat.' + (topic.category || 'general')))} · ${esc(fill(t('trends.sources'), { n: topic.source_count || 0 }))}</span>
      </span>
    </div>`).join('');
}

/* ------------------------------------------------------------------ trends */
async function loadTrends() {
  const params = new URLSearchParams();
  if (state.filters.category) params.set('category', state.filters.category);
  if (state.filters.status) params.set('status', state.filters.status);
  if (state.filters.minScore) params.set('min_score', state.filters.minScore);
  params.set('limit', '80');
  const data = await api('/api/trends?' + params.toString());
  state.trends = data.items || [];
  renderTrends();
  renderSelectionBar();
}

function metricKindNote(kind) {
  if (kind === 'verified') return t('trends.verifiedNote');
  if (kind === 'estimated') return t('trends.estimatedNote');
  return t('trends.editorialNote');
}

function renderTrends() {
  const host = $('#trendList');
  if (!host) return;
  if (!state.trends.length) {
    host.innerHTML = `<div class="panel"><div class="empty-state">
      ${esc(t('trends.noItems'))}<br><span class="muted">${esc(t('trends.emptyHint'))}</span>
    </div></div>`;
    return;
  }
  host.innerHTML = state.trends.map(topic => {
    const metrics = topic.score_breakdown || topic.metrics || [];
    const scoreClass = topic.score >= 70 ? 'high' : (topic.score < 50 ? 'low' : '');
    const checks = state.selectedTopics.has(topic.id) ? 'checked' : '';
    const metricRows = metrics.map(metric => `
      <div class="metric-row">
        <span class="metric-name">${esc(t('metric.' + metric.key))}</span>
        <span class="metric-value">${Math.round(metric.value)}</span>
        <span class="metric-detail">
          <span class="chip ${esc(metric.kind)}" title="${esc(metricKindNote(metric.kind))}">${esc(t('kind.' + (metric.kind || 'estimated')))}</span>
          ${esc(metric.detail || '')}
        </span>
      </div>`).join('');
    return `
      <div class="trend-card" data-topic-id="${topic.id}">
        <div class="trend-score ${scoreClass}">
          ${Math.round(topic.score)}<small>${esc(t('trends.score'))}</small>
        </div>
        <div class="trend-main">
          <div class="trend-meta" style="margin-bottom:6px">
            <label class="checkbox">
              <input type="checkbox" data-topic-check="${topic.id}" ${checks}>
              ${esc(String(topic.id).padStart(2, '0'))}
            </label>
            <span class="chip">${esc(t('cat.' + (topic.category || 'general')))}</span>
            <span class="chip">${esc(fill(t('trends.sources'), { n: topic.source_count || 0 }))}</span>
            <span class="chip ${esc(topic.status)}">${esc(t('topicStatus.' + (topic.status || 'new')))}</span>
            ${topic.last_seen_at ? `<span class="muted" style="font-size:11px">${esc(timeAgo(topic.last_seen_at))}</span>` : ''}
          </div>
          <h3 class="trend-title">${esc(topic.title)}</h3>
          ${topic.summary ? `<p class="trend-summary">${esc(topic.summary)}</p>` : ''}
          ${topic.rationale ? `<p class="rationale">${esc(topic.rationale)}</p>` : ''}
          ${metrics.length ? `
          <details class="metrics">
            <summary>${esc(t('trends.metrics'))}</summary>
            ${metricRows}
          </details>` : ''}
        </div>
        <div class="trend-actions">
          <button class="btn ghost sm" data-action="trend-detail" data-id="${topic.id}">${esc(t('common.detail'))}</button>
          <button class="btn ghost sm" data-action="trend-research" data-id="${topic.id}">${esc(t('trends.research'))}</button>
          <button class="btn primary sm" data-action="trend-pipeline" data-id="${topic.id}">${esc(t('trends.pipeline'))}</button>
          <button class="icon-btn" data-action="trend-delete" data-id="${topic.id}" title="${esc(t('common.delete'))}">✕</button>
        </div>
      </div>`;
  }).join('');
}

function renderSelectionBar() {
  const bar = $('#selectionBar');
  if (!bar) return;
  const count = state.selectedTopics.size;
  bar.hidden = count === 0;
  const label = $('#selectionCount');
  if (label) label.textContent = fill(t('trends.selectedCount'), { n: count });
}

async function openTrendDetail(topicId) {
  const topic = await api(`/api/trends/${topicId}`);
  const articles = topic.articles || [];
  const research = topic.research;
  const articleRows = articles.length ? articles.map((article, index) => `
    <div class="source-row">
      <span class="domain">${esc(article.domain || '—')}</span>
      <span>
        <a href="${esc(article.url)}" target="_blank" rel="noopener noreferrer">${esc(article.title || article.url)}</a>
        <span class="muted" style="font-size:11px"> · ${esc(fmtDate(article.published_at))}
        ${article.content_chars ? ` · ${fmtNum(article.content_chars)} chars` : ''}</span>
      </span>
    </div>`).join('') : `<span class="muted">${esc(t('common.none'))}</span>`;
  const researchBlock = research ? `
    <div class="settings-subhead">${esc(t('post.research'))}</div>
    <div class="meta-grid">
      <div class="meta-box"><div class="k">${esc(t('post.confidence'))}</div>
        <div class="v">${esc(String(research.confidence || 0))}% · ${esc(t('confidence.' + (research.confidence_label || 'low')))}</div></div>
      <div class="meta-box"><div class="k">${esc(t('fields.sources'))}</div>
        <div class="v">${fmtNum(research.source_count || 0)}</div></div>
      <div class="meta-box"><div class="k">${esc(t('post.posts', 'Bài viết'))}</div>
        <div class="v">${fmtNum((topic.posts || []).length)}</div></div>
    </div>
    <p class="rationale" style="margin-top:10px">${esc((research.data || {}).summary || '')}</p>` : '';
  openModal({
    title: topic.title,
    description: topic.rationale || '',
    body: `
      <div class="settings-subhead">${esc(t('trends.articles'))}</div>
      <div class="source-list">${articleRows}</div>
      ${researchBlock}
      ${topic.primary_url ? `<p style="margin-top:14px"><a href="${esc(topic.primary_url)}" target="_blank" rel="noopener noreferrer">${esc(t('common.open'))} ↗</a></p>` : ''}
    `,
    actions: [{ label: t('common.close'), kind: 'ghost', onClick: () => true }]
  });
}

/* --------------------------------------------------------- discover / batch */
function openDiscoverModal() {
  const trends = (state.settings && state.settings.trends) || {};
  openModal({
    title: t('trends.discoverTitle'),
    description: t('trends.discoverDesc'),
    body: `
      <div class="field"><label>${esc(t('trends.maxTopics'))}</label>
        <input type="number" id="discoverMax" min="1" max="30" value="${trends.max_topics || 12}"></div>
      <div class="field"><label>${esc(t('trends.lookback'))}</label>
        <input type="number" id="discoverLookback" min="2" max="168" value="${trends.lookback_hours || 36}"></div>
      <label class="checkbox" style="margin-top:4px">
        <input type="checkbox" id="discoverLlm" ${trends.llm_editorial ? 'checked' : ''}>
        ${esc(t('trends.useLlm'))}
      </label>`,
    actions: [
      { label: t('common.cancel'), kind: 'ghost', onClick: () => true },
      {
        label: t('common.start'), kind: 'primary',
        onClick: async root => {
          const body = {
            max_topics: Number($('#discoverMax', root).value) || 12,
            lookback_hours: Number($('#discoverLookback', root).value) || 36,
            use_llm: $('#discoverLlm', root).checked
          };
          const data = await api('/api/trends/discover', { method: 'POST', body });
          awaitJob(data.job_id);
          return true;
        }
      }
    ]
  });
}

function openBatchModal() {
  const pipeline = (state.settings && state.settings.pipeline) || {};
  openModal({
    title: t('trends.batchTitle'),
    description: t('trends.batchDesc'),
    body: `
      <div class="field"><label>${esc(t('trends.topN'))}</label>
        <input type="number" id="batchTopN" min="1" max="10" value="3"></div>
      <div class="field"><label>${esc(t('trends.variations'))}</label>
        <input type="number" id="batchVariations" min="1" max="4" value="${pipeline.image_variations || 1}"></div>
      <label class="checkbox"><input type="checkbox" id="batchImage" ${pipeline.auto_image ? 'checked' : ''}>
        ${esc(t('trends.makeImage'))}</label>`,
    actions: [
      { label: t('common.cancel'), kind: 'ghost', onClick: () => true },
      {
        label: t('common.start'), kind: 'primary',
        onClick: async root => {
          const selected = Array.from(state.selectedTopics);
          const body = {
            top_n: Number($('#batchTopN', root).value) || 3,
            topic_ids: selected.length ? selected : [],
            make_image: $('#batchImage', root).checked,
            variations: Number($('#batchVariations', root).value) || 1
          };
          const data = await api('/api/batch', { method: 'POST', body });
          const job = await awaitJob(data.job_id);
          if (job.status === 'done') {
            state.selectedTopics.clear();
            setView('posts');
          }
          return true;
        }
      }
    ]
  });
}

/* ------------------------------------------------------------------- posts */
async function loadPosts() {
  const params = new URLSearchParams();
  if (state.postFilter.status) params.set('status', state.postFilter.status);
  if (state.postFilter.search) params.set('search', state.postFilter.search);
  params.set('limit', '80');
  const data = await api('/api/posts?' + params.toString());
  state.posts = data.items || [];
  if (state.currentPostId && !state.posts.some(post => post.id === state.currentPostId)) {
    state.currentPostId = null;
  }
  if (!state.currentPostId && state.posts.length) state.currentPostId = state.posts[0].id;
  renderPostList();
  if (state.currentPostId) await openPost(state.currentPostId);
  else { state.postDetail = null; renderEditor(); }
}

function renderPostList() {
  const host = $('#postList');
  if (!host) return;
  if (!state.posts.length) {
    host.innerHTML = `<div class="empty-state">${esc(t('posts.noItems'))}</div>`;
    return;
  }
  host.innerHTML = state.posts.map(post => {
    const thumb = post.thumb_url
      ? `<img class="post-thumb" src="${esc(post.thumb_url)}" alt="" loading="lazy">`
      : `<span class="post-thumb">AI</span>`;
    return `<div class="post-item ${state.currentPostId === post.id ? 'is-active' : ''}"
        data-post-id="${post.id}" role="button" tabindex="0">
      ${thumb}
      <div class="post-item-body">
        <span class="post-item-title">${esc(post.headline || '(chưa có tiêu đề)')}</span>
        <span class="post-item-meta">
          <span class="chip ${esc(post.status)}">${esc(t('postStatus.' + post.status))}</span>
          <span>${esc(post.topic_title ? post.topic_title.slice(0, 46) : '')}</span>
        </span>
      </div>
    </div>`;
  }).join('');
}

async function openPost(postId) {
  const post = await api('/api/posts/' + postId);
  state.currentPostId = postId;
  state.postDetail = post;
  renderPostList();
  renderEditor();
}

function renderEditor() {
  const host = $('#postDetail');
  if (!host) return;
  const post = state.postDetail;
  if (!post) {
    host.innerHTML = `<div class="empty-state">${esc(t('posts.empty'))}</div>`;
    return;
  }
  const topic = post.topic || {};
  const research = topic.research || null;
  const report = research && research.data ? research.data : null;
  const flags = post.ai_flags || [];
  const images = post.images || [];
  const confidence = research ? research.confidence : (report ? report.confidence : null);
  const confidenceLabel = research ? research.confidence_label : (report ? report.confidence_label : null);
  const hashtags = (post.hashtags || []).join(' ');
  const alt = (post.alt_headlines || []).join('\n');
  const mainWords = String(post.main_post || '').trim().split(/\s+/).filter(Boolean).length;

  const factRows = ((report && report.key_facts) || []).map(fact => `
    <div class="fact-row">
      <span class="chip ${esc(fact.verdict || 'unverified')}">${esc(t('verdict.' + (fact.verdict || 'unverified')))}</span>
      <span>${esc(fact.fact || '')}${fact.notes ? `<br><span class="muted">${esc(fact.notes)}</span>` : ''}</span>
    </div>`).join('');

  const listBlock = (titleKey, items) => {
    if (!items || !items.length) return '';
    return `<div class="settings-subhead">${esc(t(titleKey))}</div>
      <ul style="margin:0;padding-left:18px;color:var(--muted);font-size:12px;line-height:1.6">
      ${items.map(item => `<li>${esc(typeof item === 'string' ? item : (item.claim || item.fact || JSON.stringify(item)))}</li>`).join('')}
      </ul>`;
  };

  const sourceList = (post.sources && post.sources.length ? post.sources : ((report && report.sources) || []))
    .map(source => `<div class="source-row">
      <span class="domain">${esc(source.domain || '—')}</span>
      ${source.url
        ? `<a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.title || source.url)}</a>`
        : `<span>${esc(source.title || '')}</span>`}
      ${source.published_at ? `<span class="muted" style="font-size:11px">· ${esc(fmtDate(source.published_at))}</span>` : ''}
    </div>`).join('');

  const flagRows = flags.length
    ? flags.map(flag => `<div class="flag ${flag.severity === 'info' ? 'info' : ''}">${esc(flag.detail || flag.type)}</div>`).join('')
    : `<div class="muted" style="font-size:12px">${esc(t('post.noFlags'))}</div>`;

  const gallery = images.length ? images.map(image => `
    <div class="gallery-item ${image.selected ? 'is-selected' : ''}">
      <span class="ai-tag">AI</span>
      <img src="${esc(image.url)}" alt="${esc(image.prompt || '')}" loading="lazy">
      <div class="gallery-actions">
        <button class="btn ghost sm" data-action="select-image" data-id="${post.id}" data-image-id="${image.id}">
          ${esc(image.selected ? t('post.selectedImage') : t('post.selectImage'))}
        </button>
        <a class="btn ghost sm" href="${esc(image.url)}" target="_blank" rel="noopener noreferrer" title="${esc(t('common.open'))}">↗</a>
      </div>
    </div>`).join('') : `<div class="gallery-empty">${esc(t('post.noImages'))}</div>`;

  host.innerHTML = `
    <div class="editor-head">
      <div>
        <h2 class="editor-title">${esc(post.headline || '')}</h2>
        <div class="editor-sub">
          <span class="chip ${esc(post.status)}">${esc(t('postStatus.' + post.status))}</span>
          ${post.topic_score ? `<span class="chip">${esc(t('post.topicScore'))}: ${Math.round(post.topic_score)}</span>` : ''}
          ${confidence !== null && confidence !== undefined ? `<span class="chip verified">${esc(t('post.confidence'))}: ${esc(String(Math.round(confidence)))}% · ${esc(t('confidence.' + (confidenceLabel || 'low')))}</span>` : ''}
          <span class="muted">${esc(t('post.wordCount').replace('{n}', mainWords))}</span>
          <span class="muted">${esc(fmtDate(post.created_at))}</span>
        </div>
      </div>
    </div>
    <div class="editor-actions">
      <button class="btn primary" data-action="save-post" data-id="${post.id}">${esc(t('post.save'))}</button>
      <button class="btn" data-action="approve-post" data-id="${post.id}">${esc(t('post.approve'))}</button>
      <button class="btn danger" data-action="reject-post" data-id="${post.id}">${esc(t('post.reject'))}</button>
      <button class="btn ghost" data-action="regen-text" data-id="${post.id}">${esc(t('post.regenerateText'))}</button>
      <button class="btn ghost" data-action="gen-image" data-id="${post.id}">${esc(t('post.generateImage'))}</button>
      <button class="btn ghost" data-action="export-post" data-id="${post.id}">${esc(t('post.export'))}</button>
      <button class="btn ghost" data-action="delete-post" data-id="${post.id}">${esc(t('post.delete'))}</button>
    </div>

    <div class="field">
      <label for="edHeadline">${esc(t('post.headline'))}</label>
      <input id="edHeadline" type="text" value="${esc(post.headline || '')}">
    </div>
    <div class="field-grid">
      <div class="field"><label for="edHook">${esc(t('post.hook'))}</label>
        <input id="edHook" type="text" value="${esc(post.hook || '')}"></div>
      <div class="field"><label for="edQuestion">${esc(t('post.question'))}</label>
        <input id="edQuestion" type="text" value="${esc(post.closing_question || '')}"></div>
    </div>
    <div class="field">
      <label for="edAlts">${esc(t('post.altHeadlines'))}</label>
      <textarea id="edAlts" rows="3">${esc(alt)}</textarea>
    </div>
    <div class="field">
      <label for="edHashtags">${esc(t('post.hashtags'))}</label>
      <input id="edHashtags" type="text" value="${esc(hashtags)}">
    </div>
    <div class="field">
      <div class="tabs">
        ${['main', 'short', 'long'].map(tab => `<button class="tab ${state.editorTab === tab ? 'is-active' : ''}" data-editor-tab="${tab}">${esc(t('post.' + tab))}</button>`).join('')}
      </div>
      <div class="tab-body">
        <textarea id="edMain" ${state.editorTab === 'main' ? '' : 'hidden'}>${esc(post.main_post || '')}</textarea>
        <textarea id="edShort" ${state.editorTab === 'short' ? '' : 'hidden'}>${esc(post.short_post || '')}</textarea>
        <textarea id="edLong" ${state.editorTab === 'long' ? '' : 'hidden'}>${esc(post.long_post || '')}</textarea>
      </div>
      <span class="char-count" id="editorCount"></span>
    </div>

    <details class="metrics">
      <summary>${esc(t('post.imagePrompt'))}</summary>
      <div class="field"><label>${esc(t('post.imagePrompt'))}</label>
        <textarea id="edImgPrompt" rows="3">${esc(post.image_prompt || '')}</textarea></div>
      <div class="field"><label>${esc(t('post.imageNegative'))}</label>
        <textarea id="edImgNeg" rows="2">${esc(post.image_negative_prompt || '')}</textarea></div>
      <div class="field"><label>${esc(t('post.imageConcept'))}</label>
        <textarea id="edImgConcept" rows="2">${esc(post.image_concept || '')}</textarea></div>
    </details>

    <div class="settings-subhead">${esc(t('post.images'))}</div>
    <div class="gallery">${gallery}</div>
    <p class="hint">${esc(t('post.aiDisclaimer'))}</p>

    <div class="settings-subhead">${esc(t('post.research'))}</div>
    ${report ? `
      ${report.warning ? `<div class="flag">${esc(report.warning)}</div>` : ''}
      <p class="rationale">${esc(report.summary || '')}</p>
      ${factRows ? `<div class="settings-subhead">${esc(t('post.facts'))}</div><div class="fact-list">${factRows}</div>` : ''}
      ${listBlock('post.unconfirmed', report.unconfirmed)}
      ${listBlock('post.conflicting', report.conflicting)}
      ${listBlock('post.missing', report.missing_information)}
      <div class="settings-subhead">${esc(t('post.sources'))}</div>
      <div class="source-list">${sourceList || `<span class="muted">${esc(t('common.none'))}</span>`}</div>
    ` : `<div class="muted" style="font-size:12px">${esc(report === null ? t('common.none') : '')}</div>`}

    <div class="settings-subhead">${esc(t('post.flags'))}</div>
    <div class="flags">${flagRows}</div>
  `;

  updateEditorCount();
}

function updateEditorCount() {
  const counter = $('#editorCount');
  if (!counter) return;
  const active = { main: '#edMain', short: '#edShort', long: '#edLong' }[state.editorTab] || '#edMain';
  const node = $(active);
  if (node) counter.textContent = `${node.value.length} ký tự`;
}

function collectEditor() {
  const read = id => {
    const node = $(id);
    return node ? node.value.trim() : '';
  };
  const altLines = read('#edAlts').split('\n').map(line => line.trim()).filter(Boolean).slice(0, 3);
  const hashtags = read('#edHashtags').split(/\s+/).map(tag => tag.trim()).filter(Boolean);
  return {
    headline: read('#edHeadline'),
    alt_headlines: altLines,
    hook: read('#edHook'),
    closing_question: read('#edQuestion'),
    hashtags,
    main_post: read('#edMain'),
    short_post: read('#edShort'),
    long_post: read('#edLong'),
    image_prompt: read('#edImgPrompt'),
    image_negative_prompt: read('#edImgNeg'),
    image_concept: read('#edImgConcept')
  };
}

/* -------------------------------------------------------------------- jobs */
async function loadJobs() {
  const [jobs, logs] = await Promise.all([
    api('/api/jobs?limit=60'),
    api('/api/logs?limit=60')
  ]);
  state.jobs = jobs.items || [];
  state.logs = logs.items || [];
  renderJobs();
  renderLogs();
}

function renderJobs() {
  const table = $('#jobsTable');
  if (!table) return;
  const body = table.querySelector('tbody');
  const summary = $('#queueSummary');
  if (summary && state.system && state.system.queue) {
    summary.textContent = fill(t('jobs.queue'), {
      running: (state.jobs || []).filter(job => job.status === 'running').length,
      pending: (state.jobs || []).filter(job => job.status === 'queued').length
    });
  }
  if (!state.jobs.length) {
    body.innerHTML = `<tr><td colspan="6"><div class="empty-state">${esc(t('jobs.noJobs'))}</div></td></tr>`;
    return;
  }
  body.innerHTML = state.jobs.map(job => {
    const active = ['queued', 'running'].includes(job.status);
    const canCancel = active;
    return `<tr>
      <td>#${job.id}</td>
      <td>${esc(t('jobKind.' + job.kind))}
        ${job.topic_id ? `<div class="job-message">topic #${job.topic_id}</div>` : ''}
        ${job.post_id ? `<div class="job-message">post #${job.post_id}</div>` : ''}</td>
      <td>
        <div class="progress"><span style="width:${Math.max(2, Math.min(100, job.progress || 0))}%"></span></div>
        <div class="job-message">${esc((job.message || job.stage || '').slice(0, 120))}</div>
      </td>
      <td><span class="status-pill ${esc(job.status)}">${esc(t('jobStatus.' + job.status))}</span></td>
      <td>${esc(fmtDate(job.started_at || job.created_at))}</td>
      <td>
        <button class="btn ghost sm" data-action="job-detail" data-id="${job.id}">${esc(t('jobs.detail'))}</button>
        ${canCancel ? `<button class="btn danger sm" data-action="cancel-job" data-id="${job.id}">${esc(t('jobs.cancel'))}</button>` : ''}
      </td>
    </tr>`;
  }).join('');
}

function renderLogs() {
  const host = $('#logList');
  if (!host) return;
  if (!state.logs.length) { host.innerHTML = ''; return; }
  host.innerHTML = state.logs.map(log => `
    <div class="log-row">
      <span class="log-time">${esc(fmtDate(log.created_at))}</span>
      <span class="log-level ${esc(log.level)}">${esc(log.level)}</span>
      <span class="log-message">${esc(log.message)}</span>
    </div>`).join('');
}

async function showJobDetail(jobId) {
  const job = await api('/api/jobs/' + jobId);
  const payload = JSON.stringify(job.result || {}, null, 2);
  const body = `
    <div class="meta-grid">
      <div class="meta-box"><div class="k">${esc(t('jobs.kind'))}</div><div class="v">${esc(t('jobKind.' + job.kind))}</div></div>
      <div class="meta-box"><div class="k">${esc(t('jobs.status'))}</div><div class="v">${esc(t('jobStatus.' + job.status))}</div></div>
      <div class="meta-box"><div class="k">${esc(t('jobs.started'))}</div><div class="v">${esc(fmtDate(job.started_at))}</div></div>
      <div class="meta-box"><div class="k">${esc(t('jobs.progress'))}</div><div class="v">${job.progress || 0}%</div></div>
    </div>
    <div class="settings-subhead">${esc(t('jobs.result'))}</div>
    <div class="result-box">${esc(payload.length > 4000 ? payload.slice(0, 4000) + '…' : payload)}</div>
    ${job.error ? `<div class="settings-subhead">${esc(t('jobs.error'))}</div><div class="result-box bad">${esc(job.error)}</div>` : ''}`;
  openModal({ title: `#${job.id} · ${t('jobKind.' + job.kind)}`, body, actions: [{ label: t('common.close'), kind: 'ghost', onClick: () => true }] });
}

/* ---------------------------------------------------------------- settings */
async function loadSettings() {
  const [settingsResponse, sourcesResponse, health, workflows] = await Promise.all([
    api('/api/settings'), api('/api/sources'), api('/api/health'), api('/api/comfyui/workflows')
  ]);
  state.settings = settingsResponse.settings;
  state.secrets = settingsResponse.secrets;
  state.options = settingsResponse.options;
  state.health = health;
  state.workflows = workflows.items || [];
  state.sources = sourcesResponse.items || [];
  state.sourceMeta = sourcesResponse;
  renderSettings();
}

function optionList(values, prefix) {
  return values.map(value => ({ value, label: t(`${prefix}.${value}`, value) }));
}

function settingRow(path, labelKey, config = {}) {
  const value = getPath(state.settings, path);
  const id = 'set-' + path.replace(/\./g, '-');
  const type = config.type || 'text';
  let control = '';
  if (type === 'bool') {
    control = `<label class="switch"><input type="checkbox" id="${id}" data-path="${path}" ${value ? 'checked' : ''}><span></span></label>`;
  } else if (type === 'select') {
    const options = (config.options || []).map(option =>
      `<option value="${esc(option.value)}" ${String(option.value) === String(value) ? 'selected' : ''}>${esc(option.label)}</option>`).join('');
    control = `<select id="${id}" data-path="${path}" ${config.number ? 'data-type="number"' : ''}>${options}</select>`;
  } else if (type === 'textarea') {
    const text = type === 'json' || typeof value === 'object' ? JSON.stringify(value || {}, null, 2) : (value || '');
    control = `<textarea id="${id}" data-path="${path}" ${config.json ? 'data-type="json"' : ''} rows="${config.rows || 3}">${esc(text)}</textarea>`;
  } else if (type === 'list') {
    control = `<input id="${id}" type="text" data-path="${path}" data-type="list" value="${esc((value || []).join(', '))}">`;
  } else {
    const numeric = type === 'number';
    control = `<input id="${id}" type="${numeric ? 'number' : 'text'}" data-path="${path}" ${numeric ? 'data-type="number"' : ''}
      ${config.min !== undefined ? `min="${config.min}"` : ''}
      ${config.max !== undefined ? `max="${config.max}"` : ''}
      ${config.step !== undefined ? `step="${config.step}"` : ''}
      value="${esc(value)}" placeholder="${esc(config.placeholder || '')}">`;
  }
  const help = config.help ? `<span class="muted" style="font-size:11px">${esc(config.help)}</span>` : '';
  return `<div class="settings-row"><label for="${id}"><b>${esc(t(labelKey))}</b>${help}</label>${control}</div>`;
}

function settingsPanel(titleKey, descKey, body, extra = '') {
  return `<div class="panel settings-section">
    <div class="panel-head"><h2>${esc(t(titleKey))}</h2></div>
    <p class="hint" style="margin:-6px 0 14px">${esc(t(descKey))}</p>
    ${body}${extra}
  </div>`;
}

function renderSettings() {
  const host = $('#settingsSections');
  if (!host || !state.settings) return;
  const options = state.options || { styles: [], tones: [], lengths: [], audiences: [], categories: CATEGORIES };
  const ollama = (state.health && state.health.ollama) || {};
  const modelOptions = (ollama.models || []).map(model => ({ value: model.name, label: `${model.name}${model.parameter_size ? ' · ' + model.parameter_size : ''}` }));
  const workflowOptions = (state.workflows || []).map(item => item.path);

  const ollamaBody = [
    settingRow('ollama.base_url', 'fields.baseUrl'),
    settingRow('ollama.model', 'fields.model', { type: 'select', options: modelOptions }),
    settingRow('ollama.temperature', 'fields.temperature', { type: 'number', step: 0.05, min: 0, max: 2 }),
    settingRow('ollama.num_ctx', 'fields.context', { type: 'number', min: 2048, max: 40960, step: 1024 }),
    settingRow('ollama.timeout_seconds', 'fields.timeout', { type: 'number', min: 30, max: 1800 }),
    settingRow('ollama.think', 'fields.think', { type: 'bool' }),
    settingRow('ollama.keep_alive', 'fields.keepAlive')
  ].join('') + `
    <div class="settings-foot">
      <button class="btn ghost sm" data-action="test-ollama">${esc(t('fields.test'))}</button>
      <button class="btn ghost sm" data-action="stream-test">${esc(t('fields.streamTest'))}</button>
    </div>
    <div class="result-box" id="ollamaResult">${esc(ollama.available ? `${ollama.selected_model} · ${fill(t('service.models'), { n: (ollama.models || []).length })}` : (ollama.error || t('service.offline')))}</div>`;

  const comfyBody = [
    settingRow('comfyui.base_url', 'fields.baseUrl'),
    `<div class="field"><label for="set-comfyui-workflow_path">${esc(t('fields.workflowPath'))}</label>
      <input id="set-comfyui-workflow_path" list="workflowOptions" data-path="comfyui.workflow_path" value="${esc(getPath(state.settings, 'comfyui.workflow_path'))}">
      <datalist id="workflowOptions">${workflowOptions.map(path => `<option value="${esc(path)}"></option>`).join('')}</datalist>
    </div>`,
    settingRow('comfyui.width', 'fields.width', { type: 'number', min: 256, max: 2048, step: 64 }),
    settingRow('comfyui.height', 'fields.height', { type: 'number', min: 256, max: 2048, step: 64 }),
    settingRow('comfyui.steps', 'fields.steps', { type: 'number', min: 1, max: 120 }),
    settingRow('comfyui.cfg', 'fields.cfg', { type: 'number', min: 0, max: 30, step: 0.5 }),
    settingRow('comfyui.sampler', 'fields.sampler'),
    settingRow('comfyui.scheduler', 'fields.scheduler'),
    settingRow('comfyui.seed', 'fields.seed', { type: 'number', min: -1 }),
    settingRow('comfyui.batch_size', 'fields.batch', { type: 'number', min: 1, max: 8 }),
    settingRow('comfyui.timeout_seconds', 'fields.timeout', { type: 'number', min: 30, max: 1800 }),
    settingRow('comfyui.negative_prompt', 'fields.negativePrompt', { type: 'textarea', rows: 3 }),
    `<details class="metrics"><summary>${esc(t('fields.nodeMap'))}</summary>
      <p class="hint">${esc(t('fields.nodeMapHelp'))}</p>
      <textarea rows="5" data-path="comfyui.node_map" data-type="json">${esc(JSON.stringify(getPath(state.settings, 'comfyui.node_map') || {}, null, 2))}</textarea>
    </details>`
  ].join('') + `
    <div class="settings-foot">
      <button class="btn ghost sm" data-action="validate-workflow">${esc(t('fields.validate'))}</button>
    </div>
    <div class="result-box" id="validateResult"></div>`;

  const writerBody = [
    settingRow('writer.style', 'fields.style', { type: 'select', options: optionList(options.styles, 'writerStyle') }),
    settingRow('writer.tone', 'fields.tone', { type: 'select', options: optionList(options.tones, 'tone') }),
    settingRow('writer.audience', 'fields.audience', { type: 'select', options: optionList(options.audiences, 'audience') }),
    settingRow('writer.length', 'fields.length', { type: 'select', options: optionList(options.lengths, 'length') }),
    settingRow('writer.hashtag_count', 'fields.hashtagCount', { type: 'number', min: 0, max: 15 }),
    settingRow('writer.category', 'fields.category', { type: 'select', options: optionList(['auto'].concat(options.categories || CATEGORIES), 'cat') }),
    settingRow('writer.language', 'fields.language', { type: 'select', options: [{ value: 'vi', label: 'Tiếng Việt' }, { value: 'en', label: 'English' }] }),
    settingRow('writer.include_sources', 'fields.includeSources', { type: 'bool' }),
    settingRow('writer.avoid_clickbait', 'fields.avoidClickbait', { type: 'bool' })
  ].join('');

  const trendsBody = [
    settingRow('trends.max_topics', 'fields.maxTopics', { type: 'number', min: 1, max: 40 }),
    settingRow('trends.lookback_hours', 'fields.lookback', { type: 'number', min: 2, max: 168 }),
    settingRow('trends.max_candidates', 'fields.maxCandidates', { type: 'number', min: 50, max: 1000, step: 50 }),
    settingRow('trends.min_score', 'fields.minScore', { type: 'number', min: 0, max: 100, step: 1 }),
    settingRow('trends.geo', 'fields.geo'),
    settingRow('trends.auto_discover', 'fields.autoDiscover', { type: 'bool' }),
    settingRow('trends.discover_interval_minutes', 'fields.interval', { type: 'number', min: 15, max: 1440 }),
    settingRow('trends.llm_editorial', 'fields.llmEditorial', { type: 'bool' })
  ].join('');

  const researchBody = [
    settingRow('research.max_articles', 'fields.maxArticles', { type: 'number', min: 1, max: 12 }),
    settingRow('research.min_sources', 'fields.minSources', { type: 'number', min: 1, max: 6 }),
    settingRow('research.max_chars_per_article', 'fields.maxChars', { type: 'number', min: 1000, max: 20000, step: 500 }),
    settingRow('research.fetch_timeout_seconds', 'fields.fetchTimeout', { type: 'number', min: 5, max: 120 }),
    settingRow('research.max_concurrency', 'fields.maxConcurrency', { type: 'number', min: 1, max: 10 }),
    settingRow('research.search_more_sources', 'fields.searchMore', { type: 'bool' }),
    settingRow('research.respect_robots', 'fields.robots', { type: 'bool' })
  ].join('');

  const pipelineBody = [
    settingRow('pipeline.workers', 'fields.workers', { type: 'number', min: 1, max: 6 }),
    settingRow('pipeline.auto_image', 'fields.autoImage', { type: 'bool' }),
    settingRow('pipeline.image_variations', 'fields.imageVariations', { type: 'number', min: 1, max: 4 }),
    settingRow('resource.serialize_gpu', 'fields.serializeGpu', { type: 'bool' }),
    settingRow('resource.unload_ollama_before_image', 'fields.unloadOllama', { type: 'bool' }),
    settingRow('resource.free_comfy_before_llm', 'fields.freeComfy', { type: 'bool' }),
    settingRow('resource.min_free_vram_mb', 'fields.minVram', { type: 'number', min: 512, max: 12000, step: 256 }),
    settingRow('resource.gpu_wait_seconds', 'fields.waitSeconds', { type: 'number', min: 5, max: 300 })
  ].join('');

  const facebook = (state.sourceMeta && state.sourceMeta.facebook) || {};
  const search = (state.sourceMeta && state.sourceMeta.search) || {};
  const sourcesBody = `
    ${settingRow('facebook.enabled', 'sources.facebook', { type: 'bool' })}
    ${settingRow('facebook.page_ids', 'sources.facebookPages', { type: 'list' })}
    <p class="hint">${esc(fill(t('sources.facebookToken'), { state: facebook.token_present ? '✓' : '✗' }))}
      ${esc(facebook.reason || '')} · ${esc(fill(t('sources.searchProvider'), { name: (search.active || '—') }))}</p>
    <div id="sourcesTable"></div>
    <div class="settings-subhead">${esc(t('sources.add'))}</div>
    <div class="field-grid">
      <div class="field"><label>${esc(t('sources.name'))}</label><input id="newSourceName" type="text"></div>
      <div class="field"><label>${esc(t('sources.url'))}</label><input id="newSourceUrl" type="url" placeholder="https://…/rss"></div>
      <div class="field"><label>${esc(t('sources.category'))}</label>
        <select id="newSourceCategory">${(options.categories || CATEGORIES).map(cat => `<option value="${esc(cat)}">${esc(t('cat.' + cat))}</option>`).join('')}</select></div>
      <div class="field"><label>${esc(t('sources.language'))}</label>
        <select id="newSourceLanguage"><option value="vi">Tiếng Việt</option><option value="en">English</option></select></div>
    </div>
    <div class="settings-foot"><button class="btn primary sm" data-action="add-source">${esc(t('sources.add'))}</button></div>`;

  const paths = (state.health && state.health.paths) || {};
  const exportButtons = ['md', 'json', 'csv', 'txt'].map(format =>
    `<button class="btn ghost sm" data-action="export" data-format="${format}" data-status="approved">${esc(t('export.' + format))}</button>`).join('');
  const dataBody = `
    <div class="settings-foot" style="margin-top:0">${exportButtons}</div>
    <div class="settings-subhead">${esc(t('export.paths'))}</div>
    <div class="source-list">
      <div class="source-row"><span class="domain">${esc(t('export.dbPath'))}</span><span class="mono">${esc(paths.database || '')}</span></div>
      <div class="source-row"><span class="domain">${esc(t('export.imagesPath'))}</span><span class="mono">${esc(paths.images || '')}</span></div>
      <div class="source-row"><span class="domain">${esc(t('export.exportsPath'))}</span><span class="mono">${esc(paths.exports || '')}</span></div>
    </div>`;

  host.innerHTML = `<div class="settings-grid">
    ${settingsPanel('settings.ollama', 'settings.ollamaDesc', ollamaBody)}
    ${settingsPanel('settings.comfyui', 'settings.comfyDesc', comfyBody)}
    ${settingsPanel('settings.writer', 'settings.writerDesc', writerBody)}
    ${settingsPanel('settings.trends', 'settings.trendsDesc', trendsBody)}
    ${settingsPanel('settings.research', 'settings.researchDesc', researchBody)}
    ${settingsPanel('settings.pipeline', 'settings.pipelineDesc', pipelineBody)}
    ${settingsPanel('settings.sources', 'settings.sourcesDesc', sourcesBody)}
    ${settingsPanel('settings.data', 'settings.dataDesc', dataBody)}
  </div>`;
  renderSourcesTable();
}

function renderSourcesTable() {
  const host = $('#sourcesTable');
  if (!host) return;
  const sources = state.sources || [];
  if (!sources.length) {
    host.innerHTML = `<div class="empty-state">${esc(t('sources.noSources'))}</div>`;
    return;
  }
  host.innerHTML = `<table class="source-table">
    <thead><tr><th>${esc(t('sources.name'))}</th><th>${esc(t('sources.category'))}</th><th>${esc(t('sources.language'))}</th><th>${esc(t('sources.enabled'))}</th></tr></thead>
    <tbody>${sources.map(source => `
      <tr class="${source.enabled ? '' : 'is-disabled'}">
        <td><a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.name)}</a></td>
        <td>${esc(t('cat.' + (source.category || 'general')))}</td>
        <td>${esc(source.language || '—')}</td>
        <td><label class="switch"><input type="checkbox" data-source-toggle="${source.id}" ${source.enabled ? 'checked' : ''}><span></span></label></td>
      </tr>`).join('')}</tbody>
  </table>`;
}

async function saveSettings() {
  if (state.savingSettings) return;
  state.savingSettings = true;
  try {
    const patch = {};
    $$('#settingsSections [data-path]').forEach(input => {
      const path = input.dataset.path;
      let value;
      if (input.type === 'checkbox') value = input.checked;
      else if (input.dataset.type === 'number') {
        value = Number(input.value);
        if (Number.isNaN(value)) throw new Error(`${path} phải là số`);
      } else if (input.dataset.type === 'list') {
        value = input.value.split(',').map(part => part.trim()).filter(Boolean);
      } else if (input.dataset.type === 'json') {
        try { value = input.value.trim() ? JSON.parse(input.value) : {}; }
        catch (err) { throw new Error(`JSON không hợp lệ: ${path}`); }
      } else value = input.value;
      setPath(patch, path, value);
    });
    const response = await api('/api/settings', { method: 'PUT', body: patch });
    state.settings = response.settings;
    state.secrets = response.secrets;
    toast(t('settings.saved'), 'success');
    await refreshHealth();
    renderSettings();
  } catch (err) {
    toast(`${t('settings.saveError')}: ${err.message}`, 'error', 8000);
  } finally {
    state.savingSettings = false;
  }
}

async function validateWorkflow() {
  const pathInput = $('[data-path="comfyui.workflow_path"]');
  const box = $('#validateResult');
  if (!pathInput || !box) return;
  box.className = 'result-box';
  box.textContent = t('common.loading');
  try {
    const result = await api('/api/comfyui/validate', {
      method: 'POST', body: { path: pathInput.value.trim() }
    });
    const roles = (result.summary && result.summary.roles) || {};
    const lines = [];
    lines.push(result.ok ? '✓ Workflow hợp lệ' : '✗ Workflow có lỗi');
    Object.keys(roles).forEach(role => {
      lines.push(`  ${role}: #${roles[role].node_id} (${roles[role].class_type})`);
    });
    (result.warnings || []).forEach(message => lines.push(`! ${message}`));
    (result.errors || []).forEach(message => lines.push(`✗ ${message}`));
    box.className = 'result-box ' + (result.ok ? 'ok' : 'bad');
    box.textContent = lines.join('\n');
  } catch (err) {
    box.className = 'result-box bad';
    box.textContent = err.message;
  }
}

async function testOllama() {
  const box = $('#ollamaResult');
  if (box) { box.className = 'result-box'; box.textContent = t('common.loading'); }
  try {
    const health = await api('/api/ollama/status');
    state.health = Object.assign({}, state.health, { ollama: health });
    renderHealthChips();
    if (box) {
      box.className = 'result-box ' + (health.available && health.model_available ? 'ok' : 'bad');
      box.textContent = health.available
        ? `${health.selected_model}${health.model_available ? ' ✓' : ' — ' + t('service.noModel')}\n${fill(t('service.models'), { n: (health.models || []).length })}\n${health.version ? 'v' + health.version : ''}`
        : (health.error || t('service.offline'));
    }
  } catch (err) {
    if (box) { box.className = 'result-box bad'; box.textContent = err.message; }
  }
}

function openStreamTest() {
  openModal({
    title: t('fields.streamTest'),
    body: `<div class="field"><label>${esc(t('fields.streamPrompt'))}</label>
      <textarea id="streamPrompt" rows="4">Viết 2 câu giới thiệu ngắn về một chủ đề tin công nghệ hôm nay.</textarea></div>
      <div class="result-box" id="streamOutput" style="min-height:90px">…</div>`,
    actions: [
      { label: t('common.close'), kind: 'ghost', onClick: () => true },
      {
        label: t('fields.streamRun'), kind: 'primary',
        onClick: async root => {
          const prompt = $('#streamPrompt', root).value.trim();
          const output = $('#streamOutput', root);
          if (!prompt) throw new Error('Prompt trống');
          output.textContent = '';
          await streamOllama(prompt, output);
          return false;
        }
      }
    ]
  });
}

async function streamOllama(prompt, output) {
  const response = await fetch('/api/ollama/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt })
  });
  if (!response.ok || !response.body) {
    let message = `HTTP ${response.status}`;
    try { const data = await response.json(); message = data.detail || data.error || message; } catch (err) { /* ignore */ }
    throw new Error(message);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index;
    while ((index = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, index).trim();
      buffer = buffer.slice(index + 2);
      if (!chunk.startsWith('data:')) continue;
      let payload;
      try { payload = JSON.parse(chunk.slice(5).trim()); } catch (err) { continue; }
      if (payload.error) throw new Error(payload.error);
      if (payload.delta) output.textContent += payload.delta;
    }
  }
}

/* ----------------------------------------------------------------- actions */
async function startTopicJob(action, topicId) {
  const pipeline = (state.settings && state.settings.pipeline) || {};
  if (action === 'trend-research') {
    const data = await api(`/api/topics/${topicId}/research`, { method: 'POST', body: {} });
    await awaitJob(data.job_id);
  } else {
    const data = await api(`/api/topics/${topicId}/pipeline`, {
      method: 'POST',
      body: {
        make_image: pipeline.auto_image !== false,
        variations: pipeline.image_variations || 1
      }
    });
    await awaitJob(data.job_id);
  }
}

function downloadExport(format, status) {
  const url = `/api/export?format=${encodeURIComponent(format)}&status=${encodeURIComponent(status || 'approved')}`;
  window.location.href = url;
  toast(t('toast.exported'), 'success');
}

async function handleAction(action, el) {
  const id = el && el.dataset ? Number(el.dataset.id) : null;
  switch (action) {
    case 'discover': openDiscoverModal(); break;
    case 'batch': openBatchModal(); break;
    case 'run-selected': {
      if (!state.selectedTopics.size) { toast(t('trends.selectedCount').replace('{n}', 0)); return; }
      openBatchModal();
      break;
    }
    case 'clear-selection': state.selectedTopics.clear(); renderTrends(); renderSelectionBar(); break;
    case 'apply-filters': {
      state.filters.category = $('#filterCategory').value;
      state.filters.status = $('#filterStatus').value;
      state.filters.minScore = Number($('#filterMinScore').value) || 0;
      await loadTrends();
      break;
    }
    case 'refresh-health': await refreshHealth(); await loadDashboard(); break;
    case 'refresh-jobs': await loadJobs(); break;
    case 'save-settings': await saveSettings(); break;
    case 'export-approved': downloadExport('md', 'approved'); break;
    case 'export': downloadExport(el.dataset.format, el.dataset.status || 'approved'); break;
    case 'trend-detail': await openTrendDetail(id); break;
    case 'trend-research': await startTopicJob('trend-research', id); break;
    case 'trend-pipeline': await startTopicJob('trend-pipeline', id); break;
    case 'trend-delete': {
      if (!await confirmDialog(t('common.delete'), t('trends.deleteConfirm'))) return;
      await api(`/api/trends/${id}`, { method: 'DELETE' });
      state.selectedTopics.delete(id);
      await loadTrends();
      break;
    }
    case 'open-trend': state.focusTopic = id; setView('trends'); break;
    case 'cancel-job': {
      if (!await confirmDialog(t('jobs.cancel'), t('jobs.cancelConfirm'))) return;
      await api(`/api/jobs/${id}/cancel`, { method: 'POST' });
      await loadJobs();
      break;
    }
    case 'job-detail': await showJobDetail(id); break;
    case 'save-post': {
      const payload = collectEditor();
      await api(`/api/posts/${id}`, { method: 'PATCH', body: payload });
      toast(t('post.saveSuccess'), 'success');
      await openPost(id);
      break;
    }
    case 'approve-post': {
      await api(`/api/posts/${id}/approve`, { method: 'POST' });
      toast(t('post.approvedToast'), 'success');
      await loadPosts();
      break;
    }
    case 'reject-post': {
      openModal({
        title: t('post.rejectTitle'),
        body: `<div class="field"><label>${esc(t('post.rejectReason'))}</label>
          <textarea id="rejectReason" rows="3"></textarea></div>`,
        actions: [
          { label: t('common.cancel'), kind: 'ghost', onClick: () => true },
          { label: t('post.reject'), kind: 'danger', onClick: async root => {
            await api(`/api/posts/${id}/reject`, { method: 'POST', body: { reason: $('#rejectReason', root).value } });
            toast(t('post.rejectedToast'), 'success');
            await loadPosts();
            return true;
          } }
        ]
      });
      break;
    }
    case 'regen-text': {
      const data = await api(`/api/posts/${id}/regenerate`, { method: 'POST', body: {} });
      toast(t('post.rewriting'));
      const job = await awaitJob(data.job_id, { refresh: false });
      if (job.status === 'done' && job.result && job.result.post_id) {
        state.currentPostId = job.result.post_id;
        await loadPosts();
        await openPost(job.result.post_id);
      } else {
        await loadPosts();
      }
      break;
    }
    case 'gen-image': {
      const post = state.postDetail || {};
      const pipeline = (state.settings && state.settings.pipeline) || {};
      openModal({
        title: t('post.generateImage'),
        body: `
          <div class="field"><label>${esc(t('post.variations'))}</label>
            <input id="genVariations" type="number" min="1" max="4" value="${pipeline.image_variations || 1}"></div>
          <div class="field"><label>${esc(t('post.customPrompt'))}</label>
            <textarea id="genPrompt" rows="3" placeholder="${esc(post.image_prompt || '')}"></textarea></div>
          <div class="field"><label>${esc(t('post.seed'))}</label>
            <input id="genSeed" type="number" min="0" placeholder="random"></div>`,
        actions: [
          { label: t('common.cancel'), kind: 'ghost', onClick: () => true },
          {
            label: t('post.generateImage'), kind: 'primary',
            onClick: async root => {
              const prompt = $('#genPrompt', root).value.trim();
              const seedValue = $('#genSeed', root).value.trim();
              const body = {
                variations: Number($('#genVariations', root).value) || 1,
                prompt: prompt || undefined,
                seed: seedValue ? Number(seedValue) : undefined
              };
              const data = await api(`/api/posts/${id}/image`, { method: 'POST', body });
              toast(t('post.generating'));
              const job = await awaitJob(data.job_id, { refresh: false });
              if (job.status === 'done') await openPost(id);
              return true;
            }
          }
        ]
      });
      break;
    }
    case 'select-image': {
      const imageId = Number(el.dataset.imageId);
      await api(`/api/posts/${id}/select-image/${imageId}`, { method: 'POST' });
      await openPost(id);
      break;
    }
    case 'export-post': window.location.href = `/api/export?format=md&ids=${id}`; toast(t('toast.exported'), 'success'); break;
    case 'delete-post': {
      if (!await confirmDialog(t('common.delete'), t('post.deleteConfirm'))) return;
      await api(`/api/posts/${id}`, { method: 'DELETE' });
      toast(t('post.deletedToast'), 'success');
      state.currentPostId = null;
      state.postDetail = null;
      await loadPosts();
      break;
    }
    case 'validate-workflow': await validateWorkflow(); break;
    case 'test-ollama': await testOllama(); break;
    case 'stream-test': openStreamTest(); break;
    case 'add-source': {
      const body = {
        name: $('#newSourceName').value.trim(),
        url: $('#newSourceUrl').value.trim(),
        category: $('#newSourceCategory').value,
        language: $('#newSourceLanguage').value
      };
      if (!body.url) { toast(t('sources.url') + '?', 'error'); return; }
      await api('/api/sources', { method: 'POST', body });
      toast(t('sources.added'), 'success');
      await loadSettings();
      break;
    }
    default:
      break;
  }
}

/* ------------------------------------------------------------------ events */
function bindStaticControls() {
  const bindAction = (selector, action) => {
    const node = $(selector);
    if (node) node.dataset.action = action;
  };
  bindAction('#btnDiscover', 'discover');
  bindAction('#btnBatch', 'batch');
  bindAction('#btnApplyFilters', 'apply-filters');
  bindAction('#btnRefreshHealth', 'refresh-health');
  bindAction('#btnRefreshJobs', 'refresh-jobs');
  bindAction('#btnRunSelected', 'run-selected');
  bindAction('#btnClearSelection', 'clear-selection');

  $$('.nav-item').forEach(button => {
    button.addEventListener('click', () => setView(button.dataset.view));
  });
  $$('.lang-btn').forEach(button => {
    button.addEventListener('click', () => setLang(button.dataset.lang));
  });

  const statusFilter = $('#postStatusFilter');
  if (statusFilter) {
    statusFilter.addEventListener('change', async () => {
      state.postFilter.status = statusFilter.value;
      state.currentPostId = null;
      try { await loadPosts(); } catch (err) { toast(err.message, 'error'); }
    });
  }
  const postSearch = $('#postSearch');
  if (postSearch) {
    let timer = null;
    postSearch.addEventListener('input', () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(async () => {
        state.postFilter.search = postSearch.value.trim();
        try { await loadPosts(); } catch (err) { toast(err.message, 'error'); }
      }, 450);
    });
  }

  document.addEventListener('click', async event => {
    const goto = event.target.closest('[data-goto]');
    if (goto) { setView(goto.dataset.goto); return; }

    const item = event.target.closest('.post-item[data-post-id]');
    if (item && !event.target.closest('button, a')) {
      try { await openPost(Number(item.dataset.postId)); } catch (err) { toast(err.message, 'error'); }
      return;
    }

    const tab = event.target.closest('[data-editor-tab]');
    if (tab) {
      state.editorTab = tab.dataset.editorTab;
      $$('[data-editor-tab]').forEach(node => node.classList.toggle('is-active', node === tab));
      ['main', 'short', 'long'].forEach(name => {
        const node = $('#ed' + name.charAt(0).toUpperCase() + name.slice(1));
        if (node) node.hidden = name !== state.editorTab;
      });
      updateEditorCount();
      return;
    }

    const node = event.target.closest('[data-action]');
    if (!node) return;
    if (node.tagName === 'BUTTON') node.disabled = true;
    try {
      await handleAction(node.dataset.action, node);
    } catch (err) {
      toast(err.message || t('common.error'), 'error', 9000);
    } finally {
      if (node.tagName === 'BUTTON') node.disabled = false;
    }
  });

  document.addEventListener('change', async event => {
    const topicCheck = event.target.closest('[data-topic-check]');
    if (topicCheck) {
      const topicId = Number(topicCheck.dataset.topicCheck);
      if (topicCheck.checked) state.selectedTopics.add(topicId);
      else state.selectedTopics.delete(topicId);
      renderSelectionBar();
      return;
    }
    const sourceToggle = event.target.closest('[data-source-toggle]');
    if (sourceToggle) {
      try {
        await api(`/api/sources/${sourceToggle.dataset.sourceToggle}`, {
          method: 'PATCH', body: { enabled: sourceToggle.checked }
        });
        await loadSettings();
      } catch (err) { toast(err.message, 'error'); }
    }
  });

  document.addEventListener('input', event => {
    if (event.target.matches('#edMain, #edShort, #edLong')) updateEditorCount();
  });
}

function buildFilterOptions() {
  const select = $('#filterCategory');
  if (!select) return;
  const current = select.value;
  const categories = (state.options && state.options.categories) || CATEGORIES;
  select.innerHTML = `<option value="">${esc(t('common.all'))}</option>` +
    categories.map(cat => `<option value="${esc(cat)}">${esc(t('cat.' + cat))}</option>`).join('');
  select.value = current;
}

async function loadSystem() {
  const [health, settingsResponse, system, workflows] = await Promise.all([
    api('/api/health'), api('/api/settings'), api('/api/system'), api('/api/comfyui/workflows')
  ]);
  state.health = health;
  state.settings = settingsResponse.settings;
  state.secrets = settingsResponse.secrets;
  state.options = settingsResponse.options;
  state.system = system;
  state.workflows = workflows.items || [];
  buildFilterOptions();
  renderHealthChips();
}

async function init() {
  applyStaticI18n();
  bindStaticControls();
  try {
    await loadSystem();
  } catch (err) {
    toast(`${t('toast.loadFailed')}: ${err.message}`, 'error', 9000);
  }
  setView('dashboard');
  refreshHealth();
  refreshJobBadge();
  window.setInterval(refreshHealth, 30000);
  window.setInterval(refreshJobBadge, 8000);
  window.setInterval(() => {
    if (state.view === 'jobs') loadJobs().catch(() => {});
  }, 4000);
}

document.addEventListener('DOMContentLoaded', init);
