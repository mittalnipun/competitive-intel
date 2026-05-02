#!/usr/bin/env python3
"""
bundle.py
Embeds data.json into index.html to produce a single self-contained file.
Send dashboard.html to anyone — opens in any browser, no server needed.

Usage:
    python scraper.py    # generate data.json
    python bundle.py     # produces dashboard.html
"""

import json
from pathlib import Path
from datetime import datetime, timezone

DATA_FILE = Path("data.json")
TEMPLATE  = Path("index.html")
OUTPUT    = Path("dashboard.html")


def main():
    if not DATA_FILE.exists():
        print("data.json not found. Run scraper.py first.")
        return

    if not TEMPLATE.exists():
        print("index.html not found.")
        return

    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)

    data_json_str = json.dumps(data, ensure_ascii=False)
    html = TEMPLATE.read_text(encoding="utf-8")

    # Inject inline data block before </head>
    # The loadData() function in index.html already checks window.__CI_DATA__
    # as its second fallback — no further patching needed.
    stamp    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    injected = (
        f"\n  <script>\n"
        f"    // Inline data injected by bundle.py on {stamp}\n"
        f"    window.__CI_DATA__ = {data_json_str};\n"
        f"  </script>\n"
    )

    if "</head>" not in html:
        print("Error: </head> not found in index.html.")
        return

    html = html.replace("</head>", injected + "</head>", 1)
    OUTPUT.write_text(html, encoding="utf-8")

    items  = data.get("items", [])
    high   = sum(1 for i in items if i.get("priority") == "HIGH")
    medium = sum(1 for i in items if i.get("priority") == "MEDIUM")
    low    = sum(1 for i in items if i.get("priority") == "LOW")

    print(f"Done. {OUTPUT} written.")
    print(f"  {len(items)} items  |  HIGH {high}  MEDIUM {medium}  LOW {low}")
    print(f"  Data as of: {data.get('last_updated', 'unknown')}")
    print(f"\nShare dashboard.html — opens in any browser, no internet required.")


if __name__ == "__main__":
    main()
