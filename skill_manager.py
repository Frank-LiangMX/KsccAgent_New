"""
Kscc Agent - Local Skill storage, weighted matching, and lifecycle helpers.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SKILLS_DIR = Path(__file__).parent / "skills"
SKILLS_ITEMS_DIR = SKILLS_DIR / "items"
SKILLS_INDEX_FILE = SKILLS_DIR / "index.json"
LOG_DIR = Path(__file__).parent / "logs"
SKILL_LOGGER = logging.getLogger("kscc.skill")


def _ensure_skill_logger():
    if SKILL_LOGGER.handlers:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_DIR / "skill_debug.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    SKILL_LOGGER.addHandler(fh)
    SKILL_LOGGER.setLevel(logging.DEBUG)
    SKILL_LOGGER.propagate = False


def skill_debug_log(message: str, enabled: bool = False) -> None:
    if not enabled:
        return
    _ensure_skill_logger()
    SKILL_LOGGER.debug(message)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", (text or "").strip().lower()).strip("-")
    return cleaned[:48] or "skill"


def _normalize_keywords(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [x.strip().lower() for x in re.split(r"[,\n;|]+", raw) if x.strip()]
    if isinstance(raw, list):
        out = []
        for item in raw:
            s = str(item).strip().lower()
            if s:
                out.append(s)
        return out
    return []


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts or not isinstance(ts, str):
        return None
    try:
        s = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


@dataclass
class Skill:
    id: str
    name: str
    intent_pattern: list[str]
    steps: list[str]
    success_count: int = 0
    last_used_at: str = ""
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Skill":
        return cls(
            id=str(data.get("id", "")).strip(),
            name=str(data.get("name", "Untitled Skill")).strip(),
            intent_pattern=_normalize_keywords(data.get("intent_pattern", [])),
            steps=[str(s).strip() for s in data.get("steps", []) if str(s).strip()],
            success_count=int(data.get("success_count", 0) or 0),
            last_used_at=str(data.get("last_used_at", "") or ""),
            enabled=bool(data.get("enabled", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "intent_pattern": self.intent_pattern,
            "steps": self.steps,
            "success_count": self.success_count,
            "last_used_at": self.last_used_at,
            "enabled": self.enabled,
        }


@dataclass
class SkillMatchResult:
    best: Optional[Skill] = None
    candidates: list[tuple[float, Skill]] = field(default_factory=list)
    miss_reason: str = ""
    hint: str = ""

    def is_ambiguous(self, ratio: float = 0.88) -> bool:
        if len(self.candidates) < 2:
            return False
        top = self.candidates[0][0]
        second = self.candidates[1][0]
        if top <= 0:
            return False
        return second >= top * ratio


class SkillManager:
    def __init__(self):
        self._ensure_storage()

    def _ensure_storage(self):
        SKILLS_ITEMS_DIR.mkdir(parents=True, exist_ok=True)
        if not SKILLS_INDEX_FILE.exists():
            SKILLS_INDEX_FILE.write_text(
                json.dumps({"skills": {}, "order": []}, ensure_ascii=False, indent=2),
                "utf-8",
            )

    def _load_index(self) -> tuple[dict[str, dict[str, Any]], list[str]]:
        try:
            data = json.loads(SKILLS_INDEX_FILE.read_text("utf-8"))
            skills = data.get("skills", {})
            order = data.get("order", [])
            if isinstance(skills, dict) and isinstance(order, list):
                return skills, [str(x) for x in order]
        except Exception:
            pass
        return {}, []

    def _save_index(self, skills: dict[str, dict[str, Any]], order: list[str]):
        SKILLS_INDEX_FILE.write_text(
            json.dumps({"skills": skills, "order": order}, ensure_ascii=False, indent=2),
            "utf-8",
        )

    def _write_skill_file(self, skill: Skill):
        path = SKILLS_ITEMS_DIR / f"{skill.id}.json"
        path.write_text(json.dumps(skill.to_dict(), ensure_ascii=False, indent=2), "utf-8")

    def upsert_skill(
        self,
        name: str,
        intent_pattern: list[str] | str,
        steps: list[str],
        skill_id: Optional[str] = None,
        enabled: bool = True,
    ) -> Skill:
        """Create new skill. For updates use update_skill_full."""
        skills, order = self._load_index()
        sid = (skill_id or _slugify(name)).strip()
        if sid in skills:
            base = sid
            i = 2
            while sid in skills:
                sid = f"{base}-{i}"
                i += 1
        skill = Skill(
            id=sid,
            name=name.strip() or "Untitled Skill",
            intent_pattern=_normalize_keywords(intent_pattern),
            steps=[str(s).strip() for s in steps if str(s).strip()],
            success_count=0,
            last_used_at="",
            enabled=enabled,
        )
        self._write_skill_file(skill)
        skills[skill.id] = {
            "id": skill.id,
            "name": skill.name,
            "intent_pattern": skill.intent_pattern,
            "success_count": skill.success_count,
            "last_used_at": skill.last_used_at,
            "enabled": skill.enabled,
        }
        if skill.id not in order:
            order.insert(0, skill.id)
        self._save_index(skills, order)
        return skill

    def update_skill_full(self, skill: Skill) -> Skill:
        """Overwrite skill by id (name, patterns, steps, enabled). Keeps success_count/last_used unless reset desired."""
        skills, order = self._load_index()
        if skill.id not in skills and skill.id not in order:
            order.insert(0, skill.id)
        skills[skill.id] = {
            "id": skill.id,
            "name": skill.name,
            "intent_pattern": skill.intent_pattern,
            "success_count": skill.success_count,
            "last_used_at": skill.last_used_at,
            "enabled": skill.enabled,
        }
        self._write_skill_file(skill)
        self._save_index(skills, order)
        return skill

    def delete_skill(self, skill_id: str) -> bool:
        skills, order = self._load_index()
        if skill_id in skills:
            del skills[skill_id]
        order = [x for x in order if x != skill_id]
        self._save_index(skills, order)
        path = SKILLS_ITEMS_DIR / f"{skill_id}.json"
        if path.exists():
            try:
                path.unlink()
            except OSError:
                return False
        return True

    def reorder(self, new_order: list[str]) -> None:
        skills, _ = self._load_index()
        seen = set()
        order = []
        for sid in new_order:
            if sid in skills and sid not in seen:
                order.append(sid)
                seen.add(sid)
        for sid in skills:
            if sid not in seen:
                order.append(sid)
        self._save_index(skills, order)

    def load_skill(self, skill_id: str) -> Optional[Skill]:
        path = SKILLS_ITEMS_DIR / f"{skill_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text("utf-8"))
            return Skill.from_dict(data)
        except Exception:
            return None

    def list_skills(self) -> list[Skill]:
        skills, order = self._load_index()
        out: list[Skill] = []
        visited = set()
        for sid in order:
            sk = self.load_skill(sid)
            if sk:
                out.append(sk)
                visited.add(sid)
        for sid in skills.keys():
            if sid in visited:
                continue
            sk = self.load_skill(sid)
            if sk:
                out.append(sk)
        return out

    def _score_skill(self, skill: Skill, text: str, order_index: int) -> float:
        if not skill.enabled or not skill.intent_pattern:
            return 0.0
        score = 0.0
        kws = sorted(skill.intent_pattern, key=len, reverse=True)
        for kw in kws:
            if kw and kw in text:
                score += len(kw) * 1.85
        order_bonus = max(0.0, 4.5 - order_index * 0.25)
        score += order_bonus
        score += min(28.0, (1.0 + max(0, skill.success_count)) ** 0.45 * 3.2)
        lu = _parse_iso(skill.last_used_at)
        if lu:
            age_days = (datetime.now(timezone.utc) - lu).total_seconds() / 86400.0
            if age_days <= 3:
                score += 6.0
            elif age_days <= 14:
                score += 3.0
        return score

    def match_detailed(self, prompt: str, top_k: int = 8) -> SkillMatchResult:
        text = (prompt or "").strip().lower()
        if not text:
            return SkillMatchResult(
                miss_reason="empty_prompt",
                hint="请输入任务描述后再发送；Skill 依赖用户问题中的关键词匹配。",
            )
        if not self.list_skills():
            return SkillMatchResult(
                miss_reason="no_skills",
                hint="尚未配置任何 Skill。可在设置中管理 Skills，或完成任务后选择保存为 Skill。",
            )
        skills, order = self._load_index()
        ranked: list[tuple[float, Skill]] = []
        pos = {sid: i for i, sid in enumerate(order)}
        for skill in self.list_skills():
            if not skill.enabled:
                continue
            idx = pos.get(skill.id, 99)
            s = self._score_skill(skill, text, idx)
            if s > 0:
                ranked.append((s, skill))
        ranked.sort(key=lambda x: x[0], reverse=True)
        top = ranked[:top_k]
        if not top:
            return SkillMatchResult(
                candidates=[],
                miss_reason="no_keyword_hit",
                hint="没有 Skill 的关键词命中当前问题。可为相关 Skill 添加 intent_pattern，或使用「保存为 Skill」沉淀关键词。",
            )
        return SkillMatchResult(best=top[0][1], candidates=top, miss_reason="", hint="")

    def match(self, prompt: str) -> Optional[Skill]:
        return self.match_detailed(prompt).best

    def mark_used(self, skill_id: str):
        skills, order = self._load_index()
        if skill_id not in skills:
            return
        skills[skill_id]["success_count"] = int(skills[skill_id].get("success_count", 0) or 0) + 1
        skills[skill_id]["last_used_at"] = _now_iso()
        self._save_index(skills, order)
        sk = self.load_skill(skill_id)
        if sk:
            sk.success_count = int(sk.success_count or 0) + 1
            sk.last_used_at = skills[skill_id]["last_used_at"]
            self._write_skill_file(sk)
