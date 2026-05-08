"""
Chat Panel - 聊天面板（包含输入框、消息列表、模型选择）
从 app.py 提取：ChatPanel
"""

import os
import re
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QSizePolicy, QFileDialog, QMenu, QToolTip, QTextEdit,
)
from PyQt6.QtCore import Qt, QTimer, QPoint, QSize, pyqtSignal, QRect
from PyQt6.QtGui import QFont, QFontMetrics, QPixmap, QColor, QPainter, QLinearGradient

from ui_common import (
    _is_light_theme, _with_tooltip_style, _make_plus_icon,
    _ensure_attachments_dir, _attachment_meta, _CODE_FONT_STACK,
)
from config import load_config, KSCC_MODELS
from theme import (
    quark_icon, C_ACCENT, C_DIM, C_PANEL, C_PANEL_HI,
    C_TEXT, C_TEAL, C_ACCENT_LIGHT, C_TEAL_LIGHT,
    C_ACCENT_SEL, C_ACCENT_SEL_LIGHT,
    polish_menu,
)
from chat_widgets import (
    ChatBubble, ChatInputEdit, ComposerAttachmentChip,
    ContextRingWidget,
)

# ── 复杂任务检测 ─────────────────────────────────────────
_TASK_STEP_MARKERS = [
    "然后", "接着", "之后", "最后", "并且", "同时",
    "first", "then", "next", "after that", "finally", "and also",
]
_TASK_ACTION_VERBS = [
    "创建", "生成", "编写", "部署", "配置", "安装", "搭建", "迁移", "重构",
    "优化", "监控", "自动化", "批量", "定时", "备份", "同步", "集成",
    "create", "build", "deploy", "configure", "install", "setup", "migrate",
    "refactor", "optimize", "monitor", "automate", "batch", "backup", "sync",
    "analyze", "investigate", "fix", "implement", "integrate", "test",
]


def _is_complex_task(text: str) -> bool:
    """启发式判断用户输入是否是多步骤复杂任务。"""
    if not text:
        return False
    t = text.lower()
    # 1. 包含编号列表（"1. xxx 2. xxx" 或 "1、xxx 2、xxx"）
    if len(re.findall(r'(?:^|\n)\s*(?:\d+[\.\)、])', t)) >= 2:
        return True
    # 2. 包含多个步骤标记词
    step_hits = sum(1 for m in _TASK_STEP_MARKERS if m in t)
    if step_hits >= 2:
        return True
    # 3. 长文本 + 至少一个动作动词
    verb_hits = sum(1 for v in _TASK_ACTION_VERBS if v in t)
    if len(t) > 80 and verb_hits >= 2:
        return True
    # 4. 包含多个动作动词（>=3）
    if verb_hits >= 3:
        return True
    # 5. 含"并且...还"或"不仅要...还要"等并列结构
    if ("并且" in t and "还" in t) or ("不仅" in t and "还要" in t):
        return True
    if ("not only" in t and "but also" in t) or ("and also" in t):
        return True
    short_multi = re.split(r"[，,。.;；]| and | then | 然后 | 并且 ", t)
    short_multi = [seg.strip() for seg in short_multi if seg.strip()]
    if len(t) <= 64 and len(short_multi) >= 3 and verb_hits >= 2:
        return True
    return False


class TaskSuggestionBar(QFrame):
    """悬浮在输入框上方的任务模式建议条。"""
    enable_task_mode = pyqtSignal()   # 用户点击"开启 Task 模式"
    send_anyway = pyqtSignal()        # 用户点击"直接发送"
    dismissed = pyqtSignal()          # 用户关闭

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("taskSuggestionBar")
        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.ArrowCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(10)

        icon_lbl = QLabel("⚡")
        icon_lbl.setFixedWidth(20)
        layout.addWidget(icon_lbl)

        msg_lbl = QLabel("检测到复杂任务，建议开启 Task 模式以获得更可靠的分步执行")
        msg_lbl.setWordWrap(True)
        layout.addWidget(msg_lbl, 1)

        self._enable_btn = QPushButton("开启 Task 模式")
        self._enable_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._enable_btn.setFixedHeight(28)
        self._enable_btn.clicked.connect(self.enable_task_mode.emit)
        layout.addWidget(self._enable_btn)

        self._send_btn = QPushButton("直接发送")
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setFixedHeight(28)
        self._send_btn.clicked.connect(self.send_anyway.emit)
        layout.addWidget(self._send_btn)

        dismiss_btn = QPushButton("✕")
        dismiss_btn.setFixedSize(24, 24)
        dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss_btn.clicked.connect(self.dismissed.emit)
        layout.addWidget(dismiss_btn)

        self.hide()

    def apply_theme(self, light: bool):
        bg = "rgba(255,200,50,0.12)" if light else "rgba(255,200,50,0.08)"
        border = "1px solid rgba(255,200,50,0.3)" if light else "1px solid rgba(255,200,50,0.15)"
        text = "#1a1a1a" if light else "#e0e0e0"
        accent = "#d4a017" if light else "#f0c040"
        self.setStyleSheet(
            f"#taskSuggestionBar {{background:{bg};border:{border};border-radius:10px;}}"
            f"QLabel{{color:{text};font-size:12px;background:transparent;}}"
            f"QPushButton{{background:transparent;border:1px solid {accent};color:{accent};"
            f"border-radius:6px;padding:2px 10px;font-size:11px;}}"
            f"QPushButton:hover{{background:rgba(255,200,50,0.15);}}"
        )


class ComposerRunHalo(QWidget):
    """Input card top-edge shimmer while agent is running."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._phase = 0
        self._timer = QTimer(self)
        self._timer.setInterval(14)
        self._timer.timeout.connect(self._tick)
        self._top = -2
        self._height = 2
        self._margin_x = 24
        self._bar_w = 392.0
        self._glow_pad = 36.0
        self._x = 0.0
        self._vx = 4.5
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def set_running(self, running: bool):
        running = bool(running)
        if self._running == running:
            return
        self._running = running
        if running:
            self._phase = 0
            self._x = 0.0
            self._vx = abs(self._vx) if self._vx else 4.5
            self.show()
            self.raise_()
            self._timer.start()
            self.update()
        else:
            self._timer.stop()
            self.hide()

    def _tick(self):
        self._phase = (self._phase + 1) % 2000
        self.update()

    def paintEvent(self, event):
        if not self._running:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        body = self.rect()
        body.setLeft(body.left() + self._margin_x)
        body.setRight(body.right() - self._margin_x)
        body.setTop(max(0, self._top + 1))
        body.setHeight(self._height)
        if body.width() <= 0 or body.height() <= 0:
            return
        visual_w = self._bar_w + self._glow_pad * 2.0
        max_x = max(0.0, float(body.width()) - visual_w)
        self._x += self._vx
        if self._x < 0.0:
            self._x = -self._x
            self._vx = abs(self._vx)
        elif self._x > max_x:
            self._x = max_x - (self._x - max_x)
            self._vx = -abs(self._vx)
        bar_w = int(min(self._bar_w, max(1.0, float(body.width()))))
        left = int(round(body.left() + self._glow_pad + self._x))
        top = body.top()
        bar_rect = QRect(left, top, bar_w, body.height())
        if bar_rect.width() <= 1:
            return
        # Keep the middle clearly stronger than the sides, but soften the
        # center plateau and extend the side falloff for a gentler ribbon.
        g = QLinearGradient(bar_rect.left(), bar_rect.top(), bar_rect.right(), bar_rect.top())
        g.setColorAt(0.00, QColor(59, 130, 246, 0))
        g.setColorAt(0.04, QColor(59, 130, 246, 8))
        g.setColorAt(0.14, QColor(59, 130, 246, 18))
        g.setColorAt(0.26, QColor(59, 130, 246, 42))
        g.setColorAt(0.38, QColor(59, 130, 246, 78))
        g.setColorAt(0.46, QColor(59, 130, 246, 104))
        g.setColorAt(0.50, QColor(59, 130, 246, 120))
        g.setColorAt(0.54, QColor(59, 130, 246, 104))
        g.setColorAt(0.62, QColor(59, 130, 246, 78))
        g.setColorAt(0.74, QColor(59, 130, 246, 42))
        g.setColorAt(0.86, QColor(59, 130, 246, 18))
        g.setColorAt(0.96, QColor(59, 130, 246, 8))
        g.setColorAt(1.00, QColor(59, 130, 246, 0))
        p.fillRect(bar_rect, g)


class ChatPanel(QWidget):
    send_message = pyqtSignal(str, object)
    add_skill_requested = pyqtSignal()
    task_suggestion_accepted = pyqtSignal(str, object)  # (text, attachments)
    task_mode_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(260)
        self._pending_attachments: list[dict] = []
        self._diff_cards: dict[str, dict] = {}
        self._empty_mode = False
        self._bulk_restore = False
        self._deferred_render_bubbles = []
        self._deferred_render_running = False
        self._task_mode_enabled = False
        self._mode_switch_locked = False
        l = QVBoxLayout(self)
        self._root_layout = l
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(0)
        self._empty_top_spacer = QWidget()
        self._empty_top_spacer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._empty_top_spacer.hide()
        l.addWidget(self._empty_top_spacer, 1)

        self._empty_intro = QWidget()
        ei = QVBoxLayout(self._empty_intro)
        ei.setContentsMargins(24, 0, 24, 10)
        ei.setSpacing(6)
        self._empty_workspace_lbl = QLabel("")
        self._empty_workspace_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._empty_title_lbl = QLabel("Start a new session")
        self._empty_title_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._empty_subtitle_lbl = QLabel("Plan, build, and iterate from here.")
        self._empty_subtitle_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._empty_subtitle_lbl.setWordWrap(True)
        ei.addWidget(self._empty_workspace_lbl)
        ei.addWidget(self._empty_title_lbl)
        ei.addWidget(self._empty_subtitle_lbl)
        self._empty_intro.hide()
        l.addWidget(self._empty_intro, 0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        try:
            # Lazy-render deferred markdown when the user scrolls into history.
            self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_value_changed)
        except Exception:
            pass
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setStyleSheet("background:transparent;")
        self.msg_container = QWidget()
        self.msg_container.setStyleSheet("background: transparent;")
        self.msg_layout = QVBoxLayout(self.msg_container)
        self.msg_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.msg_layout.setSpacing(10)
        self.msg_layout.setContentsMargins(12, 14, 12, 14)
        self.msg_layout.addStretch()
        self.scroll.setWidget(self.msg_container)
        l.addWidget(self.scroll, 1)

        # Status line
        self._status_base_text = ""
        self._status_phase = 0
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(120)
        self._status_timer.timeout.connect(self._tick_status)
        self.kscc_status_lbl = QLabel("")
        self.kscc_status_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.kscc_status_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.kscc_status_lbl.setContentsMargins(0, 0, 0, 0)
        self.kscc_status_lbl.setAutoFillBackground(False)
        self.kscc_status_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.kscc_status_lbl.hide()

        # Composer
        self._model_map: dict[str, list[str]] = {}
        self._backend_key = "Kscc"
        self._model_name = ""

        self.mode_menu_btn = QPushButton("")
        self.mode_menu_btn.setFixedHeight(26)
        self.mode_menu_btn.setMinimumWidth(78)
        self.mode_menu_btn.setMaximumWidth(220)
        self.mode_menu_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mode_menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_menu_btn.clicked.connect(self._show_runtime_menu)

        self._input_card = QFrame()
        self._input_card.setObjectName("inputGlassCard")
        self._run_halo = ComposerRunHalo(self._input_card)
        vl = QVBoxLayout(self._input_card)
        vl.setContentsMargins(10, 8, 10, 8)
        vl.setSpacing(4)

        self.input_edit = ChatInputEdit()
        self.input_edit.setObjectName("composerInput")
        self.input_edit.setPlaceholderText("输入消息…  Ctrl+Enter 发送")
        _ife = QFont("Segoe UI", 10)
        self.input_edit.setFont(_ife)
        _fm = QFontMetrics(_ife)
        self._input_min_h = max(28, _fm.lineSpacing() + 8)
        self._input_max_h = 220
        self.input_edit.send_requested.connect(self._send)
        self.input_edit.image_pasted.connect(self._on_image_pasted)
        self.input_edit.files_dropped.connect(self._on_files_dropped)
        self.input_edit.drag_state_changed.connect(self._set_drag_active)
        self.input_edit.document().contentsChanged.connect(self._adjust_input_height)
        self.input_edit.height_refresh_requested.connect(self._adjust_input_height)

        self.attachments_row = QWidget()
        self.attachments_layout = QHBoxLayout(self.attachments_row)
        self.attachments_layout.setContentsMargins(4, 0, 4, 2)
        self.attachments_layout.setSpacing(6)
        self.attachments_layout.addStretch(1)
        self.attachments_row.hide()

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        toolbar.setContentsMargins(0, 0, 0, 0)

        self._plus_btn = QPushButton("")
        self._plus_btn.setFixedSize(28, 28)
        self._plus_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._plus_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._plus_btn.setIconSize(QSize(14, 14))
        self._plus_btn.setToolTip("添加附件")
        self._plus_btn.clicked.connect(self._pick_attachments)

        self.task_mode_btn = QPushButton("")
        self.task_mode_btn.setCheckable(True)
        self.task_mode_btn.setChecked(False)
        self.task_mode_btn.setFixedHeight(26)
        self.task_mode_btn.setFixedWidth(26)
        self.task_mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.task_mode_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.task_mode_btn.setToolTip("Task 模式：开启后强制按计划执行")
        self.task_mode_btn.toggled.connect(self._on_task_mode_btn_toggled)

        self._context_ring_slot = QWidget()
        self._context_ring_slot.setFixedWidth(24)
        crl = QHBoxLayout(self._context_ring_slot)
        crl.setContentsMargins(2, 0, 2, 0)
        crl.setSpacing(0)

        self.send_btn = QPushButton("↑")
        self.send_btn.setFixedSize(28, 28)
        self.send_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setToolTip("发送")
        self.send_btn.clicked.connect(self._send)

        self.stop_btn = QPushButton("■")
        self.stop_btn.setFixedSize(28, 28)
        self.stop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setStyleSheet(
            _with_tooltip_style(
                "QPushButton {"
                "  background:#f8654b; color:#ffffff; border:none;"
                "  border-radius:14px; font-size:12px;"
                "  padding:0px; font-family:'Segoe UI Symbol','Microsoft YaHei';"
                "}"
                "QPushButton:hover { background:#ff7a63; }"
                "QPushButton:pressed { background:#e65740; }"
            )
        )
        self.stop_btn.setToolTip("停止")
        self.stop_btn.hide()
        # Immediate local stop: avoid lingering run halo if upper-layer stop handling lags.
        self.stop_btn.clicked.connect(self.end_stream)
        self.stop_btn.clicked.connect(lambda: self.set_kscc_status(""))

        toolbar.addWidget(self._plus_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self.task_mode_btn)
        toolbar.addWidget(self._context_ring_slot)
        toolbar.addWidget(self.mode_menu_btn)
        toolbar.addWidget(self.send_btn)
        toolbar.addWidget(self.stop_btn)

        self._refresh_mode_menu_btn_text()
        self._update_task_button_icon()

        vl.addWidget(self.attachments_row)
        vl.addWidget(self.input_edit)
        vl.addLayout(toolbar)

        self.input_wrap = QWidget()
        self.input_wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        iw = QVBoxLayout(self.input_wrap)
        iw.setContentsMargins(12, 6, 12, 12)
        iw.setSpacing(4)
        self._status_row = QWidget()
        self._status_row.setStyleSheet("background:transparent;")
        sr = QHBoxLayout(self._status_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.setSpacing(0)
        sr.addWidget(self.kscc_status_lbl, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        sr.addStretch(1)
        self._status_row.hide()
        self.save_skill_btn = QPushButton("Save Skill")
        self.save_skill_btn.setObjectName("saveSkillBtn")
        self.save_skill_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_skill_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.save_skill_btn.clicked.connect(self.add_skill_requested.emit)
        self.save_skill_btn.hide()
        iw.addWidget(self._status_row)
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.addStretch(1)
        top_bar.addWidget(self.save_skill_btn, 0, Qt.AlignmentFlag.AlignRight)
        iw.addLayout(top_bar)
        iw.addWidget(self._input_card)
        self._composer_host = QWidget()
        self._composer_layout = QHBoxLayout(self._composer_host)
        self._composer_layout.setContentsMargins(0, 0, 0, 0)
        self._composer_layout.setSpacing(0)
        self._composer_left_spacer = QWidget()
        self._composer_left_spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._composer_right_spacer = QWidget()
        self._composer_right_spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._composer_layout.addWidget(self._composer_left_spacer, 1)
        self._composer_layout.addWidget(self.input_wrap, 0)
        self._composer_layout.addWidget(self._composer_right_spacer, 1)
        self._task_suggestion = TaskSuggestionBar()
        self._task_suggestion.enable_task_mode.connect(self._on_task_suggestion_enable)
        self._task_suggestion.send_anyway.connect(self._on_task_suggestion_send_anyway)
        self._task_suggestion.dismissed.connect(self._on_task_suggestion_dismiss)
        self._pending_task_text: str = ""
        self._pending_task_attachments: list = []
        l.addWidget(self._task_suggestion)
        l.addWidget(self._composer_host)

        self._empty_bottom_spacer = QWidget()
        self._empty_bottom_spacer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._empty_bottom_spacer.hide()
        l.addWidget(self._empty_bottom_spacer, 1)
        self._stream_bubble: Optional[ChatBubble] = None
        self._drag_active = False
        self.apply_theme()
        self.set_empty_state(True)
        QTimer.singleShot(0, self._adjust_input_height)

    def apply_theme(self):
        light = _is_light_theme()
        if light:
            card_bg = "rgba(255,255,255,0.96)"
            card_border = "1px solid rgba(0,0,0,0.10)"
            card_drag_bg = "#edf6ff"
            card_drag_border = "2px solid rgba(59,130,246,0.55)"
            menu_fg = "#000000"
            menu_fg_h = "#000000"
            menu_bg_h = "rgba(0,0,0,0.06)"
            input_fg = "#000000"
            plus_bg = "rgba(0,0,0,0.06)"
            plus_bg_h = "rgba(0,0,0,0.10)"
            plus_fg = "#333333"
            plus_fg_h = "#000000"
            plus_icon = "#333333"
            send_bg = "#d1d5db"
            send_fg = "#000000"
            send_hover = "#e5e7eb"
            send_dis_bg = "#9ca3af"
            send_dis_fg = "#1f2937"
        else:
            card_bg = "rgba(32,36,44,0.94)"
            card_border = "none"
            card_drag_bg = "rgba(37,99,235,0.18)"
            card_drag_border = "1px solid rgba(94,233,255,0.45)"
            menu_fg = "#b6bac2"
            menu_fg_h = "#ffffff"
            menu_bg_h = "#313131"
            input_fg = C_TEXT
            plus_bg = "#2d2d2d"
            plus_bg_h = "#3d3d3d"
            plus_fg = "#a9adb5"
            plus_fg_h = "#ffffff"
            plus_icon = "#d4d7dd"
            send_bg = "#d7d9df"
            send_fg = "#1d1f24"
            send_hover = "#ffffff"
            send_dis_bg = "#8a8f99"
            send_dis_fg = "#262a31"
        self._input_card.setStyleSheet(
            f"QFrame#inputGlassCard{{background:{card_drag_bg if self._drag_active else card_bg};"
            f"border:{card_drag_border if self._drag_active else card_border};border-radius:22px}}"
        )
        self.input_edit.setStyleSheet(
            f"QTextEdit#composerInput{{background:transparent;border:none;color:{input_fg};padding:0px 4px}}"
            f"QTextEdit#composerInput:focus{{border:none}}"
        )
        self.mode_menu_btn.setStyleSheet(
            _with_tooltip_style(
                "QPushButton {"
                f"  background:transparent; color:{menu_fg}; border:none;"
                "  border-radius:8px; padding:1px 6px; font-size:11px;"
                "  text-align:center;"
                f"  font-family:{_CODE_FONT_STACK};"
                "}"
                f"QPushButton:hover {{ background:{menu_bg_h}; color:{menu_fg_h}; }}"
            )
        )
        task_icon = "#9aa4b2" if not self._task_mode_enabled else ("#2563eb" if light else "#5ee9ff")
        self.task_mode_btn.setStyleSheet(
            _with_tooltip_style(
                "QPushButton{background:transparent;border:none;border-radius:8px;padding:0px;}"
                "QPushButton:hover{background:rgba(148,163,184,0.10);border:none;}"
                "QPushButton:checked{background:transparent;border:none;}"
                f"QPushButton:disabled{{color:{menu_fg};}}"
            )
        )
        self.task_mode_btn.setIcon(quark_icon("spark", 14, task_icon))
        self.task_mode_btn.setIconSize(QSize(14, 14))
        self._plus_btn.setIcon(_make_plus_icon(size=14, color=plus_icon))
        self._plus_btn.setStyleSheet(
            _with_tooltip_style(
                "QPushButton {"
                f"  background:{plus_bg}; color:{plus_fg}; border:none;"
                "  border-radius:14px;"
                "  padding:0px;"
                "  text-align:center;"
                "}"
                f"QPushButton:hover {{ background:{plus_bg_h}; color:{plus_fg_h}; }}"
            )
        )
        self.send_btn.setStyleSheet(
            _with_tooltip_style(
                "QPushButton {"
                f"  background:{send_bg}; color:{send_fg}; border:none;"
                "  border-radius:14px; font-size:13px; font-weight:bold;"
                "  padding:0px; font-family:'Segoe UI Symbol','Microsoft YaHei';"
                "}"
                f"QPushButton:hover {{ background:{send_hover}; }}"
                f"QPushButton:disabled {{ background:{send_dis_bg}; color:{send_dis_fg}; }}"
            )
        )
        if light:
            chip_bg = "#ffefe5"
            chip_fg = "#9a3412"
            chip_hover = "#ffe1d2"
        else:
            chip_bg = "rgba(245,101,75,0.22)"
            chip_fg = "#ffd4cc"
            chip_hover = "rgba(245,101,75,0.30)"
        self.save_skill_btn.setStyleSheet(
            _with_tooltip_style(
                "QPushButton{"
                f"background:{chip_bg};color:{chip_fg};border:none;border-radius:10px;"
                "padding:4px 10px;font-size:11px;font-weight:700;"
                "}"
                f"QPushButton:hover{{background:{chip_hover};}}"
            )
        )
        if light:
            ws_col = "#64748b"
            title_col = "#0f172a"
            sub_col = "#475569"
        else:
            ws_col = "#8b95a5"
            title_col = "#eef4f8"
            sub_col = "#9aa4b2"
        self._empty_workspace_lbl.setStyleSheet(
            f"color:{ws_col};background:transparent;font-size:12px;font-weight:600;letter-spacing:0.03em;"
        )
        self._empty_title_lbl.setStyleSheet(
            f"color:{title_col};background:transparent;font-size:28px;font-weight:800;"
        )
        self._empty_subtitle_lbl.setStyleSheet(
            f"color:{sub_col};background:transparent;font-size:13px;font-weight:500;"
        )
        self._task_suggestion.apply_theme(light)
        for i in range(self.attachments_layout.count()):
            item = self.attachments_layout.itemAt(i)
            w = item.widget()
            if isinstance(w, ComposerAttachmentChip):
                w.apply_theme()

    def clear_messages(self):
        while self.msg_layout.count():
            item = self.msg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.msg_layout.addStretch()
        self._stream_bubble = None
        self._diff_cards.clear()

    def begin_bulk_restore(self):
        self._bulk_restore = True
        self._deferred_render_bubbles = []
        self._deferred_render_running = False
        self._stream_bubble = None
        self.scroll.hide()
        self.msg_container.setUpdatesEnabled(False)
        self.scroll.setUpdatesEnabled(False)
        self.setUpdatesEnabled(False)

    def end_bulk_restore(self):
        self._bulk_restore = False
        self.setUpdatesEnabled(True)
        self.scroll.setUpdatesEnabled(True)
        self.msg_container.setUpdatesEnabled(True)
        if not self._empty_mode:
            self.scroll.show()
        self.msg_container.adjustSize()
        QTimer.singleShot(0, self._adjust_input_height)
        QTimer.singleShot(0, self._scroll)
        # Render a small tail quickly; then keep draining the rest in the background.
        self._kick_deferred_renders(initial_only=True)
        self._schedule_deferred_render()
        self._update_deferred_status()

    def _kick_deferred_renders(self, initial_only: bool = False):
        """
        Render a small number of deferred assistant bubbles immediately.
        The remaining bubbles are drained by _schedule_deferred_render().
        """
        if not self._deferred_render_bubbles:
            return
        n = 24 if initial_only else 12
        # Bubbles are appended in visual order; render from the end (newest-first).
        batch = []
        for _ in range(min(n, len(self._deferred_render_bubbles))):
            batch.append(self._deferred_render_bubbles.pop())
        for b in batch:
            try:
                b.enable_markdown_render()
            except Exception:
                pass

    def _schedule_deferred_render(self):
        """Drain deferred renders in small batches without blocking the UI thread."""
        if self._bulk_restore:
            return
        if self._deferred_render_running:
            return
        if not self._deferred_render_bubbles:
            return
        self._deferred_render_running = True

        def step():
            if self._bulk_restore:
                self._deferred_render_running = False
                return
            if not self._deferred_render_bubbles:
                self._deferred_render_running = False
                self._update_deferred_status(done=True)
                return
            # Render a modest batch near the current viewport; keep scroll stable.
            self._render_deferred_near_viewport(batch_size=12)
            self._update_deferred_status()
            QTimer.singleShot(10, step)

        QTimer.singleShot(0, step)

    def _update_deferred_status(self, done: bool = False):
        try:
            left = len(self._deferred_render_bubbles or [])
            if done:
                left = 0
            # Avoid spamming; only update when there is pending work.
            if left > 0:
                self.set_kscc_status(f"Rendering history… {left}")
            else:
                # Clear status if we previously showed it.
                if str(getattr(self, "_status_base_text", "") or "").startswith("Rendering history"):
                    self.set_kscc_status("")
        except Exception:
            pass

    def _render_deferred_near_viewport(self, batch_size: int = 12):
        if not self._deferred_render_bubbles:
            return

        # Anchor the first visible message so height changes don't "jump" the scroll position.
        sb = self.scroll.verticalScrollBar()
        before_val = int(sb.value())
        anchor = self._find_anchor_bubble()
        anchor_y_before = None
        if anchor is not None:
            try:
                anchor_y_before = int(anchor.mapTo(self.msg_container, QPoint(0, 0)).y())
            except Exception:
                anchor_y_before = None

        batch = self._pick_deferred_batch_near_viewport(batch_size=batch_size)
        for b in batch:
            try:
                b.enable_markdown_render()
            except Exception:
                pass

        if anchor is not None and anchor_y_before is not None:
            try:
                anchor_y_after = int(anchor.mapTo(self.msg_container, QPoint(0, 0)).y())
                delta = anchor_y_after - anchor_y_before
                if delta:
                    sb.setValue(int(before_val + delta))
            except Exception:
                pass

    def _find_anchor_bubble(self):
        """Return a ChatBubble near the top of the viewport to anchor scroll position."""
        try:
            vp = self.scroll.viewport()
            top_left = vp.mapTo(self.msg_container, QPoint(0, 0))
            # Sample a point slightly below the top to avoid picking margins.
            p = QPoint(20, max(0, top_left.y() + 8))
            w = self.msg_container.childAt(p)
            # childAt may return a nested widget; walk up to ChatBubble.
            while w is not None and not isinstance(w, ChatBubble):
                w = w.parentWidget()
            return w
        except Exception:
            return None

    def _pick_deferred_batch_near_viewport(self, batch_size: int = 12):
        """Pick and remove a batch of deferred bubbles closest to the viewport."""
        if not self._deferred_render_bubbles:
            return []
        try:
            vp = self.scroll.viewport()
            tl = vp.mapTo(self.msg_container, QPoint(0, 0))
            br = vp.mapTo(self.msg_container, QPoint(vp.width(), vp.height()))
            top = int(tl.y())
            bottom = int(br.y())
        except Exception:
            # Fallback: render newest-first
            batch = []
            for _ in range(min(batch_size, len(self._deferred_render_bubbles))):
                batch.append(self._deferred_render_bubbles.pop())
            return batch

        center = (top + bottom) * 0.5
        # Expand target range so we pre-render a little above/below the viewport.
        pad = max(300, int((bottom - top) * 0.8))
        target_top = top - pad
        target_bottom = bottom + pad

        scored = []
        for i, b in enumerate(self._deferred_render_bubbles):
            try:
                g = b.geometry()
                mid = float(g.y() + g.height() * 0.5)
            except Exception:
                mid = float(i)
            in_band = 0 if (target_top <= mid <= target_bottom) else 1
            dist = abs(mid - center)
            scored.append((in_band, dist, i))

        scored.sort()
        pick_indices = [i for (_band, _dist, i) in scored[: min(batch_size, len(scored))]]
        pick_indices.sort(reverse=True)
        batch = []
        for i in pick_indices:
            try:
                batch.append(self._deferred_render_bubbles.pop(i))
            except Exception:
                pass
        return batch[::-1]

    def set_empty_context(self, workspace: str = "", title: str = "Start a new session", subtitle: str = ""):
        self._empty_workspace_lbl.setText("KsccUI")
        self._empty_title_lbl.setText(str(title or "Start a new session"))
        self._empty_subtitle_lbl.setText(str(subtitle or "Plan, build, and iterate from here."))

    def set_empty_state(self, active: bool, workspace: str = "", title: str = "", subtitle: str = ""):
        self._empty_mode = bool(active)
        if workspace or title or subtitle:
            self.set_empty_context(workspace, title or "Start a new session", subtitle)
        self.scroll.setVisible((not self._empty_mode) and (not self._bulk_restore))
        show_status = (not self._empty_mode) and bool(self._status_base_text)
        self.kscc_status_lbl.setVisible(show_status)
        if hasattr(self, "_status_row"):
            self._status_row.setVisible(show_status)
        self._empty_top_spacer.setVisible(self._empty_mode)
        self._empty_intro.setVisible(self._empty_mode)
        self._empty_bottom_spacer.setVisible(self._empty_mode)
        self.input_wrap.setMinimumWidth(640 if self._empty_mode else 0)
        self.input_wrap.setMaximumWidth(1080 if self._empty_mode else 16777215)
        self.input_wrap.setSizePolicy(
            QSizePolicy.Policy.Expanding if self._empty_mode else QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self._composer_left_spacer.setVisible(self._empty_mode)
        self._composer_right_spacer.setVisible(self._empty_mode)
        self._composer_layout.setStretch(0, 1 if self._empty_mode else 0)
        self._composer_layout.setStretch(1, 0 if self._empty_mode else 1)
        self._composer_layout.setStretch(2, 1 if self._empty_mode else 0)
        if self._empty_mode:
            self.input_edit.setPlaceholderText("Plan, build, ask, or drop files here")
        else:
            self.input_edit.setPlaceholderText("输入消息…  Ctrl+Enter 发送")
        QTimer.singleShot(0, self._adjust_input_height)

    def _set_drag_active(self, active: bool):
        active = bool(active)
        if self._drag_active == active:
            return
        self._drag_active = active
        self.apply_theme()

    def _adjust_input_height(self):
        QTimer.singleShot(0, self._sync_composer_height)

    def _sync_composer_height(self):
        te = self.input_edit
        vp_w = te.viewport().width()
        if vp_w <= 0:
            QTimer.singleShot(10, self._sync_composer_height)
            return

        doc = te.document()
        doc.setTextWidth(vp_w)
        plain = te.toPlainText()
        if not plain.strip():
            h = self._input_min_h
        else:
            doc_h = int(doc.size().height())
            if doc_h <= 0:
                doc_h = QFontMetrics(te.font()).lineSpacing() * max(1, plain.count("\n") + 1)
            m = te.contentsMargins().top() + te.contentsMargins().bottom()
            fw = int(te.frameWidth() or 0) * 2
            h = max(self._input_min_h, min(self._input_max_h, doc_h + m + fw + 8))

        te.setFixedHeight(h)
        te.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if h >= self._input_max_h
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

    def _models_for_backend(self, backend: str) -> list[str]:
        raw = list(self._model_map.get(backend) or [])
        if backend == "Kscc" and not raw:
            raw = list(KSCC_MODELS)
        return raw

    def _refresh_mode_menu_btn_text(self):
        model = self._model_name or "—"
        model_display = model.split("/", 1)[1].strip() if "/" in model else model
        abbrev = "OA" if self._backend_key == "OpenAI" else "KS"
        short = model_display if len(model_display) <= 18 else model_display[:15] + "…"
        label = f"[{abbrev}] {short}  ▾"
        self.mode_menu_btn.setText(label)
        fm = self.mode_menu_btn.fontMetrics()
        content_w = fm.horizontalAdvance(label) + 18
        target_w = max(78, min(220, content_w))
        self.mode_menu_btn.setFixedWidth(target_w)
        self.mode_menu_btn.setToolTip(f"后端: {self._backend_key}\n模型: {model}")

    def _update_task_button_icon(self):
        light = _is_light_theme()
        color = "#9aa4b2"
        if self._task_mode_enabled:
            color = "#2563eb" if light else "#5ee9ff"
        self.task_mode_btn.setIcon(quark_icon("spark", 14, color))
        self.task_mode_btn.setIconSize(QSize(14, 14))

    def _on_task_mode_btn_toggled(self, checked: bool):
        if self._mode_switch_locked:
            self.task_mode_btn.blockSignals(True)
            self.task_mode_btn.setChecked(self._task_mode_enabled)
            self.task_mode_btn.blockSignals(False)
            return
        self.set_task_mode_enabled(bool(checked))
        self.task_mode_changed.emit(self._task_mode_enabled)

    def set_task_mode_enabled(self, enabled: bool):
        self._task_mode_enabled = bool(enabled)
        if self.task_mode_btn.isChecked() != self._task_mode_enabled:
            self.task_mode_btn.blockSignals(True)
            self.task_mode_btn.setChecked(self._task_mode_enabled)
            self.task_mode_btn.blockSignals(False)
        self._update_task_button_icon()

    def set_mode_switch_locked(self, locked: bool):
        self._mode_switch_locked = bool(locked)
        self.mode_menu_btn.setEnabled(not locked)
        self.task_mode_btn.setEnabled(not locked)
        if locked:
            self.mode_menu_btn.setToolTip("运行中，暂不可切换模型")
            self.task_mode_btn.setToolTip("运行中，暂不可切换 Task 模式")
        else:
            self._refresh_mode_menu_btn_text()
            self.task_mode_btn.setToolTip("Task 模式：开启后强制按计划执行")

    def _pick_backend(self, backend: str):
        if self._backend_key == backend:
            return
        self._backend_key = backend
        models = self._models_for_backend(backend)
        self._model_name = models[0] if models else ""
        self._refresh_mode_menu_btn_text()

    def _pick_model(self, model: str):
        self._model_name = model
        self._refresh_mode_menu_btn_text()

    def _show_runtime_menu(self):
        if self._mode_switch_locked:
            return
        menu = QMenu(self)
        light = _is_light_theme()
        polish_menu(menu, "light" if light else "dark", font_size=12)
        header_font = QFont(self.font())
        header_font.setPointSize(10)
        header_font.setBold(True)

        backend_title = menu.addAction("后端")
        backend_title.setFont(header_font)
        backend_title.setEnabled(False)
        for bk, label in [("Kscc", "Kscc (Claude Code)"), ("OpenAI", "OpenAI")]:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(bk == self._backend_key)
            act.triggered.connect(lambda checked=False, b=bk: self._pick_backend(b))

        menu.addSeparator()

        model_title = menu.addAction("模型")
        model_title.setFont(header_font)
        model_title.setEnabled(False)
        models = self._models_for_backend(self._backend_key)
        if not models:
            models = ["Add in Settings..."]
        cur = self._model_name
        for m in models:
            act = menu.addAction(m)
            act.setCheckable(True)
            act.setChecked(m == cur)
            act.triggered.connect(lambda checked=False, mm=m: self._pick_model(mm))

        pos = self.mode_menu_btn.mapToGlobal(QPoint(0, self.mode_menu_btn.height() + 4))
        menu.exec(pos)

    def set_models(self, backend: str, models: list[str], active: str = ""):
        self._model_map[backend] = list(models)
        if active and self._backend_key == backend:
            self._model_name = active
        self._refresh_mode_menu_btn_text()

    def set_active(self, backend: str, model: str):
        self._backend_key = backend
        self._model_name = model
        self._refresh_mode_menu_btn_text()

    def set_context_ring_widget(self, widget: QWidget):
        if widget is None:
            return
        layout = self._context_ring_slot.layout()
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignCenter)

    def current_backend_label(self) -> str:
        return self._backend_key

    def current_model_name(self) -> str:
        return self._model_name

    def set_kscc_status(self, text: str):
        new_text = str(text or "").strip()
        if new_text != self._status_base_text:
            self._status_phase = 0  # 仅文本变化时重置动画到第0帧
        self._status_base_text = new_text
        if not self._status_base_text:
            self.kscc_status_lbl.hide()
            if hasattr(self, "_status_row"):
                self._status_row.hide()
            self._status_timer.stop()
            return
        if not self._status_timer.isActive():
            self._status_timer.start()
        self._apply_status_style()
        self._render_status()
        self.kscc_status_lbl.show()
        if hasattr(self, "_status_row"):
            self._status_row.show()

    def _apply_status_style(self):
        light = _is_light_theme()
        # Match the session activity bar blue, not the accent color setting
        fg = "#3b82f6" if light else "#5ee9ff"
        pad_l = 6
        pad_r = 4
        pad_t = 0
        pad_b = 0
        self.kscc_status_lbl.setStyleSheet(
            f"QLabel{{color:{fg};font-size:10px;padding:{pad_t}px {pad_r}px {pad_b}px {pad_l}px;"
            f"background:transparent;border:none;margin:0;}}"
        )

    def _render_status(self):
        dots = "." * ((self._status_phase % 3) + 1)
        self.kscc_status_lbl.setText(f"{self._status_base_text}{dots}")

    def _tick_status(self):
        if not self._status_base_text:
            self._status_timer.stop()
            return
        self._status_phase += 1
        self._render_status()

    def _pick_attachments(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择附件", "", "All Files (*.*)")
        for path in files:
            self._append_attachment(_attachment_meta(path, "file"))

    def _append_attachment(self, meta: dict):
        path = meta.get("path", "")
        if not path:
            return
        for existing in self._pending_attachments:
            if existing.get("path") == path:
                return
        self._pending_attachments.append(dict(meta))
        self._refresh_attachments_row()

    def _remove_attachment(self, path: str):
        self._pending_attachments = [m for m in self._pending_attachments if m.get("path") != path]
        self._refresh_attachments_row()

    def _save_pasted_image(self, pixmap: QPixmap) -> Optional[dict]:
        if pixmap.isNull():
            return None
        folder = _ensure_attachments_dir()
        name = f"pasted-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.png"
        target = folder / name
        if not pixmap.save(str(target), "PNG"):
            return None
        return _attachment_meta(str(target), "clipboard")

    def _on_image_pasted(self, pixmap):
        if not isinstance(pixmap, QPixmap):
            return
        meta = self._save_pasted_image(pixmap)
        if meta:
            self._append_attachment(meta)

    def _on_files_dropped(self, paths):
        for path in list(paths or []):
            if not path:
                continue
            ap = os.path.abspath(path)
            if os.path.isfile(ap):
                self._append_attachment(_attachment_meta(ap, "drop"))

    def _refresh_attachments_row(self):
        while self.attachments_layout.count():
            item = self.attachments_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if not self._pending_attachments:
            self.attachments_row.hide()
            return
        for meta in self._pending_attachments:
            chip = ComposerAttachmentChip(meta)
            chip.removed.connect(self._remove_attachment)
            self.attachments_layout.addWidget(chip, 0, Qt.AlignmentFlag.AlignLeft)
        self.attachments_layout.addStretch(1)
        self.attachments_row.show()

    def _send(self):
        t = self.input_edit.toPlainText().strip()
        attachments = [dict(meta) for meta in self._pending_attachments]
        if t or attachments:
            # 如果 Task 模式未开启且检测到复杂任务，弹出建议
            if not self._task_mode_enabled and _is_complex_task(t):
                self._pending_task_text = t
                self._pending_task_attachments = attachments
                self._task_suggestion.show()
                return
            self._do_send(t, attachments)
        self._adjust_input_height()

    def _do_send(self, t: str, attachments: list):
        self.set_empty_state(False)
        self.add_message("user", t, attachments=attachments)
        self.send_message.emit(t, attachments)
        self.show_save_skill_prompt(False)
        self.input_edit.clear()
        self._pending_attachments.clear()
        self._refresh_attachments_row()
        self._adjust_input_height()

    def _on_task_suggestion_enable(self):
        """用户点击"开启 Task 模式"。"""
        self._task_suggestion.hide()
        self.task_suggestion_accepted.emit(self._pending_task_text, self._pending_task_attachments)
        self._pending_task_text = ""
        self._pending_task_attachments = []

    def _on_task_suggestion_send_anyway(self):
        """用户点击"直接发送"。"""
        self._task_suggestion.hide()
        t = self._pending_task_text
        attachments = self._pending_task_attachments
        self._pending_task_text = ""
        self._pending_task_attachments = []
        if t or attachments:
            self._do_send(t, attachments)

    def _on_task_suggestion_dismiss(self):
        """用户关闭建议条。"""
        self._task_suggestion.hide()
        self._pending_task_text = ""
        self._pending_task_attachments = []

    def show_save_skill_prompt(self, visible: bool, text: str = "Save Skill"):
        self.save_skill_btn.setText(str(text or "Save Skill"))
        self.save_skill_btn.setVisible(bool(visible))

    def clear_diff_cards(self):
        self._diff_cards.clear()

    def add_message(self, role, text, attachments: Optional[list[dict]] = None, model_label: str = "", render_markdown: bool = True):
        self.set_empty_state(False)
        # Only defer assistant markdown; tool messages should match runtime rendering immediately.
        deferred = self._bulk_restore and (role == "assistant") and bool(render_markdown)
        b = ChatBubble(
            role,
            text,
            scroll_area=self.scroll,
            attachments=attachments,
            model_label=model_label,
            render_markdown=(False if deferred else render_markdown),
        )
        self.msg_layout.insertWidget(max(0, self.msg_layout.count() - 1), b)
        if deferred:
            self._deferred_render_bubbles.append(b)
        if not self._bulk_restore:
            QTimer.singleShot(30, self._scroll)
        return b

    def _on_scroll_value_changed(self, _v: int):
        # If there are deferred bubbles left, render a few on scroll so the visible region
        # gradually becomes fully formatted without blocking initial load.
        if self._bulk_restore:
            return
        if self._deferred_render_bubbles:
            # Start/continue the background drain; don't rely on a single scroll event.
            self._schedule_deferred_render()

    def start_stream(self, model_label: str = ""):
        self._stream_bubble = self.add_message("assistant", "", model_label=model_label)
        self.send_btn.setEnabled(False)
        self.send_btn.hide()
        self.stop_btn.show()
        self._run_halo.setGeometry(self._input_card.rect())
        self._run_halo.raise_()
        self._run_halo.set_running(True)

    def append_stream(self, text: str):
        if not text or not self._stream_bubble:
            return
        try:
            self._stream_bubble.append_text(text)
        except RuntimeError:
            self._stream_bubble = None

    def end_stream(self):
        # Flush any pending throttled render before clearing reference
        if self._stream_bubble:
            try:
                self._stream_bubble.finalize_stream()
            except RuntimeError:
                pass
        self._stream_bubble = None
        self.send_btn.setEnabled(True)
        self.send_btn.show()
        self.stop_btn.hide()
        self._run_halo.set_running(False)

    def add_tool(self, name, preview):
        if self._stream_bubble and name not in ("edit_file", "Write", "Edit"):
            try:
                self._stream_bubble.append_text(f"\n· Tool · {preview}\n")
            except RuntimeError:
                self._stream_bubble = None

    def add_result(self, result, error=False):
        if self._stream_bubble:
            marker = "Failed ·" if error else "Done ·"
            try:
                self._stream_bubble.append_text(f"{marker} {result[:120]}\n")
            except RuntimeError:
                self._stream_bubble = None

    def add_memory_hits(self, text: str):
        """P3-5: Display memory hit summary as a subtle info line."""
        if self._stream_bubble:
            try:
                self._stream_bubble.append_text(f"\n{text}\n")
            except RuntimeError:
                self._stream_bubble = None

    def add_context_hint(self, text: str, hint_type: str = "info"):
        """在会话区显示上下文压缩提示。hint_type: 'compressing' | 'done'"""
        if self._stream_bubble:
            try:
                if hint_type == "compressing":
                    marker = "⏳ Context · 正在压缩上下文..."
                else:
                    marker = "✓ Context · 上下文压缩完成"
                self._stream_bubble.append_text(f"\n{marker}\n")
            except RuntimeError:
                self._stream_bubble = None
            if not self._bulk_restore:
                QTimer.singleShot(40, self._scroll)

    def add_error(self, t):
        if self._stream_bubble:
            try:
                self._stream_bubble.append_text(f"\nError · {t}\n")
            except RuntimeError:
                self._stream_bubble = None
            self.end_stream()
        else:
            self.add_message("error", t)

    def add_diff(self, filepath, old, new):
        """折叠式文件变更卡片"""
        import html as _html
        nm = Path(filepath).name if filepath else "unknown"
        key = str(Path(filepath).as_posix()).lower() if filepath else f"unknown:{nm.lower()}"
        light = _is_light_theme()
        card_bg = "#f8fafc" if light else C_PANEL
        name_col = "#0f172a" if light else C_TEXT
        detail_bg = "transparent"
        detail_col = "#1f2937" if light else C_TEXT
        arrow_col = "#475569" if light else C_TEXT
        dim_col = "#64748b" if light else C_DIM
        line_no_col = "#94a3b8" if light else "#7c8594"
        code_font = "Consolas,'Cascadia Mono','Courier New',monospace"

        def _line_html(no: int, sign: str, text: str, color: str) -> str:
            return (
                "<tr>"
                f"<td style='width:44px;padding:0 10px 0 0;color:{line_no_col};"
                f"text-align:right;vertical-align:top;white-space:pre'>{no}</td>"
                f"<td style='width:14px;padding:0 8px 0 0;color:{color};vertical-align:top;white-space:pre'>{sign}</td>"
                f"<td style='padding:0;color:{color};vertical-align:top;white-space:pre-wrap'>{_html.escape(text)}</td>"
                "</tr>"
            )

        def _build_fragment() -> str:
            ts = datetime.now().strftime("%H:%M:%S")
            dt = (
                f"<div style='color:{dim_col};font-size:10px;margin:0 0 6px 0'>[{ts}]</div>"
                f"<table style='border-collapse:collapse;width:100%;font-family:{code_font};font-size:12px;line-height:1.55'>"
            )
            for idx, ln in enumerate(old.split('\n'), start=1):
                dt += _line_html(idx, "-", ln, "#ef4444")
            if old and new:
                dt += (
                    "<tr>"
                    f"<td style='width:44px;padding:2px 10px 2px 0;color:{line_no_col};text-align:right'> </td>"
                    f"<td colspan='2' style='padding:2px 0;color:{dim_col}'>→</td>"
                    "</tr>"
                )
            for idx, ln in enumerate(new.split('\n'), start=1):
                dt += _line_html(idx, "+", ln, "#22c55e")
            dt += "</table>"
            return dt

        existing = self._diff_cards.get(key)
        if existing and existing.get("card") is not None:
            parts = existing.get("parts", [])
            parts.append(_build_fragment())
            parts = parts[-24:]
            existing["parts"] = parts
            detail = existing["detail"]
            detail.setHtml("<br>".join(parts))
            QTimer.singleShot(30, self._scroll)
            return

        card = QFrame(self.msg_container)
        card.setStyleSheet(
            f"QFrame{{background:{card_bg};border:none;border-radius:12px;margin:4px 10px}}"
        )
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(8, 6, 8, 6)
        cl.setSpacing(4)

        hdr = QHBoxLayout()
        ic = QLabel(card)
        ic.setPixmap(quark_icon("file", 15).pixmap(15, 15))
        ic.setStyleSheet("background:transparent")
        hdr.addWidget(ic)
        nm_lbl = QLabel(f"<b>{nm}</b>", card)
        nm_lbl.setStyleSheet(f"color:{name_col};background:transparent;font-size:12px")
        hdr.addWidget(nm_lbl)
        hdr.addStretch()
        toggle = QLabel(card)
        toggle.setPixmap(quark_icon("chevron_right", 12, arrow_col).pixmap(12, 12))
        toggle.setStyleSheet("background:transparent")
        hdr.addWidget(toggle)
        cl.addLayout(hdr)

        detail = QTextEdit(card)
        detail.setReadOnly(True)
        detail.setFrameShape(QFrame.Shape.NoFrame)
        detail.setFont(QFont("Consolas", 10))
        detail.setMaximumHeight(560)
        detail.hide()
        detail.setStyleSheet(
            f"QTextEdit{{background:{detail_bg};color:{detail_col};border:none;padding:0;}}"
        )
        parts = [_build_fragment()]
        detail.setHtml(parts[0])
        cl.addWidget(detail)

        def toggle_diff(e=None):
            if detail.isHidden():
                detail.show()
                toggle.setPixmap(quark_icon("chevron_down", 12, arrow_col).pixmap(12, 12))
            else:
                detail.hide()
                toggle.setPixmap(quark_icon("chevron_right", 12, arrow_col).pixmap(12, 12))

        card.mousePressEvent = toggle_diff
        self._diff_cards[key] = {"card": card, "detail": detail, "parts": parts}
        self.msg_layout.insertWidget(max(0, self.msg_layout.count() - 1), card)
        QTimer.singleShot(30, self._scroll)

    def add_review_card(self, filepath, old, new, on_accept, on_reject):
        """代码审阅卡片（Accept/Reject 按钮）"""
        import html as _html
        from theme import C_RED
        nm = Path(filepath).name if filepath else "unknown"
        light = _is_light_theme()
        card_bg = "#f8fafc" if light else C_PANEL
        card_bd = "#dbe3ee" if light else "transparent"
        title_col = "#0f172a" if light else C_TEXT
        diff_bg = "#ffffff" if light else "transparent"
        diff_col = "#1f2937" if light else C_TEXT
        dim_col = "#64748b" if light else C_DIM
        card = QFrame(self.msg_container)
        card.setStyleSheet(
            f"QFrame{{background:{card_bg};border:1px solid {card_bd};border-radius:14px;margin:4px 10px}}"
        )
        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 8, 12, 8)
        cl.setSpacing(6)
        title = QLabel(f"<b>Review: {nm}</b>", card)
        title.setStyleSheet(f"color:{title_col};background:transparent;")
        cl.addWidget(title)
        diff = QTextEdit(card)
        diff.setReadOnly(True)
        diff.setFrameShape(QFrame.Shape.NoFrame)
        diff.setFont(QFont("Consolas", 11))
        diff.setMaximumHeight(200)
        diff.setStyleSheet(f"QTextEdit{{background:{diff_bg};color:{diff_col};border:none;}}")
        dt = ""
        for ln in old.split('\n'):
            dt += f"<span style='color:#ef4444'>- {_html.escape(ln)}</span><br>"
        dt += f"<span style='color:{dim_col}'>→</span><br>"
        for ln in new.split('\n'):
            dt += f"<span style='color:#22c55e'>+ {_html.escape(ln)}</span><br>"
        diff.setHtml(dt)
        cl.addWidget(diff)
        bl = QHBoxLayout()
        acc = QPushButton("Accept", card)
        acc.clicked.connect(lambda: (on_accept(), card.hide()))
        rej = QPushButton("Reject", card)
        rej.setStyleSheet(_with_tooltip_style(f"QPushButton{{background:{C_RED}}}QPushButton:hover{{background:#dc2626}}"))
        rej.clicked.connect(lambda: (on_reject(), card.hide()))
        bl.addWidget(acc)
        bl.addWidget(rej)
        cl.addLayout(bl)
        self.msg_layout.insertWidget(max(0, self.msg_layout.count() - 1), card)
        QTimer.singleShot(30, self._scroll)

    def _scroll(self):
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            self._run_halo.setGeometry(self._input_card.rect())
            self._run_halo.raise_()
        except Exception:
            pass
