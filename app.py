"""
Kscc Agent - PyQt6 Desktop Shell

模块拆分后，本文件仅保留 MainWindow + main() 入口。
UI 组件已拆分至：editor.py, chat_widgets.py, chat_panel.py,
file_tree.py, settings_page.py, agent_worker.py, ui_common.py
"""

import asyncio, json, math, os, re, subprocess, sys, threading, time, uuid
import html as _html
from datetime import datetime
from pathlib import Path
from typing import Optional
import memory_store
import insight_index

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QTextEdit, QPushButton, QTreeView, QScrollArea, QFrame, QFileDialog,
    QMenu, QSizePolicy, QToolBar, QStatusBar, QDialog, QFormLayout,
    QLineEdit, QComboBox, QDialogButtonBox, QSpinBox, QListWidget, QListWidgetItem, QCheckBox, QToolButton, QColorDialog, QFontComboBox,
    QMessageBox,
    QStyledItemDelegate, QStyle, QStyleOptionViewItem, QWidgetAction, QTabBar, QStackedWidget, QToolTip, QListView,
)
from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QThread, pyqtSignal, QTimer, QSize, QUrl, QSettings, QEvent
from PyQt6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QTextCursor,
    QFontDatabase,
    QFontMetrics,
    QKeySequence,
    QShortcut,
    QStandardItemModel,
    QStandardItem,
    QTextOption,
)
import data_portability

from ui_common import (
    APP_VERSION, _CODE_FONT_STACK, HAS_WEBENGINE,
    _is_light_theme, _tooltip_css, _with_tooltip_style, _fmt_k,
    _make_plus_icon, _ensure_attachments_dir,
    _is_image_path, _attachment_meta, _suppress_tk_root_window,
)
from editor import MonacoEditor, EditorTabHost
from chat_widgets import (
    ChatBubble, ChatInputEdit, ComposerAttachmentChip, FileAttachmentPill,
    PreviewImageLabel, ImagePreviewDialog, BubbleTextEdit,
    SessionActivityBar, ElidedLabel, WorkspaceGroupHeader,
    ContextRingWidget, NoWheelComboBox, NoWheelFontComboBox, NoWheelSpinBox,
)
from chat_panel import ChatPanel
from file_tree import FileTree
from settings_page import SettingsPage
from metrics_panel import MetricsPanel
from audit_panel import AuditPanel
from agent_worker import AgentWorker, TaskWorker, ClassifyWorker

from agent import Agent
from task_executor import TaskExecutor
from task_steps_widget import TaskStepsPanel
from config import (
    load_config,
    Config,
    save_config,
    get_active_provider,
    get_effective_model_limits,
    get_kscc_model_limits,
    KSCC_MODELS,
    OpenAIModel,
)
from context import ContextTracker
from session_store import SessionStore, Session
from skills_ui import SkillSaveDialog, SkillsPanel
import re as _re

from theme import (
    STYLESHEET,
    build_stylesheet,
    polish_menu,
    md_chat_to_html,
    quark_icon,
    C_ACCENT,
    C_ACCENT_LIGHT,
    C_ACCENT_SEL,
    C_ACCENT_SEL_LIGHT,
    C_FILE_TREE_SEL,
    C_FILE_TREE_SEL_LIGHT,
    C_BORDER,
    C_BORDER_AC,
    C_DIM,
    C_GREEN,
    C_PANEL,
    C_PANEL_HI,
    C_PANEL_HI_LIGHT,
    C_RED,
    C_TEAL,
    C_TEAL_LIGHT,
    C_TEXT,
    C_YELLOW,
)


# ── MainWindow ──────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KsccUI")
        self.resize(1400, 880)
        self.setMinimumSize(900, 560)
        self.config = load_config()
        # 校验 workspace 是否存在，不存在则回退到当前工作目录
        if not self.config.workspace or not os.path.isdir(self.config.workspace):
            self.config.workspace = os.path.abspath(os.getcwd())
            save_config(self.config)
        os.chdir(self.config.workspace)
        self.store = SessionStore()
        self.config.mode = "solo"
        self._agent: Optional[Agent] = None  # deprecated, kept for _save() compatibility
        self._worker: Optional[AgentWorker] = None  # deprecated, kept for compatibility
        self._running = False  # deprecated, kept for compatibility
        # Per-session worker/agent management
        self._workers: dict[str, AgentWorker] = {}  # session_id → worker
        self._agents: dict[str, Agent] = {}  # session_id → agent
        self._task_executors: dict[str, TaskExecutor] = {}  # session_id → task executor
        self._task_mode = False  # True = TaskExecutor 状态机模式
        self._last_task_executor = None  # 最近一次的 TaskExecutor（用于 resume）
        self._cur_session: Optional[Session] = None
        self._session_run_state: dict[str, str] = {}
        self._session_group_collapsed: dict[str, bool] = {}
        self._pending_user_message_meta: Optional[dict] = None
        self._pending_skill_draft: Optional[dict] = None
        self._last_agent_prompt: str = ""
        self._classify_worker: Optional[QThread] = None
        self._pending_send: Optional[dict] = None  # 分类期间暂存 prompt/attachments
        self._auto_task: bool = False  # 自动分类结果：当前消息使用 task 模式
        ip = Path(__file__).parent / "icon.png"
        if ip.exists():
            self.setWindowIcon(QIcon(str(ip)))
        app0 = QApplication.instance()
        if app0 is not None:
            app0.setProperty(
                "theme_mode",
                "light" if str(getattr(self.config, "theme", "dark")).lower() == "light" else "dark",
            )
        self._build_ui()
        app0 = QApplication.instance()
        if app0 is not None:
            app0.installEventFilter(self)
        self._apply()
        self._on_mode_toggle(self.config.mode == "solo")
        self._tooltip_suppressed_until = 0.0
        self._open_latest_session_on_startup()
        self._start_browser_driver()

    def eventFilter(self, obj, event):
        try:
            if event is not None and event.type() == QEvent.Type.ToolTip:
                if time.monotonic() < float(getattr(self, "_tooltip_suppressed_until", 0.0) or 0.0):
                    QToolTip.hideText()
                    if hasattr(event, "ignore"):
                        event.ignore()
                    return True
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _suppress_tooltips_temporarily(self, seconds: float = 0.9):
        self._tooltip_suppressed_until = max(
            float(getattr(self, "_tooltip_suppressed_until", 0.0) or 0.0),
            time.monotonic() + max(0.1, float(seconds)),
        )
        QToolTip.hideText()

    def _open_latest_session_on_startup(self):
        """Start on an empty session instead of auto-opening an existing one."""
        self._new_session()

    def _apply(self):
        mode = "light" if str(getattr(self.config, "theme", "dark")).lower() == "light" else "dark"
        stylesheet = build_stylesheet(mode)
        self.setStyleSheet(stylesheet)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet)
            app.setProperty("theme_mode", mode)
            f = QFont(getattr(self.config, "ui_font_family", "Segoe UI"), int(getattr(self.config, "ui_font_size", 12)))
            app.setFont(f)
            pal = app.palette()
            light = mode == "light"
            pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff" if light else "#111827"))
            pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#111827" if light else "#eef4f8"))
            pal.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.ToolTipText, QColor("#111827" if light else "#eef4f8"))
            pal.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.ToolTipBase, QColor("#ffffff" if light else "#111827"))
            app.setPalette(pal)
            QToolTip.setPalette(pal)
            tip_font = QFont(app.font())
            tip_font.setPointSize(max(9, app.font().pointSize() - 1))
            QToolTip.setFont(tip_font)
        self._apply_ide_editor_settings()
        self._sync_shell_chrome()
        self._sync_native_window_theme()
        if hasattr(self, "chat"):
            self.chat.apply_theme()
        if hasattr(self, "file_tree"):
            self.file_tree.apply_theme()
        if hasattr(self, "editor_tabs"):
            self.editor_tabs.apply_theme()
        if hasattr(self, "sess_layout"):
            self._refresh_sessions()
        if hasattr(self, "_file_tree_toggle_action"):
            QTimer.singleShot(0, self._sync_file_tree_toolbar_visibility)

    def _apply_ide_editor_settings(self):
        if not hasattr(self, "editor"):
            return
        wrap = "on" if bool(getattr(self.config, "ide_word_wrap", True)) else "off"
        minimap = "true" if bool(getattr(self.config, "ide_minimap", False)) else "false"
        fs = int(getattr(self.config, "ide_font_size", 13))
        mode = "light" if str(getattr(self.config, "theme", "dark")).lower() == "light" else "dark"
        self.editor._js(
            f"if(window.setThemeMode){{window.setThemeMode({json.dumps(mode)});}}"
            f"if(window.editor){{window.editor.updateOptions({{wordWrap:{json.dumps(wrap)}, minimap:{{enabled:{minimap}}}, fontSize:{fs}}});}}"
        )

    def _ctx_set_from_json(self, sj: str):
        try:
            self.ctx_ring.set_snapshot(json.loads(sj))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    def _ctx_apply_snapshot(self, snap: Optional[dict]):
        if isinstance(snap, dict) and snap:
            self.ctx_ring.set_snapshot(snap)
        else:
            self.ctx_ring.clear()

    def _ctx_clear(self):
        self.ctx_ring.clear()

    def _sync_file_tree_toolbar_visibility(self):
        ide = not self.mode_btn.isChecked()
        if hasattr(self, "_file_tree_toggle_action"):
            self._file_tree_toggle_action.setVisible(ide)
        elif hasattr(self, "_file_tree_toggle"):
            self._file_tree_toggle.setVisible(ide)

    def _sync_shell_chrome(self):
        light = str(getattr(self.config, "theme", "dark")).lower() == "light"
        top_text = "#000000" if light else "rgba(255,255,255,0.92)"
        hover_bg = "rgba(0,0,0,0.06)" if light else "rgba(255,255,255,0.08)"
        tip_css = _tooltip_css(light)
        dim = "#333333" if light else C_DIM
        accent_sel = C_ACCENT_SEL_LIGHT if light else C_ACCENT_SEL
        accent = C_ACCENT_LIGHT if light else C_ACCENT
        teal = C_TEAL_LIGHT if light else C_TEAL
        hi_text = "#000000" if light else C_TEXT
        if hasattr(self, "_toolbar_title"):
            self._toolbar_title.setStyleSheet("background:transparent;")
        if hasattr(self, "_toolbar_title_main"):
            self._toolbar_title_main.setStyleSheet(
                f"color:{top_text};background:transparent;font-size:11px;font-weight:800;letter-spacing:0.10em"
            )
        if hasattr(self, "_toolbar_title_sub"):
            self._toolbar_title_sub.setStyleSheet(
                f"color:{dim};background:transparent;font-size:9px;font-weight:500;letter-spacing:0.02em"
            )
        if hasattr(self, "mode_btn"):
            self.mode_btn.setStyleSheet(
                _with_tooltip_style(
                    f"QPushButton{{background:transparent;border:none;color:{dim};border-radius:10px;font-size:11px;font-weight:600;padding:0 12px;text-align:center;}}"
                    f"QPushButton::icon{{padding-left:2px;}}"
                    f"QPushButton:hover{{background:{hover_bg};color:{hi_text}}}"
                    f"QPushButton:checked{{background:{accent_sel};color:{accent}}}"
                    f"QPushButton:checked:hover{{background:{accent_sel};color:{accent}}}",
                    light,
                )
            )
            self.mode_btn.setToolTip("切换 Solo / IDE 模式")
        if hasattr(self, "_task_mode_btn"):
            self._task_mode_btn.setStyleSheet(
                _with_tooltip_style(
                    f"QPushButton{{background:transparent;border:none;color:{dim};border-radius:10px;font-size:11px;font-weight:600;padding:0 12px;text-align:center;}}"
                    f"QPushButton::icon{{padding-left:2px;}}"
                    f"QPushButton:hover{{background:{hover_bg};color:{hi_text}}}"
                    f"QPushButton:checked{{background:{accent_sel};color:{accent}}}"
                    f"QPushButton:checked:hover{{background:{accent_sel};color:{accent}}}",
                    light,
                )
            )
            self._task_mode_btn.setToolTip("切换任务状态机模式 (Plan→Execute→Reflect)")
        _tss = (
            f"QPushButton{{background:transparent;border:none;color:{dim};border-radius:8px;padding:4px 6px}}"
            f"QPushButton:hover{{background:{hover_bg};color:{hi_text}}}"
            f"QPushButton:checked{{background:{accent_sel};color:{accent}}}"
            f"QPushButton:checked:hover{{background:{accent_sel};color:{accent}}}"
            f"QPushButton:disabled{{color:{dim}}}"
        )
        if hasattr(self, "_file_tree_toggle"):
            self._file_tree_toggle.setStyleSheet(_with_tooltip_style(_tss, light))
        if hasattr(self, "_session_toggle"):
            self._session_toggle.setStyleSheet(_with_tooltip_style(_tss, light))
        if hasattr(self, "ws_btn"):
            self.ws_btn.setStyleSheet(
                _with_tooltip_style(
                    "QPushButton{background:transparent;border:none;border-radius:8px}"
                    f"QPushButton:hover{{background:{hover_bg}}}",
                    light,
                )
            )
        if hasattr(self, "model_lbl"):
            self.model_lbl.setStyleSheet(f"color:{teal};font-weight:500;font-size:11px;background:transparent")
        if hasattr(self, "ws_lbl"):
            self.ws_lbl.setStyleSheet(f"color:{dim};font-size:11px;background:transparent;max-width:220px")
        if hasattr(self, "settings_btn"):
            self.settings_btn.setStyleSheet(
                _with_tooltip_style(
                    f"QPushButton{{background:transparent;border:none;border-radius:8px;padding:0 10px;color:{dim};font-size:12px}}"
                    f"QPushButton:hover{{background:{hover_bg};color:{hi_text}}}",
                    light,
                )
            )
            if not (hasattr(self, "content_stack") and self.content_stack.currentIndex() == 1):
                self.settings_btn.setIcon(quark_icon("settings", 18))
        _tb_btn_css = (
            f"QPushButton{{background:transparent;border:none;border-radius:10px;padding:0 10px;color:{dim};font-size:11px;font-weight:600;text-align:center;}}"
            f"QPushButton:hover{{background:{hover_bg};color:{hi_text}}}"
        )
        if hasattr(self, "skills_btn"):
            self.skills_btn.setStyleSheet(_with_tooltip_style(_tb_btn_css, light))
        if hasattr(self, "metrics_btn"):
            self.metrics_btn.setStyleSheet(_with_tooltip_style(_tb_btn_css, light))
        if hasattr(self, "audit_btn"):
            self.audit_btn.setStyleSheet(_with_tooltip_style(_tb_btn_css, light))
        if hasattr(self, "ws_lbl"):
            self.ws_lbl.setStyleSheet(
                f"color:{dim};font-size:11px;font-weight:500;background:transparent;max-width:180px;padding-left:2px;"
            )
        if hasattr(self, "file_sidebar"):
            self.file_sidebar.setStyleSheet("background:#f5f5f5;" if light else "background:transparent;")
        if hasattr(self, "session_panel"):
            self.session_panel.setStyleSheet("background:#f5f5f5;" if light else "background:transparent;")
        if hasattr(self, "chat"):
            self.chat.setStyleSheet("background:#ffffff;" if light else "background:transparent;")
        if hasattr(self, "editor_tabs"):
            self.editor_tabs.setStyleSheet("background:#ffffff;" if light else "background:transparent;")
        if hasattr(self, "_new_session_btn"):
            if light:
                self._new_session_btn.setStyleSheet(
                    _with_tooltip_style(
                        "QPushButton{background:#e8e8e8;border:1px solid rgba(0,0,0,0.08);border-radius:10px;"
                        "margin:8px 10px 6px 10px;padding:10px 14px;text-align:center;font-size:12px;font-weight:600;color:#000000}"
                        "QPushButton:hover{background:#dedede}",
                        light,
                    )
                )
            else:
                self._new_session_btn.setStyleSheet(
                    _with_tooltip_style(
                        f"QPushButton{{background:{C_PANEL};border:none;border-radius:10px;margin:8px 10px 6px 10px;"
                        f"padding:10px 14px;text-align:center;font-size:12px;font-weight:500;color:{C_ACCENT}}}"
                        f"QPushButton:hover{{background:{C_PANEL_HI}}}",
                        light,
                    )
                )
        in_settings = hasattr(self, "content_stack") and self.content_stack.currentIndex() == 1
        for name in ("mode_btn", "_file_tree_toggle", "_session_toggle", "ctx_ring", "model_lbl", "ws_btn", "ws_lbl", "_toolbar_spacer"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setVisible(not in_settings)
        if hasattr(self, "_file_tree_toggle_action"):
            self._file_tree_toggle_action.setVisible((not in_settings) and (not self.mode_btn.isChecked()))

    def _sync_native_window_theme(self):
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            light = str(getattr(self.config, "theme", "dark")).lower() == "light"
            dark_flag = ctypes.c_int(0 if light else 1)
            caption = ctypes.c_int(0x00F5F5F5 if light else 0x00202020)
            text = ctypes.c_int(0x00000000 if light else 0x00F3F4F6)
            border = ctypes.c_int(0x00D6D6D6 if light else 0x00303030)
            dwmapi = ctypes.windll.dwmapi
            dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark_flag), ctypes.sizeof(dark_flag))
            dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))
            dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
            dwmapi.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), ctypes.sizeof(text))
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_native_window_theme)

    def _build_ui(self):
        c = QWidget()
        c.setObjectName("centralGlass")
        c.setStyleSheet("background: transparent;")
        self.setCentralWidget(c)
        r = QVBoxLayout(c)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(0)

        # Toolbar
        tb = QToolBar()
        tb.setMovable(False)
        self._toolbar_title = QWidget()
        self._toolbar_title.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        ttl = QVBoxLayout(self._toolbar_title)
        ttl.setContentsMargins(6, 2, 10, 2)
        ttl.setSpacing(0)
        self._toolbar_title_main = QLabel("KSCC UI")
        self._toolbar_title_sub = QLabel(APP_VERSION)
        self._toolbar_title_main.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        self._toolbar_title_sub.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        ttl.addWidget(self._toolbar_title_main)
        ttl.addWidget(self._toolbar_title_sub)
        tb.addWidget(self._toolbar_title)
        self.mode_btn = QPushButton("Solo" if self.config.mode == "solo" else "IDE")
        self.mode_btn.setCheckable(True)
        self.mode_btn.setChecked(self.config.mode == "solo")
        self.mode_btn.setFixedHeight(32)
        self.mode_btn.setIcon(quark_icon("panels", 16))
        self.mode_btn.setIconSize(QSize(16, 16))
        self.mode_btn.toggled.connect(self._on_mode_toggle)
        tb.addWidget(self.mode_btn)
        self._task_mode_btn = QPushButton("Task")
        self._task_mode_btn.setCheckable(True)
        self._task_mode_btn.setChecked(False)
        self._task_mode_btn.setFixedHeight(32)
        self._task_mode_btn.setIcon(quark_icon("spark", 16))
        self._task_mode_btn.setIconSize(QSize(16, 16))
        self._task_mode_btn.setToolTip("切换任务状态机模式 (Plan→Execute→Reflect)")
        self._task_mode_btn.toggled.connect(self._on_task_mode_toggle)
        tb.addWidget(self._task_mode_btn)
        self._file_tree_toggle = QPushButton()
        self._file_tree_toggle.setCheckable(True)
        self._file_tree_toggle.setFixedSize(34, 34)
        self._file_tree_toggle.setIcon(quark_icon("panels", 18))
        self._file_tree_toggle.setIconSize(QSize(18, 18))
        self._file_tree_toggle.setToolTip("文件树 (Ctrl+B)")
        self._file_tree_toggle.toggled.connect(self._on_file_tree_toggled)
        self._file_tree_toggle_action = QWidgetAction(self)
        self._file_tree_toggle_action.setDefaultWidget(self._file_tree_toggle)
        tb.addAction(self._file_tree_toggle_action)
        self._session_toggle = QPushButton()
        self._session_toggle.setCheckable(True)
        self._session_toggle.setFixedSize(34, 34)
        self._session_toggle.setIcon(quark_icon("bullet_list", 18))
        self._session_toggle.setIconSize(QSize(18, 18))
        self._session_toggle.setToolTip("会话列表 (Ctrl+Shift+L)")
        self._session_toggle.toggled.connect(self._on_session_panel_toggled)
        tb.addWidget(self._session_toggle)
        self.ctx_ring = ContextRingWidget(diameter=16)
        tb.addWidget(self.ctx_ring)
        p = get_active_provider(self.config)
        self.model_lbl = QLabel(f"Kscc/{self.config.kscc_model}" if self.config.backend == "kscc" else f"OpenAI/{p.model}")
        tb.addWidget(self.model_lbl)
        self._toolbar_spacer = QWidget()
        self._toolbar_spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(self._toolbar_spacer)
        light = str(getattr(self.config, "theme", "dark")).lower() == "light"
        hover_bg = "rgba(0,0,0,0.06)" if light else "rgba(255,255,255,0.08)"
        dim = "#333333" if light else C_DIM
        hi_text = "#000000" if light else C_TEXT
        self.ws_btn = QPushButton()
        self.ws_btn.setFixedSize(34, 34)
        self.ws_btn.setIcon(quark_icon("folder", 18))
        self.ws_btn.setIconSize(QSize(18, 18))
        self.ws_btn.setStyleSheet(
            _with_tooltip_style(
                f"QPushButton{{background:transparent;border:none;border-radius:8px}}QPushButton:hover{{background:{hover_bg}}}",
                light,
            )
        )
        self.ws_btn.setToolTip("Change workspace")
        self.ws_btn.clicked.connect(self._chg_ws)
        tb.addWidget(self.ws_btn)
        self.ws_lbl = QLabel(self._ws_display(self.config.workspace))
        tb.addWidget(self.ws_lbl)
        self.settings_btn = QPushButton("设置")
        self.settings_btn.setIcon(quark_icon("settings", 16))
        self.settings_btn.setIconSize(QSize(16, 16))
        self.settings_btn.setMinimumHeight(32)
        self.settings_btn.clicked.connect(self._settings)
        self.settings_btn.setStyleSheet(
            _with_tooltip_style(
                f"QPushButton{{background:transparent;border:none;border-radius:8px;padding:0 10px;color:{dim};font-size:12px}}"
                f"QPushButton:hover{{background:{hover_bg};color:{hi_text}}}",
                light,
            )
        )
        tb.addWidget(self.settings_btn)
        self.skills_btn = QPushButton("Skills")
        self.skills_btn.setIcon(quark_icon("bullet_list", 16))
        self.skills_btn.setIconSize(QSize(16, 16))
        self.skills_btn.setMinimumHeight(32)
        self.skills_btn.setToolTip("管理本地 Skills")
        self.skills_btn.clicked.connect(self._show_skills_page)
        self.skills_btn.setStyleSheet(
            _with_tooltip_style(
                f"QPushButton{{background:transparent;border:none;border-radius:8px;padding:0 10px;color:{dim};font-size:12px}}"
                f"QPushButton:hover{{background:{hover_bg};color:{hi_text}}}",
                light,
            )
        )
        tb.addWidget(self.skills_btn)
        self.metrics_btn = QPushButton("Metrics")
        self.metrics_btn.setIcon(quark_icon("chart_bar", 16))
        self.metrics_btn.setIconSize(QSize(16, 16))
        self.metrics_btn.setMinimumHeight(32)
        self.metrics_btn.setToolTip("指标看板")
        self.metrics_btn.clicked.connect(self._show_metrics_page)
        self.metrics_btn.setStyleSheet(
            _with_tooltip_style(
                f"QPushButton{{background:transparent;border:none;border-radius:8px;padding:0 10px;color:{dim};font-size:12px}}"
                f"QPushButton:hover{{background:{hover_bg};color:{hi_text}}}",
                light,
            )
        )
        tb.addWidget(self.metrics_btn)
        self.audit_btn = QPushButton("Audit")
        self.audit_btn.setIcon(quark_icon("list", 16))
        self.audit_btn.setIconSize(QSize(16, 16))
        self.audit_btn.setMinimumHeight(32)
        self.audit_btn.setToolTip("会话审计")
        self.audit_btn.clicked.connect(self._show_audit_page)
        self.audit_btn.setStyleSheet(
            _with_tooltip_style(
                f"QPushButton{{background:transparent;border:none;border-radius:8px;padding:0 10px;color:{dim};font-size:12px}}"
                f"QPushButton:hover{{background:{hover_bg};color:{hi_text}}}",
                light,
            )
        )
        tb.addWidget(self.audit_btn)
        r.addWidget(tb)

        # Splitter
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(2)
        self.splitter.splitterMoved.connect(self._on_outer_splitter_moved)

        self.file_sidebar = QWidget()
        self.file_sidebar.setMinimumWidth(180)
        self.file_sidebar.setMaximumWidth(320)
        fs_l = QVBoxLayout(self.file_sidebar)
        fs_l.setContentsMargins(0, 0, 0, 0)
        fs_l.setSpacing(0)
        self.file_tree = FileTree(self.config.workspace)
        self.file_tree.file_selected.connect(self._on_file)
        fs_l.addWidget(self.file_tree, 1)
        self.splitter.addWidget(self.file_sidebar)

        self.editor_tabs = EditorTabHost()
        self.editor = self.editor_tabs.editor
        self.editor.ready.connect(self._apply_ide_editor_settings)
        self.editor.ask_selection.connect(self._on_sel)
        self.editor.ask_file.connect(lambda f: None)
        self.editor_tabs.save_requested.connect(self._on_editor_save_requested)
        QTimer.singleShot(2000, lambda: self.editor._js("window._enableCompletions()"))
        self.splitter.addWidget(self.editor_tabs)

        # Session panel
        self.session_panel = QWidget()
        self.session_panel.setMinimumWidth(160)
        self.session_panel.setMaximumWidth(300)
        sl = QVBoxLayout(self.session_panel)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(2)
        self._new_session_btn = QPushButton("New session")
        self._new_session_btn.clicked.connect(self._new_session)
        sl.addWidget(self._new_session_btn)
        self.sess_scroll = QScrollArea()
        self.sess_scroll.setWidgetResizable(True)
        self.sess_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sess_container = QWidget()
        self.sess_container.setStyleSheet("background:transparent")
        self.sess_layout = QVBoxLayout(self.sess_container)
        self.sess_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.sess_layout.setSpacing(2)
        self.sess_layout.setContentsMargins(4, 2, 4, 2)
        self.sess_scroll.setWidget(self.sess_container)
        sl.addWidget(self.sess_scroll, 1)

        self.chat = ChatPanel()
        self.chat.send_message.connect(self._on_send)
        self.chat.add_skill_requested.connect(self._open_skill_save_dialog)
        self.chat.task_suggestion_accepted.connect(self._on_task_suggestion_accepted)
        self.chat.stop_btn.clicked.connect(self._on_stop)
        self._init_chat_selectors()

        self.session_chat_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.session_chat_splitter.setHandleWidth(2)
        self.session_chat_splitter.addWidget(self.session_panel)
        self.session_chat_splitter.addWidget(self.chat)
        self.session_chat_splitter.setCollapsible(0, False)
        self.session_chat_splitter.setCollapsible(1, False)
        self.session_chat_splitter.setStretchFactor(0, 0)
        self.session_chat_splitter.setStretchFactor(1, 1)
        self.session_chat_splitter.setSizes([200, 720])

        self.splitter.addWidget(self.session_chat_splitter)

        # Task steps panel - right sidebar (hidden by default)
        self.task_panel = TaskStepsPanel()
        self.task_panel.setMinimumWidth(240)
        self.task_panel.setMaximumWidth(500)
        self.task_panel.hide()
        self.task_panel.resume_clicked.connect(self._resume_task)
        self.splitter.addWidget(self.task_panel)
        self.splitter.setStretchFactor(0, 0)  # file_sidebar
        self.splitter.setStretchFactor(1, 1)  # editor_tabs
        self.splitter.setStretchFactor(2, 1)  # session_chat_splitter
        self.splitter.setStretchFactor(3, 0)  # task_panel
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.splitter)
        self.settings_page = SettingsPage(self.config, self)
        self.settings_page.saved.connect(self._on_settings_saved)
        self.settings_page.cancelled.connect(self._show_main_page)
        self.content_stack.addWidget(self.settings_page)
        self.metrics_panel = MetricsPanel(self.config, self)
        self.content_stack.addWidget(self.metrics_panel)
        self.audit_panel = AuditPanel(self.config, self)
        self.content_stack.addWidget(self.audit_panel)
        self.skills_panel = SkillsPanel(self.config, self)
        self.content_stack.addWidget(self.skills_panel)
        self.content_stack.setCurrentIndex(0)
        self._sync_settings_toolbar_button()
        r.addWidget(self.content_stack, 1)
        self._load_layout_panel_settings()
        self._sync_file_tree_toolbar_visibility()
        init_w = max(self.width(), 1280)
        if not self.mode_btn.isChecked():
            self.splitter.setSizes(self._compute_outer_split_sizes(init_w))
        QTimer.singleShot(0, self._layout_after_first_show)
        QShortcut(QKeySequence("Ctrl+B"), self, self._shortcut_toggle_file_tree)
        QShortcut(QKeySequence("Ctrl+Shift+L"), self, self._shortcut_toggle_session)

        # Status bar
        self.sbar = QStatusBar()
        self.slbl = QLabel("Ready")
        self.tlbl = QLabel("")
        self.sbar.addWidget(self.slbl, 1)
        self.sbar.addPermanentWidget(self.tlbl)
        self.setStatusBar(self.sbar)
        r.addWidget(self.sbar)

    def _layout_read(self, key: str, default, typ=None):
        s = QSettings()
        s.beginGroup("layout")
        try:
            if typ is bool:
                v = s.value(key, default, type=bool)
                return default if v is None else v
            if typ is int:
                v = s.value(key, default, type=int)
                return default if v is None else v
            return s.value(key, default)
        finally:
            s.endGroup()

    def _layout_write(self, key: str, value):
        s = QSettings()
        s.beginGroup("layout")
        try:
            s.setValue(key, value)
        finally:
            s.endGroup()

    def _load_layout_panel_settings(self):
        show_f = self._layout_read("show_file_tree", True, bool)
        show_s = self._layout_read("show_session_panel", True, bool)
        self._file_tree_toggle.blockSignals(True)
        self._session_toggle.blockSignals(True)
        self._file_tree_toggle.setChecked(show_f)
        self._session_toggle.setChecked(show_s)
        self._file_tree_toggle.blockSignals(False)
        self._session_toggle.blockSignals(False)
        if not show_f:
            self.file_sidebar.hide()
        if not show_s:
            self.session_panel.hide()

    def _desired_task_panel_width(self) -> int:
        width = int(self._layout_read("task_panel_width", 420, int))
        return max(320, min(500, width))

    def _compute_outer_split_sizes(self, total: int) -> list[int]:
        total = max(int(total), 680)
        tw = self._desired_task_panel_width() if self.task_panel.isVisible() else 0
        if self.mode_btn.isChecked():
            return [0, 0, total - tw, tw]
        if not self.file_sidebar.isVisible():
            chat_col = max(520, int(round(total * 0.38)))
            ed = total - chat_col - tw
            if ed < 260:
                chat_col = total - 260 - tw
                ed = 260
            chat_col = max(300, chat_col)
            ed = total - chat_col - tw
            return [0, max(200, ed), chat_col, tw]
        fw = int(self._layout_read("file_tree_width", 200, int))
        fw = max(180, min(320, fw))
        chat_col = max(520, int(round(total * 0.38)))
        ed = total - fw - chat_col - tw
        if ed < 260:
            chat_col = total - fw - 260 - tw
            ed = 260
        chat_col = max(300, chat_col)
        ed = total - fw - chat_col - tw
        return [fw, max(200, ed), chat_col, tw]

    def _ide_split_sizes(self, total: int) -> list[int]:
        return self._compute_outer_split_sizes(total)

    def _layout_after_first_show(self):
        if self.mode_btn.isChecked():
            return
        w = max(self.splitter.width(), 800)
        self.splitter.setSizes(self._compute_outer_split_sizes(w))
        self._redistribute_session_inner()

    def _sync_ide_split_after_show(self):
        self._layout_after_first_show()

    def _redistribute_outer_splitter(self):
        total = max(self.splitter.width(), 680)
        self.splitter.setSizes(self._compute_outer_split_sizes(total))
        if not self.mode_btn.isChecked():
            self._redistribute_session_inner()

    def _on_outer_splitter_moved(self, _pos: int, _index: int):
        sizes = self.splitter.sizes()
        if len(sizes) >= 4 and self.task_panel.isVisible() and sizes[3] > 0:
            self._layout_write("task_panel_width", sizes[3])

    def _ensure_task_panel_default_width(self):
        if not self.task_panel.isVisible():
            return
        desired = self._desired_task_panel_width()
        sizes = self.splitter.sizes()
        if len(sizes) < 4:
            total = max(self.splitter.width(), 680)
            self.splitter.setSizes(self._compute_outer_split_sizes(total))
            sizes = self.splitter.sizes()
        if len(sizes) < 4:
            return
        current = sizes[3]
        if current >= desired - 12:
            return
        delta = desired - current
        sizes[3] = desired
        take_idx = 2 if sizes[2] > 260 else 1
        sizes[take_idx] = max(220, sizes[take_idx] - delta)
        self.splitter.setSizes(sizes)

    def _redistribute_session_inner(self):
        sc = self.session_chat_splitter
        if sc.width() <= 0:
            return
        tw = sc.width()
        if not self.session_panel.isVisible():
            sc.setSizes([0, max(200, tw)])
            return
        sw = int(self._layout_read("session_panel_width", 200, int))
        sw = max(160, min(300, sw))
        sw = min(sw, tw - 200)
        sc.setSizes([sw, max(200, tw - sw)])

    def _sync_session_chat_split(self):
        sc = getattr(self, "session_chat_splitter", None)
        if sc is None or sc.width() <= 0:
            return
        if not self.session_panel.isVisible():
            return
        tw = sc.width()
        sess = min(260, max(160, int(tw * 0.28)))
        sc.setSizes([sess, max(200, tw - sess)])

    def _on_file_tree_toggled(self, checked: bool):
        if self.mode_btn.isChecked():
            return
        if hasattr(self, "_file_tree_toggle_action") and not self._file_tree_toggle_action.isVisible():
            return
        if not self._file_tree_toggle.isVisible():
            return
        if checked:
            self.file_sidebar.show()
        else:
            sizes = self.splitter.sizes()
            if sizes and sizes[0] > 0:
                self._layout_write("file_tree_width", sizes[0])
            self.file_sidebar.hide()
        self._layout_write("show_file_tree", checked)
        self._redistribute_outer_splitter()

    def _on_session_panel_toggled(self, checked: bool):
        if checked:
            self.session_panel.show()
        else:
            sc = self.session_chat_splitter
            sz = sc.sizes()
            if sz and sz[0] > 0:
                self._layout_write("session_panel_width", sz[0])
            self.session_panel.hide()
        self._layout_write("show_session_panel", checked)
        self._redistribute_session_inner()

    def _shortcut_toggle_file_tree(self):
        if hasattr(self, "_file_tree_toggle_action") and not self._file_tree_toggle_action.isVisible():
            return
        if not self._file_tree_toggle.isVisible():
            return
        self._file_tree_toggle.setChecked(not self._file_tree_toggle.isChecked())

    def _shortcut_toggle_session(self):
        self._session_toggle.setChecked(not self._session_toggle.isChecked())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.mode_btn.isChecked():
            return
        if self.session_panel.isVisible():
            QTimer.singleShot(0, self._sync_session_chat_split)
        else:
            QTimer.singleShot(0, self._redistribute_session_inner)

    def _on_mode_toggle(self, checked):
        w = max(self.splitter.width(), 600)
        self._sync_file_tree_toolbar_visibility()
        if checked:
            self.mode_btn.setText("Solo")
            self.file_sidebar.hide()
            self.editor_tabs.hide()
            self.splitter.setSizes(self._compute_outer_split_sizes(w))
        else:
            self.mode_btn.setText("IDE")
            self.editor_tabs.show()
            if self._file_tree_toggle.isChecked():
                self.file_sidebar.show()
            else:
                self.file_sidebar.hide()
            self.splitter.setSizes(self._compute_outer_split_sizes(w))
            QTimer.singleShot(0, self._redistribute_session_inner)
        self.config.mode = "solo" if checked else "ide"
        QTimer.singleShot(0, self._sync_file_tree_toolbar_visibility)

    def _on_task_mode_toggle(self, checked):
        if checked and not getattr(self.config, 'feature_task_mode', True):
            self._task_mode_btn.setChecked(False)
            self.slbl.setText("Task mode disabled. Enable in Settings > Agent > 实验特性")
            return
        self._task_mode = checked
        self.chat._task_mode_enabled = checked
        if checked:
            self.task_panel.show()
        else:
            self.task_panel.hide()
        # 先立即给 task 面板明确默认宽度，再在布局稳定后补一次修正。
        QTimer.singleShot(0, self._redistribute_outer_splitter)
        if checked:
            QTimer.singleShot(0, self._ensure_task_panel_default_width)
            QTimer.singleShot(80, self._ensure_task_panel_default_width)
            QTimer.singleShot(120, self._redistribute_outer_splitter)
            QTimer.singleShot(180, self._ensure_task_panel_default_width)
            QTimer.singleShot(260, self._redistribute_outer_splitter)
        else:
            QTimer.singleShot(100, self._redistribute_outer_splitter)

    def _on_task_suggestion_accepted(self, text, attachments):
        """用户在建议条中点击"开启 Task 模式"。"""
        self._task_mode_btn.setChecked(True)
        self.chat.set_empty_state(False)
        self.chat.add_message("user", text, attachments=attachments)
        self.chat.input_edit.clear()
        self.chat._pending_attachments.clear()
        self.chat._refresh_attachments_row()
        self._on_send(text, attachments)

    # ── File tree ──────────────────────────────────────────
    def _on_file(self, path):
        self.editor_tabs.open_file(path)
        self.slbl.setText(f"Opened: {Path(path).name}")
        self.chat.set_kscc_status(f"Current file: {Path(path).name}")
        QTimer.singleShot(8000, lambda: self.chat.set_kscc_status(""))
        try:
            content = Path(path).read_text("utf-8", errors="replace")[:500]
            self.chat.add_message("tool", f"File · {Path(path).name}\n```\n{content}\n```")
        except Exception:
            pass

    def _init_chat_selectors(self):
        self.chat.set_models("Kscc", KSCC_MODELS, self.config.kscc_model)
        oa_names = [m.name for m in self.config.openai_models if getattr(m, "enabled", True)]
        self.chat.set_models("OpenAI", oa_names if oa_names else ["Add in Settings..."], self.config.openai_active)
        bk = "OpenAI" if self.config.backend == "openai" else "Kscc"
        if bk == "Kscc":
            self.chat.set_active("Kscc", self.config.kscc_model)
        elif self.config.openai_active:
            self.chat.set_active("OpenAI", self.config.openai_active)
        else:
            self.chat.set_active("OpenAI", (self.chat._model_map.get("OpenAI") or ["Add in Settings..."])[0])

    def _chg_ws(self):
        base = self._cur_session.workspace if self._cur_session and self._cur_session.workspace else self.config.workspace
        p = QFileDialog.getExistingDirectory(self, "Select Workspace", base)
        if p:
            ap = os.path.abspath(p)
            if self._cur_session is not None:
                self._cur_session.workspace = ap
                self.store.save(self._cur_session)
                self._refresh_sessions()
            self.config.workspace = ap
            save_config(self.config)
            os.chdir(ap)
            self.ws_lbl.setText(self._ws_display(ap))
            self.file_tree.set_workspace(ap)

    def _ws_display(self, ws: str) -> str:
        """Return display label for a workspace path ('Home' for home dir)."""
        home = os.path.expanduser("~")
        if ws and os.path.normcase(os.path.normpath(ws)) == os.path.normcase(os.path.normpath(home)):
            return "Home"
        return self._short(ws, 30)

    @staticmethod
    def _short(t, n):
        return t if len(t) <= n else "..." + t[-(n - 3):]

    # ── Selection ──────────────────────────────────────────
    def _on_sel(self, fp, txt):
        p = txt[:300]
        self.chat.add_message("tool", f"File · {Path(fp).name or 'editor'}\n```\n{p}\n```" + ("\n..." if len(txt) > 300 else ""))
        self.slbl.setText("Selection added")

    def _on_editor_save_requested(self, path: str, content: str):
        if not path:
            self.slbl.setText("Save failed: no file path")
            return
        try:
            Path(path).write_text(content, "utf-8")
            self.editor_tabs.mark_saved(path, content)
            self.slbl.setText(f"Saved: {Path(path).name}")
        except Exception as e:
            self.slbl.setText(f"Save failed: {e}")

    # ── Sessions ───────────────────────────────────────────
    def _refresh_sessions(self):
        light = str(getattr(self.config, "theme", "dark")).lower() == "light"
        card_bg = "#ffffff" if light else C_PANEL
        card_hover = "rgba(2,6,23,0.06)" if light else C_PANEL_HI
        card_sel = "#eef6ff" if light else "rgba(94,233,255,0.18)"
        indicator = "#3b82f6" if light else C_ACCENT
        bar_idle = "#9ca3af" if light else "#4b5563"
        title_col = "#000000" if light else "rgba(255,255,255,0.9)"
        meta_col = "#333333" if light else C_DIM
        ws_head = "#000000" if light else C_TEAL
        while self.sess_layout.count():
            w = self.sess_layout.takeAt(0)
            if w.widget():
                w.widget().deleteLater()

        sessions = self.store.list_sessions()
        grouped: dict[str, list[dict]] = {}
        group_order: list[str] = []
        for s in sessions:
            ws = (s.get("workspace") or "").strip()
            key = ws if ws else "(No workspace)"
            if key not in grouped:
                grouped[key] = []
                group_order.append(key)
            grouped[key].append(s)

        for ws in group_order:
            home = os.path.expanduser("~")
            if ws not in ("", "(No workspace)"):
                ws_name = "Home" if os.path.normcase(os.path.normpath(ws)) == os.path.normcase(os.path.normpath(home)) else Path(ws).name
            else:
                ws_name = "No workspace"
            expanded = not self._session_group_collapsed.get(ws, False)
            head = WorkspaceGroupHeader(ws, ws_name[:42], expanded=expanded)
            head.toggled.connect(self._on_session_group_toggled)
            head.add_requested.connect(self._new_session_for_workspace)
            self.sess_layout.addWidget(head)

            group_body = QWidget()
            group_body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            group_body_layout = QVBoxLayout(group_body)
            group_body_layout.setContentsMargins(0, 0, 0, 0)
            group_body_layout.setSpacing(0)
            group_body.setVisible(expanded)
            for s in grouped[ws]:
                sid = s["id"]
                title = s.get("title", "Untitled")
                cnt = s.get("message_count", 0)
                selected = bool(self._cur_session and self._cur_session.id == sid)
                raw_updated = str(s.get("updated", "") or "")
                short_updated = raw_updated[:16]
                if raw_updated:
                    try:
                        dt = datetime.fromisoformat(raw_updated.replace("Z", "+00:00"))
                        if dt.utcoffset() is not None and dt.utcoffset().total_seconds() == 0:
                            # +00:00 offset likely means local time was stored
                            # as UTC (legacy data); display as-is without conversion
                            short_updated = dt.strftime("%m-%d %H:%M")
                        else:
                            short_updated = dt.astimezone().strftime("%m-%d %H:%M")
                    except Exception:
                        m = _re.match(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}:\d{2})", raw_updated)
                        if m:
                            short_updated = f"{m.group(2)}-{m.group(3)} {m.group(4)}"
                card = QFrame()
                card_margin_x = 6
                card_pad = 2
                row_left = 5
                row_right = 8
                row_gap = 3
                card.setStyleSheet(
                    f"QFrame{{background:{card_sel if selected else card_bg};border:none;border-radius:10px;margin:3px {card_margin_x}px;padding:{card_pad}px}}"
                    f"QFrame:hover{{background:{card_sel if selected else card_hover}}}"
                    f"QLabel{{background:transparent}}"
                )
                card.setCursor(Qt.CursorShape.PointingHandCursor)
                card.setMinimumWidth(0)
                card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                card.mousePressEvent = lambda e, si=sid: self._on_sess_click(e, si)
                card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                card.setProperty("sid", sid)
                card.customContextMenuRequested.connect(lambda pos: self._sess_menu(pos))
                row = QHBoxLayout(card)
                row.setContentsMargins(row_left, 8, row_right, 8)
                row.setSpacing(row_gap)
                is_running = self._session_is_running(sid)
                st = self._session_run_state.get(sid, "idle")
                if is_running:
                    st = "running"
                if selected:
                    # Selected: always blue/accent; reset completed/failed to idle
                    if st in ("success", "error"):
                        self._session_run_state[sid] = "idle"
                        st = "idle"
                    bar = SessionActivityBar(indicator, running=(st == "running"), state=st)
                else:
                    if st == "idle":
                        bar = SessionActivityBar(bar_idle, running=False, state="idle")
                    else:
                        bar = SessionActivityBar(indicator, running=(st == "running"), state=st)
                row.addWidget(bar)
                body = QWidget()
                body.setMinimumWidth(0)
                body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                hl = QVBoxLayout(body)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.setSpacing(2)
                title_font = QFont("Segoe UI", 12, QFont.Weight.DemiBold if selected else QFont.Weight.Medium)
                tlab = ElidedLabel(str(title or "Untitled"))
                tlab.setStyleSheet(f"color:{title_col};font-size:12px;font-weight:{700 if selected else 500};background:transparent")
                tlab.setFont(title_font)
                tlab.set_full_text(str(title or "Untitled"))
                tlab.setToolTip(str(title or "Untitled"))
                hl.addWidget(tlab)
                tl = QLabel(f"msgs: {cnt}  {short_updated}")
                tl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                tl.setStyleSheet(f"color:{meta_col};font-size:10px;background:transparent")
                hl.addWidget(tl)
                row.addWidget(body, 1)
                tl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                tlab.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                group_body_layout.addWidget(card)
            self.sess_layout.addWidget(group_body)
        self.sess_layout.addStretch()

    def _on_session_group_toggled(self, workspace_key: str, expanded: bool):
        self._session_group_collapsed[workspace_key] = not expanded
        self._refresh_sessions()

    def _new_session_for_workspace(self, workspace_key: str):
        home = os.path.expanduser("~")
        target_ws = workspace_key
        if target_ws in ("", "(No workspace)"):
            target_ws = home
        if target_ws and os.path.isdir(target_ws):
            self.config.workspace = os.path.abspath(target_ws)
            self.ws_lbl.setText(self._ws_display(self.config.workspace))
            self.file_tree.set_workspace(self.config.workspace)
        mode = self.config.mode
        session = self.store.create(title="", workspace=target_ws or home, mode=mode)
        self._load_session(session.id)

    def _message_display_text(self, msg: dict) -> str:
        if msg.get("display_text") is not None:
            return Agent.strip_skill_augmentation(str(msg.get("display_text") or ""))
        content = msg.get("content", "")
        if isinstance(content, str):
            return Agent.strip_skill_augmentation(content)
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    return Agent.strip_skill_augmentation(str(part.get("text", "")))
        return Agent.strip_skill_augmentation(str(content or ""))

    def _on_sess_click(self, event, sid):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # Reset completed/failed indicator when user clicks on the session
        st = self._session_run_state.get(sid, "idle")
        if st in ("success", "error"):
            self._session_run_state[sid] = "idle"
        self._load_session(sid)

    def _connect_worker_signals(self, w, sid, is_task_worker=False):
        """Connect worker signals with session_id context for multi-worker support.
        Display signals are gated: they only update UI when sid is the current session.
        State signals (done/error/finished) always fire to handle background save."""
        w.setProperty("_session_id", sid)
        # ── Display signals (gated by current session) ──
        w.text_delta.connect(lambda t, _sid=sid: self.chat.append_stream(t) if self._is_current_session(_sid) else None)
        w.tool_call.connect(lambda n, p, _sid=sid: self._on_tool_call_status(n, p) if self._is_current_session(_sid) else None)
        w.tool_result.connect(lambda r, err=False, _sid=sid: self._on_tool_result_status(r, err) if self._is_current_session(_sid) else None)
        w.diff_preview.connect(lambda p, o, n, _sid=sid: self.chat.add_diff(p, o, n) if self._is_current_session(_sid) else None)
        w.kscc_status.connect(lambda s, _sid=sid: self.chat.set_kscc_status(s) if self._is_current_session(_sid) else None)
        w.confirm_request.connect(lambda path, old, new, _sid=sid: self._on_confirm(path, old, new, _sid))
        w.context_info.connect(lambda j, _sid=sid: self._on_ctx(j) if self._is_current_session(_sid) else None)
        w.skill_info.connect(lambda p, _sid=sid: self._on_skill_info(p) if self._is_current_session(_sid) else None)
        w.skill_draft.connect(lambda p, _sid=sid: self._on_skill_draft(p) if self._is_current_session(_sid) else None)
        w.skill_auto_saved.connect(lambda sid2, sc, _sid=sid: self._on_skill_auto_saved(sid2, sc) if self._is_current_session(_sid) else None)
        w.memory_hits.connect(lambda p, _sid=sid: self._on_memory_hits(p) if self._is_current_session(_sid) else None)
        w.risk_template.connect(lambda p, _sid=sid: self._on_risk_template(p) if self._is_current_session(_sid) else None)
        w.file_modified.connect(lambda p, c, _sid=sid: self._on_fmod(p, c) if self._is_current_session(_sid) else None)
        # ── State signals (always fire for background save) ──
        w.done.connect(lambda text, turns, cj, _sid=sid: self._on_done(text, turns, cj, _sid))
        w.error.connect(lambda t, _sid=sid: self._on_err(t, _sid))
        w.finished.connect(lambda _sid=sid: self._on_fin(_sid))
        if is_task_worker:
            w.task_start.connect(lambda tid, goal, _sid=sid: self._on_task_start(tid, goal) if self._is_current_session(_sid) else None)
            w.plan_generated.connect(lambda pj, _sid=sid: self._on_plan_generated(pj, _sid))
            w.task_progress.connect(lambda pj, _sid=sid: self._on_task_progress(pj, _sid))
            w.task_complete.connect(lambda tid, rj, _sid=sid: self._on_task_complete(tid, rj) if self._is_current_session(_sid) else None)
            w.task_failed.connect(lambda tid, err, _sid=sid: self._on_task_failed(tid, err, _sid))
            w.task_resume.connect(lambda tid, goal, ri, _sid=sid: self._on_task_resume(tid, goal, ri) if self._is_current_session(_sid) else None)

    def _disconnect_worker_signals(self, w):
        """Disconnect all signals from a worker. Only call when stopping/destroying a worker."""
        if w is None:
            return
        try:
            # Block signals instead of wildcard disconnect to avoid Qt warnings
            w.blockSignals(True)
        except RuntimeError:
            pass  # C++ object already deleted

    def _session_is_running(self, sid: str) -> bool:
        """Check if a session has an active worker."""
        w = self._workers.get(sid)
        return w is not None and w.isRunning()

    def _is_current_session(self, sid: str) -> bool:
        """Check if sid matches the currently displayed session."""
        return bool(sid and self._cur_session and sid == self._cur_session.id)

    def _stop_worker_for_navigation(self):
        """Stop current session's worker for navigation. Background sessions keep running."""
        sid = self._cur_session.id if self._cur_session else None
        if sid and sid in self._workers:
            w = self._workers[sid]
            if w.isRunning():
                w.stop()
                w.wait(2000)
                if w.isRunning():
                    w.terminate()
                    w.wait(2000)
            self._disconnect_worker_signals(w)
            del self._workers[sid]
        self._agents.pop(sid, None)
        self._task_executors.pop(sid, None)
        self._running = False
        self._agent = None
        self._worker = None
        self._ctx_bind_sid = None
        self._pending_user_message_meta = None
        self.chat.end_stream()

    def _load_session(self, sid):
        self._suppress_tooltips_temporarily(1.2)
        # NOTE: Do NOT disconnect old worker signals — they stay connected and
        # gated by _is_current_session(). Background workers keep running.
        # Save current session state
        self._ctx_bind_sid = None
        self._pending_user_message_meta = None
        session = self.store.load(sid)
        if not session:
            return
        self.config._exclude_session_ids = []
        self._cur_session = session
        # Restore per-session state
        self._agent = self._agents.get(sid)
        self._worker = self._workers.get(sid)
        self._running = self._session_is_running(sid)
        # NOTE: Do NOT reconnect worker signals — they were connected at creation time.
        self._pending_skill_draft = None
        self.chat.show_save_skill_prompt(False)
        self._refresh_sessions()
        self.editor_tabs.clear_all_tabs()
        self.chat.clear_messages()
        # Choose message source: live agent.messages if running, else session.messages
        agent = self._agents.get(sid)
        if self._running and agent and agent.messages:
            source_messages = agent.messages
        else:
            source_messages = session.messages
        self.chat.begin_bulk_restore()
        try:
            for m in source_messages:
                # Skip internal task executor messages (planning prompts, execution prompts)
                if m.get("_internal"):
                    continue
                r = m.get("role", "")
                c = m.get("content", "")
                if r == "user":
                    self.chat.add_message("user", self._message_display_text(m), attachments=m.get("attachments") or [])
                elif r == "assistant":
                    if c:
                        self.chat.add_message("assistant", str(c), model_label=str(m.get("model_label", "") or ""))
                    for tc in m.get("tool_calls", []):
                        fn = tc.get("function", {})
                        n = fn.get("name", "")
                        if n == "edit_file":
                            try:
                                a = json.loads(fn.get("arguments", "{}"))
                            except Exception:
                                a = {}
                            if a.get("path"):
                                self.chat.add_diff(a["path"], a.get("old_string", ""), a.get("new_string", ""))
                elif r == "tool":
                    pass
        finally:
            self.chat.end_bulk_restore()
        # If this session is still running, show streaming state
        if self._running:
            model_label = ""
            if session.backend and session.model:
                model_label = f"{session.backend}/{session.model}"
            self.chat.start_stream(model_label=model_label)
            self.chat.send_btn.setEnabled(False)
        else:
            # Restore send/stop button state for non-running sessions
            self.chat.end_stream()
        if session.workspace and os.path.isdir(session.workspace):
            self.config.workspace = os.path.abspath(session.workspace)
            self.ws_lbl.setText(self._ws_display(self.config.workspace))
            self.file_tree.set_workspace(self.config.workspace)
        if session.backend:
            self.config.backend = str(session.backend)
        if session.backend == "kscc" and session.model:
            self.config.kscc_model = str(session.model)
        if session.backend == "openai" and session.model:
            self.config.openai_active = str(session.model)
        self._init_chat_selectors()
        self.slbl.setText(f"Session: {session.title[:36]}" if session.title.strip() else "Session")
        snap = getattr(session, "context_info", None)
        self._ctx_apply_snapshot(snap if isinstance(snap, dict) else None)
        self.chat.set_empty_state(
            not bool(source_messages),
            workspace=session.workspace or self.config.workspace,
            title=session.title if (session.title or "").strip() else "开始新会话",
            subtitle="从这里开始规划、构建与迭代。" if not source_messages else "",
        )
        self.chat.send_btn.setEnabled(not self._running)
        self._suppress_tooltips_temporarily(0.8)

    def _sess_menu(self, pos):
        snd = self.sender()
        if not snd:
            return
        sid = snd.property("sid")
        if not sid:
            return
        m = QMenu(self)
        light = str(getattr(self.config, "theme", "dark")).lower() == "light"
        polish_menu(m, "light" if light else "dark", font_size=11)
        da = m.addAction("Delete")
        if m.exec(snd.mapToGlobal(pos)) == da:
            if self._cur_session and self._cur_session.id == sid:
                self._new_session()
            # Stop background worker for deleted session
            if sid in self._workers:
                w = self._workers[sid]
                if w.isRunning():
                    w.stop()
                    w.wait(2000)
                self._disconnect_worker_signals(w)
                del self._workers[sid]
            self._agents.pop(sid, None)
            self._task_executors.pop(sid, None)
            self._session_run_state.pop(sid, None)
            self.store.delete(sid)
            self._refresh_sessions()

    def _new_session(self):
        # NOTE: Do NOT disconnect old worker signals — they stay connected and
        # gated by _is_current_session(). Background workers keep running.
        # Exclude previous session's archives from memory injection in the new session
        if self._cur_session:
            self.config._exclude_session_ids = [self._cur_session.id]
        else:
            self.config._exclude_session_ids = []
        self._cur_session = None
        self._agent = None
        self._worker = None
        self._running = False
        self._pending_skill_draft = None
        self.chat.show_save_skill_prompt(False)
        self.editor_tabs.clear_all_tabs()
        self.chat.clear_messages()
        # Reset workspace to Home (user home directory)
        home = os.path.expanduser("~")
        self.config.workspace = home
        os.chdir(home)
        self.ws_lbl.setText("Home")
        self.file_tree.set_workspace(home)
        self._refresh_sessions()
        self.slbl.setText("New session")
        self._ctx_bind_sid = None
        self._pending_user_message_meta = None
        self._ctx_clear()
        self.chat.set_empty_state(
            True,
            workspace=home,
            title="开始新会话",
            subtitle="可以直接提问、规划任务，或拖入文件开始。",
        )

    # ── Send / Agent ───────────────────────────────────────
    def _on_send(self, text, attachments=None):
        sid = self._cur_session.id if self._cur_session else None
        if sid and self._session_is_running(sid):
            return
        # 分类进行中，忽略重复发送
        if self._classify_worker and self._classify_worker.isRunning():
            return
        # 如果用户已手动开启 task mode，直接走 task 流程
        if self._task_mode:
            self._start_agent(text, attachments or [])
            return
        # 自动分类：判断是否需要 plan 模式
        prompt_str = str(text or "").strip()
        if not prompt_str and not attachments:
            return
        self._pending_send = {"prompt": text, "attachments": attachments or []}
        self._running = True
        self.chat.send_btn.setEnabled(False)
        self.slbl.setText("分析任务复杂度...")
        cw = ClassifyWorker(self.config, prompt_str or "请查看附件。")
        cw.finished.connect(self._on_classify_done)
        self._classify_worker = cw
        cw.start()

    def _on_classify_done(self, is_complex: bool):
        self._classify_worker = None
        pending = self._pending_send
        self._pending_send = None
        if not pending:
            self._running = False
            self.chat.send_btn.setEnabled(True)
            return
        # 如果用户在分类期间切换到了一个正在运行的会话，取消
        sid = self._cur_session.id if self._cur_session else None
        if sid and self._session_is_running(sid):
            self._running = False
            self.chat.send_btn.setEnabled(True)
            return
        # 自动分类结果：complex 时为当前消息启用 task，不影响全局 _task_mode
        self._auto_task = is_complex
        self._start_agent(pending["prompt"], pending["attachments"])

    def _start_agent(self, prompt, attachments_meta=None):
        self._running = True
        mode = self.config.mode
        attachments_meta = list(attachments_meta or [])
        prompt = str(prompt or "")
        agent_prompt = prompt if prompt.strip() else ("请查看附件。" if attachments_meta else prompt)
        self._last_agent_prompt = agent_prompt
        backend = self.chat.current_backend_label()
        model = self.chat.current_model_name()
        prev_backend = (self._cur_session.backend if self._cur_session else "") or ""
        prev_model = (self._cur_session.model if self._cur_session else "") or ""
        next_backend_key = "kscc" if backend == "Kscc" else "openai"
        switched_backend = bool(prev_backend and prev_backend != next_backend_key)
        switched_model = bool(prev_model and prev_model != model)
        switched_runtime = bool(prev_backend and (switched_backend or switched_model))

        if switched_backend:
            anchor = self._build_backend_switch_anchor()
            if anchor:
                agent_prompt = (
                    f"{agent_prompt}\n\n"
                    "[后端切换上下文锚点]\n"
                    "你正在同一会话中切换模型后端后继续对话。\n"
                    "请使用此锚点保持上下文连续性；若有冲突，以用户最新请求为准。\n"
                    f"{anchor}"
                )

        if backend == "Kscc":
            self.config.backend = "kscc"
            self.config.kscc_model = model
            self.editor._js("window._disableCompletions()")
        else:
            self.config.backend = "openai"
            self.config.openai_active = model
            self.editor._js("window._enableCompletions()")

        self.model_lbl.setText(f"{backend}/{model}")
        self._refresh_sessions()

        if self._cur_session is None:
            self._cur_session = self.store.create(title="", workspace=self.config.workspace, mode=mode)
        sid = self._cur_session.id
        self._session_run_state[sid] = "running"
        self._cur_session.workspace = os.path.abspath(self.config.workspace)
        self._cur_session.backend = "kscc" if backend == "Kscc" else "openai"
        self._cur_session.model = model
        self.store.save(self._cur_session)
        self._refresh_sessions()
        self._pending_user_message_meta = {
            "display_text": prompt,
            "attachments": [dict(a) for a in attachments_meta],
        }
        if switched_runtime and self._cur_session is not None:
            evt = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "from_backend": prev_backend,
                "from_model": prev_model,
                "to_backend": next_backend_key,
                "to_model": model,
            }
            try:
                self._cur_session.model_switches.append(evt)
            except Exception:
                self._cur_session.model_switches = [evt]
            self.chat.add_message(
                "tool",
                f"模型已切换: {prev_backend}/{prev_model or '-'} -> {next_backend_key}/{model or '-'}",
            )

        self.chat.send_btn.setEnabled(False)
        self.chat.start_stream(model_label=f"{backend}/{model}")
        self.slbl.setText("执行中...")
        # Dispose previous agent for this session if any
        prev_agent = self._agents.pop(sid, None)
        if prev_agent:
            try:
                bk = getattr(prev_agent, "backend", None)
                close_fn = getattr(bk, "close", None)
                if callable(close_fn):
                    close_fn()
            except Exception:
                pass
        try:
            rmsgs = self._cur_session.messages if self._cur_session.messages else None
            agent = Agent(config=self.config, mode=mode, resume_messages=rmsgs)
        except Exception as e:
            self.chat.add_error(f"Failed: {e}")
            self.chat.send_btn.setEnabled(True)
            self._running = False
            return
        self._agent = agent
        self._agents[sid] = agent
        self._ctx_bind_sid = sid

        use_task = self._task_mode or self._auto_task
        self._auto_task = False  # 一次性标记，用完即清

        if use_task:
            # 自动 task 时显示 task 面板并高亮按钮
            if not self._task_mode:
                self.task_panel.show()
                QTimer.singleShot(0, self._ensure_task_panel_default_width)
                # 设置按钮 checked 样式（不触发 _on_task_mode_toggle）
                self._task_mode_btn.blockSignals(True)
                self._task_mode_btn.setChecked(True)
                self._task_mode_btn.blockSignals(False)
            _ws = self.config.workspace or os.path.dirname(os.path.abspath(__file__))
            task_exec = TaskExecutor(agent, config=self.config, log_dir=os.path.join(_ws, "logs", "tasks"))
            self._last_task_executor = task_exec
            self._task_executors[sid] = task_exec
            w = TaskWorker(task_exec, agent_prompt, [a.get("path", "") for a in attachments_meta if a.get("path")])
            self._worker = w
            self._workers[sid] = w
            self._connect_worker_signals(w, sid, is_task_worker=True)
            w.start()
        else:
            w = AgentWorker(agent, agent_prompt, [a.get("path", "") for a in attachments_meta if a.get("path")])
            self._worker = w
            self._workers[sid] = w
            self._connect_worker_signals(w, sid)
            w.start()
        # Refresh AFTER worker starts so _session_is_running returns True for the indicator
        self._refresh_sessions()
        if switched_runtime:
            self.slbl.setText(
                f"后端已切换: {prev_backend}/{prev_model or '-'} -> {next_backend_key}/{model or '-'}（上下文连续性：尽力保持）"
            )

    def _build_backend_switch_anchor(self) -> str:
        sess = self._cur_session
        if not sess or not isinstance(sess.messages, list):
            return ""
        rows: list[str] = []
        for msg in sess.messages:
            role = str(msg.get("role", "") or "")
            if role not in ("user", "assistant"):
                continue
            if role == "user":
                text = str(msg.get("display_text", msg.get("content", "")) or "").strip()
            else:
                text = str(msg.get("content", "") or "").strip()
            if not text:
                continue
            text = text.replace("\n", " ")
            if len(text) > 180:
                text = text[:177] + "..."
            rows.append(f"- {role}: {text}")
        if not rows:
            return ""
        return "最近轮次:\n" + "\n".join(rows[-6:])

    def _on_ctx(self, sj):
        try:
            s = json.loads(sj)
        except (json.JSONDecodeError, TypeError):
            return
        if self._cur_session is None or getattr(self, "_ctx_bind_sid", None) != self._cur_session.id:
            return
        # 提取压缩提示字段（不传给 ring widget）
        warning = s.pop("_warning", None)
        note = s.pop("_note", None)
        if warning:
            self.chat.add_context_hint("", "compressing")
            self.ctx_ring.set_compression_status("compressing")
        elif note:
            self.chat.add_context_hint("", "done")
            self.ctx_ring.set_compression_status("done")
        self._ctx_set_from_json(json.dumps(s))
        self._cur_session.context_info = s

    def _on_confirm(self, path, old, new, sid=None):
        agent = self._agents.get(sid) if sid else self._agent
        # Background session: auto-reject to avoid blocking the agent
        if not self._is_current_session(sid):
            if agent:
                agent.reject()
            return
        def accept():
            if agent:
                agent.approve()

        def reject():
            if agent:
                agent.reject()

        self.chat.add_review_card(path, old, new, accept, reject)

    def _on_done(self, text, turns, cj, sid=None):
        if sid and self._cur_session and sid == self._cur_session.id:
            self._ctx_bind_sid = None
        if sid:
            self._session_run_state[sid] = "success"
        # Only update UI if this is the current session
        is_current = sid and self._cur_session and sid == self._cur_session.id
        if is_current:
            self.chat.end_stream()
            self.chat.set_kscc_status("")
            self.slbl.setText(f"Done - {turns} turns")
            try:
                c = json.loads(cj)
                self.tlbl.setText(f"in:{_fmt_k(c.get('total_input'))} out:{_fmt_k(c.get('total_output'))}")
            except Exception:
                pass
        # Save session using the correct agent
        agent = self._agents.get(sid) if sid else self._agent
        session = self._cur_session if is_current else None
        if not session and sid:
            # Load session from store for background save
            session = self.store.load(sid)
        if session and agent and agent.messages:
            # Filter out internal task executor messages before saving
            session.messages = [m for m in agent.messages if not m.get("_internal")]
            # Strip skill augmentation from user messages for clean display on reload
            for msg in session.messages:
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        msg["content"] = Agent.strip_skill_augmentation(content)
            # Apply display_text/attachments from _pending_user_message_meta before saving
            if is_current and self._pending_user_message_meta:
                for msg in reversed(session.messages):
                    if msg.get("role") == "user":
                        msg["display_text"] = self._pending_user_message_meta.get("display_text", "")
                        msg["attachments"] = [dict(a) for a in self._pending_user_message_meta.get("attachments", [])]
                        break
                self._pending_user_message_meta = None
            try:
                for msg in reversed(session.messages):
                    if msg.get("role") == "assistant":
                        msg["model_label"] = ""
                        break
            except Exception:
                pass
            if not session.title or session.title in ("New Session", "New Chat"):
                session.title = self.store.auto_title(agent.messages)
            self.store.save(session)
            if is_current:
                self._refresh_sessions()
        if bool(getattr(self.config, "memory_injection_enabled", True)):
            try:
                archive = {
                    "session_id": sid or "",
                    "title": session.title if session else "",
                    "user_prompt": self._last_agent_prompt,
                    "summary": text,
                    "turns": turns,
                }
                memory_store.append_archive(
                    session_id=archive["session_id"],
                    title=archive["title"],
                    user_prompt=archive["user_prompt"],
                    summary=archive["summary"],
                    turns=turns,
                    workspace=self.config.workspace,
                )
                # P3-1: Extract and store insights from this conversation
                try:
                    insights = insight_index.extract_insights_from_archive(archive)
                    if insights:
                        insight_index.append_insights(insights)
                except Exception:
                    pass
                # P3-3: Periodic compression (every 10th archive)
                try:
                    if getattr(self.config, 'feature_memory_compress', True):
                        all_archives = memory_store.load_recent_archives(limit=0)
                        if len(all_archives) % 10 == 0:
                            memory_store.compress_old_archives()
                except Exception:
                    pass
            except Exception:
                pass

    # ── Task mode event handlers ──────────────────────────────────

    def _on_task_start(self, task_id, goal):
        self.task_panel.clear()
        self.slbl.setText(f"Task {task_id}: Planning...")

    def _on_plan_generated(self, plan_json, sid=None):
        """计划生成后立即刷新 task panel 显示步骤列表"""
        is_current = sid and self._cur_session and sid == self._cur_session.id
        if not is_current:
            return
        task_exec = self._task_executors.get(sid)
        if task_exec:
            task_state = task_exec.get_current_task()
            if task_state:
                self.task_panel.update_task_state(task_state)

    def _on_task_progress(self, progress_json, sid=None):
        is_current = sid and self._cur_session and sid == self._cur_session.id
        try:
            progress = json.loads(progress_json)
            phase = progress.get("phase", "")
            tool_count = progress.get("tool_count", 0)
            if is_current:
                if phase == "executing":
                    self.slbl.setText(f"Executing... ({tool_count} tools used)")
                elif phase == "completed":
                    self.slbl.setText("Execution complete.")
                elif phase == "max_turns":
                    self.slbl.setText("Reached max turns, finalizing...")
        except Exception:
            pass
        # Refresh task panel and update step progress
        if is_current:
            task_exec = self._task_executors.get(sid)
            if task_exec:
                task_state = task_exec.get_current_task()
                if task_state:
                    # 根据 tool_count 推进步骤状态
                    self._advance_step_progress(task_state, tool_count, phase)
                    self.task_panel.update_task_state(task_state)

    def _advance_step_progress(self, task_state, tool_count, phase=""):
        """根据工具调用次数推进步骤状态（启发式追踪）"""
        from task_state import StepStatus, StepResult
        steps = task_state.steps
        if not steps:
            return
        # 完成/失败阶段：标记所有步骤为完成
        if phase in ("completed", "max_turns"):
            for step in steps:
                if step.status != StepStatus.SUCCESS:
                    step.mark_success(StepResult(success=True, output="Completed"))
            return
        total = len(steps)
        # 第一个工具调用时标记 step_1 为 RUNNING
        if tool_count == 0:
            return
        # 计算当前应该执行到哪一步（每 3 个工具调用推进一步，至少每个步骤 1 个工具调用）
        steps_to_advance = min(tool_count // 3, total)
        if steps_to_advance == 0 and tool_count > 0:
            steps_to_advance = 1
        for i, step in enumerate(steps):
            if i < steps_to_advance - 1:
                # 已完成的步骤
                if step.status != StepStatus.SUCCESS:
                    step.mark_success(StepResult(success=True, output="Completed"))
            elif i == steps_to_advance - 1:
                # 当前正在执行的步骤
                if step.status == StepStatus.PENDING:
                    step.mark_running()
            # i >= steps_to_advance 的步骤保持 PENDING

    def _on_task_complete(self, task_id, result):
        self.slbl.setText(f"Task {task_id}: Complete")
        self._resume_btn_hide()
        # 自动 task 结束，恢复按钮状态
        if not self._task_mode:
            self._task_mode_btn.blockSignals(True)
            self._task_mode_btn.setChecked(False)
            self._task_mode_btn.blockSignals(False)
        # 标记所有步骤为完成
        task_exec = self._task_executors.get(self._cur_session.id if self._cur_session else None)
        if task_exec:
            task_state = task_exec.get_current_task()
            if task_state:
                from task_state import StepStatus, StepResult
                for step in task_state.steps:
                    if step.status != StepStatus.SUCCESS:
                        step.mark_success(StepResult(success=True, output="Completed"))
                self.task_panel.update_task_state(task_state)

    def _on_task_failed(self, task_id, error, sid=None):
        is_current = sid and self._cur_session and sid == self._cur_session.id
        if is_current:
            self.slbl.setText(f"Task {task_id}: Failed - {error[:80]}")
            # 自动 task 失败，恢复按钮状态
            if not self._task_mode:
                self._task_mode_btn.blockSignals(True)
                self._task_mode_btn.setChecked(False)
                self._task_mode_btn.blockSignals(False)
            # 显示恢复按钮
            task_exec = self._task_executors.get(sid) or self._last_task_executor
            if task_exec:
                task_state = task_exec.get_current_task()
                if task_state:
                    self.task_panel.show_resume(task_state)

    def _on_task_resume(self, task_id, goal, resume_info):
        plan = resume_info.get("plan", "")
        plan_short = plan[:80] + "..." if len(plan) > 80 else plan
        self.slbl.setText(f"Resuming task {task_id}")
        self.chat.append_stream(f"\n\n--- Resuming task with previous plan: {plan_short} ---\n\n")

    def _resume_task(self):
        """恢复失败的任务"""
        state = self.task_panel.get_resumable_state()
        if not state:
            return

        sid = self._cur_session.id if self._cur_session else None
        if sid and self._session_is_running(sid):
            return

        # 用当前 agent 创建新的 TaskExecutor
        agent = self._agents.get(sid) if sid else self._agent
        if not agent:
            return

        _ws = self.config.workspace or os.path.dirname(os.path.abspath(__file__))
        task_exec = TaskExecutor(agent, config=self.config, log_dir=os.path.join(_ws, "logs", "tasks"))
        self._last_task_executor = task_exec
        if sid:
            self._task_executors[sid] = task_exec

        w = TaskWorker(task_exec, "", resume_state=state)
        self._worker = w
        if sid:
            self._workers[sid] = w
        self._connect_worker_signals(w, sid, is_task_worker=True)

        self._running = True
        self.chat.send_btn.setEnabled(False)
        self._resume_btn_hide()
        self.task_panel.clear()
        w.start()

    def _resume_btn_hide(self):
        """隐藏恢复按钮"""
        self.task_panel._resume_btn.hide()
        self.task_panel._resumable_state = None

    def _on_stop(self):
        # 停止分类线程（如果正在分类）
        if self._classify_worker and self._classify_worker.isRunning():
            self._classify_worker.terminate()
            self._classify_worker.wait(2000)
            self._classify_worker = None
            self._pending_send = None
        sid = self._cur_session.id if self._cur_session else None
        if sid and sid in self._workers:
            w = self._workers[sid]
            if w.isRunning():
                w.stop()
                w.wait(2000)
                if w.isRunning():
                    w.terminate()
                    w.wait(2000)
        self._ctx_bind_sid = None
        self._pending_user_message_meta = None
        if sid:
            self._session_run_state[sid] = "idle"
        self._running = False
        self._worker = None
        self.chat.end_stream()
        self._refresh_sessions()
        self.slbl.setText("Stopped")

    def _on_err(self, t, sid=None):
        is_current = sid and self._cur_session and sid == self._cur_session.id
        if is_current:
            self._ctx_bind_sid = None
            self._pending_user_message_meta = None
        if sid:
            self._session_run_state[sid] = "error"
        if is_current:
            self.chat.add_error(t)
            self.chat.end_stream()
            self.slbl.setText("Error")
            self._running = False
            self._refresh_sessions()

    def _on_skill_info(self, payload: str):
        try:
            e = json.loads(payload)
        except Exception:
            return
        t = str(e.get("type", ""))
        if t == "skill_match":
            self.slbl.setText(f"Skill matched: {e.get('skill_id', '')}")
        elif t == "skill_miss":
            hint = str(e.get("hint", "") or "")
            self.slbl.setText(f"Skill miss: {hint[:80] or e.get('reason', '')}")
        elif t == "skill_ambiguous":
            self.slbl.setText("Skill candidates close: using top match")
        elif t == "skill_status":
            self.slbl.setText("Skills disabled")

    def _on_skill_draft(self, payload: str):
        try:
            draft = json.loads(payload)
        except Exception:
            return
        self._pending_skill_draft = draft
        # 显示评分信息
        score = draft.get("score", {})
        if score:
            label = score.get("label", "")
            total = score.get("total", 0)
            btn_text = f"Save Skill ({label} {total:.0f}分)"
            self.slbl.setText(f"Skill suggestion: {label} ({total:.0f}/100)")
        else:
            btn_text = "Save Skill"
            self.slbl.setText("Skill suggestion ready")
        self.chat.show_save_skill_prompt(True, btn_text)

    def _on_skill_auto_saved(self, skill_id: str, score: float):
        """处理自动保存的 skill"""
        self.slbl.setText(f"Skill auto-saved: {skill_id} (score: {score:.0f})")
        # 如果有待保存的草稿，标记为已自动保存并隐藏按钮
        if self._pending_skill_draft:
            self._pending_skill_draft["auto_saved"] = True
            self._pending_skill_draft["saved_id"] = skill_id
            self.chat.show_save_skill_prompt(False)

    def _on_memory_hits(self, payload: str):
        """P3-5: Display memory hit summary in status bar (not in chat)."""
        try:
            hits = json.loads(payload)
        except Exception:
            return
        parts = []
        if hits.get("rules_count", 0):
            parts.append(f"{hits['rules_count']} rules")
        if hits.get("facts_count", 0):
            parts.append(f"{hits['facts_count']} facts")
        if hits.get("insights_count", 0):
            parts.append(f"{hits['insights_count']} insights")
        if hits.get("archives_count", 0):
            parts.append(f"{hits['archives_count']} archives")
        task_types = hits.get("task_types", [])
        type_str = f" [{','.join(task_types)}]" if task_types else ""
        if parts:
            self.slbl.setText(f"Memory: {', '.join(parts)}{type_str}")

    def _on_risk_template(self, payload: str):
        """P4-5: Display risk template warning in status bar."""
        try:
            info = json.loads(payload)
        except Exception:
            return
        name = info.get("name", "敏感操作")
        risk = info.get("risk_level", "high")
        self.slbl.setText(f"Risk: {name} ({risk})")

    def _on_tool_call_status(self, name: str, preview: str):
        """Show current tool execution in status bar (replaces previous)."""
        self.slbl.setText(f"Tool: {preview[:60]}")

    def _on_tool_result_status(self, result: str, error: bool = False):
        """Show tool error in status bar; ignore success."""
        if error:
            self.slbl.setText(f"Error: {result[:80]}")

    def _open_skill_save_dialog(self):
        draft = self._pending_skill_draft
        if not isinstance(draft, dict) or not draft:
            self.chat.show_save_skill_prompt(False)
            return
        dlg = SkillSaveDialog(draft, self)
        dlg.exec()
        if dlg.did_save():
            self.slbl.setText("Skill saved")
            self._pending_skill_draft = None
            self.chat.show_save_skill_prompt(False)

    def _on_fin(self, sid=None):
        is_current = sid and self._cur_session and sid == self._cur_session.id
        if is_current:
            self._ctx_bind_sid = None
            self.chat.send_btn.setEnabled(True)
            self.chat.end_stream()
            self._worker = None
            self._running = False
        # Clean up per-session state (_on_done fires before _on_fin, so agent is safe to pop)
        if sid:
            self._workers.pop(sid, None)
            self._agents.pop(sid, None)
            self._task_executors.pop(sid, None)
        self._refresh_sessions()

    def _dispose_agent_runtime(self):
        ag = getattr(self, "_agent", None)
        if ag is None:
            return
        try:
            backend = getattr(ag, "backend", None)
            close_fn = getattr(backend, "close", None)
            if callable(close_fn):
                close_fn()
        except Exception:
            pass
        self._agent = None

    def _on_fmod(self, path, content):
        ws = self.config.workspace
        fp = os.path.join(ws, path) if not os.path.isabs(path) else path
        if os.path.exists(fp):
            self.editor_tabs.open_file(fp)

    def _save(self):
        if not self._cur_session or not self._agent or not self._agent.messages:
            return
        # Filter out internal task executor messages before saving
        self._cur_session.messages = [m for m in self._agent.messages if not m.get("_internal")]
        # Strip skill augmentation from user messages for clean display on reload
        for msg in self._cur_session.messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    msg["content"] = Agent.strip_skill_augmentation(content)
        try:
            bk = self.chat.current_backend_label()
            md = self.chat.current_model_name()
            mdl = f"{bk}/{md}" if bk and md else ""
            for msg in reversed(self._cur_session.messages):
                if msg.get("role") == "assistant":
                    msg["model_label"] = mdl
                    break
        except Exception:
            pass
        if self._pending_user_message_meta:
            for msg in reversed(self._cur_session.messages):
                if msg.get("role") == "user":
                    msg["display_text"] = self._pending_user_message_meta.get("display_text", "")
                    msg["attachments"] = [dict(a) for a in self._pending_user_message_meta.get("attachments", [])]
                    break
            self._pending_user_message_meta = None
        self._cur_session.mode = self.config.mode
        self._cur_session.workspace = os.path.abspath(self.config.workspace)
        try:
            bk = self.chat.current_backend_label()
            md = self.chat.current_model_name()
            self._cur_session.backend = "kscc" if bk == "Kscc" else "openai"
            self._cur_session.model = md
        except Exception:
            pass
        if not self._cur_session.title or self._cur_session.title in ("New Session", "New Chat"):
            self._cur_session.title = self.store.auto_title(self._agent.messages)
        self.store.save(self._cur_session)
        self._refresh_sessions()

    def _show_main_page(self):
        if hasattr(self, "content_stack"):
            self.content_stack.setCurrentIndex(0)
        self._sync_settings_toolbar_button()

    def _show_settings_page(self):
        if hasattr(self, "settings_page"):
            self.settings_page.reload_from_config()
        if hasattr(self, "content_stack"):
            self.content_stack.setCurrentIndex(1)
        self._sync_settings_toolbar_button()

    def _show_metrics_page(self):
        if hasattr(self, "content_stack"):
            self.content_stack.setCurrentIndex(2)
        self._sync_settings_toolbar_button()

    def _show_audit_page(self):
        if hasattr(self, "content_stack"):
            self.content_stack.setCurrentIndex(3)
        self._sync_settings_toolbar_button()

    def _show_skills_page(self):
        if hasattr(self, "content_stack"):
            self.content_stack.setCurrentIndex(4)
        self._sync_settings_toolbar_button()

    def _sync_settings_toolbar_button(self):
        if not hasattr(self, "settings_btn") or not hasattr(self, "content_stack"):
            return
        idx = self.content_stack.currentIndex()
        if idx != 0:
            self.settings_btn.setText("返回")
            self.settings_btn.setIcon(quark_icon("arrow_left", 18))
            self.settings_btn.setToolTip("返回")
        else:
            self.settings_btn.setText("设置")
            self.settings_btn.setIcon(quark_icon("settings", 18))
            self.settings_btn.setToolTip("设置")
        if hasattr(self, "sbar"):
            self.sbar.setVisible(idx == 0)
        self._sync_shell_chrome()

    def _on_settings_saved(self):
        p = get_active_provider(self.config)
        self.model_lbl.setText(f"OpenAI/{p.model}" if self.config.backend == "openai" else f"Kscc/{self.config.kscc_model}")
        self.file_tree.set_workspace(self.config.workspace)
        self._init_chat_selectors()
        self._apply()
        # Restart browser driver if feature flag changed
        self._sync_browser_driver()
        self._show_main_page()

    def _start_browser_driver(self):
        """Start the browser CDP driver if the feature is enabled."""
        if not getattr(self.config, 'feature_browser_tools', False):
            return
        try:
            from browser_driver import start_browser_driver
            ok = start_browser_driver()
            if not ok:
                print("[App] Browser driver failed to start. Check simple-websocket-server is installed.")
        except ImportError as e:
            print(f"[App] Browser driver import error: {e}")
        except Exception as e:
            print(f"[App] Browser driver start error: {e}")

    def _stop_browser_driver(self):
        """Stop the browser CDP driver."""
        try:
            from browser_driver import stop_browser_driver
            stop_browser_driver()
        except Exception:
            pass

    def _sync_browser_driver(self):
        """Sync browser driver state with the feature flag."""
        try:
            from browser_driver import get_browser_driver
            driver = get_browser_driver()
            enabled = getattr(self.config, 'feature_browser_tools', False)
            if enabled and not driver.is_running:
                self._start_browser_driver()
            elif not enabled and driver.is_running:
                self._stop_browser_driver()
        except Exception:
            pass

    def _settings(self):
        if hasattr(self, "content_stack") and self.content_stack.currentIndex() != 0:
            self._show_main_page()
        else:
            self._show_settings_page()

    def closeEvent(self, event):
        self._stop_browser_driver()
        event.accept()



# ── Entry ───────────────────────────────────────────────────
def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("kscc.ui")
        except Exception:
            pass
    _suppress_tk_root_window()
    app = QApplication(sys.argv)
    app.setOrganizationName("Kscc")
    app.setApplicationName("KsccUI")
    icon_path = Path(__file__).parent / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    fp = Path(__file__).parent / "fonts"
    if fp.exists():
        for f in fp.glob("*.ttf"):
            QFontDatabase.addApplicationFont(str(f))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
