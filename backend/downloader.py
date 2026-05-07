"""
yt-dlp wrapper: extract info, direct URLs, and server-side download with progress.
"""
import shutil
import uuid
from pathlib import Path
from typing import Optional

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


def _build_ydl_opts(outtmpl: Optional[str] = None, *, quiet: bool = True, cookies_file: Optional[str] = None, url: str = "") -> dict:
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


def extract_info(url: str) -> dict:
    """Fetch video metadata + available formats without downloading."""
    url = _normalize_url(url)
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

    has_ffmpeg = _has_ffmpeg()
    if format_id:
        if has_ffmpeg:
            fmt = f"{format_id}+bestaudio/bestvideo+bestaudio/best"
        else:
            fmt = format_id
    elif has_ffmpeg:
        # Prefer merging high-quality separate streams over low-quality combined
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
    if has_ffmpeg:
        opts["merge_output_format"] = "mp4"
        ffmpeg_path = _get_ffmpeg_path()
        if ffmpeg_path:
            opts["ffmpeg_location"] = ffmpeg_path

    def _check_has_audio(filepath: str) -> bool:
        """Check if downloaded file contains an audio stream."""
        ffmpeg = _get_ffmpeg_path()
        if not ffmpeg:
            return True  # can't check, assume OK
        try:
            import subprocess
            r = subprocess.run(
                [ffmpeg, '-i', filepath],
                capture_output=True, timeout=15,
            )
            stderr = r.stderr.decode('utf-8', errors='replace')
            return 'Audio:' in stderr
        except Exception:
            return True  # can't check, assume OK

    def _run():
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            t = tasks.get(task_id)
            if t:
                if t["status"] != "done":
                    t["status"] = "done"
                t["title"] = info.get("title", "")

                # Find the actual output file
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

                # Verify audio
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

    import threading
    threading.Thread(target=_run, daemon=True).start()
    return task_id
