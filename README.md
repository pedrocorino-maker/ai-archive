# ai-archive

Capture, organize, and sync your ChatGPT and Gemini conversation history — locally, without API keys.

---

## Overview

`ai-archive` is a local-first tool that uses a headed (visible) browser to scrape your ChatGPT and Gemini conversations, normalize them into clean JSON and Markdown, cluster them into topic groups, generate canonical summary documents per topic, and optionally mirror everything to Google Drive.

**Key principles:**

- **No hardcoded credentials.** Login is always manual. The tool never types your password for you.
- **Headed browser only.** Playwright runs a real, visible Chrome window. This is intentional — it lets you handle 2FA, passkeys, and CAPTCHAs yourself.
- **Local-first.** Everything is stored in `data/` on your machine. Google Drive sync is opt-in.
- **No provider API required.** The tool scrapes the web UI, so it works regardless of whether you have API access.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          CLI (ai-archive)                       │
│  doctor · auth · crawl · normalize · cluster · curate · sync   │
└────────────────────────────┬────────────────────────────────────┘
                             │
              ┌──────────────▼──────────────┐
              │       Browser Auth Layer     │
              │  attach_cdp | managed_profile│
              │  BrowserSession (Playwright) │
              └──────────┬──────────────────┘
                         │
          ┌──────────────┴──────────────┐
          │                             │
   ┌──────▼───────┐             ┌───────▼──────┐
   │   ChatGPT    │             │    Gemini     │
   │   Adapter    │             │   Adapter     │
   │ (enumerate + │             │ (enumerate +  │
   │  extract)    │             │  extract)     │
   └──────┬───────┘             └───────┬───────┘
          └──────────────┬──────────────┘
                         │
              ┌──────────▼──────────────┐
              │       Pipeline           │
              │  1. Crawl                │
              │  2. Normalize            │
              │  3. Dedupe (hash-based)  │
              │  4. Cluster (HDBSCAN +   │
              │     sentence-embeddings) │
              │  5. Curate (topic docs)  │
              └──────────┬──────────────┘
                         │
          ┌──────────────┴──────────────┐
          │                             │
   ┌──────▼───────┐             ┌───────▼──────┐
   │  Local Store │             │ Google Drive  │
   │  data/raw/   │             │ (optional     │
   │  normalized/ │◄────sync────│  mirror)      │
   │  curated/    │             └───────────────┘
   │  state/      │
   └──────────────┘
```

**Data flow:** The browser attaches to (or launches) a Chrome instance where you are already logged in. Provider adapters enumerate the conversation sidebar, navigate to each conversation, extract messages and code blocks from the DOM, and save raw HTML to `data/raw/`. The normalize step converts raw HTML to clean JSON + Markdown in `data/normalized/`. The cluster step generates sentence embeddings and groups conversations into topic clusters using HDBSCAN. The curate step produces a canonical Markdown document per topic cluster in `data/curated/`. Drive sync uploads raw and curated artifacts to the configured Google Drive folders.

---

## Installation

**Requirements:** Python 3.12+, [uv](https://github.com/astral-sh/uv), Google Chrome.

```bash
# 1. Clone the repository
git clone <repo-url>
cd ai-archive

# 2. Install dependencies
uv sync

# 3. Install Playwright's Chromium browser
uv run playwright install chromium

# 4. Configure environment
cp .env.example .env
# Edit .env — see Configuration section below
```

Verify the environment is ready:

```bash
uv run ai-archive doctor
```

---

## Configuration

Settings are loaded in priority order: `.env` overrides `config/settings.yaml` (or `config/settings.example.yaml` as a reference), which overrides built-in defaults.

Copy `config/settings.example.yaml` to `config/settings.yaml` for YAML-based configuration, or use the `.env` variables below.

### `.env` Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `APP_ENV` | No | `local` | Environment tag (`local`, `prod`, etc.) |
| `AUTH_MODE` | No | `attach_cdp` | Browser auth strategy: `attach_cdp` or `managed_profile` |
| `CHROME_CDP_URL` | `attach_cdp` only | `http://127.0.0.1:9222` | URL of the Chrome remote debugging endpoint |
| `CHROME_USER_DATA_DIR` | `managed_profile` only | `./data/state/chrome_profile` | Path to the Chrome user data directory used by Playwright |
| `CHROME_CHANNEL` | No | `chrome` | Chrome channel for `managed_profile` mode (`chrome`, `chromium`) |
| `STORAGE_STATE_PATH` | No | `./data/state/storage_state.json` | Path where Playwright saves browser cookies/localStorage after login |
| `GOOGLE_DRIVE_CREDENTIALS_JSON` | Drive only | `./credentials.json` | Path to GCP OAuth Desktop App credentials file |
| `GOOGLE_DRIVE_TOKEN_JSON` | Drive only | `./token.json` | Path where the Drive OAuth token is stored after first auth |
| `GOOGLE_DRIVE_RAW_FOLDER_ID` | Drive only | _(empty)_ | Google Drive folder ID for raw conversation uploads |
| `GOOGLE_DRIVE_CURATED_FOLDER_ID` | Drive only | _(empty)_ | Google Drive folder ID for curated topic document uploads |
| `OPTIONAL_CHATGPT_EMAIL` | No | _(empty)_ | Not used by the tool — stored for your reference only |
| `OPTIONAL_GOOGLE_EMAIL` | No | _(empty)_ | Not used by the tool — stored for your reference only |
| `OPTIONAL_CHATGPT_PASSWORD` | No | _(empty)_ | Not used by the tool — stored for your reference only |
| `OPTIONAL_GOOGLE_PASSWORD` | No | _(empty)_ | Not used by the tool — stored for your reference only |
| `EMBEDDING_MODEL` | No | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | HuggingFace model for semantic embeddings |
| `TOPIC_SIMILARITY_THRESHOLD` | No | `0.82` | Cosine similarity threshold for topic cluster assignment |
| `CURATION_LLM_PROVIDER` | No | `none` | LLM backend for topic naming/summaries: `none`, `anthropic`, `openai`, `ollama` |
| `CURATION_LLM_MODEL` | No | _(empty)_ | Model name for the LLM backend (e.g. `claude-3-5-haiku-20241022`) |
| `CURATION_LLM_API_KEY` | LLM only | _(empty)_ | API key for the chosen LLM provider |
| `MAX_CONVERSATIONS_PER_RUN` | No | _(unlimited)_ | Cap the number of conversations crawled per provider per run |
| `INTERACTIVE` | No | `true` | If `true`, the tool prompts you in the terminal during manual login |
| `SLOW_MO_MS` | No | `150` | Playwright slow-motion delay in milliseconds between actions |
| `JITTER_MIN_MS` | No | `600` | Minimum random delay between conversation requests (ms) |
| `JITTER_MAX_MS` | No | `1400` | Maximum random delay between conversation requests (ms) |

For the full YAML schema (clustering algorithm, logging, archival settings, etc.) see [`config/settings.example.yaml`](config/settings.example.yaml).

---

## Google Drive Setup

Drive sync is entirely optional. Skip this section if you want local-only storage.

1. **Create a GCP project** at [console.cloud.google.com](https://console.cloud.google.com). Pick any project name.

2. **Enable the Google Drive API.**
   In the project, go to *APIs & Services → Library*, search for "Google Drive API", and click *Enable*.

3. **Create OAuth 2.0 credentials.**
   Go to *APIs & Services → Credentials → Create Credentials → OAuth client ID*.
   Choose **Desktop app** as the application type. Download the resulting JSON file.

4. **Place the credentials file.**
   Save the downloaded file as `credentials.json` in the project root (or update `GOOGLE_DRIVE_CREDENTIALS_JSON` in `.env` to point to it).

5. **Create two Drive folders** (one for raw files, one for curated docs), and copy their folder IDs (the long string at the end of the folder URL) into `.env`:
   ```
   GOOGLE_DRIVE_RAW_FOLDER_ID=<folder-id>
   GOOGLE_DRIVE_CURATED_FOLDER_ID=<folder-id>
   ```

6. **Enable Drive sync** in `.env` or `config/settings.yaml`:
   ```
   # .env
   DRIVE_ENABLED=true
   ```
   Or in YAML: `drive.enabled: true`.

7. **Authenticate:**
   ```bash
   uv run ai-archive auth drive
   ```
   This opens a browser window for the OAuth consent flow and saves a `token.json` file. Subsequent runs reuse the token automatically (it is refreshed when expired).

---

## Auth Mode: `attach_cdp`

This is the default and recommended mode. Playwright attaches to a Chrome instance you launch manually, reusing your existing logged-in session. You do not need to log in again inside the tool.

### Step 1 — Launch Chrome with remote debugging

**Windows:**
```bat
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%TEMP%\chrome-debug"
```

**macOS:**
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug
```

A fresh Chrome window opens. Log in to ChatGPT and/or Gemini manually in that window.

### Step 2 — Save browser state

```bash
uv run ai-archive auth browser
```

This connects to the running Chrome instance, detects your logged-in session, and saves the browser's storage state (cookies, localStorage) to `data/state/storage_state.json`.

### Step 3 — Run a crawl

```bash
uv run ai-archive crawl
```

Keep the Chrome window open while crawling. The tool will attach to the existing tabs.

---

## Auth Mode: `managed_profile`

In this mode Playwright launches and manages its own Chrome profile directory. Use this if you cannot run Chrome with `--remote-debugging-port` (e.g., corporate machines, certain Linux setups).

### Step 1 — Configure the mode

```
AUTH_MODE=managed_profile
CHROME_USER_DATA_DIR=./data/state/chrome_profile
```

### Step 2 — Perform the initial login

```bash
uv run ai-archive auth browser
```

Playwright opens a headed Chrome window pointing at ChatGPT (and Gemini if enabled). Log in manually in that window. When you are done with each provider, the tool detects the authenticated state and moves on. Storage state is saved automatically at the end.

### Step 3 — Run a crawl

```bash
uv run ai-archive crawl
```

Playwright relaunches Chrome using the saved profile. If the session is still valid, no login is required.

---

## First Manual Login — What to Expect

When you run `ai-archive auth browser` (in either mode), the following happens:

1. A Chrome window opens and navigates to the provider's URL (e.g., `https://chatgpt.com`).
2. The terminal displays a prompt such as:
   ```
   [yellow]Opening chatgpt...[/yellow]
   Please log in to chatgpt in the browser window, then press Enter here...
   ```
3. Complete the login in the browser — including any 2FA, passkey, or CAPTCHA challenges — entirely yourself.
4. Press **Enter** in the terminal once you are fully logged in and can see the conversation list.
5. The tool saves your session state and moves to the next provider (if configured).
6. On completion: `[green]Authenticated to chatgpt![/green]`

The tool waits up to `LOGIN_TIMEOUT_SECONDS` (default: 300 seconds) for you to complete login.

---

## Running Incremental Sync

After the initial setup and login, run the full pipeline with a single command:

```bash
uv run ai-archive sync all
```

This executes five steps in sequence:

| Step | Command equivalent | What it does |
|---|---|---|
| 1/5 Crawl | `crawl` | Attaches to browser, enumerates conversations, extracts new/changed ones, saves raw HTML |
| 2/5 Normalize | `normalize` | Converts raw HTML to structured JSON + Markdown in `data/normalized/` |
| 3/5 Cluster | `cluster` | Embeds all conversations and groups them into topic clusters using HDBSCAN |
| 4/5 Curate | `curate` | Generates a canonical Markdown document per topic cluster in `data/curated/` |
| 5/5 Drive Sync | `drive sync` | Uploads changed files to configured Google Drive folders (skipped if drive not enabled) |

The crawl step is **incremental by default**: each conversation is hashed after extraction and compared to the stored hash. Unchanged conversations are skipped after a brief jitter delay. To force re-extraction of all conversations:

```bash
uv run ai-archive crawl --full
```

To limit scope:

```bash
uv run ai-archive sync all --provider chatgpt --limit 50
```

---

## CLI Reference

| Command | Description | Key flags |
|---|---|---|
| `doctor` | Run environment health checks (Python version, Playwright, dirs, DB, CDP, Drive creds) | — |
| `auth browser` | Launch browser for manual login, save storage state | — |
| `auth drive` | Run Google Drive OAuth flow, save `token.json` | — |
| `crawl` | Crawl conversations from ChatGPT and/or Gemini | `--provider chatgpt\|gemini\|all`, `--limit N`, `--full` |
| `normalize` | Convert raw HTML in DB to clean JSON + Markdown | `--provider chatgpt\|gemini\|all` |
| `cluster` | Run HDBSCAN topic clustering on all conversations | — |
| `curate` | Generate canonical topic documents from clusters | — |
| `drive sync` | Upload local artifacts to Google Drive | — |
| `sync all` | Run full pipeline: crawl → normalize → cluster → curate → drive sync | `--provider chatgpt\|gemini\|all`, `--limit N` |
| `reindex` | Re-run normalize + cluster + curate without re-crawling | `--full` |
| `report` | Print a table showing conversation counts, topic clusters, and canonical docs | — |

All commands accept no positional arguments. Use `--help` on any command for full usage.

---

## Troubleshooting

**CDP not available (`WARN: CDP available — Not reachable`)**

Chrome is not running with remote debugging enabled, or is using a different port. Verify Chrome was launched with `--remote-debugging-port=9222` and that no firewall blocks `127.0.0.1:9222`. Test manually: `curl http://127.0.0.1:9222/json/version`.

**Challenge detected (CAPTCHA / suspicious login warning)**

The provider displayed an interstitial challenge. The crawl will stop and report: `Challenge detected on chatgpt: ...`. Navigate to the site in your Chrome window, solve the challenge, then re-run `ai-archive crawl`.

**Drive auth expired**

The Drive token in `token.json` has expired and cannot be refreshed. Delete `token.json` and re-run `ai-archive auth drive` to obtain a new token.

**Selector failures (conversations not appearing in the sidebar)**

ChatGPT or Gemini may have updated their UI. The selectors are defined in `config/selectors.chatgpt.yaml` and `config/selectors.gemini.yaml`. Update the CSS selectors in those files to match the current DOM structure. Enable `screenshot_on_error: true` (default) and check `data/logs/` for diagnostic screenshots and HTML snapshots.

**Conversation content not loading (empty messages)**

The conversation may not have fully rendered before extraction. Increase `scroll_attempts` and `scroll_wait_ms` in `config/settings.yaml`, or lower `slow_mo_ms` to give the page more time to load between actions.

**`LoginRequiredError` during crawl**

The saved session has expired. Run `ai-archive auth browser` again to re-authenticate and refresh the storage state.

---

## Known Limitations

- **Gemini conversation history:** Gemini's web UI does not reliably expose the full conversation list through the sidebar. Older conversations may not appear. There is no official export or API alternative.
- **ChatGPT archived conversations:** Conversations moved to the Archive section may require manual navigation to the archive view before crawling. Set `chatgpt_include_archived: true` in settings (default) and ensure the archive sidebar is visible before starting a crawl.
- **Auth sessions expire:** Cookies and localStorage tokens are short-lived. Expect to re-run `auth browser` every few days or after a provider forces re-authentication.
- **Rate limiting:** Neither provider exposes rate limit headers for web UI scraping. The tool uses randomized jitter delays, but aggressive crawls (large `limit`, no jitter) may result in temporary blocks or CAPTCHAs.
- **UI changes break selectors:** The tool depends on DOM selectors that can break without warning when providers update their frontends. Selector files must be maintained manually.
- **No branch/edit history:** If you edited or regenerated an AI response, only the currently visible version of the conversation is captured.
- **Single account per provider:** The tool is designed for one logged-in account per provider per run. Multiple accounts require separate runs with separate profiles.
- **Windows path handling:** `CHROME_USER_DATA_DIR` paths with spaces may need quoting in `.env`.

---

## Optional LLM Backend

By default, topic names and curated document summaries are generated using keyword extraction (KeyBERT + YAKE). You can improve quality by connecting an LLM.

Set in `.env` or `config/settings.yaml`:

```
CURATION_LLM_PROVIDER=anthropic   # anthropic | openai | ollama | none
CURATION_LLM_MODEL=claude-3-5-haiku-20241022
CURATION_LLM_API_KEY=sk-ant-...
```

Or in YAML:
```yaml
curation:
  llm_provider: anthropic
  llm_model: claude-3-5-haiku-20241022
  llm_api_key: sk-ant-...
```

**What the LLM improves:**
- Topic cluster names become more descriptive and coherent.
- Canonical topic documents include narrative summaries rather than raw keyword lists.
- Cross-provider topic merging is more accurate.

**Graceful fallback:** If `llm_provider` is `none` or the API call fails, the pipeline continues using keyword-based extraction. No LLM is required for the tool to function.

**Ollama (local):** Set `CURATION_LLM_PROVIDER=ollama` and `CURATION_LLM_MODEL=<model-name>` (e.g., `llama3.2`). Ensure Ollama is running at `http://localhost:11434` before crawling.

---

## Security & Privacy

**What this tool does not do:**

- It does not bypass 2FA, passkeys, CAPTCHAs, or Cloudflare challenges. All authentication is performed by you, manually, in a visible browser window.
- It does not store or transmit your credentials. The `OPTIONAL_*` email/password fields in `.env` exist for your reference only and are never read by the application code.
- It does not contact any server other than the provider sites and (optionally) Google Drive and your LLM backend.

**What is gitignored by default:**

The following sensitive files are excluded from version control by `.gitignore`:

```
.env
credentials.json
token.json
data/state/storage_state.json
data/state/chrome_profile/
data/logs/
data/raw/
data/normalized/
data/curated/
*.db
*.sqlite
```

**`storage_state.json` is sensitive.** It contains your browser cookies and localStorage tokens for ChatGPT and Gemini. Anyone with this file can access your accounts without a password. Keep it local, never commit it, and treat it like a password file.

**`credentials.json` and `token.json`** grant access to your Google Drive. Apply the same precautions.

---

## Data Layout

```
data/
├── state/
│   ├── archive.db                  # SQLite database (conversations, topics, crawl runs)
│   ├── storage_state.json          # Playwright browser session (sensitive — gitignored)
│   └── chrome_profile/             # Managed Chrome profile (managed_profile mode)
│
├── raw/
│   ├── chatgpt/
│   │   └── YYYY/MM/
│   │       └── <provider_id>.html  # Raw HTML snapshot per conversation
│   └── gemini/
│       └── YYYY/MM/
│           └── <provider_id>.html
│
├── normalized/
│   ├── chatgpt/
│   │   └── YYYY/MM/
│   │       ├── <provider_id>.json  # Structured conversation JSON
│   │       └── <provider_id>.md   # Conversation as Markdown
│   └── gemini/
│       └── YYYY/MM/
│           ├── <provider_id>.json
│           └── <provider_id>.md
│
├── curated/
│   └── <topic-slug>/
│       ├── index.md               # Canonical topic document (Markdown)
│       └── manifest.json          # Topic metadata (conversation refs, tags, providers)
│
└── logs/
    ├── <run_id>_<provider>_error.png   # Screenshot on extraction failure
    └── <run_id>_<provider>_error.html  # DOM snapshot on extraction failure
```

`archive.db` is the source of truth for conversation state, topic clusters, crawl run history, and Drive sync entries. The `data/raw/`, `data/normalized/`, and `data/curated/` directories mirror the database content as files and are the artifacts uploaded to Google Drive.
