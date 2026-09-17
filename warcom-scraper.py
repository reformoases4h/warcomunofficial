#!/usr/bin/env python3
"""
Unofficial RSS feed generator for Warhammer Community
(https://www.warhammer-community.com/)

The site's article listing page is rendered client-side by JavaScript, so a
plain HTML scrape of it returns no content. Instead, this script:

  1. Fetches the site's sitemap.xml, which is static and lists every
     article URL with a real <lastmod> timestamp.
  2. Filters to article URLs and takes the most recently modified ones.
  3. For any that are new (or edited) since the last run, fetches that
     individual article page directly -- these ARE server-rendered -- and
     pulls its title/description/image from standard meta tags.
  4. Writes an RSS 2.0 feed to feed.xml.

Designed to be run on a schedule (see .github/workflows/generate-feed.yml).
"""

import sys
from datetime import datetime, timezone
from xml.sax.saxutils import escape

import requests
from bs4 import BeautifulSoup

SITEMAP_URL = "https://www.warhammer-community.com/sitemap.xml"
ARTICLE_PREFIX = "https://www.warhammer-community.com/en-gb/articles/"
MAX_ARTICLES = 20
OUTPUT_FILE = "feed.xml"

FEED_TITLE = "Warhammer Community (Unofficial Feed)"
SITE_URL = "https://www.warhammer-community.com/en-gb/all-news-and-features/"
FEED_DESCRIPTION = (
    "Unofficial RSS feed for Warhammer Community, built from the site's "
    "sitemap since the official listing page requires JavaScript."
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


def parse_iso_datetime(raw: str):
    """
    Parse a sitemap <lastmod> value, tolerating the format variants that
    commonly appear (with/without fractional seconds, Z vs numeric offset).
    Returns None if none of the known formats match.
    """
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
    """
    Return a list of {"url": ..., "lastmod": datetime} for every article
    URL in the sitemap that has a parseable lastmod timestamp, newest first.
    """
    try:
        soup = BeautifulSoup(xml_text, "xml")
    except Exception:
        soup = BeautifulSoup(xml_text, "html.parser")

    entries = []
    skipped = []
    for url_tag in soup.find_all("url"):
        loc_tag = url_tag.find("loc")
        lastmod_tag = url_tag.find("lastmod")
        if not loc_tag or not lastmod_tag:
            continue

        loc = loc_tag.get_text(strip=True)
        if not loc.startswith(ARTICLE_PREFIX):
            continue

        lastmod_str = lastmod_tag.get_text(strip=True)
        lastmod = parse_iso_datetime(lastmod_str)
        if lastmod is None:
            skipped.append((loc, lastmod_str))
            continue

        entries.append({"url": loc, "lastmod": lastmod})

    if skipped:
        print(
            f"Note: skipped {len(skipped)} article URL(s) with an "
            f"unrecognised lastmod format. Example: {skipped[0]}",
            file=sys.stderr,
        )

    entries.sort(key=lambda e: e["lastmod"], reverse=True)
    return entries


def load_cache(path: str):
    """
    Read a previously generated feed.xml (if present) and return
    {url: {"lastmod": str, "title":..., "description":..., "image_url":...}}
    so unchanged articles aren't re-fetched every run.
    """
    cache = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return cache

    try:
        soup = BeautifulSoup(content, "xml")
    except Exception:
        soup = BeautifulSoup(content, "html.parser")

    for item in soup.find_all("item"):
        link = item.find("link")
        guid = item.find("guid")
        title = item.find("title")
        desc = item.find("description")
        lastmod = item.find("lastmod")  # custom, non-standard element we add
        image = item.find("enclosure")
        url = link.get_text(strip=True) if link else (
            guid.get_text(strip=True) if guid else None
        )
        if not url:
            continue
        cache[url] = {
            "lastmod": lastmod.get_text(strip=True) if lastmod else None,
            "title": title.get_text() if title else None,
            "description": desc.get_text() if desc else None,
            "image_url": image["url"] if image and image.has_attr("url") else None,
        }
    return cache


def fetch_article_details(url: str):
    """
    Fetch an article page and pull its title, description and featured
    image from standard meta tags (these pages are server-rendered).
    """
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    def meta(prop_or_name, attr="property"):
        tag = soup.find("meta", attrs={attr: prop_or_name})
        return tag["content"].strip() if tag and tag.get("content") else None

    title = meta("og:title") or (soup.title.get_text().strip() if soup.title else url)
    description = meta("og:description") or meta("description", attr="name") or ""
    image_url = meta("og:image")

    return title, description, image_url


def build_rss(articles) -> str:
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")

    items_xml = []
    for art in articles:
        pub_date_str = art["lastmod"].strftime("%a, %d %b %Y %H:%M:%S %z")
        enclosure = ""
        if art.get("image_url"):
            enclosure = f'\n      <enclosure url="{escape(art["image_url"])}" type="image/jpeg"/>'
        items_xml.append(
            f"""    <item>
      <title>{escape(art['title'])}</title>
      <link>{escape(art['url'])}</link>
      <guid isPermaLink="true">{escape(art['url'])}</guid>
      <pubDate>{pub_date_str}</pubDate>
      <lastmod>{escape(art['lastmod'].isoformat())}</lastmod>
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
    entries = parse_sitemap(sitemap_xml)

    if not entries:
        with open("debug_response.html", "w", encoding="utf-8") as f:
            f.write(sitemap_xml)
        print(
            "No article URLs found in the sitemap. The sitemap format may "
            "have changed. See the debug-response artifact.",
            file=sys.stderr,
        )
        sys.exit(1)

    latest = entries[:MAX_ARTICLES]
    cache = load_cache(OUTPUT_FILE)

    articles = []
    for entry in latest:
        url = entry["url"]
        lastmod = entry["lastmod"]
        lastmod_iso = lastmod.isoformat()

        cached = cache.get(url)
        if cached and cached["lastmod"] == lastmod_iso:
            title = cached["title"]
            description = cached["description"]
            image_url = cached["image_url"]
        else:
            title, description, image_url = fetch_article_details(url)

        articles.append(
            {
                "url": url,
                "lastmod": lastmod,
                "title": title,
                "description": description,
                "image_url": image_url,
            }
        )

    rss = build_rss(articles)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(rss)

    print(f"Wrote {len(articles)} articles to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
