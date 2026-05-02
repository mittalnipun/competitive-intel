#!/usr/bin/env python3
"""
Competitive Intelligence Scraper — Schneider Electric
Tracks Siemens, ABB, Rockwell Automation, Honeywell, Emerson, Yokogawa
across 10+ source types per competitor.

Source hierarchy:
  1. Google News RSS  — primary, broad, high quality
  2. Industry publications RSS — Control Engineering, Industrial Cyber,
     SecurityWeek, Automation World, ISSSource, The Manufacturer
  3. PR Newswire / Business Wire company feeds
  4. SEC EDGAR RSS   — earnings, 8-K filings (public companies)
  5. Company direct RSS/pages — fallback

Output: data.json consumed by index.html dashboard.

Install: pip install requests beautifulsoup4 feedparser lxml
"""

import json
import time
import logging
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, quote_plus

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
MAX_PER_SOURCE     = 8     # max items per individual source
MAX_PER_COMPETITOR = 8     # max items per competitor in final output
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
# Competitor profiles
# ─────────────────────────────────────────────────────────────────────────────

COMPETITORS = [
    {
        "name": "Siemens",
        "aliases": ["Siemens", "Siemens AG", "Siemens Energy", "Siemens Digital"],
        "google_news_queries": [
            "Siemens industrial automation OT security",
            "Siemens Sinec cybersecurity",
            "Siemens EcoStruxure competitor",
            "Siemens Simatic PLC security",
        ],
        "prnewswire_id": "siemens",
        "businesswire_slug": "siemens",
        "sec_cik": None,  # German company, not SEC-listed
        "direct_rss": [
            "https://press.siemens.com/global/en/pressreleases/rss.xml",
            "https://new.siemens.com/global/en/company/press/press-releases.rss",
        ],
        "direct_html": "https://press.siemens.com/global/en/pressreleases",
    },
    {
        "name": "ABB",
        "aliases": ["ABB", "ABB Ltd", "ABB Group", "ABB Ability"],
        "google_news_queries": [
            "ABB Ltd industrial automation",
            "ABB Group robotics acquisition 2025 2026",
            "ABB Ability digital platform launch",
            "ABB OT cybersecurity process control",
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
        "aliases": ["Rockwell Automation", "Rockwell", "FactoryTalk", "Allen-Bradley"],
        "google_news_queries": [
            "Rockwell Automation FactoryTalk security",
            "Rockwell Automation smart manufacturing",
            "Rockwell Automation OT cybersecurity contract",
            "Rockwell Automation partnership acquisition",
        ],
        "prnewswire_id": "rockwell-automation",
        "businesswire_slug": "rockwellautomation",
        "sec_cik": "0001024478",
        "direct_rss": [
            "https://www.rockwellautomation.com/en-us/about/news.rss.xml",
        ],
        "direct_html": "https://www.rockwellautomation.com/en-us/about/news.html",
    },
    {
        "name": "Honeywell",
        "aliases": ["Honeywell", "Honeywell International", "Honeywell Forge", "Honeywell Connected"],
        "google_news_queries": [
            "Honeywell Forge OT industrial cybersecurity",
            "Honeywell process automation security",
            "Honeywell industrial AI partnership",
            "Honeywell OT threat detection",
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
        "aliases": ["Emerson", "Emerson Electric", "DeltaV", "Emerson Automation"],
        "google_news_queries": [
            "Emerson DeltaV OT automation security",
            "Emerson process automation cybersecurity",
            "Emerson Electric industrial AI",
            "Emerson automation partnership contract",
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
        "aliases": ["Yokogawa", "Yokogawa Electric", "CENTUM", "ProSafe"],
        "google_news_queries": [
            "Yokogawa Electric automation",
            "Yokogawa CENTUM industrial control",
            "Yokogawa partnership contract 2025 2026",
            "Yokogawa digital transformation OT",
        ],
        "prnewswire_id": "yokogawa",
        "businesswire_slug": "yokogawa",
        "sec_cik": None,  # Japanese company, not SEC-listed
        "direct_rss": [
            "https://www.yokogawa.com/news/rss/",
            "https://www.yokogawa.com/library/resources/white-papers/rss/",
        ],
        "direct_html": "https://www.yokogawa.com/news",
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Industry publication RSS feeds
# Fetched once, then filtered by competitor name mentions
# ─────────────────────────────────────────────────────────────────────────────

INDUSTRY_FEEDS = [
    {
        "name": "Industrial Cyber",
        "url": "https://industrialcyber.co/feed/",
        "weight": "HIGH",   # OT/ICS security focused — signals are high quality
    },
    {
        "name": "SecurityWeek",
        "url": "https://feeds.feedburner.com/Securityweek",
        "weight": "MEDIUM",
    },
    {
        "name": "Control Engineering",
        "url": "https://www.controleng.com/feed/",
        "weight": "MEDIUM",
    },
    {
        "name": "Automation World",
        "url": "https://www.automationworld.com/home/rss.xml",
        "weight": "MEDIUM",
    },
    {
        "name": "ISSSource",
        "url": "https://www.isssource.com/feed/",
        "weight": "MEDIUM",
    },
    {
        "name": "The Manufacturer",
        "url": "https://www.themanufacturer.com/feed/",
        "weight": "LOW",
    },
    {
        "name": "IndustryWeek",
        "url": "https://www.industryweek.com/rss/all",
        "weight": "LOW",
    },
    {
        "name": "Plant Engineering",
        "url": "https://www.plantengineering.com/feed/",
        "weight": "LOW",
    },
]

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
    "integration", "solution", "platform",
]

# Why-it-matters mapped to keyword matched — Schneider context
WHY_MATTERS = {
    "launch":        "New product/feature launch — assess capability overlap with Schneider's EcoStruxure stack and update competitive positioning.",
    "launches":      "New product/feature launch — assess capability overlap with Schneider's EcoStruxure stack and update competitive positioning.",
    "release":       "New release may shift feature parity — review against Schneider's current roadmap.",
    "partnership":   "Ecosystem partnership expands competitor's reach — evaluate overlap with Schneider's partner network and customer base.",
    "partner":       "Ecosystem partnership expands competitor's reach — evaluate overlap with Schneider's partner network and customer base.",
    "acquisition":   "Acquisition signals strategic capability gap being filled — identify what they bought and whether Schneider has equivalent depth.",
    "acquires":      "Acquisition signals strategic capability gap being filled — identify what they bought and whether Schneider has equivalent depth.",
    "contract":      "Contract win in a key vertical — assess whether this is a Schneider account, adjacent account, or new territory.",
    "win":           "Competitive win reported — identify vertical and geography and assess displacement risk for Schneider accounts.",
    "deployment":    "Live deployment in the field — use as a reference case signal and check for overlap with Schneider's installed base.",
    "vulnerability": "Security vulnerability disclosed — may prompt customers to review their automation vendor mix. Potential Schneider opportunity.",
    "breach":        "Breach or incident linked to competitor's platform — assess reputational impact and customer confidence signals.",
    "cve":           "CVE disclosed in competitor's product — monitor for customer concern and prepare Schneider's security positioning response.",
    "funding":       "New funding round — competitor has capital to accelerate R&D and market expansion. Reassess competitive intensity.",
    "acquisition":   "Acquisition signals capability gap being filled — identify what they bought and whether Schneider has equivalent capability.",
    "appoints":      "Leadership change — new executives often signal strategic pivots. Monitor messaging changes over next 90 days.",
    "report":        "Competitor thought leadership shaping buyer criteria — review for messaging gaps in Schneider's content strategy.",
    "webinar":       "Competitor investing in demand generation targeting the same buyer profiles as Schneider — match or counter.",
    "whitepaper":    "Competitor thought leadership on a key topic — review for messaging gaps against Schneider's content strategy.",
    "award":         "Industry recognition strengthens competitor's enterprise sales credibility — relevant to Schneider's positioning.",
}

DEFAULT_WHY = "Monitor for impact on Schneider's competitive positioning and customer conversations."

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

def why_matters(keyword: str) -> str:
    return WHY_MATTERS.get(keyword, DEFAULT_WHY)

def parse_date(raw: str) -> str:
    """Parse various date formats to YYYY-MM-DD."""
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
    # feedparser struct_time
    try:
        t = time.strptime(raw, "%a, %d %b %Y %H:%M:%S %Z")
        return datetime(*t[:3]).strftime("%Y-%m-%d")
    except Exception:
        pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def parse_feedparser_date(entry) -> str:
    """Extract date from feedparser entry."""
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
    """Return True if date is within MAX_AGE_DAYS."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return (datetime.now() - d).days <= MAX_AGE_DAYS
    except Exception:
        return True

def make_item(competitor: str, headline: str, date: str, url: str,
              summary: str, source: str = "") -> dict:
    priority, kw = classify(headline + " " + summary)
    return {
        "competitor": competitor,
        "headline": headline.strip(),
        "date": date,
        "url": url,
        "priority": priority,
        "summary": summary.strip()[:200],
        "why_matters": why_matters(kw),
        "source": source,
    }

def fetch_rss(url: str, label: str) -> list:
    """Fetch and parse an RSS/Atom feed. Returns list of feedparser entries."""
    try:
        log.info(f"  RSS  {label} → {url}")
        feed = feedparser.parse(url, request_headers=HEADERS)
        entries = feed.get("entries", [])
        log.info(f"       {len(entries)} entries")
        time.sleep(DELAY)
        return entries
    except Exception as e:
        log.warning(f"  RSS  {label} failed: {e}")
        return []

def fetch_html(url: str, label: str) -> BeautifulSoup | None:
    """Fetch a page and return BeautifulSoup object."""
    try:
        log.info(f"  HTML {label} → {url}")
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        r.raise_for_status()
        time.sleep(DELAY)
        return BeautifulSoup(r.text, "lxml")
    except Exception as e:
        log.warning(f"  HTML {label} failed: {e}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
# Source 1: Google News RSS  (primary — best quality)
# ─────────────────────────────────────────────────────────────────────────────

def scrape_google_news(competitor: dict) -> list[dict]:
    """Pull Google News RSS for each query string configured per competitor."""
    items = []
    seen_urls = set()

    for query in competitor["google_news_queries"]:
        encoded = quote_plus(query)
        url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
        entries = fetch_rss(url, f"Google News / {competitor['name']}")

        for e in entries[:MAX_PER_SOURCE]:
            link = getattr(e, "link", "") or ""
            if link in seen_urls:
                continue
            seen_urls.add(link)

            title   = getattr(e, "title", "").strip()
            summary = getattr(e, "summary", title).strip()
            # Strip HTML tags from summary
            summary = re.sub(r"<[^>]+>", "", summary)[:200]
            date    = parse_feedparser_date(e)

            if not title or not is_recent(date):
                continue

            # Hard filter: article must actually mention this competitor
            if not mentions_competitor(title + " " + summary, competitor):
                continue

            items.append(make_item(
                competitor["name"], title, date, link, summary, "Google News"
            ))

    log.info(f"  [Google News] {competitor['name']}: {len(items)} items")
    return items

# ─────────────────────────────────────────────────────────────────────────────
# Source 2: PR Newswire company feed
# ─────────────────────────────────────────────────────────────────────────────

def mentions_competitor(text: str, competitor: dict) -> bool:
    """Return True if any competitor alias appears in text (case-insensitive)."""
    text_lower = text.lower()
    return any(alias.lower() in text_lower for alias in competitor["aliases"])


def scrape_prnewswire(competitor: dict) -> list[dict]:
    """Pull PR Newswire RSS for this company's press room."""
    slug = competitor.get("prnewswire_id", "")
    if not slug:
        return []

    urls = [
        f"https://www.prnewswire.com/rss/news-releases-list.rss?company={slug}",
    ]

    items = []
    for url in urls:
        entries = fetch_rss(url, f"PR Newswire / {competitor['name']}")
        for e in entries[:MAX_PER_SOURCE * 3]:  # scan more, filter strictly
            title   = getattr(e, "title", "").strip()
            link    = getattr(e, "link", "") or ""
            summary = re.sub(r"<[^>]+>", "", getattr(e, "summary", title))[:200]
            date    = parse_feedparser_date(e)
            if not title or not is_recent(date):
                continue
            # Hard filter: article must actually mention this competitor
            if not mentions_competitor(title + " " + summary, competitor):
                continue
            items.append(make_item(
                competitor["name"], title, date, link, summary, "PR Newswire"
            ))

    return items

# ─────────────────────────────────────────────────────────────────────────────
# Source 3: Business Wire company feed
# ─────────────────────────────────────────────────────────────────────────────

def scrape_businesswire(competitor: dict) -> list[dict]:
    """Pull Business Wire RSS for this company."""
    slug = competitor.get("businesswire_slug", "")
    if not slug:
        return []

    url = f"https://www.businesswire.com/rss/home/?rss=G7&company={slug}"
    items = []
    entries = fetch_rss(url, f"Business Wire / {competitor['name']}")
    for e in entries[:MAX_PER_SOURCE * 3]:  # scan more, filter strictly
        title   = getattr(e, "title", "").strip()
        link    = getattr(e, "link", "") or ""
        summary = re.sub(r"<[^>]+>", "", getattr(e, "summary", title))[:200]
        date    = parse_feedparser_date(e)
        if not title or not is_recent(date):
            continue
        # Hard filter: article must actually mention this competitor
        if not mentions_competitor(title + " " + summary, competitor):
            continue
        items.append(make_item(
            competitor["name"], title, date, link, summary, "Business Wire"
        ))
    return items

# ─────────────────────────────────────────────────────────────────────────────
# Source 4: SEC EDGAR RSS (US-listed companies only)
# ─────────────────────────────────────────────────────────────────────────────

def scrape_sec_edgar(competitor: dict) -> list[dict]:
    """Pull SEC EDGAR filings RSS (8-K, earnings) for US-listed companies."""
    cik = competitor.get("sec_cik")
    if not cik:
        return []

    url = (
        f"https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={cik}&type=8-K&dateb=&owner=include"
        f"&count=10&search_text=&output=atom"
    )
    items = []
    entries = fetch_rss(url, f"SEC EDGAR / {competitor['name']}")
    for e in entries[:5]:
        title   = getattr(e, "title", "").strip()
        link    = getattr(e, "link", "") or ""
        date    = parse_feedparser_date(e)
        if not title or not is_recent(date):
            continue
        # 8-K filings are always significant
        items.append({
            "competitor": competitor["name"],
            "headline":   f"{competitor['name']} SEC Filing: {title}",
            "date":       date,
            "url":        link,
            "priority":   "MEDIUM",
            "summary":    f"SEC 8-K filing by {competitor['name']}: {title}",
            "why_matters": "Regulatory filing may contain material business events — review for M&A, restructuring, or major contract disclosures.",
            "source":     "SEC EDGAR",
        })
    return items

# ─────────────────────────────────────────────────────────────────────────────
# Source 5: Company direct RSS feeds
# ─────────────────────────────────────────────────────────────────────────────

def scrape_direct_rss(competitor: dict) -> list[dict]:
    """Try each company's own RSS feed URLs."""
    items = []
    seen = set()

    for url in competitor.get("direct_rss", []):
        entries = fetch_rss(url, f"Direct RSS / {competitor['name']}")
        for e in entries[:MAX_PER_SOURCE]:
            title   = getattr(e, "title", "").strip()
            link    = getattr(e, "link", "") or ""
            if not title or link in seen:
                continue
            seen.add(link)
            summary = re.sub(r"<[^>]+>", "", getattr(e, "summary", title))[:200]
            date    = parse_feedparser_date(e)
            if not is_recent(date):
                continue
            items.append(make_item(
                competitor["name"], title, date, link, summary,
                f"Direct / {competitor['name']}"
            ))
        if items:
            break  # stop at first successful feed

    return items

# ─────────────────────────────────────────────────────────────────────────────
# Source 6: Industry publications  (fetched once, filtered per competitor)
# ─────────────────────────────────────────────────────────────────────────────

def scrape_industry_feeds(all_competitors: list[dict]) -> dict[str, list[dict]]:
    """
    Fetch each industry publication RSS once.
    For each item, detect which competitor(s) it mentions.
    Returns {competitor_name: [items]}.
    """
    # Build alias lookup: alias_lower -> competitor_name
    alias_map: dict[str, str] = {}
    for c in all_competitors:
        for alias in c["aliases"]:
            alias_map[alias.lower()] = c["name"]

    results: dict[str, list[dict]] = {c["name"]: [] for c in all_competitors}

    for feed_cfg in INDUSTRY_FEEDS:
        entries = fetch_rss(feed_cfg["url"], f"Industry / {feed_cfg['name']}")

        for e in entries:
            title   = getattr(e, "title", "").strip()
            link    = getattr(e, "link", "") or ""
            summary = re.sub(r"<[^>]+>", "", getattr(e, "summary", title))[:300]
            date    = parse_feedparser_date(e)

            if not title or not is_recent(date):
                continue

            text_lower = (title + " " + summary).lower()

            matched = set()
            for alias_lower, comp_name in alias_map.items():
                if alias_lower in text_lower:
                    matched.add(comp_name)

            for comp_name in matched:
                if len(results[comp_name]) >= MAX_PER_SOURCE:
                    continue
                results[comp_name].append(make_item(
                    comp_name, title, date, link, summary, feed_cfg["name"]
                ))

    for comp_name, items in results.items():
        log.info(f"  [Industry feeds] {comp_name}: {len(items)} items")

    return results

# ─────────────────────────────────────────────────────────────────────────────
# Deduplication
# ─────────────────────────────────────────────────────────────────────────────

def _story_fingerprint(headline: str) -> frozenset:
    """Extract significant words from a headline for story-level matching."""
    stop = {"the", "a", "an", "and", "or", "for", "to", "of", "in", "on",
            "at", "by", "as", "is", "are", "its", "with", "new", "how",
            "from", "that", "this", "has", "have", "will", "be", "it"}
    words = re.sub(r"\W+", " ", headline.lower()).split()
    return frozenset(w for w in words if len(w) > 3 and w not in stop)


def deduplicate(items: list[dict]) -> list[dict]:
    """
    Remove duplicate items.
    1. Exact URL match (stripped of query params)
    2. Very similar headline prefix (first 60 chars)
    3. Same competitor + same story within 7 days (Jaccard similarity >= 0.5)
    """
    seen_urls   = set()
    seen_heads  = set()
    # story clusters: list of (competitor, date, fingerprint) for accepted items
    accepted_stories: list[tuple] = []
    unique = []

    for item in items:
        url      = item["url"].split("?")[0].rstrip("/")
        head_key = re.sub(r"\W+", " ", item["headline"].lower()).strip()[:60]

        if url and url in seen_urls:
            continue
        if head_key and head_key in seen_heads:
            continue

        # Story-level dedup: same competitor, close date, similar words
        fp = _story_fingerprint(item["headline"])
        is_dupe = False
        try:
            item_date = datetime.strptime(item["date"], "%Y-%m-%d")
        except Exception:
            item_date = datetime.now()

        for (comp, acc_date, acc_fp) in accepted_stories:
            if comp != item["competitor"]:
                continue
            try:
                days_apart = abs((item_date - acc_date).days)
            except Exception:
                days_apart = 99
            if days_apart > 14:
                continue
            # Jaccard similarity
            if len(fp | acc_fp) == 0:
                continue
            jaccard = len(fp & acc_fp) / len(fp | acc_fp)
            if jaccard >= 0.25:
                is_dupe = True
                break

        if is_dupe:
            continue

        seen_urls.add(url)
        seen_heads.add(head_key)
        accepted_stories.append((item["competitor"], item_date, fp))
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

    # 2. Industry publications (pre-fetched, passed in)
    all_items += industry_items

    # 3. PR Newswire
    all_items += scrape_prnewswire(competitor)

    # 4. Business Wire
    all_items += scrape_businesswire(competitor)

    # 5. SEC EDGAR
    all_items += scrape_sec_edgar(competitor)

    # 6. Direct RSS (fallback)
    all_items += scrape_direct_rss(competitor)

    # Dedup and sort
    all_items = deduplicate(all_items)
    all_items.sort(key=lambda x: x["date"], reverse=True)

    # Prioritise HIGH > MEDIUM > LOW when capping
    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    all_items.sort(key=lambda x: (priority_order.get(x["priority"], 3), x["date"]))
    all_items = all_items[:MAX_PER_COMPETITOR]
    all_items.sort(key=lambda x: x["date"], reverse=True)

    log.info(f"  [{name}] Total unique: {len(all_items)}")
    return all_items

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("Competitive Intelligence Scraper — Schneider Electric")
    log.info(f"Sources: Google News, Industry Publications, PR Newswire,")
    log.info(f"         Business Wire, SEC EDGAR, Company Direct Feeds")
    log.info("=" * 60)

    # Fetch industry publication feeds once (efficient — one fetch serves all competitors)
    log.info("\n[Phase 1] Fetching industry publication feeds...")
    industry_by_competitor = scrape_industry_feeds(COMPETITORS)

    # Scrape each competitor
    log.info("\n[Phase 2] Scraping per-competitor sources...")
    all_items = []
    for competitor in COMPETITORS:
        industry_items = industry_by_competitor.get(competitor["name"], [])
        items = scrape_competitor(competitor, industry_items)
        all_items.extend(items)

    # Final sort: date descending
    all_items.sort(key=lambda x: x["date"], reverse=True)

    # Stats
    high   = sum(1 for i in all_items if i["priority"] == "HIGH")
    medium = sum(1 for i in all_items if i["priority"] == "MEDIUM")
    low    = sum(1 for i in all_items if i["priority"] == "LOW")

    # Write data.json
    output = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": all_items,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info("\n" + "=" * 60)
    log.info(f"Done. {len(all_items)} items → data.json")
    log.info(f"  HIGH: {high}  MEDIUM: {medium}  LOW: {low}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
