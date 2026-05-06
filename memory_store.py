"""
Kscc Agent - Local layered memory (M4 minimal)

Stores under memory/:
- rules.json: list of strings (user rules / preferences)
- facts.json: list of {text, source?}
- archives.jsonl: one JSON object per line (session/task summaries)

All local-only; no server.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

MEMORY_DIR = Path(__file__).parent / "memory"
RULES_FILE = MEMORY_DIR / "rules.json"
FACTS_FILE = MEMORY_DIR / "facts.json"
ARCHIVES_FILE = MEMORY_DIR / "archives.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir():
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    if not RULES_FILE.exists():
        RULES_FILE.write_text(json.dumps({"rules": []}, ensure_ascii=False, indent=2), "utf-8")
    if not FACTS_FILE.exists():
        FACTS_FILE.write_text(json.dumps({"facts": []}, ensure_ascii=False, indent=2), "utf-8")


def load_rules() -> list[str]:
    _ensure_dir()
    try:
        data = json.loads(RULES_FILE.read_text("utf-8"))
        rules = data.get("rules", [])
        return [str(x).strip() for x in rules if str(x).strip()]
    except Exception:
        return []


def load_facts() -> list[dict[str, Any]]:
    _ensure_dir()
    try:
        data = json.loads(FACTS_FILE.read_text("utf-8"))
        facts = data.get("facts", [])
        out = []
        for f in facts:
            if isinstance(f, dict) and str(f.get("text", "")).strip():
                out.append({"text": str(f["text"]).strip(), "source": str(f.get("source", "") or "")})
            elif isinstance(f, str) and f.strip():
                out.append({"text": f.strip(), "source": ""})
        return out
    except Exception:
        return []


def load_recent_archives(limit: int = 5) -> list[dict[str, Any]]:
    _ensure_dir()
    if not ARCHIVES_FILE.exists():
        return []
    lines = []
    try:
        raw = ARCHIVES_FILE.read_text("utf-8").splitlines()
        for line in raw[-200:]:
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return lines[-limit:] if limit else lines
    except Exception:
        return []


def build_injection_text(
    rules_limit: int = 12,
    facts_limit: int = 8,
    archives_limit: int = 5,
    max_chars: int = 4000,
) -> str:
    """Compact text block for system prompt."""
    parts: list[str] = []
    rules = load_rules()[:rules_limit]
    if rules:
        parts.append("### User rules")
        for r in rules:
            parts.append(f"- {r}")
    facts = load_facts()[:facts_limit]
    if facts:
        parts.append("\n### Stable facts")
        for f in facts:
            src = f.get("source") or ""
            line = f"- {f['text']}"
            if src:
                line += f" (source: {src})"
            parts.append(line)
    archives = load_recent_archives(archives_limit)
    if archives:
        parts.append("\n### Recent task archives (short)")
        for a in archives:
            title = str(a.get("title", "") or "")[:60]
            summ = str(a.get("summary", "") or "")[:200]
            if title or summ:
                parts.append(f"- {title}: {summ}")
    text = "\n".join(parts).strip()
    if len(text) > max_chars:
        return text[: max_chars - 20] + "\n...[truncated]"
    return text


def append_archive(
    *,
    session_id: str = "",
    title: str = "",
    user_prompt: str = "",
    summary: str = "",
    turns: int = 0,
    workspace: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> None:
    _ensure_dir()
    row = {
        "ts": _now_iso(),
        "session_id": session_id,
        "title": title,
        "user_prompt": (user_prompt or "")[:2000],
        "summary": (summary or "")[:4000],
        "turns": int(turns or 0),
        "workspace": workspace or "",
    }
    if extra:
        row["extra"] = extra
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with open(ARCHIVES_FILE, "a", encoding="utf-8") as f:
        f.write(line)


def add_rule(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    _ensure_dir()
    try:
        data = json.loads(RULES_FILE.read_text("utf-8"))
    except Exception:
        data = {"rules": []}
    rules = list(data.get("rules", []))
    if text not in rules:
        rules.append(text)
    data["rules"] = rules
    RULES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return True


def add_fact(text: str, source: str = "") -> bool:
    text = (text or "").strip()
    if not text:
        return False
    _ensure_dir()
    try:
        data = json.loads(FACTS_FILE.read_text("utf-8"))
    except Exception:
        data = {"facts": []}
    facts = list(data.get("facts", []))
    facts.append({"text": text, "source": (source or "").strip()})
    data["facts"] = facts[-500:]
    FACTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return True
