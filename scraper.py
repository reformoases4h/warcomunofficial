#!/usr/bin/env python3
"""
Unofficial RSS feed generator for Warhammer Community
(https://www.warhammer-community.com/)

The site's listing page is JS-rendered, so this reads sitemap.xml instead
(static, lists every article URL). Important quirk discovered in practice:
the sitemap's <lastmod> values are NOT reliably accurate -- some genuinely
new articles carry stale/batch-set timestamps months old. So instead of
trusting <lastmod> for ordering, this script tracks, in its own persisted
state (seen_urls.json), the first time it ever observed each article URL.
A URL appearing for the first time is treated as newly published *now*,
regardless of what timestamp the sitemap attaches to it.

  1. Fetch sitemap.xml, extract all /en-gb/articles/ URLs (lastmod is
     still recorded, but only used to detect edits, never for ordering).
  2. Load seen_urls.json (previous state). Any URL not in it is new.
  3. For new URLs: fetch the article page for title/description/image,
     record first_seen = now.
  4. For previously-seen URLs whose sitemap lastmod changed: refresh their
     title/description/image (the article was edited) but KEEP their
     original first_seen date, so editing an old article doesn't bump it
     to the top of the feed.
  5. Build the RSS feed from the most recent MAX_ARTICLES by first_seen.

Designed to be run on a schedule (see .github/workflows/generate-feed.yml).
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

import requests
from bs4 import BeautifulSoup

SITEMAP_URL = "https://www.warhammer-community.com/sitemap.xml"
ARTICLE_PREFIX = "https://www.warhammer-community.com/en-gb/articles/"

MAX_ARTICLES = 20          # how many items go in the published feed

OUTPUT_FILE = "feed.xml"
STATE_FILE = "seen_urls.json"

FEED_TITLE = "Warhammer Community (Unofficial Feed)"
SITE_URL = "https://www.warhammer-community.com/en-gb/all-news-and-features/"
FEED_DESCRIPTION = (
    "Unofficial RSS feed for Warhammer Community, built from the site's "
    "sitemap since the official listing page requires JavaScript. Ordered "
    "by when this feed first noticed each article, since the site's own "
    "sitemap timestamps aren't reliable."
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}


def fetch(url: str) -> str:
    """Fatal fetch, used only for the sitemap itself -- without that,
    there's nothing else the script can do."""
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        with open("debug_response.html", "w", encoding="utf-8") as f:
            f.write(resp.text)
        print(
            f"Request to {url} returned HTTP {resp.status_code}. "
            "See the debug-response artifact for what was actually returned.",
            file=sys.stderr,
        )
        sys.exit(1)
    return resp.text


def fetch_optional(url: str):
    """Non-fatal fetch for individual article pages. A single bad/404'ing
    article URL (e.g. a malformed sitemap entry) should not take down the
    whole run -- log a warning and return None so the caller can skip it."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            print(
                f"Warning: {url} returned HTTP {resp.status_code}, skipping.",
                file=sys.stderr,
            )
            return None
        return resp.text
    except requests.RequestException as e:
        print(f"Warning: request to {url} failed ({e}), skipping.", file=sys.stderr)
        return None


def parse_iso_datetime(raw: str):
    """Tolerate the handful of ISO 8601 variants sitemaps commonly use."""
    raw = raw.strip()
    formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def parse_sitemap(xml_text: str):
    """Return {url: lastmod_iso_str_or_None} for every article URL."""
    try:
        soup = BeautifulSoup(xml_text, "xml")
    except Exception:
        soup = BeautifulSoup(xml_text, "html.parser")

    result = {}
    for url_tag in soup.find_all("url"):
        loc_tag = url_tag.find("loc")
        if not loc_tag:
            continue
        loc = loc_tag.get_text(strip=True)
        if not loc.startswith(ARTICLE_PREFIX):
            continue

        lastmod_tag = url_tag.find("lastmod")
        lastmod_iso = None
        if lastmod_tag:
            parsed = parse_iso_datetime(lastmod_tag.get_text(strip=True))
            if parsed:
                lastmod_iso = parsed.isoformat()

        result[loc] = lastmod_iso

    return result


def load_state(path: str) -> dict:
    """
    {url: {"first_seen": iso_str, "lastmod": iso_str_or_None,
           "title":..., "description":..., "image_url":...}}
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: str, state: dict):
    # No cap here: this needs to remember every article URL the site has
    # ever listed, or evicted URLs falsely reappear as "new" the next time
    # they're seen. A plain JSON file, even at several MB for a site with
    # years of history, is trivial for a git repo to hold -- trimming it
    # by count caused more problems than it solved (see the state-capping
    # bug notes/history for this project).
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


import re as _re

_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
# Matches the page's visible date format, e.g. "30 Sep 26"
_VISIBLE_DATE_RE = _re.compile(
    r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{2})\b",
    _re.IGNORECASE,
)


def extract_published_date(soup: BeautifulSoup):
    """
    Try to find the article's real publish date, shown visibly on the page
    (e.g. "30 Sep 26") but not present in any meta tag. Tries several
    approaches, most reliable first, and returns None if none work --
    callers should fall back to first-seen tracking in that case.
    """
    # 1. A <time> element with a machine-readable datetime attribute.
    time_tag = soup.find("time")
    if time_tag and time_tag.get("datetime"):
        dt = parse_iso_datetime(time_tag["datetime"])
        if dt:
            return dt

    # 2. Any embedded JSON blob (common in React/Next.js SSR pages) that
    # might carry a real publish timestamp under a recognisable key name.
    for script in soup.find_all("script", attrs={"type": "application/json"}):
        text = script.string or script.get_text() or ""
        if not text.strip():
            continue
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        found = _search_json_for_date(data)
        if found:
            return found

    # 3. The visible date text near the top of the article, e.g. "30 Sep 26".
    # Search within the first ~2000 characters of visible text so we don't
    # accidentally match a date mentioned somewhere in the article body.
    visible_text = soup.get_text(" ", strip=True)[:2000]
    match = _VISIBLE_DATE_RE.search(visible_text)
    if match:
        day, mon_abbr, yy = match.groups()
        month = _MONTH_ABBR.get(mon_abbr.lower()[:3])
        if month:
            try:
                year = 2000 + int(yy)
                return datetime(year, month, int(day), tzinfo=timezone.utc)
            except ValueError:
                pass

    return None


def _search_json_for_date(obj, depth=0):
    """Recursively search a parsed JSON structure for a plausible publish
    date under a commonly-used key name."""
    if depth > 6:
        return None
    date_keys = {
        "publishedat", "publishdate", "publisheddate", "datepublished",
        "publishedon", "createdat", "date",
    }
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str) and key.lower() in date_keys:
                dt = parse_iso_datetime(value)
                if dt:
                    return dt
        for value in obj.values():
            found = _search_json_for_date(value, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj[:50]:  # bound the search, avoid huge arrays
            found = _search_json_for_date(item, depth + 1)
            if found:
                return found
    return None


def fetch_article_details(url: str):
    html = fetch_optional(url)
    if html is None:
        return None, None, None, None

    soup = BeautifulSoup(html, "html.parser")

    def meta(prop_or_name, attr="property"):
        tag = soup.find("meta", attrs={attr: prop_or_name})
        return tag["content"].strip() if tag and tag.get("content") else None

    title = meta("og:title") or (soup.title.get_text().strip() if soup.title else url)
    description = meta("og:description") or meta("description", attr="name") or ""
    image_url = meta("og:image")
    published_date = extract_published_date(soup)

    return title, description, image_url, published_date


def build_rss(articles) -> str:
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")

    items_xml = []
    for art in articles:
        pub_date_str = art["display_date"].strftime("%a, %d %b %Y %H:%M:%S %z")
        enclosure = ""
        if art.get("image_url"):
            enclosure = f'\n      <enclosure url="{escape(art["image_url"])}" type="image/jpeg"/>'
        items_xml.append(
            f"""    <item>
      <title>{escape(art['title'])}</title>
      <link>{escape(art['url'])}</link>
      <guid isPermaLink="true">{escape(art['url'])}</guid>
      <pubDate>{pub_date_str}</pubDate>
      <description>{escape(art['description'])}</description>{enclosure}
    </item>"""
        )

    items_block = "\n".join(items_xml)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{escape(FEED_TITLE)}</title>
    <link>{escape(SITE_URL)}</link>
    <description>{escape(FEED_DESCRIPTION)}</description>
    <language>en-gb</language>
    <lastBuildDate>{now}</lastBuildDate>
{items_block}
  </channel>
</rss>
"""


def main():
    sitemap_xml = fetch(SITEMAP_URL)
    sitemap = parse_sitemap(sitemap_xml)

    if not sitemap:
        with open("debug_response.html", "w", encoding="utf-8") as f:
            f.write(sitemap_xml)
        print(
            "No article URLs found in the sitemap. Format may have "
            "changed. See the debug-response artifact.",
            file=sys.stderr,
        )
        sys.exit(1)

    state = load_state(STATE_FILE)
    now_iso = datetime.now(timezone.utc).isoformat()
    is_cold_start = len(state) == 0

    if is_cold_start:
        # No history yet. This site's sitemap <lastmod> values are not
        # reliably ordered even at the top -- we've directly observed cases
        # where a September 20th article outranks content from the 24th to
        # 27th. That means ANY attempt to guess an initial "best 20" batch
        # from these dates can surface stale content instead of recent
        # content, with no way to tell which is which from the data alone.
        #
        # So: don't guess. Lock in every current sitemap URL as already
        # known, and display NOTHING yet. The feed starts empty (or near
        # empty) and fills up over the following run(s) purely from
        # genuine detections -- a URL that didn't exist in the sitemap
        # before now existing is the one signal on this site that's
        # actually reliable, since it's based on our own observation, not
        # the site's self-reported (and sometimes wrong) timestamps.
        for url, lastmod_iso in sitemap.items():
            state[url] = {
                "first_seen": lastmod_iso or now_iso,
                "lastmod": lastmod_iso,
                "title": None,
                "description": None,
                "image_url": None,
                "published_date": None,
            }
        print(
            "Cold start: recorded all current sitemap URLs as known, "
            "nothing displayed yet. The feed will fill in as genuinely "
            "new articles are published and detected on future runs.",
            file=sys.stderr,
        )
    else:
        for url, lastmod in sitemap.items():
            if url not in state:
                # Genuinely new: first time we've ever seen this URL.
                title, description, image_url, published_date = fetch_article_details(url)
                state[url] = {
                    "first_seen": now_iso,
                    "lastmod": lastmod,
                    "title": title,
                    "description": description,
                    "image_url": image_url,
                    "published_date": published_date.isoformat() if published_date else None,
                }
            elif lastmod and lastmod != state[url].get("lastmod"):
                # Edited: refresh details, keep original first_seen. If the
                # refetch fails, keep the previously-cached good data rather
                # than clobbering it with None -- we'll just retry the
                # refresh again next run since lastmod still won't match.
                title, description, image_url, published_date = fetch_article_details(url)
                if title is not None:
                    state[url]["lastmod"] = lastmod
                    state[url]["title"] = title
                    state[url]["description"] = description
                    state[url]["image_url"] = image_url
                    state[url]["published_date"] = (
                        published_date.isoformat() if published_date else state[url].get("published_date")
                    )

    save_state(STATE_FILE, state)

    # Build the published feed: most recent MAX_ARTICLES by first_seen
    # (ordering stays based on our own detection time -- see the module
    # docstring for why the site's dates aren't trusted for THAT), but the
    # displayed pubDate prefers the article's real published_date when we
    # managed to extract one, falling back to first_seen otherwise.
    dated_items = [
        (url, data) for url, data in state.items() if data.get("title")
    ]
    dated_items.sort(key=lambda kv: kv[1]["first_seen"], reverse=True)

    articles = []
    for url, data in dated_items[:MAX_ARTICLES]:
        first_seen_dt = parse_iso_datetime(data["first_seen"]) or datetime.now(timezone.utc)
        published_dt = parse_iso_datetime(data.get("published_date") or "") or first_seen_dt
        articles.append(
            {
                "url": url,
                "first_seen": first_seen_dt,
                "display_date": published_dt,
                "title": data["title"],
                "description": data["description"] or "",
                "image_url": data.get("image_url"),
            }
        )

    rss = build_rss(articles)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(rss)

    print(f"Wrote {len(articles)} articles to {OUTPUT_FILE} (tracking {len(state)} URLs total)")


if __name__ == "__main__":
    main()
