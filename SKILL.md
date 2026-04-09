---
name: slack-digest
description: Collect Slack channel data (messages + threads + replies + permalinks + resolved user names) over an arbitrary time window using a Slack user token, and expose it as normalized JSONL for downstream use. Use when the user asks to "fetch a Slack channel", "pull slack history", "ingest slack threads", "슬랙 채널 수집/분석/요약", "슬랙 FAQ 정리", "슬랙 대화 긁어줘", or provides a Slack channel name/ID/archive URL with any request that needs its conversation data. This skill owns the **collection + normalization** layer. Downstream operations (FAQ generation, daily digest, knowledge-base publishing via ctk/pika, etc.) are implemented as **recipes** under `recipes/` — read the relevant recipe file after collection to execute the user's actual goal.
---

# slack-digest

**One job: reliably pull Slack conversation data into a normalized local JSONL.** Everything else (summarize, FAQ-ify, publish, diff-against-last-week, ...) is a downstream consumer of that JSONL, implemented as a *recipe* in `recipes/`.

Think of this skill as the Slack **ingest layer**. Recipes are the cookbook that consumes the ingested data.

## When invoked

1. Figure out the user's **end goal** (FAQ? daily recap? publish to Confluence? raw dump?). This determines which recipe you'll execute *after* collection.
2. Run the **collection pipeline** (this file, Steps 1–3 below).
3. Hand off to the matching recipe file in `recipes/`. **Read the recipe before executing it** — each recipe has its own inputs, prompting rules, and output format.

If no clear recipe matches, stop after Step 3 and give the user the path to `threads.jsonl` so they can decide.

---

## Step 0 — Preflight

1. **Token**. The fetch script auto-loads `$SKILL_DIR/.env` (`SLACK_USER_TOKEN=xoxp-...`). If `.env` is absent AND `$SLACK_USER_TOKEN` is not exported, tell the user to create `.env` from `.env.example` and stop.

2. **Sanity check** the token:
   ```bash
   set -a; source "$SKILL_DIR/.env" 2>/dev/null; set +a
   curl -s -H "Authorization: Bearer $SLACK_USER_TOKEN" https://slack.com/api/auth.test
   ```
   If `ok:false`, surface `error` and stop.

3. **Python 3.9+** available. Stdlib only — no pip install.

## Step 1 — Gather required inputs

Ask the user (in one consolidated question) for anything missing:

| Input | Default | Accepted forms |
|---|---|---|
| **channel** | required | `#name`, `C01ABCDE12`, or `https://*.slack.com/archives/C...` |
| **time window** | last 3 months | `--months N`, `--days N`, or `--oldest YYYY-MM-DD [--latest YYYY-MM-DD]` |
| **goal / recipe** | `raw` | `faq`, `daily-digest`, `knowledge-base`, `raw`, or free-form |

Channel must be **explicitly named** — never iterate across the workspace.

## Step 2 — Collect

```bash
python3 "$SKILL_DIR/scripts/fetch_slack.py" \
  --channel "<channel>" \
  --months 3 \
  --out "./.slack-digest/<channel>-<yyyymmdd>"
```

The script produces, under `--out`:

| File | Contents |
|---|---|
| `threads.jsonl` | **The canonical artifact.** One JSON object per thread — parent + cleaned replies, resolved user names, permalink, date, participants. |
| `meta.json` | channel info, time window, counts, generation timestamp |
| `users.json` | user ID → display_name cache (reusable across runs) |
| `raw/` | untouched `conversations.history` / `conversations.replies` responses (for reproducibility + re-normalization) |

The script handles: channel name/URL/ID resolution, cursor pagination, thread drill-down, `users.info` caching, `chat.getPermalink`, Slack markup cleanup (`<@U123>` → `@name`, `<#C123|name>` → `#name`, `<http|label>` → `label`), Tier-3 rate limits (1.2s floor + `Retry-After`), and system-message filtering (`channel_join`, etc.).

## Step 3 — Verify collection

Before handing off to a recipe, sanity-check:

```bash
wc -l ./.slack-digest/<out>/threads.jsonl
head -1 ./.slack-digest/<out>/threads.jsonl | python3 -m json.tool
cat ./.slack-digest/<out>/meta.json
```

Report to the user: **channel, time window, thread count, first/last date**. If counts are suspiciously low (e.g. 0 threads on a busy channel), the token is probably not in the channel or the private-channel scope is missing — surface that before proceeding.

## Step 4 — Recipe handoff

Based on the user's goal, open the corresponding recipe file and follow its instructions:

| Goal | Recipe file |
|---|---|
| FAQ / Q&A document from threads | `recipes/faq.md` |
| Daily / weekly digest | `recipes/daily-digest.md` |
| Publish to Confluence / Notion / Pika | `recipes/knowledge-base.md` |
| Something else | Use `threads.jsonl` directly; consider writing a new recipe afterwards. |

**Always read the recipe file before executing it** — recipes evolve independently of this SKILL.md.

---

## Extending: add a new recipe

When the user asks for a new downstream operation that doesn't fit existing recipes:

1. Create `recipes/<name>.md` with: **Inputs**, **Prompting rules**, **Output format**, **Handoff** (if any).
2. Add a row to the "Recipe handoff" table above.
3. Keep the recipe self-contained — it should be runnable by reading only the recipe file + `threads.jsonl`.

## Safety

- User token acts as the user. **Never** call `conversations.list` to iterate the workspace — only touch the channel the user explicitly named.
- `.env`, `.slack-digest/`, `raw/` are in `.gitignore`. Do not commit collected data or tokens.
- Before publishing to external systems (Confluence, Notion, Slack post-back, ...), confirm the destination with the user. FAQs and digests often contain internal context.

## File map

```
slack-digest/
├── SKILL.md                    ← this file (ingest layer)
├── README.md                   ← human setup guide
├── .env / .env.example         ← SLACK_USER_TOKEN
├── .gitignore
├── scripts/
│   └── fetch_slack.py          ← the collector (stdlib only)
└── recipes/                    ← downstream consumers of threads.jsonl
    ├── faq.md
    ├── daily-digest.md
    └── knowledge-base.md
```

`$SKILL_DIR` = the directory containing this `SKILL.md`.
