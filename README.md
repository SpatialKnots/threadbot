# Thread Search Bot

Telegram bot for searching locally indexed thread images from a VK group.

The MVP pipeline is:

```text
VK API -> SQLite -> OCR-enriched text search -> Telegram image response
```

SQLite FTS5 is the primary search index. Optional semantic ranking is implemented
through `intfloat/multilingual-e5-small` embeddings and a local FAISS index.
PostgreSQL and admin commands are intentionally left for later stages.

## Setup

Create `.env` from `.env.example`:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
VK_ACCESS_TOKEN=your_vk_token
VK_GROUP_DOMAIN=thewebmthread
DATABASE_URL=sqlite:///./threads.db
IMAGE_STORAGE_PATH=./data/images
RESULTS_PER_PAGE=5
```

Install dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Fetch VK Posts

```bash
python scripts/fetch_vk_posts.py --limit 100
```

Use `--update-existing` to refresh counters and metadata for posts already saved in the database.

## Run Bot

```bash
python -m app.main
```

On startup, the bot checks the latest VK wall posts and imports posts that are
not yet present in the local database. If new posts were saved, it rebuilds
`posts.search_text` and the SQLite FTS index before Telegram polling starts.

Startup sync settings:

```env
STARTUP_FETCH_ENABLED=true
STARTUP_FETCH_LIMIT=100
STARTUP_FETCH_BATCH_SIZE=100
STARTUP_REBUILD_SEARCH=true
```

Increase `STARTUP_FETCH_LIMIT` if more than 100 new wall posts can appear
between bot launches. Set `STARTUP_FETCH_ENABLED=false` for maintenance runs
that must not call VK.

Supported commands:

- `/start`
- `/help`
- `/search query`
- `/random`
- `/latest`
- `/check`

Any regular text message is treated as a search query.

`/check` manually checks VK for new wall posts, stores new thread images, runs
OCR for newly imported threads, tries to resolve original 2ch links from OCR
post numbers, and rebuilds local search artifacts when new data was added.

## OCR

OCR uses `pytesseract` and Pillow preprocessing. The Python dependency is listed in
`requirements.txt`, but the Tesseract OCR executable must also be installed separately
and available in `PATH`.

Run a small dry run first:

```bash
python scripts/run_ocr.py --limit 10 --dry-run
```

Write recognized text to `posts.ocr_text`:

```bash
python scripts/run_ocr.py --limit 100
```

Useful options:

- `--post-id ID` processes one local post id.
- `--force` reruns OCR for posts that already have OCR text.
- `--lang rus+eng` controls Tesseract languages.

Current storage is post-level: OCR text from selected images is aggregated into
`posts.ocr_text`. Per-image OCR text is not stored yet.

## Search Index

The bot can use a materialized SQLite FTS5 search index when it exists. The
Telegram search API stays the same and falls back to the in-Python scorer if the
FTS tables have not been built yet.

When `data/faiss/threads.index` and `data/faiss/thread_ids.json` exist, search
also blends FAISS semantic candidates with FTS/Python scores. Semantic search is
enabled by default and can be disabled with:

```env
THREADBOT_SEMANTIC_SEARCH=false
```

At runtime, semantic model loading uses local files only. Build the FAISS index
first so the model and index are available locally before relying on semantic
ranking in the bot.

Do not rebuild the live database while import is running. If `threads.db-journal`
exists, make a copy first or wait until the writer exits.

Build and inspect a copy:

```bash
py -3.12 -c "import sqlite3; src=sqlite3.connect('file:threads.db?mode=ro', uri=True); dst=sqlite3.connect('threads_search_test.db'); src.backup(dst); src.close(); dst.close()"
py -3.12 scripts/rebuild_search_text.py --database-url sqlite:///./threads_search_test.db
py -3.12 scripts/rebuild_fts_index.py --database-url sqlite:///./threads_search_test.db
py -3.12 scripts/test_search.py --database-url sqlite:///./threads_search_test.db
```

After import and OCR finish, run the same rebuild commands against the live
database during a maintenance window.

Build semantic search only after `search_text` is populated:

```bash
py -3.12 scripts/build_faiss_index.py --database-url sqlite:///./threads_search_test.db
```

To rebuild every search artifact on an explicit database:

```bash
py -3.12 scripts/rebuild_all_search.py --database-url sqlite:///./threads_search_test.db --with-faiss
```

## Notes

- Tokens are read from environment variables only.
- Posts are deduplicated by `vk_post_id`.
- Images are stored locally and are not re-downloaded if the stable target file already exists.
- VK API and Telegram API behavior can change; ingestion and bot code are isolated to keep fixes local.
