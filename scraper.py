#!/usr/bin/env python3
"""
Unofficial RSS feed generator for Warhammer Community
(https://www.warhammer-community.com/)

The site's article listing page is JS-rendered, so it can't be scraped
directly. This script finds articles two ways:

  1. sitemap.xml (static, lists article URLs). Its <lastmod> values are
     unreliable and it can lag the site by days, so it is only used to
     remember which URLs already exist and to spot brand-new ones.
  2. The home page, whose "Latest News / Latest Features" sections ARE
     server-rendered. Links there that we don't hold details for are
     picked up even when the sitemap hasn't listed them yet.

For each new article the article page is fetched for its title,
description, image and its real published date (shown under the
headline). Articles are ordered by that date where it could be read,
otherwise by the time this script first noticed them. Articles whose
published date is more than STALE_DAYS old are left out.

State lives in seen_urls.json, which MUST be committed between runs.
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

# The sitemap can lag behind the site (new articles sometimes don't appear
# in it for days), so the home page is also read. Its "Latest News",
# "Latest Features" and per-game sections are server-rendered, so a plain
# HTTP request sees real article links there.
HOME_URL = "https://www.warhammer-community.com/en-gb/"

# Articles whose real publish date (read from the article page) is older
# than this are left out of the feed, so evergreen pages linked from the
# site menu can never pose as news.
STALE_DAYS = 45

OUTPUT_FILE = "feed.xml"
STATE_FILE = "seen_urls.json"

FEED_TITLE = "Warhammer Community (Unofficial Feed)"
SITE_URL = "https://www.warhammer-community.com/en-gb/all-news-and-features/"
FEED_DESCRIPTION = (
    "Unofficial RSS feed for Warhammer Community, built from the site's "
    "home page and sitemap since the official listing page requires "
    "JavaScript. Dates are the articles' own published dates where they "
    "could be read, otherwise the time this feed first noticed the article."
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

    # 3. The visible date shown under the headline, e.g. "30 Sep 26". Pages
    # open with a very long navigation menu, so a fixed-size search of the
    # start of the page would miss it. Instead, look at the text that
    # follows the <h1>, and only accept a piece of text that is *exactly*
    # a date, so a date mentioned inside the article body can't match.
    h1 = soup.find("h1")
    if h1 is not None:
        for text in h1.find_all_next(string=True, limit=60):
            found = _date_from_exact_text(text)
            if found:
                return found

    # 4. Last resort: first piece of text anywhere on the page that is
    # exactly a date.
    for text in soup.find_all(string=True):
        found = _date_from_exact_text(text)
        if found:
            return found

    return None


_EXACT_DATE_RE = _re.compile(
    r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{2})",
    _re.IGNORECASE,
)


def _date_from_exact_text(text: str):
    """Return a datetime if the whole string is a date like '30 Sep 26'."""
    match = _EXACT_DATE_RE.fullmatch(text.strip())
    if not match:
        return None
    day, mon_abbr, yy = match.groups()
    month = _MONTH_ABBR.get(mon_abbr.lower()[:3])
    if not month:
        return None
    try:
        return datetime(2000 + int(yy), month, int(day), tzinfo=timezone.utc)
    except ValueError:
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


def discover_from_listing(url: str):
    """
    Return the article URLs linked from a listing page (e.g. the home page),
    in page order, without duplicates. Links inside the site menu, header
    or footer are ignored: those point at evergreen pages, not news.
    """
    html = fetch_optional(url)
    if html is None:
        return []

    soup = BeautifulSoup(html, "html.parser")
    found = []
    seen = set()
    for a in soup.find_all("a", href=True):
        if any(parent.name in ("nav", "header", "footer") for parent in a.parents):
            continue

        href = a["href"].split("#")[0].split("?")[0].strip()
        if href.startswith("/"):
            href = "https://www.warhammer-community.com" + href
        if not href.startswith(ARTICLE_PREFIX):
            continue
        if not href.endswith("/"):
            href += "/"

        # Expect exactly <id>/<slug>/ after the prefix.
        parts = href[len(ARTICLE_PREFIX):].strip("/").split("/")
        if len(parts) != 2 or not all(parts):
            continue

        if href not in seen:
            seen.add(href)
            found.append(href)
    return found


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
    new_count = 0

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
                new_count += 1
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

    # Second source of new articles: the home page. The sitemap can lag the
    # site by days, so anything linked from the home page that we don't
    # already hold with details gets picked up here.
    now_dt = datetime.now(timezone.utc)
    for url in discover_from_listing(HOME_URL):
        entry = state.get(url)

        if entry is None:
            # Not in the sitemap-derived state at all: genuinely new to us.
            title, description, image_url, published_date = fetch_article_details(url)
            if title is None:
                continue  # page failed to load; try again next run
            state[url] = {
                "first_seen": now_iso,
                "lastmod": None,
                "title": title,
                "description": description,
                "image_url": image_url,
                "published_date": published_date.isoformat() if published_date else None,
            }
            new_count += 1

        elif not entry.get("title") and not entry.get("listing_checked"):
            # Known only as a placeholder locked in from the sitemap, yet
            # it's linked from the home page. Only bring it into the feed
            # if its own page confirms a recent publish date, so evergreen
            # pages can't sneak in. Checked once, then left alone.
            title, description, image_url, published_date = fetch_article_details(url)
            if title is None:
                continue  # failed to load; try again next run
            entry["listing_checked"] = True
            if published_date is not None and (now_dt - published_date).days <= STALE_DAYS:
                entry.update(
                    {
                        "first_seen": now_iso,
                        "title": title,
                        "description": description,
                        "image_url": image_url,
                        "published_date": published_date.isoformat(),
                    }
                )
                new_count += 1

    save_state(STATE_FILE, state)

    # Build the published feed. Each article's date is its own published
    # date when one was read from its page (reliable: it's what the site
    # shows readers), otherwise the time this script first noticed it. The
    # feed is the newest MAX_ARTICLES by that date, leaving out anything
    # whose real published date is older than STALE_DAYS.
    candidates = []
    for url, data in state.items():
        if not data.get("title"):
            continue
        first_seen_dt = parse_iso_datetime(data["first_seen"]) or now_dt
        published_dt = parse_iso_datetime(data.get("published_date") or "")
        if published_dt is not None and (now_dt - published_dt).days > STALE_DAYS:
            continue
        display_dt = published_dt or first_seen_dt
        candidates.append((display_dt, first_seen_dt, url, data))

    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)

    articles = []
    for display_dt, first_seen_dt, url, data in candidates[:MAX_ARTICLES]:
        articles.append(
            {
                "url": url,
                "first_seen": first_seen_dt,
                "display_date": display_dt,
                "title": data["title"],
                "description": data["description"] or "",
                "image_url": data.get("image_url"),
            }
        )

    rss = build_rss(articles)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(rss)

    print(
        f"Wrote {len(articles)} articles to {OUTPUT_FILE} "
        f"(tracking {len(state)} URLs total; {new_count} new this run)"
    )


if __name__ == "__main__":
    main()
