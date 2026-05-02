#!/usr/bin/env python3
"""
Competitive Intelligence Scraper — Schneider Electric
Tracks Siemens, ABB, Rockwell Automation, Honeywell, Emerson, Yokogawa

Source hierarchy:
  1. Google News RSS  — primary, broad, high quality
  2. Industry publications RSS — Control Engineering, Industrial Cyber,
     SecurityWeek, Automation World, ISSSource, The Manufacturer
  3. PR Newswire / Business Wire company feeds
  4. SEC EDGAR RSS   — earnings, 8-K filings (public companies)
  5. Company direct RSS — fallback

Quality gates (applied in order):
  1. Publisher blocklist  — drops known junk/stock-blog outlets
  2. Alias filter         — article must mention competitor by name or product
  3. Strategic signal     — LOW-priority items must carry at least one
                            actionable business term beyond the company name
  4. Story dedup          — Jaccard + named-entity dedup across 14-day window

Output: data.json consumed by index.html dashboard.

Install: pip install requests beautifulsoup4 feedparser lxml
"""

import json
import time
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus

try:
    import requests
    from bs4 import BeautifulSoup
    import feedparser
except ImportError:
    print("Missing deps. Run: pip install requests beautifulsoup4 feedparser lxml")
    raise

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DELAY              = 1.2   # polite delay between requests (seconds)
MAX_PER_SOURCE     = 10    # max items scanned per individual source feed
MAX_PER_COMPETITOR = 8     # max items in final output per competitor
MAX_AGE_DAYS       = 30    # ignore items older than this

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ─────────────────────────────────────────────────────────────────────────────
# Source quality gates
# ─────────────────────────────────────────────────────────────────────────────

# Publishers that are blocklisted: stock blogs, PR farms, niche trade press
# irrelevant to Schneider Electric's competitive context.
BLOCKED_PUBLISHERS = {
    "prittle prattle", "sahm", "stock titan", "fastener", "fastener + fixing",
    "azorobotics", "azocleantech", "azomining", "azooptics", "azosensors",
    "proactiveinvestors", "seeking alpha", "motley fool", "thestreet",
    "stockanalysis", "simply wall st", "benzinga", "zacks", "tipranks",
    "marketbeat", "gurufocus", "wisesheets", "finviz",
    "just auto", "just-auto", "automotivetoday",
    "kron4", "kron", "kqed",   # local TV/radio — no industrial relevance
    "ad hoc news",             # low-quality financial wire
    "yahoo finance",           # aggregator — catches stock/market noise
}

# Regex patterns checked against lowercased headline (with competitor tokens removed).
# Any match = item dropped regardless of source or priority.
BLOCKED_HEADLINE_RE = [
    re.compile(p) for p in [
        r"maxxing",                         # meme/slang use of company ticker
        r"market\s+outlook\s+20\d\d",       # market research previews
        r"market\s+size\s+20\d\d",
        r"market\s+forecast\s+20\d\d",
        r"steady\s+pick",                   # stock-pick narrative
        r"\bstock:\s",                      # "Company Stock: ..."
        r"stock\s+price\s+target",
        r"share\s+price",
        r"price\s+target\s+(raised|lowered|cut)",
        r"earnings\s+set\s+for\s+release",  # pre-announcement placeholders
        r"q[1-4]\s+fy\s+20\d\d\s+earnings", # quarterly financial summaries
        r"why\s+.{5,50}\s+makes\s+it\s+a",  # "why X makes it a good buy"
    ]
]


def is_headline_blocked(headline: str, competitor_aliases: list) -> bool:
    """Return True if headline matches a known-junk pattern."""
    text = headline.lower()
    for alias in competitor_aliases:
        text = text.replace(alias.lower(), " ")
    return any(pat.search(text) for pat in BLOCKED_HEADLINE_RE)

# Premium publishers — items from these pass without needing a strategic signal check.
PREMIUM_PUBLISHERS = {
    "industrial cyber", "securityweek", "darkreading", "threatpost",
    "control engineering", "automation world", "industry week", "industryweek",
    "isssource", "the manufacturer", "plant engineering", "processing magazine",
    "chemical engineering", "oil & gas journal", "power magazine",
    "manufacturing tomorrow", "smart industry", "design news",
    "reuters", "bloomberg", "financial times", "wall street journal",
    "pr newswire", "business wire", "globe newswire", "accesswire",
    "sec edgar", "sec.gov",
    "securitybrief", "helpnetsecurity", "ot-isac",
}

# At least one of these must appear in the headline/summary (after stripping
# competitor name) for LOW-priority items from non-premium sources.
STRATEGIC_SIGNAL_TERMS = {
    # Business events
    "launch", "launches", "launched", "release", "releases", "released",
    "expand", "expands", "expanded", "expansion",
    "partnership", "partner", "partners", "partnered", "collaborate",
    "acqui", "merger", "divest", "spin-off", "deal",
    "contract", "deploy", "deployment", "win", "wins", "won",
    "appoint", "appoints", "names", "hire", "ceo", "cto", "ciso",
    "funding", "ipo", "billion", "million", "revenue", "earnings",
    "certification", "certified", "standard", "compliance",
    "award", "recognized", "named",
    # Security / OT
    "vulnerability", "breach", "attack", "exploit", "cve", "zero-day",
    "cybersecurity", "cyber", "ransomware", "malware", "incident",
    "ot security", "ics security", "scada security", "critical infrastructure",
    # Technology domains
    "industrial", "automation", "manufacturing", "iiot", "iot",
    "scada", "plc", "dcs", "hmi",
    "ot", "operational technology", "ics",
    "digital twin", "edge", "cloud", "saas", "platform",
    "ai", "machine learning", "artificial intelligence", "generative ai",
    "robotics", "cobot", "robot",
    "grid", "power", "electrification", "energy management",
    "building automation", "hvac", "bms",
    "process control", "process automation", "smart manufacturing",
    "smart factory", "connected worker", "augmented reality",
}

# ─────────────────────────────────────────────────────────────────────────────
# Competitor-specific "Why It Matters" defaults
# Used when no keyword match fires — replaces the useless generic fallback.
# ─────────────────────────────────────────────────────────────────────────────

COMPETITOR_DEFAULT_WHY = {
    "Siemens": (
        "Siemens Xcelerator directly competes with EcoStruxure. "
        "Assess how this development shifts their positioning in industrial automation, "
        "OT security, and energy management — and whether it exposes or closes gaps "
        "Schneider can exploit in customer conversations."
    ),
    "ABB": (
        "ABB competes with Schneider across electrification, motion control, "
        "and industrial automation. Evaluate impact on Schneider's power and "
        "process automation customer base, and check for overlap with ABB Ability."
    ),
    "Rockwell Automation": (
        "Rockwell's FactoryTalk competes directly with EcoStruxure in manufacturing "
        "execution and IIoT. Assess customer overlap and displacement risk, "
        "particularly in discrete manufacturing accounts in North America."
    ),
    "Honeywell": (
        "Honeywell Forge and Experion compete with Schneider in process automation, "
        "connected buildings, and OT security. Review for account risk in energy, "
        "infrastructure, and commercial real estate verticals."
    ),
    "Emerson": (
        "Emerson DeltaV competes with Schneider in process automation and "
        "OT cybersecurity, especially in utilities and critical infrastructure. "
        "Assess capability overlap and partnership ecosystem implications."
    ),
    "Yokogawa": (
        "Yokogawa CENTUM competes with Schneider in process control and industrial IoT, "
        "particularly in energy, chemicals, and APAC markets. "
        "Review for positioning gaps and account intelligence in key verticals."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# Competitor profiles
# ─────────────────────────────────────────────────────────────────────────────

COMPETITORS = [
    {
        "name": "Siemens",
        "aliases": [
            "Siemens", "Siemens AG", "Siemens Energy", "Siemens Digital",
            "Siemens Xcelerator", "SINEC", "Simatic",
        ],
        "google_news_queries": [
            "Siemens industrial automation news 2026",
            "Siemens Xcelerator digital platform launch",
            "Siemens OT cybersecurity SINEC industrial",
            "Siemens Energy acquisition partnership deal",
            "Siemens AI smart manufacturing edge",
        ],
        "prnewswire_id": "siemens",
        "businesswire_slug": "siemens",
        "sec_cik": None,
        "direct_rss": [
            "https://press.siemens.com/global/en/pressreleases/rss.xml",
            "https://new.siemens.com/global/en/company/press/press-releases.rss",
        ],
        "direct_html": "https://press.siemens.com/global/en/pressreleases",
    },
    {
        "name": "ABB",
        "aliases": [
            "ABB", "ABB Ltd", "ABB Group", "ABB Ability",
            "ABB Motion", "ABB Electrification", "800xA",
        ],
        "google_news_queries": [
            "ABB industrial automation news 2026",
            "ABB electrification grid technology launch",
            "ABB Ability digital platform update partnership",
            "ABB acquisition deal announcement",
            "ABB process automation OT cybersecurity",
        ],
        "prnewswire_id": "abb",
        "businesswire_slug": "abb",
        "sec_cik": "0001091818",
        "direct_rss": [
            "https://new.abb.com/news/rss",
            "https://media.abb.com/api/rss",
        ],
        "direct_html": "https://new.abb.com/news",
    },
    {
        "name": "Rockwell Automation",
        "aliases": [
            "Rockwell Automation", "Rockwell", "FactoryTalk", "Allen-Bradley",
            "Plex Systems", "Plex Technologies",
        ],
        "google_news_queries": [
            "Rockwell Automation news 2026",
            "Rockwell Automation FactoryTalk launch partnership",
            "Rockwell Automation smart manufacturing OT cybersecurity",
            "Rockwell Automation acquisition deal contract",
            "Rockwell Automation AI industrial edge",
        ],
        "prnewswire_id": "rockwell-automation",
        "businesswire_slug": "rockwellautomation",
        "sec_cik": "0001024478",
        "direct_rss": [],  # rockwellautomation.com/en-us/about/news.rss.xml returns 404
        "direct_html": "https://www.rockwellautomation.com/en-us/about/news.html",
    },
    {
        "name": "Honeywell",
        "aliases": [
            "Honeywell", "Honeywell International", "Honeywell Forge",
            "Honeywell Connected", "Experion", "Honeywell Process Solutions",
        ],
        "google_news_queries": [
            "Honeywell industrial automation news 2026",
            "Honeywell Forge Experion OT cybersecurity launch",
            "Honeywell connected buildings AI industrial",
            "Honeywell acquisition deal partnership",
            "Honeywell process automation energy management",
        ],
        "prnewswire_id": "honeywell",
        "businesswire_slug": "honeywell",
        "sec_cik": "0000773840",
        "direct_rss": [
            "https://www.honeywell.com/us/en/press/rss.xml",
        ],
        "direct_html": "https://www.honeywell.com/us/en/press",
    },
    {
        "name": "Emerson",
        "aliases": [
            "Emerson", "Emerson Electric", "DeltaV", "Emerson Automation",
            "Ovation", "Emerson Process Management",
        ],
        "google_news_queries": [
            "Emerson Electric automation news 2026",
            "Emerson DeltaV OT cybersecurity launch partnership",
            "Emerson process automation industrial AI",
            "Emerson acquisition deal contract",
            "Emerson Ovation energy utilities deployment",
        ],
        "prnewswire_id": "emerson-electric",
        "businesswire_slug": "emersonelectric",
        "sec_cik": "0000032604",
        "direct_rss": [
            "https://www.emerson.com/en-us/news/rss",
            "https://www.emerson.com/feeds/news",
        ],
        "direct_html": "https://www.emerson.com/en-us/news",
    },
    {
        "name": "Yokogawa",
        "aliases": [
            "Yokogawa", "Yokogawa Electric", "CENTUM", "ProSafe",
            "Yokogawa OpreX",
        ],
        "google_news_queries": [
            "Yokogawa news 2026",
            "Yokogawa Electric news",
            "Yokogawa automation industrial",
            "Yokogawa CENTUM OpreX launch partnership",
            "Yokogawa process control digital transformation",
        ],
        "prnewswire_id": "yokogawa",
        "businesswire_slug": "yokogawa",
        "sec_cik": None,
        "direct_rss": [],  # yokogawa.com/news/rss/ returns 404
        "direct_html": "https://www.yokogawa.com/news",
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Industry publication RSS feeds  (fetched once, filtered per competitor)
# ─────────────────────────────────────────────────────────────────────────────

INDUSTRY_FEEDS = [
    {"name": "Industrial Cyber",    "url": "https://industrialcyber.co/feed/",                 "weight": "HIGH"},
    {"name": "SecurityWeek",        "url": "https://feeds.feedburner.com/Securityweek",         "weight": "MEDIUM"},
    {"name": "Control Engineering", "url": "https://www.controleng.com/feed/",                  "weight": "MEDIUM"},
    {"name": "Automation World",    "url": "https://www.automationworld.com/home/rss.xml",       "weight": "MEDIUM"},
    {"name": "ISSSource",           "url": "https://www.isssource.com/feed/",                   "weight": "MEDIUM"},
    {"name": "The Manufacturer",    "url": "https://www.themanufacturer.com/feed/",              "weight": "LOW"},
    {"name": "IndustryWeek",        "url": "https://www.industryweek.com/rss/all",               "weight": "LOW"},
    {"name": "Plant Engineering",   "url": "https://www.plantengineering.com/feed/",             "weight": "LOW"},
]

# Fallback Google News queries for thin-coverage competitors (< 3 items after primary scrape)
FALLBACK_QUERIES = {
    "Siemens":             ["Siemens news 2026", "Siemens Simatic Xcelerator"],
    "ABB":                 ["ABB news 2026", "ABB robotics power grids"],
    "Rockwell Automation": ["Rockwell Automation news", "FactoryTalk launch", "Allen-Bradley automation 2026"],
    "Honeywell":           ["Honeywell news industrial 2026"],
    "Emerson":             ["Emerson Electric news announcement 2026"],
    "Yokogawa":            [
        "Yokogawa news",
        "Yokogawa Electric announcement",
        "Yokogawa OpreX industrial",
        "Yokogawa CENTUM automation",
        "Yokogawa process industry news",
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Priority classification
# ─────────────────────────────────────────────────────────────────────────────

HIGH_KEYWORDS = [
    "launch", "launches", "launched", "release", "releases", "released",
    "partnership", "partner", "partners", "partnered",
    "acquisition", "acquires", "acquired", "acquire", "merger",
    "contract", "win", "wins", "won", "deploy", "deploys", "deployed", "deployment",
    "vulnerability", "breach", "attack", "exploit", "cve", "zero-day",
    "ipo", "funding", "investment", "deal", "billion", "million",
    "expands", "expansion", "enters", "new market", "appoints", "ceo",
]

MEDIUM_KEYWORDS = [
    "report", "survey", "study", "research", "whitepaper", "white paper",
    "webinar", "event", "conference", "summit", "award", "recognized",
    "blog", "insight", "perspective", "thought leadership",
    "update", "upgrade", "version", "feature",
    "integration", "solution", "platform", "certification", "certified",
]

WHY_MATTERS = {
    "launch":        "New product or feature launch — assess capability overlap with Schneider's EcoStruxure stack and update competitive positioning.",
    "launches":      "New product or feature launch — assess capability overlap with Schneider's EcoStruxure stack and update competitive positioning.",
    "release":       "New release may shift feature parity — review against Schneider's current roadmap and update sales battlecards.",
    "partnership":   "Ecosystem partnership expands competitor's reach — evaluate overlap with Schneider's partner network and identify at-risk customer accounts.",
    "partner":       "Ecosystem partnership expands competitor's reach — evaluate overlap with Schneider's partner network and identify at-risk customer accounts.",
    "acquisition":   "Acquisition signals a capability gap being filled — identify what they bought and whether Schneider has equivalent or superior depth.",
    "acquires":      "Acquisition signals a capability gap being filled — identify what they bought and whether Schneider has equivalent or superior depth.",
    "contract":      "Contract win in a key vertical — confirm whether this is a Schneider account, adjacent account, or new territory being entered.",
    "win":           "Competitive win — identify vertical and geography. Assess displacement risk for Schneider's installed base in the region.",
    "wins":          "Competitive win — identify vertical and geography. Assess displacement risk for Schneider's installed base in the region.",
    "deployment":    "Live deployment in the field — use as a reference case signal and check for overlap with Schneider's installed base.",
    "vulnerability": "Security vulnerability disclosed — may prompt customers to evaluate their automation vendor mix. Prepare Schneider's security differentiation response.",
    "breach":        "Breach or incident linked to competitor's platform — assess reputational impact and customer confidence signals. Prepare account team talking points.",
    "cve":           "CVE disclosed in competitor's product — monitor for customer concern. Prepare Schneider's security positioning counter-narrative.",
    "funding":       "New funding round — competitor has capital to accelerate R&D and market expansion. Reassess competitive intensity in target segments.",
    "deal":          "Major commercial deal — assess strategic direction and market segment implications for Schneider.",
    "billion":       "Major financial event — assess strategic direction and investment signal for R&D or market expansion.",
    "million":       "Significant financial event — assess investment signal and competitive intensity implications.",
    "appoints":      "Leadership change — new executives often signal strategic pivots. Monitor messaging changes over the next 90 days.",
    "ceo":           "CEO-level announcement — assess strategic direction signals and potential pivot in competitive approach.",
    "expands":       "Market or capability expansion — assess whether this moves competitor into Schneider's core accounts or geographies.",
    "expansion":     "Market or capability expansion — assess whether this moves competitor into Schneider's core accounts or geographies.",
    "report":        "Competitor thought leadership shaping buyer criteria — review for messaging gaps in Schneider's content strategy.",
    "webinar":       "Competitor investing in demand generation targeting the same buyer profiles — match or counter with Schneider content.",
    "whitepaper":    "Competitor thought leadership on a key topic — review for messaging gaps against Schneider's content strategy.",
    "award":         "Industry recognition strengthens competitor's enterprise sales credibility — relevant to Schneider's positioning in the same category.",
    "certification": "Certification or standard achievement — signals product maturity and compliance readiness. Assess whether Schneider holds equivalent certifications.",
    "certified":     "Certification or standard achievement — signals product maturity and compliance readiness. Assess whether Schneider holds equivalent certifications.",
}

# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def classify(text: str) -> tuple[str, str]:
    """Return (priority, matched_keyword)."""
    lower = text.lower()
    for kw in HIGH_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', lower):
            return "HIGH", kw
    for kw in MEDIUM_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', lower):
            return "MEDIUM", kw
    return "LOW", ""


def why_matters(keyword: str, competitor: str = "") -> str:
    """Return specific insight for keyword, or competitor-specific default."""
    if keyword and keyword in WHY_MATTERS:
        return WHY_MATTERS[keyword]
    return COMPETITOR_DEFAULT_WHY.get(
        competitor,
        "Monitor for competitive positioning impact on Schneider's accounts and messaging."
    )


def clean_headline(raw: str) -> tuple[str, str]:
    """
    Strip publisher suffix from Google News-style titles.
    'Headline text - Publisher Name' => ('Headline text', 'Publisher Name')
    Returns (clean_headline, publisher_name).
    """
    for sep in [" - ", " | ", " – ", " — "]:
        idx = raw.rfind(sep)
        if idx == -1:
            continue
        potential_pub = raw[idx + len(sep):].strip()
        # Publisher names are short (< 70 chars) and don't end with sentence punctuation
        if 2 < len(potential_pub) < 70 and not potential_pub.endswith((".", "!", "?", ",")):
            return raw[:idx].strip(), potential_pub
    return raw.strip(), ""


def is_publisher_blocked(publisher: str) -> bool:
    pub_lower = publisher.lower()
    return any(blocked in pub_lower for blocked in BLOCKED_PUBLISHERS)


def is_publisher_premium(publisher: str) -> bool:
    pub_lower = publisher.lower()
    return any(prem in pub_lower for prem in PREMIUM_PUBLISHERS)


def has_strategic_signal(headline: str, summary: str, competitor_aliases: list) -> bool:
    """
    After stripping competitor name tokens, check that the remaining text
    contains at least one strategic signal term.
    Prevents customer success stories (e.g. ice cream deployments) from
    being classified as competitive intelligence.
    """
    combined = (headline + " " + summary).lower()
    # Strip competitor alias tokens so company name alone doesn't count
    for alias in competitor_aliases:
        combined = combined.replace(alias.lower(), " ")
    return any(term in combined for term in STRATEGIC_SIGNAL_TERMS)


def resolve_url(url: str) -> str:
    """
    Best-effort resolution of Google News redirect URLs to actual article URLs.
    Uses GET with stream=True and short timeout — fails fast, safe to skip.
    """
    if "news.google.com" not in url:
        return url
    try:
        r = requests.get(
            url, headers=HEADERS, allow_redirects=True,
            timeout=6, stream=True
        )
        r.close()
        final = r.url
        if "news.google.com" not in final and final.startswith("http"):
            log.debug(f"    URL resolved: {final[:80]}")
            return final
    except Exception:
        pass
    return url


def parse_date(raw: str) -> str:
    if not raw:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    formats = [
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
        "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT",
        "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d",
        "%d %B %Y", "%d %b %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            continue
    try:
        t = time.strptime(raw, "%a, %d %b %Y %H:%M:%S %Z")
        return datetime(*t[:3]).strftime("%Y-%m-%d")
    except Exception:
        pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def parse_feedparser_date(entry) -> str:
    for field in ["published_parsed", "updated_parsed", "created_parsed"]:
        val = getattr(entry, field, None)
        if val:
            try:
                return datetime(*val[:3]).strftime("%Y-%m-%d")
            except Exception:
                pass
    for field in ["published", "updated", "created"]:
        val = getattr(entry, field, None)
        if val:
            return parse_date(val)
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def is_recent(date_str: str) -> bool:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return (datetime.now() - d).days <= MAX_AGE_DAYS
    except Exception:
        return True


def mentions_competitor(text: str, competitor: dict) -> bool:
    text_lower = text.lower()
    return any(alias.lower() in text_lower for alias in competitor["aliases"])


def make_item(competitor: str, headline: str, date: str, url: str,
              summary: str, source: str = "", publisher: str = "") -> dict:
    priority, kw = classify(headline + " " + summary)
    return {
        "competitor":  competitor,
        "headline":    headline.strip(),
        "date":        date,
        "url":         url,
        "priority":    priority,
        "summary":     summary.strip()[:200],
        "why_matters": why_matters(kw, competitor),
        "source":      source,
        "publisher":   publisher,
    }


def fetch_rss(url: str, label: str) -> list:
    try:
        log.info(f"  RSS  {label} => {url}")
        feed = feedparser.parse(url, request_headers=HEADERS)
        entries = feed.get("entries", [])
        log.info(f"       {len(entries)} entries")
        time.sleep(DELAY)
        return entries
    except Exception as e:
        log.warning(f"  RSS  {label} failed: {e}")
        return []


def fetch_html(url: str, label: str):
    try:
        log.info(f"  HTML {label} => {url}")
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        r.raise_for_status()
        time.sleep(DELAY)
        return BeautifulSoup(r.text, "lxml")
    except Exception as e:
        log.warning(f"  HTML {label} failed: {e}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
# Source 1: Google News RSS  (primary)
# ─────────────────────────────────────────────────────────────────────────────

def scrape_google_news(competitor: dict, extra_queries: list = None) -> list[dict]:
    """Pull Google News RSS for each configured query. Applies all quality gates."""
    items = []
    seen_urls = set()
    queries = list(competitor["google_news_queries"]) + (extra_queries or [])

    for query in queries:
        encoded = quote_plus(query)
        url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
        entries = fetch_rss(url, f"Google News / {competitor['name']}")

        for e in entries[:MAX_PER_SOURCE]:
            link = getattr(e, "link", "") or ""
            if link in seen_urls:
                continue
            seen_urls.add(link)

            raw_title = getattr(e, "title", "").strip()
            summary   = re.sub(r"<[^>]+>", "", getattr(e, "summary", raw_title)).strip()
            date      = parse_feedparser_date(e)

            if not raw_title or not is_recent(date):
                continue

            # Clean headline — strip publisher suffix
            headline, publisher = clean_headline(raw_title)
            if not headline:
                headline = raw_title

            # Gate 1: blocked publisher
            if is_publisher_blocked(publisher):
                log.debug(f"    BLOCKED publisher [{publisher}]: {headline[:60]}")
                continue

            # Gate 2: competitor alias must appear
            if not mentions_competitor(headline + " " + summary, competitor):
                continue

            # Gate 3: headline pattern blocklist (junk patterns regardless of source)
            if is_headline_blocked(headline, competitor["aliases"]):
                log.debug(f"    BLOCKED headline pattern [{competitor['name']}]: {headline[:60]}")
                continue

            # Gate 4: strategic signal check for LOW items from non-premium sources
            priority, _ = classify(headline + " " + summary)
            if priority == "LOW" and not is_publisher_premium(publisher):
                if not has_strategic_signal(headline, summary, competitor["aliases"]):
                    log.debug(f"    NO SIGNAL [{competitor['name']}]: {headline[:60]}")
                    continue

            # Resolve Google News redirect URL
            resolved_url = resolve_url(link)

            items.append(make_item(
                competitor["name"], headline, date, resolved_url,
                summary[:200], "Google News", publisher
            ))

    log.info(f"  [Google News] {competitor['name']}: {len(items)} items after filtering")
    return items

# ─────────────────────────────────────────────────────────────────────────────
# Source 2: PR Newswire
# ─────────────────────────────────────────────────────────────────────────────

def scrape_prnewswire(competitor: dict) -> list[dict]:
    # PR Newswire ?company=slug parameter is non-functional — the URL returns
    # the full global feed regardless of slug value. Disabled to avoid wasting
    # timeout budget and introducing unfiltered noise. Remove when PRN fixes
    # company-specific filtering or a direct authenticated API is available.
    return []

# ─────────────────────────────────────────────────────────────────────────────
# Source 3: Business Wire
# ─────────────────────────────────────────────────────────────────────────────

def scrape_businesswire(competitor: dict) -> list[dict]:
    slug = competitor.get("businesswire_slug", "")
    if not slug:
        return []

    url     = f"https://www.businesswire.com/rss/home/?rss=G7&company={slug}"
    items   = []
    entries = fetch_rss(url, f"Business Wire / {competitor['name']}")

    for e in entries[:MAX_PER_SOURCE * 3]:
        raw_title = getattr(e, "title", "").strip()
        link      = getattr(e, "link", "") or ""
        summary   = re.sub(r"<[^>]+>", "", getattr(e, "summary", raw_title))[:200]
        date      = parse_feedparser_date(e)

        if not raw_title or not is_recent(date):
            continue

        headline, publisher = clean_headline(raw_title)
        if not headline:
            headline = raw_title

        if not mentions_competitor(headline + " " + summary, competitor):
            continue

        items.append(make_item(
            competitor["name"], headline, date, link, summary,
            "Business Wire", publisher or "Business Wire"
        ))

    return items

# ─────────────────────────────────────────────────────────────────────────────
# Source 4: SEC EDGAR  (US-listed companies only)
# Uses data.sec.gov JSON API — the old atom feed (cgi-bin/browse-edgar?output=atom)
# returns HTTP 403. The JSON submissions endpoint is stable and confirmed working.
# SEC requires a descriptive User-Agent identifying the requester.
# ─────────────────────────────────────────────────────────────────────────────

SEC_HEADERS = {
    "User-Agent": "nipunmittal@icloud.com CI Tool",
    "Accept": "application/json",
}

def scrape_sec_edgar(competitor: dict) -> list[dict]:
    cik = competitor.get("sec_cik")
    if not cik:
        return []

    # Pad CIK to 10 digits as required by the submissions endpoint
    cik_padded = cik.lstrip("0").zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"

    items = []
    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=10)
        if resp.status_code != 200:
            log.warning(f"  [SEC EDGAR] {competitor['name']}: HTTP {resp.status_code}")
            return []
        data = resp.json()
    except Exception as exc:
        log.warning(f"  [SEC EDGAR] {competitor['name']}: {exc}")
        return []

    # filings.recent is a column-oriented dict with parallel arrays
    recent = data.get("filings", {}).get("recent", {})
    forms      = recent.get("form", [])
    dates      = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    descriptions = recent.get("primaryDocDescription", [])

    for i, form in enumerate(forms):
        if form != "8-K":
            continue

        date = dates[i] if i < len(dates) else ""
        if not date or not is_recent(date):
            continue

        acc = accessions[i] if i < len(accessions) else ""
        desc = descriptions[i] if i < len(descriptions) else "8-K Filing"
        acc_clean = acc.replace("-", "")
        link = (
            f"https://www.sec.gov/cgi-bin/browse-edgar"
            f"?action=getcompany&CIK={cik_padded}"
            f"&type=8-K&dateb=&owner=include&count=10"
        ) if not acc else (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{cik_padded.lstrip('0')}/{acc_clean}/{acc}-index.htm"
        )

        desc_label = desc or "8-K Filing"
        items.append({
            "competitor":  competitor["name"],
            "headline":    f"{competitor['name']} SEC 8-K: {desc_label} ({date})",
            "date":        date,
            "url":         link,
            "priority":    "MEDIUM",
            "summary":     (
                f"SEC 8-K regulatory filing by {competitor['name']} dated {date}. "
                f"Filing type: {desc_label}."
            ),
            "why_matters": (
                "Regulatory filing may contain material business events — "
                "review for M&A, restructuring, major contract, or guidance changes "
                "that signal strategic direction shifts impacting Schneider."
            ),
            "source":      "SEC EDGAR",
            "publisher":   "SEC EDGAR",
        })

        if len(items) >= 3:  # cap at 3 SEC filings per competitor
            break

    log.info(f"  [SEC EDGAR] {competitor['name']}: {len(items)} filings")
    return items

# ─────────────────────────────────────────────────────────────────────────────
# Source 5: Company direct RSS feeds
# ─────────────────────────────────────────────────────────────────────────────

def scrape_direct_rss(competitor: dict) -> list[dict]:
    items = []
    seen  = set()

    for url in competitor.get("direct_rss", []):
        entries = fetch_rss(url, f"Direct RSS / {competitor['name']}")
        for e in entries[:MAX_PER_SOURCE]:
            raw_title = getattr(e, "title", "").strip()
            link      = getattr(e, "link", "") or ""

            if not raw_title or link in seen:
                continue
            seen.add(link)

            headline, publisher = clean_headline(raw_title)
            if not headline:
                headline = raw_title

            summary = re.sub(r"<[^>]+>", "", getattr(e, "summary", raw_title))[:200]
            date    = parse_feedparser_date(e)

            if not is_recent(date):
                continue

            items.append(make_item(
                competitor["name"], headline, date, link, summary,
                f"Direct / {competitor['name']}", publisher or competitor["name"]
            ))

        if items:
            break  # stop at first successful feed

    return items

# ─────────────────────────────────────────────────────────────────────────────
# Source 6: Industry publications  (fetched once, filtered per competitor)
# ─────────────────────────────────────────────────────────────────────────────

def scrape_industry_feeds(all_competitors: list[dict]) -> dict[str, list[dict]]:
    """
    Fetch each industry publication once. Detect all competitor mentions per item.
    Uses first-word short aliases for broader matching (e.g. 'ABB', 'Siemens').
    Returns {competitor_name: [items]}.
    """
    # Build short-alias lookup: alias -> competitor_name
    # Short aliases: first word only, or the full alias if it's one word
    alias_map: dict[str, str] = {}
    for c in all_competitors:
        for alias in c["aliases"]:
            alias_lower = alias.lower()
            alias_map[alias_lower] = c["name"]
            # Also add first word if multi-word alias
            first_word = alias_lower.split()[0]
            if len(first_word) > 2:  # skip very short tokens
                alias_map.setdefault(first_word, c["name"])

    results: dict[str, list[dict]] = {c["name"]: [] for c in all_competitors}

    for feed_cfg in INDUSTRY_FEEDS:
        entries = fetch_rss(feed_cfg["url"], f"Industry / {feed_cfg['name']}")

        # Scan all entries (no per-feed cap — capped per competitor below)
        for e in entries:
            raw_title = getattr(e, "title", "").strip()
            link      = getattr(e, "link", "") or ""
            summary   = re.sub(r"<[^>]+>", "", getattr(e, "summary", raw_title))[:300]
            date      = parse_feedparser_date(e)

            if not raw_title or not is_recent(date):
                continue

            headline, publisher = clean_headline(raw_title)
            if not headline:
                headline = raw_title

            text_lower = (headline + " " + summary).lower()

            # Detect which competitors appear in this article
            matched = set()
            for alias_lower, comp_name in alias_map.items():
                if alias_lower in text_lower:
                    matched.add(comp_name)

            for comp_name in matched:
                if len(results[comp_name]) >= MAX_PER_SOURCE:
                    continue
                results[comp_name].append(make_item(
                    comp_name, headline, date, link, summary,
                    feed_cfg["name"], publisher or feed_cfg["name"]
                ))

    for comp_name, items in results.items():
        log.info(f"  [Industry feeds] {comp_name}: {len(items)} items")

    return results

# ─────────────────────────────────────────────────────────────────────────────
# Deduplication
# ─────────────────────────────────────────────────────────────────────────────

def _story_fingerprint(headline: str) -> frozenset:
    stop = {"the", "a", "an", "and", "or", "for", "to", "of", "in", "on",
            "at", "by", "as", "is", "are", "its", "with", "new", "how",
            "from", "that", "this", "has", "have", "will", "be", "it"}
    words = re.sub(r"\W+", " ", headline.lower()).split()
    return frozenset(w for w in words if len(w) > 3 and w not in stop)


def _named_entities(headline: str) -> frozenset:
    """
    Extract capitalised tokens from position 2+ in headline.
    Catches proper nouns: partner names, product names, technology names.
    """
    skip = {"The", "For", "And", "With", "From", "New", "How", "Its",
            "This", "That", "Will", "Into", "Over", "When", "What", "Why",
            "Has", "Are", "Not", "But", "Key", "Top", "All", "Now", "Its"}
    tokens = headline.split()
    entities = set()
    for tok in tokens[1:]:
        clean = re.sub(r"\W", "", tok)
        if len(clean) > 3 and clean[0].isupper() and clean not in skip:
            entities.add(clean)
    return frozenset(entities)


def deduplicate(items: list[dict]) -> list[dict]:
    """
    Remove duplicate items using three layers:
    1. Exact URL match (stripped of query params)
    2. Headline prefix match (first 60 chars)
    3. Story-level: same competitor + within 14 days + either:
       a. Jaccard word similarity >= 0.25
       b. Shared named entity (partner/product name)
    """
    seen_urls   = set()
    seen_heads  = set()
    accepted_stories: list[tuple] = []  # (competitor, date, fingerprint, named_entities)
    unique = []

    for item in items:
        url      = item["url"].split("?")[0].rstrip("/")
        head_key = re.sub(r"\W+", " ", item["headline"].lower()).strip()[:60]

        if url and url in seen_urls:
            continue
        if head_key and head_key in seen_heads:
            continue

        fp = _story_fingerprint(item["headline"])
        ne = _named_entities(item["headline"])
        is_dupe = False

        try:
            item_date = datetime.strptime(item["date"], "%Y-%m-%d")
        except Exception:
            item_date = datetime.now()

        for (comp, acc_date, acc_fp, acc_ne) in accepted_stories:
            if comp != item["competitor"]:
                continue
            try:
                days_apart = abs((item_date - acc_date).days)
            except Exception:
                days_apart = 99
            if days_apart > 14:
                continue

            # Check A: Jaccard word similarity
            if len(fp | acc_fp) > 0:
                jaccard = len(fp & acc_fp) / len(fp | acc_fp)
                if jaccard >= 0.25:
                    is_dupe = True
                    break

            # Check B: shared named entity (e.g. same partner/product name)
            if ne and acc_ne and len(ne & acc_ne) >= 1:
                is_dupe = True
                break

        if is_dupe:
            continue

        seen_urls.add(url)
        seen_heads.add(head_key)
        accepted_stories.append((item["competitor"], item_date, fp, ne))
        unique.append(item)

    return unique

# ─────────────────────────────────────────────────────────────────────────────
# Per-competitor orchestration
# ─────────────────────────────────────────────────────────────────────────────

def scrape_competitor(competitor: dict, industry_items: list[dict]) -> list[dict]:
    name = competitor["name"]
    log.info(f"\n{'='*60}")
    log.info(f"  {name}")
    log.info(f"{'='*60}")

    all_items = []

    # 1. Google News (primary)
    all_items += scrape_google_news(competitor)

    # 2. Industry publications (pre-fetched)
    all_items += industry_items

    # 3. PR Newswire
    all_items += scrape_prnewswire(competitor)

    # 4. Business Wire
    all_items += scrape_businesswire(competitor)

    # 5. SEC EDGAR
    all_items += scrape_sec_edgar(competitor)

    # 6. Direct RSS (fallback)
    all_items += scrape_direct_rss(competitor)

    # Dedup
    all_items = deduplicate(all_items)

    # Coverage check — run fallback queries if thin
    if len(all_items) < 3:
        fallback = FALLBACK_QUERIES.get(name, [])
        if fallback:
            log.info(f"  [{name}] Coverage thin ({len(all_items)} items) — running fallback queries")
            extra = scrape_google_news(competitor, extra_queries=fallback)
            all_items += extra
            all_items = deduplicate(all_items)

    # Sort and cap
    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    all_items.sort(key=lambda x: (priority_order.get(x["priority"], 3), x["date"]))
    all_items = all_items[:MAX_PER_COMPETITOR]
    all_items.sort(key=lambda x: x["date"], reverse=True)

    log.info(f"  [{name}] Final unique: {len(all_items)}")
    return all_items

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("Competitive Intelligence Scraper — Schneider Electric")
    log.info("Sources: Google News, Industry Feeds, PR Newswire,")
    log.info("         Business Wire, SEC EDGAR, Company Direct Feeds")
    log.info("Quality gates: Publisher blocklist, Alias filter,")
    log.info("               Strategic signal, Story-level dedup")
    log.info("=" * 60)

    log.info("\n[Phase 1] Fetching industry publication feeds...")
    industry_by_competitor = scrape_industry_feeds(COMPETITORS)

    log.info("\n[Phase 2] Scraping per-competitor sources...")
    all_items = []
    for competitor in COMPETITORS:
        industry_items = industry_by_competitor.get(competitor["name"], [])
        items = scrape_competitor(competitor, industry_items)
        all_items.extend(items)

    all_items.sort(key=lambda x: x["date"], reverse=True)

    high   = sum(1 for i in all_items if i["priority"] == "HIGH")
    medium = sum(1 for i in all_items if i["priority"] == "MEDIUM")
    low    = sum(1 for i in all_items if i["priority"] == "LOW")

    output = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": all_items,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info("\n" + "=" * 60)
    log.info(f"Done. {len(all_items)} items => data.json")
    log.info(f"  HIGH: {high}   MEDIUM: {medium}   LOW: {low}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
