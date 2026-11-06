from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup

from .util import (
    HTTP_HEADERS,
    canonical_url,
    clean_text,
    first_sentence,
    parse_datetime,
    truncate,
)

log = logging.getLogger("heor_signal.enrich")


class RateLimited(Exception):
    """Raised when LinkedIn starts throttling guest requests."""


# ---------------------------------------------------------------------------
# Google News redirects
# ---------------------------------------------------------------------------

def decode_google_news(urls: Iterable[str], interval: float = 0.05) -> dict[str, str]:
    """Resolve news.google.com article links to the original publisher URL.

    Returns a mapping of input URL -> decoded URL. Failures are skipped; the
    caller can keep using the Google News redirect, which still works.
    """
    unique = [u for u in dict.fromkeys(urls) if u]
    if not unique:
        return {}

    try:
        from googlenewsdecoder import gnewsdecoder
    except Exception as exc:  # pragma: no cover - dependency optional at runtime
        log.warning("googlenewsdecoder unavailable: %s", exc)
        return {}

    decoded: dict[str, str] = {}
    for start in range(0, len(unique), 40):
        chunk = unique[start : start + 40]
        try:
            result = gnewsdecoder(chunk, interval=interval, timeout=15.0)
        except Exception as exc:
            log.warning("Google News decode batch failed: %s", exc)
            continue
        if isinstance(result, dict):
            result = [result]
        if not isinstance(result, list):
            continue
        for source_url, entry in zip(chunk, result):
            if isinstance(entry, dict) and entry.get("success") and entry.get("decoded_url"):
                decoded[source_url] = entry["decoded_url"]
    return decoded


# ---------------------------------------------------------------------------
# LinkedIn public posts
# ---------------------------------------------------------------------------

def extend_headline(title: str, body: str) -> str:
    """Turn a terse LinkedIn opener into a readable digest headline.

    Public posts often start with a one-line hook ("The election results are
    in.") and carry the substance in the next sentence. Use the body when the
    title is short and is clearly the opening of that body.
    """
    title = clean_text(title)
    body = clean_text(body)
    if not title or not body or len(title) >= 70:
        return title
    if body.lower().startswith(title.lower()):
        extended = truncate(body, 172)
        if len(extended) > len(title) + 20:
            return extended
    return title


def _iter_nodes(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_nodes(child)


def _load_json_ld(script: BeautifulSoup | Any) -> Any:
    raw = script.string or script.get_text() or ""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        # Some LinkedIn payloads include raw control characters.
        try:
            cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", raw)
            return json.loads(cleaned)
        except Exception:
            return None


def _social_posting(soup: BeautifulSoup) -> dict[str, Any] | None:
    for script in soup.find_all("script", type="application/ld+json"):
        data = _load_json_ld(script)
        for node in _iter_nodes(data):
            if not isinstance(node, dict):
                continue
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if any(value and "SocialMediaPosting" in str(value) for value in kinds):
                return node
    return None


def _author_name(posting: dict[str, Any]) -> str:
    author = posting.get("author")
    if isinstance(author, dict):
        return clean_text(author.get("name") or author.get("alternateName") or "")
    if isinstance(author, list) and author:
        return _author_name({"author": author[0]})
    if isinstance(author, str):
        return clean_text(author)
    return ""


def _posting_image(posting: dict[str, Any]) -> str:
    image = posting.get("image")
    if isinstance(image, str):
        return image
    if isinstance(image, dict):
        return image.get("url") or ""
    if isinstance(image, list) and image:
        return _posting_image({"image": image[0]})
    return ""


def enrich_linkedin(url: str, timeout: int = 24) -> dict[str, Any] | None:
    """Read a public LinkedIn post's JSON-LD without signing in."""
    headers = dict(HTTP_HEADERS)
    headers.update(
        {
            "Referer": "https://www.google.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    response = requests.get(url, headers=headers, timeout=timeout)
    if response.status_code == 429:
        raise RateLimited(url)
    if response.status_code != 200:
        log.info("LinkedIn returned %s for %s", response.status_code, url)
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    posting = _social_posting(soup) or {}

    body = clean_text(posting.get("articleBody") or "")
    if not body:
        tag = soup.find("meta", attrs={"property": "og:description"}) or soup.find(
            "meta", attrs={"name": "description"}
        )
        body = clean_text(tag.get("content")) if tag and tag.get("content") else ""

    og_title = ""
    tag = soup.find("meta", attrs={"property": "og:title"}) or soup.find("meta", attrs={"name": "og:title"})
    if tag and tag.get("content"):
        og_title = clean_text(tag["content"])

    author = _author_name(posting)
    if not author and "|" in og_title:
        maybe = clean_text(og_title.rsplit("|", 1)[-1])
        if 2 < len(maybe) < 80:
            author = maybe

    headline = clean_text(posting.get("headline") or "")
    if not headline and "|" in og_title:
        headline = clean_text(og_title.rsplit("|", 1)[0])

    title = ""
    for candidate in (headline, first_sentence(body, 165)):
        candidate = clean_text(candidate)
        if len(candidate) >= 22 and not candidate.startswith("#"):
            title = candidate
            break
    if not title:
        title = first_sentence(body, 165) or headline

    # Very short openers ("The election results are in.") make poor headlines.
    # Borrow the next sentence from the post body so the card is readable.
    title = extend_headline(title, body)

    if not body:
        body = headline or og_title

    summary = body
    if title and summary.lower().startswith(title.lower()):
        summary = summary[len(title) :].lstrip(" .:-—–\n\t")
    summary = truncate(summary, 620)

    published = parse_datetime(
        posting.get("datePublished")
        or posting.get("dateCreated")
        or posting.get("dateModified")
    )

    return {
        "title": title,
        "summary": summary,
        "author": author,
        "published_at": published,
        "image": _posting_image(posting),
        "url": canonical_url(str(posting.get("url") or url)),
        "body": body,
        "headline": headline,
    }


# ---------------------------------------------------------------------------
# Generic article metadata (used by `run.py add`)
# ---------------------------------------------------------------------------

def enrich_page(url: str, timeout: int = 25) -> dict[str, Any] | None:
    response = requests.get(url, headers=HTTP_HEADERS, timeout=timeout)
    if response.status_code != 200:
        return None
    soup = BeautifulSoup(response.text, "html.parser")

    title = ""
    summary = ""
    published: datetime | None = None
    author = ""

    for script in soup.find_all("script", type="application/ld+json"):
        data = _load_json_ld(script)
        for node in _iter_nodes(data):
            if not isinstance(node, dict):
                continue
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if not any(
                value and any(word in str(value) for word in ("Article", "Posting", "Report"))
                for value in kinds
            ):
                continue
            title = title or clean_text(node.get("headline") or node.get("name") or "")
            summary = summary or clean_text(node.get("description") or "")
            published = published or parse_datetime(
                node.get("datePublished") or node.get("dateCreated")
            )
            author = author or _author_name(node)

    if not title:
        tag = soup.find("meta", attrs={"property": "og:title"})
        title = clean_text(tag.get("content")) if tag else ""
    if not title and soup.title:
        title = clean_text(soup.title.get_text())
    if not summary:
        tag = soup.find("meta", attrs={"property": "og:description"}) or soup.find(
            "meta", attrs={"name": "description"}
        )
        summary = clean_text(tag.get("content")) if tag and tag.get("content") else ""
    if not published:
        for key in ("article:published_time", "og:updated_time"):
            tag = soup.find("meta", attrs={"property": key})
            if tag and tag.get("content"):
                published = parse_datetime(tag["content"])
                break

    if not title:
        return None
    return {
        "title": title,
        "summary": truncate(summary, 620),
        "author": author,
        "published_at": published,
        "url": canonical_url(url),
        "image": "",
    }
