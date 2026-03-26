#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

MSK = timezone(timedelta(hours=3))


def first_sentences(text: str, max_chars: int = 200, max_sentences: int = 3) -> str:
    if not text:
        return ""
    s = re.sub(r"\s+", " ", text.strip())
    parts = re.split(r"(?<=[.!?])\s+", s)
    chosen: List[str] = []
    for p in parts:
        if p:
            chosen.append(p)
        if len(chosen) >= max_sentences:
            break
    out = " ".join(chosen)
    return out[:max_chars].strip()


def parse_timestamp(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # Поддержка ISO с Z
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.astimezone(MSK)
    except Exception:
        return None


def detect_conv_id(d: Dict) -> Optional[str]:
    for key in ("conversationId", "chatId", "sessionId", "conversation_id", "chat_id", "session_id"):
        if key in d and d.get(key):
            return str(d.get(key))
    meta = d.get("metadata") or d.get("meta") or {}
    if isinstance(meta, dict):
        for key in ("conversationId", "chatId", "id", "sessionId"):
            if key in meta and meta.get(key):
                return str(meta.get(key))
    return None


def extract_user(prompt: Optional[str]) -> str:
    if not prompt:
        return ""
    m = re.search(r"<user>\s*(.*?)(?=\s*<assistant>|\s*<tool>|$)", prompt, re.DOTALL)
    if m:
        return m.group(1).strip()
    um = re.findall(r"User:\s*(.+)", prompt)
    return um[-1].strip() if um else prompt.strip()[:500]


def extract_thinking(prompt: Optional[str]) -> str:
    if not prompt:
        return ""
    m = re.search(r"<thinking>\s*(.*?)(?=\s*</thinking>|\s*<assistant>|\s*$)", prompt, re.DOTALL)
    return m.group(1).strip() if m else ""


@dataclass
class ConvInfo:
    count: int = 0
    last_ts: Optional[datetime] = None


def load_index(path: Path) -> Dict[str, ConvInfo]:
    index: Dict[str, ConvInfo] = {}
    with path.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            cid = detect_conv_id(d) or "unknown"
            ts = parse_timestamp(d.get("timestamp") or d.get("time") or d.get("createdAt"))
            info = index.setdefault(cid, ConvInfo())
            info.count += 1
            if ts and (info.last_ts is None or ts > info.last_ts):
                info.last_ts = ts
    return index


def stream_chat(path: Path, target_cid: str, limit: int) -> List[Dict]:
    buf: deque = deque(maxlen=limit)
    with path.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            cid = detect_conv_id(d) or "unknown"
            if cid != target_cid:
                continue
            ts = parse_timestamp(d.get("timestamp") or d.get("time") or d.get("createdAt"))
            model = d.get("modelTitle") or d.get("model") or d.get("model_name") or "unknown"
            prompt = d.get("prompt") or ""
            completion = (d.get("completion") or "").strip()
            user = extract_user(prompt)
            thinking = extract_thinking(prompt)
            buf.append(
                {
                    "ts": ts,
                    "model": model,
                    "user": user,
                    "thinking": thinking,
                    "assistant": completion,
                    "raw": d,
                }
            )
    return list(buf)


def print_entries(entries: Iterable[Dict]) -> None:
    for i, e in enumerate(entries, 1):
        ts = e.get("ts")
        time_str = ts.strftime("%Y-%m-%d %H:%M") if isinstance(ts, datetime) else "unknown"
        user_snip = (e.get("user") or "")[:300]
        thinking_snip = first_sentences(e.get("thinking") or "", max_chars=200, max_sentences=3)
        assistant_snip = first_sentences(e.get("assistant") or "", max_chars=200, max_sentences=3)
        print(f"[{i:02d}] {time_str} | {e.get('model')}")
        print(f"  User: {user_snip}")
        if thinking_snip:
            print(f"  Thinking: {thinking_snip}")
        print(f"  Assistant: {assistant_snip}")
        print()


def find_existing_path(candidates: Iterable[Path]) -> Optional[Path]:
    for p in candidates:
        if p and p.exists():
            return p
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Show last N messages for a conversation from chatInteraction.jsonl")
    ap.add_argument("path", nargs="?", help="path to chatInteraction.jsonl", default=None)
    ap.add_argument("--list", action="store_true", help="list conversation ids with counts and last timestamp")
    ap.add_argument("--chat", help="conversation id to show (if omitted, uses most recent)")
    ap.add_argument("--count", type=int, default=100, help="how many last messages to show (default 100)")
    args = ap.parse_args()

    candidates: List[Path] = []
    if args.path:
        candidates.append(Path(args.path))
    candidates.extend(
        [
            Path("/home/segun/.antigravity/chatInteraction.jsonl"),
            Path("/mnt/c/Users/123/.antigravity/chatInteraction.jsonl"),
            Path("/mnt/c/Users/123/.koda/dev_data/0.2.0/chatInteraction.jsonl"),
        ]
    )
    path = find_existing_path(candidates)
    if not path:
        print("File not found. Provide path to chatInteraction.jsonl as first argument.")
        return

    if args.list:
        idx = load_index(path)
        if not idx:
            print("No conversations found.")
            return
        items = sorted(idx.items(), key=lambda kv: (kv[1].last_ts or datetime.min), reverse=True)
        for cid, info in items:
            last = info.last_ts.strftime("%Y-%m-%d %H:%M") if info.last_ts else "unknown"
            print(f"{cid}  —  {info.count} records  —  last: {last}")
        return

    target = args.chat
    if not target:
        idx = load_index(path)
        if not idx:
            print("No conversations found.")
            return
        items = sorted(idx.items(), key=lambda kv: (kv[1].last_ts or datetime.min), reverse=True)
        target = items[0][0]
        print(f"No --chat provided. Using most recent conversation id: {target}")

    entries = stream_chat(path, target, args.count)
    if not entries:
        print(f"No entries found for conversation id: {target}")
        return
    print_entries(entries)


if __name__ == "__main__":
    main()
