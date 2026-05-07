# 开发日志 — VidFlow 万能视频下载器

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
