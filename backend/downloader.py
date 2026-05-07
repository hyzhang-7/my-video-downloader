"""
yt-dlp wrapper: extract info, direct URLs, and server-side download with progress.
"""
import re
import shutil
import uuid
from pathlib import Path
from typing import Optional

import requests
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

PROJECT_DIR = Path(__file__).resolve().parent.parent
DOWNLOADS_DIR = PROJECT_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)
DEFAULT_COOKIES = PROJECT_DIR / "cookies.txt"

# In-memory task store
tasks: dict[str, dict] = {}

_ffmpeg_path: Optional[str] = None
_proxy_url: Optional[str] = None  # cached proxy detection result


def _detect_proxy() -> Optional[str]:
    """Auto-detect local proxy (Clash, V2Ray, etc.) by probing common ports.
    Returns proxy URL like 'http://127.0.0.1:7890' or None."""
    global _proxy_url
    if _proxy_url is not None:
        return _proxy_url or None

    import socket
    candidates = [
        ("http", 7890),   # Clash HTTP
        ("socks5", 7891),  # Clash SOCKS5
        ("http", 10809),   # V2Ray / Clash Verge HTTP
        ("socks5", 10808),  # V2Ray / Clash Verge SOCKS5
        ("socks5", 1080),   # Shadowsocks
        ("http", 8888),     # Generic HTTP
    ]
    for scheme, port in candidates:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.3)
            if s.connect_ex(('127.0.0.1', port)) == 0:
                s.close()
                _proxy_url = f"{scheme}://127.0.0.1:{port}"
                return _proxy_url
            s.close()
        except Exception:
            pass

    _proxy_url = ""
    return None


def _get_ffmpeg_path() -> Optional[str]:
    """Find ffmpeg binary: system PATH > imageio_ffmpeg bundle."""
    global _ffmpeg_path
    if _ffmpeg_path is not None:
        return _ffmpeg_path or None

    # 1. Check system PATH
    found = shutil.which("ffmpeg")
    if found:
        _ffmpeg_path = found
        return found

    # 2. Try imageio_ffmpeg bundled binary
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe:
            _ffmpeg_path = exe
            return exe
    except Exception:
        pass

    _ffmpeg_path = ""
    return None


def _has_ffmpeg() -> bool:
    return _get_ffmpeg_path() is not None


def _build_ydl_opts(outtmpl: Optional[str] = None, *, quiet: bool = True, cookies_file: Optional[str] = None, url: str = "", extra_headers: Optional[dict] = None) -> dict:
    opts: dict = {
        "quiet": quiet,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        # Use android_vr client to avoid PO Token / SABR streaming issues on YouTube
        "extractor_args": {"youtube": {"player_client": ["android_vr", "android"]}},
    }
    if outtmpl:
        opts["outtmpl"] = outtmpl
    effective_cookies = cookies_file or (DEFAULT_COOKIES if DEFAULT_COOKIES.exists() else None)
    if effective_cookies:
        opts["cookiefile"] = str(effective_cookies)
    if url and ("youtube.com" in url or "youtu.be" in url):
        proxy = _detect_proxy()
        if proxy:
            opts["proxy"] = proxy
    if extra_headers:
        opts["http_headers"] = dict(extra_headers)
    return opts


def _normalize_url(url: str) -> str:
    """Convert platform-specific URL formats to yt-dlp compatible patterns."""
    from urllib.parse import urlparse, parse_qs

    parsed = urlparse(url)
    host = parsed.netloc.replace("www.", "")

    # Douyin: /jingxuan?modal_id=123  or  /user/xxx?modal_id=123  →  /video/123
    if host == "douyin.com":
        qs = parse_qs(parsed.query)
        modal_id = qs.get("modal_id", [None])[0]
        if modal_id:
            return f"https://www.douyin.com/video/{modal_id}"
        # Also handle /user/username path without modal_id
        if "/user/" in parsed.path and "/video/" not in url:
            pass  # user profile page, not a video — leave as-is

    return url


# Bilibili public API helpers — avoids yt-dlp extractor 403 issues
BILIBILI_HEADERS_TEMPLATE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": "https://www.bilibili.com",
}

BILIBILI_VIDEO_RE = re.compile(r"bilibili\.com/video/(BV[a-zA-Z0-9]+)")

def _is_bilibili_url(url: str) -> bool:
    return bool(BILIBILI_VIDEO_RE.search(url))

def _get_bilibili_headers(bvid: str) -> dict:
    return {**BILIBILI_HEADERS_TEMPLATE, "Referer": f"https://www.bilibili.com/video/{bvid}"}

def _extract_bilibili_info(url: str) -> dict:
    """Fetch Bilibili video metadata via public APIs (requests)."""
    m = BILIBILI_VIDEO_RE.search(url)
    if not m:
        raise ValueError("无法解析 Bilibili 视频 ID")
    bvid = m.group(1)
    headers = _get_bilibili_headers(bvid)

    # Step 1: Get video info
    r = requests.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", headers=headers, timeout=15)
    r.raise_for_status()
    view = r.json()
    if view.get("code") != 0:
        raise RuntimeError(view.get("message", "B站 API 返回错误"))

    data = view["data"]
    title = data.get("title", "")
    thumbnail = data.get("pic", "")
    duration = data.get("duration")
    uploader = data.get("owner", {}).get("name", "")

    # Step 2: Get available formats via playurl
    cid = data.get("cid") or (data.get("pages", [{}])[0].get("cid", 0))
    r2 = requests.get("https://api.bilibili.com/x/player/playurl",
        headers=headers,
        params={"bvid": bvid, "cid": cid, "qn": 0, "fnval": 4048, "fourk": 1},
        timeout=15)
    r2.raise_for_status()
    playurl = r2.json()
    playurl_data = playurl.get("data", {}) if playurl.get("code") == 0 else {}

    # Build formats from support_formats
    formats = []
    for f in playurl_data.get("support_formats", []):
        quality = f.get("quality", 0)
        desc = f.get("new_description", str(quality))
        formats.append({
            "format_id": str(quality),
            "ext": "mp4",
            "resolution": desc,
            "filesize": None,
            "vcodec": "",
            "acodec": "",
            "note": desc,
        })

    return {
        "title": title,
        "thumbnail": thumbnail,
        "duration": duration,
        "uploader": uploader,
        "webpage_url": url,
        "formats": formats,
        "direct_url_available": False,
        "_bvid": bvid,
        "_cid": cid,
    }

def extract_info(url: str) -> dict:
    """Fetch video metadata + available formats without downloading."""
    url = _normalize_url(url)

    # Bilibili: use public API to avoid yt-dlp extractor 403
    if _is_bilibili_url(url):
        return _extract_bilibili_info(url)

    with YoutubeDL(_build_ydl_opts(url=url)) as ydl:
        info = ydl.extract_info(url, download=False)
        info = ydl.sanitize_info(info)

    formats = []
    for f in info.get("formats", []):
        # Skip storyboard images, mhtml, and formats without proper codec
        if f.get("ext") in ("mhtml",):
            continue
        if not f.get("resolution") and not f.get("format_note"):
            continue
        formats.append({
            "format_id": f["format_id"],
            "ext": f.get("ext", ""),
            "resolution": f.get("resolution") or f.get("format_note", ""),
            "filesize": f.get("filesize") or f.get("filesize_approx"),
            "vcodec": f.get("vcodec", ""),
            "acodec": f.get("acodec", ""),
            "note": f.get("format_note", ""),
        })

    return {
        "title": info.get("title", ""),
        "thumbnail": info.get("thumbnail", ""),
        "duration": info.get("duration"),
        "uploader": info.get("uploader", ""),
        "webpage_url": info.get("webpage_url", url),
        "formats": formats,
        "direct_url_available": _has_direct_url(info),
    }


def _has_direct_url(info: dict) -> bool:
    """Check if any format has a direct http(s) URL."""
    for f in info.get("formats", []):
        if f.get("url") and f["url"].startswith("http"):
            return True
    return False


def get_direct_url(url: str, format_id: Optional[str] = None) -> dict:
    """Extract direct stream URL (no server-side download).
    Returns empty direct_url when a single combined stream isn't available.
    """
    url = _normalize_url(url)

    # Bilibili: use public API
    if _is_bilibili_url(url):
        bvid = BILIBILI_VIDEO_RE.search(url).group(1)
        headers = _get_bilibili_headers(bvid)
        try:
            r = requests.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", headers=headers, timeout=15)
            r.raise_for_status()
            view = r.json()["data"]
            cid = view.get("cid") or (view.get("pages", [{}])[0].get("cid", 0))
            dl_url, dl_ext = _get_bilibili_download_url(bvid, cid)
            return {"direct_url": dl_url, "title": view.get("title", ""), "ext": dl_ext}
        except Exception:
            return {"direct_url": "", "title": "", "ext": ""}

    fmt = format_id or "bestvideo+bestaudio/best"
    opts = _build_ydl_opts(url=url)
    opts["format"] = fmt

    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        selected_url = info.get("url", "")

        # Detect partial/segmented streams that can't be used as a standalone file
        if selected_url:
            ext = info.get("ext", "")
            if ext in ("m4s",):
                selected_url = ""
            # video-only without audio
            elif info.get("acodec") == "none" and info.get("vcodec") != "none":
                selected_url = ""
            # audio-only without video
            elif info.get("vcodec") == "none" and info.get("acodec") != "none":
                selected_url = ""

        return {
            "direct_url": selected_url,
            "title": info.get("title", ""),
            "ext": info.get("ext", ""),
        }
    except Exception:
        return {"direct_url": "", "title": "", "ext": ""}


def _get_bilibili_download_url(bvid: str, cid: int, qn: int = 120) -> tuple[str, str]:
    """Get direct CDN download URL for a Bilibili video. Returns (url, ext)."""
    headers = _get_bilibili_headers(bvid)

    # Try fnval=1 first (single mp4 with audio)
    for fnval, fallback_qn in [(1, 120), (1, 112), (0, 80)]:
        r = requests.get("https://api.bilibili.com/x/player/playurl",
            headers=headers,
            params={"bvid": bvid, "cid": cid, "qn": fallback_qn, "fnval": fnval},
            timeout=15)
        if r.status_code != 200:
            continue
        data = r.json().get("data", {})
        durl = data.get("durl", [])
        if durl and durl[0].get("url"):
            return durl[0]["url"], "mp4"

    raise RuntimeError("无法获取视频下载链接")

def start_download(url: str, format_id: Optional[str] = None, cookies_file: Optional[str] = None) -> str:
    """Launch a server-side download task, return task_id."""
    url = _normalize_url(url)
    task_id = uuid.uuid4().hex[:12]
    tasks[task_id] = {
        "status": "starting",
        "progress": 0,
        "speed": "",
        "eta": 0,
        "filename": "",
        "title": "",
        "error": "",
        "warning": "",
    }

    is_bili = _is_bilibili_url(url)

    if is_bili:
        bvid = BILIBILI_VIDEO_RE.search(url).group(1)
        headers = _get_bilibili_headers(bvid)
        r = requests.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", headers=headers, timeout=15)
        r.raise_for_status()
        view = r.json()["data"]
        video_title = view.get("title", "")
        cid = view.get("cid") or (view.get("pages", [{}])[0].get("cid", 0))
        tasks[task_id]["title"] = video_title

        qn = int(format_id) if format_id and format_id.isdigit() else 120
        try:
            dl_url, dl_ext = _get_bilibili_download_url(bvid, cid, qn)
        except RuntimeError as e:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["error"] = str(e)
            return task_id

        url = dl_url  # Replace: yt-dlp downloads from CDN directly
        has_ffmpeg = False  # Single file, no merge needed
        fmt = "best"
    else:
        has_ffmpeg = _has_ffmpeg()
        if format_id:
            if has_ffmpeg:
                fmt = f"{format_id}+bestaudio/bestvideo+bestaudio/best"
            else:
                fmt = format_id
        elif has_ffmpeg:
            fmt = "bestvideo+bestaudio/best"
        else:
            fmt = "best"

    captured_filename = [""]  # mutable container for closure

    def _hook(d: dict):
        t = tasks.get(task_id)
        if not t:
            return
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            t["status"] = "downloading"
            t["progress"] = round((downloaded / total) * 100, 1) if total else 0
            speed = d.get("speed") or 0
            t["speed"] = f"{speed/1024/1024:.1f} MB/s" if speed else ""
            fname = d.get("filename", "")
            if fname and not captured_filename[0]:
                captured_filename[0] = fname
        elif d["status"] == "finished":
            t["status"] = "processing"
            t["progress"] = 100
            fname = d.get("filename", "")
            if fname:
                captured_filename[0] = fname

    def _pp_hook(d: dict):
        """Postprocessor hook — tracks ffmpeg merge progress."""
        t = tasks.get(task_id)
        if not t:
            return
        if d["status"] == "started":
            t["status"] = "merging"
        elif d["status"] == "finished":
            t["status"] = "done"
            t["progress"] = 100

    outtmpl = str(DOWNLOADS_DIR / f"%(title)s_{task_id}.%(ext)s")
    opts = _build_ydl_opts(outtmpl, quiet=False, cookies_file=cookies_file, url=url)
    opts["format"] = fmt
    opts["progress_hooks"] = [_hook]
    opts["postprocessor_hooks"] = [_pp_hook]
    if not is_bili and has_ffmpeg:
        opts["merge_output_format"] = "mp4"
        ffmpeg_path = _get_ffmpeg_path()
        if ffmpeg_path:
            opts["ffmpeg_location"] = ffmpeg_path

    def _check_has_audio(filepath: str) -> bool:
        """Check if downloaded file contains an audio stream."""
        ffmpeg = _get_ffmpeg_path()
        if not ffmpeg:
            return True
        try:
            import subprocess
            r = subprocess.run(
                [ffmpeg, '-i', filepath],
                capture_output=True, timeout=15,
            )
            stderr = r.stderr.decode('utf-8', errors='replace')
            return 'Audio:' in stderr
        except Exception:
            return True

    def _run_bilibili():
        """Direct HTTP download for Bilibili — avoids yt-dlp entirely."""
        t = tasks.get(task_id)
        try:
            safe_title = "".join(c for c in video_title if c.isalnum() or c in " _-").rstrip() or "video"
            filepath = str(DOWNLOADS_DIR / f"{safe_title}_{task_id}.mp4")

            t["status"] = "downloading"
            dl_headers = _get_bilibili_headers(bvid)
            resp = requests.get(dl_url, headers=dl_headers, stream=True, timeout=300)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))

            downloaded = 0
            start_time = time.time()
            with open(filepath, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            t["progress"] = round((downloaded / total) * 100, 1)
                        elapsed = time.time() - start_time
                        if elapsed > 0:
                            t["speed"] = f"{downloaded / elapsed / 1024 / 1024:.1f} MB/s"

            t["status"] = "done"
            t["progress"] = 100
            t["filename"] = filepath

        except Exception as e:
            if t:
                t["status"] = "error"
                t["error"] = str(e)[:500]

    def _run():
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            t = tasks.get(task_id)
            if t:
                if t["status"] != "done":
                    t["status"] = "done"
                t["title"] = info.get("title", "")

                fname = captured_filename[0]
                if fname and Path(fname).exists():
                    t["filename"] = fname
                else:
                    reqs = info.get("requested_downloads") or []
                    for rd in reqs:
                        fp = rd.get("filepath", "")
                        if fp and Path(fp).exists():
                            fname = fp
                            break
                    if not fname:
                        fname = info.get("_filename", "")
                    if fname:
                        t["filename"] = fname

                if not t["filename"] or not Path(t["filename"]).exists():
                    for p in DOWNLOADS_DIR.glob(f"*{task_id}*"):
                        if p.is_file():
                            t["filename"] = str(p)
                            break

                final_path = t.get("filename", "")
                if final_path and Path(final_path).exists() and has_ffmpeg:
                    if not _check_has_audio(final_path):
                        t["warning"] = (
                            "This video has NO audio track. "
                            "The platform may require cookies/login for full access. "
                            "Place a cookies.txt file and retry."
                        )

                if not has_ffmpeg and not format_id:
                    t["warning"] = "FFmpeg not installed — video only (no audio). Install ffmpeg for full quality."

        except DownloadError as e:
            t = tasks.get(task_id)
            if t:
                t["status"] = "error"
                t["error"] = str(e)[:500]
        except Exception as e:
            t = tasks.get(task_id)
            if t:
                t["status"] = "error"
                t["error"] = str(e)[:500]

    import threading
    import time
    target = _run_bilibili if is_bili else _run
    threading.Thread(target=target, daemon=True).start()
    return task_id
