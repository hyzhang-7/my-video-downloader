# 开发日志 — VidFlow 万能视频下载器

## v1.5 — GEO 生成式引擎优化

### 背景

传统 SEO 针对搜索引擎（Google、百度），GEO（Generative Engine Optimization）针对 AI 对话工具（ChatGPT、Perplexity、Google AI Overviews、Bing Copilot）。AI 搜索不只看 meta 标签，更依赖**结构化数据**和**可直接引用的内容**。

### 新增

**llms.txt** (`frontend/public/llms.txt`)
- 符合 [llmstxt.org](https://llmstxt.org) 规范，AI 爬虫专用入口
- 声明站点类型、核心功能、支持平台、JSON-LD 类型
- 指向 llms-full.txt 供 LLM 深度消费

**llms-full.txt** (`frontend/public/llms-full.txt`)
- 全站 Markdown 文档，覆盖 AI 用户常见问题
- FAQ、功能概述、技术架构、隐私政策、开发者指南

**增强 JSON-LD 结构化数据** (`frontend/index.html`)
- 从单一 `WebApplication` 扩展为 `@graph` 多类型：
  - `SoftwareApplication` — 比 WebApplication 更具体，含 featureList
  - `Organization` — 品牌权威，含 logo 和 foundingDate
  - `FAQPage` — 5 个高频问答，AI 搜索引擎优先引用 FAQ 结构化数据生成答案
- 新增 `<noscript>` 兜底内容：不执行 JS 的 AI 爬虫也能看到核心内容

**robots.txt 增强**
- 新增 `LLMs-txt:` 声明，指向 llms.txt

### 未修改

- `App.vue`、`i18n.js`、后端代码 — 零功能影响

---

## v1.2 — AI 视频总结

### 新增

**AI 视频内容分析** (`backend/summarizer.py`)
- 字幕提取：B站使用 `x/v2/dm/view` 公开接口，免 cookies；其他平台走 yt-dlp
- DeepSeek API 集成：生成视频大纲、核心要点、一句话总结
- 思维导图：Markdown 大纲 → `markmap-lib` + `markmap-view` 交互式 SVG
- AI 问答：基于视频字幕内容的上下文对话
- 内存缓存：字幕和总结按 URL 缓存，避免重复请求

**新增 API**
| `/api/subtitles` | POST | 提取视频字幕文本（带时间戳） |
| `/api/summarize` | POST | AI 总结（字幕 + 大纲 + 要点 + 思维导图） |
| `/api/chat` | POST | AI 视频问答 |

**B站免登录字幕**
- B站字幕从 `x/player/wbi/v2`（需登录）迁移至 `x/v2/dm/view`（公开）
- 优先人工中文字幕，降级 AI 中文字幕
- 字幕带时间戳显示，可折叠展开

### 技术栈

新增依赖：`openai`（DeepSeek SDK）、`python-dotenv`（环境变量）、`requests`（B站 API）、`markmap-lib` + `markmap-view`（思维导图）

---

## v1.1 — 抖音免 Cookie + YouTube 下载修复

### 新增

**抖音免 Cookie 下载** (`backend/douyin_extractor.py`)
- 使用 iOS Safari UA 伪装移动端，通过 iesdouyin.com 公开 API 获取视频信息
- 支持多种 URL 格式：`/video/`、`/jingxuan?modal_id=`、`/user/...?modal_id=`
- 内置 WAF 挑战求解器（SHA-256 暴力破解）
- 自动去水印：`playwm` → `play`
- 服务端流式下载，WebSocket 实时推送进度
- **全程无需用户提供 cookies**

**代理自动检测** (`backend/downloader.py`)
- 探测本地常见代理端口（Clash 7890、V2Ray 10809、Shadowsocks 1080 等）
- 仅对 YouTube 启用代理，国内平台不受影响

### 修复

**YouTube 下载 403 / JSON 空文件**
- 切换至 `android_vr` 客户端（无需 PO Token，全格式支持）
- 优先 `bestvideo+bestaudio` 合并高清流（1080p），降级到 `best` 合并格式

**Python 3.9 兼容**
- `str | None` → `Optional[str]`（3.9 不支持 PEP 604 联合类型）

**Cookies 统一处理**
- `_build_ydl_opts()` 统一注入 cookies，解析和下载阶段均生效

### 格式选择策略

```
有 FFmpeg + 用户选格式 → format_id+bestaudio/bestvideo+bestaudio/best
有 FFmpeg + 未选格式   → bestvideo+bestaudio/best（优先高清合并）
无 FFmpeg             → best（单文件含音频）
```

### 技术栈

| 层 | 技术 | 版本 |
|----|------|------|
| 后端 | FastAPI + yt-dlp + httpx | Python 3.9 |
| 前端 | Vue 3 + Vite | — |
| 合并 | FFmpeg（imageio-ffmpeg 降级） | — |
| 代理 | 本地 Clash / V2Ray 自动检测 | — |
