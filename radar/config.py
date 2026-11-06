from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .util import ROOT


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass
class Category:
    key: str
    label: str
    short: str
    color: str
    blurb: str = ""


@dataclass
class Source:
    id: str
    name: str
    type: str
    category: str
    url: str = ""
    query: str = ""
    term: str = ""
    limit: int = 10
    max_age_days: int = 7
    locale: str = "en"
    gl: str = "US"
    ceid: str = "US:en"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    name: str
    tagline: str
    timezone: str
    max_items: int
    min_score: float
    min_store_score: float
    max_linkedin: int
    max_per_source: int
    max_per_category: int
    max_linkedin_enrich: int
    decode_limit: int
    database: Path
    digest_dir: Path
    log_dir: Path
    categories: dict[str, Category]
    sources: list[Source]
    root: Path
    host: str
    port: int
    telegram_token: str
    telegram_chat_id: str
    public_base_url: str

    def category(self, key: str) -> Category:
        return self.categories.get(key) or self.categories["heor"]

    def source(self, source_id: str) -> Source | None:
        return next((s for s in self.sources if s.id == source_id), None)


def load_config(path: str | Path | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    config_path = Path(path) if path else ROOT / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    app = data.get("app", {}) or {}
    categories = {
        key: Category(
            key=key,
            label=value.get("label", key.title()),
            short=value.get("short", value.get("label", key.title())),
            color=value.get("color", "#40508F"),
            blurb=value.get("blurb", ""),
        )
        for key, value in (data.get("categories", {}) or {}).items()
    }
    sources = []
    for raw in data.get("sources", []) or []:
        known = {"id", "name", "type", "category", "url", "query", "term", "limit", "max_age_days", "locale", "gl", "ceid"}
        extra = {k: v for k, v in raw.items() if k not in known}
        sources.append(
            Source(
                id=raw["id"],
                name=raw.get("name", raw["id"]),
                type=raw["type"],
                category=raw.get("category", "heor"),
                url=raw.get("url", ""),
                query=raw.get("query", ""),
                term=raw.get("term", ""),
                limit=int(raw.get("limit", 10)),
                max_age_days=int(raw.get("max_age_days", 7)),
                locale=raw.get("locale", "en"),
                gl=raw.get("gl", "US"),
                ceid=raw.get("ceid", "US:en"),
                extra=extra,
            )
        )

    database = ROOT / app.get("database", "data/heor_signal.db")
    digest_dir = ROOT / app.get("digest_dir", "data/digests")
    log_dir = ROOT / app.get("log_dir", "data/logs")
    for folder in (database.parent, digest_dir, log_dir):
        folder.mkdir(parents=True, exist_ok=True)

    return Config(
        name=app.get("name", "HEOR Signal"),
        tagline=app.get("tagline", "The daily read on evidence, access & policy."),
        timezone=app.get("timezone", "Asia/Kolkata"),
        max_items=int(app.get("max_items", 42)),
        min_score=float(app.get("min_score", 5.0)),
        min_store_score=float(app.get("min_store_score", 3.0)),
        max_linkedin=int(app.get("max_linkedin", 10)),
        max_per_source=int(app.get("max_per_source", 5)),
        max_per_category=int(app.get("max_per_category", 9)),
        max_linkedin_enrich=int(app.get("max_linkedin_enrich", 16)),
        decode_limit=int(app.get("decode_limit", 120)),
        database=database,
        digest_dir=digest_dir,
        log_dir=log_dir,
        categories=categories,
        sources=sources,
        root=ROOT,
        host=_env("HOST", "127.0.0.1"),
        port=int(_env("PORT", "8765") or 8765),
        telegram_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        public_base_url=_env("PUBLIC_BASE_URL").rstrip("/"),
    )
