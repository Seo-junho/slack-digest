# Recipe: Daily / Weekly Digest

Turn `threads.jsonl` into a chronological recap — "what happened in this channel during the window".

Different from `faq.md` in intent:
- **FAQ** is reference-oriented (Q → A, evergreen, deduped)
- **Digest** is timeline-oriented (what happened, who pushed it, what decisions were made)

## Inputs

- `threads.jsonl`, `meta.json`
- (Optional) `--granularity daily|weekly` — default picks `daily` if window ≤ 14 days, else `weekly`

## Pipeline

### 1. Bucket threads by date

Group threads by `date` (daily) or by ISO week (weekly). Each bucket becomes one section.

### 2. Per-bucket summarization

For each bucket, Claude produces:

```markdown
## 2026-04-09 (Tue)

- **Topic** — 1~2 lines summarizing what was discussed or decided.
  - 주요 참여자: name1, name2
  - 링크: [permalink](...)
- **Topic** — ...
```

**Rules**:
- One bullet per distinct topic, not per thread (merge threads that are obvious continuations)
- Lead with **decisions** and **blockers**; background chat goes last or is dropped
- Skip days with only bot notifications
- Keep it scannable — if a day has 20 threads, pick the top 5 by reply_count / participant count / reactions

### 3. Header summary

Add a top section with:
- Window range
- Total threads / participants
- **TL;DR** — 3~5 bullets of the highest-signal items across the entire window

## Output

- `./.slack-digest/<out>/DIGEST.md`

## Handoff

To publish, chain into `recipes/knowledge-base.md`.

If the user wants this regularly (e.g. every Monday morning), suggest setting it up as a cron/trigger at the end — don't silently schedule it.
