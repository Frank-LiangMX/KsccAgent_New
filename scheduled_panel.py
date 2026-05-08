"""
Scheduled Tasks Panel - 定时任务面板

展示定时任务列表、执行状态、执行报告。
通过顶栏按钮切换显示，类似 MetricsPanel。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QPushButton, QSizePolicy, QGridLayout,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

from theme import C_TEXT, C_DIM, C_PANEL, C_PANEL_HI, C_ACCENT, C_GREEN, C_RED, C_YELLOW


def _is_light(cfg) -> bool:
    return str(getattr(cfg, "theme", "dark")).lower() == "light"


def _border(light: bool) -> str:
    return "rgba(0,0,0,0.08)" if light else "rgba(255,255,255,0.08)"


def _border_loose(light: bool) -> str:
    return "rgba(0,0,0,0.06)" if light else "rgba(255,255,255,0.06)"


# ── Repeat label mapping ──────────────────────────────────────

REPEAT_LABELS = {
    "daily": "每天",
    "weekday": "工作日",
    "weekly": "每周",
    "monthly": "每月",
    "once": "仅一次",
}


def _repeat_label(repeat: str) -> str:
    if repeat in REPEAT_LABELS:
        return REPEAT_LABELS[repeat]
    # every_Nh/Nm/Nd
    import re
    m = re.match(r"^every_(\d+)([hmd])$", repeat)
    if m:
        n, unit = m.group(1), m.group(2)
        unit_map = {"h": "小时", "m": "分钟", "d": "天"}
        return f"每{n}{unit_map.get(unit, unit)}"
    return repeat


# ── Task Card Widget ──────────────────────────────────────────

class TaskCard(QFrame):
    """单个定时任务卡片"""

    def __init__(self, task_data: dict, light: bool = False, parent=None):
        super().__init__(parent)
        self.task_data = task_data
        self._light = light
        self.setObjectName("taskCard")
        self._setup_ui()

    def _setup_ui(self):
        light = self._light
        txt = "#0f172a" if light else C_TEXT
        dim = "#475569" if light else "#b0bec5"
        accent = "#0c4a6e" if light else C_ACCENT
        data = self.task_data
        enabled = data.get("enabled", True)
        bdr = _border(light)

        self.setStyleSheet(
            f"QFrame#taskCard {{ background: {C_PANEL}; border: 1px solid {bdr};"
            f"border-radius: 10px; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        # Row 1: status icon + name + schedule
        top = QHBoxLayout()
        top.setSpacing(8)

        status_icon = QLabel("●" if enabled else "○")
        status_icon.setStyleSheet(
            f"color: {C_GREEN if enabled else dim}; font-size: 16px; background: transparent;"
        )
        status_icon.setFixedWidth(20)
        top.addWidget(status_icon)

        name = data.get("name", "未命名")
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet(
            f"color: {txt}; font-size: 14px; font-weight: 700; background: transparent;"
        )
        top.addWidget(name_lbl, 1)

        repeat = data.get("repeat", "daily")
        repeat_lbl = QLabel(_repeat_label(repeat))
        repeat_lbl.setStyleSheet(
            f"color: {accent}; font-size: 11px; font-weight: 600; background: transparent;"
            f"padding: 2px 8px; border-radius: 4px; border: 1px solid {accent}40;"
        )
        top.addWidget(repeat_lbl)

        schedule = data.get("schedule", "")
        sched_lbl = QLabel(schedule)
        sched_lbl.setStyleSheet(
            f"color: {txt}; font-size: 12px; background: transparent;"
        )
        top.addWidget(sched_lbl)

        layout.addLayout(top)

        # Row 2: prompt preview
        prompt = data.get("prompt", "")
        if prompt:
            prompt_lbl = QLabel(prompt[:100] + ("..." if len(prompt) > 100 else ""))
            prompt_lbl.setStyleSheet(
                f"color: {dim}; font-size: 12px; background: transparent;"
            )
            prompt_lbl.setWordWrap(True)
            layout.addWidget(prompt_lbl)

        # Row 3: last run info
        bottom = QHBoxLayout()
        bottom.setSpacing(16)

        last_run = data.get("last_run")
        if last_run:
            try:
                lr_dt = datetime.fromisoformat(last_run)
                lr_text = lr_dt.strftime("%m-%d %H:%M")
            except (ValueError, TypeError):
                lr_text = str(last_run)[:16]
        else:
            lr_text = "从未执行"

        lr_label = QLabel(f"上次执行: {lr_text}")
        lr_label.setStyleSheet(f"color: {dim}; font-size: 11px; background: transparent;")
        bottom.addWidget(lr_label)

        last_status = data.get("last_status")
        if last_status:
            sc = C_GREEN if last_status == "ok" else (C_RED if last_status == "failed" else C_YELLOW)
            st_lbl = QLabel(last_status)
            st_lbl.setStyleSheet(f"color: {sc}; font-size: 11px; font-weight: 600; background: transparent;")
            bottom.addWidget(st_lbl)

        bottom.addStretch(1)

        # Delay info
        max_delay = data.get("max_delay_hours", 6)
        delay_lbl = QLabel(f"延迟上限: {max_delay}h")
        delay_lbl.setStyleSheet(f"color: {dim}; font-size: 11px; background: transparent;")
        bottom.addWidget(delay_lbl)

        layout.addLayout(bottom)

    def update_task(self, task_data: dict):
        """Update the card with new task data."""
        self.task_data = task_data
        # Rebuild UI
        while self.layout().count():
            child = self.layout().takeAt(0)
            if child.widget():
                child.widget().deleteLater()
            elif child.layout():
                while child.layout().count():
                    sub = child.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()
        self._setup_ui()


# ── Report Card Widget ────────────────────────────────────────

class ReportCard(QFrame):
    """单个执行报告卡片"""

    def __init__(self, report: dict, light: bool = False, parent=None):
        super().__init__(parent)
        self.report = report
        self._light = light
        self.setObjectName("reportCard")
        self._setup_ui()

    def _setup_ui(self):
        light = self._light
        txt = "#0f172a" if light else C_TEXT
        dim = "#475569" if light else "#b0bec5"
        bdr = _border_loose(light)

        self.setStyleSheet(
            f"QFrame#reportCard {{ background: {C_PANEL}; border: 1px solid {bdr};"
            f"border-radius: 8px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        # Header: time + filename
        header = QHBoxLayout()
        mtime = self.report.get("mtime", "")
        try:
            dt = datetime.fromisoformat(mtime)
            time_text = dt.strftime("%m-%d %H:%M")
        except (ValueError, TypeError):
            time_text = mtime[:16]

        time_lbl = QLabel(time_text)
        time_lbl.setStyleSheet(f"color: {dim}; font-size: 11px; background: transparent;")
        header.addWidget(time_lbl)

        fname = self.report.get("file", "")
        # Extract task name from filename: YYYY-MM-DD_HHMM_taskname.md
        parts = fname.split("_", 3)
        task_name = parts[3].replace(".md", "").replace("_", " ") if len(parts) > 3 else fname
        name_lbl = QLabel(task_name)
        name_lbl.setStyleSheet(f"color: {txt}; font-size: 12px; font-weight: 600; background: transparent;")
        header.addWidget(name_lbl, 1)
        layout.addLayout(header)

        # Content preview
        content = self.report.get("content", "")
        if content:
            preview = content[:200].replace("\n", " ")
            content_lbl = QLabel(preview + ("..." if len(content) > 200 else ""))
            content_lbl.setStyleSheet(f"color: {dim}; font-size: 11px; background: transparent;")
            content_lbl.setWordWrap(True)
            layout.addWidget(content_lbl)


# ── Main Panel ────────────────────────────────────────────────

class ScheduledTasksPanel(QWidget):
    """定时任务面板页面"""

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setObjectName("ScheduledTasksPanel")
        self._setup_ui()
        # Auto-refresh every 30s
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(30000)
        QTimer.singleShot(500, self.refresh)

    def _setup_ui(self):
        light = _is_light(self.cfg)
        txt = "#0f172a" if light else C_TEXT
        dim = "#475569" if light else "#b0bec5"
        accent = "#0c4a6e" if light else C_ACCENT
        bdr = "rgba(0,0,0,0.08)" if light else "rgba(255,255,255,0.08)"

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # Header
        header = QHBoxLayout()
        title = QLabel("Scheduled Tasks")
        title.setStyleSheet(f"font-size: 22px; font-weight: 800; color: {txt}; background: transparent;")
        header.addWidget(title)
        header.addStretch(1)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setMinimumHeight(32)
        self._update_refresh_btn_style()
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)
        root.addLayout(header)

        # Stat cards row
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(12)

        self.card_total = self._make_stat_card("Total Tasks", "0", accent, txt, dim, bdr)
        self.card_enabled = self._make_stat_card("Enabled", "0", C_GREEN, txt, dim, bdr)
        self.card_due = self._make_stat_card("Next Due", "-", C_YELLOW, txt, dim, bdr)
        self.card_reports = self._make_stat_card("Reports", "0", accent, txt, dim, bdr)

        for card in (self.card_total, self.card_enabled, self.card_due, self.card_reports):
            cards_layout.addWidget(card)
        root.addLayout(cards_layout)

        # Tasks section
        tasks_label = QLabel("Tasks")
        tasks_label.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {txt}; background: transparent;")
        root.addWidget(tasks_label)

        self.tasks_container = QWidget()
        self.tasks_container.setStyleSheet("background:transparent;")
        self.tasks_layout = QVBoxLayout(self.tasks_container)
        self.tasks_layout.setContentsMargins(0, 0, 0, 0)
        self.tasks_layout.setSpacing(8)
        root.addWidget(self.tasks_container)

        # Reports section with its own scroll area — no height limit
        reports_label = QLabel("Execution History")
        reports_label.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {txt}; background: transparent;")
        root.addWidget(reports_label)

        self.reports_scroll = QScrollArea()
        self.reports_scroll.setWidgetResizable(True)
        self.reports_scroll.setMinimumHeight(200)
        self.reports_scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        reports_content = QWidget()
        reports_content.setStyleSheet("background:transparent;")
        self.reports_layout = QVBoxLayout(reports_content)
        self.reports_layout.setContentsMargins(0, 0, 0, 0)
        self.reports_layout.setSpacing(6)
        self.reports_scroll.setWidget(reports_content)
        root.addWidget(self.reports_scroll, 1)

    def _make_stat_card(self, title: str, value: str, color: str,
                        txt: str, dim: str, bdr: str) -> QFrame:
        """Create a stat card widget."""
        card = QFrame()
        card.setObjectName("statCard")
        card.setFixedHeight(80)
        card.setStyleSheet(
            f"QFrame#statCard {{ background: {C_PANEL}; border: 1px solid {bdr};"
            f"border-radius: 10px; }}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 10, 16, 8)
        layout.setSpacing(2)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {dim}; font-size: 11px; font-weight: 600; background: transparent;")
        layout.addWidget(title_lbl)

        value_lbl = QLabel(value)
        value_lbl.setObjectName("statValue")
        value_lbl.setStyleSheet(f"color: {color}; font-size: 24px; font-weight: 800; background: transparent;")
        layout.addWidget(value_lbl)

        return card

    def _update_stat_card(self, card: QFrame, value: str, color: str = ""):
        """Update a stat card's value."""
        lbl = card.findChild(QLabel, "statValue")
        if lbl:
            lbl.setText(value)
            if color:
                lbl.setStyleSheet(f"color: {color}; font-size: 24px; font-weight: 800; background: transparent;")

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(100, self.refresh)

    def _update_refresh_btn_style(self):
        light = _is_light(self.cfg)
        dim = "#333333" if light else C_DIM
        hover_bg = "rgba(0,0,0,0.06)" if light else "rgba(255,255,255,0.08)"
        hi_text = "#000000" if light else C_TEXT
        self.refresh_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;border-radius:8px;"
            f"padding:0 10px;color:{dim};font-size:12px;font-weight:500}}"
            f"QPushButton:hover{{background:{hover_bg};color:{hi_text}}}"
        )

    def refresh(self):
        """Read task configs and reports, update UI."""
        scheduler = getattr(self.cfg, "_scheduler", None)
        if not scheduler:
            return

        light = _is_light(self.cfg)
        self._update_refresh_btn_style()
        txt = "#0f172a" if light else C_TEXT
        dim = "#475569" if light else "#b0bec5"

        # ── Tasks ──
        tasks = scheduler.list_tasks()
        total = len(tasks)
        enabled_count = sum(1 for t in tasks if t.enabled)

        self._update_stat_card(self.card_total, str(total))

        enabled_color = C_GREEN if enabled_count > 0 else dim
        self._update_stat_card(self.card_enabled, str(enabled_count), enabled_color)

        # Find next due task
        next_due = self._find_next_due(tasks)
        self._update_stat_card(self.card_due, next_due, C_YELLOW if next_due != "-" else dim)

        # Rebuild task list
        self._clear_layout(self.tasks_layout)
        if not tasks:
            empty = QLabel("No scheduled tasks. Use chat to create one.")
            empty.setStyleSheet(f"color: {dim}; font-size: 13px; background: transparent; padding: 20px;")
            self.tasks_layout.addWidget(empty)
        else:
            for task in tasks:
                card = TaskCard(task.to_dict(), light=light)
                self.tasks_layout.addWidget(card)
        self.tasks_layout.addStretch(1)

        # ── Reports ──
        reports = scheduler.get_reports(limit=10)
        self._update_stat_card(self.card_reports, str(len(reports)))

        self._clear_layout(self.reports_layout)
        if not reports:
            empty = QLabel("No reports yet.")
            empty.setStyleSheet(f"color: {dim}; font-size: 12px; background: transparent; padding: 8px;")
            self.reports_layout.addWidget(empty)
        else:
            for report in reports:
                card = ReportCard(report, light=light)
                self.reports_layout.addWidget(card)
        self.reports_layout.addStretch(1)

    def _find_next_due(self, tasks) -> str:
        """Find the next task that will fire."""
        now = datetime.now()
        candidates = []
        for t in tasks:
            if not t.enabled:
                continue
            # Simple: just show the schedule time for calendar tasks
            if ":" in t.schedule:
                candidates.append(t.schedule)
        if candidates:
            # Sort and find the next one after now
            now_str = now.strftime("%H:%M")
            future = [c for c in sorted(candidates) if c >= now_str]
            if future:
                return future[0]
            return candidates[0]  # Tomorrow
        return "-"

    @staticmethod
    def _clear_layout(layout):
        """Remove all widgets from a layout."""
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
