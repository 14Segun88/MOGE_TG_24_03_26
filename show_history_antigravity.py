#!/usr/bin/env python3
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

DEFAULT_CANDIDATES = [
    Path(sys.argv[1]) if len(sys.argv) > 1 else None,
    Path('/home/segun/.antigravity/chatInteraction.jsonl'),
    Path('/mnt/c/Users/123/.antigravity/chatInteraction.jsonl'),
    Path('/mnt/c/Users/123/.koda/dev_data/0.2.0/chatInteraction.jsonl')
]
JSONL_PATH = next((p for p in DEFAULT_CANDIDATES if p and p.exists()), DEFAULT_CANDIDATES[1])
MSK = timezone(timedelta(hours=3))

def first_sentences(text, max_chars=150, max_sentences=3):
    if not text:
        return ''
    s = re.sub(r'\s+', ' ', text.strip())
    parts = re.split(r'(?<=[.!?])\s+', s)
    chosen = []
    for p in parts:
        if p:
            chosen.append(p)
        if len(chosen) >= max_sentences:
            break
    out = ' '.join(chosen)
    return out[:max_chars].strip()

def extract_last_user(prompt):
    if not prompt:
        return ''
    m = re.search(r'<user>\s*(.*?)(?=\s*<assistant>|\s*<tool>|$)', prompt, re.DOTALL)
    if m:
        return m.group(1).strip()
    um = re.findall(r'User:\s*(.+)', prompt)
    return um[-1].strip() if um else ''

def extract_thinking(prompt):
    if not prompt:
        return ''
    m = re.search(r'<thinking>\s*(.*?)(?=\s*</thinking>|\s*<assistant>|\s*$)', prompt, re.DOTALL)
    return m.group(1).strip() if m else ''

def parse_line(line):
    try:
        d = json.loads(line)
    except Exception:
        return None
    ts = str(d.get('timestamp',''))
    try:
        dt = datetime.fromisoformat(ts.replace('Z','+00:00')).astimezone(MSK)
        time_str = dt.strftime('%Y-%m-%d %H:%M')
    except Exception:
        time_str = 'unknown'
    model = d.get('modelTitle') or d.get('model') or 'antigraviti'
    prompt = d.get('prompt','') or ''
    completion = (d.get('completion') or '').strip()
    user_text = extract_last_user(prompt) or '—'
    thinking = extract_thinking(prompt) or ''
    return {
        'time': time_str,
        'model': model,
        'user': user_text,
        'thinking': thinking,
        'assistant': completion
    }

def main():
    if not JSONL_PATH or not JSONL_PATH.exists():
        print(f'File not found: {JSONL_PATH}')
        return
    with JSONL_PATH.open(encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            rec = parse_line(line)
            if not rec:
                continue
            user_snip = rec['user'][:150]
            thinking_snip = first_sentences(rec['thinking'], max_chars=150, max_sentences=3)
            assistant_snip = first_sentences(rec['assistant'], max_chars=150, max_sentences=3)
            print(f'[{i:02d}] {rec["time"]} | {rec["model"]}')
            print(f'  User: {user_snip}')
            if thinking_snip:
                print(f'  Thinking: {thinking_snip}')
            print(f'  Assistant: {assistant_snip}')
            print()

if __name__ == '__main__':
    main()
