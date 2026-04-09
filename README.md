# slack-digest

Personal Claude Code **plugin** — the Slack ingest layer. Reliably pulls a Slack channel's messages, threads, replies, permalinks, and resolved user names into a normalized local JSONL. Everything else (FAQ generation, daily digests, publishing to Confluence / Notion / Pika, ...) is implemented as a **recipe** that consumes that JSONL.

> Once Slack data is in `threads.jsonl`, you can do anything with it.

## Repository layout

```
slack-digest/
├── .claude-plugin/
│   ├── marketplace.json        ← directory-source marketplace
│   └── plugin.json             ← plugin manifest
├── skills/
│   └── slack-digest/           ← the skill itself
│       ├── SKILL.md            ← ingest pipeline + minimal-context invocation
│       ├── .env / .env.example ← SLACK_USER_TOKEN
│       ├── scripts/
│       │   └── fetch_slack.py  ← stdlib collector
│       └── recipes/            ← downstream consumers of threads.jsonl
│           ├── faq.md
│           ├── daily-digest.md
│           └── knowledge-base.md
└── README.md
```

| Layer | Lives in | Responsibility |
|---|---|---|
| **Ingest** | `skills/slack-digest/scripts/fetch_slack.py` + `SKILL.md` | Fetch + normalize Slack → `threads.jsonl` |
| **Recipes** | `skills/slack-digest/recipes/*.md` | Consume `threads.jsonl` for a specific downstream goal |
| **Publishing** | `recipes/knowledge-base.md` → delegates to `ctk` / `pika` / etc. | Push rendered docs to external systems |

Current recipes:

- [`skills/slack-digest/recipes/faq.md`](skills/slack-digest/recipes/faq.md) — FAQ-style Q&A document
- [`skills/slack-digest/recipes/daily-digest.md`](skills/slack-digest/recipes/daily-digest.md) — daily/weekly timeline recap
- [`skills/slack-digest/recipes/knowledge-base.md`](skills/slack-digest/recipes/knowledge-base.md) — publish a rendered Markdown to Confluence / Notion / Pika / GitHub

Adding a new recipe = adding one Markdown file. See the "Extending" section below.

## Install

This repo is a **self-contained Claude Code plugin + directory-source marketplace**. Two ways to install:

### A. As a local directory marketplace (recommended)

```bash
# 1. Clone anywhere
git clone https://github.com/Seo-junho/slack-digest.git /Users/you/slack-digest

# 2. Symlink into ~/.claude/plugins/local/ for convention (optional but tidy)
mkdir -p ~/.claude/plugins/local
ln -sfn /Users/you/slack-digest ~/.claude/plugins/local/slack-digest

# 3. Register the marketplace
# Edit ~/.claude/plugins/known_marketplaces.json and add:
#   "slack-digest-marketplace": {
#     "source": { "source": "directory", "path": "/Users/you/slack-digest" },
#     "installLocation": "/Users/you/slack-digest"
#   }
#
# Edit ~/.claude/plugins/installed_plugins.json and add under "plugins":
#   "slack-digest@slack-digest-marketplace": [{
#     "scope": "user",
#     "installPath": "/Users/you/slack-digest",
#     "version": "0.1.0",
#     "installedAt": "<iso timestamp>",
#     "lastUpdated": "<iso timestamp>"
#   }]
```

Restart Claude Code (new session). The skill will auto-activate on Slack-related prompts.

### B. Via the `/plugin` UI

If your Claude Code build supports it, run `/plugin` inside Claude Code and point it at this directory / URL.

## Slack token setup

1. Create a Slack app → add **User Token Scopes**:
   - `channels:history`
   - `groups:history` (for private channels)
   - `channels:read`
   - `groups:read`
   - `users:read`
2. Install the app to your workspace → copy the **User OAuth Token** (`xoxp-...`).
3. Save the token via `.env` inside the skill directory:
   ```bash
   cp skills/slack-digest/.env.example skills/slack-digest/.env
   # then edit skills/slack-digest/.env and paste your xoxp-... token
   ```

`fetch_slack.py` auto-loads `skills/slack-digest/.env` — no need to `source` it manually. `.env` is in `.gitignore` and will never be committed.

> **Never paste the token in chat, PRs, or screenshots.** If it leaks, rotate it from the Slack app's OAuth & Permissions page (reinstall preserves scopes).

Verify:
```bash
set -a; source skills/slack-digest/.env; set +a
curl -s -H "Authorization: Bearer $SLACK_USER_TOKEN" https://slack.com/api/auth.test
```
Expected: `{"ok":true, ...}`

## Usage (inside Claude Code)

Just tell Claude what you want:

- "슬랙 `#foo` 채널 최근 3개월 대화 가져와서 FAQ로 정리해줘" → ingest + `recipes/faq.md`
- "analyze `C0123ABC` last 30 days, daily recap" → ingest + `recipes/daily-digest.md`
- "https://musinsa.slack.com/archives/C0123ABC 이 채널 3개월치 긁어서 Confluence 에 올려줘" → ingest + `recipes/faq.md` → `recipes/knowledge-base.md` → `ctk`
- "그냥 raw JSONL 만 뽑아줘" → ingest only, stop after Step 3

The `slack-digest` skill will:
1. Preflight check the token
2. Ask for any missing inputs (channel, window, goal)
3. Run `fetch_slack.py`
4. Verify the output
5. Read the matching recipe file and follow it

## Manual invocation (debug / CI)

```bash
python3 skills/slack-digest/scripts/fetch_slack.py \
  --channel "#foo" \
  --months 3 \
  --out ./.slack-digest/foo-$(date +%Y%m%d)
```

Other time-window options:
```bash
--days 7
--oldest 2026-01-01 --latest 2026-04-01
```

Outputs (under `--out`):

| File | What |
|---|---|
| `threads.jsonl` | **The canonical artifact.** One JSON object per thread. |
| `meta.json` | Channel info + time window + counts |
| `users.json` | user ID → display_name cache (reusable across runs) |
| `raw/` | Untouched API responses, for reproducibility |

### Shape of `threads.jsonl` (per line)

```json
{
  "channel": {"id": "C0123ABC", "name": "foo"},
  "thread_ts": "1711500000.123456",
  "date": "2026-03-27",
  "permalink": "https://....slack.com/archives/C0123ABC/p1711500000123456",
  "parent": {
    "ts": "1711500000.123456",
    "user": "juno",
    "text": "cleaned text with @mentions and #channels resolved",
    "reactions": [{"name": "thumbsup", "count": 3}]
  },
  "replies": [
    {"ts": "1711500300.123456", "user": "alice", "text": "...", "reactions": []}
  ],
  "reply_count": 4,
  "participants": ["alice", "bob", "juno"]
}
```

## Extending: add a new recipe

1. Create `recipes/<name>.md` with sections: **Inputs**, **Pipeline**, **Output**, **Handoff** (if any).
2. Add a row to the recipe table in `SKILL.md` (Step 4).
3. Keep it self-contained — Claude should be able to execute it by reading only the recipe file + `threads.jsonl`.

## Safety

- The user token acts as **you**. The fetch script only touches the channel you explicitly name — no workspace-wide iteration.
- `.env`, `.slack-digest/`, `raw/` are gitignored.
- Before publishing to external systems, `recipes/knowledge-base.md` always confirms destination + redaction with the user.

## License

Personal use. Fork freely.
