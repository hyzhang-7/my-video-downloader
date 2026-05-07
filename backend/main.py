"""
FastAPI application: video download API + WebSocket progress + static serve.
"""
import asyncio
import json
import time
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

import httpx
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from urllib.parse import urlparse

from downloader import DOWNLOADS_DIR, DEFAULT_COOKIES, extract_info, get_direct_url, start_download, tasks
from douyin_extractor import (
    extract_info_douyin,
    get_direct_url_douyin,
    start_download_douyin,
    douyin_tasks,
    extract_video_id as is_douyin_url,
)

app = FastAPI(title="Video Downloader")

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


# ── Thumbnail proxy ─────────────────────────────────────────────

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


@app.post("/api/info")
def api_info(req: InfoRequest):
    """Extract video metadata and available formats."""
    try:
        if is_douyin_url(req.url):
            info = extract_info_douyin(req.url)
        else:
            info = extract_info(req.url)
        return {"ok": True, "data": info}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/download")
def api_download(req: DownloadRequest):
    """Start a download task (server mode) or return direct URL."""
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
    referer = f"{parsed.scheme}://{parsed.netloc}/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": referer,
        "Origin": referer.rstrip("/"),
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
