from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .util import clean_text

# Phrases that make an item relevant to this brief. These are deliberately
# specific: "AI" or "market" on their own are not enough.
CORE_PATTERNS = [
    r"\bheor\b",
    r"\bhealth economics?\b",
    r"\bhealth economist\b",
    r"\boutcomes research\b",
    r"\bpharmacoeconomic",
    r"\breal[- ]world (evidence|data|studies|study)\b",
    r"\brwe\b",
    r"\brwd\b",
    r"\bhealth technology assessment\b",
    r"\bhta\b",
    r"\bjoint clinical assessment\b",
    r"\bjca\b",
    r"\bmarket access\b",
    r"\bpatient access\b",
    r"\breimbursement\b",
    r"\bpayer(s)?\b",
    r"\bformulary\b",
    r"\bdrug pric(e|es|ing)\b",
    r"\bprice negotiation\b",
    r"\bvalue assessment\b",
    r"\bcost[- ]effective",
    r"\bqaly",
    r"\bbudget impact\b",
    r"\bevidence generation\b",
    r"\bevidence synthesis\b",
    r"\bclinical trial(s)?\b",
    r"\bcontract research\b",
    r"\bcro\b",
    r"\bregulatory (decision|submission|approval|pathway)\b",
    r"\b(fda|ema|mhra|cdsco)\b.{0,45}\b(approv|authori[sz]|clear|grant|guidance|designat|review)\w*\b",
    r"\b(accelerated|conditional|marketing|regulatory) approval\b",
    r"\borphan drug designation\b",
    r"\bbreakthrough therapy\b",
    r"\bmedicare\b",
    r"\bmedicaid\b",
    r"\bnppa\b",
    r"\bcdsco\b",
    r"\bhtain\b",
    r"\bprice control\b",
    r"\bdrug price\b",
]

HEALTH_PATTERNS = [
    r"\bhealth",
    r"\bhealthcare\b",
    r"\bpharma",
    r"\bbiotech\b",
    r"\bbiopharma\b",
    r"\bdrug(s)?\b",
    r"\bmedicine(s)?\b",
    r"\bmedical\b",
    r"\bclinical\b",
    r"\bpatient(s)?\b",
    r"\btherapy\b",
    r"\btherapies\b",
    r"\btreatment(s)?\b",
    r"\bdisease(s)?\b",
    r"\bcancer\b",
    r"\btumou?r\b",
    r"\bdiagnos",
    r"\bdiagnostic(s)?\b",
    r"\bdevice(s)?\b",
    r"\bhospital(s)?\b",
    r"\bprescription(s)?\b",
    r"\bmedicare\b",
    r"\bmedicaid\b",
    r"\bnhs\b",
    r"\bcoverage\b",
    r"\bpayer(s)?\b",
    r"\breimbursement\b",
    r"\bregulatory\b",
    r"\bfda\b",
    r"\beuropean medicines agency\b",
    r"\bema\b(?=.{0,80}\b(medicine|drug|regulatory|approval|safety|committee|vaccine|therapy|authorisation|authorization)\b)",
    r"\bnice\b(?=.{0,70}\b(recommend|guidance|appraisal|nhs|drug|medicine|treatment|technology|committee)\b)",
    r"\bcdsco\b",
    r"\bnppa\b",
    r"\boncology\b",
    r"\bvaccine(s)?\b",
    r"\bscreening\b",
]

CATEGORY_PATTERNS: dict[str, list[str]] = {
    "ai": [
        r"\bai\b",
        r"\bartificial intelligence\b",
        r"\bmachine learning\b",
        r"\bdeep learning\b",
        r"\bllm(s)?\b",
        r"\blarge language model",
        r"\bgenerative\b",
        r"\bagentic\b",
        r"\bneural network",
    ],
    "hta": [
        r"\bhealth technology assessment\b",
        r"\bhta\b",
        r"\bjoint clinical assessment\b",
        r"\bjca\b",
        r"\bquality[- ]adjusted life year",
        r"\bqaly",
        r"\bcost[- ]effectiveness\b",
        r"\bbudget impact\b",
        r"\bvalue assessment\b",
        r"\btechnology appraisal\b",
    ],
    "market_access": [
        r"\bmarket access\b",
        r"\bpatient access\b",
        r"\baccess strategy\b",
        r"\breimbursement\b",
        r"\bpayer(s)?\b",
        r"\bformulary\b",
        r"\bcoverage decision",
        r"\bprice negotiation\b",
        r"\bpricing\b",
        r"\blaunch price\b",
        r"\bcontract(s)?\b",
    ],
    "policy": [
        r"\bpolicy\b",
        r"\bgovernment\b",
        r"\bregulat",
        r"\blegislation\b",
        r"\bcms\b",
        r"\bmedicare\b",
        r"\bmedicaid\b",
        r"\bfda\b",
        r"\bema\b",
        r"\bmhra\b",
        r"\bnppa\b",
        r"\bcdsco\b",
        r"\bira\b",
        r"\binflation reduction act\b",
        r"\bmost favored nation\b",
        r"\bmfn\b",
        r"\bpbm\b",
        r"\b340b\b",
        r"\bfederal\b",
        r"\breform\b",
    ],
    "heor": [
        r"\bheor\b",
        r"\bhealth economics?\b",
        r"\boutcomes research\b",
        r"\breal[- ]world (evidence|data|study|studies)\b",
        r"\brwe\b",
        r"\brwd\b",
        r"\bpharmacoeconomic",
        r"\bevidence generation\b",
        r"\bobservational study\b",
        r"\bpatient[- ]reported outcome",
        r"\bhealth utilities?\b",
        r"\bepidemiolog",
    ],
    "cro": [
        r"\bcro\b",
        r"\bcontract research\b",
        r"\bclinical trial(s)?\b",
        r"\bclinical research\b",
        r"\bsite activation\b",
        r"\bpatient recruitment\b",
        r"\bdecentralized trial",
        r"\bstudy start[- ]up\b",
        r"\bclinical operations\b",
    ],
}

TAGS: list[tuple[str, re.Pattern[str]]] = [
    ("ISPOR", re.compile(r"\bispor\b", re.I)),
    ("EU HTA", re.compile(r"\beu[- ]hta\b|\bjoint clinical assessment\b|\bjca\b|\bhta regulation\b", re.I)),
    ("AI", re.compile(r"\bai\b|\bartificial intelligence\b|\bmachine learning\b|\bllm\b|\blarge language model", re.I)),
    ("RWE", re.compile(r"\breal[- ]world (evidence|data)\b|\brwe\b|\brwd\b", re.I)),
    ("HTA", re.compile(r"\bhealth technology assessment\b|\bhta\b", re.I)),
    ("Market access", re.compile(r"\bmarket access\b|\bpatient access\b", re.I)),
    ("Pricing", re.compile(r"\bprice|\bpricing\b|\breimbursement\b|\bnegotiation\b", re.I)),
    ("Policy", re.compile(r"\bpolicy\b|\bregulat|\bgovernment\b|\blegislation\b", re.I)),
    ("CRO", re.compile(r"\bcro\b|\bcontract research\b|\bclinical trial", re.I)),
    ("Oncology", re.compile(r"\boncology\b|\bcancer\b|\btumou?r\b|\bcarcinoma\b", re.I)),
    ("Rare disease", re.compile(r"\brare disease|\borphan\b", re.I)),
    ("India", re.compile(r"\bindia\b|\bcdsco\b|\bnppa\b|\bhtain\b", re.I)),
    ("US", re.compile(r"\bunited states\b|\bu\.s\.\b|\bmedicare\b|\bmedicaid\b|\bcms\b|\bfda\b", re.I)),
    ("UK", re.compile(r"\bunited kingdom\b|\buk\b|\bnice\b|\bnhs\b|\bmhra\b", re.I)),
    ("EU", re.compile(r"\beuropean union\b|\beu\b|\bema\b", re.I)),
    ("QALY", re.compile(r"\bqaly", re.I)),
    ("Patient preference", re.compile(r"\bpatient preference|\bdiscrete choice\b|\bpreference study", re.I)),
    ("Digital health", re.compile(r"\bdigital health\b|\bdigital therapeutic|\bwearable", re.I)),
]

HARD_EXCLUDE = [
    r"\b(we'?re|we are) hiring\b",
    r"\bhiring\b",
    r"\bjob (opening|opportunity|alert|title|description|posting)\b",
    r"\bvacanc",
    r"\bapply now\b",
    r"\bcareers? page\b",
    r"\binternship\b",
    r"\bfellowship position\b",
    r"\((?:f\/m\/x|f\/m\/d|m\/f\/d|m\/w\/d)\)",
    r"\b(join (our|the) team|open position|open role|we(?:'| a)re recruiting|now hiring|apply today|send your (cv|resume))\b",
    r"\b(consultant|manager|director|analyst|specialist|scientist|associate)\b\s*[–—|-]\s*\b(health economics|heor|hta|market access|rwe|real[- ]world)\b",
    r"\b(adjusted eps|rate base|capital plan|revenue grew|revenue (up|down)|quarterly (results|revenue)|earnings (call|report|results)|stock (rises|falls|jumps)|shares (rose|fell|gained|climbed)|transcript)\b",
    r"\bmarket size\b",
    r"\bmarket report\b",
    r"\bcagr\b",
    r"\bforecast (to|through) 20\d\d\b",
    r"\bresearch report\b",
    r"\bdownload (our|the) (free )?(guide|report|whitepaper)\b",
    r"\bwebinar registration\b",
    r"\bregister (now|here|today)\b",
    r"\bbook a demo\b",
    r"\brequest a demo\b",
    r"\bfacebook\b|\binstagram\b",
]

SOFT_PENALTIES: list[tuple[str, float]] = [
    (r"\b(hiring|vacancy|job|career|internship|apply)\b", -5.0),
    (r"\b(webinar|register|registration|join us|sign up)\b", -2.2),
    (r"\b(booth|come meet us|visit us at|meet the team|looking forward to attending|we will be at)\b", -2.8),
    (r"\b(will be there|heading to|see you (at|in)|swing by|stop by|catch us|visit (us|our booth)|meet (us|the team)|come (say|meet)|let'?s connect|connect with (us|me)|get in touch|reach out|book a (meeting|demo)|schedule a (call|meeting)|looking forward to (attending|connecting|seeing))\b", -2.6),
    (r"\b(join (us|our)|save your seat|reserve your spot)\b", -1.6),
    (r"\b(congratulations|celebrating|anniversary|award|honoree|welcome to)\b", -2.0),
    (r"\b(sponsored|advertisement|partner content)\b", -4.0),
    (r"\b(our platform|our solution|our software|clients|demo)\b", -1.3),
    (r"\b(press release|announces? (the )?launch|launches?)\b", -0.4),
    (r"\b(market research|survey finds|poll finds)\b", -1.0),
    (r"\b(closed on|public holiday|holiday notice)\b", -3.0),
    (r"\b(recall|food|toddler|snack|pet food|veterinary|animal)\b", -5.0),
    (r"\b(podcast|webinar series|episode \d+)\b", -1.0),
    (r"\b(our (latest|new) (perspective|report|whitepaper|guide)|read (our|the) (latest|full)|download (our|the))\b", -0.9),
    (r"\b(i'?ve (joined|started)|i am (joining|starting)|starting (a )?new (role|chapter|position)|new (role|chapter|position)|joined the team|joined our team|welcome to (our|the) team|thrilled to (join|announce)|excited to (join|announce)|happy to (share|announce) (that i|I've|i have)|i'?m (thrilled|excited|happy|pleased) to (share|announce|join))\b", -6.0),
    (r"\b(joined ispor|isporeurope|ispor .{0,20} (for me|for you|this year|this time)|my first ispor|first ispor)\b", -2.5),
    (r"\b(arrived at|we'?ve (arrived|landed)|i'?m at|i am at|landed at|this week (at|in)|book time|send (a )?message to|stop by (our|the) (booth|stand)|come (say hi|meet us)|connecting,? collaborating|celebrating women|women in (heor|health)|i'?ll be presenting|i'?m presenting|presenting at (ispor|the)|podium session|poster session)\b", -4.0),
]

HARD_FOOD = re.compile(r"\b(recall|toddler|snack|pet food|veterinary|animal|food)\b", re.I)
HARD_OTHER_INDUSTRY = re.compile(r"\b(agricultur|poultry|real estate|surf|retail|tourism|travel|fashion)\b", re.I)

# Conference notices and booth invitations crowd out actual evidence and policy.
# These are penalised rather than blocked so genuinely analytical preview posts
# can still appear lower in the edition.
EVENT_PROMO = re.compile(
    r"\b("
    r"counting down|save the date|don'?t miss|last chance|"
    r"we(?:'| a)re (?:excited|thrilled|proud|delighted) to (?:share|announce|present|be)|"
    r"(?:excited|thrilled|proud|delighted) to (?:share|announce|present)|"
    r"share \d+ posters?|\d+ posters? at|"
    r"join us (?:at|for|in)|meet (?:us|our team)|"
    r"join (?!us\b|our\b|the\b)[\w'’-]+.{0,30}\b(?:at|for|in)\b|"
    r"meet .{0,40} at\b|"
    r"if you'?re attending|are you attending|who'?s (?:attending|going)|"
    r"will you be (?:at|in)|come (?:say|meet)|swing by|stop by|"
    r"find us at|visit (?:us|our booth)|booth #?\w+|"
    r"see you (?:at|in)|heading to|will be there|"
    r"preview series|starts (?:today|tomorrow)|happening (?:today|tomorrow)"
    r")\b",
    re.I,
)

PRESS_RELEASE = re.compile(
    r"\b("
    r"announces? (?:its |the )?(?:research|presence|participation|data|results)|"
    r"publishes? (?:a )?(?:whitepaper|report)|"
    r"brings? \d+ years?|celebrates? \d+ years?"
    r")\b",
    re.I,
)


def _matches(patterns: list[str], text: str) -> int:
    return sum(1 for pattern in patterns if re.search(pattern, text, re.I))


def _has(pattern: str, text: str) -> bool:
    return bool(re.search(pattern, text, re.I))


def detect_tags(text: str, category: str) -> list[str]:
    tags: list[str] = []
    for label, pattern in TAGS:
        if pattern.search(text):
            if label.lower() in {category.lower(), category.replace("_", " ").lower()}:
                continue
            tags.append(label)
    # Keep specific signals first; cap at four.
    tags = list(dict.fromkeys(tags))
    return tags[:4]


def classify(
    title: str,
    summary: str,
    source_type: str,
    default_category: str,
    published_at: datetime | None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    now = now or datetime.now(timezone.utc)
    title = clean_text(title)
    summary = clean_text(summary)
    text = f"{title} {summary}".strip()
    if len(title) < 18:
        return None
    if not text:
        return None

    # Obvious junk: job ads, market-size reports, unrelated industries.
    for pattern in HARD_EXCLUDE:
        if re.search(pattern, text, re.I):
            return None
    if HARD_OTHER_INDUSTRY.search(text) and not _has(r"\b(drug|medicine|pharma|clinical|patient|health|medical|biotech)\b", text):
        return None
    if HARD_FOOD.search(text) and not _has(r"\b(drug|medicine|pharma|clinical|patient|oncology|therapy|treatment)\b", text):
        return None

    health_hits = _matches(HEALTH_PATTERNS, text)
    core_hits = _matches(CORE_PATTERNS, text)
    if health_hits == 0 and core_hits == 0:
        return None
    # The brief is health/pharma-specific. A single generic mention is not enough.
    if health_hits == 0 and source_type not in {"official", "research"}:
        return None
    if health_hits == 0 and source_type in {"official", "research"} and core_hits == 0:
        return None

    category_scores: dict[str, float] = {key: 0.0 for key in CATEGORY_PATTERNS}
    for key, patterns in CATEGORY_PATTERNS.items():
        hits = _matches(patterns, text)
        category_scores[key] = hits * 1.35
        if key == default_category:
            category_scores[key] += 2.4

    # A specific default source is a strong hint, but content can override it.
    category = max(category_scores, key=lambda k: (category_scores[k], k == default_category))
    if category_scores[category] < 1.0:
        category = default_category

    # AI should be the primary label only when it is central, not incidental.
    if category == "ai" and category_scores.get("hta", 0) > category_scores["ai"]:
        category = "hta"
    if category == "ai" and category_scores.get("heor", 0) > category_scores["ai"]:
        category = "heor"

    score = 1.8
    score += min(3.4, core_hits * 0.95)
    score += min(1.8, health_hits * 0.3)
    score += min(2.0, category_scores[category] * 0.35)
    if source_type == "official":
        score += 2.0
    elif source_type == "research":
        score += 1.4
    elif source_type == "linkedin":
        score += 0.6
    else:
        score += 0.3
    if len(title) > 140:
        score += 0.5
    if len(summary) > 120:
        score += 0.45
    if re.search(r"\bjoint clinical assessment\b|\beu[- ]hta\b", text, re.I):
        score += 1.0
    elif re.search(r"\bispor\b", text, re.I):
        # ISPOR is a useful signal, but conference chatter should not outrank
        # an actual reimbursement decision or policy change.
        score += 0.45
    if EVENT_PROMO.search(text):
        score -= 3.0
        if source_type == "linkedin":
            score -= 1.0
    if PRESS_RELEASE.search(text):
        score -= 1.2
    if published_at:
        age_hours = max(0.0, (now - published_at).total_seconds() / 3600)
        if age_hours <= 24:
            score += 2.0
        elif age_hours <= 48:
            score += 1.2
        elif age_hours <= 96:
            score += 0.4
        elif age_hours > 24 * 21:
            score -= 1.2
    for pattern, penalty in SOFT_PENALTIES:
        if re.search(pattern, text, re.I):
            score += penalty

    tags = detect_tags(text, category)
    return {
        "category": category,
        "tags": tags,
        "score": round(max(0.0, score), 2),
        "core_hits": core_hits,
        "health_hits": health_hits,
    }
