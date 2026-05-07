<script setup>
import { ref, reactive, computed, onUnmounted, nextTick } from 'vue'
import { useI18n } from './composables/i18n.js'
import { Transformer } from 'markmap-lib'
import { Markmap } from 'markmap-view'

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
  aiResult.value = null
  showAiPanel.value = false
  chatMessages.value = []
  aiError.value = ''
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
  url.value = ''
  videoInfo.value = null
  selectedFormat.value = ''
  error.value = ''
  resetProgress()
}

// ── AI state ──────────────────────────────────────────────────
const aiLoading = ref(false)
const aiError = ref('')
const aiResult = ref(null)
const showAiPanel = ref(false)
const chatMessages = ref([])
const chatInput = ref('')
const chatLoading = ref(false)
const mindmapSvg = ref(null)
const mindmapFullscreen = ref(false)
const mindmapFullscreenSvg = ref(null)
let mmInline = null
let mmFullscreen = null
const overlayRef = ref(null)

async function startAiSummarize() {
  if (!videoInfo.value) return
  aiError.value = ''
  aiLoading.value = true
  showAiPanel.value = true
  aiResult.value = null
  chatMessages.value = []
  try {
    const res = await fetch('/api/summarize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url.value.trim() }),
    })
    const data = await res.json()
    if (!data.ok) {
      aiError.value = data.message || t('parseFailed')
      return
    }
    aiResult.value = data.data
    await nextTick()
    if (data.data.outline_markdown) {
      renderMindmap(data.data.outline_markdown)
    }
  } catch (e) {
    aiError.value = e.message || t('parseFailed')
  } finally {
    aiLoading.value = false
  }
}

function renderMindmapTo(markdown, el) {
  if (!el || !markdown) return null
  el.innerHTML = ''
  const transformer = new Transformer()
  const mm = Markmap.create(el)
  const { root } = transformer.transform(markdown)
  mm.setData(root)
  mm.fit()
  return mm
}

function renderMindmap(markdown) {
  mmInline = renderMindmapTo(markdown, mindmapSvg.value)
}

async function sendChatMessage() {
  if (!chatInput.value.trim() || chatLoading.value) return
  const question = chatInput.value.trim()
  chatInput.value = ''
  chatMessages.value.push({ role: 'user', content: question })
  chatLoading.value = true
  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: url.value.trim(),
        question,
        history: chatMessages.value.slice(0, -1),
      }),
    })
    const data = await res.json()
    if (data.ok) {
      chatMessages.value.push({ role: 'assistant', content: data.answer })
    } else {
      chatMessages.value.push({ role: 'assistant', content: data.answer || t('parseFailed') })
    }
  } catch (e) {
    chatMessages.value.push({ role: 'assistant', content: t('wsFailed') })
  } finally {
    chatLoading.value = false
  }
}

function closeAiPanel() {
  showAiPanel.value = false
  aiResult.value = null
  aiError.value = ''
  chatMessages.value = []
}

function openMindmapFullscreen() {
  mindmapFullscreen.value = true
  nextTick(() => {
    if (aiResult.value?.outline_markdown && mindmapFullscreenSvg.value) {
      mmFullscreen = renderMindmapTo(aiResult.value.outline_markdown, mindmapFullscreenSvg.value)
    }
    overlayRef.value?.focus()
  })
}

function closeMindmapFullscreen() {
  mindmapFullscreen.value = false
}

function onFullscreenKeydown(e) {
  if (e.key === 'Escape') {
    closeMindmapFullscreen()
  }
}

function downloadMindmapImage() {
  const isFull = mindmapFullscreen.value
  const svgEl = isFull ? mindmapFullscreenSvg.value : mindmapSvg.value
  const mm = isFull ? mmFullscreen : mmInline
  if (!svgEl || !mm) return

  // Get content bounds from markmap's internal state (actual node coords, not screen pixels)
  const { x1, y1, x2, y2 } = mm.state.rect
  if (!x2 || !y2) return

  const padding = 20
  const vbX = x1 - padding
  const vbY = y1 - padding
  const vbW = x2 - x1 + padding * 2
  const vbH = y2 - y1 + padding * 2

  const svgClone = svgEl.cloneNode(true)

  // Remove d3-zoom transform from the top-level <g> — content renders at natural coords
  const mainG = svgClone.querySelector('svg > g')
  if (mainG) mainG.removeAttribute('transform')

  // Replace foreignObject elements with text — canvas can't render foreignObject
  const fos = svgClone.querySelectorAll('foreignObject')
  fos.forEach(fo => {
    const div = fo.querySelector('div')
    if (!div) { fo.remove(); return }
    const text = document.createElementNS('http://www.w3.org/2000/svg', 'text')
    text.textContent = div.textContent || ''
    text.setAttribute('x', fo.getAttribute('x') || '0')
    const foY = parseFloat(fo.getAttribute('y') || '0')
    const foH = parseFloat(fo.getAttribute('height') || '20')
    text.setAttribute('y', foY + foH / 2 + 1)
    const ds = div.style
    const fontSize = ds?.fontSize || '14px'
    const color = ds?.color || '#222222'
    const fw = ds?.fontWeight || '400'
    text.setAttribute('style', `font-size:${fontSize};fill:${color};font-weight:${fw};font-family:Inter,sans-serif;dominant-baseline:central`)
    fo.parentNode.replaceChild(text, fo)
  })

  const scale = 2
  svgClone.setAttribute('width', vbW * scale)
  svgClone.setAttribute('height', vbH * scale)
  svgClone.setAttribute('viewBox', `${vbX} ${vbY} ${vbW} ${vbH}`)
  svgClone.removeAttribute('xmlns:fo')

  let svgString = new XMLSerializer().serializeToString(svgClone)
  if (!svgString.includes('xmlns="http://www.w3.org/2000/svg"')) {
    svgString = svgString.replace('<svg ', '<svg xmlns="http://www.w3.org/2000/svg" ')
  }

  const blob = new Blob([svgString], { type: 'image/svg+xml;charset=utf-8' })
  const url = URL.createObjectURL(blob)

  const img = new Image()
  img.onload = () => {
    const canvas = document.createElement('canvas')
    canvas.width = vbW * scale
    canvas.height = vbH * scale
    const ctx = canvas.getContext('2d')
    ctx.fillStyle = '#f5f5f5'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, vbW * scale, vbH * scale)
    URL.revokeObjectURL(url)
    canvas.toBlob((b) => {
      if (!b) return
      const dl = URL.createObjectURL(b)
      const a = document.createElement('a')
      a.href = dl
      a.download = 'mindmap.png'
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(dl)
    }, 'image/png')
  }
  img.onerror = () => {
    URL.revokeObjectURL(url)
    const a = document.createElement('a')
    const blob2 = new Blob([svgString], { type: 'image/svg+xml;charset=utf-8' })
    a.href = URL.createObjectURL(blob2)
    a.download = 'mindmap.svg'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  }
  img.src = url
}

function downloadSubtitles() {
  const dlUrl = `/api/subtitles/download?url=${encodeURIComponent(url.value.trim())}`
  const a = document.createElement('a')
  a.href = dlUrl
  a.download = ''
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
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
          <button
            v-if="step !== 'input'"
            class="btn-ai"
            :disabled="aiLoading"
            @click="startAiSummarize()"
          >
            <span v-if="aiLoading">...</span>
            <span v-else>{{ t('aiSummarize') }}</span>
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

    <!-- AI Panel -->
    <section v-if="showAiPanel && step !== 'input'" class="content animate-in">
      <div class="ai-panel">
        <button class="btn-close-panel" @click="closeAiPanel">&times;</button>

        <div v-if="aiLoading" class="ai-loading">{{ t('aiGenerating') }}</div>
        <div v-if="aiError" class="error-msg">{{ aiError }}</div>

        <template v-if="aiResult">
          <div class="ai-section">
            <span class="label">{{ t('aiSummary') }}</span>
            <p class="ai-summary">{{ aiResult.summary }}</p>
          </div>

          <div v-if="aiResult.subtitles_text" class="ai-section">
            <div class="section-header">
              <span class="label">{{ t('aiSubtitles') }}</span>
              <button class="btn-inline" @click="downloadSubtitles">&#8595; {{ t('downloadSubtitles') }}</button>
            </div>
            <details class="subtitles-detail">
              <summary class="subtitles-summary">共 {{ aiResult.subtitles_text.length }} 字 — 点击展开</summary>
              <div class="subtitles-box">{{ aiResult.subtitles_text }}</div>
            </details>
          </div>

          <div class="ai-section">
            <span class="label">{{ t('aiKeyPoints') }}</span>
            <ul class="ai-points">
              <li v-for="(p, i) in aiResult.key_points" :key="i">{{ p }}</li>
            </ul>
          </div>

          <div class="ai-section">
            <div class="section-header">
              <span class="label">{{ t('aiMindMap') }}</span>
              <div class="section-actions">
                <button class="btn-inline" @click="openMindmapFullscreen">&#9974; {{ t('mindmapFullscreen') }}</button>
                <button class="btn-inline" @click="downloadMindmapImage">&#8595; {{ t('mindmapDownload') }}</button>
              </div>
            </div>
            <div class="mindmap-wrap">
              <svg ref="mindmapSvg" class="mindmap-svg"></svg>
            </div>
          </div>

          <div class="ai-section">
            <span class="label">{{ t('aiChat') }}</span>
            <div class="chat-messages">
              <div
                v-for="(msg, i) in chatMessages"
                :key="i"
                :class="['chat-msg', msg.role === 'user' ? 'chat-user' : 'chat-ai']"
              >
                {{ msg.content }}
              </div>
            </div>
            <div class="chat-input-row">
              <input
                v-model="chatInput"
                class="chat-input"
                :placeholder="t('aiAskPlaceholder')"
                @keyup.enter="sendChatMessage"
              />
              <button
                class="btn-send"
                :disabled="!chatInput.trim() || chatLoading"
                @click="sendChatMessage"
              >
                {{ t('aiSend') }}
              </button>
            </div>
          </div>
        </template>
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

    <!-- Mindmap fullscreen overlay -->
    <Teleport to="body">
      <div
        v-if="mindmapFullscreen"
        class="mindmap-overlay"
        @keydown="onFullscreenKeydown"
        tabindex="-1"
        ref="overlayRef"
      >
        <div class="mindmap-overlay-header">
          <span class="mindmap-overlay-title">{{ t('aiMindMap') }}</span>
          <div class="mindmap-overlay-actions">
            <button class="btn-inline" @click="downloadMindmapImage">
              &#8595; {{ t('mindmapDownload') }}
            </button>
            <button class="btn-inline" @click="closeMindmapFullscreen">
              &#10005; {{ t('exitFullscreen') }}
            </button>
          </div>
        </div>
        <div class="mindmap-overlay-body">
          <svg ref="mindmapFullscreenSvg" class="mindmap-fullscreen-svg"></svg>
        </div>
      </div>
    </Teleport>

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

/* ── Section header (label + actions) ────────────────────────── */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.section-header .label {
  margin-bottom: 0;
}
.section-actions {
  display: flex;
  gap: 4px;
}
.btn-inline {
  background: none;
  border: 1px solid var(--border);
  color: var(--text-dim);
  font-size: 0.65rem;
  font-weight: 500;
  letter-spacing: 0.04em;
  padding: 4px 10px;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.2s;
}
.btn-inline:hover {
  color: var(--text);
  border-color: rgba(0,0,0,0.2);
}
/* ── Mindmap fullscreen overlay ──────────────────────────────── */
.mindmap-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: #e9e9e9;
  display: flex;
  flex-direction: column;
  outline: none;
}
.mindmap-overlay-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-bottom: 1px solid rgba(0,0,0,0.08);
  flex-shrink: 0;
}
.mindmap-overlay-title {
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--text-dim);
  letter-spacing: 0.08em;
}
.mindmap-overlay-actions {
  display: flex;
  gap: 8px;
}
.mindmap-overlay-body {
  flex: 1;
  overflow: hidden;
  padding: 20px;
}
.mindmap-fullscreen-svg {
  width: 100%;
  height: 100%;
  display: block;
}

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

/* ── AI Button ─────────────────────────────────────────────────── */
.btn-ai {
  flex-shrink: 0;
  margin-left: 1px;
  padding: 12px 18px;
  border: 1px solid var(--border);
  background: var(--bg-card);
  color: var(--text-dim);
  cursor: pointer;
  font-size: 0.8rem;
  font-family: inherit;
  font-weight: 500;
  letter-spacing: 0.04em;
  transition: all 0.2s;
}
.btn-ai:hover {
  color: var(--text);
  border-color: rgba(0,0,0,0.15);
}
.btn-ai:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ── AI Panel ──────────────────────────────────────────────────── */
.ai-panel {
  position: relative;
  padding: 24px;
  border: 1px solid var(--border);
  background: var(--bg-card);
}
.ai-panel .label {
  margin-bottom: 12px;
}
.btn-close-panel {
  position: absolute;
  top: 10px;
  right: 14px;
  background: none;
  border: none;
  font-size: 1.2rem;
  color: var(--text-dim);
  cursor: pointer;
  padding: 4px 8px;
  line-height: 1;
}
.btn-close-panel:hover { color: var(--text); }

.ai-loading {
  padding: 24px 0;
  color: var(--text-dim);
  font-size: 0.85rem;
}

.ai-section {
  margin-top: 28px;
}
.ai-section:first-of-type {
  margin-top: 0;
}

.ai-summary {
  font-size: 0.9rem;
  line-height: 1.7;
  color: var(--text);
}

.ai-points {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ai-points li {
  position: relative;
  padding-left: 16px;
  font-size: 0.85rem;
  line-height: 1.6;
  color: var(--text);
}
.ai-points li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 9px;
  width: 5px;
  height: 5px;
  background: var(--accent);
}

/* ── Mind Map ──────────────────────────────────────────────────── */
.mindmap-wrap {
  border: 1px solid var(--border);
  overflow: hidden;
}
.mindmap-svg {
  width: 100%;
  height: 420px;
  display: block;
}

/* ── Chat ──────────────────────────────────────────────────────── */
.chat-messages {
  max-height: 300px;
  overflow-y: auto;
  margin-bottom: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.chat-msg {
  padding: 10px 14px;
  font-size: 0.8rem;
  line-height: 1.6;
  max-width: 85%;
}
.chat-user {
  align-self: flex-end;
  background: #e0e0e0;
  color: var(--text);
}
.chat-ai {
  align-self: flex-start;
  background: #f0f0f0;
  color: var(--text);
  border-left: 2px solid var(--accent);
}
.chat-input-row {
  display: flex;
  gap: 0;
  border: 1px solid var(--border);
}
.chat-input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  color: var(--text);
  font-size: 0.8rem;
  padding: 10px 12px;
  font-family: inherit;
}
.chat-input::placeholder {
  color: var(--text-dim);
  opacity: 0.5;
}
.btn-send {
  flex-shrink: 0;
  padding: 10px 16px;
  border: none;
  border-left: 1px solid var(--border);
  background: transparent;
  color: var(--text-dim);
  cursor: pointer;
  font-size: 0.75rem;
  font-family: inherit;
  font-weight: 500;
  letter-spacing: 0.04em;
}
.btn-send:hover { color: var(--text); }
.btn-send:disabled { opacity: 0.4; cursor: not-allowed; }

/* ── Subtitle text ──────────────────────────────────────────────── */
.subtitles-detail {
  border: 1px solid var(--border);
}
.subtitles-summary {
  padding: 8px 12px;
  font-size: 0.7rem;
  color: var(--text-dim);
  cursor: pointer;
  user-select: none;
}
.subtitles-summary:hover { color: var(--text); }
.subtitles-box {
  padding: 12px 14px;
  max-height: 260px;
  overflow-y: auto;
  font-size: 0.78rem;
  line-height: 1.7;
  color: var(--text);
  white-space: pre-wrap;
  border-top: 1px solid var(--border);
}
</style>
