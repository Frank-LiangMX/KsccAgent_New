"""
Kscc Agent - Insight Index (P3-1)

Stores cross-task searchable summaries extracted from completed conversations.
Each insight is a concise, reusable piece of knowledge.

Storage: memory/insights.jsonl (one JSON per line)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

MEMORY_DIR = Path(__file__).parent / "memory"
INSIGHTS_FILE = MEMORY_DIR / "insights.jsonl"

# Task type keywords for auto-tagging
_TASK_TYPE_KEYWORDS: dict[str, list[str]] = {
    "code": ["python", "javascript", "typescript", "java", "c++", "rust", "go", "代码", "编程", "函数", "class", "def ", "import ", "function", "const ", "let ", "var "],
    "debug": ["error", "bug", "报错", "异常", "traceback", "exception", "fix", "调试", "debug", "crash", "失败", "fault"],
    "config": ["配置", "设置", "config", "setting", "env", "yaml", "json", "toml", "ini", "环境变量"],
    "file": ["文件", "读取", "写入", "file", "read", "write", "创建", "删除", "目录", "path", "folder"],
    "git": ["git", "commit", "push", "pull", "branch", "merge", "rebase", "版本", "提交", "分支"],
    "data": ["数据", "data", "csv", "json", "数据库", "database", "sql", "pandas", "分析", "统计"],
    "deploy": ["部署", "deploy", "docker", "k8s", "kubernetes", "ci/cd", "nginx", "发布"],
    "test": ["测试", "test", "assert", "pytest", "unittest", "mock", "验证"],
    "refactor": ["重构", "refactor", "优化", "optimize", "重写", "改进"],
    "ui": ["ui", "界面", "css", "html", "组件", "component", "布局", "layout", "样式", "style"],
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir():
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def classify_task_type(text: str) -> list[str]:
    """Classify a task prompt into type tags based on keyword matching."""
    t = (text or "").lower()
    if not t:
        return []
    scores: dict[str, int] = {}
    for tag, keywords in _TASK_TYPE_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in t)
        if hits > 0:
            scores[tag] = hits
    if not scores:
        return ["general"]
    sorted_tags = sorted(scores.items(), key=lambda x: -x[1])
    # Return top 2 types
    return [tag for tag, _ in sorted_tags[:2]]


def extract_insights_from_archive(archive: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract concise insights from an archive entry."""
    insights = []
    title = str(archive.get("title", "") or "").strip()
    summary = str(archive.get("summary", "") or "").strip()
    user_prompt = str(archive.get("user_prompt", "") or "").strip()
    ts = archive.get("ts", _now_iso())
    session_id = archive.get("session_id", "")

    if not title and not summary:
        return []

    # Classify task type
    task_types = classify_task_type(f"{title} {user_prompt}")

    # Insight 1: Task outcome summary (always)
    if title:
        outcome = title
        if summary:
            # Take first sentence of summary as outcome detail
            first_sent = re.split(r"[。\n.]", summary)[0][:120]
            if first_sent:
                outcome += f" — {first_sent}"
        insights.append({
            "text": outcome,
            "tags": task_types,
            "source_session": session_id,
            "ts": ts,
        })

    # Insight 2: Key technical detail (if summary has code-like content)
    code_patterns = re.findall(r"(?:文件|file|路径|path|命令|command)[:：]\s*[^\n]{5,80}", summary, re.IGNORECASE)
    if code_patterns:
        insights.append({
            "text": code_patterns[0][:150],
            "tags": task_types + ["technical"],
            "source_session": session_id,
            "ts": ts,
        })

    # Insight 3: Problem-solution pattern (if debug-like)
    if any(t in task_types for t in ("debug", "config")):
        # Look for cause → fix pattern
        cause_match = re.search(r"(?:原因|cause|问题是|issue)[:：]\s*([^\n]{10,100})", summary, re.IGNORECASE)
        fix_match = re.search(r"(?:解决|fix|修复|solution)[:：]\s*([^\n]{10,100})", summary, re.IGNORECASE)
        if cause_match and fix_match:
            insights.append({
                "text": f"问题: {cause_match.group(1).strip()} → 解决: {fix_match.group(1).strip()}",
                "tags": task_types + ["problem-solution"],
                "source_session": session_id,
                "ts": ts,
            })

    return insights


def append_insights(insights: list[dict[str, Any]], max_insights: int = 500) -> int:
    """Append insights to the index file. Returns count written.

    When total count exceeds *max_insights*, the oldest entries are trimmed so
    only the newest *max_insights* lines are kept.
    """
    if not insights:
        return 0
    _ensure_dir()
    count = 0
    with open(INSIGHTS_FILE, "a", encoding="utf-8") as f:
        for ins in insights:
            if not ins.get("text", "").strip():
                continue
            line = json.dumps(ins, ensure_ascii=False) + "\n"
            f.write(line)
            count += 1

    # Truncate: keep only the newest max_insights lines
    if count > 0 and INSIGHTS_FILE.exists():
        try:
            lines = INSIGHTS_FILE.read_text("utf-8").splitlines(keepends=True)
            if len(lines) > max_insights:
                kept = lines[-max_insights:]
                INSIGHTS_FILE.write_text("".join(kept), "utf-8")
        except Exception:
            pass  # best-effort; don't crash on trim failure

    return count


def load_insights(limit: int = 50) -> list[dict[str, Any]]:
    """Load recent insights from the index."""
    _ensure_dir()
    if not INSIGHTS_FILE.exists():
        return []
    results = []
    try:
        raw = INSIGHTS_FILE.read_text("utf-8").splitlines()
        for line in raw[-200:]:
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return results[-limit:] if limit else results
    except Exception:
        return []


def search_insights(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search insights by keyword relevance."""
    query_lower = (query or "").lower().strip()
    if not query_lower:
        return load_insights(limit)

    # Extract query tokens
    q_tokens = set(re.findall(r"[a-zA-Z0-9_./-]+|[\u4e00-\u9fff]{2,}", query_lower))

    all_insights = load_insights(limit=200)
    scored = []
    for ins in all_insights:
        text = str(ins.get("text", "")).lower()
        tags = [str(t).lower() for t in ins.get("tags", [])]
        searchable = text + " " + " ".join(tags)

        # Score: count matching tokens
        score = sum(1 for tok in q_tokens if tok in searchable)
        if score > 0:
            scored.append((score, ins))

    scored.sort(key=lambda x: -x[0])
    return [ins for _, ins in scored[:limit]]


def get_insight_stats() -> dict[str, Any]:
    """Get statistics about the insight index."""
    all_insights = load_insights(limit=0)
    tag_counts: dict[str, int] = {}
    for ins in all_insights:
        for tag in ins.get("tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    return {
        "total": len(all_insights),
        "tag_counts": tag_counts,
    }
