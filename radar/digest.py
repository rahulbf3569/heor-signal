from __future__ import annotations

from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
import re as _re

from .config import Config
from .pipeline import CATEGORY_ORDER
from .util import clean_text, relative_time, truncate

SECTION_ORDER = CATEGORY_ORDER


def _is_personal_announcement(item: dict[str, Any]) -> bool:
    text = f"{item.get('title') or ''} {item.get('summary') or ''}".lower()
    return bool(_re.search(
        r"\b(i'?ve (joined|started)|i am (joining|starting)|starting (a )?new (role|chapter|position)|"
        r"joined the team|joined our team|welcome to (our|the) team|thrilled to (join|announce)|"
        r"excited to (join|announce)|happy to (share|announce) (that i|I've|i have)|"
        r"i'?m (thrilled|excited|happy|pleased) to (share|announce|join))\b",
        text,
    ))


def _top_priority(item: dict[str, Any]) -> float:
    score = float(item.get("score") or 0)
    source_type = item.get("source_type") or ""
    if source_type in {"official", "research"}:
        score += 2.0
    elif source_type == "news":
        score += 0.6
    if _is_personal_announcement(item):
        score -= 6.0
    if source_type == "linkedin" and _re.search(
        r"\b(counting down|excited to share|meet (us|me) at|join us at|see you (at|in)|"
        r"will be there|preview series|save the date|arrived at|we'?ve (arrived|landed)|"
        r"i'?m at|i am at|book time|send (a )?message to|connecting,? collaborating|"
        r"celebrating women|women in (heor|health)|i'?ll be presenting|i'?m presenting|"
        r"presenting at (ispor|the)|podium session|poster session)\b",
        f"{item.get('title') or ''} {item.get('summary') or ''}".lower(),
    ):
        score -= 5.0
    return score


def date_label(value: str) -> str:
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return value
    return f"{dt.strftime('%A')}, {dt.day} {dt.strftime('%B %Y')}"


def digest_stats(items: list[dict[str, Any]]) -> dict[str, Any]:
    sources = {item.get("source_name") or "Unknown" for item in items}
    categories = Counter(item.get("category") or "heor" for item in items)
    linkedin = sum(1 for item in items if item.get("source_type") == "linkedin")
    return {
        "total": len(items),
        "sources": len(sources),
        "linkedin": linkedin,
        "categories": dict(categories),
    }


def _source_label(item: dict[str, Any]) -> str:
    if item.get("source_type") == "linkedin":
        return item.get("author") or "LinkedIn"
    return item.get("source_name") or "Unknown"


def _meta_line(item: dict[str, Any]) -> str:
    bits = [_source_label(item)]
    if item.get("published_dt"):
        bits.append(relative_time(item["published_dt"]))
    tags = item.get("tags") or []
    if tags:
        bits.append(" · ".join(tags[:3]))
    return "  ·  ".join(bits)


def grouped(items: list[dict[str, Any]]) -> "OrderedDict[str, list[dict[str, Any]]]":
    buckets: dict[str, list[dict[str, Any]]] = {key: [] for key in SECTION_ORDER}
    for item in items:
        buckets.setdefault(item.get("category") or "heor", []).append(item)
    for key in buckets:
        buckets[key].sort(key=lambda i: float(i.get("score") or 0), reverse=True)
    ordered: "OrderedDict[str, list[dict[str, Any]]]" = OrderedDict()
    for key in SECTION_ORDER:
        if buckets.get(key):
            ordered[key] = buckets[key]
    for key, value in buckets.items():
        if key not in ordered and value:
            ordered[key] = value
    return ordered


# ---------------------------------------------------------------------------
# Markdown edition
# ---------------------------------------------------------------------------

def render_markdown(config: Config, items: list[dict[str, Any]], date: str) -> str:
    stats = digest_stats(items)
    top = max(items, key=_top_priority) if items else None
    rest = [item for item in items if top is None or item["id"] != top["id"]]

    lines: list[str] = [
        f"# {config.name} — {date_label(date)}",
        "",
        f"> {config.tagline}",
        "",
        f"**{stats['total']} stories · {stats['sources']} sources · {stats['linkedin']} LinkedIn posts**",
        "",
    ]

    if top:
        label = config.category(top.get("category") or "heor").short
        lines += [
            "## Top story",
            "",
            f"### [{clean_text(top['title'])}]({top.get('url', '')})",
            "",
            f"**{_meta_line(top)}**",
            "",
        ]
        if top.get("summary"):
            lines += [clean_text(top["summary"]), ""]
        if label:
            lines += [f"`{label}`", ""]

    for key, section_items in grouped(rest).items():
        category = config.category(key)
        lines += [f"## {category.label} · {len(section_items)}", ""]
        for item in section_items:
            lines += [
                f"### [{clean_text(item['title'])}]({item.get('url', '')})",
                "",
                f"**{_meta_line(item)}**",
                "",
            ]
            summary = clean_text(item.get("summary") or "")
            if summary:
                lines += [truncate(summary, 420), ""]

    lines += [
        "---",
        "",
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · {config.name}",
        "",
    ]
    return "\n".join(lines).strip() + "\n"


def write_markdown(config: Config, date: str, text: str) -> Path:
    path = config.digest_dir / f"{date}.md"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Telegram edition
# ---------------------------------------------------------------------------

def _telegram_block(item: dict[str, Any], index: int | None = None) -> str:
    prefix = f"{index}. " if index else "• "
    title = clean_text(item["title"])
    source = _source_label(item)
    when = relative_time(item["published_dt"]) if item.get("published_dt") else ""
    meta = f"{source} · {when}" if when else source
    return f"{prefix}{title}\n   {meta}\n   {item.get('url', '')}"


def render_telegram_messages(
    config: Config, items: list[dict[str, Any]], date: str, limit: int = 3500
) -> list[str]:
    if not items:
        return []

    top = max(items, key=_top_priority)
    rest = [item for item in items if item["id"] != top["id"]]

    blocks: list[str] = [
        f"TOP STORY\n{top['title']}\n{_source_label(top)} · "
        f"{relative_time(top['published_dt']) if top.get('published_dt') else ''}\n{top.get('url', '')}",
    ]
    for key, section_items in grouped(rest).items():
        category = config.category(key)
        blocks.append(f"{category.label.upper()} ({len(section_items)})")
        for item in section_items:
            blocks.append(_telegram_block(item))

    header = f"HEOR SIGNAL · {date_label(date)}"
    if config.public_base_url:
        header += f"\nFull edition · {config.public_base_url}/?date={date}"
    pages: list[str] = []
    current: list[str] = []

    def length(chunks: list[str]) -> int:
        return len("\n\n".join(chunks))

    for block in blocks:
        if length(current) + len(block) + 2 > limit and current:
            pages.append("\n\n".join(current))
            current = []
        if len(block) > limit:
            block = truncate(block, limit - 10)
        current.append(block)
    if current:
        pages.append("\n\n".join(current))

    total = len(pages)
    if total == 1:
        return [f"{header}\n\n{pages[0]}"]
    return [f"{header} ({i + 1}/{total})\n\n{page}" for i, page in enumerate(pages)]


# ---------------------------------------------------------------------------
# Edition payload used by the dashboard / CLI summary
# ---------------------------------------------------------------------------

def build_edition(config: Config, items: list[dict[str, Any]], date: str) -> dict[str, Any]:
    stats = digest_stats(items)
    top = max(items, key=_top_priority) if items else None
    return {
        "date": date,
        "label": date_label(date),
        "items": items,
        "top": top,
        "stats": stats,
        "sections": grouped(items),
        "categories": {key: config.category(key) for key in config.categories},
        "markdown": render_markdown(config, items, date) if items else "",
        "telegram": render_telegram_messages(config, items, date) if items else [],
    }
