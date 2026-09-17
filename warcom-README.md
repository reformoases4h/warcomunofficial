# Unofficial Warhammer Community RSS Feed

Generates an RSS feed for [Warhammer Community](https://www.warhammer-community.com/)
news articles. The official listing page loads its article grid via JavaScript,
so this doesn't scrape that page directly — instead it reads the site's
`sitemap.xml` (a static, always-current list of every article URL with a real
last-modified timestamp), takes the most recently modified article URLs, and
fetches each one individually to pull its title, summary, and image from
standard meta tags.

Runs automatically once an hour via GitHub Actions and publishes `feed.xml`
through GitHub Pages — same pattern as the Trench Wire feed, if you've set
that one up already.

## Setup (one-time)

1. **Create a new GitHub repository** (e.g. `warcom-unofficial-feed`).
   Public, no need to initialize with a README.

2. **Add these files**, keeping this structure:
   ```
   /
   ├── scraper.py
   ├── requirements.txt
   ├── README.md
   └── .github/
       └── workflows/
           └── generate-feed.yml
   ```
   The workflow file must go inside `.github/workflows/` — when creating it
   through GitHub's web UI, type the full path
   `.github/workflows/generate-feed.yml` into the "Name your file" box and
   GitHub will create the folders for you.

3. **Enable GitHub Pages**: Settings → Pages → Source: **Deploy from a
   branch** → Branch: `main`, folder: `/ (root)` → Save.

4. **Run the workflow once manually**: Actions tab → "Generate Warhammer
   Community RSS Feed" → Run workflow. This creates the first `feed.xml`.

5. **Your feed URL**:
   ```
   https://<your-username>.github.io/<repo-name>/feed.xml
   ```

## How it works

- `sitemap.xml` is fetched fresh every run — it's static, so this is a single
  lightweight request, similar to what a search engine crawler already does.
- Only the 20 most recently modified `/articles/` URLs are considered.
- For each one, the script checks `feed.xml` from the *previous* run: if that
  article's `lastmod` hasn't changed, its cached title/description/image are
  reused rather than re-fetching the page. Only genuinely new or edited
  articles trigger a fresh page fetch — typically 0–3 per hourly run.
- The feed includes title, link, a short preview (from the site's own meta
  description), and a featured image — not the full article body.

## If it breaks

If the run fails, check the Actions tab — a failed run uploads a
**debug-response** artifact containing whatever the script actually
received (either the sitemap or an article page), which tells you whether
the site changed its structure or started blocking the request.
