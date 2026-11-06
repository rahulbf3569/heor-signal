from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

from .config import Config, Source
from .util import (
    HTTP_HEADERS,
    canonical_url,
    clean_text,
    parse_datetime,
    soup,
    truncate,
    utcnow,
)

log = logging.getLogger("heor_signal.fetch")

# NCBI allows roughly 3 requests/second without an API key. Two PubMed
# sources running in parallel trip that limit, so serialise them.
_PUBMED_LOCK = threading.Lock()

PUBLISHER_ALIASES = {
    "u.s. food and drug administration (.gov)": "FDA",
    "food and drug administration": "FDA",
    "centers for medicare & medicaid services | cms (.gov)": "CMS",
    "centers for medicare & medicaid services": "CMS",
    "haute autorité de santé": "HAS",
    "institute for clinical and economic review": "ICER",
    "canada's drug agency": "Canada's Drug Agency",
    "linkedin": "LinkedIn",
}


@dataclass
class SourceResult:
    source_id: str
    name: str
    source_type: str
    items: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ok"
    error: str = ""


def _get(url: str, timeout: int = 30, **kwargs: Any) -> requests.Response:
    headers = dict(HTTP_HEADERS)
    headers.update(kwargs.pop("headers", {}) or {})
    response = requests.get(url, headers=headers, timeout=timeout, **kwargs)
    response.raise_for_status()
    return response


def _get_with_retry(url: str, attempts: int = 3, **kwargs: Any) -> requests.Response:
    """Retry a small number of times on 429/5xx, with a short backoff."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return _get(url, **kwargs)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status not in {429, 500, 502, 503, 504}:
                raise
            last_error = exc
            time.sleep(1.2 * (attempt + 1))
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(1.2 * (attempt + 1))
    raise last_error or RuntimeError(f"request failed: {url}")


def _publisher_name(value: str) -> str:
    name = clean_text(value)
    return PUBLISHER_ALIASES.get(name.lower(), name)


def _strip_publisher_suffix(title: str, publisher: str) -> str:
    title = clean_text(title)
    if not publisher:
        return title
    suffix = f" - {publisher}"
    if title.lower().endswith(suffix.lower()):
        return title[: -len(suffix)].strip()
    # Google News sometimes abbreviates the publisher.
    if " - " in title:
        head, tail = title.rsplit(" - ", 1)
        if len(tail) < 70 and "linkedin" not in tail.lower():
            return head.strip()
    return title


def _within(value: datetime | None, max_age_days: int, now: datetime) -> bool:
    if not value:
        return True
    return value >= now.replace(microsecond=0) - __import__("datetime").timedelta(days=max_age_days)


def fetch_google_news(source: Source, config: Config) -> SourceResult:
    locale = (source.locale or "en").lower()
    if locale.startswith("fr"):
        hl, gl, ceid = "fr", "FR", "FR:fr"
    elif locale.startswith("en-in"):
        hl, gl, ceid = "en-IN", "IN", "IN:en"
    elif locale.startswith("en-gb"):
        hl, gl, ceid = "en-GB", "GB", "GB:en"
    else:
        hl, gl, ceid = "en-US", "US", "US:en"
    # Per-source overrides (India query) take precedence.
    gl = source.gl or gl
    ceid = source.ceid or ceid

    url = "https://news.google.com/rss/search"
    params = {"q": source.query, "hl": hl, "gl": gl, "ceid": ceid}
    response = _get(url, params=params)
    feed = feedparser.parse(response.content)
    if getattr(feed, "bozo", False) and not feed.entries:
        raise RuntimeError(f"Google News parse error: {feed.bozo_exception}")

    now = utcnow()
    items: list[dict[str, Any]] = []
    for entry in feed.entries[: source.limit]:
        raw_title = clean_text(entry.get("title", ""))
        publisher = clean_text((entry.get("source") or {}).get("title", ""))
        is_linkedin = (
            source.id.startswith("li_")
            or "linkedin" in publisher.lower()
            or "linkedin.com" in (entry.get("link") or "").lower()
        )
        title = _strip_publisher_suffix(raw_title, publisher)
        if not title:
            continue
        published = parse_datetime(entry.get("published") or entry.get("updated"))
        if published and (now - published).days > 21:
            # Google News can occasionally surface stale results; keep the brief current.
            continue
        source_name = "LinkedIn" if is_linkedin else (_publisher_name(publisher) or source.name)
        items.append(
            {
                "title": title,
                "url": entry.get("link", ""),
                "published_at": published or now,
                "summary": "",
                "author": "",
                "source_id": source.id,
                "source_name": source_name,
                "source_type": "linkedin" if is_linkedin else "news",
                "category_hint": source.category,
                "max_age_days": 6 if is_linkedin else 5,
                "raw": {
                    "query": source.query,
                    "publisher": publisher,
                    "google_news_title": raw_title,
                },
            }
        )
    return SourceResult(source.id, source.name, "linkedin" if source.id.startswith("li_") else "news", items)


def fetch_rss(source: Source, config: Config) -> SourceResult:
    response = _get(source.url)
    feed = feedparser.parse(response.content)
    if getattr(feed, "bozo", False) and not feed.entries:
        raise RuntimeError(f"RSS parse error: {feed.bozo_exception}")
    now = utcnow()
    items: list[dict[str, Any]] = []
    for entry in feed.entries:
        published = parse_datetime(
            entry.get("published") or entry.get("updated") or entry.get("created")
        )
        if published and published < now.replace(microsecond=0) - __import__("datetime").timedelta(
            days=source.max_age_days
        ):
            continue
        title = clean_text(entry.get("title", ""))
        if not title:
            continue
        summary = clean_text(entry.get("summary") or entry.get("description") or "")
        if summary and summary.lower() == title.lower():
            summary = ""
        items.append(
            {
                "title": title,
                "url": entry.get("link", ""),
                "published_at": published or now,
                "summary": truncate(summary, 420),
                "author": clean_text(entry.get("author", "")),
                "source_id": source.id,
                "source_name": source.name,
                "source_type": "official",
                "category_hint": source.category,
                "max_age_days": source.max_age_days,
                "raw": {"feed_title": feed.feed.get("title", "")},
            }
        )
    items.sort(key=lambda x: x.get("published_at") or now, reverse=True)
    return SourceResult(source.id, source.name, "official", items[: source.limit])


def fetch_nice(source: Source, config: Config) -> SourceResult:
    listing = soup(source.url)
    anchors: list[tuple[str, str, datetime | None]] = []
    seen: set[str] = set()
    for anchor in listing.select('a[href*="/news/articles/"]'):
        href = urljoin(source.url, anchor.get("href", ""))
        if "/news/articles/" not in href or href in seen:
            continue
        title = clean_text(anchor.get_text(" ", strip=True))
        date_hint: datetime | None = None
        card = anchor.find_parent("article") or anchor.find_parent("li")
        if card:
            heading = card.find(["h2", "h3", "h4"])
            if heading:
                candidate = clean_text(heading.get_text(" ", strip=True))
                if len(candidate) >= 15:
                    title = candidate
            time_tag = card.find("time")
            if time_tag:
                date_hint = parse_datetime(
                    time_tag.get("datetime") or time_tag.get_text(" ", strip=True)
                )
        if len(title) < 15:
            heading = anchor.find(["h2", "h3", "h4"])
            title = clean_text(heading.get_text(" ", strip=True)) if heading else ""
        if len(title) < 15:
            continue
        seen.add(href)
        anchors.append((href, title, date_hint))
        if len(anchors) >= source.limit:
            break

    now = utcnow()
    items: list[dict[str, Any]] = []
    for href, title, date_hint in anchors:
        published = date_hint or now
        summary = ""
        try:
            page = soup(href)
            html_text = str(page)
            match = re.search(r'"published_at"\s*:\s*"([^"]+)"', html_text)
            if match:
                published = parse_datetime(match.group(1)) or published
            description = page.find("meta", attrs={"property": "og:description"})
            summary = clean_text(description.get("content") if description else "")
            h1 = page.find("h1")
            if h1:
                heading = clean_text(h1.get_text(" ", strip=True))
                if 15 < len(heading) < 240:
                    title = heading
        except Exception as exc:
            log.warning("NICE article fetch failed %s: %s", href, exc)
        if (now - published).days > source.max_age_days:
            continue
        items.append(
            {
                "title": title,
                "url": href,
                "published_at": published,
                "summary": truncate(summary, 420),
                "author": "NICE",
                "source_id": source.id,
                "source_name": source.name,
                "source_type": "official",
                "category_hint": source.category,
                "max_age_days": source.max_age_days,
                "raw": {"listing": source.url},
            }
        )
    return SourceResult(source.id, source.name, "official", items)


def fetch_ispor(source: Source, config: Config) -> SourceResult:
    listing = soup(source.url)
    now = utcnow()
    found: list[tuple[datetime, str, str]] = []
    seen: set[str] = set()
    for anchor in listing.select('a[href*="/news/view/"]'):
        href = urljoin(source.url, anchor.get("href", ""))
        match = re.search(r"/news/view/(\d{4})/(\d{2})/(\d{2})/", href)
        if not match:
            continue
        try:
            published = datetime(
                int(match.group(1)), int(match.group(2)), int(match.group(3)), 12, tzinfo=timezone.utc
            )
        except ValueError:
            continue
        title = clean_text(anchor.get_text(" ", strip=True))
        if len(title) < 15 or href in seen:
            continue
        seen.add(href)
        found.append((published, href, title))
    found.sort(key=lambda x: x[0], reverse=True)

    items: list[dict[str, Any]] = []
    for published, href, title in found[: source.limit]:
        if (now - published).days > source.max_age_days:
            continue
        summary = ""
        try:
            page = soup(href)
            og_title = page.find("meta", attrs={"property": "og:title"})
            og_description = page.find("meta", attrs={"property": "og:description"})
            if og_title and og_title.get("content"):
                candidate = clean_text(og_title["content"])
                if len(candidate) > 15:
                    title = candidate
            if og_description and og_description.get("content"):
                summary = clean_text(og_description["content"])
        except Exception as exc:
            log.warning("ISPOR article fetch failed %s: %s", href, exc)
        items.append(
            {
                "title": title,
                "url": href,
                "published_at": published,
                "summary": truncate(summary, 420),
                "author": "ISPOR",
                "source_id": source.id,
                "source_name": source.name,
                "source_type": "official",
                "category_hint": source.category,
                "max_age_days": source.max_age_days,
                "raw": {"listing": source.url},
            }
        )
    return SourceResult(source.id, source.name, "official", items)


_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _pubmed_date(article: ET.Element) -> datetime | None:
    for path in ("MedlineCitation/Article/ArticleDate", "MedlineCitation/Article/Journal/JournalIssue/PubDate"):
        node = article.find(path)
        if node is None:
            continue
        year = node.findtext("Year")
        month = node.findtext("Month") or "1"
        day = node.findtext("Day") or "1"
        if not year:
            continue
        month_num = _MONTHS.get(month[:3].lower(), int(month) if month.isdigit() else 1)
        try:
            return datetime(int(year), month_num, int(day), tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
    return None


def fetch_pubmed(source: Source, config: Config) -> SourceResult:
    term = source.term.replace("2026[PDAT]", f"{utcnow().year}[PDAT]")
    search_params = {
        "db": "pubmed",
        "term": term,
        "retmax": source.limit,
        "retmode": "json",
        "sort": "pub date",
        "tool": "heor_signal",
    }

    with _PUBMED_LOCK:
        search_response = _get_with_retry(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params=search_params,
        )
        search_data = search_response.json()
        ids = (search_data.get("esearchresult") or {}).get("idlist") or []
        if not ids:
            return SourceResult(source.id, source.name, "research", [])

        fetch_response = _get_with_retry(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={
                "db": "pubmed",
                "id": ",".join(ids),
                "retmode": "xml",
                "tool": "heor_signal",
            },
        )
        time.sleep(0.4)

    root = ET.fromstring(fetch_response.content)
    now = utcnow()
    items: list[dict[str, Any]] = []
    for article in root.findall(".//PubmedArticle"):
        pmid = article.findtext(".//PMID") or ""
        title_node = article.find(".//ArticleTitle")
        title = clean_text("".join(title_node.itertext())) if title_node is not None else ""
        if not title:
            continue
        abstract_parts = []
        for node in article.findall(".//Abstract/AbstractText"):
            text = clean_text("".join(node.itertext()))
            if text:
                abstract_parts.append(text)
        summary = " ".join(abstract_parts)
        journal = clean_text(article.findtext(".//Journal/Title") or "PubMed")
        published = _pubmed_date(article)
        if published and (now - published).days > source.max_age_days:
            continue
        items.append(
            {
                "title": title,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                "published_at": published or now,
                "summary": truncate(summary, 480),
                "author": clean_text(article.findtext(".//AuthorList/Author/LastName") or ""),
                "source_id": source.id,
                "source_name": journal,
                "source_type": "research",
                "category_hint": source.category,
                "max_age_days": source.max_age_days,
                "raw": {"pmid": pmid, "journal": journal},
            }
        )
    return SourceResult(source.id, source.name, "research", items[: source.limit])


def fetch_source(source: Source, config: Config) -> SourceResult:
    try:
        if source.type == "google_news":
            return fetch_google_news(source, config)
        if source.type == "rss":
            return fetch_rss(source, config)
        if source.type == "nice":
            return fetch_nice(source, config)
        if source.type == "ispor":
            return fetch_ispor(source, config)
        if source.type == "pubmed":
            return fetch_pubmed(source, config)
        raise ValueError(f"Unknown source type: {source.type}")
    except Exception as exc:
        log.exception("Source failed: %s", source.id)
        return SourceResult(source.id, source.name, source.type, [], "error", str(exc))
