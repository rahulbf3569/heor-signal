from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .util import iso, parse_datetime


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    source_id TEXT,
    source_name TEXT,
    source_type TEXT,
    category TEXT,
    published_at TEXT,
    first_seen TEXT NOT NULL,
    summary TEXT,
    author TEXT,
    tags TEXT,
    score REAL DEFAULT 0,
    max_age_days INTEGER DEFAULT 7,
    is_saved INTEGER DEFAULT 0,
    digest_date TEXT,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_digest ON items(digest_date);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_score ON items(score);
CREATE INDEX IF NOT EXISTS idx_items_saved ON items(is_saved);

CREATE TABLE IF NOT EXISTS source_status (
    source_id TEXT PRIMARY KEY,
    name TEXT,
    last_run TEXT,
    status TEXT,
    item_count INTEGER DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    finished_at TEXT,
    fetched INTEGER DEFAULT 0,
    stored INTEGER DEFAULT 0,
    selected INTEGER DEFAULT 0,
    status TEXT,
    notes TEXT
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        try:
            conn.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass
        return conn

    def init(self) -> None:
        try:
            with self.connect() as conn:
                conn.executescript(SCHEMA)
        except Exception:
            # Read-only filesystem (e.g. Vercel): schema already exists in the
            # bundled database; nothing to create.
            pass

    # ---- items ---------------------------------------------------------
    def get_by_fingerprint(self, fingerprint: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM items WHERE fingerprint = ?", (fingerprint,)).fetchone()

    def get_item(self, item_id: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()

    def insert_item(self, item: dict[str, Any]) -> bool:
        columns = [
            "id",
            "fingerprint",
            "title",
            "url",
            "source_id",
            "source_name",
            "source_type",
            "category",
            "published_at",
            "first_seen",
            "summary",
            "author",
            "tags",
            "score",
            "max_age_days",
            "is_saved",
            "digest_date",
            "raw",
        ]
        values = [item.get(col) for col in columns]
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO items ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(columns))})",
                values,
            )
        return True

    def merge_item(self, item: dict[str, Any]) -> str:
        """Insert a new item, or enrich an existing one without losing curation."""
        fp = item.get("fingerprint", "")
        existing = self.get_by_fingerprint(fp)
        if not existing:
            self.insert_item(item)
            return str(item["id"])

        updates: dict[str, Any] = {}
        # Upgrade a Google News redirect to the real publisher URL.
        existing_url = existing["url"] or ""
        new_url = item.get("url") or ""
        if new_url and (not existing_url or "news.google.com" in existing_url):
            updates["url"] = new_url

        if item.get("source_type") == "linkedin" and item.get("title"):
            existing_title = existing["title"] or ""
            new_title = item["title"]
            if new_title != existing_title and (
                len(new_title) <= len(existing_title)
                or new_title.startswith(existing_title)
                or len(existing_title) < 70
            ):
                updates["title"] = new_title

        if float(item.get("score") or 0) > float(existing["score"] or 0):
            updates["score"] = item["score"]
            updates["category"] = item.get("category", existing["category"])
            updates["tags"] = item.get("tags", existing["tags"])
        if item.get("summary") and (
            not existing["summary"] or len(item["summary"]) > len(existing["summary"])
        ):
            updates["summary"] = item["summary"]
        if not existing["author"] and item.get("author"):
            updates["author"] = item["author"]
        if not existing["published_at"] and item.get("published_at"):
            updates["published_at"] = item["published_at"]
        if int(item.get("max_age_days") or 0) > int(existing["max_age_days"] or 0):
            updates["max_age_days"] = item["max_age_days"]
        if updates:
            self.update_item(str(existing["id"]), **updates)
        return str(existing["id"])

    def update_item(self, item_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "title",
            "url",
            "source_name",
            "source_type",
            "category",
            "published_at",
            "summary",
            "author",
            "tags",
            "score",
            "max_age_days",
            "is_saved",
            "digest_date",
            "raw",
        }
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self.connect() as conn:
            conn.execute(f"UPDATE items SET {assignments} WHERE id = ?", [*fields.values(), item_id])

    def toggle_saved(self, item_id: str) -> bool:
        with self.connect() as conn:
            conn.execute(
                "UPDATE items SET is_saved = CASE WHEN is_saved = 1 THEN 0 ELSE 1 END WHERE id = ?",
                (item_id,),
            )
            row = conn.execute("SELECT is_saved FROM items WHERE id = ?", (item_id,)).fetchone()
        return bool(row and row["is_saved"])

    def all_candidates(self, today: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """SELECT * FROM items
                   WHERE (digest_date IS NULL OR digest_date = ?)
                   ORDER BY score DESC, published_at DESC""",
                (today,),
            ).fetchall()

    def all_items(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM items ORDER BY published_at DESC, score DESC"
            ).fetchall()

    def unassign_digest(self, date: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE items SET digest_date = NULL WHERE digest_date = ?", (date,))

    def assign_digest(self, item_ids: Iterable[str], date: str) -> None:
        ids = list(item_ids)
        if not ids:
            return
        with self.connect() as conn:
            conn.executemany("UPDATE items SET digest_date = ? WHERE id = ?", [(date, i) for i in ids])

    def get_digest(self, date: str | None = None) -> list[sqlite3.Row]:
        with self.connect() as conn:
            if date:
                rows = conn.execute(
                    "SELECT * FROM items WHERE digest_date = ? ORDER BY score DESC, published_at DESC",
                    (date,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM items WHERE digest_date IS NOT NULL ORDER BY digest_date DESC, score DESC"
                ).fetchall()
        return rows

    def get_digest_items(self, date: str) -> list[dict[str, Any]]:
        rows = self.get_digest(date)
        items = [row_to_item(row) for row in rows]
        return [item for item in items if item]

    def get_saved(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM items WHERE is_saved = 1 ORDER BY published_at DESC, score DESC"
            ).fetchall()

    def digest_dates(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT digest_date, COUNT(*) AS count
                   FROM items WHERE digest_date IS NOT NULL
                   GROUP BY digest_date ORDER BY digest_date DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_digest_date(self) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT digest_date FROM items WHERE digest_date IS NOT NULL ORDER BY digest_date DESC LIMIT 1"
            ).fetchone()
        return row["digest_date"] if row else None

    def latest_update(self) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT MAX(first_seen) AS last_seen FROM items").fetchone()
        return row["last_seen"] if row else None

    def category_counts(self, date: str | None = None) -> dict[str, int]:
        with self.connect() as conn:
            if date:
                rows = conn.execute(
                    "SELECT category, COUNT(*) AS count FROM items WHERE digest_date = ? GROUP BY category",
                    (date,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT category, COUNT(*) AS count FROM items WHERE digest_date IS NOT NULL GROUP BY category"
                ).fetchall()
        return {row["category"]: row["count"] for row in rows}

    def source_counts(self, date: str | None = None) -> dict[str, int]:
        with self.connect() as conn:
            if date:
                rows = conn.execute(
                    "SELECT source_name, COUNT(*) AS count FROM items WHERE digest_date = ? GROUP BY source_name",
                    (date,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT source_name, COUNT(*) AS count FROM items WHERE digest_date IS NOT NULL GROUP BY source_name"
                ).fetchall()
        return {row["source_name"]: row["count"] for row in rows}

    def linkedin_count(self, date: str | None = None) -> int:
        with self.connect() as conn:
            if date:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM items WHERE digest_date = ? AND source_type = 'linkedin'",
                    (date,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM items WHERE digest_date IS NOT NULL AND source_type = 'linkedin'"
                ).fetchone()
        return int(row["count"] if row else 0)

    # ---- source health --------------------------------------------------
    def record_source(self, source_id: str, name: str, status: str, count: int, error: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO source_status (source_id, name, last_run, status, item_count, error)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_id) DO UPDATE SET
                     name = excluded.name,
                     last_run = excluded.last_run,
                     status = excluded.status,
                     item_count = excluded.item_count,
                     error = excluded.error""",
                (source_id, name, iso(datetime.now(timezone.utc)), status, count, error[:500]),
            )

    def source_status(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM source_status ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(row) for row in rows]

    # ---- runs -----------------------------------------------------------
    def start_run(self) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO runs (started_at, status) VALUES (?, 'running')",
                (iso(datetime.now(timezone.utc)),),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, fetched: int, stored: int, selected: int, status: str, notes: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE runs SET finished_at = ?, fetched = ?, stored = ?, selected = ?,
                   status = ?, notes = ? WHERE id = ?""",
                (iso(datetime.now(timezone.utc)), fetched, stored, selected, status, notes[:1000], run_id),
            )

    def last_run(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def row_to_item(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    try:
        item["tags"] = json.loads(item.get("tags") or "[]")
    except Exception:
        item["tags"] = []
    item["published_dt"] = parse_datetime(item.get("published_at"))
    item["first_seen_dt"] = parse_datetime(item.get("first_seen"))
    return item
