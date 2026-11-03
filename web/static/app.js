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
  savingSettings: false,
  calendar: null,
  report: null,
  reportKind: 'daily_trend',
  learning: null,
  imageStudio: null,
  metricsAnalysis: null
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
    case 'versions':
      return fill(t('toast.versionsReady'), { n: (result.versions || []).length });
    case 'assess_image':
      return (result.relevance && typeof result.relevance.score === 'number')
        ? `${t('quality.image')}: ${Math.round(result.relevance.score)}/100`
        : (job.message || 'OK');
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
    calendar: [
      { action: 'calendar-refresh', label: t('common.refresh'), kind: 'ghost' }
    ],
    reports: [
      { action: 'report-export', label: t('reports.export'), kind: 'ghost' }
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
    else if (state.view === 'analytics') await loadAnalytics();
    else if (state.view === 'calendar') await loadCalendar();
    else if (state.view === 'reports') await loadReports();
    else if (state.view === 'jobs') await loadJobs();
    else if (state.view === 'settings') await loadSettings();
  } catch (err) {
    toast(err.message || t('toast.loadFailed'), 'error');
  }
}

/* --------------------------------------------------------------- dashboard */
async function loadDashboard() {
  const [stats, trends, calendar] = await Promise.all([
    api('/api/stats'),
    api('/api/trends?limit=6'),
    api('/api/calendar?days=7').catch(() => null)
  ]);
  state.stats = stats;
  state.calendar = calendar;
  renderStatCards();
  renderServiceCards();
  renderRecentJobs();
  renderDashboardTrends(trends.items || []);
  renderDashboardReminders();
}

function renderDashboardReminders() {
  const host = $('#dashboardReminders');
  if (!host) return;
  const reminders = (state.calendar || {}).reminders || {};
  const pending = reminders.pending_approval || [];
  const overdue = reminders.overdue || [];
  if (!pending.length && !overdue.length) {
    host.innerHTML = `<div class="empty-state">${esc(t('calendar.none'))}</div>`;
    return;
  }
  host.innerHTML = `
    <div class="mini-row">
      <span class="chip warn">${esc(fill(t('calendar.pending'), { n: pending.length }))}</span>
      <span class="chip danger">${esc(fill(t('calendar.overdue'), { n: overdue.length }))}</span>
    </div>` + pending.slice(0, 4).map(post => `
      <div class="mini-row">
        <span class="grow">${esc(String(post.headline || '').slice(0, 64))}</span>
        <span class="time">${esc(fmtDate(post.created_at))}</span>
      </div>`).join('');
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
            ${phaseChip(topic)}
            ${viralChip(topic)}
            ${predictionChips(topic)}
            ${sparkline((topic.intelligence || {}).history, { width: 88, height: 26 })}
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
      ${intelligenceBlock(topic)}
      ${predictionBlock(topic)}
      ${strategyBlock(topic)}
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
          ${post.status === 'draft' ? `<button class="btn ghost sm quick-approve" data-action="approve-post" data-id="${post.id}" title="${esc(t('post.approve'))}">✓ ${esc(t('post.approve'))}</button>` : ''}
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

  const gallery = images.length ? images.map(image => {
    const checks = (image.meta && image.meta.checks) || null;
    return `
    <div class="gallery-item ${image.selected ? 'is-selected' : ''}">
      <span class="ai-tag">AI</span>
      ${image.meta && image.meta.relevance
        ? `<span class="relevance-badge ${image.meta.relevance.score < 50 ? 'low' : ''}" title="${esc(image.meta.relevance.note || '')}">${Math.round(image.meta.relevance.score)}/100</span>`
        : ''}
      ${checks ? `<span class="check-badge ${esc(checks.verdict || 'review')}" title="${esc(t('imageStudio.score'))}: ${Math.round(checks.score)} · ${esc(t('imageStudio.verdict'))}: ${esc(t('imgVerdict.' + (checks.verdict || 'review')))}">✓ ${Math.round(checks.score)}</span>` : ''}
      <img src="${esc(image.url)}" alt="${esc(image.prompt || '')}" loading="lazy">
      <div class="gallery-actions">
        <button class="btn ghost sm" data-action="select-image" data-id="${post.id}" data-image-id="${image.id}">
          ${esc(image.selected ? t('post.selectedImage') : t('post.selectImage'))}
        </button>
        <button class="btn ghost sm" data-action="image-check" data-id="${post.id}" data-image-id="${image.id}" title="${esc(t('imageStudio.check'))}">◎</button>
        <a class="btn ghost sm" href="${esc(image.url)}" target="_blank" rel="noopener noreferrer" title="${esc(t('common.open'))}">↗</a>
      </div>
    </div>`;
  }).join('') : `<div class="gallery-empty">${esc(t('post.noImages'))}</div>`;

  const imageOptions = imageStudioOptions();
  const recommendation = post.image_recommendation || {};
  const imageControls = `
    <div class="inline-controls">
      <label class="field-inline">
        <span>${esc(t('imageStudio.style'))}</span>
        <select id="imageStyle">
          <option value="auto">${esc(t('imageStudio.auto'))}</option>
          ${imageOptions.styles.map(item => `<option value="${esc(item.key)}">${esc(t('imgStyle.' + item.key))}</option>`).join('')}
        </select>
      </label>
      <label class="field-inline">
        <span>${esc(t('imageStudio.aspect'))}</span>
        <select id="imageAspect">
          ${imageOptions.aspects.map(item => `<option value="${esc(item.key)}" ${item.key === (imageOptions.default_aspect || '1:1') ? 'selected' : ''}>${esc(t('imageStudio.aspect'))} ${esc(item.key)}</option>`).join('')}
        </select>
      </label>
      ${recommendation.style ? `<span class="hint">${esc(t('imageStudio.recommended'))}: ${esc(t('imgStyle.' + recommendation.style))} · ${esc(recommendation.aspect || '1:1')}</span>` : ''}
    </div>`;

  const metrics = post.metrics || [];
  const metricTotals = metrics.reduce((acc, row) => {
    ['reach', 'engagement', 'reactions', 'comments', 'shares', 'link_clicks'].forEach(key => {
      acc[key] = (acc[key] || 0) + Number(row[key] || 0);
    });
    return acc;
  }, {});
  const metricsPanel = `
    <div class="settings-subhead">${esc(t('fb.title'))}</div>
    ${metrics.length ? `<div class="metric-grid">
      ${[['fb.reach', 'reach'], ['fb.engagement', 'engagement'], ['fb.reactions', 'reactions'],
         ['fb.comments', 'comments'], ['fb.shares', 'shares'], ['fb.clicks', 'link_clicks']]
        .map(([labelKey, key]) => `<div class="metric-box"><span class="k">${esc(t(labelKey))}</span><span class="v">${fmtNum(metricTotals[key] || 0)}</span></div>`).join('')}
    </div>` : `<div class="muted" style="font-size:12px">${esc(t('fb.noData'))}</div>`}
    <div class="inline-actions" style="margin-top:8px">
      <button class="btn ghost sm" data-action="import-metrics" data-id="${post.id}">${esc(t('fb.import'))}</button>
      <button class="btn ghost sm" data-action="fetch-metrics" data-id="${post.id}">${esc(t('fb.fetch'))}</button>
    </div>
    <p class="hint">${esc(t('fb.disclaimer'))}</p>`;

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
      ${topic.id ? `<button class="btn ghost" data-action="versions-compare" data-id="${topic.id}">${esc(t('versions.compare'))}</button>` : ''}
      <button class="btn ghost" data-action="export-post" data-id="${post.id}">${esc(t('post.export'))}</button>
      <button class="btn ghost" data-action="delete-post" data-id="${post.id}">${esc(t('post.delete'))}</button>
    </div>
    ${qualityPanel(post.quality)}
    ${strategyPanel(post)}
    ${learningPanel(post)}

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
    ${imageControls}
    <div class="gallery">${gallery}</div>
    <div class="inline-actions" style="margin-top:8px">
      ${[1, 2, 3].map(count => `<button class="btn ghost sm" data-action="gen-image-quick" data-id="${post.id}" data-count="${count}">${esc(fill(t('post.quickImages'), { n: count }))}</button>`).join('')}
      ${images.length ? `<button class="btn ghost sm" data-action="assess-image" data-id="${post.id}">${esc(t('quality.assessImage'))}</button>` : ''}
    </div>
    <p class="hint">${esc(t('post.aiDisclaimer'))}</p>
    ${metricsPanel}

    <div class="settings-subhead">${esc(t('versions.title'))}</div>
    ${topic.id ? `<div class="inline-actions">
      <button class="btn ghost sm" data-action="versions-generate" data-id="${topic.id}">${esc(t('versions.generate'))}</button>
      <button class="btn ghost sm" data-action="versions-compare" data-id="${topic.id}">${esc(t('versions.compare'))}</button>
    </div>` : `<div class="muted" style="font-size:12px">${esc(t('versions.none'))}</div>`}

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
  const [settingsResponse, sourcesResponse, health, workflows, learningResponse] = await Promise.all([
    api('/api/settings'), api('/api/sources'), api('/api/health'),
    api('/api/comfyui/workflows'), api('/api/learning').catch(() => null)
  ]);
  state.settings = settingsResponse.settings;
  state.secrets = settingsResponse.secrets;
  state.options = settingsResponse.options;
  state.health = health;
  state.workflows = workflows.items || [];
  state.sources = sourcesResponse.items || [];
  state.sourceMeta = sourcesResponse;
  state.learning = learningResponse;
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

  const qualityBody = [
    settingRow('quality.vision_model', 'fields.visionModel', { type: 'text' }),
    settingRow('quality.auto_assess_images', 'fields.autoAssess', { type: 'bool' })
  ].join('');

  const pipelineBody = [
    settingRow('pipeline.workers', 'fields.workers', { type: 'number', min: 1, max: 6 }),
    settingRow('pipeline.auto_image', 'fields.autoImage', { type: 'bool' }),
    settingRow('pipeline.image_variations', 'fields.imageVariations', { type: 'number', min: 1, max: 3 }),
    settingRow('resource.serialize_gpu', 'fields.serializeGpu', { type: 'bool' }),
    settingRow('resource.unload_ollama_before_image', 'fields.unloadOllama', { type: 'bool' }),
    settingRow('resource.free_comfy_before_llm', 'fields.freeComfy', { type: 'bool' }),
    settingRow('resource.min_free_vram_mb', 'fields.minVram', { type: 'number', min: 512, max: 12000, step: 256 }),
    settingRow('resource.gpu_wait_seconds', 'fields.waitSeconds', { type: 'number', min: 5, max: 300 }),
    settingRow('resource.max_concurrent_image_jobs', 'fields.maxImageJobs', { type: 'number', min: 1, max: 4 })
  ].join('');

  const studioOptions = imageStudioOptions();
  const strategyBody = [
    settingRow('strategy.enabled', 'fields.enabled', { type: 'bool' }),
    settingRow('strategy.apply_to_writing', 'fields.applyStrategy', { type: 'bool' }),
    settingRow('strategy.allow_override', 'fields.allowOverride', { type: 'bool' })
  ].join('');

  const learningBody = [
    settingRow('learning.enabled', 'fields.enabled', { type: 'bool' }),
    settingRow('learning.use_in_prompts', 'fields.learningInPrompts', { type: 'bool' }),
    settingRow('learning.min_samples', 'fields.minSamples', { type: 'number', min: 1, max: 50 }),
    `<div class="settings-subhead">${esc(t('learning.title'))}</div>`,
    `<div id="learningSummary" class="learning-summary"></div>`,
    `<div class="settings-foot"><button class="btn danger sm" data-action="learning-reset">${esc(t('learning.reset'))}</button></div>`
  ].join('');

  const imagesBody = [
    settingRow('images.style', 'fields.imageStyle', {
      type: 'select',
      options: [{ value: 'auto', label: t('imageStudio.auto') },
        ...studioOptions.styles.map(item => ({ value: item.key, label: t('imgStyle.' + item.key) }))]
    }),
    settingRow('images.aspect', 'fields.aspect', {
      type: 'select', options: studioOptions.aspects.map(item => ({ value: item.key, label: item.key }))
    }),
    settingRow('images.detect_duplicates', 'fields.detectDuplicates', { type: 'bool' }),
    settingRow('images.text_risk_check', 'fields.textCheck', { type: 'bool' }),
    settingRow('images.min_short_side', 'fields.minShortSide', { type: 'number', min: 256, max: 2048, step: 64 })
  ].join('');

  const calendarBody = [
    settingRow('calendar.enabled', 'fields.enabled', { type: 'bool' }),
    settingRow('calendar.days', 'fields.calendarDays', { type: 'list' }),
    settingRow('calendar.time_windows', 'fields.timeWindows', { type: 'list' }),
    settingRow('calendar.max_per_day', 'fields.maxPerDay', { type: 'number', min: 1, max: 6 }),
    settingRow('calendar.categories', 'fields.category', { type: 'list' }),
    settingRow('calendar.reminder_hours', 'fields.reminderHours', { type: 'number', min: 1, max: 168 }),
    settingRow('calendar.utc_offset_hours', 'fields.offset', { type: 'number', min: -12, max: 14 })
  ].join('');

  const facebookMetricsBody = [
    settingRow('facebook_metrics.enabled', 'fields.metricsEnabled', { type: 'bool' }),
    settingRow('facebook_metrics.graph_version', 'fields.graphVersion'),
    settingRow('facebook_metrics.utc_offset_hours', 'fields.offset', { type: 'number', min: -12, max: 14 }),
    `<p class="hint">${esc(t('fb.disclaimer'))}</p>`
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
    ${settingsPanel('settings.strategy', 'settings.strategyDesc', strategyBody)}
    ${settingsPanel('settings.learning', 'settings.learningDesc', learningBody)}
    ${settingsPanel('settings.images', 'settings.imagesDesc', imagesBody)}
    ${settingsPanel('settings.calendar', 'settings.calendarDesc', calendarBody)}
    ${settingsPanel('settings.facebookMetrics', 'settings.facebookMetricsDesc', facebookMetricsBody)}
    ${settingsPanel('settings.pipeline', 'settings.pipelineDesc', pipelineBody)}
    ${settingsPanel('settings.quality', 'settings.qualityDesc', qualityBody)}
    ${settingsPanel('settings.sources', 'settings.sourcesDesc', sourcesBody)}
    ${settingsPanel('settings.data', 'settings.dataDesc', dataBody)}
  </div>`;
  renderSourcesTable();
  renderLearningSummary();
}

function renderLearningSummary() {
  const host = $('#learningSummary');
  if (!host) return;
  const learning = state.learning || {};
  if (!learning.samples) {
    host.innerHTML = `<div class="muted" style="font-size:12px">${esc(t('learning.none'))}</div>`;
    return;
  }
  const counts = learning.counts || {};
  const preferred = (learning.styles || []).filter(row => row.approval_rate !== null).slice(0, 3);
  const edits = learning.edit_behaviour || {};
  const percent = value => Math.round((value || 0) * 100);
  host.innerHTML = `
    <div class="meta-grid">
      <div class="meta-box"><div class="k">${esc(t('learning.samples'))}</div>
        <div class="v">${fmtNum(learning.samples)}</div></div>
      <div class="meta-box"><div class="k">${esc(t('learning.preferred'))}</div>
        <div class="v">${preferred.length
          ? preferred.map(row => `${esc(t('writerStyle.' + row.key))} (${Math.round(row.approval_rate)}%)`).join(', ')
          : '—'}</div></div>
      <div class="meta-box"><div class="k">${esc(t('learning.edits'))}</div>
        <div class="v">${esc(fill(t('learning.editRate'), { h: percent(edits.headline_change), b: percent(edits.body_change) }))}</div></div>
    </div>
    <div class="muted" style="font-size:12px">
      ${esc(fill(t('learning.counts'), { a: counts.approve || 0, b: counts.reject || 0, c: counts.edit || 0 }))}
    </div>`;
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

/* ----------------------------------------------------------- viral / quality */
function phaseChip(topic) {
  const intel = topic.intelligence || {};
  const phase = intel.phase || topic.phase || 'unknown';
  const delta = intel.score_delta;
  const deltaText = (delta === null || delta === undefined) ? '' : ` ${delta > 0 ? '+' : ''}${delta}`;
  return `<span class="chip phase phase-${esc(phase)}" title="${esc(intel.phase_reason || t('phase.tooltip'))}">${esc(t('phase.' + phase))}${esc(deltaText)}</span>`;
}

function viralChip(topic) {
  const intel = topic.intelligence || {};
  const viral = topic.viral || intel.viral || {};
  const label = viral.label || 'insufficient';
  return `<span class="chip viral viral-${esc(label)}" title="${esc(viral.explanation || '')}">${esc(t('viral.' + label))}</span>`;
}

function sparkline(points, options = {}) {
  const values = (points || []).map(point => Number(point.score)).filter(value => Number.isFinite(value));
  if (values.length < 2) return '';
  const width = options.width || 120;
  const height = options.height || 32;
  const pad = 4;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const stepX = (width - pad * 2) / (values.length - 1);
  const coords = values.map((value, index) => [
    pad + index * stepX,
    height - pad - ((value - min) / span) * (height - pad * 2)
  ]);
  const path = coords.map(([x, y], index) => `${index ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const last = coords[coords.length - 1];
  const rising = values[values.length - 1] >= values[0];
  return `<svg class="sparkline ${rising ? 'up' : 'down'}" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="score history">
    <path d="${path}" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="2.3" fill="currentColor"/>
  </svg>`;
}

function intelligenceBlock(topic) {
  const intel = topic.intelligence || {};
  if (!intel.phase && !intel.viral) return '';
  const viral = intel.viral || {};
  const history = intel.history || [];
  const signals = (viral.verified_signals || []).map(item => `<li>${esc(item.detail || '')}</li>`).join('');
  const missing = (viral.missing_signals || []).map(item => `<li>${esc(item)}</li>`).join('');
  return `
    <div class="settings-subhead">${esc(t('viral.title'))}</div>
    <div class="meta-grid">
      <div class="meta-box"><div class="k">${esc(t('phase.' + (intel.phase || 'unknown')))}</div>
        <div class="v">${esc(intel.phase_reason || '')}</div></div>
      <div class="meta-box"><div class="k">${esc(t('viral.score'))}</div>
        <div class="v">${Math.round(viral.score || 0)}/100 · ${esc(t('viral.' + (viral.label || 'insufficient')))}</div></div>
      <div class="meta-box"><div class="k">${esc(t('viral.freshness'))}</div>
        <div class="v">${intel.freshness_hours === null || intel.freshness_hours === undefined ? '—' : esc(fill(t('viral.hoursAgo'), { n: intel.freshness_hours }))}</div></div>
    </div>
    <p class="rationale">${esc(viral.explanation || '')}</p>
    <div class="meta-grid">
      <div class="meta-box"><div class="k">${esc(t('viral.verified'))}</div>
        <div class="v"><ul class="quality-list">${signals || `<li>${esc(t('common.none'))}</li>`}</ul></div></div>
      <div class="meta-box"><div class="k">${esc(t('viral.missing'))}</div>
        <div class="v"><ul class="quality-list">${missing || `<li>${esc(t('common.none'))}</li>`}</ul></div></div>
    </div>
    <div class="settings-subhead">${esc(t('viral.history'))}</div>
    <div class="history-chart">${sparkline(history, { width: 520, height: 90 }) || `<span class="muted">${esc(t('viral.insufficient'))}</span>`}</div>
    <p class="hint">${esc(viral.disclaimer || '')}</p>`;
}

function qualityPanel(quality) {
  if (!quality) return '';
  const components = quality.components || {};
  const order = [
    ['quality.source', 'source_reliability'],
    ['quality.factCheck', 'fact_check'],
    ['quality.content', 'content'],
    ['quality.image', 'image'],
    ['quality.freshness', 'freshness']
  ];
  const rows = order.map(([labelKey, key]) => {
    const component = components[key] || {};
    const score = component.score;
    const width = score === null || score === undefined ? 0 : Math.max(0, Math.min(100, score));
    return `<div class="quality-row">
      <span class="q-label">${esc(t(labelKey))}</span>
      <span class="q-bar"><span style="width:${width}%"></span></span>
      <span class="q-value">${score === null || score === undefined ? '—' : Math.round(score)}</span>
      <span class="chip ${esc(component.kind || 'estimated')}">${esc(t('kind.' + (component.kind || 'estimated')))}</span>
      <span class="q-detail">${esc(component.detail || '')}</span>
    </div>`;
  }).join('');
  const reasons = (quality.verdict_reasons || []).map(reason => `<li>${esc(reason)}</li>`).join('');
  const unsupported = (quality.unsupported_claims || []).map(claim => `<li>${esc(claim.claim || '')}</li>`).join('');
  return `
    <div class="quality-panel">
      <div class="quality-head">
        <span class="quality-score ${esc(quality.verdict || '')}">${quality.overall === null || quality.overall === undefined ? '—' : Math.round(quality.overall)}</span>
        <div>
          <strong>${esc(t('quality.title'))}</strong>
          <div class="muted" style="font-size:12px">${esc(t('quality.coverage'))}: ${Math.round((quality.coverage || 0) * 100)}%</div>
        </div>
        <span class="chip verdict-${esc(quality.verdict || 'review')}">${esc(t('quality.' + (quality.verdict || 'review')))}</span>
      </div>
      <div class="quality-rows">${rows}</div>
      ${reasons ? `<div class="settings-subhead">${esc(t('quality.warnings'))}</div><ul class="quality-list">${reasons}</ul>` : ''}
      ${unsupported ? `<div class="settings-subhead">${esc(t('quality.unsupported'))}</div><ul class="quality-list">${unsupported}</ul>` : ''}
    </div>`;
}

/* ------------------------------------------------ prediction / strategy */
function momentumSpeed(momentum) {
  if (momentum.per_6h !== null && momentum.per_6h !== undefined) {
    return `${momentum.per_6h > 0 ? '+' : ''}${momentum.per_6h}/6h`;
  }
  if (momentum.per_run !== null && momentum.per_run !== undefined) {
    return `${momentum.per_run > 0 ? '+' : ''}${momentum.per_run}/${t('prediction.perRun')}`;
  }
  return '';
}

function momentumChip(topic) {
  const momentum = ((topic.prediction || {}).momentum) || {};
  if (!momentum.label) return '';
  const speed = momentumSpeed(momentum);
  return `<span class="chip momentum momentum-${esc(momentum.label)}" title="${esc(momentum.reason || '')}">${esc(t('momentum.' + momentum.label))}${speed ? ' ' + esc(speed) : ''}</span>`;
}

function predictionChips(topic) {
  const pred = topic.prediction || {};
  const confidence = pred.confidence || {};
  const opportunity = pred.opportunity || {};
  if (!pred.momentum) return '';
  return [
    momentumChip(topic),
    confidence.value !== null && confidence.value !== undefined
      ? `<span class="chip conf" title="${esc(confidence.reason || '')}">${esc(t('prediction.confidence'))} ${Math.round(confidence.value)}%</span>`
      : '',
    opportunity.score !== null && opportunity.score !== undefined
      ? `<span class="chip opp opp-${esc(opportunity.label || '')}" title="${esc(opportunity.reason || '')}">${esc(t('prediction.opportunity'))} ${Math.round(opportunity.score)}</span>`
      : ''
  ].filter(Boolean).join(' ');
}

function predictionBlock(topic) {
  const pred = topic.prediction || {};
  const momentum = pred.momentum || {};
  if (!momentum.label) return '';
  const confidence = pred.confidence || {};
  const opportunity = pred.opportunity || {};
  const forecast = pred.forecast || {};
  const compare = pred.history || {};
  const change = pred.score_change;
  const changeText = change === null || change === undefined
    ? '—' : `${change > 0 ? '+' : ''}${change}`;
  const forecastText = forecast.available
    ? `${Math.round(forecast.next_score)} · ${esc(t('prediction.' + (forecast.direction || 'flat')))}
       <span class="muted">(${esc(fill(t('prediction.windowValue'), { n: forecast.horizon_hours }))})</span>`
    : `<span class="muted">${esc(t('prediction.noForecast'))}</span>`;
  const factors = (confidence.factors || []).map(factor =>
    `<li>${esc(factor.detail || '')}</li>`).join('');
  return `
    <div class="settings-subhead">${esc(t('prediction.title'))}</div>
    <div class="prediction-grid">
      <div class="prediction-card">
        <span class="k">${esc(t('prediction.current'))}</span>
        <span class="v">${pred.current_score === null || pred.current_score === undefined ? '—' : Math.round(pred.current_score)}</span>
        <span class="muted">${esc(t('prediction.previous'))}: ${pred.previous_score === null || pred.previous_score === undefined ? '—' : Math.round(pred.previous_score)} · ${esc(t('prediction.change'))}: ${esc(changeText)}</span>
      </div>
      <div class="prediction-card">
        <span class="k">${esc(t('prediction.momentum'))}</span>
        <span class="v"><span class="chip momentum momentum-${esc(momentum.label)}">${esc(t('momentum.' + momentum.label))}</span></span>
        <span class="muted">${esc(momentumSpeed(momentum) || '—')} · ${esc(fill(t('prediction.snapshots'), { n: momentum.snapshots || 0 }))}</span>
      </div>
      <div class="prediction-card">
        <span class="k">${esc(t('prediction.confidence'))}</span>
        <span class="v">${Math.round(confidence.value || 0)}%</span>
        <span class="muted">${esc(t('confidence.' + (confidence.label || 'low')))}</span>
      </div>
      <div class="prediction-card">
        <span class="k">${esc(t('prediction.opportunity'))}</span>
        <span class="v">${Math.round(opportunity.score || 0)}</span>
        <span class="muted">${esc(t('prediction.window'))}: ${esc(fill(t('prediction.windowValue'), { n: opportunity.publish_window_hours || 24 }))}</span>
      </div>
      <div class="prediction-card">
        <span class="k">${esc(t('prediction.forecast'))}</span>
        <span class="v">${forecast.available ? Math.round(forecast.next_score) : '—'}</span>
        <span class="muted">${forecastText}</span>
      </div>
      <div class="prediction-card">
        <span class="k">${esc(t('prediction.vsPeak'))}</span>
        <span class="v">${compare.current_vs_peak === null || compare.current_vs_peak === undefined ? '—' : `${compare.current_vs_peak > 0 ? '+' : ''}${compare.current_vs_peak}`}</span>
        <span class="muted">${esc(t('prediction.runs'))}: ${compare.runs || 0} · ${esc(t('prediction.phase'))}: ${esc(t('phase.' + ((pred.lifecycle || {}).phase || 'unknown')))}</span>
      </div>
    </div>
    ${factors ? `<details class="metrics"><summary>${esc(t('prediction.confidence'))}</summary><ul class="quality-list">${factors}</ul></details>` : ''}
    <p class="hint">${esc(pred.disclaimer || '')}</p>`;
}

function strategyBlock(topic) {
  const resolved = (topic || {}).strategy || null;
  if (!resolved || !resolved.effective_key) return '';
  const options = ((state.system || {}).options || {}).strategies || [];
  const effective = resolved.effective_key;
  const list = items => (items || []).map(item => `<li>${esc(item)}</li>`).join('');
  return `
    <div class="settings-subhead">${esc(t('strategy.title'))}</div>
    <div class="strategy-panel">
      <div class="strategy-head">
        <div>
          <strong>${esc(t('strategyKind.' + effective))}</strong>
          <div class="muted" style="font-size:12px">
            ${esc(resolved.is_override ? t('strategy.isOverride') : t('strategy.recommended'))}
            · ${esc(t('strategy.engine'))}
          </div>
        </div>
        <span class="chip strategy-chip">${esc(t('strategyKind.' + effective))}</span>
      </div>
      <p class="rationale">${esc(resolved.summary || '')}</p>
      ${(resolved.reasons || []).length ? `<ul class="quality-list">${list(resolved.reasons)}</ul>` : ''}
      <div class="strategy-columns">
        <div><span class="k">${esc(t('strategy.keyPoints'))}</span><ul>${list(resolved.key_points)}</ul></div>
        <div><span class="k">${esc(t('strategy.avoid'))}</span><ul>${list(resolved.avoid)}</ul></div>
        <div><span class="k">${esc(t('strategy.checklist'))}</span><ul>${list(resolved.checklist)}</ul></div>
      </div>
      <div class="inline-controls">
        <label class="field-inline">
          <span>${esc(t('strategy.override'))}</span>
          <select id="trendStrategySelect">
            ${options.map(item => `<option value="${esc(item.key)}" ${item.key === effective ? 'selected' : ''}>${esc(t('strategyKind.' + item.key))}</option>`).join('')}
          </select>
        </label>
        <input id="trendStrategyNote" type="text" placeholder="${esc(t('strategy.note'))}" value="${esc(resolved.override_note || '')}">
        <button class="btn ghost sm" data-action="trend-strategy-save" data-id="${topic.id}">${esc(t('strategy.apply'))}</button>
        <button class="btn ghost sm" data-action="trend-strategy-reset" data-id="${topic.id}">${esc(t('strategy.reset'))}</button>
      </div>
      <p class="hint">${esc(resolved.disclaimer || '')}</p>
    </div>`;
}

function strategyPanel(post) {
  const strategy = post.strategy || null;
  if (!strategy || !strategy.effective_key) return '';
  const options = ((state.system || {}).options || {}).strategies || [];
  const effective = strategy.effective_key;
  const list = items => (items || []).map(item => `<li>${esc(item)}</li>`).join('');
  return `
    <div class="strategy-panel">
      <div class="strategy-head">
        <div>
          <strong>${esc(t('strategy.title'))}</strong>
          <div class="muted" style="font-size:12px">
            ${esc(strategy.is_override ? t('strategy.isOverride') : t('strategy.recommended'))}
            · ${esc(t('strategy.engine'))}
          </div>
        </div>
        <span class="chip strategy-chip">${esc(t('strategyKind.' + effective))}</span>
      </div>
      <p class="rationale">${esc(strategy.summary || '')}</p>
      ${(strategy.reasons || []).length ? `<ul class="quality-list">${list(strategy.reasons)}</ul>` : ''}
      <div class="strategy-columns">
        <div><span class="k">${esc(t('strategy.keyPoints'))}</span><ul>${list(strategy.key_points)}</ul></div>
        <div><span class="k">${esc(t('strategy.avoid'))}</span><ul>${list(strategy.avoid)}</ul></div>
        <div><span class="k">${esc(t('strategy.checklist'))}</span><ul>${list(strategy.checklist)}</ul></div>
      </div>
      <div class="inline-controls">
        <label class="field-inline">
          <span>${esc(t('strategy.override'))}</span>
          <select id="strategySelect">
            ${options.map(item => `<option value="${esc(item.key)}" ${item.key === effective ? 'selected' : ''}>${esc(t('strategyKind.' + item.key))}</option>`).join('')}
          </select>
        </label>
        <input id="strategyNote" type="text" placeholder="${esc(t('strategy.note'))}" value="${esc(strategy.override_note || '')}">
        <button class="btn ghost sm" data-action="strategy-save" data-id="${post.topic_id}">${esc(t('strategy.apply'))}</button>
        <button class="btn ghost sm" data-action="strategy-reset" data-id="${post.topic_id}">${esc(t('strategy.reset'))}</button>
      </div>
      <p class="hint">${esc(strategy.disclaimer || '')}</p>
    </div>`;
}

function learningPanel(post) {
  return `
    <div class="learning-panel">
      <div class="settings-subhead">${esc(t('learning.title'))}</div>
      <div class="inline-actions">
        <span class="muted">${esc(t('learning.rate'))}</span>
        ${[1, 2, 3, 4, 5].map(score =>
          `<button class="rating-btn" data-action="rate-post" data-id="${post.id}" data-rating="${score}" title="${score}/5">${'★'.repeat(score)}</button>`).join('')}
      </div>
      <p class="hint">${esc(t('learning.privacy'))}</p>
    </div>`;
}

function imageStudioOptions() {
  const options = (state.system || {}).options || {};
  return {
    styles: options.image_styles || [],
    aspects: options.aspects || [{ key: '1:1' }, { key: '4:5' }, { key: '16:9' }],
    default_aspect: ((state.settings || {}).images || {}).aspect || '1:1'
  };
}

/* ---------------------------------------------------------------- versions */
async function startVersions(topicId) {
  const data = await api(`/api/topics/${topicId}/versions`, { method: 'POST', body: {} });
  toast(t('versions.generating'));
  const job = await awaitJob(data.job_id, { refresh: false });
  if (job.status !== 'done') throw new Error(job.error || t('toast.jobFailed'));
  await openVersionsModal(topicId);
}

async function openVersionsModal(topicId) {
  const data = await api(`/api/topics/${topicId}/versions`);
  const versions = data.items || [];
  const body = versions.length ? `<div class="version-grid">${versions.map(version => {
    const words = String(version.main_post || '').split(/\s+/).filter(Boolean).length;
    const qualityScore = Math.round(((version.quality || {}).score) || 0);
    const flags = (version.flags || []).length;
    return `<article class="version-card ${version.selected ? 'is-selected' : ''}">
      <header>
        <span class="chip variant-${esc(version.variant)}">${esc(t('versions.variant' + version.variant))}</span>
        <span class="muted">${esc(fill(t('versions.words'), { n: words }))}</span>
        ${version.selected ? `<span class="chip verified">${esc(t('versions.selected'))}</span>` : ''}
      </header>
      <h4>${esc(version.headline || '')}</h4>
      ${version.hook ? `<p class="version-hook">${esc(version.hook)}</p>` : ''}
      <div class="version-body">${esc(String(version.main_post || '').slice(0, 1200))}${String(version.main_post || '').length > 1200 ? '…' : ''}</div>
      <footer>
        <span class="chip">${esc(t('writerStyle.' + version.style))}</span>
        <span class="muted">${esc(t('quality.content'))}: ${qualityScore}</span>
        ${flags ? `<span class="chip warn">${flags} ⚠</span>` : ''}
        <button class="btn primary sm" data-action="apply-version" data-id="${version.id}" data-topic-id="${topicId}">${esc(t('versions.apply'))}</button>
      </footer>
    </article>`;
  }).join('')}</div>` : `<div class="empty-state">${esc(t('versions.none'))}</div>`;

  openModal({
    title: t('versions.compare'),
    description: t('versions.note'),
    body,
    actions: [
      {
        label: t('versions.regenerate'), kind: 'ghost',
        onClick: async () => { await startVersions(topicId); return true; }
      },
      { label: t('common.close'), kind: 'ghost', onClick: () => true }
    ]
  });
}

/* --------------------------------------------------------------- analytics */
const ANALYTICS_SERIES = [
  ['discovered', '#8b6cff', 'analytics.discovered'],
  ['researched', '#22d3ee', 'analytics.researched'],
  ['drafted', '#fbbf24', 'analytics.drafted'],
  ['approved', '#34d399', 'analytics.approved'],
  ['rejected', '#fb7185', 'analytics.rejected']
];

async function loadAnalytics() {
  const days = Number(($('#analyticsDays') || {}).value || 14);
  const data = await api(`/api/analytics?days=${days}`);
  state.analytics = data;
  renderAnalyticsFunnel(data);
  renderAnalyticsChart(data);
  renderAnalyticsCategories(data);
  renderAnalyticsRecurring(data);
}

function renderAnalyticsFunnel(data) {
  const host = $('#analyticsFunnel');
  if (!host) return;
  const funnel = data.funnel || {};
  const confidence = data.confidence || {};
  const cards = [
    ['analytics.discovered', funnel.discovered, fill(t('analytics.days'), { n: data.window_days })],
    ['analytics.researched', funnel.researched, ''],
    ['analytics.drafted', funnel.drafted, ''],
    ['analytics.approved', funnel.approved, ''],
    ['analytics.rejected', funnel.rejected, ''],
    ['analytics.confidence', confidence.average === null || confidence.average === undefined ? '—' : `${confidence.average}%`,
      `${confidence.low || 0} low · ${confidence.moderate || 0} mid · ${confidence.high || 0} high`]
  ];
  host.innerHTML = cards.map(([labelKey, value, foot]) => {
    const display = typeof value === 'number' ? fmtNum(value) : String(value === undefined ? '—' : value);
    return `
    <div class="stat-card">
      <span class="stat-label">${esc(t(labelKey))}</span>
      <span class="stat-value">${esc(display)}</span>
      <span class="stat-foot">${esc(foot || '')}</span>
    </div>`;
  }).join('');
}

function renderAnalyticsChart(data) {
  const host = $('#analyticsChart');
  const legend = $('#analyticsLegend');
  if (!host) return;
  const rows = data.series || [];
  if (!rows.length) { host.innerHTML = `<div class="empty-state">${esc(t('analytics.noData'))}</div>`; return; }
  const width = 760;
  const height = 230;
  const padL = 30;
  const padB = 26;
  const padT = 12;
  const padR = 12;
  const maxValue = Math.max(1, ...rows.flatMap(row => ANALYTICS_SERIES.map(([key]) => Number(row[key]) || 0)));
  const x = index => padL + (index * (width - padL - padR)) / Math.max(1, rows.length - 1);
  const y = value => height - padB - (value / maxValue) * (height - padB - padT);
  const grid = [0, 0.25, 0.5, 0.75, 1].map(fraction => {
    const lineY = y(maxValue * fraction);
    return `<line x1="${padL}" y1="${lineY.toFixed(1)}" x2="${width - padR}" y2="${lineY.toFixed(1)}" class="chart-grid"/>`
      + `<text x="4" y="${(lineY + 3).toFixed(1)}" class="chart-axis">${Math.round(maxValue * fraction)}</text>`;
  }).join('');
  const paths = ANALYTICS_SERIES.map(([key, color]) => {
    const points = rows.map((row, index) => [x(index), y(Number(row[key]) || 0)]);
    const d = points.map(([px, py], index) => `${index ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)}`).join(' ');
    return `<path d="${d}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>`;
  }).join('');
  const labelStep = Math.max(1, Math.ceil(rows.length / 8));
  const labels = rows.map((row, index) => index % labelStep === 0
    ? `<text x="${x(index).toFixed(1)}" y="${height - 8}" class="chart-axis" text-anchor="middle">${esc(row.date.slice(5))}</text>`
    : '').join('');
  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" class="line-chart" role="img">${grid}${paths}${labels}</svg>`;
  if (legend) {
    legend.innerHTML = ANALYTICS_SERIES.map(([, color, labelKey]) =>
      `<span class="legend-item"><span class="legend-dot" style="background:${color}"></span>${esc(t(labelKey))}</span>`).join('');
  }
}

function renderAnalyticsCategories(data) {
  const host = $('#analyticsCategories');
  if (!host) return;
  const rows = data.categories || [];
  if (!rows.length) { host.innerHTML = `<div class="empty-state">${esc(t('analytics.noData'))}</div>`; return; }
  host.innerHTML = `<table class="table compact">
    <thead><tr>
      <th>${esc(t('fields.category'))}</th>
      <th>${esc(t('stats.topics'))}</th>
      <th>${esc(t('analytics.drafted'))}</th>
      <th>${esc(t('analytics.approved'))}</th>
      <th>${esc(t('analytics.approvalRate'))}</th>
    </tr></thead><tbody>
    ${rows.map(row => `<tr>
      <td>${esc(t('cat.' + (row.category || 'general')))}</td>
      <td>${fmtNum(row.topics)}</td>
      <td>${fmtNum(row.posts)}</td>
      <td>${fmtNum(row.approved)}</td>
      <td>${row.approval_rate === null || row.approval_rate === undefined ? '—' : `${row.approval_rate}%`}</td>
    </tr>`).join('')}
    </tbody></table>`;
}

function renderAnalyticsRecurring(data) {
  const host = $('#analyticsRecurring');
  if (!host) return;
  const rows = data.recurring || [];
  if (!rows.length) { host.innerHTML = `<div class="empty-state">${esc(t('analytics.noData'))}</div>`; return; }
  host.innerHTML = `<table class="table compact">
    <thead><tr>
      <th>${esc(t('trends.title', 'Chủ đề'))}</th>
      <th>${esc(t('analytics.appearances'))}</th>
      <th>${esc(t('analytics.peak'))}</th>
      <th>${esc(t('analytics.change'))}</th>
    </tr></thead><tbody>
    ${rows.map(row => `<tr>
      <td><a href="#" data-action="open-trend" data-id="${row.topic_id}">${esc(String(row.title || '').slice(0, 70))}</a></td>
      <td>${fmtNum(row.appearances)}</td>
      <td>${Math.round(row.peak_score || 0)}</td>
      <td class="${row.score_change >= 0 ? 'pos' : 'neg'}">${row.score_change > 0 ? '+' : ''}${row.score_change}</td>
    </tr>`).join('')}
    </tbody></table>`;
}

/* ---------------------------------------------------------------- calendar */
async function loadCalendar() {
  const days = Number(($('#calendarDays') || {}).value || 7);
  const data = await api(`/api/calendar?days=${days}`);
  state.calendar = data;
  renderCalendar();
}

function barRows(rows, labelFn) {
  if (!rows || !rows.length) return `<span class="muted">${esc(t('common.none'))}</span>`;
  const max = Math.max(...rows.map(row => Number(row.count) || 0)) || 1;
  return rows.map(row => `
    <div class="bar-row">
      <span class="bar-label">${esc(labelFn(row) || '—')}</span>
      <span class="bar-track"><span style="width:${Math.max(4, (Number(row.count) || 0) / max * 100)}%"></span></span>
      <span class="bar-value">${fmtNum(row.count)}</span>
    </div>`).join('');
}

function renderCalendar() {
  const data = state.calendar;
  if (!data) return;
  const config = data.config || {};
  const meta = $('#calendarMeta');
  if (meta) {
    meta.textContent = `${fill(t('calendar.maxPerDay'), { n: config.max_per_day || 1 })} · ` +
      `${(data.slots || []).length} ${t('calendar.slots').toLowerCase()}`;
  }

  const reminders = data.reminders || {};
  const reminderHost = $('#calendarReminders');
  if (reminderHost) {
    reminderHost.innerHTML = `
      <div class="reminder-row">
        <span class="chip warn">${esc(fill(t('calendar.pending'), { n: reminders.pending_count || 0 }))}</span>
        <span class="chip danger">${esc(fill(t('calendar.overdue'), { n: (reminders.overdue || []).length }))}</span>
        <span class="hint">${esc(reminders.message || t('calendar.notPublisher'))}</span>
      </div>`;
  }

  const plan = data.plan || [];
  const planHost = $('#calendarPlan');
  if (planHost) {
    planHost.innerHTML = plan.length ? `<table class="table">
      <thead><tr>
        <th>${esc(t('calendar.post'))}</th>
        <th>${esc(t('calendar.when'))}</th>
        <th>${esc(t('calendar.slot'))}</th>
        <th></th>
      </tr></thead><tbody>${plan.map(item => `
        <tr>
          <td>
            <div>${esc(item.headline || '')}</div>
            <div class="muted" style="font-size:11px">
              ${esc(t('cat.' + (item.category || 'general')))}
              ${item.needs_approval ? `· <span class="chip warn">${esc(t('calendar.needsApproval'))}</span>` : ''}
            </div>
            <div class="muted" style="font-size:11px">${esc(item.reason || '')}</div>
          </td>
          <td>${esc(item.local_label || '')}</td>
          <td>${esc(item.slot || '')}</td>
          <td><button class="btn ghost sm" data-action="schedule-post" data-id="${item.post_id}"
            data-scheduled="${esc(item.scheduled_at)}">${esc(t('calendar.schedule'))}</button></td>
        </tr>`).join('')}</tbody></table>` : `<div class="empty-state">${esc(t('calendar.noPlan'))}</div>`;
  }

  const distribution = data.distribution || {};
  const distHost = $('#calendarDistribution');
  if (distHost) {
    distHost.innerHTML = `
      <div class="settings-subhead">${esc(t('calendar.byCategory'))}</div>
      ${barRows(distribution.categories, row => t('cat.' + row.category))}
      <div class="settings-subhead">${esc(t('calendar.byDay'))}</div>
      ${barRows((distribution.days || []).map(row => ({ ...row, count: row.count })), row => row.date)}`;
  }

  const times = data.suggested_times || [];
  const timeHost = $('#calendarTimes');
  if (timeHost) {
    timeHost.innerHTML = times.length ? times.map(item => `
      <div class="mini-row">
        <span class="chip">${esc(item.local_time)}</span>
        <span class="grow">${esc(t('calendar.source.' + (item.source || 'settings')))}
          ${item.samples ? `<span class="muted">· ${fmtNum(item.samples)}</span>` : ''}</span>
        <span class="muted">${item.average_engagement === null || item.average_engagement === undefined ? '—' : fmtNum(item.average_engagement)}</span>
      </div>`).join('') : `<div class="empty-state">${esc(t('common.none'))}</div>`;
  }

  const entries = data.entries || [];
  const entryHost = $('#calendarEntries');
  if (entryHost) {
    entryHost.innerHTML = entries.length ? `<table class="table">
      <thead><tr>
        <th>${esc(t('calendar.post'))}</th>
        <th>${esc(t('calendar.when'))}</th>
        <th>${esc(t('jobs.status'))}</th>
        <th>${esc(t('calendar.note'))}</th>
        <th></th>
      </tr></thead><tbody>${entries.map(entry => `
        <tr>
          <td>${esc(entry.headline || '')}</td>
          <td>${esc(fmtDate(entry.scheduled_at))}</td>
          <td><span class="chip ${esc(entry.post_status || 'draft')}">${esc(t('postStatus.' + (entry.post_status || 'draft')))}</span></td>
          <td>${esc(entry.note || '')}</td>
          <td><button class="btn danger sm" data-action="calendar-remove" data-id="${entry.id}">${esc(t('calendar.remove'))}</button></td>
        </tr>`).join('')}</tbody></table>` : `<div class="empty-state">${esc(t('calendar.none'))}</div>`;
  }

  const unscheduled = data.unscheduled || [];
  const unscheduledHost = $('#calendarUnscheduled');
  if (unscheduledHost) {
    unscheduledHost.innerHTML = unscheduled.length ? unscheduled.map(item => `
      <div class="mini-row">
        <span class="grow">${esc(item.headline || '')}</span>
        <span class="chip ${esc(item.status)}">${esc(t('postStatus.' + item.status))}</span>
        <button class="btn ghost sm" data-action="schedule-post" data-id="${item.post_id}">${esc(t('calendar.schedule'))}</button>
      </div>`).join('') : `<div class="empty-state">${esc(t('calendar.none'))}</div>`;
  }
}

function openScheduleModal(postId, defaultAt) {
  const slots = ((state.calendar || {}).slots) || [];
  const options = slots.slice(0, 30).map(slot =>
    `<option value="${esc(slot.scheduled_at)}" ${slot.scheduled_at === defaultAt ? 'selected' : ''}>
      ${esc(slot.local_label)} · ${esc(slot.window)}
    </option>`).join('');
  openModal({
    title: t('calendar.scheduleTitle'),
    description: t('calendar.notPublisher'),
    body: `
      <div class="field"><label>${esc(t('calendar.when'))}</label>
        <select id="scheduleSlot">${options}</select></div>
      <div class="field"><label>${esc(t('calendar.note'))}</label>
        <input id="scheduleNote" type="text" maxlength="200"></div>`,
    actions: [
      { label: t('common.cancel'), kind: 'ghost', onClick: () => true },
      {
        label: t('calendar.schedule'), kind: 'primary',
        onClick: async root => {
          const scheduledAt = $('#scheduleSlot', root).value;
          if (!scheduledAt) { toast(t('calendar.noPlan'), 'error'); return false; }
          await api('/api/calendar/entries', {
            method: 'POST',
            body: { post_id: postId, scheduled_at: scheduledAt, note: $('#scheduleNote', root).value.trim() }
          });
          toast(t('calendar.scheduledToast'), 'success');
          await loadCalendar();
          return true;
        }
      }
    ]
  });
}

/* ----------------------------------------------------------------- reports */
async function loadReports() {
  const kinds = ((state.system || {}).options || {}).report_kinds || [];
  const select = $('#reportKind');
  if (select && kinds.length) {
    const current = select.value || state.reportKind;
    select.innerHTML = kinds.map(kind =>
      `<option value="${esc(kind.kind)}">${esc(t('reportKind.' + kind.kind))}</option>`).join('');
    select.value = kinds.some(kind => kind.kind === current) ? current : kinds[0].kind;
  }
  state.reportKind = (select && select.value) || state.reportKind;
  const days = Number(($('#reportDays') || {}).value || 7);
  const data = await api(`/api/reports/${state.reportKind}?days=${days}`);
  state.report = data;
  renderReports();
}

function renderReports() {
  const report = state.report;
  if (!report) return;
  const meta = $('#reportMeta');
  if (meta) {
    meta.textContent = `${fill(t('reports.days'), { n: report.window_days })} · ${fill(t('reports.generated'), { time: fmtDate(report.generated_at) })}`;
  }

  const summaryHost = $('#reportSummary');
  if (summaryHost) {
    summaryHost.innerHTML = (report.summary || []).map(item => `
      <div class="stat-card">
        <span class="stat-label">${esc(item.label_vi)}</span>
        <span class="stat-value">${esc(String(item.value))}</span>
      </div>`).join('');
  }

  const sectionsHost = $('#reportSections');
  if (sectionsHost) {
    sectionsHost.innerHTML = (report.sections || []).map(section => {
      const columns = section.columns || [];
      const rows = section.rows || [];
      return `<div class="panel report-panel">
        <div class="panel-head"><h2>${esc(section.title_vi)}</h2></div>
        ${rows.length ? `<div class="table-scroll"><table class="table">
          <thead><tr>${columns.map(column => `<th>${esc(column.label_vi)}</th>`).join('')}</tr></thead>
          <tbody>${rows.map(row => `<tr>${columns.map(column =>
            `<td>${esc(row[column.key] === null || row[column.key] === undefined ? '—' : String(row[column.key]))}</td>`).join('')}</tr>`).join('')}</tbody>
        </table></div>` : `<div class="empty-state">${esc(t('reports.empty'))}</div>`}
      </div>`;
    }).join('');
  }

  const notesHost = $('#reportNotes');
  const notesPanel = $('#reportNotesPanel');
  if (notesHost) {
    notesHost.innerHTML = (report.notes || []).map(note => `<div class="flag info">${esc(note)}</div>`).join('')
      || `<div class="muted">${esc(t('common.none'))}</div>`;
  }
  if (notesPanel) notesPanel.hidden = !(report.notes || []).length;
}

/* --------------------------------------------------------- facebook metrics */
function openMetricsImportModal(postId) {
  openModal({
    title: t('fb.importTitle'),
    description: t('fb.disclaimer'),
    body: `
      <div class="field">
        <label>${esc(t('fb.csv'))}</label>
        <textarea id="metricsCsv" rows="8" placeholder="post_id,reach,engagement,reactions,comments,shares,link_clicks,published_at"></textarea>
        <span class="hint">${esc(t('fb.csvHint'))}</span>
      </div>`,
    actions: [
      { label: t('common.cancel'), kind: 'ghost', onClick: () => true },
      {
        label: t('fb.import'), kind: 'primary',
        onClick: async root => {
          const csv = $('#metricsCsv', root).value.trim();
          if (!csv) { toast(t('fb.noData'), 'error'); return false; }
          const result = await api('/api/facebook/metrics/import', { method: 'POST', body: { csv } });
          toast(fill(t('fb.imported'), { n: result.stored }), 'success');
          if (result.errors && result.errors.length) {
            toast(result.errors.slice(0, 3).join(' · '), 'error', 9000);
          }
          await openPost(postId);
          return true;
        }
      }
    ]
  });
}

function openMetricsFetchModal(postId) {
  openModal({
    title: t('fb.fetchTitle'),
    description: t('fb.disclaimer'),
    body: `
      <div class="field"><label>${esc(t('fb.externalId'))}</label>
        <input id="metricsExternalId" type="text" placeholder="123456789_987654321"></div>`,
    actions: [
      { label: t('common.cancel'), kind: 'ghost', onClick: () => true },
      {
        label: t('fb.fetch'), kind: 'primary',
        onClick: async root => {
          const externalId = $('#metricsExternalId', root).value.trim();
          if (!externalId) { toast(t('fb.externalId') + '?', 'error'); return false; }
          try {
            const result = await api(`/api/posts/${postId}/metrics/fetch`, {
              method: 'POST', body: { external_id: externalId }
            });
            toast(fill(t('fb.imported'), { n: result.stored }), 'success');
            await openPost(postId);
            return true;
          } catch (err) {
            toast(err.message || t('common.error'), 'error', 9000);
            return false;
          }
        }
      }
    ]
  });
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
    case 'calendar-refresh': await loadCalendar(); break;
    case 'save-settings': await saveSettings(); break;
    case 'trend-strategy-save': {
      const select = $('#trendStrategySelect');
      if (!select) return;
      await api(`/api/trends/${id}/strategy`, {
        method: 'PUT',
        body: { strategy: select.value, note: (($('#trendStrategyNote') || {}).value || '').trim() }
      });
      toast(t('strategy.saved'), 'success');
      await loadTrends();
      await openTrendDetail(Number(id));
      break;
    }
    case 'trend-strategy-reset': {
      await api(`/api/trends/${id}/strategy`, { method: 'DELETE' });
      toast(t('strategy.resetDone'), 'success');
      await loadTrends();
      await openTrendDetail(Number(id));
      break;
    }
    case 'strategy-save': {
      const select = $('#strategySelect');
      if (!select) return;
      await api(`/api/trends/${id}/strategy`, {
        method: 'PUT',
        body: { strategy: select.value, note: (($('#strategyNote') || {}).value || '').trim() }
      });
      toast(t('strategy.saved'), 'success');
      if (state.currentPostId) await openPost(state.currentPostId);
      break;
    }
    case 'strategy-reset': {
      await api(`/api/trends/${id}/strategy`, { method: 'DELETE' });
      toast(t('strategy.resetDone'), 'success');
      if (state.currentPostId) await openPost(state.currentPostId);
      break;
    }
    case 'rate-post': {
      const rating = Number(el.dataset.rating);
      await api(`/api/posts/${id}/rating`, { method: 'POST', body: { rating } });
      toast(`${t('learning.saved')} · ${rating}/5`, 'success');
      break;
    }
    case 'image-check': {
      const imageId = Number(el.dataset.imageId);
      const result = await api(`/api/posts/${id}/image-check`, {
        method: 'POST', body: { image_id: imageId }
      });
      toast(`${t('imageStudio.checked')}: ${Math.round(result.score)}/100 · ${t('imgVerdict.' + result.verdict)}`,
        result.verdict === 'reject' ? 'error' : 'success');
      await openPost(id);
      break;
    }
    case 'import-metrics': openMetricsImportModal(id); break;
    case 'fetch-metrics': openMetricsFetchModal(id); break;
    case 'schedule-post': openScheduleModal(Number(el.dataset.id), el.dataset.scheduled || null); break;
    case 'calendar-remove': {
      if (!await confirmDialog(t('calendar.remove'), t('calendar.confirmRemove'))) return;
      await api(`/api/calendar/entries/${id}`, { method: 'DELETE' });
      toast(t('calendar.removedToast'), 'success');
      await loadCalendar();
      break;
    }
    case 'report-export': {
      const days = Number(($('#reportDays') || {}).value || 7);
      window.location.href = `/api/reports/${state.reportKind}?days=${days}&format=md`;
      break;
    }
    case 'learning-reset': {
      if (!await confirmDialog(t('learning.reset'), t('learning.resetConfirm'))) return;
      await api('/api/learning', { method: 'DELETE' });
      toast(t('learning.resetDone'), 'success');
      await loadSettings();
      break;
    }
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
      const studio = imageStudioOptions();
      openModal({
        title: t('post.generateImage'),
        body: `
          <div class="field"><label>${esc(t('post.variations'))}</label>
            <input id="genVariations" type="number" min="1" max="3" value="${Math.min(3, pipeline.image_variations || 1)}">
            <span class="hint">${esc(t('post.gpuLimit'))}</span></div>
          <div class="field-grid">
            <div class="field"><label>${esc(t('imageStudio.style'))}</label>
              <select id="genStyle">
                <option value="auto">${esc(t('imageStudio.auto'))}</option>
                ${studio.styles.map(item => `<option value="${esc(item.key)}">${esc(t('imgStyle.' + item.key))}</option>`).join('')}
              </select></div>
            <div class="field"><label>${esc(t('imageStudio.aspect'))}</label>
              <select id="genAspect">
                ${studio.aspects.map(item => `<option value="${esc(item.key)}" ${item.key === studio.default_aspect ? 'selected' : ''}>${esc(item.key)}</option>`).join('')}
              </select></div>
          </div>
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
                seed: seedValue ? Number(seedValue) : undefined,
                style: $('#genStyle', root).value || 'auto',
                aspect: $('#genAspect', root).value || undefined
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
    case 'versions-generate': await startVersions(id); break;
    case 'versions-compare': await openVersionsModal(id); break;
    case 'apply-version': {
      const created = await api(`/api/versions/${id}/apply`, { method: 'POST' });
      closeModal();
      toast(t('versions.applied'), 'success');
      state.currentPostId = created.id;
      await loadPosts();
      await openPost(created.id);
      break;
    }
    case 'assess-image': {
      const data = await api(`/api/posts/${id}/assess-image`, { method: 'POST', body: {} });
      toast(t('quality.assessingImage'));
      const job = await awaitJob(data.job_id, { refresh: false });
      if (job.status === 'done') await openPost(id);
      break;
    }
    case 'gen-image-quick': {
      const count = Number(el.dataset.count) || 1;
      const style = (($('#imageStyle') || {}).value) || 'auto';
      const aspect = (($('#imageAspect') || {}).value) || undefined;
      const data = await api(`/api/posts/${id}/image`, {
        method: 'POST', body: { variations: count, style, aspect }
      });
      toast(t('post.generating'));
      const job = await awaitJob(data.job_id, { refresh: false });
      if (job.status === 'done') await openPost(id);
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
  const analyticsDays = $('#analyticsDays');
  if (analyticsDays) {
    analyticsDays.addEventListener('change', async () => {
      try { await loadAnalytics(); } catch (err) { toast(err.message, 'error'); }
    });
  }

  const calendarDays = $('#calendarDays');
  if (calendarDays) {
    calendarDays.addEventListener('change', async () => {
      try { await loadCalendar(); } catch (err) { toast(err.message, 'error'); }
    });
  }

  const reportKind = $('#reportKind');
  if (reportKind) {
    reportKind.addEventListener('change', async () => {
      state.reportKind = reportKind.value;
      try { await loadReports(); } catch (err) { toast(err.message, 'error'); }
    });
  }
  const reportDays = $('#reportDays');
  if (reportDays) {
    reportDays.addEventListener('change', async () => {
      try { await loadReports(); } catch (err) { toast(err.message, 'error'); }
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
