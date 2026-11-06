from __future__ import annotations

import calendar
import hashlib
import html
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dtparser


ROOT = Path(__file__).resolve().parent.parent

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

HTTP_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
}

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"https?://\S+")
_TRACKING_RE = re.compile(r"^(utm_.*|oc|hl|gl|ceid|ref|ref_src|source|campaign)$", re.I)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def local_now(tz_name: str = "Asia/Kolkata") -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now()


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, tuple) and len(value) >= 6:
        try:
            return datetime(*value[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if re.fullmatch(r"\d{9,11}", text):
            return datetime.fromtimestamp(int(text), tz=timezone.utc)
        dt = dtparser.parse(text)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, TypeError):
        return None


def iso(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(value: Any, limit: int = 0) -> str:
    if value is None:
        return ""
    text = str(value)
    text = html.unescape(text)
    text = _TAG_RE.sub(" ", text)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = _SPACE_RE.sub(" ", text).strip()
    if limit and len(text) > limit:
        cut = text[:limit]
        if " " in cut[-40:]:
            cut = cut.rsplit(" ", 1)[0]
        text = cut.rstrip(" ,;:") + "…"
    return text


def strip_html(value: Any, limit: int = 0) -> str:
    return clean_text(value, limit)


def truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut[-50:]:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:") + "…"


def first_sentence(text: str, limit: int = 155) -> str:
    text = clean_text(text)
    if not text:
        return ""
    # Treat line breaks and sentence punctuation as boundaries.
    parts = re.split(r"(?<=[.!?])\s+|\s*\n+\s*", text)
    candidate = next((p.strip() for p in parts if p.strip()), text)
    if len(candidate) < 24 and len(parts) > 1:
        candidate = (candidate + " " + parts[1]).strip()
    return truncate(candidate, limit)


def split_linkedin_title(text: str) -> tuple[str, str]:
    """LinkedIn post text is often a full paragraph. Make a readable headline."""
    text = clean_text(text)
    if not text:
        return "", ""
    # If it is already short, keep it whole.
    if len(text) <= 155:
        return text, text
    lead = first_sentence(text, 155)
    if len(lead) < 35:
        lead = truncate(text, 155)
    return lead, text


def fingerprint(title: str, url: str = "") -> str:
    base = clean_text(title).lower()
    base = re.sub(r"https?://\S+", "", base)
    base = re.sub(r"[^a-z0-9#]+", " ", base)
    base = _SPACE_RE.sub(" ", base).strip()
    if len(base) < 30 and url:
        base = f"{base} {canonical_url(url)}"
    if not base:
        base = canonical_url(url)
    return hashlib.sha1(base[:220].encode("utf-8")).hexdigest()


def item_id(fp: str) -> str:
    return hashlib.sha1(fp.encode("utf-8")).hexdigest()[:16]


def canonical_url(url: str) -> str:
    if not url:
        return ""
    try:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        parts = urlsplit(url.strip())
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING_RE.match(k)]
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))
    except Exception:
        return url.strip()


def get(url: str, timeout: int = 28, **kwargs) -> requests.Response:
    headers = dict(HTTP_HEADERS)
    headers.update(kwargs.pop("headers", {}) or {})
    session = kwargs.pop("session", None) or requests
    response = session.get(url, headers=headers, timeout=timeout, **kwargs)
    response.raise_for_status()
    return response


def soup(url_or_html: str, is_html: bool = False) -> BeautifulSoup:
    if is_html:
        return BeautifulSoup(url_or_html, "html.parser")
    return BeautifulSoup(get(url_or_html).text, "html.parser")


def meta_content(s: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = s.find("meta", attrs={"property": name}) or s.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return clean_text(tag["content"])
    return ""


def html_to_plain(value: str) -> str:
    return clean_text(value)


def safe_json(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


def unescape_json(value: str) -> str:
    return html.unescape(value).replace("\\/", "/")


def title_from_url(url: str) -> str:
    path = url.rstrip("/").split("/")[-1]
    return path.replace("-", " ").replace("_", " ").strip().title()


def source_initial(name: str) -> str:
    name = clean_text(name)
    if not name:
        return "•"
    return name[0].upper()


def dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def relative_time(dt: datetime | None, now: datetime | None = None) -> str:
    if not dt:
        return ""
    now = now or utcnow()
    delta = max(0, int((now - dt).total_seconds()))
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    if delta < 172800:
        return "yesterday"
    return f"{delta // 86400}d ago"


def nice_date(dt: datetime | None, tz_name: str = "Asia/Kolkata") -> str:
    if not dt:
        return ""
    try:
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo(tz_name)).strftime("%a, %-d %b %Y")
    except Exception:
        return dt.strftime("%a, %d %b %Y")


def compact_date(dt: datetime | None, tz_name: str = "Asia/Kolkata") -> str:
    if not dt:
        return ""
    try:
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo(tz_name)).strftime("%d %b %Y")
    except Exception:
        return dt.strftime("%d %b %Y")


def ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
