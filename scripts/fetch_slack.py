#!/usr/bin/env python3
"""
fetch_slack.py — Collect a Slack channel's messages + threads over a time window
and emit a normalized JSONL suitable for LLM summarization.

Stdlib only. Requires env var SLACK_USER_TOKEN (xoxp-...).

Usage:
    python3 fetch_slack.py --channel <name|id|url> --months 3 --out ./out
    python3 fetch_slack.py --channel C0123ABC --days 30 --out ./out
    python3 fetch_slack.py --channel #foo --oldest 2026-01-01 --latest 2026-04-01 --out ./out

Outputs in --out:
    threads.jsonl   one JSON object per thread (parent + replies, cleaned)
    meta.json       channel info, time window, counts
    raw/            untouched API responses (for reproducibility)
    checkpoint.json resumable cursor state

Rate limits are respected: honors Retry-After on 429, and paces Tier 3 calls.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

SLACK_API = "https://slack.com/api"
TIER3_MIN_INTERVAL = 1.2  # seconds between Tier-3 calls (history/replies)


def log(msg: str) -> None:
    print(f"[slack-digest] {msg}", file=sys.stderr, flush=True)


# ---------- HTTP ----------

def slack_get(method: str, params: dict[str, Any], token: str) -> dict[str, Any]:
    url = f"{SLACK_API}/{method}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    attempt = 0
    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry = int(e.headers.get("Retry-After", "5"))
                log(f"429 on {method}, sleeping {retry}s")
                time.sleep(retry + 1)
                continue
            raise
        if not data.get("ok"):
            err = data.get("error", "unknown")
            if err == "ratelimited":
                time.sleep(5)
                continue
            raise RuntimeError(f"Slack API {method} failed: {err}")
        return data


# ---------- Channel resolution ----------

CHANNEL_URL_RE = re.compile(r"/archives/([A-Z0-9]+)")


def resolve_channel(channel_arg: str, token: str) -> dict[str, Any]:
    # URL form
    m = CHANNEL_URL_RE.search(channel_arg)
    if m:
        cid = m.group(1)
        info = slack_get("conversations.info", {"channel": cid}, token)
        return info["channel"]

    # Already an ID
    if re.fullmatch(r"[CG][A-Z0-9]{8,}", channel_arg):
        info = slack_get("conversations.info", {"channel": channel_arg}, token)
        return info["channel"]

    # Name
    name = channel_arg.lstrip("#")
    cursor = ""
    while True:
        params = {
            "limit": 1000,
            "types": "public_channel,private_channel",
            "exclude_archived": "false",
        }
        if cursor:
            params["cursor"] = cursor
        data = slack_get("conversations.list", params, token)
        for ch in data.get("channels", []):
            if ch.get("name") == name:
                return ch
        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break
    raise SystemExit(f"Channel not found: {channel_arg}")


# ---------- User cache ----------

class UserCache:
    def __init__(self, token: str, path: Path):
        self.token = token
        self.path = path
        self.cache: dict[str, str] = {}
        if path.exists():
            self.cache = json.loads(path.read_text())

    def name(self, user_id: str) -> str:
        if not user_id:
            return ""
        if user_id in self.cache:
            return self.cache[user_id]
        try:
            data = slack_get("users.info", {"user": user_id}, self.token)
            u = data.get("user", {})
            prof = u.get("profile", {}) or {}
            name = prof.get("display_name") or prof.get("real_name") or u.get("name") or user_id
        except Exception:
            name = user_id
        self.cache[user_id] = name
        self._flush()
        return name

    def _flush(self) -> None:
        self.path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2))


# ---------- Text cleanup ----------

MENTION_USER_RE = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]+)?>")
MENTION_CHANNEL_RE = re.compile(r"<#([CG][A-Z0-9]+)(?:\|([^>]+))?>")
URL_RE = re.compile(r"<(https?://[^|>]+)(?:\|([^>]+))?>")
SPECIAL_RE = re.compile(r"<!([^>|]+)(?:\|([^>]+))?>")


def clean_text(text: str, users: UserCache) -> str:
    if not text:
        return ""
    text = MENTION_USER_RE.sub(lambda m: f"@{users.name(m.group(1))}", text)
    text = MENTION_CHANNEL_RE.sub(lambda m: f"#{m.group(2) or m.group(1)}", text)
    text = URL_RE.sub(lambda m: m.group(2) or m.group(1), text)
    text = SPECIAL_RE.sub(lambda m: f"@{m.group(2) or m.group(1)}", text)
    return text


# ---------- Time parsing ----------

def parse_time_window(args: argparse.Namespace) -> tuple[float, float]:
    now = datetime.now(tz=timezone.utc)
    latest = now
    if args.latest:
        latest = datetime.fromisoformat(args.latest).replace(tzinfo=timezone.utc)
    if args.oldest:
        oldest = datetime.fromisoformat(args.oldest).replace(tzinfo=timezone.utc)
    elif args.days:
        oldest = latest - timedelta(days=args.days)
    else:
        months = args.months or 3
        oldest = latest - timedelta(days=30 * months)
    return oldest.timestamp(), latest.timestamp()


# ---------- Fetch history + threads ----------

def iter_history(channel_id: str, oldest: float, latest: float, token: str, raw_dir: Path) -> Iterable[dict]:
    cursor = ""
    page = 0
    last_call = 0.0
    while True:
        elapsed = time.time() - last_call
        if elapsed < TIER3_MIN_INTERVAL:
            time.sleep(TIER3_MIN_INTERVAL - elapsed)
        params = {
            "channel": channel_id,
            "oldest": f"{oldest:.6f}",
            "latest": f"{latest:.6f}",
            "limit": 200,
            "inclusive": "true",
        }
        if cursor:
            params["cursor"] = cursor
        data = slack_get("conversations.history", params, token)
        last_call = time.time()
        page += 1
        (raw_dir / f"history-{page:04d}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )
        for msg in data.get("messages", []):
            yield msg
        if not data.get("has_more"):
            break
        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break


def fetch_replies(channel_id: str, thread_ts: str, token: str, raw_dir: Path) -> list[dict]:
    msgs: list[dict] = []
    cursor = ""
    page = 0
    last_call = 0.0
    while True:
        elapsed = time.time() - last_call
        if elapsed < TIER3_MIN_INTERVAL:
            time.sleep(TIER3_MIN_INTERVAL - elapsed)
        params = {"channel": channel_id, "ts": thread_ts, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = slack_get("conversations.replies", params, token)
        last_call = time.time()
        page += 1
        (raw_dir / f"replies-{thread_ts}-{page:02d}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )
        msgs.extend(data.get("messages", []))
        if not data.get("has_more"):
            break
        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break
    return msgs


def get_permalink(channel_id: str, ts: str, token: str) -> str:
    try:
        data = slack_get(
            "chat.getPermalink", {"channel": channel_id, "message_ts": ts}, token
        )
        return data.get("permalink", "")
    except Exception:
        return ""


# ---------- Normalization ----------

SKIP_SUBTYPES = {
    "channel_join", "channel_leave", "channel_topic", "channel_purpose",
    "channel_name", "bot_add", "bot_remove", "pinned_item", "unpinned_item",
}


def normalize_message(msg: dict, users: UserCache) -> dict:
    return {
        "ts": msg.get("ts"),
        "user": users.name(msg.get("user") or msg.get("bot_id") or ""),
        "text": clean_text(msg.get("text", ""), users),
        "reactions": [
            {"name": r.get("name"), "count": r.get("count")}
            for r in msg.get("reactions", [])
        ],
        "subtype": msg.get("subtype"),
    }


def build_thread_record(
    parent: dict,
    replies: list[dict],
    channel: dict,
    users: UserCache,
    token: str,
) -> dict | None:
    if parent.get("subtype") in SKIP_SUBTYPES:
        return None
    parent_norm = normalize_message(parent, users)
    reply_norms = [
        normalize_message(r, users)
        for r in replies
        if r.get("ts") != parent.get("ts") and r.get("subtype") not in SKIP_SUBTYPES
    ]
    ts = parent.get("ts", "")
    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc) if ts else None
    participants = sorted({parent_norm["user"], *[r["user"] for r in reply_norms]} - {""})
    return {
        "channel": {"id": channel.get("id"), "name": channel.get("name")},
        "thread_ts": ts,
        "date": dt.strftime("%Y-%m-%d") if dt else "",
        "permalink": get_permalink(channel["id"], ts, token),
        "parent": parent_norm,
        "replies": reply_norms,
        "reply_count": len(reply_norms),
        "participants": participants,
    }


# ---------- Main ----------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True, help="name, ID, or archive URL")
    ap.add_argument("--months", type=int, default=None)
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--oldest", help="YYYY-MM-DD (UTC)")
    ap.add_argument("--latest", help="YYYY-MM-DD (UTC)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    # Auto-load .env from the skill root (parent of scripts/) if present.
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    token = os.environ.get("SLACK_USER_TOKEN", "").strip()
    if not token:
        print("ERROR: SLACK_USER_TOKEN env var is not set (checked .env and process env)", file=sys.stderr)
        return 2

    out = Path(args.out).resolve()
    raw_dir = out / "raw"
    out.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(exist_ok=True)

    log(f"resolving channel: {args.channel}")
    channel = resolve_channel(args.channel, token)
    log(f"→ #{channel.get('name')} ({channel.get('id')})")

    oldest, latest = parse_time_window(args)
    log(f"time window: {datetime.fromtimestamp(oldest, tz=timezone.utc).isoformat()} "
        f"→ {datetime.fromtimestamp(latest, tz=timezone.utc).isoformat()}")

    users = UserCache(token, out / "users.json")

    # Collect top-level messages first, then fetch replies per thread.
    top_level: list[dict] = []
    for msg in iter_history(channel["id"], oldest, latest, token, raw_dir):
        # Only keep thread parents and standalone messages.
        thread_ts = msg.get("thread_ts")
        ts = msg.get("ts")
        if thread_ts and thread_ts != ts:
            continue  # reply surfaced in history — we'll pull via replies anyway
        top_level.append(msg)
    log(f"top-level messages in window: {len(top_level)}")

    threads_path = out / "threads.jsonl"
    written = 0
    with threads_path.open("w", encoding="utf-8") as fh:
        for idx, msg in enumerate(top_level, start=1):
            ts = msg.get("ts")
            if msg.get("reply_count", 0) > 0:
                replies = fetch_replies(channel["id"], ts, token, raw_dir)
            else:
                replies = [msg]
            record = build_thread_record(msg, replies, channel, users, token)
            if record is None:
                continue
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
            if idx % 25 == 0:
                log(f"processed {idx}/{len(top_level)} threads")

    meta = {
        "channel": {"id": channel.get("id"), "name": channel.get("name")},
        "oldest": oldest,
        "latest": oldest and latest,
        "oldest_iso": datetime.fromtimestamp(oldest, tz=timezone.utc).isoformat(),
        "latest_iso": datetime.fromtimestamp(latest, tz=timezone.utc).isoformat(),
        "top_level_count": len(top_level),
        "threads_written": written,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    log(f"done. threads written: {written}")
    log(f"output: {threads_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
