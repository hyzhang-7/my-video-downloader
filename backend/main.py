"""
FastAPI application: video download API + WebSocket progress + static serve.
"""
import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

import httpx
from fastapi import FastAPI, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from urllib.parse import urlparse

from database import init_db, get_db
from auth import (
    hash_password,
    verify_password,
    create_token,
    get_current_user,
    get_optional_user,
    check_vip,
    check_daily_limit,
)
from payment import create_checkout_session, handle_webhook, verify_and_fulfill

from downloader import DOWNLOADS_DIR, DEFAULT_COOKIES, extract_info, get_direct_url, start_download, tasks
from douyin_extractor import (
    extract_info_douyin,
    get_direct_url_douyin,
    start_download_douyin,
    douyin_tasks,
    extract_video_id as is_douyin_url,
)
from summarizer import extract_subtitles, generate_summary, chat_with_video, chat_with_video_stream, get_subtitle_download

app = FastAPI(title="Video Downloader")

# Initialize database on startup
init_db()

# CORS for Vue dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request models ──────────────────────────────────────────────

class InfoRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    format_id: Optional[str] = None
    mode: str = "server"  # "server" | "direct"
    cookies_file: Optional[str] = None  # path to cookies.txt for platforms that need auth


class SummarizeRequest(BaseModel):
    url: str
    cookies_source: Optional[str] = None  # browser name or path to cookies.txt


class ChatRequest(BaseModel):
    url: str
    question: str
    history: list = []
    cookies_source: Optional[str] = None


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class VerifyRequest(BaseModel):
    session_id: str


# ── Thumbnail proxy ─────────────────────────────────────────────

def _filter_formats_for_free(formats: list) -> list:
    """Mark formats above 720p as locked for free users."""
    for f in formats:
        height = _parse_height(f)
        if height and height > 720:
            f["locked"] = True
    return formats


def _parse_height(f: dict) -> Optional[int]:
    """Extract video height from format dict. Returns int or None."""
    res = f.get("resolution", "")
    # e.g. "1920x1080" -> 1080
    m = re.search(r"(\d+)x(\d+)", res)
    if m:
        return int(m.group(2))
    note = f.get("note", "")
    m2 = re.search(r"(\d+)p", note)
    if m2:
        return int(m2.group(1))
    return None

@app.get("/api/thumbnail")
async def api_thumbnail(url: str = Query(...)):
    """Proxy thumbnail images to bypass referrer/CORS restrictions."""
    # Derive Referer from the thumbnail's own origin
    parsed = urlparse(url)
    referer = f"{parsed.scheme}://{parsed.netloc}/"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": referer,
        "Origin": referer.rstrip("/"),
    }

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code >= 400:
                raise HTTPException(status_code=404, detail="Thumbnail not available")
            return Response(
                content=resp.content,
                media_type=resp.headers.get("content-type", "image/jpeg"),
            )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="Thumbnail not available")


# ── API routes ──────────────────────────────────────────────────

@app.get("/api/cookies-status")
def api_cookies_status():
    """Check if cookies.txt is configured."""
    return {
        "ok": True,
        "configured": DEFAULT_COOKIES.exists(),
        "path": str(DEFAULT_COOKIES),
    }


# ── Auth routes ──────────────────────────────────────────────────

@app.post("/api/auth/register")
def api_register(req: RegisterRequest):
    """Register a new user account."""
    email = req.email.strip().lower()
    password = req.password.strip()

    if not email or "@" not in email or len(email) > 200:
        raise HTTPException(status_code=400, detail="请输入有效的邮箱地址")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    if len(password) > 128:
        raise HTTPException(status_code=400, detail="密码过长")

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="该邮箱已注册")

    db.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        (email, hash_password(password)),
    )
    db.commit()
    return {"ok": True, "message": "注册成功"}


@app.post("/api/auth/login")
def api_login(req: LoginRequest):
    """Login with email + password, return JWT token."""
    email = req.email.strip().lower()
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")

    token = create_token(user["id"], user["email"], bool(user["is_admin"]))
    return {
        "ok": True,
        "token": token,
        "user": {"id": user["id"], "email": user["email"]},
    }


@app.get("/api/auth/me")
def api_me(user: dict = Depends(get_current_user)):
    """Get current user info + VIP status."""
    vip = check_vip(user["id"])
    db = get_db()
    today_usage = db.execute(
        "SELECT count FROM daily_usage WHERE user_id = ? AND usage_date = ?",
        (user["id"], datetime.now(timezone.utc).strftime("%Y-%m-%d")),
    ).fetchone()

    return {
        "ok": True,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "vip": {
                "active": vip is not None,
                "end_date": vip["end_date"] if vip else None,
            } if vip else None,
            "daily_usage": today_usage["count"] if today_usage else 0,
        },
    }


# ── Payment routes ───────────────────────────────────────────────

@app.post("/api/payment/create-checkout")
def api_create_checkout(user: dict = Depends(get_current_user)):
    """Create Stripe Checkout Session for VIP purchase."""
    result = create_checkout_session(user["id"], user["email"])
    return {"ok": True, "checkout_url": result["checkout_url"]}


@app.post("/api/payment/verify")
def api_verify_payment(req: VerifyRequest, user: dict = Depends(get_current_user)):
    """Verify Stripe payment and activate VIP (sync fallback for webhook)."""
    try:
        result = verify_and_fulfill(user["id"], req.session_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/webhook/stripe")
async def api_stripe_webhook(request: Request):
    """Receive Stripe webhook events."""
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")
    try:
        result = handle_webhook(payload, sig_header)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── API routes ──────────────────────────────────────────────────

@app.post("/api/info")
def api_info(req: InfoRequest, user: Optional[dict] = Depends(get_optional_user)):
    """Extract video metadata and available formats."""
    try:
        if is_douyin_url(req.url):
            info = extract_info_douyin(req.url)
        else:
            info = extract_info(req.url)

        # Free users: mark formats above 720p as locked
        is_vip = user and check_vip(user["id"])
        if not is_vip:
            info["formats"] = _filter_formats_for_free(info["formats"])
            info["quality_limited"] = True
        else:
            info["quality_limited"] = False

        return {"ok": True, "data": info}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/download")
def api_download(req: DownloadRequest, user: Optional[dict] = Depends(get_optional_user)):
    """Start a download task (server mode) or return direct URL."""
    # Free users: reject formats above 720p
    if req.format_id and not (user and check_vip(user["id"])):
        # Parse video info to check format resolution
        try:
            if is_douyin_url(req.url):
                info = extract_info_douyin(req.url)
            else:
                info = extract_info(req.url)
            target = next((f for f in info.get("formats", []) if f["format_id"] == req.format_id), None)
            if target:
                height = _parse_height(target)
                if height and height > 720:
                    raise HTTPException(status_code=403, detail="免费用户最高支持 720p 清晰度，请升级 VIP")
        except HTTPException:
            raise
        except Exception:
            pass  # If we can't check, allow the download attempt

    is_dy = is_douyin_url(req.url)

    if req.mode == "direct":
        try:
            if is_dy:
                info = get_direct_url_douyin(req.url, req.format_id)
            else:
                info = get_direct_url(req.url, req.format_id)
            return {"ok": True, "mode": "direct", "data": info}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Server-side download
    try:
        if is_dy:
            task_id = start_download_douyin(req.url, req.format_id)
        else:
            task_id = start_download(req.url, req.format_id, cookies_file=req.cookies_file)
        return {"ok": True, "mode": "server", "task_id": task_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/file/{task_id}")
def api_file(task_id: str):
    """Serve downloaded file."""
    t = tasks.get(task_id) or douyin_tasks.get(task_id)
    if not t or t["status"] != "done":
        raise HTTPException(status_code=404, detail="File not ready or not found")

    filepath = t.get("filename", "")
    if not filepath or not Path(filepath).exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    filename = Path(filepath).name
    return FileResponse(filepath, filename=filename, media_type="application/octet-stream")


@app.get("/api/task/{task_id}")
def api_task(task_id: str):
    """Poll task status (alternative to WebSocket)."""
    t = tasks.get(task_id) or douyin_tasks.get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True, "data": t}


# ── AI summarization routes ──────────────────────────────────────

@app.post("/api/subtitles")
def api_subtitles(req: SummarizeRequest):
    """Extract subtitles from a video URL."""
    try:
        result = extract_subtitles(req.url, cookies_source=req.cookies_source)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/subtitles/download")
def api_subtitles_download(url: str = Query(...)):
    """Download subtitle file (SRT/VTT)."""
    try:
        content, filename, media_type = get_subtitle_download(url)
        safe_name = filename.replace('"', '').replace("'", "")
        return Response(
            content=content.encode("utf-8"),
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{safe_name}"',
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/summarize")
def api_summarize(req: SummarizeRequest, user: dict = Depends(get_current_user)):
    """Generate AI summary for a video (outline + key points + one-liner)."""
    # VIP check
    vip = check_vip(user["id"])
    if not vip:
        raise HTTPException(status_code=403, detail="AI 总结仅限 VIP 会员使用，请升级 VIP")
    if not check_daily_limit(user["id"], 10):
        raise HTTPException(status_code=429, detail="今日 AI 总结次数已用完（10次/天），请明天再试")

    try:
        result = generate_summary(req.url, cookies_source=req.cookies_source)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("message", "总结失败"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/chat")
def api_chat(req: ChatRequest, user: dict = Depends(get_current_user)):
    """Chat with AI about a video's content."""
    # VIP check
    vip = check_vip(user["id"])
    if not vip:
        raise HTTPException(status_code=403, detail="AI 问答仅限 VIP 会员使用，请升级 VIP")
    if not check_daily_limit(user["id"], 10):
        raise HTTPException(status_code=429, detail="今日 AI 问答次数已用完（10次/天），请明天再试")

    try:
        result = chat_with_video(req.url, req.question, req.history, cookies_source=req.cookies_source)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("answer", "问答失败"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/chat/stream")
async def api_chat_stream(req: ChatRequest, user: dict = Depends(get_current_user)):
    """Streaming AI chat — Server-Sent Events."""
    vip = check_vip(user["id"])
    if not vip:
        raise HTTPException(status_code=403, detail="AI 问答仅限 VIP 会员使用")
    if not check_daily_limit(user["id"], 10):
        raise HTTPException(status_code=429, detail="今日 AI 问答次数已用完（10次/天）")

    async def generate():
        for chunk in chat_with_video_stream(req.url, req.question, req.history, cookies_source=req.cookies_source):
            yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── WebSocket ───────────────────────────────────────────────────

@app.websocket("/ws/progress/{task_id}")
async def ws_progress(ws: WebSocket, task_id: str):
    await ws.accept()
    try:
        while True:
            t = tasks.get(task_id) or douyin_tasks.get(task_id, {})
            await ws.send_text(json.dumps({
                "status": t.get("status", "unknown"),
                "progress": t.get("progress", 0),
                "speed": t.get("speed", ""),
                "title": t.get("title", ""),
                "error": t.get("error", ""),
                "warning": t.get("warning", ""),
            }))
            if t.get("status") in ("done", "error"):
                break
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass


# ── Direct URL proxy download ──────────────────────────────────


@app.get("/api/proxy-download")
async def api_proxy_download(url: str = Query(...), filename: str = Query("video")):
    """Stream a direct URL to the client as a download (browser-native, no popup)."""
    parsed = urlparse(url)
    # Bilibili CDN requires bilibili.com Referer, not the CDN domain itself
    if "bilivideo.com" in parsed.netloc or "mcdn.bilivideo.cn" in parsed.netloc:
        referer = "https://www.bilibili.com/"
    else:
        referer = f"{parsed.scheme}://{parsed.netloc}/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": referer,
        "Origin": "https://www.bilibili.com" if "bilivideo" in parsed.netloc else referer.rstrip("/"),
    }

    try:
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code >= 400:
                raise HTTPException(status_code=404, detail="Download source not available")
            content_type = resp.headers.get("content-type", "application/octet-stream")
            ext = content_type.split("/")[-1].split(";")[0]
            safe_name = filename.replace('"', '').replace("'", "")
            return StreamingResponse(
                resp.aiter_bytes(),
                media_type=content_type,
                headers={
                    "Content-Disposition": f'attachment; filename="{safe_name}.{ext}"',
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Serve frontend (production) ─────────────────────────────────

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
