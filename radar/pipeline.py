from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from .classify import classify
from .config import Config
from .db import Database, row_to_item
from .enrich import RateLimited, decode_google_news, enrich_linkedin, extend_headline
from .fetch import SourceResult, fetch_source
from .util import (
    canonical_url,
    clean_text,
    iso,
    item_id,
    local_now,
    parse_datetime,
    safe_json,
    truncate,
    utcnow,
)

log = logging.getLogger("heor_signal.pipeline")

CATEGORY_ORDER = ["policy", "market_access", "hta", "heor", "cro", "ai"]
LINKEDIN_HOSTS = ("linkedin.com/posts/", "linkedin.com/feed/update/")

# Financial wires and low-quality syndication produce truncated, context-free
# headlines that look relevant but are not useful in this brief.
BLOCKED_DOMAINS = {
    "pluang.com",
    "tradingview.com",
    "stocktitan.net",
    "marketscreener.com",
    "investing.com",
    "barchart.com",
}
BLOCKED_PATHS = ("/tags/", "/tag/")
BLOCKED_PUBLISHERS = {"TradingView", "Pluang", "Stock Titan", "MarketScreener"}
SOURCE_QUERY_CAPS = {"gnews_india": 3}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def today_key(config: Config) -> str:
    return local_now(config.timezone).strftime("%Y-%m-%d")


def _fingerprint(item: dict[str, Any]) -> str:
    """Stable identity: LinkedIn/research use the URL, everything else the title."""
    source_type = item.get("source_type") or ""
    url = item.get("url") or ""
    if source_type in {"linkedin", "research"} and url and "news.google.com" not in url:
        return hashlib.sha1(("url:" + canonical_url(url)).encode("utf-8")).hexdigest()

    base = clean_text(item.get("title") or "").lower()
    base = re.sub(r"https?://\S+", "", base)
    base = re.sub(r"[^a-z0-9#]+", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    if len(base) < 24 and url:
        base = f"{base} {canonical_url(url)}"
    if not base:
        base = canonical_url(url)
    return hashlib.sha1(base[:220].encode("utf-8")).hexdigest()


def _is_linkedin_post(url: str, source_type: str) -> bool:
    if source_type != "linkedin":
        return False
    lowered = (url or "").lower()
    return any(part in lowered for part in LINKEDIN_HOSTS)


def _age_days(value: datetime | None, now: datetime) -> float:
    if not value:
        return 0.0
    return max(0.0, (now - value).total_seconds() / 86400)


def _fresh_enough(item: dict[str, Any], now: datetime) -> bool:
    published = item.get("published_dt")
    if not published:
        return True
    max_age = int(item.get("max_age_days") or 7)
    # The daily edition should not carry month-old conference notices just
    # because the source keeps a long archive window.
    source_type = item.get("source_type") or "news"
    if source_type == "linkedin":
        max_age = min(max_age, 7)
    elif source_type == "research":
        max_age = min(max_age, 21)
    elif source_type == "official":
        max_age = min(max_age, 30)
    else:
        max_age = min(max_age, 10)
    return published >= now - timedelta(days=max_age)


def _url_allowed(url: str, publisher: str = "") -> bool:
    if publisher and publisher in BLOCKED_PUBLISHERS:
        return False
    if not url:
        return True
    lowered = url.lower()
    if any(part in lowered for part in BLOCKED_PATHS):
        return False
    host = urlparse(lowered).netloc
    return not any(host == domain or host.endswith("." + domain) for domain in BLOCKED_DOMAINS)


def _preliminary(raw: dict[str, Any], config: Config) -> dict[str, Any] | None:
    result = classify(
        title=raw.get("title", ""),
        summary=raw.get("summary", ""),
        source_type=raw.get("source_type", "news"),
        default_category=raw.get("category_hint") or "heor",
        published_at=raw.get("published_at"),
    )
    if not result:
        return None
    if result["score"] < config.min_store_score:
        return None
    raw = dict(raw)
    raw["classification"] = result
    return raw


def _normalise(raw: dict[str, Any], config: Config) -> dict[str, Any] | None:
    classification = classify(
        title=raw.get("title", ""),
        summary=raw.get("summary", ""),
        source_type=raw.get("source_type", "news"),
        default_category=raw.get("category_hint") or "heor",
        published_at=raw.get("published_at"),
    )
    if not classification or classification["score"] < config.min_store_score:
        return None

    title = clean_text(raw.get("title", ""))
    if (raw.get("source_type") or "") == "linkedin":
        body = ((raw.get("raw") or {}).get("linkedin") or {}).get("body") or ""
        title = extend_headline(title, body)
    if len(title) < 18:
        return None
    fingerprint = _fingerprint(raw)
    published = raw.get("published_at") or utcnow()
    now = utcnow()

    item = {
        "id": item_id(fingerprint),
        "fingerprint": fingerprint,
        "title": title,
        "url": raw.get("url") or "",
        "source_id": raw.get("source_id") or "",
        "source_name": clean_text(raw.get("source_name") or ""),
        "source_type": raw.get("source_type") or "news",
        "category": classification["category"],
        "published_at": iso(published),
        "first_seen": iso(now),
        "summary": truncate(clean_text(raw.get("summary") or ""), 700),
        "author": clean_text(raw.get("author") or ""),
        "tags": json.dumps(classification["tags"], ensure_ascii=False),
        "score": classification["score"],
        "max_age_days": int(raw.get("max_age_days") or 7),
        "is_saved": int(raw.get("is_saved") or 0),
        "digest_date": None,
        "raw": safe_json(raw.get("raw") or {}),
    }
    return item


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def fetch_all(
    config: Config,
    source_ids: Iterable[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[SourceResult]:
    wanted = set(source_ids or [])
    sources = [s for s in config.sources if not wanted or s.id in wanted]
    results: list[SourceResult] = []
    if not sources:
        return results

    workers = min(6, max(2, len(sources)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_source, source, config): source for source in sources}
        for future in as_completed(futures):
            source = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # defensive: fetch_source already catches
                result = SourceResult(source.id, source.name, source.type, [], "error", str(exc))
            results.append(result)
            if progress:
                state = "ok" if result.status == "ok" else "failed"
                progress(f"{source.name}: {state} ({len(result.items)} items)")
            log.info(
                "source=%s status=%s items=%s error=%s",
                source.id,
                result.status,
                len(result.items),
                result.error,
            )
    return results


def _decode_candidates(
    candidates: list[dict[str, Any]], config: Config
) -> dict[str, str]:
    google_urls = [raw["url"] for raw in candidates if "news.google.com" in (raw.get("url") or "")]
    google_urls = list(dict.fromkeys(google_urls))
    if not google_urls:
        return {}

    # Decode the most promising items first; LinkedIn posts are essential
    # because their public page is what gives us the post body.
    score_by_url: dict[str, float] = {}
    for raw in candidates:
        url = raw.get("url") or ""
        if "news.google.com" not in url:
            continue
        bonus = 5.0 if raw.get("source_type") == "linkedin" else 0.0
        score = float((raw.get("classification") or {}).get("score") or 0) + bonus
        score_by_url[url] = max(score_by_url.get(url, 0.0), score)

    ordered = sorted(google_urls, key=lambda u: score_by_url.get(u, 0.0), reverse=True)
    ordered = ordered[: max(1, config.decode_limit)]
    log.info("Decoding %s Google News links", len(ordered))
    return decode_google_news(ordered)


def _enrich_linkedin_candidates(
    candidates: list[dict[str, Any]], config: Config
) -> tuple[int, bool]:
    """Enrich the best LinkedIn posts. Returns (enriched count, rate_limited)."""
    seen: set[str] = set()
    targets: list[dict[str, Any]] = []
    for raw in sorted(
        candidates,
        key=lambda r: float((r.get("classification") or {}).get("score") or 0),
        reverse=True,
    ):
        if not _is_linkedin_post(raw.get("url") or "", raw.get("source_type") or ""):
            continue
        key = canonical_url(raw.get("url") or "")
        if key in seen:
            continue
        seen.add(key)
        targets.append(raw)
        if len(targets) >= max(1, config.max_linkedin_enrich):
            break

    enriched = 0
    for index, raw in enumerate(targets):
        if index:
            time.sleep(0.8)
        try:
            data = enrich_linkedin(raw["url"])
        except RateLimited:
            log.warning("LinkedIn rate limited after %s enrichments", enriched)
            return enriched, True
        except Exception as exc:
            log.info("LinkedIn enrichment failed for %s: %s", raw.get("url"), exc)
            continue
        if not data:
            continue
        if data.get("title"):
            raw["title"] = data["title"]
        if data.get("summary"):
            raw["summary"] = data["summary"]
        if data.get("author"):
            raw["author"] = data["author"]
        if data.get("published_at"):
            raw["published_at"] = data["published_at"]
        raw["source_name"] = "LinkedIn"
        raw["source_type"] = "linkedin"
        raw["max_age_days"] = max(int(raw.get("max_age_days") or 7), 7)
        base_raw = raw.setdefault("raw", {})
        base_raw["linkedin"] = {
            "author": data.get("author") or "",
            "image": data.get("image") or "",
            "headline": data.get("headline") or "",
            "body": truncate(data.get("body") or "", 2400),
        }
        enriched += 1
    return enriched, False


def update(
    config: Config,
    source_ids: Iterable[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Fetch, classify, enrich and store everything. Then refresh today's digest."""
    db = Database(config.database)
    run_id = db.start_run()
    started = utcnow()
    stats: dict[str, Any] = {
        "fetched": 0,
        "candidates": 0,
        "stored": 0,
        "selected": 0,
        "linkedin_enriched": 0,
        "sources_ok": 0,
        "sources_failed": 0,
        "errors": [],
    }

    try:
        results = fetch_all(config, source_ids, progress=progress)
        stats["sources_ok"] = sum(1 for r in results if r.status == "ok")
        stats["sources_failed"] = sum(1 for r in results if r.status != "ok")

        candidates: list[dict[str, Any]] = []
        for result in results:
            db.record_source(result.source_id, result.name, result.status, len(result.items), result.error)
            if result.error:
                stats["errors"].append(f"{result.name}: {result.error}")
            if result.status != "ok":
                continue
            stats["fetched"] += len(result.items)
            for raw in result.items:
                prepared = _preliminary(raw, config)
                if prepared and _url_allowed(
                    prepared.get("url") or "", prepared.get("source_name") or ""
                ):
                    candidates.append(prepared)
        stats["candidates"] = len(candidates)
        if progress:
            progress(f"Classified {len(candidates)} relevant candidates")

        # 1. Resolve Google News redirects so we link to the publisher, not Google.
        decoded = _decode_candidates(candidates, config)
        for raw in candidates:
            direct = decoded.get(raw.get("url") or "")
            if direct:
                raw["url"] = canonical_url(direct)
                if "linkedin.com" in raw["url"].lower():
                    raw["source_type"] = "linkedin"
                    raw["source_name"] = "LinkedIn"
        candidates = [
            raw
            for raw in candidates
            if _url_allowed(raw.get("url") or "", raw.get("source_name") or "")
        ]

        # 2. Read public LinkedIn post pages for full text and author.
        enriched, limited = _enrich_linkedin_candidates(candidates, config)
        stats["linkedin_enriched"] = enriched
        if limited:
            stats["errors"].append("LinkedIn rate limit reached; kept headline-only posts")
        if progress:
            progress(f"Enriched {enriched} LinkedIn posts")

        # 3. Store (dedupe on fingerprint).
        for raw in candidates:
            item = _normalise(raw, config)
            if not item:
                continue
            db.merge_item(item)
            stats["stored"] += 1

        # 4. Refresh today's digest.
        selected = select_digest(config, db, today_key(config))
        stats["selected"] = len(selected)
        stats["run_id"] = run_id
        stats["duration_s"] = round((utcnow() - started).total_seconds(), 1)

        notes = {
            "linkedin_enriched": enriched,
            "sources_failed": stats["sources_failed"],
            "errors": stats["errors"][:8],
        }
        status = "ok" if stats["sources_failed"] == 0 else ("partial" if stats["sources_ok"] else "error")
        db.finish_run(run_id, stats["fetched"], stats["stored"], stats["selected"], status, json.dumps(notes))
        return stats
    except Exception as exc:
        log.exception("Update failed")
        db.finish_run(run_id, stats["fetched"], stats["stored"], stats["selected"], "error", str(exc))
        raise


# ---------------------------------------------------------------------------
# Digest selection
# ---------------------------------------------------------------------------

def _story_key(title: str) -> tuple[str, ...]:
    """A sparse prefix key used to drop syndicated copies of the same story.

    Long words are shortened so "Biopharm" and "Biopharmaceuticals" collide,
    while the key stays long enough that different drug names or CMS rules do
    not.
    """
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    normalized = [word[:6] if len(word) > 6 else word for word in words]
    return tuple(normalized[:9])


def select_digest(config: Config, db: Database, date: str | None = None) -> list[dict[str, Any]]:
    """Pick today's edition. Safe to run repeatedly; the selection is rebuilt."""
    date = date or today_key(config)
    now = utcnow()
    db.unassign_digest(date)

    rows = db.all_candidates(date)
    candidates = [row_to_item(row) for row in rows]
    candidates = [item for item in candidates if item and _fresh_enough(item, now)]
    candidates = [
        item
        for item in candidates
        if _url_allowed(item.get("url") or "", item.get("source_name") or "")
    ]
    candidates = [item for item in candidates if float(item.get("score") or 0) >= config.min_score]

    def effective(item: dict[str, Any]) -> float:
        age = _age_days(item.get("published_dt"), now)
        return float(item.get("score") or 0) - min(2.4, age * 0.22)

    candidates.sort(key=effective, reverse=True)

    selected: list[dict[str, Any]] = []
    per_source: dict[str, int] = {}
    per_category: dict[str, int] = {}
    per_query: dict[str, int] = {}
    seen_stories: set[tuple[str, ...]] = set()
    linkedin = 0

    for item in candidates:
        if len(selected) >= config.max_items:
            break
        is_linkedin = item.get("source_type") == "linkedin"
        source_name = item.get("source_name") or item.get("source_id") or "unknown"
        source_id = item.get("source_id") or ""
        category = item.get("category") or "heor"

        key = _story_key(item.get("title") or "")
        if len(key) >= 6 and key in seen_stories:
            continue

        query_cap = SOURCE_QUERY_CAPS.get(source_id)
        if query_cap and per_query.get(source_id, 0) >= query_cap:
            continue
        if is_linkedin:
            if linkedin >= config.max_linkedin:
                continue
        elif per_source.get(source_name, 0) >= config.max_per_source:
            continue
        if per_category.get(category, 0) >= config.max_per_category:
            continue

        selected.append(item)
        per_category[category] = per_category.get(category, 0) + 1
        per_source[source_name] = per_source.get(source_name, 0) + 1
        per_query[source_id] = per_query.get(source_id, 0) + 1
        if len(key) >= 6:
            seen_stories.add(key)
        if is_linkedin:
            linkedin += 1

    db.assign_digest([item["id"] for item in selected], date)
    return db.get_digest_items(date)


def digest_items(db: Database, date: str | None = None) -> list[dict[str, Any]]:
    rows = db.get_digest(date) if date else db.get_digest()
    return [item for item in (row_to_item(row) for row in rows) if item]
