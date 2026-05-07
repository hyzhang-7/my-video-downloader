"""
Douyin video extractor — mobile UA + public API, no cookies required.

Uses iOS User-Agent with iesdouyin.com API/share-page to bypass bot detection.
Try share-page first (more fields), fall back to public API, with WAF challenge solver.
"""
import base64
import json
import re
import time
import uuid
from hashlib import sha256
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

import httpx

DOWNLOADS_DIR = Path(__file__).resolve().parent.parent / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
        "Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.douyin.com/",
}

API_ITEM_INFO = "https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/"


def extract_video_id(url: str) -> Optional[str]:
    """Extract douyin video ID from various URL formats."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    # modal_id in query string
    for key in ("modal_id", "item_ids", "group_id", "aweme_id"):
        values = query.get(key)
        if values:
            m = re.search(r"(\d{8,24})", values[0])
            if m:
                return m.group(1)

    # /video/123 path
    m = re.search(r"/video/(\d{8,24})", parsed.path)
    if m:
        return m.group(1)

    # /note/123 path
    m = re.search(r"/note/(\d{8,24})", parsed.path)
    if m:
        return m.group(1)

    # Bare numeric ID in path
    m = re.search(r"/(\d{8,24})(?:/|$)", parsed.path)
    if m:
        return m.group(1)

    # In the full URL string
    m = re.search(r"(?:video_id|video/|modal_id=)(\d{8,24})", url)
    if m:
        return m.group(1)

    return None


def _client(timeout: int = 20) -> httpx.Client:
    return httpx.Client(
        headers=MOBILE_HEADERS,
        timeout=timeout,
        follow_redirects=True,
    )


def _b64url_decode(v: str) -> bytes:
    v = v.replace("-", "+").replace("_", "/")
    v += "=" * (-len(v) % 4)
    return base64.b64decode(v)


def _solve_waf_and_retry(html: str, share_url: str) -> str:
    """Solve WAF challenge and retry; returns HTML on success, empty on failure."""
    m = re.search(r'wci="([^"]+)"\s*,\s*cs="([^"]+)"', html)
    if not m:
        return ""
    cookie_name, challenge_blob = m.groups()
    try:
        cd = json.loads(_b64url_decode(challenge_blob).decode("utf-8"))
        prefix = _b64url_decode(cd["v"]["a"])
        expected = _b64url_decode(cd["v"]["c"]).hex()
        solved = None
        for i in range(1_000_001):
            if sha256(prefix + str(i).encode()).hexdigest() == expected:
                solved = i
                break
        if solved is None:
            return ""
        cd["d"] = base64.b64encode(str(solved).encode()).decode()
        cv = base64.b64encode(
            json.dumps(cd, separators=(",", ":")).encode()
        ).decode()
        with _client() as c2:
            c2.cookies.set(cookie_name, cv, domain="www.iesdouyin.com", path="/")
            resp = c2.get(share_url)
            if resp.status_code == 200:
                return resp.text
    except Exception:
        pass
    return ""


def _parse_router_data(html: str) -> dict:
    """Extract item_info from _ROUTER_DATA JSON in HTML."""
    marker = "window._ROUTER_DATA = "
    start = html.find(marker)
    if start < 0:
        return {}

    start += len(marker)
    while start < len(html) and html[start].isspace():
        start += 1
    if start >= len(html) or html[start] != "{":
        return {}

    depth = 0
    in_string = False
    escaped = False
    for end in range(start, len(html)):
        c = html[end]
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    router = json.loads(html[start:end + 1])
                except json.JSONDecodeError:
                    return {}
                loader = router.get("loaderData", {})
                for node in loader.values():
                    if not isinstance(node, dict):
                        continue
                    vir = node.get("videoInfoRes", {})
                    if not isinstance(vir, dict):
                        continue
                    il = vir.get("item_list", [])
                    if il and isinstance(il[0], dict):
                        return il[0]
                return {}
    return {}


def _fetch_item_info(video_id: str) -> dict:
    """Fetch video metadata — share page first, then public API as fallback."""
    share_url = f"https://www.iesdouyin.com/share/video/{video_id}/"

    with _client() as client:
        # 1. Try share page (richer data, no API rate limit)
        try:
            resp = client.get(share_url)
            if resp.status_code == 200:
                html = resp.text
                # Check WAF challenge
                if "wci=" in html and "Please wait..." in html:
                    html = _solve_waf_and_retry(html, share_url) or html
                if html:
                    item = _parse_router_data(html)
                    if item:
                        return item
        except Exception:
            pass

        # 2. Try public item API
        try:
            resp = client.get(API_ITEM_INFO, params={"item_ids": video_id})
            if resp.status_code == 200 and resp.content:
                data = resp.json()
                item_list = data.get("item_list", [])
                if item_list:
                    return item_list[0]
        except Exception:
            pass

    return {}


def extract_info_douyin(url: str) -> dict:
    """Fetch video metadata, returns same structure as downloader.extract_info."""
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError("Could not extract douyin video ID from URL")

    item = _fetch_item_info(video_id)
    if not item:
        raise ValueError(
            "Unable to fetch douyin video info. "
            "The video may be private or the platform rate-limited the request."
        )

    video_data = item.get("video", {})
    formats = []

    # Extract available resolutions
    play_addr = video_data.get("play_addr", {})
    url_list = play_addr.get("url_list", [])
    if url_list:
        # Replace playwm → play for watermark-free URL
        direct_url = url_list[0].replace("playwm", "play")
        # Determine resolution from bit_rate or play_addr info
        formats.append({
            "format_id": "video",
            "ext": "mp4",
            "resolution": _extract_resolution(play_addr),
            "filesize": None,
            "vcodec": "h264",
            "acodec": "aac",
            "note": "Video (mobile)",
            "douyin_direct_url": direct_url,
        })

    # Audio
    music = item.get("music", {})
    audio_urls = music.get("play_url", {}).get("url_list", [])
    if audio_urls:
        formats.append({
            "format_id": "audio",
            "ext": "mp3",
            "resolution": "Audio only",
            "filesize": None,
            "vcodec": "none",
            "acodec": "mp3",
            "note": "Audio only (mobile)",
            "douyin_direct_url": audio_urls[0],
        })

    # Thumbnail
    cover = (
        video_data.get("cover", {}).get("url_list", [None])[0]
        or video_data.get("origin_cover", {}).get("url_list", [None])[0]
        or item.get("video", {}).get("dynamic_cover", {}).get("url_list", [None])[0]
    )

    author = item.get("author", {})

    # Douyin API returns duration in milliseconds, convert to seconds
    duration_ms = video_data.get("duration") or item.get("music", {}).get("duration") or 0
    duration = duration_ms // 1000 if duration_ms > 1000 else duration_ms

    return {
        "title": item.get("desc") or f"douyin_{video_id}",
        "thumbnail": cover or "",
        "duration": duration,
        "uploader": author.get("nickname", ""),
        "webpage_url": f"https://www.douyin.com/video/{video_id}",
        "formats": formats,
        "direct_url_available": len(formats) > 0,
        "douyin_raw_item": item,  # cached for download
    }


def _extract_resolution(play_addr: dict) -> str:
    """Extract human-readable resolution from play_addr or URL params."""
    # Try width/height first
    w = play_addr.get("width", 0)
    h = play_addr.get("height", 0)
    if w and h:
        if h >= 2160: return "4K"
        if h >= 1080: return "1080p"
        if h >= 720: return "720p"
        if h >= 480: return "480p"
        return f"{w}x{h}"

    # Parse from URL params (e.g., ratio=1080p or video_quality=...)
    url_list = play_addr.get("url_list", [])
    if url_list:
        m = re.search(r"ratio=(\d{3,4}p)", url_list[0])
        if m:
            return m.group(1)
    return "HD"


def get_direct_url_douyin(url: str, format_id: Optional[str] = None) -> dict:
    """Get direct download URL for douyin video."""
    info = extract_info_douyin(url)
    formats = info.get("formats", [])

    target = None
    if format_id:
        for f in formats:
            if f["format_id"] == format_id:
                target = f
                break
    if not target and formats:
        target = formats[0]

    if not target:
        return {"direct_url": "", "title": info.get("title", ""), "ext": ""}

    return {
        "direct_url": target.get("douyin_direct_url", ""),
        "title": info["title"],
        "ext": target["ext"],
    }


# In-memory task store for progress tracking
douyin_tasks: dict[str, dict] = {}


def start_download_douyin(url: str, format_id: Optional[str] = None) -> str:
    """Download a douyin video, returns task_id for progress tracking."""
    info = extract_info_douyin(url)
    formats = info.get("formats", [])

    target = None
    if format_id:
        for f in formats:
            if f["format_id"] == format_id:
                target = f
                break
    if not target and formats:
        target = formats[0]

    if not target or not target.get("douyin_direct_url"):
        raise ValueError("No downloadable URL found for this video")

    direct_url = target["douyin_direct_url"]
    ext = target["ext"]
    title = info["title"]

    task_id = uuid.uuid4().hex[:12]
    out_path = DOWNLOADS_DIR / f"{title}_{task_id}.{ext}"
    # Sanitize filename
    safe_name = re.sub(r'[\\/*?:"<>|]', "_", title)
    out_path = DOWNLOADS_DIR / f"{safe_name}_{task_id}.{ext}"

    douyin_tasks[task_id] = {
        "status": "starting",
        "progress": 0,
        "speed": "",
        "eta": 0,
        "filename": str(out_path),
        "title": title,
        "error": "",
        "warning": "",
    }

    def _run():
        t = douyin_tasks.get(task_id)
        try:
            import threading
            current_thread = threading.current_thread()

            with _client(timeout=300) as client:
                with client.stream("GET", direct_url) as resp:
                    if resp.status_code >= 400:
                        raise Exception(f"HTTP {resp.status_code}")
                    total = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    last_update = time.time()

                    with open(out_path, "wb") as f:
                        t["status"] = "downloading"
                        for chunk in resp.iter_bytes(65536):
                            if not getattr(current_thread, "_active", True):
                                if out_path.exists():
                                    out_path.unlink(missing_ok=True)
                                return
                            f.write(chunk)
                            downloaded += len(chunk)
                            now = time.time()
                            if now - last_update >= 0.5 and t:
                                t["progress"] = round((downloaded / total) * 100, 1) if total else 0
                                t["speed"] = f"{downloaded / max(now - last_update, 0.1) / 1024 / 1024:.1f} MB/s" if total else ""
                                last_update = now

            if t:
                t["status"] = "done"
                t["progress"] = 100
                t["filename"] = str(out_path)

        except Exception as e:
            if t:
                t["status"] = "error"
                t["error"] = str(e)[:500]
            if out_path.exists():
                out_path.unlink(missing_ok=True)

    import threading
    t = threading.Thread(target=_run, daemon=True)
    t._active = True
    t.start()
    return task_id
