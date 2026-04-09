# Recipe: Knowledge-base publishing

Publish an already-rendered Markdown artifact (from `recipes/faq.md`, `recipes/daily-digest.md`, or anywhere else) to an external knowledge system.

This recipe does **not** generate content — it only moves existing Markdown to a destination. Always run a content-producing recipe first.

## Inputs

- Path to a rendered `.md` file (e.g. `./.slack-digest/<out>/FAQ.md`)
- Destination — one of:
  - **Confluence** (via `ctk` skill family)
  - **Notion**
  - **Pika** (via `pika` skill — if available in the current environment)
  - **GitHub Gist / repo commit**
  - **Local only** (no-op; just report the path)
- Destination-specific metadata: space key + parent page (Confluence), database + parent (Notion), repo + path (GitHub), etc.

## Pipeline

### 1. Confirm destination with the user

**Always** re-confirm before publishing. Show:
- Source file path + size + first N lines
- Destination system + space/database/parent
- Whether any redaction is needed (internal names, permalinks to private channels, etc.)

Never publish without an explicit yes.

### 2. Redaction pass (optional)

If the user says "redact" or the destination is more public than the source, do a pass to:
- Replace `@name` mentions with initials or roles
- Strip permalinks (or keep them — confirm)
- Remove direct quotes containing PII

Write redacted version alongside original: `FAQ.redacted.md`.

### 3. Route to the destination skill

| Destination | Action |
|---|---|
| Confluence | Invoke `ctk` skill family (e.g. `ctk:report` or a page-create flow). Pass: title, space key, parent page ID, body = Markdown. Ask the user for any missing destination metadata. |
| Notion | Use the Notion MCP if available, else ask the user to paste an integration token and target DB. |
| Pika | Invoke the `pika` skill with the Markdown path. |
| GitHub | Create a branch + commit + optional PR. Confirm repo/branch first. |
| Local only | Print the absolute path. Done. |

### 4. Report

After publishing, report the destination URL(s) back to the user.

## Safety

- Never include raw `.env`, `threads.jsonl`, or `raw/` in published output.
- For Confluence/Notion: prefer page creation under an explicit parent rather than top-level — easier to audit/revert.
- For GitHub: never force-push, never commit to `main` without confirmation.
