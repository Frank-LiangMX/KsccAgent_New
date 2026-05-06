"""
Kscc Agent - local data export/import (skills + memory)

Export format: a zip containing folders:
  - skills/
  - memory/

Import merges by default:
  - skills/: copies items and merges index.json (keeps existing, adds new)
  - memory/: appends archives.jsonl, merges rules/facts lists (dedupe by text)
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parent
SKILLS_DIR = ROOT / "skills"
MEMORY_DIR = ROOT / "memory"


def _read_json(path: Path, default: Any):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def export_zip(target_zip: str) -> str:
    zp = Path(target_zip)
    zp.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for base in (SKILLS_DIR, MEMORY_DIR):
            if not base.exists():
                continue
            for p in base.rglob("*"):
                if p.is_file():
                    arc = str(p.relative_to(ROOT))
                    z.write(p, arcname=arc)
    return str(zp)


def import_zip(source_zip: str) -> dict[str, int]:
    """Returns counts: skills_added, skills_updated, memory_rules_added, memory_facts_added, archives_appended."""
    src = Path(source_zip)
    if not src.exists():
        raise FileNotFoundError(str(src))
    tmp = ROOT / ".tmp_import"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(src, "r") as z:
        z.extractall(tmp)

    counts = {
        "skills_added": 0,
        "skills_updated": 0,
        "memory_rules_added": 0,
        "memory_facts_added": 0,
        "archives_appended": 0,
    }

    # ---- skills merge ----
    in_skills = tmp / "skills"
    if in_skills.exists():
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        (SKILLS_DIR / "items").mkdir(parents=True, exist_ok=True)
        cur_index = _read_json(SKILLS_DIR / "index.json", {"skills": {}, "order": []})
        inc_index = _read_json(in_skills / "index.json", {"skills": {}, "order": []})
        cur_sk = cur_index.get("skills", {}) if isinstance(cur_index, dict) else {}
        cur_order = cur_index.get("order", []) if isinstance(cur_index, dict) else []
        inc_sk = inc_index.get("skills", {}) if isinstance(inc_index, dict) else {}
        inc_order = inc_index.get("order", []) if isinstance(inc_index, dict) else []

        # copy items first
        in_items = in_skills / "items"
        if in_items.exists():
            for p in in_items.glob("*.json"):
                dst = SKILLS_DIR / "items" / p.name
                if dst.exists():
                    counts["skills_updated"] += 1
                else:
                    counts["skills_added"] += 1
                shutil.copy2(p, dst)

        # merge index
        for sid, meta in inc_sk.items():
            if sid not in cur_sk:
                cur_sk[sid] = meta
        # order: prefer existing, append new in imported order
        seen = set([str(x) for x in cur_order if x])
        for sid in inc_order:
            sid = str(sid)
            if sid and sid not in seen and sid in cur_sk:
                cur_order.append(sid)
                seen.add(sid)
        (SKILLS_DIR / "index.json").write_text(
            json.dumps({"skills": cur_sk, "order": cur_order}, ensure_ascii=False, indent=2),
            "utf-8",
        )

    # ---- memory merge ----
    in_mem = tmp / "memory"
    if in_mem.exists():
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        # rules
        cur_rules = _read_json(MEMORY_DIR / "rules.json", {"rules": []})
        inc_rules = _read_json(in_mem / "rules.json", {"rules": []})
        cur_list = cur_rules.get("rules", []) if isinstance(cur_rules, dict) else []
        inc_list = inc_rules.get("rules", []) if isinstance(inc_rules, dict) else []
        cur_set = set(str(x).strip() for x in cur_list if str(x).strip())
        for r in inc_list:
            r = str(r).strip()
            if r and r not in cur_set:
                cur_list.append(r)
                cur_set.add(r)
                counts["memory_rules_added"] += 1
        (MEMORY_DIR / "rules.json").write_text(json.dumps({"rules": cur_list}, ensure_ascii=False, indent=2), "utf-8")

        # facts
        cur_facts = _read_json(MEMORY_DIR / "facts.json", {"facts": []})
        inc_facts = _read_json(in_mem / "facts.json", {"facts": []})
        cur_list = cur_facts.get("facts", []) if isinstance(cur_facts, dict) else []
        inc_list = inc_facts.get("facts", []) if isinstance(inc_facts, dict) else []
        cur_texts = set()
        for f in cur_list:
            if isinstance(f, dict):
                cur_texts.add(str(f.get("text", "")).strip())
            elif isinstance(f, str):
                cur_texts.add(f.strip())
        for f in inc_list:
            if isinstance(f, dict):
                text = str(f.get("text", "")).strip()
                if text and text not in cur_texts:
                    cur_list.append({"text": text, "source": str(f.get("source", "") or "")})
                    cur_texts.add(text)
                    counts["memory_facts_added"] += 1
            elif isinstance(f, str):
                text = f.strip()
                if text and text not in cur_texts:
                    cur_list.append({"text": text, "source": ""})
                    cur_texts.add(text)
                    counts["memory_facts_added"] += 1
        (MEMORY_DIR / "facts.json").write_text(json.dumps({"facts": cur_list}, ensure_ascii=False, indent=2), "utf-8")

        # archives: append jsonl
        inc_arch = in_mem / "archives.jsonl"
        if inc_arch.exists():
            dst_arch = MEMORY_DIR / "archives.jsonl"
            dst_arch.parent.mkdir(parents=True, exist_ok=True)
            data = inc_arch.read_text("utf-8", errors="ignore").strip()
            if data:
                with open(dst_arch, "a", encoding="utf-8") as f:
                    f.write(data + "\n")
                counts["archives_appended"] += len(data.splitlines())

    shutil.rmtree(tmp, ignore_errors=True)
    return counts

