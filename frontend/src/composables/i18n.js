import { ref, computed } from 'vue'

const zh = {
  platforms: '支持 1000+ 平台',
  heroTitle: '粘贴链接，',
  heroSub: '即刻下载。',
  heroDesc: '万能视频下载器。无广告、无限制、不废话。',
  placeholder: '粘贴视频链接 — YouTube、B站、抖音、Twitter…',
  parse: '解析',
  download: '下载',
  newLink: '← 重新输入',
  modeLabel: '下载模式',
  modeServer: '服务端 &mdash; 稳定可靠，支持格式转换',
  modeDirect: '直链 &mdash; 极速直连，无需等待',
  qualityLabel: '清晰度',
  audioOnly: '仅音频 (MP3)',
  formats: '种格式',
  downloading: '正在下载…',
  processing: '正在处理…',
  doneTitle: '下载完成',
  saveFile: '保存到本地',
  feature1Title: '全平台支持',
  feature1Desc: 'YouTube、B站、抖音、Twitter、Instagram 等 1000+ 网站，基于 yt-dlp。',
  feature2Title: '原始画质',
  feature2Desc: '4K、1080p60、HDR — 以最高可用分辨率下载。',
  feature3Title: '零摩擦',
  feature3Desc: '无需注册、无广告、无需排队。粘贴链接，点击下载，完成。',
  footer: '基于 yt-dlp 构建 · 仅供学习使用 · 请尊重版权',
  parseFailed: '解析失败，请检查链接是否有效',
  downloadFailed: '下载请求失败',
  wsFailed: 'WebSocket 连接失败',
  aiSummarize: 'AI 总结',
  aiChat: 'AI 问答',
  aiSummary: '视频总结',
  aiKeyPoints: '核心要点',
  aiMindMap: '思维导图',
  aiAskPlaceholder: '针对视频内容提问…',
  aiSend: '发送',
  aiNoSubtitles: '该视频无可用字幕，暂不支持 AI 总结',
  aiGenerating: 'AI 分析中…',
  aiApiKeyMissing: '请配置 DEEPSEEK_API_KEY',
  aiSubtitles: '字幕原文',
}

const en = {
  platforms: '1,000+ PLATFORMS',
  heroTitle: 'Paste a link.',
  heroSub: 'Download anything.',
  heroDesc: 'The universal video downloader. No ads, no limits, no nonsense.',
  placeholder: 'Paste video URL — YouTube, Bilibili, TikTok, Twitter…',
  parse: 'Parse',
  download: 'Download',
  newLink: '← New link',
  modeLabel: 'DOWNLOAD MODE',
  modeServer: 'Server — reliable, format conversion',
  modeDirect: 'Direct — fast, no server wait',
  qualityLabel: 'QUALITY',
  audioOnly: 'Audio only (MP3)',
  formats: 'formats',
  downloading: 'Downloading…',
  processing: 'Processing…',
  doneTitle: 'Download complete',
  saveFile: 'Save file',
  feature1Title: 'All platforms',
  feature1Desc: 'YouTube, Bilibili, TikTok, Twitter, Instagram — 1,000+ sites supported via yt-dlp.',
  feature2Title: 'Original quality',
  feature2Desc: '4K, 1080p60, HDR — download at the highest available resolution.',
  feature3Title: 'No friction',
  feature3Desc: 'No accounts, no ads, no waiting in queues. Paste link, hit download, done.',
  footer: 'Built on yt-dlp · For educational use · Respect copyright',
  parseFailed: 'Failed to parse link. Check the URL.',
  downloadFailed: 'Download request failed.',
  wsFailed: 'WebSocket connection failed.',
  aiSummarize: 'AI Summarize',
  aiChat: 'AI Chat',
  aiSummary: 'Video Summary',
  aiKeyPoints: 'Key Points',
  aiMindMap: 'Mind Map',
  aiAskPlaceholder: 'Ask about the video…',
  aiSend: 'Send',
  aiNoSubtitles: 'No subtitles available for this video.',
  aiGenerating: 'AI analyzing…',
  aiApiKeyMissing: 'DEEPSEEK_API_KEY not configured',
  aiSubtitles: 'Subtitle Text',
}

const messages = { zh, en }
const locale = ref(localStorage.getItem('locale') || 'zh')

export function useI18n() {
  const t = (key) => {
    return messages[locale.value]?.[key] ?? messages.en[key] ?? key
  }

  function setLocale(lang) {
    locale.value = lang
    localStorage.setItem('locale', lang)
  }

  function toggleLocale() {
    setLocale(locale.value === 'zh' ? 'en' : 'zh')
  }

  const isZh = computed(() => locale.value === 'zh')

  return { locale, t, setLocale, toggleLocale, isZh }
}
