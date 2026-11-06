from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

# Windows consoles often default to cp1252, which cannot print the brief's
# middle dots and arrows. Force UTF-8 with replacement instead of crashing.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from radar.classify import classify
from radar.config import load_config
from radar.db import Database, row_to_item
from radar.digest import (
    build_edition,
    date_label,
    digest_stats,
    render_markdown,
    render_telegram_messages,
    write_markdown,
)
from radar.enrich import enrich_linkedin, enrich_page, extend_headline
from radar.pipeline import select_digest, today_key, update
from radar.telegram import TelegramError, discover_chats, send_messages, send_test
from radar.util import (
    canonical_url,
    clean_text,
    iso,
    item_id,
    local_now,
    parse_datetime,
    relative_time,
    safe_json,
    truncate,
    utcnow,
)

log = logging.getLogger("heor_signal")


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def rule(title: str = "") -> None:
    print()
    if title:
        print(title)
        print("─" * min(72, max(24, len(title))))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_update(args: argparse.Namespace) -> int:
    config = load_config()
    rule(f"{config.name} · update")
    stats = update(config, args.source or None, progress=lambda msg: print(f"  {msg}"))
    rule("Done")
    print(f"  fetched       {stats['fetched']}")
    print(f"  candidates    {stats['candidates']}")
    print(f"  stored        {stats['stored']}")
    print(f"  LinkedIn      {stats['linkedin_enriched']} enriched")
    print(f"  today's digest {stats['selected']} items")
    print(f"  duration      {stats['duration_s']}s")
    if stats["errors"]:
        print("  notes")
        for error in stats["errors"][:6]:
            print(f"    · {error}")
    return 0 if stats["sources_ok"] else 1


def cmd_select(args: argparse.Namespace) -> int:
    config = load_config()
    db = Database(config.database)
    date = args.date or today_key(config)
    items = select_digest(config, db, date)
    stats = digest_stats(items)
    print(f"{date_label(date)}: {stats['total']} items, {stats['sources']} sources")
    for item in items[:10]:
        print(f"  {item['score']:>5.2f}  {item['category']:<14} {item['title'][:86]}")
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    config = load_config()
    date = args.date or today_key(config)
    rule(f"{config.name} · daily edition")

    if not args.skip_update:
        stats = update(config, progress=lambda msg: print(f"  {msg}"))
        print(
            f"  fetched {stats['fetched']} · stored {stats['stored']} · "
            f"selected {stats['selected']} · {stats['duration_s']}s"
        )
        if stats["errors"]:
            for error in stats["errors"][:5]:
                print(f"  note: {error}")

    db = Database(config.database)
    items = select_digest(config, db, date)
    if not items:
        print("\nNo items passed the digest threshold. Try `python run.py update` again later.")
        return 1

    edition = build_edition(config, items, date)
    path = write_markdown(config, date, edition["markdown"])
    rule(f"{date_label(date)}")
    print(f"  {edition['stats']['total']} stories · {edition['stats']['sources']} sources · {edition['stats']['linkedin']} LinkedIn")
    print(f"  edition saved to {path}")

    if args.no_telegram:
        print("  Telegram skipped (--no-telegram)")
        return 0

    if not config.telegram_token or not config.telegram_chat_id:
        print("  Telegram not configured. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env")
        return 0

    try:
        sent = send_messages(
            config.telegram_token,
            config.telegram_chat_id,
            edition["telegram"],
        )
    except TelegramError as exc:
        print(f"  Telegram failed: {exc}")
        return 2
    print(f"  Telegram sent ({sent} message{'s' if sent != 1 else ''})")
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    config = load_config()
    db = Database(config.database)
    date = args.date or today_key(config)
    items = db.get_digest_items(date)
    if not items:
        print(f"No digest exists for {date}.")
        return 1
    text = render_markdown(config, items, date)
    path = write_markdown(config, date, text)
    print(f"Wrote {path} ({len(items)} items)")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    config = load_config()
    host = args.host or config.host
    port = args.port or config.port
    rule(f"{config.name} · dashboard")
    print(f"  http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}")
    uvicorn.run("app:app", host=host, port=port, log_level="info", reload=args.reload)
    return 0


def _manual_item(
    config,
    url: str,
    data: dict,
    source_type: str,
    category: str | None,
    saved: bool = True,
) -> dict:
    title = clean_text(data.get("title") or "")
    summary = clean_text(data.get("summary") or "")
    published = data.get("published_at") or utcnow()
    classification = classify(
        title=title,
        summary=summary,
        source_type=source_type,
        default_category=category or "heor",
        published_at=published,
    ) or {}
    resolved_category = category or classification.get("category") or "heor"
    fingerprint = hashlib.sha1(
        ("url:" + canonical_url(url)).encode("utf-8")
    ).hexdigest()
    host = urlparse(url).netloc.replace("www.", "")
    return {
        "id": item_id(fingerprint),
        "fingerprint": fingerprint,
        "title": title,
        "url": canonical_url(url),
        "source_id": "manual",
        "source_name": "LinkedIn" if source_type == "linkedin" else host,
        "source_type": source_type,
        "category": resolved_category,
        "published_at": iso(published),
        "first_seen": iso(utcnow()),
        "summary": truncate(summary, 700),
        "author": clean_text(data.get("author") or ""),
        "tags": json.dumps(classification.get("tags") or [], ensure_ascii=False),
        "score": max(float(classification.get("score") or 0), 9.0 if saved else 0.0),
        "max_age_days": 365,
        "is_saved": 1 if saved else 0,
        "digest_date": None,
        "raw": safe_json({"manual": True, "image": data.get("image") or ""}),
    }


def _add_one(config, url: str, category: str | None, saved: bool) -> tuple[bool, str]:
    is_linkedin = "linkedin.com" in url.lower()
    data = enrich_linkedin(url) if is_linkedin else enrich_page(url)
    if not data:
        return False, "could not read the page"
    if not data.get("title"):
        return False, "no title found"
    item = _manual_item(
        config,
        data.get("url") or url,
        data,
        "linkedin" if is_linkedin else "news",
        category,
        saved=saved,
    )
    db = Database(config.database)
    db.merge_item(item)
    if saved:
        db.update_item(item["id"], is_saved=1)
    return True, item["title"]


def cmd_add(args: argparse.Namespace) -> int:
    config = load_config()
    failures = 0
    for url in args.urls:
        try:
            ok, message = _add_one(config, url, args.category, saved=not args.unsaved)
        except Exception as exc:
            ok, message = False, str(exc)
        if ok:
            print(f"  added  {message}")
        else:
            failures += 1
            print(f"  failed {url} — {message}")
    if args.unsaved:
        print("  items are stored in the dashboard but not marked saved")
    else:
        print("  find them under Saved in the dashboard")
    return 1 if failures else 0


SEEDS = [
    "https://www.linkedin.com/posts/shubhrampandey_realworldevidence-rwe-heor-share-7518768361133068289-UwYb/",
    "https://www.linkedin.com/feed/update/urn:li:activity:7516774228881223680/",
]


def cmd_seed(args: argparse.Namespace) -> int:
    config = load_config()
    rule("Seeding example LinkedIn posts")
    for url in SEEDS:
        try:
            ok, message = _add_one(config, url, None, saved=True)
        except Exception as exc:
            ok, message = False, str(exc)
        print(("  saved  " if ok else "  failed ") + message)
    return 0


def cmd_reclassify(args: argparse.Namespace) -> int:
    config = load_config()
    db = Database(config.database)
    changed = 0
    for row in db.all_items():
        item = row_to_item(row)
        if not item:
            continue
        if item.get("source_type") == "linkedin":
            try:
                raw_data = json.loads(item.get("raw") or "{}")
            except Exception:
                raw_data = {}
            body = ((raw_data.get("linkedin") or {}).get("body") or "") if isinstance(raw_data, dict) else ""
            if body:
                extended = extend_headline(item["title"], body)
                if extended != item["title"]:
                    db.update_item(item["id"], title=extended)
                    item["title"] = extended
        result = classify(
            title=item["title"],
            summary=item.get("summary") or "",
            source_type=item.get("source_type") or "news",
            default_category=item.get("category") or "heor",
            published_at=item.get("published_dt"),
        )
        if not result:
            if float(item.get("score") or 0) != 0:
                db.update_item(item["id"], score=0)
                changed += 1
            continue
        if result["category"] != item["category"] or result["score"] != item["score"]:
            db.update_item(
                item["id"],
                category=result["category"],
                score=result["score"],
                tags=json.dumps(result["tags"], ensure_ascii=False),
            )
            changed += 1
    print(f"Reclassified {changed} items")
    date = args.date or today_key(config)
    items = select_digest(config, db, date)
    print(f"Rebuilt {date_label(date)}: {len(items)} items")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config()
    db = Database(config.database)
    last = db.last_run()
    rule("Last run")
    if last:
        print(f"  {last['started_at']} → {last['finished_at']}  [{last['status']}]")
        print(f"  fetched {last['fetched']} · stored {last['stored']} · selected {last['selected']}")
        if last.get("notes"):
            print(f"  {last['notes']}")
    else:
        print("  never run")
    rule("Sources")
    for source in db.source_status():
        marker = "ok  " if source["status"] == "ok" else "fail"
        print(f"  [{marker}] {source['name'][:34]:<34} {source['item_count']:>3} items  {source.get('error') or ''}")
    rule("Editions")
    for entry in db.digest_dates()[:10]:
        print(f"  {entry['digest_date']}  {entry['count']} items")
    return 0


def cmd_telegram_discover(args: argparse.Namespace) -> int:
    config = load_config()
    token = args.token or config.telegram_token
    if not token:
        print("Set TELEGRAM_BOT_TOKEN in .env or pass --token")
        return 1
    chats = discover_chats(token)
    if not chats:
        print("No chats found. Open Telegram, message your bot, then run this again.")
        return 1
    for chat in chats:
        print(
            f"  chat_id={chat['chat_id']}  type={chat['type']}  "
            f"name={chat['name']}  @{chat['username']}"
        )
    print("\nPut the chat_id in .env as TELEGRAM_CHAT_ID")
    return 0


def cmd_telegram_test(args: argparse.Namespace) -> int:
    config = load_config()
    token = args.token or config.telegram_token
    chat_id = args.chat or config.telegram_chat_id
    if not token or not chat_id:
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return 1
    try:
        send_test(token, chat_id, config.name)
    except TelegramError as exc:
        print(f"Telegram failed: {exc}")
        return 2
    print("Test message sent.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="HEOR Signal — fetch, read and deliver the daily HEOR brief.",
    )
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("update", help="fetch sources and refresh today's digest")
    p.add_argument("--source", action="append", help="only run this source id (repeatable)")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("select", help="rebuild today's selection without fetching")
    p.add_argument("--date")
    p.set_defaults(func=cmd_select)

    p = sub.add_parser("daily", help="fetch, select, write Markdown and send Telegram")
    p.add_argument("--date")
    p.add_argument("--skip-update", action="store_true")
    p.add_argument("--no-telegram", action="store_true")
    p.set_defaults(func=cmd_daily)

    p = sub.add_parser("digest", help="write the Markdown edition for an existing digest")
    p.add_argument("--date")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("serve", help="run the web dashboard")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("add", help="add a LinkedIn post or article by URL")
    p.add_argument("urls", nargs="+")
    p.add_argument("--category", choices=["policy", "market_access", "hta", "heor", "cro", "ai"])
    p.add_argument("--unsaved", action="store_true", help="store without marking it saved")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("seed", help="add the two example LinkedIn posts as saved items")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("reclassify", help="rescore everything after tuning the classifier")
    p.add_argument("--date")
    p.set_defaults(func=cmd_reclassify)

    p = sub.add_parser("status", help="last run, source health and recent editions")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("telegram-discover", help="find the chat id for your bot")
    p.add_argument("--token")
    p.set_defaults(func=cmd_telegram_discover)

    p = sub.add_parser("telegram-test", help="send a test Telegram message")
    p.add_argument("--token")
    p.add_argument("--chat")
    p.set_defaults(func=cmd_telegram_test)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
