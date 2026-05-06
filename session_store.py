"""
Kscc Agent - 会话持久化存储

存储结构:
  sessions/
  ├── index.json        # 会话索引
  └── <uuid>.json        # 单会话数据（包含完整消息历史）
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SESSION_DIR = Path(__file__).parent / "sessions"
INDEX_FILE = SESSION_DIR / "index.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Session:
    def __init__(self, sid: str = "", title: str = "", workspace: str = "", mode: str = "solo"):
        self.id = sid or str(uuid.uuid4())
        self.title = title or "New Session"
        self.workspace = workspace
        self.mode = mode
        # Per-session model selection
        self.backend: str = ""   # "kscc" | "openai" | ...
        self.model: str = ""     # kscc model name OR openai active name
        self.created = _now_iso()
        self.updated = _now_iso()
        self.messages: list[dict] = []
        # Runtime switch history, persisted for timeline/audit.
        self.model_switches: list[dict[str, Any]] = []
        # Kscc 上下文用量快照（随流传输；用于恢复会话时显示环形指示）
        self.context_info: Optional[dict[str, Any]] = None

    def to_index_entry(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "workspace": self.workspace,
            "mode": self.mode,
            "backend": self.backend,
            "model": self.model,
            "created": self.created,
            "updated": self.updated,
            "message_count": len([m for m in self.messages if m.get("role") in ("assistant", "tool")]),
        }

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "title": self.title,
            "workspace": self.workspace,
            "mode": self.mode,
            "backend": self.backend,
            "model": self.model,
            "created": self.created,
            "updated": self.updated,
            "messages": self.messages,
            "model_switches": self.model_switches,
        }
        if self.context_info is not None:
            d["context_info"] = self.context_info
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        s = cls(
            sid=data.get("id", ""),
            title=data.get("title", "Untitled"),
            workspace=data.get("workspace", ""),
            mode=data.get("mode", "solo"),
        )
        s.created = data.get("created", s.created)
        s.updated = data.get("updated", s.updated)
        s.backend = str(data.get("backend", "") or "")
        s.model = str(data.get("model", "") or "")
        s.messages = data.get("messages", [])
        s.model_switches = data.get("model_switches", [])
        s.context_info = data.get("context_info")
        return s


class SessionStore:
    def __init__(self):
        SESSION_DIR.mkdir(exist_ok=True)
        self._index: dict[str, dict] = {}
        self._order: list[str] = []
        self._load_index()

    def _load_index(self):
        if INDEX_FILE.exists():
            try:
                data = json.loads(INDEX_FILE.read_text("utf-8"))
                self._index = data.get("sessions", {})
                self._order = data.get("order", [])
            except Exception:
                self._index = {}
                self._order = []

    def _save_index(self):
        INDEX_FILE.write_text(
            json.dumps({"sessions": self._index, "order": self._order}, ensure_ascii=False, indent=2),
            "utf-8",
        )

    def list_sessions(self) -> list[dict]:
        return [self._index[sid] for sid in self._order if sid in self._index]

    def create(self, title: str = "", workspace: str = "", mode: str = "solo") -> Session:
        session = Session(title=title or "New Session", workspace=workspace, mode=mode)
        self._index[session.id] = session.to_index_entry()
        self._order.insert(0, session.id)
        self._save_index()
        self._save_session(session)
        return session

    def load(self, sid: str) -> Optional[Session]:
        if sid not in self._index:
            return None
        session_file = SESSION_DIR / f"{sid}.json"
        if not session_file.exists():
            return None
        try:
            data = json.loads(session_file.read_text("utf-8"))
            return Session.from_dict(data)
        except Exception:
            return None

    def save(self, session: Session, title: str = ""):
        session.updated = _now_iso()
        if title:
            session.title = title
        self._save_session(session)
        self._index[session.id] = session.to_index_entry()
        if session.id not in self._order:
            self._order.insert(0, session.id)
        self._save_index()

    def delete(self, sid: str):
        if sid in self._index:
            del self._index[sid]
        if sid in self._order:
            self._order.remove(sid)
        session_file = SESSION_DIR / f"{sid}.json"
        if session_file.exists():
            session_file.unlink()
        self._save_index()

    def auto_title(self, messages: list[dict]) -> str:
        """从第一条用户消息中提取标题"""
        for m in messages:
            if m.get("role") == "user":
                text = str(m.get("display_text", m.get("content", ""))).strip()
                if not text and m.get("attachments"):
                    first = m["attachments"][0] if isinstance(m["attachments"], list) and m["attachments"] else {}
                    text = str(first.get("name", "")).strip()
                # 取前 40 个字符
                text = text.replace("\n", " ").strip()
                return text[:40] + ("..." if len(text) > 40 else "")
        return "Empty Session"

    def _save_session(self, session: Session):
        session_file = SESSION_DIR / f"{session.id}.json"
        session_file.write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2),
            "utf-8",
        )
