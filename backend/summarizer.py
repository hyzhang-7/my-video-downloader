"""
AI video summarization: subtitle extraction + DeepSeek API integration.
"""
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
from openai import OpenAI
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")
DEFAULT_COOKIES = PROJECT_DIR / "cookies.txt"

# In-memory caches (lifetime = server process, matches existing architecture)
_subtitle_cache: dict[str, dict] = {}  # url -> {text, language, source}
_summary_cache: dict[str, dict] = {}  # url -> summary result

# Subtitle language priority: Chinese first, English fallback (for yt-dlp fallback)
SUBTITLE_LANGS = ["zh-Hans", "zh", "zh-CN", "zh-TW", "en"]

# Bilibili request headers (public APIs, no auth needed)
BILIBILI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}

SYSTEM_PROMPT = """你是一个视频内容分析助手。根据提供的视频字幕内容，请完成以下任务：

1. 生成视频大纲（用 Markdown 层级标题格式，以 ## 开头，适合直接生成思维导图）
2. 提取核心要点（3-5 条有价值的见解或知识点）
3. 用一句话概括视频主题

请严格以 JSON 格式回复，不要添加任何额外说明：
{"outline_markdown": "## 主题\\n### 子主题1\\n- 细节", "key_points": ["要点1", "要点2", "要点3"], "summary": "一句话总结"}"""


def _get_client() -> Optional[OpenAI]:
    """Create DeepSeek API client. Returns None if key not configured."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return None
    return OpenAI(api_key=api_key, base_url="https://api.deepseek.com")


def _parse_vtt(text: str) -> str:
    """Parse VTT subtitle content to plain readable text, removing timestamps and tags."""
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or line == "WEBVTT" or "-->" in line:
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if line:
            lines.append(line)
    # Deduplicate consecutive identical lines (common in auto-generated subs)
    result = []
    for line in lines:
        if not result or line != result[-1]:
            result.append(line)
    return "\n".join(result)


def _get_best_subtitle_lang(info: dict) -> tuple[Optional[str], str]:
    """
    Find the best available subtitle language from video info (yt-dlp format).
    Returns (lang_code, source) where source is 'manual' or 'auto'.
    Skips non-subtitle types like 'danmaku'.
    """
    manual_subs = info.get("subtitles") or {}
    auto_subs = info.get("automatic_captions") or {}
    skip_langs = {"danmaku", "live_chat"}

    for lang in SUBTITLE_LANGS:
        if lang in manual_subs and lang not in skip_langs:
            return lang, "manual"

    for lang in SUBTITLE_LANGS:
        if lang in auto_subs and lang not in skip_langs:
            return lang, "auto"

    for lang in manual_subs:
        if lang not in skip_langs:
            return lang, "manual"
    for lang in auto_subs:
        if lang not in skip_langs:
            return lang, "auto"

    return None, "none"


def _is_bilibili_url(url: str) -> bool:
    """Check if URL is a Bilibili video."""
    return bool(re.search(r"bilibili\.com/video/(BV[a-zA-Z0-9]+)", url))


def _extract_bilibili_subtitles(url: str) -> dict:
    """
    Extract subtitles from Bilibili using public APIs (no login required).
    Flow: x/web-interface/view → x/v2/dm/view → subtitle JSON download.

    Returns {ok, subtitles_text, language, source, message?}.
    """
    m = re.search(r"BV[a-zA-Z0-9]+", url)
    if not m:
        return {"ok": False, "subtitles_text": None, "message": "无法解析 Bilibili 视频 ID"}

    bvid = m.group(0)
    headers = dict(BILIBILI_HEADERS)
    headers["Referer"] = f"https://www.bilibili.com/video/{bvid}"

    try:
        # Step 1: Get aid and cid from public view API
        r1 = requests.get(
            f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
            headers=headers, timeout=15,
        )
        r1.raise_for_status()
        view_data = r1.json().get("data", {})
        aid = view_data.get("aid")
        cid = view_data.get("cid")
        if not cid:
            return {"ok": False, "subtitles_text": None, "message": "无法获取视频信息"}

        # Step 2: Get subtitle URLs via dm/view (public, no auth needed)
        r2 = requests.get(
            "https://api.bilibili.com/x/v2/dm/view",
            params={"aid": aid, "oid": cid, "type": 1},
            headers=headers, timeout=15,
        )
        r2.raise_for_status()
        dm_data = r2.json()
        subtitles = dm_data.get("data", {}).get("subtitle", {}).get("subtitles", [])
        if not subtitles:
            return {"ok": False, "subtitles_text": None, "message": "该视频无可用字幕"}

        # Step 3: Select best subtitle track
        # Priority: manual zh > ai-zh > any first available
        best, source = None, "unknown"
        for s in subtitles:
            if s.get("lan") == "zh" and not s.get("lan", "").startswith("ai-"):
                best, source = s, "manual"
                break

        if not best:
            for s in subtitles:
                if s.get("lan", "").startswith("ai-zh"):
                    best, source = s, "auto"
                    break

        if not best:
            best = subtitles[0]
            source = "auto" if best.get("lan", "").startswith("ai-") else "manual"

        # Step 4: Download subtitle content JSON
        sub_url = best.get("subtitle_url", "")
        if not sub_url:
            return {"ok": False, "subtitles_text": None, "message": "字幕链接无效"}

        if sub_url.startswith("//"):
            sub_url = "https:" + sub_url
        elif sub_url.startswith("http:"):
            sub_url = sub_url.replace("http:", "https:")

        r3 = requests.get(sub_url, headers=headers, timeout=15)
        r3.raise_for_status()
        sub_json = r3.json()
        body = sub_json.get("body", [])
        if not body:
            return {"ok": False, "subtitles_text": None, "message": "字幕内容为空"}

        # Join segments with timestamps
        lines = []
        for item in body:
            content = item.get("content", "")
            if content:
                start = item.get("from", 0)
                m = int(start // 60)
                s = int(start % 60)
                ts = f"[{m:02d}:{s:02d}]"
                lines.append(f"{ts} {content}")
        text = "\n".join(lines)

        return {
            "ok": True,
            "subtitles_text": text,
            "language": best.get("lan_doc", best.get("lan", "unknown")),
            "source": source,
        }

    except requests.RequestException as e:
        return {"ok": False, "subtitles_text": None, "message": f"B站 API 请求失败: {str(e)[:200]}"}
    except Exception as e:
        return {"ok": False, "subtitles_text": None, "message": f"字幕提取出错: {str(e)[:200]}"}


def _extract_subtitles_ytdlp(url: str, cookies_source: Optional[str] = None) -> dict:
    """
    Extract subtitles using yt-dlp (fallback for non-Bilibili platforms).
    Returns {ok, subtitles_text, language, source, message?}.
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts = {
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": SUBTITLE_LANGS,
                "subtitlesformat": "vtt",
                "outtmpl": f"{tmpdir}/%(id)s.%(ext)s",
                "quiet": True,
                "no_warnings": False,
            }
            if cookies_source:
                if cookies_source.endswith(".txt") and Path(cookies_source).exists():
                    ydl_opts["cookiefile"] = cookies_source
                else:
                    ydl_opts["cookiesfrombrowser"] = (cookies_source,)
            elif DEFAULT_COOKIES.exists():
                ydl_opts["cookiefile"] = str(DEFAULT_COOKIES)

            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                best_lang, source = _get_best_subtitle_lang(info)

                if not best_lang:
                    return {
                        "ok": False,
                        "subtitles_text": None,
                        "message": "该视频无可用字幕，暂不支持 AI 总结",
                    }

                info = ydl.extract_info(url, download=True)
                video_id = info["id"]

                subtitle_text = None
                for fname in os.listdir(tmpdir):
                    if fname.startswith(video_id) and fname.endswith(".vtt"):
                        filepath = os.path.join(tmpdir, fname)
                        with open(filepath, "r", encoding="utf-8") as f:
                            subtitle_text = _parse_vtt(f.read())
                        break

                if not subtitle_text:
                    return {
                        "ok": False,
                        "subtitles_text": None,
                        "message": "字幕文件解析失败",
                    }

                return {
                    "ok": True,
                    "subtitles_text": subtitle_text,
                    "language": best_lang,
                    "source": source,
                }

    except DownloadError as e:
        return {
            "ok": False,
            "subtitles_text": None,
            "message": f"视频解析失败: {str(e)[:200]}",
        }
    except Exception as e:
        return {
            "ok": False,
            "subtitles_text": None,
            "message": f"字幕提取出错: {str(e)[:200]}",
        }


def extract_subtitles(url: str, cookies_source: Optional[str] = None) -> dict:
    """
    Extract subtitles from a video URL.
    Uses Bilibili public API for Bilibili URLs, yt-dlp for everything else.

    cookies_source: browser name for cookies-from-browser (e.g. 'chrome', 'firefox', 'edge')
                    or path to a cookies.txt file. (Only used for non-Bilibili platforms)
    Returns {ok, subtitles_text, language, source, cached?, message?}.
    """
    if url in _subtitle_cache:
        cached = _subtitle_cache[url]
        return {
            "ok": True,
            "subtitles_text": cached["text"],
            "language": cached.get("language", "unknown"),
            "source": cached.get("source", "unknown"),
            "cached": True,
        }

    # Bilibili: use public API (cookie-free)
    if _is_bilibili_url(url):
        result = _extract_bilibili_subtitles(url)
    else:
        result = _extract_subtitles_ytdlp(url, cookies_source=cookies_source)

    if result["ok"] and result["subtitles_text"]:
        _subtitle_cache[url] = {
            "text": result["subtitles_text"],
            "language": result.get("language", "unknown"),
            "source": result.get("source", "unknown"),
        }

    return result


def generate_summary(url: str, cookies_source: Optional[str] = None) -> dict:
    """Generate AI summary (outline + key points + one-liner) for a video."""
    sub_result = extract_subtitles(url, cookies_source=cookies_source)
    if not sub_result["ok"] or not sub_result["subtitles_text"]:
        return {"ok": False, "message": sub_result.get("message", "无法获取字幕")}

    subtitle_text = sub_result["subtitles_text"]

    if url in _summary_cache:
        return {"ok": True, "data": _summary_cache[url]}

    client = _get_client()
    if not client:
        return {"ok": False, "message": "未配置 DEEPSEEK_API_KEY，请在 .env 文件中设置"}

    # Truncate to stay within reasonable context limits
    max_chars = 15000
    truncated = subtitle_text
    if len(truncated) > max_chars:
        truncated = truncated[:max_chars] + "\n...(内容已截断)"

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"请分析以下视频字幕内容：\n\n{truncated}"},
            ],
            temperature=0.7,
            max_tokens=4096,
        )

        content = response.choices[0].message.content.strip()

        # Strip markdown code fences if present
        if content.startswith("```"):
            content = re.sub(r"^```\w*\n", "", content)
            content = re.sub(r"\n```$", "", content)

        result = json.loads(content)

        data = {
            "outline_markdown": result.get("outline_markdown", ""),
            "key_points": result.get("key_points", []),
            "summary": result.get("summary", ""),
            "subtitles_text": subtitle_text,
            "subtitles_source": sub_result.get("source", "unknown"),
            "subtitles_language": sub_result.get("language", "unknown"),
        }

        _summary_cache[url] = data
        return {"ok": True, "data": data}

    except json.JSONDecodeError:
        return {"ok": False, "message": "AI 返回格式异常，请重试"}
    except Exception as e:
        return {"ok": False, "message": f"AI 总结出错: {str(e)[:300]}"}


def chat_with_video(url: str, question: str, history: Optional[list[dict]] = None, cookies_source: Optional[str] = None) -> dict:
    """Chat with AI about a video's content."""
    sub_result = extract_subtitles(url, cookies_source=cookies_source)
    if not sub_result["ok"] or not sub_result["subtitles_text"]:
        return {"ok": False, "answer": "无法获取视频字幕，无法进行问答"}

    subtitle_text = sub_result["subtitles_text"]
    client = _get_client()
    if not client:
        return {"ok": False, "answer": "未配置 DEEPSEEK_API_KEY"}

    max_chars = 12000
    ctx = subtitle_text[:max_chars] if len(subtitle_text) > max_chars else subtitle_text

    messages = [
        {
            "role": "system",
            "content": (
                "你是一个视频内容问答助手。根据以下视频字幕内容回答用户的问题。"
                "用中文回答，简洁准确。如果答案不能从字幕中得出，请如实说明。\n\n"
                f"视频字幕：\n{ctx}"
            ),
        },
    ]

    if history:
        messages.extend(history[-10:])

    messages.append({"role": "user", "content": question})

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            temperature=0.7,
            max_tokens=2048,
        )
        return {"ok": True, "answer": response.choices[0].message.content.strip()}
    except Exception as e:
        return {"ok": False, "answer": f"AI 问答出错: {str(e)[:300]}"}
