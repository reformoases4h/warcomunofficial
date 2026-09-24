# Unofficial Warhammer Community RSS Feed

Generates an RSS feed for [Warhammer Community](https://www.warhammer-community.com/)
news articles. The official listing page loads its article grid via JavaScript
(confirmed by fetching it directly — the raw HTML returns no articles), so
this doesn't scrape that page. Instead it reads the site's `sitemap.xml` (a
static, always-present list of every article URL) and tracks, in its own
memory (`seen_urls.json`), the first time it ever notices each URL. That's
necessary because the sitemap's own `<lastmod>` timestamps are sometimes
just wrong — some genuinely new articles carry stale, months-old dates —
so this script doesn't trust them for ordering, only for detecting edits.

Runs automatically once an hour via GitHub Actions and publishes `feed.xml`
through GitHub Pages.

## Setup (one-time)

1. **Create a new, empty GitHub repository** (e.g. `warcom-unofficial-feed`).
   Public, no need to initialize with a README.

2. **Upload these four files exactly as named**, in this structure:
   ```
   /
   ├── scraper.py
   ├── requirements.txt
   ├── README.md
   └── .github/
       └── workflows/
           └── generate-feed.yml
   ```
   The workflow file MUST be at that exact nested path. When creating it
   through GitHub's web UI: tap **Add file → Create new file**, then in the
   "Name your file" box type the full path
   `.github/workflows/generate-feed.yml` — GitHub turns each `/` into a
   folder automatically as you type. Paste the file's contents into the
   editor below that, then commit.

   The other three files (`scraper.py`, `requirements.txt`, `README.md`)
   just go in the repo root — no folder needed for those.

3. **Enable GitHub Pages**: Settings → Pages → Source: **Deploy from a
   branch** → Branch: `main`, folder: `/ (root)` → Save.

4. **Set workflow permissions to read/write** (needed for the workflow to
   commit `feed.xml` back to the repo): Settings → Actions → General →
   scroll to "Workflow permissions" → select **Read and write permissions**
   → Save.

5. **Run the workflow manually**: Actions tab → "Generate Warhammer
   Community RSS Feed" → Run workflow. This creates the first `feed.xml`
   and `seen_urls.json`.

6. **Your feed URL**:
   ```
   https://<your-username>.github.io/<repo-name>/feed.xml
   ```

## How it works, in short

- Every hour, the script fetches `sitemap.xml` and looks at every article
  URL in it.
- Any URL it's never seen before is treated as newly published *right now*
  — this is deliberate, since the site's own timestamps aren't reliable
  enough to trust.
- `seen_urls.json` is the script's memory of every URL it's ever noticed.
  **This file must be committed by the workflow for any of this to work
  across runs** — if it's ever missing from the repo, every run will look
  like a fresh start.
- The feed shows the 20 most recently *noticed* articles, each with a
  title, short preview (from the site's own meta description), and a
  featured image.
- A single article page failing to load (e.g. a malformed sitemap entry)
  is skipped with a warning, not treated as a fatal error for the whole run.

## If something looks wrong

Check the **Actions tab** first:
- A **failed run** will have a `debug-response` artifact attached — download
  it to see exactly what the site returned.
- A **successful run that still looks wrong** (e.g. articles not updating,
  wrong order) — check that `seen_urls.json` exists in the repo and that
  its "last updated" time matches the most recent workflow run. If it's
  missing or stale, open `.github/workflows/generate-feed.yml` and confirm
  the commit step includes both files:
  ```
  git add feed.xml seen_urls.json
  ```
  If it only says `git add feed.xml`, that's the bug — the script's memory
  never gets saved, so it can never make progress between runs.
