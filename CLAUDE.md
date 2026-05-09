# VidFlow — 万能视频下载器

## 项目定位

从任意平台（YouTube、B站、抖音、Twitter 等 1000+ 网站）下载视频的 Web 工具。支持 **AI 视频总结**（字幕提取 + AI 大纲/要点/思维导图 + AI 问答）、**会员付费**（Stripe 支付 + VIP 权限控制）。轻量、Python 技术栈。

## 技术栈

- **后端**: FastAPI + yt-dlp (Python API) + FFmpeg + DeepSeek API + Stripe SDK + PyJWT
- **前端**: Vue 3 + Vite，单文件组件 `App.vue` + markmap 思维导图
- **存储**: 本地 `downloads/` 目录 + SQLite 数据库（`vidflow.db`）

## 目录结构

```
backend/
  main.py              # FastAPI 应用入口（API + WebSocket + 静态文件）
  downloader.py        # yt-dlp 封装（解析、直链、服务端下载、格式选择、FFmpeg 检测）
  summarizer.py        # AI 总结（字幕提取 + DeepSeek API + 思维导图 + 问答）
  douyin_extractor.py  # 抖音免 Cookie 解析
  database.py          # SQLite 数据库（users, memberships, daily_usage）
  auth.py              # 认证模块（PBKDF2 密码哈希 + JWT + VIP/每日限额检查）
  payment.py           # Stripe 支付（Checkout Session + Webhook + Verify）
  requirements.txt     # fastapi, uvicorn, yt-dlp, openai, requests, stripe, PyJWT
frontend/
  src/
    App.vue            # 主组件（全部 UI 逻辑，含 <script setup> + <style scoped>）
    main.js            # Vue 入口
    style.css          # 全局 CSS 变量（配色、按钮、动画）
    composables/i18n.js  # 中英文切换（reactive locale + t() 函数）
  public/              # 静态资源（Vite 构建直接复制到 dist/）
    favicon.svg        # 网站图标
    og-image.png       # OG 社交分享图（1200x630）
    robots.txt         # 爬虫控制
    sitemap.xml        # 网站地图
  vite.config.js       # 开发代理 /api → :8000, /ws → ws://:8000
docs/
  requirements.md      # 需求分析文档
  design.md            # 方案设计文档
downloads/             # 下载文件暂存（gitignore）
cookies.txt            # 可选，Netscape 格式 cookies（gitignore）
```

## 启动方式

```bash
# 开发
cd backend  && uvicorn main:app --port 8000 --reload
cd frontend && npx vite                     # http://localhost:5173

# 生产
cd frontend && npx vite build
cd backend  && uvicorn main:app --port 8000  # http://localhost:8000
```

## 核心 API

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/info` | POST | 解析视频链接，返回标题/封面/格式列表 |
| `/api/download` | POST | 发起下载（mode: server/direct） |
| `/api/file/{task_id}` | GET | 提供已下载文件 |
| `/api/thumbnail?url=...` | GET | 封面代理（绕过 Referrer/CORS） |
| `/api/proxy-download?url=...&filename=...` | GET | 直链流式代理下载 |
| `/api/task/{task_id}` | GET | 轮询任务状态 |
| `/api/cookies-status` | GET | 查询 cookies.txt 是否配置 |
| `/api/subtitles` | POST | 提取视频字幕（B站免登公开 API） |
| `/api/subtitles/download?url=...` | GET | 下载字幕文件（SRT 格式，B站/VTT 自动转换） |
| `/api/summarize` | POST | AI 视频总结（**VIP 限定**，10 次/天，字幕 + 大纲 + 要点 + 思维导图） |
| `/api/chat` | POST | AI 视频问答（**VIP 限定**，10 次/天，非流式） |
| `/api/chat/stream` | POST | AI 视频问答流式输出（**VIP 限定**，SSE，逐 token 返回） |
| `/api/auth/register` | POST | 邮箱+密码注册 |
| `/api/auth/login` | POST | 登录，返回 JWT token（72h 有效） |
| `/api/auth/me` | GET | 获取当前用户信息 + VIP 状态 + 今日使用次数 |
| `/api/payment/create-checkout` | POST | 创建 Stripe Checkout Session，返回支付 URL |
| `/api/payment/verify` | POST | 同步验证支付状态并激活 VIP（前端从 Stripe 跳回时调用） |
| `/api/webhook/stripe` | POST | Stripe Webhook 接收（签名验证 + checkout.session.completed） |
| `/ws/progress/{task_id}` | WS | 实时进度推送（0.5s 间隔） |

## 下载模式

1. **服务端下载（默认）** — yt-dlp 下载到 downloads/，WebSocket 推送进度，完成后提供文件链接
2. **直链下载** — 后端提取直链 URL，前端通过隐藏 iframe 代理下载，不弹窗。直链不可用时自动降级

## 会员系统

### 定价与模式
- **¥9.90/30天**，一次性购买，到期手动续费，非自动订阅
- 货币 CNY，`STRIPE_CURRENCY = "cny"`, `PRICE_UNIT_AMOUNT = 990`（fen）
- Stripe Checkout 模式: `mode="payment"`（非 `subscription`）

### 权限控制

| 功能 | 免费用户 | VIP 用户 |
|------|---------|---------|
| AI 总结 (`/api/summarize`) | 403 禁止 | 10 次/天，超限 429 |
| AI 问答 (`/api/chat`) | 403 禁止 | 10 次/天（与总结共享配额） |
| 视频清晰度 | 最高 720p（>720p 格式锁定，前端加锁图标） | 无限制 |

### 数据库表（SQLite, `vidflow.db`）

- **users**: `id, email (UNIQUE), password_hash, is_admin, created_at`
- **memberships**: `id, user_id (UNIQUE FK), status (active/expired/cancelled), start_date, end_date, amount, stripe_session_id (UNIQUE), created_at` — 一个用户一条记录，续费 UPDATE
- **daily_usage**: `id, user_id (FK), usage_date, count`, `UNIQUE(user_id, usage_date)` — AI 每日次数 upsert

### 认证
- PBKDF2-SHA256 密码哈希（60 万次迭代, 16 字节 salt）
- JWT HS256，`sub` 必须为 `str`（PyJWT 要求），72 小时过期
- `get_current_user` / `get_optional_user` 两个 FastAPI 依赖，前者 401，后者返回 None
- `check_vip(user_id)` 查询有效会员（`status='active' AND end_date >= now`）
- `check_daily_limit(user_id, limit)` 检查 + 原子递增每日使用次数

### 支付流程
```
用户点击"升级VIP" → POST /api/payment/create-checkout（需登录）
→ 前端 window.location.href = checkout_url（跳转 Stripe 支付页）
→ 用户付款 → Stripe 重定向到 FRONTEND_URL?session_id=cs_test_xxx
→ 前端 onMounted → checkStripeReturn()
→ POST /api/payment/verify（同步验证，幂等）
→ 后端 stripe.checkout.Session.retrieve() → 验证 user_id 匹配 → _fulfill_vip()
→ 同时 Webhook（备选通道）→ /api/webhook/stripe → 签名验证 → _fulfill_vip()
```

### Stripe 开发坑点（重要）

- **StripeObject ≠ dict**: `session.metadata` 是 StripeObject，不支持 `.get()`，必须用 `.to_dict().get()` 或 `[]`
- **Webhook Secret 每次变化**: `stripe listen` 每次运行生成新的 `whsec_xxx`，需同步更新 `.env`
- **本地测试**: 需要 `stripe login` + `stripe listen --forward-to localhost:8000/api/webhook/stripe`
- **`sub` 类型**: PyJWT 强制 `sub` 为 `str`，创建时 `str(user_id)`，解析后 `int(payload["sub"])`
- **幂等性**: `stripe_session_id` 设 UNIQUE 约束，`_fulfill_vip()` 先查再插；Session 创建时传 `idempotency_key`

### 环境变量（`.env`）

```
STRIPE_SECRET_KEY=sk_test_xxx
STRIPE_WEBHOOK_SECRET=whsec_xxx   # 每次 stripe listen 都会变
VIP_PRICE_CNY=9.90
VIP_DURATION_DAYS=30
JWT_SECRET=xxx                    # 至少 32 字符
FRONTEND_URL=http://localhost:5173  # Stripe 支付后跳回地址
```

## 关键逻辑

### 格式选择（downloader.py `start_download`）
```
用户选了 format_id + 有 FFmpeg → format_id+bestaudio/best （合并音视频）
用户选了 format_id + 无 FFmpeg → format_id （仅该流）
未选 + 有 FFmpeg           → best/bestvideo+bestaudio
未选 + 无 FFmpeg           → best
```

### FFmpeg 检测
1. 系统 PATH 中查找 `ffmpeg`
2. `imageio-ffmpeg` 捆绑的二进制 (`get_ffmpeg_exe()`)
3. 都没有 → 只下载视频流 + 显示 Warning

### Cookies
自动检测项目根目录 `cookies.txt`，存在则所有下载自动附带。抖音等平台需要 cookies 才能下载音频。

### 封面代理
`/api/thumbnail` 从目标 URL 提取域名作为 Referer，绕过浏览器的同源/Referrer 限制。

### 直链有效性
返回空直链的情况：`.m4s` 分段流、video-only 流、audio-only 流、格式不可用。前端收到空直链自动切换服务端模式。

### B站特殊处理
yt-dlp 内置的 B站提取器对 API 请求（`api.bilibili.com/x/player/*`）返回 403。**解析和下载都不走 yt-dlp**，改为 `requests` 库直接调用 B站公开 API：
- `x/web-interface/view` — 获取 title/封面/cid/分P列表
- `x/player/playurl` — 获取格式列表 + CDN 直链（`fnval=1` 单文件 mp4）
- CDN 下载用 `requests` 流式拉取 + 手动进度追踪，**完全绕过 yt-dlp**
- 代理下载 `/api/proxy-download` 对 `bilivideo.com` CDN 自动设置 `Referer: https://www.bilibili.com/`

### 下载进度
状态机: `starting → downloading → processing → merging → done`（出错则 → `error`）
B站跳过 `merging`（durl 单文件已含音频），直接 `downloading → done`。

## 前端设计

暖灰白底浅色风格（参考 awwards + Apple），胶囊按钮 + 圆角卡片，无渐变：

- `--bg: #e9e9e9`, `--bg-card: #f5f5f5`
- `--text: #222222`, `--text-dim: #222222`（同色，靠字重区分层次 — 主文字 500-700，次要 300-400）
- `--primary: #222222`（黑底白字按钮）
- `--accent: #e9ad68`（暖琥珀点缀）
- `--radius: 100px`（胶囊按钮/输入框），`--radius-sm: 14px`（内容卡片）
- border: `rgba(0,0,0,0.08)`
- 基础字号 `18px`，按钮胶囊形（border-radius: 100px）
- Logo: 纯文字 `VidFlow`，favicon.svg（黑底 VF 字母标）
- Hero 区域：仅标题"粘贴链接，即刻下载。"，下方留白 60px 居中输入框
- 特性卡片：无外框，竖线分隔（响应式：移动端横线分隔）
- AI 面板：无关闭按钮（× 已移除）
- i18n 通过 `useI18n()` composable，localStorage 持久化语言选择

## AI 视频总结

### 流程
```
B站: bvid → x/web-interface/view → aid+cid → x/v2/dm/view → subtitle_url → 字幕 JSON
其他: yt-dlp → extract_info → VTT 文件 → 解析纯文本
→ DeepSeek API → 结构化输出（大纲 + 要点 + 思维导图 + 问答）
```

### B站字幕（免登录）
- `x/v2/dm/view` 接口公开访问，无需 cookies
- 优先人工字幕（`lan=zh`），降级 AI 字幕（`lan=ai-zh`）
- 字幕缓存于内存，同 URL 不重复提取

### DeepSeek 集成
- 使用 OpenAI 兼容 SDK，`base_url="https://api.deepseek.com"`
- API Key 从项目根目录 `.env` 文件读取 `DEEPSEEK_API_KEY`
- 非流式调用，`temperature=0.7`，max_tokens=4096

### 前端展示
- 一句话总结 + 核心要点（列表）+ 字幕原文（可折叠，带时间戳）+ 字幕下载按钮
- 思维导图：`markmap-lib` + `markmap-view` 渲染 Markdown → 交互式 SVG
- 思维导图全屏模式（Teleport 全屏浮层，Esc 关闭）
- 思维导图下载 PNG：读取 `mm.state.rect`（markmap 内部节点坐标边界）计算 viewBox，移除 d3-zoom transform 后序列化 SVG → canvas → PNG，确保导出完整内容而非当前缩放区域
- AI 问答：聊天界面，对话历史保留最近 10 轮
- AI 面板在下载阶段（`step !== 'input'`）也可用，新 URL 解析时自动重置

### 字幕下载
- B站字幕自动转为 SRT 格式（`_bilibili_segments_to_srt`）
- yt-dlp VTT 字幕自动转为 SRT（`_vtt_to_srt`），降级保留 VTT
- 原始字幕缓存于 `_subtitle_raw_cache`，同 URL 不重复提取

## SEO 搜索引擎优化

生产域名: `https://vidflow.cn`

### TDK（index.html 静态标签，搜索引擎直接读取）

- **Title**: `VidFlow - 万能视频下载器 | 在线视频下载，支持YouTube、B站、抖音等1000+网站`（49 字符）
- **Description**: 覆盖产品功能、目标用户、核心价值，150+ 字符
- **Keywords**: 视频下载, 在线视频下载, YouTube下载, B站下载, 抖音下载, 万能视频下载器, AI视频总结, VidFlow

### Meta 标签

| 类型 | 标签 | 用途 |
|------|------|------|
| OG | og:title/description/image/type/url/locale/site_name | 社交分享（微信、Facebook、Twitter） |
| Twitter Card | twitter:card/title/description/image | Twitter 分享（summary_large_image） |
| Schema.org | itemprop + JSON-LD（SoftwareApplication + Organization + FAQPage） | 结构化数据，争取 Rich Snippet + AI 引用 |
| 爬虫 | robots meta + canonical + robots.txt + sitemap.xml | 索引控制 + 重复内容规避 |

### 静态资源（frontend/public/）

| 文件 | 说明 |
|------|------|
| `favicon.svg` | SVG 网站图标 |
| `og-image.png` | 1200x630 分享图，暖灰底 + VidFlow 品牌文字 |
| `robots.txt` | 允许全部爬虫，指向 sitemap + LLMs-txt |
| `sitemap.xml` | 网站地图，首页 URL |
| `llms.txt` | AI 爬虫入口，站点摘要 + 结构声明（llmstxt.org 规范） |
| `llms-full.txt` | 全站 Markdown 文档，供 LLM 深度消费 |

### GEO 生成式引擎优化（v1.5）

针对 ChatGPT、Perplexity、Google AI Overviews、Bing Copilot 等 AI 搜索工具优化：

- **llms.txt / llms-full.txt**：符合 [llmstxt.org](https://llmstxt.org) 规范，LLM 爬虫专用入口
- **JSON-LD @graph**：SoftwareApplication + Organization + FAQPage（5 个高频问答），替代单一 WebApplication
- **`<noscript>` 兜底**：index.html 含 noscript 内容，不执行 JS 的爬虫也能看到核心描述
- **robots.txt**：`LLMs-txt:` 声明指向 llms.txt

### 图片 SEO

- `App.vue` 视频缩略图 `alt` 动态绑定 `videoInfo.title`
- OG 图片使用绝对 URL `https://vidflow.cn/og-image.png`

### 上线后任务

- [ ] Google Search Console 提交 sitemap
- [ ] 百度站长平台提交 sitemap
- [ ] Facebook Sharing Debugger 验证 OG 标签
- [ ] Google Rich Results Test 验证结构化数据（FAQ + SoftwareApp）
- [ ] 验证 llms.txt 可被主流 AI 爬虫访问（OpenAI GPTBot、Anthropic Claude-Web）

## 已知限制

- B站解析/下载完全绕过 yt-dlp，用 `requests` 直调公开 API（规避 403）
- 抖音需 cookies 才能下载音频（视频流不需要）
- 任务状态存在内存，服务重启后丢失
- YouTube 部分网络环境可能 SSL 失败
- AI 总结的 DeepSeek API 需联网，B站字幕免登
- Stripe 本地测试需保持 `stripe listen` 运行（Webhook 转发），生产环境需部署公网 Webhook 端点
- Stripe Webhook Secret 每次运行 `stripe listen` 都会变化，需同步更新 `.env`
- `STRIPE_CURRENCY = "cny"` 仅部分 Stripe 账户支持，如不可用需改为 `"usd"`
