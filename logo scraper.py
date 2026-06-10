"""
logo_scraper.py
---------------
Scrapes logos for a list of organizations.

SETUP:
  pip install requests beautifulsoup4 Pillow

USAGE:
  1. Edit DOMAIN_MAP below if you know an org's exact domain.
  2. Create orgs.txt with one organization name per line.
  3. Run:  python logo_scraper.py
  4. Logos saved to ./logos/
  5. Summary saved to ./results.csv

OPTIONAL GOOGLE FALLBACK:
  Set env vars GOOGLE_API_KEY and GOOGLE_CSE_ID to enable
  Google Custom Search as an additional fallback strategy.
"""

import os
import re
import csv
import time
import logging
import requests
from pathlib import Path
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

ORGS_FILE       = "orgs.txt"
OUTPUT_DIR      = Path("logos")
RESULTS_CSV     = "results.csv"
DELAY_SECONDS   = 1.5
REQUEST_TIMEOUT = 12

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; LogoScraper/1.0)"
    )
}

# Optional Google Custom Search fallback
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID  = os.environ.get("GOOGLE_CSE_ID", "")

# ── Known domain map ──────────────────────────────────────────────────────────
# Pre-filled for the Asheville/WNC org list.
# Add or correct entries here — format is "Org Name (lowercased)": "domain.tld"
# This bypasses the domain-guessing step and goes straight to Clearbit + homepage.

DOMAIN_MAP: dict[str, str] = {
    "asheville area chamber of commerce":                    "ashevillechamber.org",
    "asheville chamber of commerce":                         "ashevillechamber.org",
    "economic development coalition for asheville-buncombe county": "edcavl.com",
    "henderson county chamber of commerce":                  "hendersoncountychamber.org",
    "henderson county partnership":                          "hendersoncountypartnership.org",
    "goodwill industries of northwest carolina":             "goodwillnwc.org",
    "habitat for humanity international inc":                "habitat.org",
    "literacy together":                                     "literacytogether.org",
    "manna foodbank":                                        "mannafoodbank.org",
    "ontrack wnc":                                           "ontrackwnc.org",
    "open doors of asheville":                               "opendoorsofasheville.org",
    "operation gateway":                                     "operationgateway.org",
    "pisgah legal services":                                 "pisgahlegal.org",
    "safelight inc":                                         "safelightnc.org",
    "veterans healing farm":                                 "veteranshealingfarm.org",
    "all souls counseling":                                  "allsoulscounseling.com",
    "american red cross":                                    "redcross.org",
    "arts avl":                                              "artsavl.org",
    "artsavl":                                               "artsavl.org",
    "blue ridge community college foundation":               "blueridge.edu",
    "bounty & soul":                                         "bountyandsoul.org",
    "boys & girls club of henderson county":                 "bgchendersoncounty.org",
    "helpmate":                                              "helpmateonline.org",
    "asheville museum of science":                           "ashevillescience.org",
    "asheville art museum":                                  "ashevilleart.org",
    "mountain bizworks":                                     "mountainbizworks.org",
    "self help credit union":                                "self-help.org",
    "appalachian community capital":                         "appalachiancommunitycapital.org",
    "asheville downtown association":                        "ashevilledowntown.org",
    "asheville symphony & orchestra":                        "ashevillesymphony.org",
    "blue ridge pride":                                      "blueridgepride.org",
    "avl stonewall fest":                                    "ashevillestonewallsports.org",
    "unc asheville athletics":                               "uncaathletics.com",
    "wortham center for the performing arts":                "worthamcenter.org",
}

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name.strip().lower())


def get_domain(org_name: str) -> list[str]:
    """
    Returns a prioritized list of domains to try.
    Uses DOMAIN_MAP first, then falls back to naive guessing.
    """
    key = org_name.strip().lower()
    if key in DOMAIN_MAP:
        return [DOMAIN_MAP[key]]

    # Naive fallback: strip stopwords, try .org and .com
    stopwords = {"the", "of", "for", "and", "&", "a", "an", "inc", "llc", "corp",
                 "international", "county", "center", "community"}
    tokens = [t for t in re.sub(r"[^\w\s]", "", key).split() if t not in stopwords]
    slug = "".join(tokens[:4])
    return [slug + ".org", slug + ".com", slug + ".net"]


def download_image(url: str, dest: Path) -> bool:
    """Download image at url to dest. Returns True on success."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True)
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "")
        if not any(t in ct for t in ("image", "octet-stream")):
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return dest.stat().st_size > 500
    except Exception as e:
        log.debug("Download failed %s → %s", url, e)
        return False


# ── Strategy 1: Clearbit Logo API ─────────────────────────────────────────────

def try_clearbit(org_name: str, dest_stem: Path) -> tuple[bool, str]:
    """
    Clearbit returns a logo PNG for a given domain. Free, no key needed.
    Tries all candidate domains from get_domain().
    """
    for domain in get_domain(org_name):
        url = f"https://logo.clearbit.com/{domain}"
        dest = dest_stem.with_suffix(".png")
        log.debug("  Clearbit: %s", url)
        if download_image(url, dest):
            return True, url
    return False, ""


# ── Strategy 2: Scrape official homepage ──────────────────────────────────────

def find_homepage_via_search(org_name: str) -> str | None:
    """
    DuckDuckGo HTML search to find official site. No API key needed.
    Rate-sensitive — the DELAY_SECONDS pause between orgs helps.
    """
    query = f'"{org_name}" official site'
    url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a.result__url"):
            href = a.get("href", "").strip()
            if href.startswith("http"):
                return href
        for a in soup.select(".result__title a"):
            href = a.get("href", "").strip()
            if href.startswith("http") and "duckduckgo" not in href:
                return href
    except Exception as e:
        log.debug("  DDG search failed: %s", e)
    return None


def homepage_from_domain(org_name: str) -> str | None:
    """Try reaching the known/guessed domain directly before searching."""
    for domain in get_domain(org_name):
        for scheme in ("https", "http"):
            url = f"{scheme}://{domain}"
            try:
                r = requests.head(url, headers=HEADERS, timeout=REQUEST_TIMEOUT,
                                  allow_redirects=True)
                if r.status_code < 400:
                    return r.url
            except Exception:
                continue
    return None


def scrape_homepage_logo(homepage_url: str, dest_stem: Path) -> tuple[bool, str]:
    """
    Pull homepage and look for:
      1. og:image   2. apple-touch-icon   3. rel=icon
    """
    try:
        r = requests.get(homepage_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        base = "{u.scheme}://{u.netloc}".format(u=urlparse(homepage_url))

        candidates: list[str] = []

        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            candidates.append(og["content"])

        for rel in ("apple-touch-icon", "apple-touch-icon-precomposed"):
            tag = soup.find("link", rel=rel)
            if tag and tag.get("href"):
                candidates.append(urljoin(base, tag["href"]))

        for rel in ("icon", "shortcut icon"):
            tag = soup.find("link", rel=rel)
            if tag and tag.get("href"):
                candidates.append(urljoin(base, tag["href"]))

        for img_url in candidates:
            ext = Path(urlparse(img_url).path).suffix
            safe_ext = ext if ext in {".png", ".jpg", ".jpeg", ".svg", ".ico", ".webp"} else ".png"
            dest = dest_stem.with_suffix(safe_ext)
            if download_image(img_url, dest):
                return True, img_url

    except Exception as e:
        log.debug("  Homepage scrape failed: %s", e)

    return False, ""


# ── Strategy 3: Google Custom Search (optional) ───────────────────────────────

def try_google_image_search(org_name: str, dest_stem: Path) -> tuple[bool, str]:
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return False, ""

    params = {
        "key": GOOGLE_API_KEY,
        "cx":  GOOGLE_CSE_ID,
        "q":   f"{org_name} logo",
        "searchType": "image",
        "num": 3,
        "imgType": "clipart",
        "safe": "active",
    }
    try:
        r = requests.get("https://www.googleapis.com/customsearch/v1",
                         params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        for item in r.json().get("items", []):
            img_url = item.get("link", "")
            ext = Path(urlparse(img_url).path).suffix or ".jpg"
            safe_ext = ext if ext in {".png", ".jpg", ".jpeg", ".svg", ".webp"} else ".jpg"
            dest = dest_stem.with_suffix(safe_ext)
            if download_image(img_url, dest):
                return True, img_url
    except Exception as e:
        log.debug("  Google search failed: %s", e)

    return False, ""


# ── Per-org orchestration ─────────────────────────────────────────────────────

def process_org(org_name: str) -> dict:
    slug = slugify(org_name)
    dest_stem = OUTPUT_DIR / slug

    log.info("▶ %s", org_name)

    # 1. Clearbit (fast, clean, domain-based)
    ok, url = try_clearbit(org_name, dest_stem)
    if ok:
        log.info("  ✓ clearbit → %s", url)
        return _result(org_name, "clearbit", url, "success")
    time.sleep(DELAY_SECONDS)

    # 2a. Try reaching domain directly
    homepage = homepage_from_domain(org_name)
    time.sleep(DELAY_SECONDS)

    # 2b. DuckDuckGo search if direct hit failed
    if not homepage:
        homepage = find_homepage_via_search(org_name)
        time.sleep(DELAY_SECONDS)

    if homepage:
        ok, url = scrape_homepage_logo(homepage, dest_stem)
        if ok:
            log.info("  ✓ homepage → %s", url)
            return _result(org_name, "homepage_scrape", url, "success")
    time.sleep(DELAY_SECONDS)

    # 3. Google Custom Search (optional, requires API key)
    ok, url = try_google_image_search(org_name, dest_stem)
    if ok:
        log.info("  ✓ google   → %s", url)
        return _result(org_name, "google_search", url, "success")

    log.warning("  ✗ no logo found")
    return _result(org_name, "", "", "failed")


def _result(org, source, url, status):
    return {"org_name": org, "source": source, "logo_url": url, "status": status}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    orgs_path = Path(ORGS_FILE)
    if not orgs_path.exists():
        log.error("'%s' not found. Create it with one org name per line.", ORGS_FILE)
        return

    orgs = [l.strip() for l in orgs_path.read_text().splitlines() if l.strip()]
    if not orgs:
        log.error("'%s' is empty.", ORGS_FILE)
        return

    OUTPUT_DIR.mkdir(exist_ok=True)
    log.info("Loaded %d orgs. Logos → %s/", len(orgs), OUTPUT_DIR)

    results = []
    for org in orgs:
        result = process_org(org)
        results.append(result)
        time.sleep(DELAY_SECONDS)

    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["org_name", "source", "logo_url", "status"])
        writer.writeheader()
        writer.writerows(results)

    success = sum(1 for r in results if r["status"] == "success")
    log.info("Done. %d/%d logos found. See %s", success, len(orgs), RESULTS_CSV)


if __name__ == "__main__":
    main()
