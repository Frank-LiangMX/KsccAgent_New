"""
P5-4: Session Audit Panel - 会话审计视图

展示会话级别的关键事件：模型切换、工具调用、风控告警、记忆命中等。
支持按会话浏览和按事件类型筛选。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QComboBox, QSizePolicy, QSplitter, QPushButton,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont

from theme import C_TEXT, C_DIM, C_PANEL, C_PANEL_HI, C_ACCENT, C_GREEN, C_RED, C_YELLOW


def _is_light(cfg) -> bool:
    return str(getattr(cfg, "theme", "dark")).lower() == "light"


# Event type -> (icon char, color)
_EVENT_STYLE = {
    "task_start": ("▶", C_ACCENT),
    "task_complete": ("✓", C_GREEN),
    "task_failure": ("✗", C_RED),
    "step_start": ("▸", C_DIM),
    "step_complete": ("✓", C_GREEN),
    "step_failure": ("✗", C_RED),
    "step_retry": ("↻", C_YELLOW),
    "tool_call": ("⚙", C_ACCENT),
    "tool_result": ("◼", C_DIM),
    "reflection": ("💡", C_YELLOW),
    "decision": ("◆", C_ACCENT),
    "recovery_action": ("↺", C_YELLOW),
    "risk_template": ("⚠", C_RED),
    "model_switch": ("⇄", C_ACCENT),
    "memory_hit": ("🧠", C_GREEN),
}


class AuditEventRow(QFrame):
    """单个审计事件行"""

    def __init__(self, event_type: str, timestamp: str, summary: str, detail: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("auditEventRow")
        self._detail = detail
        self._expanded = False

        icon_char, color = _EVENT_STYLE.get(event_type, ("•", C_DIM))

        self.setStyleSheet(
            f"QFrame#auditEventRow {{ background: transparent; border-bottom: 1px solid rgba(255,255,255,0.05); }}"
            f"QFrame#auditEventRow:hover {{ background: {C_PANEL}; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        # Icon
        icon_lbl = QLabel(icon_char)
        icon_lbl.setFixedWidth(20)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet(f"color: {color}; font-size: 14px; background: transparent;")
        layout.addWidget(icon_lbl)

        # Timestamp
        ts_lbl = QLabel(timestamp)
        ts_lbl.setFixedWidth(70)
        ts_lbl.setStyleSheet(f"color: {C_DIM}; font-size: 11px; font-family: 'JetBrains Mono', monospace; background: transparent;")
        layout.addWidget(ts_lbl)

        # Event type badge
        badge = QLabel(event_type)
        badge.setFixedWidth(100)
        badge.setStyleSheet(
            f"color: {color}; font-size: 10px; font-weight: 700; "
            f"background: rgba(255,255,255,0.04); border-radius: 4px; padding: 2px 6px;"
        )
        layout.addWidget(badge)

        # Summary
        summary_lbl = QLabel(summary)
        summary_lbl.setStyleSheet(f"color: {C_TEXT}; font-size: 12px; background: transparent;")
        layout.addWidget(summary_lbl, 1)

    def mousePressEvent(self, event):
        # Toggle detail expansion (future enhancement)
        super().mousePressEvent(event)


class AuditPanel(QWidget):
    """会话审计视图"""

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setObjectName("AuditPanel")
        self.log_dir = Path("logs/tasks")
        self._all_events = []
        self._setup_ui()

    def _setup_ui(self):
        txt = "#0f172a" if _is_light(self.cfg) else C_TEXT
        dim = "#64748b" if _is_light(self.cfg) else C_DIM

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        # Header
        header = QHBoxLayout()
        title = QLabel("Session Audit")
        title.setStyleSheet(f"font-size: 22px; font-weight: 800; color: {txt}; background: transparent;")
        header.addWidget(title)
        header.addStretch(1)

        # Session selector
        self.session_combo = QComboBox()
        self.session_combo.setMinimumWidth(200)
        self.session_combo.setStyleSheet(
            f"QComboBox{{background:{C_PANEL};color:{txt};border:1px solid rgba(255,255,255,0.1);"
            f"border-radius:6px;padding:4px 10px;font-size:12px;}}"
        )
        self.session_combo.currentIndexChanged.connect(self._on_session_changed)
        header.addWidget(self.session_combo)

        # Event type filter
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("All Events")
        for etype in sorted(_EVENT_STYLE.keys()):
            self.filter_combo.addItem(etype)
        self.filter_combo.setStyleSheet(
            f"QComboBox{{background:{C_PANEL};color:{txt};border:1px solid rgba(255,255,255,0.1);"
            f"border-radius:6px;padding:4px 10px;font-size:12px;}}"
        )
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        header.addWidget(self.filter_combo)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setStyleSheet(
            f"QPushButton{{background:{C_PANEL};color:{txt};border:1px solid rgba(255,255,255,0.1);"
            f"border-radius:6px;padding:4px 14px;font-size:12px;}}"
            f"QPushButton:hover{{background:{C_PANEL_HI};}}"
        )
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        root.addLayout(header)

        # Stats summary bar
        self.stats_bar = QLabel("")
        self.stats_bar.setStyleSheet(
            f"color:{dim};font-size:12px;background:{C_PANEL};border-radius:6px;padding:6px 12px;"
        )
        root.addWidget(self.stats_bar)

        # Event list
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        self.events_container = QWidget()
        self.events_layout = QVBoxLayout(self.events_container)
        self.events_layout.setContentsMargins(0, 0, 0, 0)
        self.events_layout.setSpacing(0)
        self.scroll.setWidget(self.events_container)
        root.addWidget(self.scroll, 1)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(100, self.refresh)

    def refresh(self):
        """Load task logs and populate session list."""
        if not self.log_dir.exists():
            return

        self.session_combo.blockSignals(True)
        current_text = self.session_combo.currentText()
        self.session_combo.clear()

        log_files = sorted(self.log_dir.glob("task_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

        for f in log_files[:50]:  # Max 50 sessions
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                entries = data.get("entries", [])
                task_id = ""
                goal = ""
                for e in entries:
                    if e.get("event_type") == "task_start":
                        task_id = e["data"].get("task_id", "")
                        goal = e["data"].get("goal", "")[:40]
                        break
                label = f"{task_id} - {goal}" if task_id else f.name
                self.session_combo.addItem(label, str(f))
            except Exception:
                continue

        # Restore previous selection
        if current_text:
            idx = self.session_combo.findText(current_text)
            if idx >= 0:
                self.session_combo.setCurrentIndex(idx)

        self.session_combo.blockSignals(False)

        if self.session_combo.count() > 0:
            self._on_session_changed()

    def _on_session_changed(self):
        """Load events for the selected session."""
        log_path = self.session_combo.currentData()
        if not log_path:
            return

        self._all_events = []
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("entries", [])
            for e in entries:
                self._all_events.append({
                    "type": e.get("event_type", "unknown"),
                    "timestamp": e.get("timestamp", ""),
                    "data": e.get("data", {}),
                })
        except Exception:
            return

        self._apply_filter()

    def _apply_filter(self):
        """Filter events by type and render."""
        filter_type = self.filter_combo.currentText()
        events = self._all_events
        if filter_type != "All Events":
            events = [e for e in events if e["type"] == filter_type]

        # Update stats
        type_counts = {}
        for e in self._all_events:
            t = e["type"]
            type_counts[t] = type_counts.get(t, 0) + 1
        stats_parts = [f"{t}: {c}" for t, c in sorted(type_counts.items(), key=lambda x: -x[1])[:6]]
        self.stats_bar.setText(f"Total: {len(self._all_events)} events | " + " | ".join(stats_parts))

        # Clear and rebuild
        while self.events_layout.count():
            child = self.events_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        for event in events:
            ts_str = event["timestamp"]
            try:
                ts = datetime.fromisoformat(ts_str)
                ts_display = ts.strftime("%H:%M:%S")
            except Exception:
                ts_display = ts_str[:8] if len(ts_str) >= 8 else ts_str

            summary = self._summarize_event(event["type"], event["data"])
            row = AuditEventRow(event["type"], ts_display, summary)
            self.events_layout.addWidget(row)

        self.events_layout.addStretch(1)

    def _summarize_event(self, event_type: str, data: dict) -> str:
        """Generate a human-readable summary for an event."""
        if event_type == "task_start":
            return f"Goal: {data.get('goal', '')[:60]}"
        elif event_type == "task_complete":
            p = data.get("progress", {})
            return f"Completed — {p.get('completed', 0)}/{p.get('total', 0)} steps, {data.get('total_duration_ms', 0)/1000:.1f}s"
        elif event_type == "task_failure":
            return f"Failed: {data.get('error', '')[:50]}"
        elif event_type == "step_start":
            desc = data.get("description", "")
            return f"Step {data.get('step_id', '')}: {desc[:50]}"
        elif event_type == "step_complete":
            return f"Step {data.get('step_id', '')} — {'success' if data.get('success') else 'failed'}, {data.get('duration_ms', 0)}ms"
        elif event_type == "step_failure":
            return f"Step {data.get('step_id', '')} — {data.get('error', '')[:40]}"
        elif event_type == "step_retry":
            return f"Step {data.get('step_id', '')} — retry #{data.get('retry_count', 0)}"
        elif event_type == "tool_call":
            return f"{data.get('tool_name', '?')}({json.dumps(data.get('arguments', {}), ensure_ascii=False)[:40]})"
        elif event_type == "tool_result":
            err = " [ERROR]" if data.get("error") else ""
            return f"{data.get('tool_name', '?')}{err}: {str(data.get('result', ''))[:40]}"
        elif event_type == "reflection":
            return data.get("observation", "")[:60]
        elif event_type == "decision":
            return f"{data.get('decision_point', '')}: chose '{data.get('chosen', '')}'"
        elif event_type == "recovery_action":
            return f"{data.get('action_type', '')}: {json.dumps(data.get('details', {}), ensure_ascii=False)[:40]}"
        elif event_type == "risk_template":
            t = data.get("template", {})
            return f"Risk: {t.get('name', '?')} ({t.get('level', '?')})"
        elif event_type == "memory_hit":
            return f"Injected: {data.get('summary', '')[:50]}"
        else:
            return json.dumps(data, ensure_ascii=False)[:60]
