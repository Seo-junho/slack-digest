# Recipe: FAQ

Turn `threads.jsonl` into a structured FAQ document (Markdown by default, optionally published via a knowledge-base recipe).

## Inputs

- `threads.jsonl` produced by the collection pipeline (`fetch_slack.py`)
- `meta.json` — for channel name + time window in the output header
- (Optional) user preferences: language (default = channel's primary language, usually Korean), category scheme, minimum thread length

## Pipeline

### 1. Thread-level summarization

Read `threads.jsonl` in chunks (~20 threads per pass to keep context tight). For each thread, emit one JSON record:

```json
{
  "id": "<parent ts>",
  "permalink": "https://...",
  "question": "짧은 자연어 질문 형태로 재서술",
  "answer": "답변의 핵심 결론 + 맥락 (2~6줄)",
  "tags": ["카테고리", "키워드"],
  "participants": ["name1", "name2"],
  "confidence": "high|medium|low",
  "date": "YYYY-MM-DD"
}
```

**Rules**:
- Skip noise: bot notifications, join/leave, emoji-only "+1" threads, unanswered one-liners
- `confidence = low` when the thread has no clear resolution → keep but down-weight in clustering
- Preserve the original `permalink` — the final doc must link back
- Summarize in the channel's primary language (don't translate unless asked)
- If the thread discusses multiple distinct questions, split into multiple FAQ records with the same `permalink`

Write intermediate output: `./.slack-digest/<out>/summaries.jsonl`

### 2. Cluster & dedupe

Merge near-duplicates:
- group by tag overlap + keyword similarity in `question`
- canonical answer = highest-confidence record in the cluster
- other members go under `related: [permalink, ...]`
- if a tag appears in only 1 cluster, collapse it into the nearest parent category

Write: `./.slack-digest/<out>/faq.json`

Use embeddings only if the user asks — heuristic clustering is usually sufficient for a single channel.

### 3. Render Markdown

Default layout (`./.slack-digest/<out>/FAQ.md`):

```markdown
# <#channel> FAQ (<oldest_iso> ~ <latest_iso>)

> <thread count> threads · generated <YYYY-MM-DD>

## <Category>

### Q. <question>

<answer>

- 원본: [permalink](...)
- 참고: [permalink2](...), [permalink3](...)
- 참여자: name1, name2
- 날짜: YYYY-MM-DD

---
```

Sort categories by cluster size (most-asked first). Within a category, sort by confidence desc → date desc.

## Output

- `./.slack-digest/<out>/summaries.jsonl` — raw thread summaries
- `./.slack-digest/<out>/faq.json` — clustered/canonicalized FAQ entries
- `./.slack-digest/<out>/FAQ.md` — final human-readable document

Report to the user: path to `FAQ.md`, cluster count, category list.

## Handoff

If the user wants to publish the result, chain into `recipes/knowledge-base.md` — do **not** publish from this recipe directly.
