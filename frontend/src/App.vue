<script setup>
import { ref, reactive, computed, onUnmounted } from 'vue'
import { useI18n } from './composables/i18n.js'

const { t, toggleLocale, isZh } = useI18n()

const url = ref('')
const loading = ref(false)
const error = ref('')
const videoInfo = ref(null)
const selectedFormat = ref('')
const downloadMode = ref('server')
const taskId = ref('')
const progress = reactive({
  status: '',
  percent: 0,
  speed: '',
  title: '',
  error: '',
  warning: '',
})
let ws = null

const formats = computed(() => {
  if (!videoInfo.value) return []
  const seen = new Set()
  return (videoInfo.value.formats || []).filter(f => {
    const label = f.resolution || f.note || f.ext
    if (!label || seen.has(label)) return false
    seen.add(label)
    return f.vcodec !== 'none'
  })
})

const hasAudioOnly = computed(() => {
  return (videoInfo.value?.formats || []).some(f => f.vcodec === 'none')
})

const step = computed(() => {
  if (progress.status === 'done') return 'done'
  if (progress.status === 'downloading' || progress.status === 'processing' || progress.status === 'merging') return 'downloading'
  if (videoInfo.value) return 'ready'
  return 'input'
})

async function parseUrl() {
  if (!url.value.trim()) return
  error.value = ''
  loading.value = true
  videoInfo.value = null
  selectedFormat.value = ''
  try {
    const res = await fetch('/api/info', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url.value.trim() }),
    })
    if (!res.ok) {
      const d = await res.json()
      throw new Error(d.detail || t('parseFailed'))
    }
    const data = await res.json()
    videoInfo.value = data.data
    if (formats.value.length > 0) {
      selectedFormat.value = formats.value[0].format_id
    }
  } catch (e) {
    error.value = e.message || t('parseFailed')
  } finally {
    loading.value = false
  }
}

async function startDownload() {
  if (!videoInfo.value) return
  error.value = ''
  resetProgress()
  try {
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: url.value.trim(),
        format_id: selectedFormat.value || undefined,
        mode: downloadMode.value,
      }),
    })
    if (!res.ok) {
      const d = await res.json()
      throw new Error(d.detail || t('downloadFailed'))
    }
    const data = await res.json()

    if (data.mode === 'direct') {
      const directUrl = data.data?.direct_url
      if (directUrl) {
        // Download via hidden iframe — no popup, no navigation, works in async context
        const title = encodeURIComponent(data.data.title || 'video')
        const ext = encodeURIComponent(data.data.ext || 'mp4')
        const iframe = document.createElement('iframe')
        iframe.style.display = 'none'
        iframe.src = `/api/proxy-download?url=${encodeURIComponent(directUrl)}&filename=${title}.${ext}`
        document.body.appendChild(iframe)
        setTimeout(() => document.body.removeChild(iframe), 10000)
        progress.status = 'done'
        progress.percent = 100
        return
      }
      // Direct URL not available, retry with server mode
      downloadMode.value = 'server'
      startDownload()
      return
    }

    taskId.value = data.task_id
    connectWs(data.task_id)
  } catch (e) {
    error.value = e.message || t('downloadFailed')
  }
}

function connectWs(id) {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${protocol}//${location.host}/ws/progress/${id}`
  ws = new WebSocket(wsUrl)
  ws.onmessage = (e) => {
    const d = JSON.parse(e.data)
    progress.status = d.status
    progress.percent = d.progress
    progress.speed = d.speed
    progress.title = d.title
    progress.error = d.error
    progress.warning = d.warning || ''
  }
  ws.onerror = () => { error.value = t('wsFailed') }
}

function resetProgress() {
  progress.status = ''
  progress.percent = 0
  progress.speed = ''
  progress.title = ''
  progress.error = ''
  if (ws) { ws.close(); ws = null }
}

function getDownloadLink() {
  return `/api/file/${taskId.value}`
}

function goBack() {
  videoInfo.value = null
  selectedFormat.value = ''
  error.value = ''
  resetProgress()
}

onUnmounted(() => { if (ws) ws.close() })

function formatDuration(sec) {
  if (!sec) return ''
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

function formatSize(bytes) {
  if (!bytes) return ''
  if (bytes > 1e9) return (bytes / 1e9).toFixed(1) + ' GB'
  if (bytes > 1e6) return (bytes / 1e6).toFixed(0) + ' MB'
  return (bytes / 1e3).toFixed(0) + ' KB'
}
</script>

<template>
  <div class="app-container">
    <!-- Header -->
    <header class="header">
      <div class="logo">
        <span class="logo-mark">&#9654;</span>
        <span class="logo-text">vid<span class="logo-light">flow</span></span>
      </div>
      <div class="header-right">
        <button class="lang-toggle" @click="toggleLocale">
          {{ isZh ? 'EN' : '中' }}
        </button>
        <span class="header-tag">{{ t('platforms') }}</span>
      </div>
    </header>

    <!-- Hero -->
    <section class="hero" :class="{ compact: step !== 'input' }">
      <div v-if="step === 'input'" class="hero-content animate-in">
        <h1 class="hero-title">
          {{ t('heroTitle') }}<br />
          <span class="hero-subtitle">{{ t('heroSub') }}</span>
        </h1>
        <p class="hero-desc">{{ t('heroDesc') }}</p>
      </div>

      <!-- Input -->
      <div class="input-card" :class="{ raised: step === 'input' }">
        <div class="input-row">
          <input
            v-model="url"
            type="url"
            class="url-input"
            :placeholder="t('placeholder')"
            @keyup.enter="parseUrl"
          />
          <button
            class="btn-primary"
            :disabled="!url.trim() || loading"
            @click="step === 'input' ? parseUrl() : startDownload()"
          >
            <span v-if="loading">...</span>
            <span v-else-if="step === 'input'">{{ t('parse') }}</span>
            <span v-else>{{ t('download') }}</span>
          </button>
        </div>
        <div v-if="error" class="error-msg">{{ error }}</div>
      </div>

      <button v-if="step !== 'input'" class="btn-back" @click="goBack">
        {{ t('newLink') }}
      </button>
    </section>

    <!-- Video info -->
    <section v-if="videoInfo" class="content animate-in">
      <div class="video-card">
        <img
          v-if="videoInfo.thumbnail"
          :src="'/api/thumbnail?url=' + encodeURIComponent(videoInfo.thumbnail)"
          class="video-thumb"
          alt=""
        />
        <div class="video-meta">
          <h2 class="video-title">{{ videoInfo.title }}</h2>
          <div class="video-tags">
            <span v-if="videoInfo.uploader" class="tag">{{ videoInfo.uploader }}</span>
            <span v-if="videoInfo.duration" class="tag">{{ formatDuration(videoInfo.duration) }}</span>
            <span class="tag">{{ videoInfo.formats?.length || 0 }} {{ t('formats') }}</span>
          </div>
        </div>
      </div>

      <!-- Mode toggle -->
      <div class="mode-row">
        <span class="label">{{ t('modeLabel') }}</span>
        <div class="mode-toggle">
          <button
            :class="['mode-btn', { active: downloadMode === 'server' }]"
            @click="downloadMode = 'server'"
          >
            {{ t('modeServer') }}
          </button>
          <button
            :class="['mode-btn', { active: downloadMode === 'direct' }]"
            @click="downloadMode = 'direct'"
          >
            {{ t('modeDirect') }}
          </button>
        </div>
      </div>

      <!-- Format selector -->
      <div class="format-section">
        <span class="label">{{ t('qualityLabel') }}</span>
        <div class="format-grid">
          <button
            v-for="f in formats"
            :key="f.format_id"
            :class="['format-chip', { selected: selectedFormat === f.format_id }]"
            @click="selectedFormat = f.format_id"
          >
            <span class="fmt-res">{{ f.resolution || f.note || f.ext }}</span>
            <span class="fmt-ext">.{{ f.ext }}</span>
            <span v-if="f.filesize" class="fmt-size">{{ formatSize(f.filesize) }}</span>
          </button>
          <button
            v-if="hasAudioOnly"
            :class="['format-chip', { selected: selectedFormat === 'bestaudio' }]"
            @click="selectedFormat = 'bestaudio'"
          >
            <span class="fmt-res">{{ t('audioOnly') }}</span>
          </button>
        </div>
      </div>
    </section>

    <!-- Progress -->
    <section v-if="step === 'downloading' || step === 'done'" class="content animate-in">
      <div class="progress-card">
        <template v-if="step === 'downloading'">
          <div class="progress-header">
            <span class="label">
              <template v-if="progress.status === 'merging'">MERGING…</template>
              <template v-else-if="progress.status === 'processing'">{{ t('processing') }}</template>
              <template v-else>{{ t('downloading') }}</template>
            </span>
            <span class="progress-pct">{{ progress.percent }}%</span>
          </div>
          <div class="progress-bar">
            <div class="progress-fill" :style="{ width: progress.percent + '%' }"></div>
          </div>
          <div v-if="progress.speed" class="progress-meta">{{ progress.speed }}</div>
        </template>
        <template v-else>
          <div class="done-row">
            <span class="done-mark">&#10003;</span>
            <div>
              <div class="done-title">{{ t('doneTitle') }}</div>
              <div class="done-file">{{ progress.title || 'Video' }}</div>
            </div>
          </div>
          <div v-if="progress.warning" class="done-warning">{{ progress.warning }}</div>
          <a :href="getDownloadLink()" class="btn-primary download-btn" download>
            {{ t('saveFile') }}
          </a>
        </template>
      </div>
    </section>

    <!-- Features -->
    <section v-if="step === 'input'" class="features animate-in">
      <div class="feature">
        <div class="feature-num">01</div>
        <h3>{{ t('feature1Title') }}</h3>
        <p>{{ t('feature1Desc') }}</p>
      </div>
      <div class="feature">
        <div class="feature-num">02</div>
        <h3>{{ t('feature2Title') }}</h3>
        <p>{{ t('feature2Desc') }}</p>
      </div>
      <div class="feature">
        <div class="feature-num">03</div>
        <h3>{{ t('feature3Title') }}</h3>
        <p>{{ t('feature3Desc') }}</p>
      </div>
    </section>

    <!-- Footer -->
    <footer class="footer">
      <div class="footer-line"></div>
      <p>{{ t('footer') }}</p>
    </footer>
  </div>
</template>

<style scoped>
.app-container {
  max-width: 720px;
  margin: 0 auto;
  padding: 32px 24px 80px;
}

/* ── Header ──────────────────────────────────────────────────── */
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0 48px;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}
.lang-toggle {
  background: none;
  border: 1px solid rgba(0,0,0,0.12);
  color: var(--text-dim);
  font-size: 0.6rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  padding: 4px 8px;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.2s;
}
.lang-toggle:hover {
  color: var(--text);
  border-color: rgba(0,0,0,0.25);
}
.logo {
  display: flex;
  align-items: baseline;
  gap: 2px;
}
.logo-mark {
  font-size: 0.7rem;
  color: var(--text-dim);
  margin-right: 6px;
}
.logo-text {
  font-size: 1.15rem;
  font-weight: 600;
  letter-spacing: -0.02em;
}
.logo-light {
  font-weight: 300;
  color: var(--text-dim);
}
.header-tag {
  font-size: 0.65rem;
  font-weight: 500;
  color: var(--text-dim);
  letter-spacing: 0.08em;
}

/* ── Hero ────────────────────────────────────────────────────── */
.hero {
  transition: padding 0.4s;
  padding-bottom: 16px;
}
.hero.compact {
  padding-top: 0;
}
.hero-title {
  font-size: clamp(2.2rem, 5vw, 3rem);
  font-weight: 600;
  line-height: 1.1;
  letter-spacing: -0.03em;
  margin-bottom: 16px;
}
.hero-subtitle {
  font-weight: 300;
  color: var(--text-dim);
}
.hero-desc {
  color: var(--text-dim);
  font-size: 1rem;
  line-height: 1.5;
  margin-bottom: 40px;
  max-width: 440px;
}

/* ── Input ───────────────────────────────────────────────────── */
.input-card {
  border: 1px solid var(--border);
  background: var(--bg-card);
  padding: 6px;
}
.input-card.raised {
  border-color: rgba(0,0,0,0.12);
}
.input-row {
  display: flex;
  gap: 0;
}
.url-input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  color: var(--text);
  font-size: 0.9rem;
  padding: 12px 14px;
  font-family: inherit;
  letter-spacing: -0.01em;
}
.url-input::placeholder {
  color: var(--text-dim);
  opacity: 0.5;
}
.input-row .btn-primary {
  flex-shrink: 0;
}

.error-msg {
  margin-top: 10px;
  padding: 10px 14px;
  background: rgba(239, 68, 68, 0.08);
  border-left: 2px solid var(--error);
  color: var(--error);
  font-size: 0.8rem;
}

.btn-back {
  margin-top: 14px;
  background: none;
  border: none;
  color: var(--text-dim);
  cursor: pointer;
  font-size: 0.8rem;
  font-family: inherit;
  letter-spacing: 0.02em;
}
.btn-back:hover { color: var(--text); }

/* ── Content section ─────────────────────────────────────────── */
.content {
  margin-top: 40px;
}
.label {
  font-size: 0.6rem;
  font-weight: 600;
  color: var(--text-dim);
  letter-spacing: 0.1em;
  margin-bottom: 10px;
  display: block;
}

/* ── Video card ──────────────────────────────────────────────── */
.video-card {
  display: flex;
  gap: 20px;
  padding: 14px;
  border: 1px solid var(--border);
  background: var(--bg-card);
}
.video-thumb {
  width: 180px;
  height: 105px;
  object-fit: cover;
  flex-shrink: 0;
  background: #e0e0e0;
}
.video-meta {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 10px;
}
.video-title {
  font-size: 0.95rem;
  font-weight: 500;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.video-tags {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.tag {
  font-size: 0.7rem;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 2px 0;
}

/* ── Mode ────────────────────────────────────────────────────── */
.mode-row {
  margin-top: 32px;
}
.mode-toggle {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1px;
  background: var(--border);
  border: 1px solid var(--border);
}
.mode-btn {
  padding: 12px 14px;
  border: none;
  background: var(--bg-card);
  color: var(--text-dim);
  cursor: pointer;
  font-size: 0.75rem;
  font-family: inherit;
  letter-spacing: 0.02em;
  text-align: left;
  transition: color 0.2s;
}
.mode-btn:hover { color: var(--text); }
.mode-btn.active {
  color: var(--text);
  background: #e0e0e0;
}

/* ── Format ──────────────────────────────────────────────────── */
.format-section {
  margin-top: 32px;
}
.format-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 1px;
}
.format-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-dim);
  cursor: pointer;
  transition: all 0.15s;
  font-family: inherit;
  font-size: 0.8rem;
}
.format-chip:hover { color: var(--text); border-color: rgba(0,0,0,0.15); }
.format-chip.selected {
  color: var(--text);
  border-color: rgba(0,0,0,0.3);
  background: #e0e0e0;
}
.fmt-res { font-weight: 500; }
.fmt-ext { font-size: 0.7rem; opacity: 0.5; }
.fmt-size { font-size: 0.7rem; opacity: 0.4; margin-left: auto; }

/* ── Progress ────────────────────────────────────────────────── */
.progress-card {
  padding: 24px;
  border: 1px solid var(--border);
  background: var(--bg-card);
}
.progress-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 14px;
}
.progress-pct {
  font-size: 1.1rem;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.progress-bar {
  height: 2px;
  background: rgba(0,0,0,0.06);
}
.progress-fill {
  height: 100%;
  background: var(--text);
  transition: width 0.3s;
}
.progress-meta {
  margin-top: 10px;
  font-size: 0.75rem;
  color: var(--text-dim);
}

/* ── Done ────────────────────────────────────────────────────── */
.done-row {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 24px;
}
.done-mark {
  font-size: 1rem;
  color: var(--text-dim);
}
.done-title { font-size: 1rem; font-weight: 500; }
.done-file {
  font-size: 0.8rem;
  color: var(--text-dim);
  max-width: 400px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.done-warning {
  margin-bottom: 16px;
  padding: 10px 14px;
  background: rgba(233, 173, 104, 0.12);
  border-left: 2px solid var(--accent);
  color: var(--accent-dark);
  font-size: 0.75rem;
  line-height: 1.5;
}
.download-btn {
  width: 100%;
  text-decoration: none;
}

/* ── Features ────────────────────────────────────────────────── */
.features {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1px;
  margin-top: 80px;
  background: var(--border);
  border: 1px solid var(--border);
}
.feature {
  padding: 28px 20px;
  background: var(--bg-card);
}
.feature-num {
  font-size: 0.6rem;
  font-weight: 600;
  color: var(--text-dim);
  letter-spacing: 0.1em;
  margin-bottom: 14px;
}
.feature h3 {
  font-size: 0.9rem;
  font-weight: 500;
  margin-bottom: 8px;
}
.feature p {
  font-size: 0.75rem;
  color: var(--text-dim);
  line-height: 1.6;
}

/* ── Footer ──────────────────────────────────────────────────── */
.footer {
  margin-top: 80px;
  text-align: center;
}
.footer-line {
  height: 1px;
  background: var(--border);
  margin-bottom: 20px;
}
.footer p {
  font-size: 0.7rem;
  color: var(--text-dim);
  letter-spacing: 0.03em;
}
.footer a {
  color: var(--text-dim);
  text-decoration: none;
  border-bottom: 1px solid rgba(0,0,0,0.1);
}
.footer a:hover { color: var(--text); }

/* ── Responsive ──────────────────────────────────────────────── */
@media (max-width: 640px) {
  .hero-title { font-size: 1.8rem; }
  .input-row { flex-direction: column; }
  .input-row .btn-primary { padding: 14px; }
  .video-card { flex-direction: column; }
  .video-thumb { width: 100%; height: 180px; }
  .features { grid-template-columns: 1fr; }
  .mode-toggle { grid-template-columns: 1fr; }
}
</style>
