#!/usr/bin/env python3
"""
Competitive Intelligence Scraper
Tracks news from Siemens, ABB, Rockwell Automation, Honeywell, Emerson, Yokogawa.

Strategy:
  1. RSS/Atom feed (most reliable)
  2. HTML scrape with BeautifulSoup (fallback)
  3. Link extraction (last resort for JS-heavy sites)

Output: data.json consumed by index.html dashboard.

Install deps: pip install requests beautifulsoup4 feedparser lxml
"""

import json
import time
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
    import feedparser
except ImportError:
    print("Missing dependencies. Run:")
    print("  pip install requests beautifulsoup4 feedparser lxml")
    raise

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DELAY = 1.5  # seconds between requests (be polite)
MAX_ITEMS_PER_SITE = 8

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

# ---------------------------------------------------------------------------
# Classification rules
# ---------------------------------------------------------------------------

HIGH_KEYWORDS = [
    "launch", "launches", "launched",
    "partnership", "partner",
    "acquisition", "acquires", "acquired",
    "customer win", "wins contract", "contract award", "awarded",
    "deployment", "deployed", "go live",
    "security incident", "incident",
    "vulnerability", "vulnerabilities", "cve",
    "breach", "breached",
    "integration", "integrated",
    "merger", "merges",
    "joint venture",
    "deal", "signed",
    "expansion", "expands",
    "new product", "new solution",
]

MEDIUM_KEYWORDS = [
    "blog", "thought leadership", "webinar", "speaking",
    "opinion", "whitepaper", "white paper",
    "report", "survey", "research",
    "trend", "insight", "prediction",
    "guide", "ebook", "case study",
    "podcast", "interview",
    "award", "recognition",
]

WHY_MATTERS_MAP = {
    "launch": "New product launch may shift competitive positioning — assess capability overlap.",
    "launches": "New product launch may shift competitive positioning — assess capability overlap.",
    "launched": "New product launch may shift competitive positioning — assess capability overlap.",
    "partnership": "Partnership could expand competitor reach and ecosystem capabilities.",
    "partner": "Partnership could expand competitor reach and ecosystem capabilities.",
    "acquisition": "Acquisition signals strategic capability expansion — assess for overlap.",
    "acquires": "Acquisition signals strategic capability expansion — assess for overlap.",
    "customer win": "Customer win demonstrates competitive momentum in target verticals.",
    "contract award": "Contract win signals active competitive motion in enterprise accounts.",
    "awarded": "Contract win signals active competitive motion in enterprise accounts.",
    "deployment": "Active deployment indicates growing market penetration.",
    "deployed": "Active deployment indicates growing market penetration.",
    "security incident": "Security incident may shift buyer trust and vendor evaluation criteria.",
    "vulnerability": "Vulnerability news may affect buyer confidence in competitor solutions.",
    "breach": "Data breach may shift buyer preference during active vendor evaluations.",
    "integration": "Integration announcement expands competitor ecosystem reach.",
    "expansion": "Market expansion signals direct competitive overlap in target geographies.",
    "joint venture": "Joint venture may combine capabilities that create a stronger competitive offering.",
    "deal": "New commercial deal signals competitor momentum in the market.",
    "merger": "Merger signals consolidation — watch for capability or channel overlap.",
    "webinar": "Competitor content motion — signals investment in demand generation.",
    "report": "Competitor thought leadership may be shaping buyer criteria.",
    "award": "Analyst or industry recognition strengthens competitor brand credibility.",
    "whitepaper": "Competitor thought leadership may be shaping buyer evaluation criteria.",
    "case study": "Case study indicates live deployment proof points being built.",
}


def classify(text: str) -> tuple[str, str | None]:
    """Return (priority, matched_keyword) based on text content."""
    lower = text.lower()
    for kw in HIGH_KEYWORDS:
        if kw in lower:
            return "HIGH", kw
    for kw in MEDIUM_KEYWORDS:
        if kw in lower:
            return "MEDIUM", kw
    return "LOW", None


def why_matters(keyword: str | None) -> str:
    if not keyword:
        return "Competitor activity — monitor for strategic relevance."
    for k, v in WHY_MATTERS_MAP.items():
        if k in (keyword or "").lower():
            return v
    return "Competitor activity — monitor for strategic relevance."


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
    "%Y/%m/%d",
    "%a, %d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M:%S GMT",
]


def parse_date(raw: str | None) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not raw:
        return today

    raw = raw.strip()

    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Regex fallback: look for YYYY-MM-DD anywhere in the string
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return m.group(0)

    return today


# ---------------------------------------------------------------------------
# Competitor definitions
# ---------------------------------------------------------------------------

COMPETITORS = [
    {
        "name": "Siemens",
        "rss_urls": [
            "https://press.siemens.com/global/en/tag/cybersecurity/rss.xml",
            "https://press.siemens.com/global/en/pressreleases/rss.xml",
        ],
        "html_url": "https://press.siemens.com/global/en/tag/cybersecurity",
        "item_selectors": ["article", ".press-release-item", ".teaser", ".media-item"],
        "title_selectors": ["h2", "h3", ".title", ".heading"],
        "date_selectors": ["time", ".date", ".published"],
        "link_selectors": ["a"],
        "summary_selectors": ["p", ".description", ".teaser-text"],
    },
    {
        "name": "ABB",
        "rss_urls": [
            "https://new.abb.com/news/rss",
            "https://new.abb.com/feeds/news.rss",
        ],
        "html_url": "https://new.abb.com/news",
        "item_selectors": ["article", ".news-item", ".media-card", ".content-item"],
        "title_selectors": ["h2", "h3", ".heading", ".card-title"],
        "date_selectors": ["time", ".date", ".published"],
        "link_selectors": ["a"],
        "summary_selectors": ["p", ".ingress", ".excerpt", ".card-text"],
    },
    {
        "name": "Rockwell Automation",
        "rss_urls": [
            "https://www.rockwellautomation.com/en-us/about/news.rss.xml",
            "https://www.rockwellautomation.com/rss/news.xml",
        ],
        "html_url": "https://www.rockwellautomation.com/en-us/about/news.html",
        "item_selectors": [".news-card", "article", ".press-release", ".media-card"],
        "title_selectors": ["h2", "h3", ".card-title", ".headline"],
        "date_selectors": ["time", ".date", ".publish-date"],
        "link_selectors": ["a"],
        "summary_selectors": ["p", ".card-text", ".excerpt"],
    },
    {
        "name": "Honeywell",
        "rss_urls": [
            "https://www.honeywell.com/us/en/press/rss.xml",
            "https://honeywell.com/feeds/press.rss",
        ],
        "html_url": "https://www.honeywell.com/us/en/press",
        "item_selectors": ["article", ".press-release", ".news-item", ".card"],
        "title_selectors": ["h2", "h3", ".title", ".card-title"],
        "date_selectors": ["time", ".date", ".published-date"],
        "link_selectors": ["a"],
        "summary_selectors": ["p", ".summary", ".excerpt"],
    },
    {
        "name": "Emerson",
        "rss_urls": [
            "https://www.emerson.com/en-us/news/rss",
            "https://www.emerson.com/feeds/news",
        ],
        "html_url": "https://www.emerson.com/en-us/news",
        "item_selectors": ["article", ".news-card", ".press-release", ".item"],
        "title_selectors": ["h2", "h3", ".headline", ".title"],
        "date_selectors": ["time", ".date", ".news-date"],
        "link_selectors": ["a"],
        "summary_selectors": ["p", ".summary", ".lead"],
    },
    {
        "name": "Yokogawa",
        "rss_urls": [
            "https://www.yokogawa.com/news/rss/",
            "https://www.yokogawa.com/library/resources/white-papers/rss/",
        ],
        "html_url": "https://www.yokogawa.com/news",
        "item_selectors": [".news-item", "article", ".press-release", ".list-item"],
        "title_selectors": ["h2", "h3", ".title", ".news-title"],
        "date_selectors": ["time", ".date", ".news-date", ".publish-date"],
        "link_selectors": ["a"],
        "summary_selectors": ["p", ".description", ".excerpt"],
    },
]


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def make_item(competitor_name: str, title: str, date: str, url: str, summary: str) -> dict:
    combined = f"{title} {summary}"
    priority, kw = classify(combined)
    return {
        "competitor": competitor_name,
        "headline": title.strip(),
        "date": date,
        "url": url,
        "priority": priority,
        "summary": summary.strip()[:160],
        "why_matters": why_matters(kw),
    }


def try_rss(competitor: dict) -> list[dict]:
    """Attempt each RSS URL in order. Return items from first that works."""
    for rss_url in competitor.get("rss_urls", []):
        try:
            log.info(f"  [{competitor['name']}] RSS → {rss_url}")
            feed = feedparser.parse(rss_url)

            if not feed.entries:
                log.debug(f"  [{competitor['name']}] RSS empty, trying next")
                continue

            items = []
            for entry in feed.entries[:MAX_ITEMS_PER_SITE]:
                title = (entry.get("title") or "").strip()
                if not title or len(title) < 10:
                    continue

                link = entry.get("link") or competitor["html_url"]

                # Date
                date_raw = (
                    entry.get("published")
                    or entry.get("updated")
                    or entry.get("created")
                    or None
                )
                date = parse_date(date_raw)

                # Summary: strip HTML tags
                raw_summary = (
                    entry.get("summary")
                    or entry.get("description")
                    or ""
                )
                summary = BeautifulSoup(raw_summary, "html.parser").get_text()
                summary = re.sub(r"\s+", " ", summary).strip()[:160]
                if not summary:
                    summary = title[:160]

                items.append(make_item(competitor["name"], title, date, link, summary))

            if items:
                log.info(f"  [{competitor['name']}] RSS success: {len(items)} items")
                return items

        except Exception as e:
            log.debug(f"  [{competitor['name']}] RSS error ({rss_url}): {e}")

    return []


def try_html(competitor: dict) -> list[dict]:
    """BeautifulSoup HTML scrape with multi-selector approach."""
    url = competitor["html_url"]
    try:
        log.info(f"  [{competitor['name']}] HTML → {url}")
        resp = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        time.sleep(DELAY)

        # Find item containers
        containers = []
        for sel in competitor["item_selectors"]:
            found = soup.select(sel)
            if found:
                containers = found[:MAX_ITEMS_PER_SITE]
                log.debug(f"  [{competitor['name']}] Container '{sel}': {len(containers)}")
                break

        if not containers:
            return try_links(competitor, soup)

        items = []
        for container in containers:
            # Title
            title = ""
            for sel in competitor["title_selectors"]:
                el = container.select_one(sel)
                if el:
                    title = el.get_text(strip=True)
                    break
            if not title:
                for tag in ["h1", "h2", "h3", "h4"]:
                    el = container.find(tag)
                    if el:
                        title = el.get_text(strip=True)
                        break
            if not title or len(title) < 10:
                continue

            # Link
            link = url
            for sel in competitor["link_selectors"]:
                el = container.select_one(sel)
                if el and el.get("href"):
                    href = el["href"]
                    link = href if href.startswith("http") else urljoin(url, href)
                    break

            # Date
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            for sel in competitor["date_selectors"]:
                el = container.select_one(sel)
                if el:
                    raw = el.get("datetime") or el.get_text(strip=True)
                    date = parse_date(raw)
                    break

            # Summary
            summary = ""
            for sel in competitor["summary_selectors"]:
                el = container.select_one(sel)
                if el:
                    summary = el.get_text(strip=True)[:160]
                    break
            if not summary:
                summary = title[:160]

            items.append(make_item(competitor["name"], title, date, link, summary))

        if items:
            log.info(f"  [{competitor['name']}] HTML success: {len(items)} items")
        return items

    except requests.RequestException as e:
        log.warning(f"  [{competitor['name']}] HTML fetch failed: {e}")
        return []
    except Exception as e:
        log.error(f"  [{competitor['name']}] HTML parse error: {e}")
        return []


def try_links(competitor: dict, soup: BeautifulSoup) -> list[dict]:
    """Last resort: extract meaningful anchor text from page."""
    log.info(f"  [{competitor['name']}] Link extraction fallback")
    base_url = competitor["html_url"]
    base_domain = urlparse(base_url).netloc

    skip_patterns = [
        "privacy", "terms", "cookie", "legal", "careers",
        "contact", "about", "login", "signin", "subscribe",
        "newsletter", "sitemap", "accessibility",
    ]

    # Junk patterns common in nav/region/language selectors
    junk_patterns = [
        " - english", " - spanish", " - french", " - german",
        " - portuguese", " - italian", " - dutch", " - chinese",
        " - japanese", " - korean", " - indonesian", " - thai",
        " - vietnamese", " - arabic", " - russian", " - polish",
        "south africa", "united states", "united kingdom", "republic of",
        "hong kong", "new zealand", "middle east", "latin america",
        "asia pacific", "select country", "select region", "select language",
        "global site", "local site",
    ]

    seen = set()
    items = []

    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]

        if not text or len(text) < 30 or text in seen:
            continue

        # Skip nav/region/language links
        text_lower = text.lower()
        if any(j in text_lower for j in junk_patterns):
            continue

        # Build full URL
        if href.startswith("http"):
            full_url = href
        elif href.startswith("/"):
            full_url = urljoin(base_url, href)
        else:
            continue

        if urlparse(full_url).netloc != base_domain:
            continue

        if any(p in href.lower() for p in skip_patterns):
            continue

        seen.add(text)
        items.append(make_item(
            competitor["name"],
            text,
            datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            full_url,
            text[:160],
        ))

        if len(items) >= MAX_ITEMS_PER_SITE:
            break

    log.info(f"  [{competitor['name']}] Link fallback: {len(items)} items")
    return items


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scrape(competitor: dict) -> list[dict]:
    log.info(f"[{competitor['name']}] Starting...")

    items = try_rss(competitor)
    if not items:
        time.sleep(DELAY)
        items = try_html(competitor)

    if not items:
        log.warning(f"[{competitor['name']}] No items found (site may be JS-rendered or unreachable).")

    time.sleep(DELAY)
    return items


def main():
    log.info("=" * 60)
    log.info("Competitive Intelligence Scraper — Schneider Electric")
    log.info("=" * 60)

    all_items: list[dict] = []

    for comp in COMPETITORS:
        try:
            items = scrape(comp)
            all_items.extend(items)
        except Exception as e:
            log.error(f"[{comp['name']}] Unexpected error: {e}")

    # Sort: date descending, then priority for same date
    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    all_items.sort(
        key=lambda x: (x["date"], priority_order.get(x["priority"], 3)),
        reverse=True,
    )

    output = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": all_items,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    high = sum(1 for i in all_items if i["priority"] == "HIGH")
    medium = sum(1 for i in all_items if i["priority"] == "MEDIUM")
    low = sum(1 for i in all_items if i["priority"] == "LOW")

    log.info("=" * 60)
    log.info(f"Done. {len(all_items)} items → data.json")
    log.info(f"  HIGH: {high}  MEDIUM: {medium}  LOW: {low}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
