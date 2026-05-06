# 方案设计文档 — VidFlow 万能视频下载器

## 1. 系统架构

```
用户浏览器 (Vue 3 + Vite)
    │
    ├── http://localhost:5173 (dev) / :8000 (prod)
    │
    ▼
FastAPI 后端 (Python)
    ├── /api/info          → yt-dlp 解析视频元信息
    ├── /api/download      → 发起下载任务（server/direct）
    ├── /api/file/{id}     → 提供已下载文件
    ├── /api/thumbnail     → 代理封面图（绕过 Referrer/CORS）
    ├── /api/proxy-download → 直链流式代理下载
    ├── /api/cookies-status → cookies 配置状态
    ├── /api/task/{id}     → 轮询任务状态
    └── /ws/progress/{id}  → WebSocket 实时进度推送
    │
    ▼
yt-dlp (Python API)
    ├── extract_info(download=False) → 元信息
    ├── extract_info(download=True)  → 服务端下载
    └── progress_hooks               → 进度回调
    │
    ▼
downloads/ 目录 (本地文件存储)
```

## 2. 项目结构

```
my-video-downloader/
├── backend/
│   ├── main.py            # FastAPI 应用入口
│   ├── downloader.py      # yt-dlp 封装层
│   └── requirements.txt   # fastapi, uvicorn, yt-dlp, httpx, python-multipart
├── frontend/
│   ├── src/
│   │   ├── App.vue              # 主组件（全部 UI）
│   │   ├── main.js              # Vue 入口
│   │   ├── style.css            # 全局样式 + 设计 Token
│   │   └── composables/
│   │       └── i18n.js          # 中英文切换
│   ├── index.html
│   ├── vite.config.js           # 含 API/WS 代理配置
│   └── package.json
├── downloads/              # 下载文件暂存（gitignore）
├── cookies.txt             # 可选，平台认证 cookies
└── docs/
    ├── requirements.md     # 需求分析文档
    └── design.md           # 方案设计文档（本文件）
```

## 3. 两种下载模式

### 3.1 服务端下载（默认）

```
用户点击下载 → FastAPI 创建任务 → yt-dlp 下载到服务器
→ WebSocket 推送进度 → 完成后提供 /api/file/{id} 链接
```

- **适用**: 需要合并音视频（B站、抖音等）、需要格式转换
- **格式策略**:
  - 有 FFmpeg + 用户选格式: `{format_id}+bestaudio/best`
  - 有 FFmpeg + 未选格式: `best/bestvideo+bestaudio`
  - 无 FFmpeg: `best`（单文件含音频）
- **进度**: progress_hooks → WebSocket 每 0.5s 推送
- **后处理**: postprocessor_hooks 追踪合并状态

### 3.2 直链下载

```
用户点击下载 → 后端提取直链URL → 前端 iframe 加载代理URL
→ 服务端流式拉取直链内容 → Content-Disposition: attachment
→ 浏览器触发下载对话框
```

- **适用**: YouTube 等提供完整直链的平台
- **代理端点**: `GET /api/proxy-download?url=...&filename=...`
- **降级**: 直链不可用时（分段流、m4s 等），前端自动切换服务端模式
- **不弹窗**: 使用隐藏 iframe 触发下载，不用 window.open()

## 4. 核心流程

### 4.1 视频解析

```
POST /api/info { url }
→ extract_info(url, download=False)
→ sanitize_info()
→ 返回 { title, thumbnail, duration, uploader, formats[] }
```

- 过滤掉 mhtml（storyboard 图片）
- 每个 format 含: format_id, ext, resolution, filesize, vcodec, acodec

### 4.2 下载流程

```
POST /api/download { url, format_id?, mode }
→ mode=server: start_download() → task_id → WebSocket 进度
→ mode=direct: get_direct_url() → { direct_url }
   → 无效直链 → 前端降级为 server 模式重试
```

### 4.3 进度推送

```
WebSocket /ws/progress/{task_id}
每 0.5s 推送: { status, progress, speed, title, error, warning }
状态机: starting → downloading → processing → merging → done
                                                      ↘ error
```

## 5. 前端设计

### 5.1 配色方案

参考 awwards 设计风格，暖灰白底 + 深色文字 + 琥珀暖调点缀：

| Token | 值 | 用途 |
|-------|-----|------|
| `--bg` | `#e9e9e9` | 页面背景 |
| `--bg-card` | `#f5f5f5` | 卡片背景 |
| `--text` | `#222222` | 主文字 |
| `--text-dim` | `#717171` | 次要文字 |
| `--primary` | `#222222` | 主按钮（黑底白字） |
| `--accent` | `#e9ad68` | 暖琥珀点缀 |
| `--border` | `rgba(0,0,0,0.08)` | 分割线 |

### 5.2 设计原则

- 直角无边角（0px radius）
- 无渐变、无玻璃态、无点阵背景
- 大写标签（editorial 风格）
- 大量留白
- 移动端响应式

### 5.3 组件结构

单文件 `App.vue`，按步骤切换视图：
```
step=input    → Hero + URL输入框 + 特性展示
step=ready    → 视频信息卡片 + 模式切换 + 清晰度选择
step=downloading → 进度条 + 实时速度
step=done     → 完成提示 + 保存按钮
```

## 6. 关键技术决策

### 6.1 为什么不用数据库

任务状态存在内存 dict，文件存在本地磁盘。下载完成后用户取走文件即可。简单、够用。

### 6.2 为什么前端不分离部署

单个 HTML 文件由 FastAPI serve，开发时 Vite 代理到后端。减少部署复杂度。

### 6.3 FFmpeg 策略

1. 优先检测系统 PATH 中的 ffmpeg
2. 其次使用 `imageio-ffmpeg` 捆绑的二进制
3. 都没有 → 只下载视频流，显示 Warning

### 6.4 Cookies 策略

1. 自动检测项目根目录 `cookies.txt`
2. 存在则所有下载自动附带
3. `/api/cookies-status` 可查询配置状态

### 6.5 封面代理

- `/api/thumbnail?url=...` — 服务端请求封面图
- 自动从目标 URL 提取域名作为 Referer
- 绕过浏览器的 Referrer/CORS 限制

## 7. 已知限制

| 限制 | 说明 |
|------|------|
| 抖音需 cookies | 无 cookies 只能下载视频流（无音频） |
| YouTube SSL | 部分网络环境可能无法连接 |
| 无批量下载 | 当前仅支持单视频下载 |
| 内存任务存储 | 服务重启后任务状态丢失 |

## 8. 启动方式

```bash
# 开发模式
cd backend && uvicorn main:app --port 8000 --reload
cd frontend && npx vite

# 生产模式
cd frontend && npx vite build
cd backend && uvicorn main:app --port 8000
```
