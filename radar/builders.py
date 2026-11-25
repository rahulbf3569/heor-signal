"""Spot companies building AI tools for HEOR, RWE, SLR, regulatory/HTA and CROs.

An item is a "builder" story when it pairs AI with one of those domains and
reports something that happened to a product or company: a launch, a funding
round, an acquisition or a partnership. Stories naming a company on the
watchlist in config.yaml (`builders.watchlist`) qualify with a weaker signal.

Each builder story is tagged with its segment and stage, for example
"Funding · $95M Series B" and "Regulatory & writing".
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import yaml

from .util import ROOT

AI = re.compile(
    r"\b(ai|a\.i\.|artificial intelligence|gen ?ai|generative|agentic|ai[- ]agents?|"
    r"ai[- ](native|powered|driven|enabled)|llms?|large language models?|machine learning|copilot)\b",
    re.I,
)

# Segment label -> pattern. Order matters: the first match is the primary segment.
SEGMENTS: list[tuple[str, re.Pattern[str]]] = [
    ("SLR & evidence", re.compile(
        r"\b(systematic (literature )?reviews?|slrs?|literature reviews?|evidence synthesis|"
        r"meta[- ]analys[ie]s|network meta|prisma|screening and extraction)\b", re.I)),
    ("HEOR & access", re.compile(
        r"\b(heor|health economics?|economic model(l)?ing|cost[- ]effectiveness|"
        r"market access|value dossiers?|payer|pricing and reimbursement|"
        r"outcomes research|pico)\b", re.I)),
    ("HTA & JCA", re.compile(
        r"\b(health technology assessment|hta|joint clinical assessment|jca|amnog|nice submissions?)\b", re.I)),
    ("Regulatory & writing", re.compile(
        r"\b(regulatory (writing|affairs|submissions?|documents?|documentation|intelligence|platform|ai|tech)|"
        r"medical writ\w+|clinical study reports?|csrs?|ind|nda|bla|ectd|submission (package|documents?))\b",
        re.I)),
    ("RWE & data", re.compile(
        r"\b(real[- ]world (evidence|data)|rwe|rwd|ehr data|electronic health records?|"
        r"claims data|health data platform|observational (research|studies))\b", re.I)),
    ("CRO & trials", re.compile(
        r"\b(clinical trials?|clinical research|cros?|contract research|protocol design|"
        r"trial design|patient recruitment|clinical operations|trial sites?|clinical data platform|"
        r"drug development)\b", re.I)),
]

FUNDING = re.compile(
    r"\b(raises?|raised|raising|secures?|secured|closes?|closed|lands?|bags?|nabs?|rakes? in|"
    r"nets?|garners?|snags?|pulls? in|attracts?|reels? in)\b.{0,80}"
    r"(?:\b(?:funding|financing|round|seed|series [a-f]|investment|million|billion)\b|[$€£]\s?\d)|"
    r"\b(pre[- ]seed|seed round|seed funding|series [a-f]( round| funding)?|venture (funding|round))\b",
    re.I,
)
ACQUISITION = re.compile(
    r"\b(acquires?|acquired|acquisition of|to acquire|buys|bought|merges? with|merger)\b", re.I
)
LAUNCH = re.compile(
    r"\b(launch(es|ed)?|unveil(s|ed)?|introduc(es|ed)|debut(s|ed)?|rolls? out|rolled out|"
    r"releases?|released|now available|general availability|announces? (the )?(new|first)|"
    r"new (ai )?(platform|tool|product|agent|solution))\b",
    re.I,
)
PARTNERSHIP = re.compile(
    r"\b(partners? with|partnership|collaborat(es|ion) with|teams up|alliance|strategic agreement)\b", re.I
)

ROUND = re.compile(r"\b(pre[- ]seed|seed|series [a-f])\b", re.I)
AMOUNT = re.compile(
    r"(?:(?:[$€£]|us\$|usd\s?)\s?\d+(?:[.,]\d+)?\s?(?:m|mn|million|b|bn|billion)\b"
    r"|\b\d+(?:\.\d+)?\s?(?:million|billion)\s?(?:dollars|euros|usd|eur)?)",
    re.I,
)

# Builders stories are about health/life-science software, not generic AI.
HEALTH = re.compile(
    r"\b(health|healthcare|pharma\w*|biotech|biopharma|life sciences?|clinical|medical|"
    r"medtech|drug|patients?|trials?)\b",
    re.I,
)


@lru_cache(maxsize=1)
def watchlist() -> dict[str, str]:
    """Company name (lower case) -> segment, read from config.yaml."""
    try:
        with open(ROOT / "config.yaml", "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except Exception:
        return {}
    entries = ((data.get("builders") or {}).get("watchlist")) or []
    result: dict[str, str] = {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("name"):
            result[str(entry["name"]).lower()] = str(entry.get("segment") or "")
        elif isinstance(entry, str):
            result[entry.lower()] = ""
    return result


def _watch_hit(text: str) -> tuple[str, str] | None:
    lowered = text.lower()
    for name, segment in watchlist().items():
        if re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", lowered):
            return name, segment
    return None


def _format_amount(raw: str) -> str:
    """"$5 Billion" -> "$5B", "USD 47 million" -> "$47M", "€6 mn" -> "€6M"."""
    match = re.search(r"\d+(?:[.,]\d+)?", raw)
    if not match:
        return raw.strip()
    number = match.group(0)
    lowered = raw.lower()
    unit = "B" if re.search(r"\d\s?(b|bn|billion)\b", lowered) else "M"
    currency = "€" if ("€" in raw or "eur" in lowered) else "£" if "£" in raw else "$"
    return f"{currency}{number}{unit}"


def _stage(text: str) -> str:
    if ACQUISITION.search(text):
        return "Acquisition"
    if FUNDING.search(text):
        label = "Funding"
        round_match = ROUND.search(text)
        amount_match = AMOUNT.search(text)
        detail = " ".join(
            part
            for part in (
                _format_amount(amount_match.group(0)) if amount_match else "",
                round_match.group(0).title() if round_match else "",
            )
            if part
        )
        return f"{label} · {detail}" if detail else label
    if PARTNERSHIP.search(text):
        return "Partnership"
    if LAUNCH.search(text):
        return "Launch"
    return ""


# Pharma/life-science context strong enough to keep an AI launch or round
# that names no specific segment (e.g. "life sciences AI platform").
LIFESCI = re.compile(
    r"\b(pharma\w*|biopharma|biotech|life sciences?|drug develop\w*)\b", re.I
)


def detect_builder(title: str, summary: str, source_type: str = "news") -> dict[str, Any] | None:
    """Return {"segment", "stage", "watch", "strength"} for a builder story, else None."""
    text = f"{title} {summary}"
    watch = _watch_hit(text)
    has_ai = bool(AI.search(text))
    segment = next((label for label, pattern in SEGMENTS if pattern.search(text)), "")
    # Segment words are field-specific; otherwise insist on a health context.
    if not (segment or watch or HEALTH.search(text)):
        return None
    if watch and not segment:
        segment = watch[1]
    stage = _stage(text)

    if watch and (stage or has_ai):
        strength = 2 if stage else 1
    elif has_ai and segment and stage:
        strength = 2
    elif has_ai and stage and LIFESCI.search(text):
        strength = 1
    else:
        return None
    # LinkedIn "launch" chatter is mostly campaigns and events; keep posts that
    # report a round, a deal, or name a tracked company.
    if source_type == "linkedin" and not watch and not stage.startswith(("Funding", "Acquisition")):
        return None
    return {
        "segment": segment or "AI in life sciences",
        "stage": stage or "News",
        "watch": watch[0] if watch else "",
        "strength": strength,
    }
