# Logo-scrape

Scrapes logos for the Asheville/WNC organization list in `orgs.txt`.

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
python logo_scraper.py
```

- Logos are saved to `./logos/`
- A summary is written to `./results.csv` (org name, source strategy, logo URL, status)

## How it works

For each org in `orgs.txt`, the script tries three strategies in order:

1. **Clearbit Logo API** — uses the org's domain (pre-filled in `DOMAIN_MAP`
   inside `logo_scraper.py` for the current list; edit it to add or correct domains).
2. **Homepage scrape** — reaches the domain directly (or finds it via DuckDuckGo
   search) and pulls `og:image`, `apple-touch-icon`, or favicon.
3. **Google Custom Search** *(optional)* — enabled by setting the
   `GOOGLE_API_KEY` and `GOOGLE_CSE_ID` environment variables.

Requests are throttled (`DELAY_SECONDS = 1.5`), so a full run over the 34-org
list takes a few minutes.
