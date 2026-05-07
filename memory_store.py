"""
Kscc Agent - Local layered memory (M4+ enhanced)

Stores under memory/:
- rules.json: list of strings (user rules / preferences)
- facts.json: list of {text, source?}
- archives.jsonl: one JSON object per line (session/task summaries)
- insights.jsonl: cross-task searchable insight index (P3-1)

Features:
- P3-1: Insight Index (cross-task searchable summaries)
- P3-2: Task-type-aware selective injection
- P3-3: Memory compression (short/medium/long term)
- P3-4: Conflict detection (contradictory/outdated facts)

All local-only; no server.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

MEMORY_DIR = Path(__file__).parent / "memory"
RULES_FILE = MEMORY_DIR / "rules.json"
FACTS_FILE = MEMORY_DIR / "facts.json"
ARCHIVES_FILE = MEMORY_DIR / "archives.jsonl"

# Thread lock for concurrent write safety (multi-worker)
_write_lock = threading.Lock()


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


def load_recent_archives(limit: int = 5, exclude_session_ids: Optional[list[str]] = None) -> list[dict[str, Any]]:
    _ensure_dir()
    if not ARCHIVES_FILE.exists():
        return []
    exclude = set(exclude_session_ids or [])
    lines = []
    try:
        raw = ARCHIVES_FILE.read_text("utf-8").splitlines()
        for line in raw[-200:]:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if exclude and entry.get("session_id", "") in exclude:
                    continue
                lines.append(entry)
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
    task_types: Optional[list[str]] = None,
    query: str = "",
    exclude_session_ids: Optional[list[str]] = None,
) -> str:
    """Compact text block for system prompt.

    P3-2: If task_types is provided, filter archives and insights to match.
    P3-1: Includes relevant insights from the insight index.
    """
    from insight_index import search_insights, classify_task_type

    parts: list[str] = []

    # Rules (always include)
    rules = load_rules()[:rules_limit]
    if rules:
        parts.append("### User rules")
        for r in rules:
            parts.append(f"- {r}")

    # Facts (always include)
    facts = load_facts()[:facts_limit]
    if facts:
        parts.append("\n### Stable facts")
        for f in facts:
            src = f.get("source") or ""
            line = f"- {f['text']}"
            if src:
                line += f" (source: {src})"
            parts.append(line)

    # P3-1: Insights (relevant to current task)
    if query:
        insights = search_insights(query, limit=5)
    else:
        from insight_index import load_insights
        insights = load_insights(limit=5)
    if insights:
        parts.append("\n### Relevant insights")
        for ins in insights:
            text = str(ins.get("text", ""))[:150]
            tags = ins.get("tags", [])
            tag_str = f" [{','.join(tags)}]" if tags else ""
            if text:
                parts.append(f"- {text}{tag_str}")

    # Archives (P3-2: filter by task type if provided)
    archives = load_recent_archives(archives_limit * 2 if task_types else archives_limit, exclude_session_ids=exclude_session_ids)
    if task_types:
        # Filter archives by matching task type keywords
        archives = _filter_archives_by_type(archives, task_types)[:archives_limit]
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


def get_injection_hits(
    task_types: Optional[list[str]] = None,
    query: str = "",
    rules_limit: int = 12,
    facts_limit: int = 8,
    archives_limit: int = 5,
    exclude_session_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """P3-5: Return metadata about what memory was injected.

    Returns a dict with counts and summaries of each layer.
    """
    from insight_index import search_insights, load_insights

    rules = load_rules()[:rules_limit]
    facts = load_facts()[:facts_limit]

    if query:
        insights = search_insights(query, limit=5)
    else:
        insights = load_insights(limit=5)

    archives = load_recent_archives(archives_limit * 2 if task_types else archives_limit, exclude_session_ids=exclude_session_ids)
    if task_types:
        archives = _filter_archives_by_type(archives, task_types)[:archives_limit]

    return {
        "rules_count": len(rules),
        "facts_count": len(facts),
        "insights_count": len(insights),
        "archives_count": len(archives),
        "task_types": task_types or [],
        "insight_tags": list({tag for ins in insights for tag in ins.get("tags", [])}),
    }


def _filter_archives_by_type(archives: list[dict], task_types: list[str]) -> list[dict]:
    """P3-2: Filter archives to match given task types."""
    if not task_types:
        return archives
    from insight_index import _TASK_TYPE_KEYWORDS
    # Collect all keywords for the requested types
    keywords = set()
    for tt in task_types:
        keywords.update(_TASK_TYPE_KEYWORDS.get(tt, []))
    if not keywords:
        return archives

    scored = []
    for a in archives:
        searchable = f"{a.get('title', '')} {a.get('user_prompt', '')} {a.get('summary', '')}".lower()
        hits = sum(1 for kw in keywords if kw in searchable)
        scored.append((hits, a))
    # Sort by relevance, keep all but put relevant ones first
    scored.sort(key=lambda x: -x[0])
    return [a for _, a in scored]


def compress_old_archives(days_threshold: int = 14, max_per_day: int = 2) -> dict[str, Any]:
    """P3-3: Compress old archives by keeping only top entries per day.

    Archives older than days_threshold days are compressed:
    - Group by date
    - Keep max_per_day entries per day (most turns first)
    - Remaining entries are summarized into facts

    Returns stats about compression.
    """
    _ensure_dir()
    if not ARCHIVES_FILE.exists():
        return {"compressed": 0, "kept": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_threshold)
    cutoff_str = cutoff.isoformat()

    with _write_lock:
        all_lines = []
        old_lines = []
        new_lines = []

        try:
            raw = ARCHIVES_FILE.read_text("utf-8").splitlines()
            for line in raw:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = entry.get("ts", "")
                    if ts and ts < cutoff_str:
                        old_lines.append(entry)
                    else:
                        new_lines.append(entry)
                except json.JSONDecodeError:
                    continue
        except Exception:
            return {"compressed": 0, "kept": 0, "error": "read failed"}

        if not old_lines:
            return {"compressed": 0, "kept": len(new_lines)}

        # Group old archives by date
        by_date: dict[str, list[dict]] = {}
        for entry in old_lines:
            ts = entry.get("ts", "")
            date_key = ts[:10] if len(ts) >= 10 else "unknown"
            by_date.setdefault(date_key, []).append(entry)

        # Keep top N per day, compress rest
        kept_old = []
        compressed_count = 0
        for date_key, entries in by_date.items():
            entries.sort(key=lambda e: -int(e.get("turns", 0)))
            kept_old.extend(entries[:max_per_day])
            compressed_count += max(0, len(entries) - max_per_day)

        # Rewrite archives file
        all_entries = kept_old + new_lines
        all_entries.sort(key=lambda e: e.get("ts", ""))
        with open(ARCHIVES_FILE, "w", encoding="utf-8") as f:
            for entry in all_entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return {
        "compressed": compressed_count,
        "kept": len(all_entries),
        "total_old": len(old_lines),
    }


def detect_fact_conflicts() -> list[dict[str, Any]]:
    """P3-4: Detect potentially conflicting or outdated facts.

    Returns a list of conflict pairs with details.
    """
    facts = load_facts()
    if len(facts) < 2:
        return []

    conflicts = []
    for i in range(len(facts)):
        for j in range(i + 1, len(facts)):
            f1 = facts[i]["text"].lower()
            f2 = facts[j]["text"].lower()

            # Check for negation pairs (simple heuristic)
            negation_words = ["不", "没有", "无法", "不是", "不能", "never", "not", "no", "don't", "doesn't", "cannot"]
            f1_has_neg = any(neg in f1 for neg in negation_words)
            f2_has_neg = any(neg in f2 for neg in negation_words)

            # If one has negation and the other doesn't, check keyword overlap
            if f1_has_neg != f2_has_neg:
                # Extract significant tokens
                tokens1 = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}", f1))
                tokens2 = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}", f2))
                overlap = tokens1 & tokens2
                # Remove common noise
                noise = {"the", "is", "are", "was", "has", "have", "this", "that", "一个", "一个", "的是", "不是"}
                overlap -= noise
                if len(overlap) >= 2:
                    conflicts.append({
                        "type": "negation_conflict",
                        "fact_1": facts[i]["text"],
                        "fact_2": facts[j]["text"],
                        "shared_keywords": list(overlap)[:5],
                    })

            # Check for very similar facts (possible duplicates)
            elif f1 != f2:
                tokens1 = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{4,}", f1))
                tokens2 = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{4,}", f2))
                if tokens1 and tokens2:
                    jaccard = len(tokens1 & tokens2) / max(len(tokens1 | tokens2), 1)
                    if jaccard > 0.6:
                        conflicts.append({
                            "type": "possible_duplicate",
                            "fact_1": facts[i]["text"],
                            "fact_2": facts[j]["text"],
                            "similarity": round(jaccard, 2),
                        })

    return conflicts


def remove_fact(index: int) -> bool:
    """Remove a fact by index. Used for conflict resolution."""
    _ensure_dir()
    with _write_lock:
        try:
            data = json.loads(FACTS_FILE.read_text("utf-8"))
        except Exception:
            return False
        facts = list(data.get("facts", []))
        if 0 <= index < len(facts):
            facts.pop(index)
            data["facts"] = facts
            FACTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
            return True
    return False


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
    with _write_lock:
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
    with _write_lock:
        try:
            data = json.loads(FACTS_FILE.read_text("utf-8"))
        except Exception:
            data = {"facts": []}
        facts = list(data.get("facts", []))
        facts.append({"text": text, "source": (source or "").strip()})
        data["facts"] = facts[-500:]
        FACTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return True
