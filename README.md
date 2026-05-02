# Competitive Intelligence Dashboard
**Mozart by Certis — OT/ICS Security Landscape Tracker**

Tracks competitor news from Siemens, ABB, Rockwell Automation, Honeywell, Emerson, and Yokogawa. Classifies signals by priority. Updates daily at 6 AM PST via GitHub Actions. Hosted on GitHub Pages.

---

## File Structure

```
competitive-intel/
├── scraper.py              # Python scraper — generates data.json
├── index.html              # Apple-style dashboard (reads data.json)
├── data.json               # Auto-generated — do not edit manually
├── requirements.txt        # Python dependencies
└── .github/
    └── workflows/
        └── scrape.yml      # GitHub Actions daily schedule
```

---

## Setup (One Time)

### 1. Create a GitHub repository

Go to [github.com/new](https://github.com/new) and create a **public** repository. Name it something like `competitive-intel`. Copy the clone URL.

### 2. Clone and add files

```bash
git clone https://github.com/YOUR-USERNAME/competitive-intel.git
cd competitive-intel

# Copy all files from this folder into the repo root:
# scraper.py, index.html, requirements.txt, README.md
# and the .github/workflows/scrape.yml folder structure
```

### 3. Run the scraper locally first (optional but recommended)

```bash
pip install requests beautifulsoup4 feedparser lxml
python scraper.py
```

This generates `data.json`. Open `index.html` in a browser to verify the dashboard loads.

> **Note on JS-heavy sites:** Rockwell Automation and Honeywell render most content via JavaScript, so the scraper may return sparse results for those two. Siemens, Emerson, and Yokogawa have RSS feeds that work reliably. ABB HTML scraping is generally successful. This is a known limitation of free, no-API scraping.

### 4. Push to GitHub

```bash
git add .
git commit -m "Initial setup"
git push origin main
```

### 5. Enable GitHub Pages

1. Go to your repo on GitHub.
2. Click **Settings** → **Pages** (left sidebar).
3. Under **Source**, select:
   - Branch: `main`
   - Folder: `/ (root)`
4. Click **Save**.
5. Wait 1–2 minutes. GitHub will display your URL:
   `https://YOUR-USERNAME.github.io/competitive-intel/`

### 6. Enable GitHub Actions

Actions are enabled by default on public repos. To verify:

1. Click the **Actions** tab in your repo.
2. If prompted, click **"I understand my workflows, go ahead and enable them"**.
3. You should see **Daily Competitive Intelligence Scrape** listed.

### 7. Test the workflow manually

1. Click **Actions** → **Daily Competitive Intelligence Scrape** → **Run workflow** → **Run workflow**.
2. The job will run the scraper, commit updated `data.json`, and push.
3. Your dashboard at the GitHub Pages URL will show fresh data on next load.

---

## How the Scraper Works

| Step | Method | Notes |
|------|--------|-------|
| 1 | RSS/Atom feed | Most reliable. Works for Siemens, Emerson, Yokogawa. |
| 2 | HTML scrape | BeautifulSoup with targeted CSS selectors. Works for ABB. |
| 3 | Link extraction | Last resort for JS-rendered sites (Honeywell, Rockwell). |

**Priority classification** is keyword-based:

- **HIGH** — launch, partnership, acquisition, contract, deployment, vulnerability, breach
- **MEDIUM** — blog, webinar, whitepaper, report, thought leadership
- **LOW** — everything else

**"Why it matters"** is generated from the matched keyword, contextualised for Mozart's competitive position.

---

## Schedule

The workflow runs daily at **6:00 AM PST** (14:00 UTC). It only commits `data.json` if the content has changed, keeping the git history clean.

To change the schedule, edit `.github/workflows/scrape.yml`:

```yaml
- cron: "0 14 * * *"   # UTC — adjust as needed
```

---

## Sharing with Your Leader

Once GitHub Pages is live:

1. Open `https://YOUR-USERNAME.github.io/competitive-intel/` to confirm it loads.
2. Share that URL directly. No login required.
3. The page fetches `data.json` from the same repo on every load — no server needed.

---

## Maintenance Notes

- If a competitor site redesigns its HTML, update the CSS selectors in the `COMPETITORS` list inside `scraper.py`.
- To add a competitor, add a new entry to the `COMPETITORS` list with its RSS URL, HTML URL, and selectors.
- RSS feeds are the most stable long-term — check if new competitors expose one before relying on HTML parsing.
- The `data.json` file is committed to the repo, so you always have a fallback of the last successful scrape even if today's run fails.

---

*Internal use only — Mozart by Certis*
