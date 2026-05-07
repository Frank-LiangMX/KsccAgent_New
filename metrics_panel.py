"""
P5-3: Metrics Dashboard - 指标看板

聚合任务日志，展示成功率、耗时、工具错误率、token 使用等指标。
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QSizePolicy, QComboBox, QGridLayout, QPushButton,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush

from theme import C_TEXT, C_DIM, C_PANEL, C_PANEL_HI, C_ACCENT, C_GREEN, C_RED, C_YELLOW, build_combobox_stylesheet


def _is_light(cfg) -> bool:
    return str(getattr(cfg, "theme", "dark")).lower() == "light"


class StatCard(QFrame):
    """单个指标卡片"""

    def __init__(self, title: str, value: str, subtitle: str = "", color: str = C_ACCENT, parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")
        self.setFixedHeight(100)
        self.setStyleSheet(
            f"QFrame#statCard {{ background: {C_PANEL}; border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 10)
        layout.setSpacing(2)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {C_DIM}; font-size: 12px; font-weight: 600; background: transparent;")
        layout.addWidget(title_lbl)

        self.value_lbl = QLabel(value)
        self.value_lbl.setStyleSheet(f"color: {color}; font-size: 28px; font-weight: 800; background: transparent;")
        layout.addWidget(self.value_lbl)

        self.sub_lbl = QLabel(subtitle)
        self.sub_lbl.setStyleSheet(f"color: {C_DIM}; font-size: 11px; background: transparent;")
        layout.addWidget(self.sub_lbl)

    def update_value(self, value: str, subtitle: str = "", color: str = ""):
        self.value_lbl.setText(value)
        if subtitle:
            self.sub_lbl.setText(subtitle)
        if color:
            self.value_lbl.setStyleSheet(f"color: {color}; font-size: 28px; font-weight: 800; background: transparent;")


class MiniBarChart(QWidget):
    """简单柱状图"""

    def __init__(self, title: str, data: list[tuple[str, float]], color: str = C_ACCENT, parent=None):
        super().__init__(parent)
        self.title = title
        self.data = data  # [(label, value), ...]
        self.color = color
        self.setMinimumHeight(190)

    def set_data(self, data: list[tuple[str, float]]):
        self.data = data
        self.update()

    def paintEvent(self, event):
        if not self.data:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        margin_left = 40
        margin_right = 16
        margin_top = 28
        margin_bottom = 42
        chart_w = w - margin_left - margin_right
        chart_h = h - margin_top - margin_bottom

        # Title
        painter.setPen(QPen(QColor(C_DIM), 1))
        font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(margin_left, 18, self.title)

        if not self.data:
            painter.end()
            return

        max_val = max(v for _, v in self.data) if self.data else 1
        if max_val == 0:
            max_val = 1

        n = len(self.data)
        bar_w = max(8, min(40, chart_w // n - 4))
        gap = (chart_w - bar_w * n) / (n + 1) if n > 0 else 0

        # Draw bars
        for i, (label, value) in enumerate(self.data):
            x = margin_left + gap + i * (bar_w + gap)
            bar_h = (value / max_val) * chart_h
            y = margin_top + chart_h - bar_h

            color = QColor(self.color)
            color.setAlpha(200)
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(Qt.PenStyle.NoPen))
            painter.drawRoundedRect(int(x), int(y), int(bar_w), int(bar_h), 3, 3)

            # Label
            painter.setPen(QPen(QColor(C_DIM), 1))
            small_font = QFont("Segoe UI", 8)
            painter.setFont(small_font)
            painter.drawText(
                int(x) - 8,
                margin_top + chart_h + 10,
                int(bar_w) + 16,
                26,
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                label[:10],
            )

        painter.end()


class MetricsPanel(QWidget):
    """指标看板页面"""

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setObjectName("MetricsPanel")
        ws = getattr(cfg, 'workspace', '') or str(Path(__file__).resolve().parent)
        self.log_dir = Path(ws) / "logs" / "tasks"
        self._setup_ui()
        # Auto-refresh every 30s
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(30000)
        # Initial load on first show
        QTimer.singleShot(500, self.refresh)

    def _setup_ui(self):
        txt = "#0f172a" if _is_light(self.cfg) else C_TEXT
        dim = "#64748b" if _is_light(self.cfg) else C_DIM
        combo_css = build_combobox_stylesheet("light" if _is_light(self.cfg) else "dark")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # Header
        header = QHBoxLayout()
        title = QLabel("Metrics Dashboard")
        title.setStyleSheet(f"font-size: 22px; font-weight: 800; color: {txt}; background: transparent;")
        header.addWidget(title)
        header.addStretch(1)

        self.range_combo = QComboBox()
        self.range_combo.addItems(["Last 7 days", "Last 30 days", "All time"])
        self.range_combo.setMaxVisibleItems(12)
        self.range_combo.setStyleSheet(combo_css)
        self.range_combo.currentIndexChanged.connect(self.refresh)
        header.addWidget(self.range_combo)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setStyleSheet(
            f"QPushButton{{background:{C_PANEL};color:{txt};border:1px solid rgba(255,255,255,0.1);"
            f"border-radius:6px;padding:4px 14px;font-size:12px;}}"
            f"QPushButton:hover{{background:{C_PANEL_HI};}}"
        )
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        root.addLayout(header)

        # Stat cards row
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(12)

        self.card_tasks = StatCard("Total Tasks", "0", "no data", C_ACCENT)
        self.card_success = StatCard("Success Rate", "0%", "no data", C_GREEN)
        self.card_duration = StatCard("Avg Duration", "0s", "no data", C_YELLOW)
        self.card_tools = StatCard("Tool Calls", "0", "no data", C_ACCENT)

        for card in (self.card_tasks, self.card_success, self.card_duration, self.card_tools):
            cards_layout.addWidget(card)
        root.addLayout(cards_layout)

        # Charts row
        charts_layout = QHBoxLayout()
        charts_layout.setSpacing(12)

        self.chart_daily = MiniBarChart("Daily Task Count", [], C_ACCENT)
        self.chart_category = MiniBarChart("By Category", [], C_GREEN)
        self.chart_errors = MiniBarChart("Error Types", [], C_RED)

        for chart in (self.chart_daily, self.chart_category, self.chart_errors):
            charts_layout.addWidget(chart)
        root.addLayout(charts_layout)

        # Recent failures list
        fail_label = QLabel("Recent Failures")
        fail_label.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {txt}; background: transparent;")
        root.addWidget(fail_label)

        self.failures_scroll = QScrollArea()
        self.failures_scroll.setWidgetResizable(True)
        self.failures_scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        self.failures_container = QWidget()
        self.failures_layout = QVBoxLayout(self.failures_container)
        self.failures_layout.setContentsMargins(0, 0, 0, 0)
        self.failures_layout.setSpacing(4)
        self.failures_scroll.setWidget(self.failures_container)
        root.addWidget(self.failures_scroll, 1)

        root.addStretch(0)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(100, self.refresh)

    def refresh(self):
        """Read task logs and update all metrics."""
        if not self.log_dir.exists():
            return

        # Determine time range
        range_text = self.range_combo.currentText()
        now = datetime.now()
        if "7" in range_text:
            cutoff = now - timedelta(days=7)
        elif "30" in range_text:
            cutoff = now - timedelta(days=30)
        else:
            cutoff = datetime.min

        # Parse all log files
        logs = []
        for f in sorted(self.log_dir.glob("task_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                entries = data.get("entries", [])
                log_info = self._parse_log(entries, f)
                if log_info and log_info.get("timestamp") and log_info["timestamp"] >= cutoff:
                    logs.append(log_info)
            except Exception:
                continue

        if not logs:
            return

        # Update stat cards
        total = len(logs)
        successes = sum(1 for l in logs if l.get("success"))
        success_rate = successes / total * 100 if total else 0
        avg_duration = sum(l.get("duration_s", 0) for l in logs) / total if total else 0
        total_tools = sum(l.get("tool_count", 0) for l in logs)

        self.card_tasks.update_value(str(total), f"{range_text.lower()}")
        color = C_GREEN if success_rate >= 80 else (C_YELLOW if success_rate >= 50 else C_RED)
        self.card_success.update_value(f"{success_rate:.0f}%", f"{successes}/{total} passed", color)
        self.card_duration.update_value(f"{avg_duration:.1f}s", "per task")
        self.card_tools.update_value(str(total_tools), f"{total_tools/total:.1f}/task" if total else "")

        # Daily task count chart
        daily = Counter()
        for l in logs:
            day = l["timestamp"].strftime("%m-%d")
            daily[day] += 1
        daily_data = sorted(daily.items())[-14:]  # last 14 days
        self.chart_daily.set_data(daily_data)

        # Category chart
        categories = Counter()
        for l in logs:
            cat = l.get("category", "unknown")
            categories[cat] += 1
        cat_data = categories.most_common(6)
        self.chart_category.set_data(cat_data)

        # Error types chart
        errors = Counter()
        for l in logs:
            if not l.get("success"):
                err = l.get("error_type", "unknown")
                errors[err] += 1
        err_data = errors.most_common(6)
        self.chart_errors.set_data(err_data if err_data else [("none", 0)])

        # Recent failures
        # Clear existing
        while self.failures_layout.count():
            child = self.failures_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        failures = [l for l in logs if not l.get("success")][:10]
        dim = "#64748b" if _is_light(self.cfg) else C_DIM
        txt = "#0f172a" if _is_light(self.cfg) else C_TEXT

        for fail in failures:
            row = QFrame()
            row.setStyleSheet(f"QFrame{{background:{C_PANEL};border-radius:6px;padding:4px;}}")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 6, 10, 6)

            ts = fail["timestamp"].strftime("%m-%d %H:%M")
            ts_lbl = QLabel(ts)
            ts_lbl.setStyleSheet(f"color:{dim};font-size:11px;background:transparent;")
            ts_lbl.setFixedWidth(80)
            row_layout.addWidget(ts_lbl)

            goal = fail.get("goal", "")[:60]
            goal_lbl = QLabel(goal)
            goal_lbl.setStyleSheet(f"color:{txt};font-size:12px;background:transparent;")
            row_layout.addWidget(goal_lbl, 1)

            err = fail.get("error", "")[:40]
            err_lbl = QLabel(err)
            err_lbl.setStyleSheet(f"color:{C_RED};font-size:11px;background:transparent;")
            err_lbl.setFixedWidth(160)
            row_layout.addWidget(err_lbl)

            self.failures_layout.addWidget(row)

        self.failures_layout.addStretch(1)

    def _parse_log(self, entries: list[dict], path: Path) -> Optional[dict]:
        """Parse a task log file into a summary dict."""
        info = {}

        for e in entries:
            etype = e.get("event_type", "")
            data = e.get("data", {})
            ts_str = e.get("timestamp", "")

            if etype == "task_start":
                info["task_id"] = data.get("task_id", "")
                info["goal"] = data.get("goal", "")
                try:
                    info["timestamp"] = datetime.fromisoformat(ts_str)
                except Exception:
                    info["timestamp"] = datetime.fromtimestamp(path.stat().st_mtime)

            elif etype == "task_complete":
                info["success"] = True
                info["duration_s"] = data.get("total_duration_ms", 0) / 1000
                info["total_steps"] = data.get("total_steps", 0)
                info["failed_steps"] = data.get("failed_steps", 0)

            elif etype == "task_failure":
                info["success"] = False
                info["duration_s"] = data.get("total_duration_ms", 0) / 1000
                info["error"] = data.get("error", "")[:100]
                info["error_type"] = self._classify_error(data.get("error", ""))

            elif etype == "tool_call":
                info["tool_count"] = info.get("tool_count", 0) + 1

        if "timestamp" not in info:
            try:
                info["timestamp"] = datetime.fromtimestamp(path.stat().st_mtime)
            except Exception:
                return None

        return info if info.get("task_id") else None

    def _classify_error(self, error: str) -> str:
        """Classify error into a short category."""
        error_lower = error.lower()
        if "timeout" in error_lower or "timed out" in error_lower:
            return "timeout"
        if "token" in error_lower or "context" in error_lower:
            return "token_limit"
        if "rate" in error_lower or "429" in error_lower:
            return "rate_limit"
        if "connection" in error_lower or "network" in error_lower:
            return "network"
        if "permission" in error_lower or "access" in error_lower:
            return "permission"
        return "other"
