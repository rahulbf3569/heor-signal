from __future__ import annotations

import threading
from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from radar.config import load_config
from radar.db import Database, row_to_item
from radar.digest import date_label, digest_stats, grouped, _top_priority
from radar.pipeline import CATEGORY_ORDER, select_digest, today_key, update
from radar.util import compact_date, local_now, parse_datetime, relative_time, truncate, utcnow

config = load_config()
db = Database(config.database)

app = FastAPI(title=config.name, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(config.root / "static")), name="static")
templates = Jinja2Templates(directory=str(config.root / "templates"))

_update_lock = threading.Lock()
_update_state: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "message": "",
    "ok": None,
}


# ---------------------------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------------------------

def present(row: Any) -> dict[str, Any] | None:
    item = row_to_item(row)
    if not item:
        return None
    dt = item.get("published_dt") or item.get("first_seen_dt")
    item["when"] = relative_time(dt)
    item["dateline"] = compact_date(dt, config.timezone) if dt else ""
    item["saved"] = bool(item.get("is_saved"))
    item["linkedin"] = item.get("source_type") == "linkedin"
    item["source_label"] = (item.get("author") or "LinkedIn") if item["linkedin"] else (item.get("source_name") or "Unknown")
    item["host"] = urlparse(item.get("url") or "").netloc.replace("www.", "")
    item["summary_text"] = truncate(item.get("summary") or "", 300)
    meta = config.category(item.get("category") or "heor")
    item["category_key"] = meta.key
    item["category_label"] = meta.label
    item["category_short"] = meta.short
    item["category_color"] = meta.color
    item["category_blurb"] = meta.blurb
    return item


def present_many(rows: list[Any]) -> list[dict[str, Any]]:
    return [item for item in (present(row) for row in rows) if item]


def section_payload(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections = []
    for key, section_items in grouped(items).items():
        meta = config.category(key)
        sections.append(
            {
                "key": key,
                "label": meta.label,
                "short": meta.short,
                "color": meta.color,
                "blurb": meta.blurb,
                "items": section_items,
            }
        )
    return sections


def edition_index() -> list[dict[str, Any]]:
    rows = db.get_digest()
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        item = present(row)
        if item and item.get("digest_date"):
            buckets.setdefault(item["digest_date"], []).append(item)
    editions = []
    for date in sorted(buckets, reverse=True):
        items = buckets[date]
        editions.append(
            {
                "date": date,
                "label": date_label(date),
                "short": compact_date(
                    datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=None), config.timezone
                ),
                "count": len(items),
                "stats": digest_stats(items),
                "titles": [item["title"] for item in items[:3]],
                "categories": dict(Counter(item["category"] for item in items)),
            }
        )
    return editions


def matches(item: dict[str, Any], needle: str) -> bool:
    haystack = " ".join(
        [
            item.get("title") or "",
            item.get("summary") or "",
            item.get("author") or "",
            item.get("source_name") or "",
            " ".join(item.get("tags") or []),
        ]
    ).lower()
    return needle in haystack


def run_state() -> dict[str, Any]:
    state = dict(_update_state)
    last = db.last_run()
    state["last_run"] = last
    if last and last.get("finished_at"):
        state["last_run_label"] = relative_time(
            datetime.fromisoformat(last["finished_at"].replace("Z", "+00:00"))
        )
    return state


def _background_update() -> None:
    global _update_state
    if not _update_lock.acquire(blocking=False):
        return
    try:
        _update_state = {
            "running": True,
            "started_at": utcnow().isoformat(),
            "finished_at": None,
            "message": "Fetching sources…",
            "ok": None,
        }
        stats = update(config)
        _update_state.update(
            {
                "running": False,
                "finished_at": utcnow().isoformat(),
                "message": (
                    f"{stats['stored']} stored · {stats['selected']} selected · "
                    f"{stats['linkedin_enriched']} LinkedIn enriched"
                ),
                "ok": True,
            }
        )
    except Exception as exc:
        _update_state.update(
            {
                "running": False,
                "finished_at": utcnow().isoformat(),
                "message": f"Update failed: {exc}",
                "ok": False,
            }
        )
    finally:
        _update_lock.release()


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    date: str | None = None,
    category: str | None = None,
    q: str | None = None,
) -> HTMLResponse:
    editions = edition_index()
    valid_dates = [entry["date"] for entry in editions]
    selected_date = date if date else (valid_dates[0] if valid_dates else today_key(config))
    items = present_many(db.get_digest(selected_date))
    edition = next((entry for entry in editions if entry["date"] == selected_date), None)

    all_count = len(items)
    counts = Counter(item["category"] for item in items)
    active_category = category if category in config.categories else None
    needle = (q or "").strip()

    filtered = items
    if active_category:
        filtered = [item for item in filtered if item["category"] == active_category]
    if needle:
        filtered = [item for item in filtered if matches(item, needle.lower())]

    show_top = bool(filtered) and not active_category and not needle
    top = max(filtered, key=_top_priority) if show_top else None
    body_items = [item for item in filtered if top is None or item["id"] != top["id"]]

    index_now = valid_dates.index(selected_date) if selected_date in valid_dates else -1
    newer = valid_dates[index_now - 1] if index_now > 0 else None
    older = valid_dates[index_now + 1] if 0 <= index_now < len(valid_dates) - 1 else None

    if selected_date == today_key(config):
        day_heading = "Today"
    else:
        day_heading = "Edition"

    context = {
        "request": request,
        "app": config,
        "edition": edition,
        "edition_date": selected_date,
        "edition_label": date_label(selected_date),
        "day_heading": day_heading,
        "is_today": selected_date == today_key(config),
        "items": items,
        "filtered": filtered,
        "counts": dict(counts),
        "all_count": all_count,
        "top": top,
        "sections": section_payload(body_items),
        "categories": config.categories,
        "category_order": CATEGORY_ORDER,
        "active_category": active_category,
        "q": needle,
        "editions": editions,
        "newer": newer,
        "older": older,
        "stats": digest_stats(items),
        "last_run": run_state(),
        "has_any": bool(editions) or bool(items),
    }
    return templates.TemplateResponse(request=request, name="index.html", context=context)


@app.get("/archive", response_class=HTMLResponse)
def archive(request: Request) -> HTMLResponse:
    editions = edition_index()
    context = {
        "request": request,
        "app": config,
        "editions": editions,
        "categories": config.categories,
        "page": "archive",
        "last_run": run_state(),
        "total": sum(entry["count"] for entry in editions),
    }
    return templates.TemplateResponse(request=request, name="archive.html", context=context)


@app.get("/saved", response_class=HTMLResponse)
def saved(request: Request) -> HTMLResponse:
    items = present_many(db.get_saved())
    context = {
        "request": request,
        "app": config,
        "items": items,
        "categories": config.categories,
        "page": "saved",
        "last_run": run_state(),
    }
    return templates.TemplateResponse(request=request, name="saved.html", context=context)


@app.get("/sources", response_class=HTMLResponse)
def sources(request: Request) -> HTMLResponse:
    rows = db.source_status()
    for row in rows:
        row["when"] = relative_time(parse_datetime(row.get("last_run")))
    healthy = sum(1 for row in rows if row.get("status") == "ok")
    context = {
        "request": request,
        "app": config,
        "sources": rows,
        "healthy": healthy,
        "total": len(rows),
        "page": "sources",
        "last_run": run_state(),
        "configured": len(config.sources),
    }
    return templates.TemplateResponse(request=request, name="sources.html", context=context)


# ---------------------------------------------------------------------------
# JSON endpoints
# ---------------------------------------------------------------------------

@app.post("/api/save/{item_id}")
def toggle_save(item_id: str) -> JSONResponse:
    if not db.get_item(item_id):
        raise HTTPException(status_code=404, detail="Unknown item")
    try:
        saved = db.toggle_saved(item_id)
    except Exception:
        return JSONResponse(
            {"ok": False, "saved": False, "message": "Saving is disabled on this read-only deployment."},
            status_code=503,
        )
    return JSONResponse({"ok": True, "saved": saved})


@app.post("/api/update")
def trigger_update() -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "running": False,
            "message": (
                "This hosted copy is read-only. Updates run automatically every day "
                "via GitHub Actions. To refresh now, run `python run.py daily` locally "
                "or trigger the workflow on GitHub."
            ),
        },
        status_code=503,
    )


@app.get("/api/status")
def status() -> JSONResponse:
    return JSONResponse(run_state())


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "app": config.name,
            "editions": len(db.digest_dates()),
            "last_update": db.latest_update(),
        }
    )
