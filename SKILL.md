---
name: slack-digest
description: |
  Slack channel ingest + analysis skill. Pulls messages, threads, replies, permalinks,
  and resolved user names from any Slack channel over a time window using a Slack user
  token (xoxp-), emits normalized JSONL, then hands off to a recipe
  (FAQ / daily digest / knowledge-base publish / raw).

  **Invoke this skill whenever the user mentions Slack in a data/analysis/summary context**,
  even with minimal input. Trigger phrases include (non-exhaustive):
    - "슬랙 채널 분석", "슬랙 분석해줘", "슬랙 대화 요약", "슬랙 FAQ", "슬랙 수집",
      "슬랙 대화 긁어줘", "슬랙 채널 정리", "슬랙 스레드 요약", "슬랙에서 뽑아줘"
    - "analyze slack", "slack channel summary", "fetch slack", "pull slack history",
      "ingest slack", "slack to faq", "summarize slack threads"
    - Any Slack archive URL (https://*.slack.com/archives/C...) or channel ID
      (C0XXXXXXXX / G0XXXXXXXX) appearing with an analysis/summary/dump intent
    - Any mention of `xoxp-` token + "channel"

  When invoked with only a vague instruction (e.g. "슬랙 채널 분석해줘"), DO NOT ask a
  long list of questions up-front. Instead, read the "Minimal-context invocation" section
  of SKILL.md and follow its single-consolidated-question protocol.

  This skill owns the **collection + normalization** layer only. Downstream operations
  (FAQ, digest, Confluence/Notion/Pika publish) live as recipes in `recipes/` — read the
  matching recipe file after collection.
---

# slack-digest

**One job: reliably pull Slack conversation data into a normalized local JSONL.** Everything else (summarize, FAQ-ify, publish, diff-against-last-week, ...) is a downstream consumer of that JSONL, implemented as a *recipe* in `recipes/`.

Think of this skill as the Slack **ingest layer**. Recipes are the cookbook that consumes the ingested data.

## When invoked

1. Figure out the user's **end goal** (FAQ? daily recap? publish to Confluence? raw dump?). This determines which recipe you'll execute *after* collection.
2. Run the **collection pipeline** (this file, Steps 1–3 below).
3. Hand off to the matching recipe file in `recipes/`. **Read the recipe before executing it** — each recipe has its own inputs, prompting rules, and output format.

If no clear recipe matches, stop after Step 3 and give the user the path to `threads.jsonl` so they can decide.

## Minimal-context invocation

The user will often invoke this skill with a one-liner like:

- "슬랙 채널 분석해줘"
- "슬랙 대화 요약"
- "analyze slack"

When that happens, **do not** interrogate them with 5 separate questions. Follow this protocol:

### 1. Scan the conversation context first

Before asking anything, reuse whatever context is already available:

- **Channel** — is there a Slack archive URL, `#channelname`, or `C0XXXXXXXX` ID anywhere in the conversation, cwd, clipboard hint, recent files, or the user's prior turns? If yes, use it.
- **Time window** — did the user mention "3개월", "지난주", "last month", "오늘", a date? If yes, use it. Otherwise default to **last 3 months**.
- **Goal / recipe** — did the user say "FAQ", "요약", "정리", "Confluence", "daily", "digest"? Map to a recipe:
  - "FAQ" / "정리" / "문서화" → `recipes/faq.md`
  - "daily" / "weekly" / "오늘 있었던" / "어제" / "recap" → `recipes/daily-digest.md`
  - "Confluence" / "Notion" / "올려줘" / "publish" → `recipes/faq.md` → `recipes/knowledge-base.md`
  - "raw" / "json만" / "데이터만" → stop after collection
  - Nothing specified → default to `recipes/faq.md` (most common use case)
- **Token** — check `.env` and `$SLACK_USER_TOKEN`. If neither exists, that's the first blocker.

### 2. Ask ONE consolidated question for what's still missing

Format (adapt to the user's language):

> 슬랙 채널 분석 시작할게요. 아래만 확인해주세요:
>
> 1. **채널**: (#name / C01XXXXXX / archive URL)
> 2. **기간**: 지난 3개월 (default) — 다르면 알려주세요
> 3. **목적**: FAQ 문서 (default) / daily recap / Confluence 발행 / raw JSONL
>
> 답 주시면 바로 수집 시작합니다. 토큰은 `.env` 에서 자동 로드됩니다.

- Only include lines for values you couldn't infer. If you already know the channel, don't ask again.
- Accept **partial answers** — if the user replies just "#foo", fill defaults for the rest and confirm in one line before running.
- If the user replies with only "ㄱ" / "go" / "진행" and you still have blanks, assume **all defaults** (3 months, FAQ) and proceed — announce the assumptions in one line before running.

### 3. Run

After inputs are resolved (or defaulted), go directly to Step 0 → Step 4 of the main pipeline below. Don't re-confirm again unless something changes.

### Anti-patterns (don't do these)

- ❌ Asking "which workspace?" — user tokens are workspace-scoped, so this is always redundant.
- ❌ Asking "what language should the output be?" — default to the channel's primary language.
- ❌ Asking about category schemes, clustering strategy, embeddings, etc. — those are recipe implementation details, not user decisions.
- ❌ Running `conversations.list` to browse channels — always require an explicit channel from the user.
- ❌ Blocking on missing inputs when sensible defaults exist.

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
