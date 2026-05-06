"""
Kscc Agent - PyQt6 Desktop Shell
"""

import asyncio, json, math, os, re, subprocess, sys, threading, uuid
import html as _html
from datetime import datetime
from pathlib import Path
from typing import Optional
import memory_store

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QTextEdit, QPushButton, QTreeView, QScrollArea, QFrame, QFileDialog,
    QMenu, QSizePolicy, QToolBar, QStatusBar, QDialog, QFormLayout,
    QLineEdit, QComboBox, QDialogButtonBox, QSpinBox, QListWidget, QListWidgetItem, QCheckBox, QToolButton, QColorDialog, QFontComboBox,
    QMessageBox,
    QStyledItemDelegate, QStyle, QStyleOptionViewItem, QWidgetAction, QTabBar, QStackedWidget, QToolTip, QListView,
)
from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QThread, pyqtSignal, QTimer, QSize, QUrl, QSettings
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

# 与 kscc UI composer 一致（chat_widgets.CODE_FONT_STACK）
_CODE_FONT_STACK = (
    "ui-monospace,'SFMono-Regular',Menlo,Monaco,Consolas,"
    "'Liberation Mono','Courier New',monospace"
)

APP_VERSION = "v1.4.0"
SETTINGS_ICON_DARK = Path(__file__).parent / "setting.png"
SETTINGS_ICON_LIGHT = Path(__file__).parent / "setting_black.png"
ATTACHMENTS_DIR = Path(__file__).parent / "sessions" / "_attachments"
MONACO_PACKAGE_JSON = Path(__file__).parent / "node_modules" / "monaco-editor" / "package.json"
PROJECT_PACKAGE_JSON = Path(__file__).parent / "package.json"


def _is_light_theme() -> bool:
    app = QApplication.instance()
    if app is None:
        return False
    return str(app.property("theme_mode") or "dark").lower() == "light"


def _tooltip_css(light: Optional[bool] = None) -> str:
    if light is None:
        light = _is_light_theme()
    tip_bg = "#ffffff" if light else "#111827"
    tip_fg = "#111827" if light else "#eef4f8"
    tip_border = "#d1d5db" if light else "#334155"
    return f"QToolTip{{background-color:{tip_bg};color:{tip_fg};border:1px solid {tip_border};padding:4px 6px;font-size:11px;}}"


def _with_tooltip_style(css: str, light: Optional[bool] = None) -> str:
    return css + _tooltip_css(light)


def _fmt_k(value) -> str:
    """Format token/context counts in K units."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—" if value is None else str(value)
    k = n / 1000.0
    s = f"{k:.2f}".rstrip("0").rstrip(".")
    return f"{s}K"


def _make_plus_icon(size=14, color="#d4d7dd"):
    """Kscc UI attach 按钮使用的十字图标。"""
    icon_size = max(int(size), 10)
    pix = QPixmap(icon_size, icon_size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(max(1.4, icon_size * 0.14))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    c = icon_size / 2.0
    r = icon_size * 0.28
    painter.drawLine(QPointF(c - r, c), QPointF(c + r, c))
    painter.drawLine(QPointF(c, c - r), QPointF(c, c + r))
    painter.end()
    return QIcon(pix)


def _settings_icon(light: bool, size: int = 18) -> QIcon:
    src = SETTINGS_ICON_LIGHT if light else SETTINGS_ICON_DARK
    if src.exists():
        pm = QPixmap(str(src))
        if not pm.isNull():
            return QIcon(pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
    return quark_icon("settings", size, "#000000" if light else "#ffffff")


def _ensure_attachments_dir() -> Path:
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    return ATTACHMENTS_DIR


def _is_image_path(path: str) -> bool:
    return Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def _attachment_meta(path: str, source: str = "file") -> dict:
    ap = os.path.abspath(path)
    return {
        "path": ap,
        "name": Path(ap).name or ap,
        "is_image": _is_image_path(ap),
        "source": source,
    }

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    HAS_WEBENGINE = True
except ImportError:
    HAS_WEBENGINE = False

from agent import Agent
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
from skills_ui import SkillSaveDialog, SkillsManagerDialog
import re as _re

from theme import (
    STYLESHEET,
    build_stylesheet,
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

# ── MonacoPage ──────────────────────────────────────────────
if HAS_WEBENGINE:
    class MonacoPage(QWebEnginePage):
        js_message = pyqtSignal(object)
        def javaScriptConsoleMessage(self, level, message, line, src):
            text = str(message).strip()
            for prefix in ('{', '['):
                if text.startswith(prefix):
                    try:
                        data = json.loads(text)
                        if isinstance(data, dict) and 't' in data:
                            self.js_message.emit(data); return
                        if isinstance(data, list):
                            for m in data:
                                if isinstance(m, dict) and 't' in m: self.js_message.emit(m)
                            return
                    except (json.JSONDecodeError, ValueError): pass
            super().javaScriptConsoleMessage(level, message, line, src)

# ── MonacoEditor ────────────────────────────────────────────
class MonacoEditor(QWidget):
    content_changed = pyqtSignal(str)
    save_requested = pyqtSignal(str, str)
    ask_selection  = pyqtSignal(str, str)
    ask_file       = pyqtSignal(str)
    file_opened    = pyqtSignal(str, str)
    ready          = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path = ""
        self.setStyleSheet("background: transparent;")
        l = QVBoxLayout(self); l.setContentsMargins(0,0,0,0)
        if not HAS_WEBENGINE: l.addWidget(QLabel("PyQt6-WebEngine required")); self.webview = None; return
        self.page = MonacoPage(self); self.page.js_message.connect(self._on_msg)
        self.webview = QWebEngineView(); self.webview.setPage(self.page)
        s = self.page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        hp = Path(__file__).parent / "monaco.html"
        if hp.exists(): self.webview.load(QUrl.fromLocalFile(str(hp)))
        l.addWidget(self.webview)

    def _on_msg(self, m):
        t, d = m.get('t',''), m.get('d',{})
        if t=='content_changed': self.content_changed.emit(str(d))
        elif t=='save_requested':
            content = str(d)
            self.content_changed.emit(content)
            self.save_requested.emit(self._current_path, content)
        elif t=='ask_selection': self.ask_selection.emit(str(d.get('file','')), str(d.get('text','')))
        elif t=='ask_file': self.ask_file.emit(str(d.get('file','')))
        elif t=='file_opened':
            self._current_path = str(d.get('path',''))
            self.file_opened.emit(self._current_path, str(d.get('language','')))
        elif t=='ready':
            self.ready.emit()
        elif t=='completion_request': self._on_completion(d)
        elif t=='error': print(f"[Monaco] {d}")

    def set_file(self, path, content=""):
        self._current_path = str(path or "")
        if not content:
            try: content = Path(path).read_text("utf-8", errors="replace")
            except: content = ""
        self._js(f"window.setFile({json.dumps(path)},{json.dumps(content)})")

    def _js(self, code, cb=None):
        if self.webview:
            if cb: self.webview.page().runJavaScript(code, cb)
            else: self.webview.page().runJavaScript(code)

    def _on_completion(self, d):
        from config import load_config
        cfg = load_config()
        if cfg.backend == "kscc":
            return  # kscc 不走补全
        req_id = d.get('id', 0)
        context = str(d.get('context', ''))
        language = str(d.get('language', ''))
        threading.Thread(target=self._do_completion, args=(req_id, context, language), daemon=True).start()

    def _do_completion(self, req_id, context, language):
        import httpx
        from config import get_active_provider, load_config
        try:
            cfg = load_config()
            if cfg.backend == "kscc":
                self._js(f"window._resolveCompletion({req_id}, '')"); return
            prov = get_active_provider(cfg)
            if not prov.api_key:
                self._js(f"window._resolveCompletion({req_id}, '')"); return
            prompt = f"Complete this {language} code. Return ONLY completion, no explanation:\n\n```{language}\n{context}\n```"
            r = httpx.post(f"{prov.base_url}/chat/completions", json={
                "model": prov.model, "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 128, "temperature": 0.1, "stream": False,
            }, headers={"Authorization": f"Bearer {prov.api_key}"}, timeout=6)
            if r.status_code == 200:
                text = r.json()["choices"][0]["message"]["content"].strip()
                # strip code fences
                text = text.replace("```", "").strip()
                self._js(f"window._resolveCompletion({req_id}, {json.dumps(text)})")
            else:
                self._js(f"window._resolveCompletion({req_id}, '')")
        except Exception:
            self._js(f"window._resolveCompletion({req_id}, '')")


class EditorTabHost(QWidget):
    """IDE 顶栏多标签 + 单一 Monaco 实例；切换标签时在内存中交换缓冲区。"""
    save_requested = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(200)
        self._paths: list[str] = []
        self._buffers: dict[str, str] = {}
        self._clean_snapshots: dict[str, str] = {}
        self._dirty_paths: set[str] = set()
        self._tab_prev_idx = -1

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self.tab_bar = QTabBar()
        self.tab_bar.setDocumentMode(True)
        self.tab_bar.setMovable(True)
        self.tab_bar.setExpanding(False)
        self.tab_bar.setTabsClosable(True)
        self.tab_bar.setUsesScrollButtons(True)
        self.apply_theme()
        self.tab_bar.currentChanged.connect(self._on_tab_changed)
        self.tab_bar.tabCloseRequested.connect(self._on_tab_close)
        self.tab_bar.tabMoved.connect(self._on_tab_moved)

        self.editor = MonacoEditor(self)
        self.editor.content_changed.connect(self._on_editor_dirty)
        self.editor.save_requested.connect(self._on_editor_save_requested)

        v.addWidget(self.tab_bar)
        v.addWidget(self.editor, 1)

    def apply_theme(self):
        light = _is_light_theme()
        tab_bg = "rgba(0,0,0,0.04)" if light else "rgba(255,255,255,0.06)"
        tab_bg_sel = "rgba(12,74,110,0.12)" if light else "rgba(255,255,255,0.11)"
        tab_bg_hover = "rgba(0,0,0,0.08)" if light else "rgba(255,255,255,0.09)"
        tab_text = "#334155" if light else C_DIM
        tab_text_sel = "#0f172a" if light else C_TEXT
        self.tab_bar.setStyleSheet(
            f"QTabBar{{background:transparent}}"
            f"QTabBar::tab{{background:{tab_bg};color:{tab_text};padding:6px 14px;border:none;margin-right:2px;border-top-left-radius:8px;border-top-right-radius:8px;min-width:72px;max-width:220px}}"
            f"QTabBar::tab:selected{{background:{tab_bg_sel};color:{tab_text_sel}}}"
            f"QTabBar::tab:hover{{background:{tab_bg_hover}}}"
            "QTabBar::close-button{image:none;width:0px;height:0px;}"
        )
        self._refresh_close_buttons()

    def _tab_index_for_close_btn(self, btn: QToolButton) -> int:
        pt = self.tab_bar.mapFromGlobal(btn.mapToGlobal(btn.rect().center()))
        return self.tab_bar.tabAt(pt)

    def _make_close_button(self) -> QToolButton:
        light = _is_light_theme()
        fg = "#64748b" if light else "#94a3b8"
        fg_h = "#0f172a" if light else "#e2e8f0"
        bg_h = "rgba(2,6,23,0.10)" if light else "rgba(255,255,255,0.12)"
        btn = QToolButton(self.tab_bar)
        btn.setAutoRaise(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setText("×")
        btn.setFixedSize(16, 16)
        btn.setStyleSheet(
            "QToolButton{background:transparent;border:none;padding:0px;"
            f"color:{fg};font-size:12px;font-weight:500;"
            "}"
            f"QToolButton:hover{{background:{bg_h};color:{fg_h};border-radius:8px;}}"
        )
        btn.clicked.connect(lambda _=False, b=btn: self._on_tab_close(self._tab_index_for_close_btn(b)))
        return btn

    def _refresh_close_buttons(self):
        for i in range(self.tab_bar.count()):
            self.tab_bar.setTabButton(i, QTabBar.ButtonPosition.RightSide, self._make_close_button())

    def _on_editor_dirty(self, text: str):
        i = self.tab_bar.currentIndex()
        if 0 <= i < len(self._paths):
            path = self._paths[i]
            self._buffers[path] = text
            clean = self._clean_snapshots.get(path, "")
            self._set_dirty(path, text != clean)

    def _refresh_tab_caption(self, index: int):
        if index < 0 or index >= len(self._paths):
            return
        path = self._paths[index]
        name = Path(path).name or path
        dirty = path in self._dirty_paths
        self.tab_bar.setTabText(index, f"● {name}" if dirty else name)
        self.tab_bar.setTabToolTip(index, path)

    def _set_dirty(self, path: str, dirty: bool):
        if dirty:
            self._dirty_paths.add(path)
        else:
            self._dirty_paths.discard(path)
        if path in self._paths:
            self._refresh_tab_caption(self._paths.index(path))

    def _on_editor_save_requested(self, path: str, content: str):
        if not path:
            return
        self._buffers[path] = content
        self.save_requested.emit(path, content)

    def _on_tab_moved(self, from_i: int, to_i: int):
        if 0 <= from_i < len(self._paths) and 0 <= to_i < len(self._paths):
            p = self._paths.pop(from_i)
            self._paths.insert(to_i, p)
            self._refresh_tab_caption(to_i)
            self._refresh_close_buttons()

    def _load_tab_content_for_index(self, index: int):
        if index < 0 or index >= len(self._paths):
            self.editor.set_file("", "")
            return
        path = self._paths[index]
        c = self._buffers.get(path)
        if c is None:
            try:
                c = Path(path).read_text("utf-8", errors="replace")
            except Exception:
                c = ""
            self._buffers[path] = c
            self._clean_snapshots[path] = c
            self._set_dirty(path, False)
        self.editor.set_file(path, c)

    def _flush_then_show(self, prev_idx: int, new_idx: int):
        """把编辑器内容写入 prev 对应文件缓冲区，再显示 new_idx。"""

        def after(content: str):
            if 0 <= prev_idx < len(self._paths):
                p = self._paths[prev_idx]
                self._buffers[p] = content
                self._set_dirty(p, content != self._clean_snapshots.get(p, ""))
            self._tab_prev_idx = new_idx
            self._load_tab_content_for_index(new_idx)

        if self.editor.webview:
            self.editor.webview.page().runJavaScript("window.getContent()", after)
        else:
            after("")

    def open_file(self, path: str):
        ap = os.path.normpath(os.path.abspath(path))
        if not os.path.isfile(ap):
            return
        if ap in self._paths:
            idx = self._paths.index(ap)
            if self.tab_bar.currentIndex() != idx:
                self.tab_bar.setCurrentIndex(idx)
            return
        prev = self._tab_prev_idx
        self._paths.append(ap)
        self.tab_bar.blockSignals(True)
        ti = self.tab_bar.addTab(Path(ap).name)
        self.tab_bar.setTabToolTip(ti, ap)
        self._refresh_close_buttons()
        ni = len(self._paths) - 1
        self.tab_bar.setCurrentIndex(ni)
        self.tab_bar.blockSignals(False)
        self._refresh_tab_caption(ni)
        self._flush_then_show(prev, ni)

    def _on_tab_changed(self, index: int):
        prev = self._tab_prev_idx
        if index == prev:
            return
        self._flush_then_show(prev, index)

    def _on_tab_close(self, index: int):
        if index < 0 or index >= len(self._paths):
            return
        closing_path = self._paths[index]
        cur = self.tab_bar.currentIndex()

        def apply_remove(content: str):
            self._buffers[closing_path] = content
            self._set_dirty(closing_path, content != self._clean_snapshots.get(closing_path, ""))
            self._paths.pop(index)
            self.tab_bar.removeTab(index)
            self._dirty_paths.discard(closing_path)
            self._clean_snapshots.pop(closing_path, None)
            self._buffers.pop(closing_path, None)
            self._refresh_close_buttons()
            if not self._paths:
                self.editor.set_file("", "")
                self._tab_prev_idx = -1
                return
            ni = self.tab_bar.currentIndex()
            self._tab_prev_idx = ni
            self._load_tab_content_for_index(ni)

        def remove_other():
            self._paths.pop(index)
            self._buffers.pop(closing_path, None)
            self._dirty_paths.discard(closing_path)
            self._clean_snapshots.pop(closing_path, None)
            self.tab_bar.removeTab(index)
            self._refresh_close_buttons()
            if not self._paths:
                self.editor.set_file("", "")
                self._tab_prev_idx = -1
                return
            ni = self.tab_bar.currentIndex()
            self._tab_prev_idx = ni
            self._load_tab_content_for_index(ni)

        if index == cur and self.editor.webview:
            self.editor.webview.page().runJavaScript("window.getContent()", apply_remove)
        else:
            remove_other()

    def clear_all_tabs(self):
        self._paths.clear()
        self._buffers.clear()
        self._clean_snapshots.clear()
        self._dirty_paths.clear()
        self.tab_bar.blockSignals(True)
        while self.tab_bar.count():
            self.tab_bar.removeTab(0)
        self.tab_bar.blockSignals(False)
        self._tab_prev_idx = -1
        self.editor.set_file("", "")

    def mark_saved(self, path: str, content: str):
        self._buffers[path] = content
        self._clean_snapshots[path] = content
        self._set_dirty(path, False)


# ── BubbleTextEdit (read-only; wheel scrolls outer QScrollArea, not inside bubble) ──
class BubbleTextEdit(QTextEdit):
    link_clicked = pyqtSignal(str)

    def __init__(self, scroll_area: Optional[QScrollArea] = None, parent=None):
        super().__init__(parent)
        self._scroll_area = scroll_area
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 允许点击获取焦点后使用 Ctrl+C，同时保持只读
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )

    def mouseReleaseEvent(self, event):
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                pos = event.position().toPoint()
                href = self.anchorAt(pos)
                if href:
                    self.link_clicked.emit(str(href))
                    event.accept()
                    return
        except Exception:
            pass
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if self._scroll_area is not None:
            QApplication.sendEvent(self._scroll_area.viewport(), event)
            return
        super().wheelEvent(event)


class ImagePreviewDialog(QDialog):
    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self._image_path = image_path
        self._pixmap = QPixmap(image_path)
        self.setWindowTitle(Path(image_path).name or "预览")
        self.resize(960, 720)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setWidget(self._label)
        layout.addWidget(self._scroll)
        self._apply_theme()
        self._refresh_pixmap()

    def _apply_theme(self):
        light = _is_light_theme()
        bg = "#f8fafc" if light else "#111827"
        self.setStyleSheet(f"QDialog{{background:{bg};}}QScrollArea{{background:transparent;border:none;}}")

    def _refresh_pixmap(self):
        if self._pixmap.isNull():
            self._label.setText("无法预览该图片")
            return
        area = self._scroll.viewport().size()
        target = QSize(max(120, area.width() - 24), max(120, area.height() - 24))
        scaled = self._pixmap.scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._label.setPixmap(scaled)
        self._label.resize(scaled.size())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_pixmap()


class PreviewImageLabel(QLabel):
    def __init__(self, image_path: str, size: QSize, radius: int = 12, parent=None):
        super().__init__(parent)
        self.image_path = image_path
        self._size = QSize(size)
        self._radius = radius
        self.setFixedSize(self._size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_pixmap()

    def _apply_pixmap(self):
        px = QPixmap(self.image_path)
        if px.isNull():
            self.setText("图片")
            self.setStyleSheet("background:rgba(0,0,0,0.08);border-radius:12px;")
            return
        scaled = px.scaled(self._size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(scaled)
        self.setStyleSheet(f"background:transparent;border:none;border-radius:{self._radius}px;")

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and os.path.exists(self.image_path):
            ImagePreviewDialog(self.image_path, self.window()).exec()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class SessionActivityBar(QWidget):
    """Left indicator bar for session cards with running shimmer."""

    def __init__(self, color: str, running: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedWidth(3)
        self.setMinimumHeight(30)
        self._color = QColor(color)
        self._running = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._on_tick)
        self.set_running(running)

    def _on_tick(self):
        self._phase = (self._phase + 0.03) % 1.0
        self.update()

    def set_running(self, running: bool):
        self._running = bool(running)
        if self._running:
            if not self._timer.isActive():
                self._timer.start()
        else:
            if self._timer.isActive():
                self._timer.stop()
            self._phase = 0.0
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        base = QColor(self._color)
        if not self._running:
            base.setAlpha(220)
            p.fillRect(rect, base)
            return
        dim = QColor(self._color)
        dim.setAlpha(90)
        bright = QColor(self._color)
        bright.setAlpha(240)
        center = self._phase
        a = max(0.0, center - 0.22)
        c = min(1.0, center + 0.22)
        g = QLinearGradient(0, 0, 0, rect.height())
        g.setColorAt(0.0, dim)
        g.setColorAt(a, dim)
        g.setColorAt(center, bright)
        g.setColorAt(c, dim)
        g.setColorAt(1.0, dim)
        p.fillRect(rect, g)


class ElidedLabel(QLabel):
    """A label that elides against its actual laid-out width."""

    def __init__(self, text: str = "", parent=None):
        super().__init__("", parent)
        self._full_text = str(text or "")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._refresh_text()

    def set_full_text(self, text: str):
        self._full_text = str(text or "")
        self._refresh_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_text()

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(0)
        return hint

    def _refresh_text(self):
        width = self.contentsRect().width()
        text = self._full_text
        if width > 0:
            text = QFontMetrics(self.font()).elidedText(text, Qt.TextElideMode.ElideRight, width)
        if self.text() != text:
            QLabel.setText(self, text)


class ComposerAttachmentChip(QFrame):
    removed = pyqtSignal(str)

    def __init__(self, meta: dict, parent=None):
        super().__init__(parent)
        self.meta = dict(meta)
        self.setObjectName("composerAttachmentChip")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(6)
        if self.meta.get("is_image"):
            row.addWidget(PreviewImageLabel(self.meta.get("path", ""), QSize(18, 18), radius=6))
        else:
            icon = QLabel()
            icon.setPixmap(quark_icon("file", 14, "#bfc5cf").pixmap(14, 14))
            row.addWidget(icon)
        self._name = QLabel(self.meta.get("name", "附件"))
        row.addWidget(self._name)
        self._remove = QToolButton()
        self._remove.setAutoRaise(True)
        self._remove.setText("×")
        self._remove.clicked.connect(lambda: self.removed.emit(self.meta.get("path", "")))
        row.addWidget(self._remove)
        self.apply_theme()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.meta.get("is_image") and os.path.exists(self.meta.get("path", "")):
            ImagePreviewDialog(self.meta["path"], self.window()).exec()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def apply_theme(self):
        light = _is_light_theme()
        bg = "#f3f4f6" if light else "#2d2d2d"
        bd = "#d7dbe2" if light else "#3a3d41"
        fg = "#374151" if light else "#e5e7eb"
        dim = "#6b7280" if light else "#9ca3af"
        self.setStyleSheet(
            f"QFrame#composerAttachmentChip{{background:{bg};border:1px solid {bd};border-radius:10px;}}"
            f"QToolButton{{background:transparent;border:none;color:{dim};font-size:13px;font-weight:700;padding:0 2px;}}"
            f"QToolButton:hover{{color:{fg};}}"
            f"QLabel{{background:transparent;color:{fg};font-size:11px;font-weight:600;}}"
        )


class FileAttachmentPill(QFrame):
    def __init__(self, meta: dict, parent=None):
        super().__init__(parent)
        self.meta = dict(meta)
        self.setObjectName("fileAttachmentPill")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(6)
        icon = QLabel()
        icon.setPixmap(quark_icon("file", 14, "#9ca3af").pixmap(14, 14))
        row.addWidget(icon)
        text = QLabel(self.meta.get("name", "附件"))
        row.addWidget(text)
        self.apply_theme()

    def apply_theme(self):
        light = _is_light_theme()
        bg = "#f3f4f6" if light else "#2d2d2d"
        fg = "#374151" if light else "#e5e7eb"
        bd = "#d7dbe2" if light else "#3a3d41"
        self.setStyleSheet(
            f"QFrame#fileAttachmentPill{{background:{bg};border:1px solid {bd};border-radius:10px;}}"
            f"QLabel{{background:transparent;color:{fg};font-size:10px;font-weight:600;}}"
        )

# ── ChatBubble ──────────────────────────────────────────────
class ChatBubble(QFrame):
    def __init__(self, role, text="", parent=None, scroll_area: Optional[QScrollArea] = None, attachments: Optional[list[dict]] = None, model_label: str = ""):
        super().__init__(parent); self.role = role
        self._raw_text = ""
        self._attachments = list(attachments or [])
        light = _is_light_theme()
        accent_hex = str(getattr(load_config(), "accent_color", "#5ee9ff") or "#5ee9ff").strip()
        text_user = "#000000" if light else "#eef4f8"
        text_assist = "#000000" if light else "#d5dee8"
        panel_user = "rgba(12,74,110,0.10)" if light else "rgba(94,233,255,0.1)"
        panel_assist = "rgba(0,0,0,0.04)" if light else C_PANEL
        hdr_user = C_ACCENT_LIGHT if light else C_ACCENT
        hdr_assist = accent_hex if accent_hex.startswith("#") else (C_TEAL_LIGHT if light else C_TEAL)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setFrameShape(QFrame.Shape.NoFrame)
        outer=QHBoxLayout(self); outer.setContentsMargins(14,6,14,6)
        inner=QFrame(); inner.setMaximumWidth(900); self.inner = inner
        if role=="user":
            inner.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        else:
            inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        il=QVBoxLayout(inner); il.setContentsMargins(12,8,12,8); il.setSpacing(4)
        names={"user":"You","assistant":"Kscc UI","tool":"Tool","error":"Error"}
        self.header=QLabel(names.get(role,role)); self.header.setFont(QFont("Segoe UI",10,QFont.Weight.Bold))
        self.model_tag = QLabel(str(model_label or "").strip())
        self.model_tag.setVisible(bool(str(model_label or "").strip()))
        self.model_tag.setFont(QFont("Segoe UI", 9))
        self.body=BubbleTextEdit(scroll_area, self)
        self.body.link_clicked.connect(self._on_link_clicked)
        self.attachments_wrap = QWidget()
        self.attachments_layout = QHBoxLayout(self.attachments_wrap)
        self.attachments_layout.setContentsMargins(0, 0, 0, 0)
        self.attachments_layout.setSpacing(8)
        self.attachments_wrap.hide()
        if role=="user":
            self.body.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            self.body.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        else:
            self.body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.body.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        bf=QFont(); bf.setPointSize(12)
        bf.setFamilies(["Segoe UI", "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", "sans-serif"])
        self.body.setFont(bf); self.body.document().setDocumentMargin(4)
        self.body.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.body.document().documentLayout().documentSizeChanged.connect(self._fit)
        if role=="user":
            self.header.setStyleSheet(f"color:{hdr_user};background:transparent;font-size:12px;font-weight:700;letter-spacing:0.04em")
            inner.setStyleSheet(f"QFrame{{background:{panel_user};border:none;border-radius:14px}}")
            self.body.setStyleSheet(f"QTextEdit{{background:transparent;color:{text_user};border:none}}")
        elif role=="assistant":
            self.header.setStyleSheet(f"color:{hdr_assist};background:transparent;font-size:12px;font-weight:700;letter-spacing:0.04em")
            tag_col = "#667085" if light else "#8b95a5"
            self.model_tag.setStyleSheet(f"color:{tag_col};background:transparent;font-size:11px;font-weight:400;")
            inner.setStyleSheet(f"QFrame{{background:{panel_assist};border:none;border-radius:14px}}")
            self.body.setStyleSheet(f"QTextEdit{{background:transparent;color:{text_assist};border:none}}")
        elif role=="tool":
            self.header.setStyleSheet(f"color:{C_YELLOW};background:transparent;font-size:10px;letter-spacing:0.06em")
            _tool_panel = "rgba(0,0,0,0.04)" if light else "rgba(255,255,255,0.04)"
            _tool_dim = "#333333" if light else C_DIM
            inner.setStyleSheet(f"QFrame{{background:{_tool_panel};border:none;border-radius:12px}}")
            self.body.setStyleSheet(f"QTextEdit{{background:transparent;color:{_tool_dim};border:none;font-size:11px}}")
        elif role=="error":
            self.header.setStyleSheet(f"color:{C_RED};background:transparent;font-size:10px;letter-spacing:0.06em")
            inner.setStyleSheet("QFrame{background:rgba(248,113,113,0.1);border:none;border-radius:12px}")
            self.body.setStyleSheet("QTextEdit{background:transparent;color:#fecaca;border:none}")
        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.setSpacing(8)
        head_row.addWidget(self.header, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        head_row.addWidget(self.model_tag, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        head_row.addStretch(1)
        il.addLayout(head_row)
        il.addWidget(self.attachments_wrap)
        il.addWidget(self.body)
        if role=="user":
            outer.addStretch(); outer.addWidget(inner)
        else:
            outer.addWidget(inner); outer.addStretch()
        self._render_attachments()
        self.set_text(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "body"): QTimer.singleShot(0, self._fit)

    def _scroll_viewport_width(self) -> int:
        """聊天区可视宽度上限：沿父链找到 QScrollArea，否则用祖先 widget 宽度（viewport 中间层可能挡在中间）。"""
        p = self.parentWidget()
        best = 900
        while p is not None:
            if isinstance(p, QScrollArea):
                return max(160, p.viewport().width() - 40)
            if p.width() > 0:
                best = min(best, p.width() - 48)
            p = p.parentWidget()
        return max(160, best)

    def _avail_inner_content_width(self) -> int:
        cap = min(880, self._scroll_viewport_width())
        w = self.inner.width() if self.inner.width() > 80 else 0
        if w <= 0:
            p = self.parentWidget()
            while p is not None and w <= 0:
                w = max(p.width() - 48, 200)
                p = p.parentWidget()
        return max(120, min(cap, w - 20))

    def _fit(self):
        if not self.body.isVisible():
            return
        doc = self.body.document()
        side_pad = 8
        cap = self._avail_inner_content_width()
        if self.role == "user":
            fm = QFontMetrics(self.body.font())
            plain = self.body.toPlainText()
            lines = plain.split("\n") if plain else [""]
            mw = max((fm.horizontalAdvance(L) for L in lines), default=40)
            # Use doc width + widget gutter to avoid right-edge clipping on wrapped CJK text.
            gutter = 14
            doc_w = min(max(56, cap - gutter), max(56, mw + 18))
            doc.setTextWidth(float(doc_w))
            h = math.ceil(float(doc.size().height())) + side_pad * 2 + 2
            widget_w = doc_w + gutter
            self.body.setFixedSize(int(widget_w), int(max(h, 30)))
        else:
            bw = self.body.width()
            if bw > 80:
                tw = max(100, min(cap, bw - 16))
            else:
                tw = self._avail_inner_content_width()
            doc.setTextWidth(float(tw))
            h = math.ceil(float(doc.size().height())) + side_pad * 2
            self.body.setFixedHeight(int(max(h, 36)))

    def set_text(self, t):
        self._raw_text = str(t or "")
        self.body.setVisible(bool(str(t or "").strip()))
        if self.role == "assistant":
            self.body.setHtml(md_chat_to_html(self._raw_text, light=_is_light_theme()))
        else:
            self.body.setPlainText(self._raw_text)
        QTimer.singleShot(0, self._fit)

    def append_text(self, t):
        if self.role == "assistant":
            if not self.body.isVisible():
                self.body.setVisible(True)
            self._raw_text += str(t or "")
            self.body.setHtml(md_chat_to_html(self._raw_text, light=_is_light_theme()))
            self.body.moveCursor(QTextCursor.MoveOperation.End)
            QTimer.singleShot(0, self._fit)
        else:
            self.body.setReadOnly(False)
            try:
                c = self.body.textCursor()
                c.movePosition(QTextCursor.MoveOperation.End)
                c.insertText(t)
            finally:
                self.body.setReadOnly(True)

    def _on_link_clicked(self, href: str):
        href = str(href or "")
        if not href.startswith("copycode:"):
            return
        try:
            idx = int(href.split(":", 1)[1])
        except Exception:
            return
        blocks = re.findall(r"```(?:\\w*)\\n(.*?)```", self._raw_text, flags=re.DOTALL)
        if idx < 0 or idx >= len(blocks):
            return
        code = blocks[idx]
        try:
            cb = QApplication.clipboard()
            cb.setText(code)
            QToolTip.showText(QCursor.pos(), "Copied", self)
        except Exception:
            pass

    def _render_attachments(self):
        while self.attachments_layout.count():
            item = self.attachments_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if not self._attachments:
            self.attachments_wrap.hide()
            return
        if self.role == "user":
            self.attachments_layout.addStretch(1)
        for meta in self._attachments:
            if meta.get("is_image"):
                thumb = PreviewImageLabel(meta.get("path", ""), QSize(92, 64), radius=10)
                thumb.setToolTip(meta.get("name", "图片"))
                self.attachments_layout.addWidget(thumb, 0, Qt.AlignmentFlag.AlignRight)
            else:
                pill = FileAttachmentPill(meta)
                pill.setToolTip(meta.get("path", meta.get("name", "附件")))
                self.attachments_layout.addWidget(pill, 0, Qt.AlignmentFlag.AlignRight)
        self.attachments_wrap.show()


class ChatInputEdit(QTextEdit):
    """多行输入 + Ctrl+Enter 发送。使用 QTextEdit 而非 QPlainTextEdit，便于与 kscc 一样用 document.size() 可靠算高。"""

    send_requested = pyqtSignal()
    height_refresh_requested = pyqtSignal()
    image_pasted = pyqtSignal(object)
    files_dropped = pyqtSignal(object)
    drag_state_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setAcceptDrops(True)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.document().setDocumentMargin(0)
        self.setViewportMargins(0, 0, 0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.height_refresh_requested.emit()

    def keyPressEvent(self, event):
        if (
            event.key() == Qt.Key.Key_Return
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def canInsertFromMimeData(self, source):
        if source is not None and source.hasImage():
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if source is not None and source.hasImage():
            image = source.imageData()
            if isinstance(image, QImage):
                self.image_pasted.emit(QPixmap.fromImage(image))
                return
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md is not None and (md.hasUrls() or md.hasImage()):
            self.drag_state_changed.emit(True)
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        md = event.mimeData()
        if md is not None and (md.hasUrls() or md.hasImage()):
            self.drag_state_changed.emit(True)
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self.drag_state_changed.emit(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        md = event.mimeData()
        if md is None:
            self.drag_state_changed.emit(False)
            super().dropEvent(event)
            return
        if md.hasImage():
            image = md.imageData()
            if isinstance(image, QImage):
                self.image_pasted.emit(QPixmap.fromImage(image))
                self.drag_state_changed.emit(False)
                event.acceptProposedAction()
                return
        if md.hasUrls():
            paths = []
            for url in md.urls():
                if url.isLocalFile():
                    paths.append(url.toLocalFile())
            if paths:
                self.files_dropped.emit(paths)
                self.drag_state_changed.emit(False)
                event.acceptProposedAction()
                return
        self.drag_state_changed.emit(False)
        super().dropEvent(event)


class NoWheelComboBox(QComboBox):
    """Disable mouse wheel value changes to prevent accidental edits."""
    def wheelEvent(self, event):
        event.ignore()


class NoWheelFontComboBox(QFontComboBox):
    """Font combo with wheel disabled."""
    def wheelEvent(self, event):
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    """Disable mouse wheel value changes to prevent accidental edits."""
    def wheelEvent(self, event):
        event.ignore()


class WorkspaceGroupHeader(QFrame):
    toggled = pyqtSignal(str, bool)
    add_requested = pyqtSignal(str)

    def __init__(self, workspace_key: str, label: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        self.workspace_key = workspace_key
        self._label = label
        self._expanded = expanded
        self._hovered = False
        self._light = _is_light_theme()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("workspaceGroupHeader")

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(6)

        self.arrow = QLabel()
        self.arrow.setFixedSize(12, 12)
        self.arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(self.arrow, 0, Qt.AlignmentFlag.AlignVCenter)

        self.title = QLabel(label)
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(self.title, 1)

        self.add_btn = QToolButton()
        self.add_btn.setAutoRaise(True)
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.setIconSize(QSize(12, 12))
        self.add_btn.clicked.connect(lambda: self.add_requested.emit(self.workspace_key))
        self.add_btn.hide()
        row.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self.setToolTip(workspace_key if workspace_key not in ("", "(No workspace)") else "No workspace")
        self._apply_style()

    def _apply_style(self):
        self._light = _is_light_theme()
        fg = "#6b7280" if self._light else "#aeb6c2"
        hover_bg = "#edf3ff" if self._light else "rgba(255,255,255,0.08)"
        hover_fg = "#374151" if self._light else "#eef4f8"
        icon_col = "#9aa3af" if self._light else "#94a3b8"
        active_icon = "#7c8796" if self._light else "#cbd5e1"
        self.setStyleSheet(
            _with_tooltip_style(
                f"QFrame#workspaceGroupHeader{{background:{hover_bg if self._hovered else 'transparent'};border:none;border-radius:8px;}}",
                self._light,
            )
        )
        self.title.setStyleSheet(
            f"color:{hover_fg if self._hovered else fg};font-size:11px;font-weight:700;background:transparent;"
        )
        arrow_name = "chevron_down" if self._expanded else "chevron_right"
        self.arrow.setPixmap(quark_icon(arrow_name, 12, active_icon if self._hovered else icon_col).pixmap(12, 12))
        self.add_btn.setIcon(_make_plus_icon(12, active_icon if self._hovered else icon_col))
        self.add_btn.setStyleSheet(
            "QToolButton{background:transparent;border:none;padding:0;}"
            "QToolButton:hover{background:transparent;border:none;}"
        )

    def set_expanded(self, expanded: bool):
        self._expanded = expanded
        self._apply_style()

    def enterEvent(self, event):
        self._hovered = True
        self.add_btn.show()
        self._apply_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.add_btn.hide()
        self._apply_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._expanded = not self._expanded
            self._apply_style()
            self.toggled.emit(self.workspace_key, self._expanded)
            event.accept()
            return
        super().mousePressEvent(event)


class ContextRingWidget(QWidget):
    """顶栏环形上下文占用指示（悬停显示已用/上限）。"""

    def __init__(self, parent=None, diameter: int = 18):
        super().__init__(parent)
        d = max(14, min(28, int(diameter)))
        self._d = float(d)
        self.setFixedSize(d, d)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ratio: Optional[float] = None
        self._current = "—"
        self._limit = "—"
        self._refresh_tip()

    def clear(self):
        self._ratio = None
        self._current = "—"
        self._limit = "—"
        self._refresh_tip()
        self.update()

    def set_snapshot(self, s: dict):
        try:
            self._ratio = float(s.get("usage_ratio", 0))
        except (TypeError, ValueError):
            self._ratio = None
        cu = s.get("current_usage")
        lm = s.get("limit")
        self._current = _fmt_k(cu)
        self._limit = _fmt_k(lm)
        self._refresh_tip()
        self.update()

    def _refresh_tip(self):
        self.setStyleSheet(_tooltip_css(_is_light_theme()))
        if self._ratio is None:
            self.setToolTip("上下文占用\n开始对话后显示用量与上限。")
            return
        try:
            pct = int(round(self._ratio * 100))
        except Exception:
            pct = 0
        self.setToolTip(f"上下文\n已用: {self._current}\n上限: {self._limit}\n占用: {pct}%")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = max(1.0, self._d * 0.10)
        side = self._d - 2.0 * m
        rect = QRectF(m, m, side, side)
        light = _is_light_theme()
        pen_bg = QPen(QColor(15, 23, 42, 55) if light else QColor(255, 255, 255, 45))
        pen_bg.setWidthF(max(1.2, self._d * 0.11))
        p.setPen(pen_bg)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        if self._ratio is None:
            p.end()
            return

        r = max(0.0, min(1.0, float(self._ratio)))
        if light:
            if r < 0.55:
                col = QColor("#0f766e")
            elif r < 0.8:
                col = QColor("#b45309")
            else:
                col = QColor("#b91c1c")
        else:
            hue = int(120 - r * 120)
            col = QColor.fromHsv(hue, 210, 235)
        pen_fg = QPen(col)
        pen_fg.setWidthF(max(1.4, self._d * 0.13))
        pen_fg.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen_fg)
        inset = max(0.75, self._d * 0.07)
        inner = QRectF(rect.x() + inset, rect.y() + inset, rect.width() - 2 * inset, rect.height() - 2 * inset)
        span = int(r * 360 * 16)
        p.drawArc(inner, 90 * 16, -span)


# ── ChatPanel ───────────────────────────────────────────────
class ChatPanel(QWidget):
    send_message = pyqtSignal(str, object)
    add_skill_requested = pyqtSignal()
    def __init__(self, parent=None):
        super().__init__(parent); self.setMinimumWidth(260)
        self._pending_attachments: list[dict] = []
        l=QVBoxLayout(self); l.setContentsMargins(0,0,0,0); l.setSpacing(0)
        self.scroll=QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.msg_container=QWidget()
        self.msg_container.setStyleSheet("background: transparent;")
        self.msg_layout=QVBoxLayout(self.msg_container)
        self.msg_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.msg_layout.setSpacing(10); self.msg_layout.setContentsMargins(12,14,12,14)
        self.msg_layout.addStretch(); self.scroll.setWidget(self.msg_container)
        l.addWidget(self.scroll,1)
        # Status line (aligned with composer input, accent colored)
        self._status_base_text = ""
        self._status_phase = 0
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(420)
        self._status_timer.timeout.connect(self._tick_status)
        self.kscc_status_lbl = QLabel("")
        self.kscc_status_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.kscc_status_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.kscc_status_lbl.setContentsMargins(0, 0, 0, 0)
        self.kscc_status_lbl.hide()
        l.addWidget(self.kscc_status_lbl)

        # ── Composer：模型/后端菜单交互对齐 kscc UI（QPushButton + 弹出 QMenu，非 QToolButton 内嵌菜单） ──
        self._model_map: dict[str, list[str]] = {}
        self._backend_key = "Kscc"
        self._model_name = ""

        self.mode_menu_btn = QPushButton("")
        self.mode_menu_btn.setFixedHeight(28)
        self.mode_menu_btn.setMinimumWidth(156)
        self.mode_menu_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mode_menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_menu_btn.clicked.connect(self._show_runtime_menu)

        self._input_card = QFrame()
        self._input_card.setObjectName("inputGlassCard")
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

        toolbar.addWidget(self._plus_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self.mode_menu_btn)
        toolbar.addWidget(self.send_btn)
        toolbar.addWidget(self.stop_btn)

        self._refresh_mode_menu_btn_text()

        vl.addWidget(self.attachments_row)
        vl.addWidget(self.input_edit)
        vl.addLayout(toolbar)

        input_wrap = QWidget()
        iw = QVBoxLayout(input_wrap)
        iw.setContentsMargins(12, 6, 12, 12)
        iw.setSpacing(4)
        self.save_skill_btn = QPushButton("Save Skill")
        self.save_skill_btn.setObjectName("saveSkillBtn")
        self.save_skill_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_skill_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.save_skill_btn.clicked.connect(self.add_skill_requested.emit)
        self.save_skill_btn.hide()
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.addStretch(1)
        top_bar.addWidget(self.save_skill_btn, 0, Qt.AlignmentFlag.AlignRight)
        iw.addLayout(top_bar)
        iw.addWidget(self._input_card)
        l.addWidget(input_wrap)
        self._stream_bubble: Optional[ChatBubble] = None
        self._drag_active = False
        self.apply_theme()
        QTimer.singleShot(0, self._adjust_input_height)

    def apply_theme(self):
        """输入框外壳、模型菜单、附件按钮随 light/dark 切换。"""
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
                "  border-radius:8px; padding:2px 8px; font-size:12px;"
                "  text-align:center;"
                f"  font-family:{_CODE_FONT_STACK};"
                "}"
                f"QPushButton:hover {{ background:{menu_bg_h}; color:{menu_fg_h}; }}"
            )
        )
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
        for meta in list(self._pending_attachments):
            pass
        for i in range(self.attachments_layout.count()):
            item = self.attachments_layout.itemAt(i)
            w = item.widget()
            if isinstance(w, ComposerAttachmentChip):
                w.apply_theme()

    def _set_drag_active(self, active: bool):
        active = bool(active)
        if self._drag_active == active:
            return
        self._drag_active = active
        self.apply_theme()

    def _adjust_input_height(self):
        """延后一帧再量高度，保证 QTextDocument 已完成折行布局。"""
        QTimer.singleShot(0, self._sync_composer_height)

    def _sync_composer_height(self):
        """按 QTextDocument 布局高度拉伸（与 kscc `document().size().height()` 一致）。"""
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
        short = model_display if len(model_display) <= 28 else model_display[:25] + "…"
        self.mode_menu_btn.setText(f"[{abbrev}] {short}  ▾")
        self.mode_menu_btn.setToolTip(f"后端: {self._backend_key}\n模型: {model}")

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
        """运行时后端/模型菜单；浅色和深色分别使用独立配色。"""
        menu = QMenu(self)
        light = _is_light_theme()
        if light:
            m_bg = "rgba(255,255,255,0.98)"
            m_fg = "#0f172a"
            m_sel = "rgba(12,74,110,0.12)"
            m_sep = "rgba(15,23,42,0.10)"
            m_disabled = "#64748b"
        else:
            m_bg = "#252526"
            m_fg = "#ffffff"
            m_sel = "#3a3a3d"
            m_sep = "#3a3d41"
            m_disabled = "#8f959f"
        menu.setStyleSheet(
            "QMenu {"
            f"  background:{m_bg}; color:{m_fg}; border:none;"
            "  border-radius:10px; padding:8px 6px;"
            f"  font-family:{_CODE_FONT_STACK}; font-size:12px;"
            "}"
            f"QMenu::item {{ padding:7px 12px; border-radius:6px; margin:1px 4px; color:{m_fg}; }}"
            f"QMenu::item:selected {{ background:{m_sel}; color:{m_fg}; }}"
            f"QMenu::item:disabled {{ color:{m_disabled}; background:transparent; }}"
            f"QMenu::separator {{ height:1px; background:{m_sep}; margin:6px 8px; }}"
        )
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

    def current_backend_label(self) -> str:
        return self._backend_key

    def current_model_name(self) -> str:
        return self._model_name

    def set_kscc_status(self, text: str):
        self._status_base_text = str(text or "").strip()
        self._status_phase = 0
        if not self._status_base_text:
            self.kscc_status_lbl.hide()
            self._status_timer.stop()
            return
        # show thinking animation prefix
        if not self._status_timer.isActive():
            self._status_timer.start()
        self._apply_status_style()
        self._render_status()
        self.kscc_status_lbl.show()

    def _apply_status_style(self):
        light = _is_light_theme()
        accent_hex = str(getattr(load_config(), "accent_color", "#5ee9ff") or "#5ee9ff").strip()
        if not accent_hex.startswith("#"):
            accent_hex = "#5ee9ff"
        # remove any different background; keep transparent, align with input left padding
        fg = accent_hex
        pad_l = 22  # roughly aligns with composer card + inner padding
        pad_r = 10
        pad_t = 2
        pad_b = 4
        self.kscc_status_lbl.setStyleSheet(
            f"QLabel{{color:{fg};font-size:10px;padding:{pad_t}px {pad_r}px {pad_b}px {pad_l}px;background:transparent;}}"
        )

    def _render_status(self):
        dots = "." * (self._status_phase % 4)
        prefix = f"Thinking{dots} "
        self.kscc_status_lbl.setText(prefix + self._status_base_text)

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
            self.add_message("user", t, attachments=attachments)
            self.send_message.emit(t, attachments)
            self.show_save_skill_prompt(False)
            self.input_edit.clear()
            self._pending_attachments.clear()
            self._refresh_attachments_row()
        self._adjust_input_height()

    def show_save_skill_prompt(self, visible: bool, text: str = "Save Skill"):
        self.save_skill_btn.setText(str(text or "Save Skill"))
        self.save_skill_btn.setVisible(bool(visible))

    def add_message(self, role, text, attachments: Optional[list[dict]] = None, model_label: str = ""): 
        b=ChatBubble(role, text, scroll_area=self.scroll, attachments=attachments, model_label=model_label)
        self.msg_layout.insertWidget(max(0,self.msg_layout.count()-1),b)
        QTimer.singleShot(30,self._scroll); return b
    def start_stream(self, model_label: str = ""):
        self._stream_bubble=self.add_message("assistant","", model_label=model_label)
        self.send_btn.setEnabled(False); self.send_btn.hide()
        self.stop_btn.show()

    def append_stream(self, text: str):
        """流式追加助手正文（由 AgentWorker.text_delta 调用）。"""
        if not text or not self._stream_bubble:
            return
        try:
            self._stream_bubble.append_text(text)
        except RuntimeError:
            # 气泡控件已被销毁（如切会话/新建会话后仍收到旧流式信号）
            self._stream_bubble = None
            return
        QTimer.singleShot(40, self._scroll)

    def end_stream(self):
        self._stream_bubble=None
        self.send_btn.setEnabled(True); self.send_btn.show()
        self.stop_btn.hide()

    def add_tool(self, name, preview):
        if self._stream_bubble and name not in ("edit_file","Write","Edit"):
            try:
                self._stream_bubble.append_text(f"\n· Tool · {preview}\n")
            except RuntimeError:
                self._stream_bubble = None

    def add_result(self, result, error=False):
        if self._stream_bubble:
            marker = "Failed ·" if error else "Done ·"
            try:
                self._stream_bubble.append_text(f"{marker} {result[:400]}\n")
            except RuntimeError:
                self._stream_bubble = None

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
        nm=Path(filepath).name if filepath else "unknown"
        card=QFrame()
        card.setStyleSheet(f"QFrame{{background:{C_PANEL};border:none;border-radius:12px;margin:4px 10px}}")
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        cl=QVBoxLayout(card); cl.setContentsMargins(8,6,8,6); cl.setSpacing(4)

        # 文件头（始终可见）
        hdr=QHBoxLayout()
        ic=QLabel(); ic.setPixmap(quark_icon("file", 15).pixmap(15, 15))
        ic.setStyleSheet("background:transparent")
        hdr.addWidget(ic)
        nm_lbl=QLabel(f"<b>{nm}</b>")
        nm_lbl.setStyleSheet(f"color:{C_TEXT};background:transparent;font-size:12px")
        hdr.addWidget(nm_lbl)
        hdr.addStretch()
        toggle=QLabel(); toggle.setPixmap(quark_icon("chevron_right", 12).pixmap(12, 12))
        toggle.setStyleSheet("background:transparent")
        hdr.addWidget(toggle)
        cl.addLayout(hdr)

        # 详细 diff（默认隐藏）
        detail=QTextEdit(); detail.setReadOnly(True); detail.setFrameShape(QFrame.Shape.NoFrame)
        detail.setFont(QFont("Consolas",10)); detail.setMaximumHeight(200); detail.hide()
        dt=""
        for ln in old.split('\n'): dt+=f"<span style='color:#ef4444'>- {_html.escape(ln)}</span><br>"
        if old and new: dt+=f"<span style='color:{C_DIM}'>→</span><br>"
        for ln in new.split('\n'): dt+=f"<span style='color:#22c55e'>+ {_html.escape(ln)}</span><br>"
        detail.setHtml(dt)
        cl.addWidget(detail)

        def toggle_diff(e=None):
            if detail.isHidden():
                detail.show(); toggle.setPixmap(quark_icon("chevron_down", 12).pixmap(12, 12))
            else:
                detail.hide(); toggle.setPixmap(quark_icon("chevron_right", 12).pixmap(12, 12))
        card.mousePressEvent = toggle_diff

        self.msg_layout.insertWidget(max(0,self.msg_layout.count()-1),card)
        QTimer.singleShot(30,self._scroll)

    def add_review_card(self, filepath, old, new, on_accept, on_reject):
        """代码审阅卡片（Accept/Reject 按钮）"""
        nm=Path(filepath).name if filepath else "unknown"
        card=QFrame()
        card.setStyleSheet(f"QFrame{{background:{C_PANEL};border:none;border-radius:14px;margin:4px 10px}}")
        cl=QVBoxLayout(card); cl.setContentsMargins(12,8,12,8); cl.setSpacing(6)
        cl.addWidget(QLabel(f"<b>Review: {nm}</b>"))
        diff=QTextEdit(); diff.setReadOnly(True); diff.setFrameShape(QFrame.Shape.NoFrame)
        diff.setFont(QFont("Consolas",11)); diff.setMaximumHeight(200)
        dt=""
        for ln in old.split('\n'): dt+=f"<span style='color:#ef4444'>- {_html.escape(ln)}</span><br>"
        dt+=f"<span style='color:{C_DIM}'>→</span><br>"
        for ln in new.split('\n'): dt+=f"<span style='color:#22c55e'>+ {_html.escape(ln)}</span><br>"
        diff.setHtml(dt); cl.addWidget(diff)
        bl=QHBoxLayout()
        acc=QPushButton("Accept"); acc.clicked.connect(lambda: (on_accept(), card.hide()))
        rej=QPushButton("Reject"); rej.setStyleSheet(_with_tooltip_style(f"QPushButton{{background:{C_RED}}}QPushButton:hover{{background:#dc2626}}"))
        rej.clicked.connect(lambda: (on_reject(), card.hide()))
        bl.addWidget(acc); bl.addWidget(rej); cl.addLayout(bl)
        self.msg_layout.insertWidget(max(0,self.msg_layout.count()-1),card)
        QTimer.singleShot(30,self._scroll)

    def _scroll(self):
        sb=self.scroll.verticalScrollBar(); sb.setValue(sb.maximum())

# ── FileTree ────────────────────────────────────────────────
class FileTreeDelegate(QStyledItemDelegate):
    """Hover/选中背景横跨整行（含左侧缩进与分支区），避免只在文字/图标格上色。"""

    def __init__(self, tree: QTreeView):
        super().__init__(tree)
        self._tree = tree

    def paint(self, painter, option, index):
        vp = self._tree.viewport()
        r = option.rect
        y, h = r.top(), max(1, r.height())
        # 不覆盖树的缩进留白区，只高亮当前项可视内容区域
        full = QRect(r.left(), y, max(0, vp.width() - r.left()), h)

        st = option.state
        enabled = bool(st & QStyle.StateFlag.State_Enabled)
        selected = bool(st & QStyle.StateFlag.State_Selected)
        hover = bool(st & QStyle.StateFlag.State_MouseOver)

        if _is_light_theme():
            # QColor 对 CSS 字符串中的小数 alpha 支持不稳定，直接用 RGBA 整数避免发黑
            sel_col = QColor(12, 74, 110, 56)
            hi_col = QColor(0, 0, 0, 15)
        else:
            sel_col = QColor(C_FILE_TREE_SEL)
            hi_col = QColor(C_PANEL_HI)
        if enabled and selected:
            painter.fillRect(full, sel_col)
        elif hover:
            painter.fillRect(full, hi_col)

        painter.save()
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        text_col = "#000000" if _is_light_theme() else C_TEXT
        content = r.adjusted(6, 0, -6, 0)
        x = content.left()
        if isinstance(icon, QIcon):
            icon_size = option.decorationSize if option.decorationSize.isValid() else QSize(16, 16)
            icon_rect = QRect(x, content.center().y() - icon_size.height() // 2, icon_size.width(), icon_size.height())
            icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter)
            x = icon_rect.right() + 8
        text_rect = QRect(x, content.top(), max(0, content.right() - x), content.height())
        painter.setPen(QColor(text_col))
        painter.setFont(option.font)
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
        painter.restore()


class FileTree(QTreeView):
    file_selected = pyqtSignal(str)
    def __init__(self, ws, parent=None):
        super().__init__(parent)
        self.setObjectName("fileTree")
        self.setItemDelegate(FileTreeDelegate(self))
        self.setHeaderHidden(True); self.setMinimumWidth(160)
        self.setAnimated(True); self.setIndentation(14)
        self.setMouseTracking(True)
        self.setAllColumnsShowFocus(True)
        self._model=QStandardItemModel(); self.setModel(self._model)
        self._fi=quark_icon("file", 15)
        self._di = quark_icon("folder", 15, C_ACCENT_LIGHT if _is_light_theme() else C_ACCENT)
        self.set_workspace(ws); self.doubleClicked.connect(self._clk)

    def apply_theme(self):
        """Refresh folder icons when switching light/dark."""
        light = _is_light_theme()
        c = C_ACCENT_LIGHT if light else C_ACCENT
        self._di = quark_icon("folder", 15, c)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Base, QColor(Qt.GlobalColor.transparent))
        pal.setColor(QPalette.ColorRole.Window, QColor(Qt.GlobalColor.transparent))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(C_FILE_TREE_SEL_LIGHT if light else C_FILE_TREE_SEL))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#000000" if light else C_TEXT))
        pal.setColor(QPalette.ColorRole.Text, QColor("#000000" if light else C_TEXT))
        self.setPalette(pal)
        self.setStyleSheet(
            "QTreeView#fileTree{background:transparent;border:none;outline:none;}"
            "QTreeView#fileTree::item{background:transparent;border:none;}"
            f"QTreeView#fileTree::item:hover{{background:{'rgba(0,0,0,15)' if light else C_PANEL_HI};}}"
            f"QTreeView#fileTree::item:selected{{background:{'rgba(12,74,110,56)' if light else C_FILE_TREE_SEL};}}"
            f"QTreeView#fileTree::item:selected:active{{background:{'rgba(12,74,110,56)' if light else C_FILE_TREE_SEL};}}"
            "QTreeView#fileTree::branch{background:transparent;}"
        )

        def walk(item):
            if item is None:
                return
            for i in range(item.rowCount()):
                ch = item.child(i, 0)
                if ch is None:
                    continue
                if ch.rowCount() > 0:
                    ch.setIcon(self._di)
                walk(ch)

        root = self._model.item(0, 0)
        if root is not None:
            root.setIcon(self._di)
            walk(root)
        self.viewport().update()

    def set_workspace(self, ws):
        self._ws=os.path.abspath(ws); self._model.clear()
        r=QStandardItem(self._di,os.path.basename(self._ws) or self._ws)
        r.setData(self._ws,Qt.ItemDataRole.UserRole); r.setEditable(False)
        self._model.appendRow(r); self._pop(r,self._ws); self.expandAll()

    def _pop(self, p, path, d=0):
        if d>3: return
        try: es=sorted(os.scandir(path),key=lambda e:(not e.is_dir(),e.name.lower()))
        except PermissionError: return
        for e in es:
            if e.name.startswith('.') and e.name not in ('.env','.gitignore'): continue
            if e.is_dir():
                it=QStandardItem(self._di,e.name); it.setData(e.path,Qt.ItemDataRole.UserRole)
                it.setEditable(False); p.appendRow(it); self._pop(it,e.path,d+1)
            else:
                it=QStandardItem(self._fi,e.name); it.setData(e.path,Qt.ItemDataRole.UserRole)
                it.setEditable(False); p.appendRow(it)

    def _clk(self, idx):
        p=idx.data(Qt.ItemDataRole.UserRole)
        if p and os.path.isfile(p): self.file_selected.emit(p)

# ── AgentWorker ─────────────────────────────────────────────
class AgentWorker(QThread):
    text_delta   = pyqtSignal(str)
    tool_call    = pyqtSignal(str, str)
    tool_result  = pyqtSignal(str, bool)
    diff_preview = pyqtSignal(str, str, str)
    confirm_request = pyqtSignal(str, str, str)  # path, old, new (edit_file review)
    kscc_status  = pyqtSignal(str)       # kscc thinking/tool status
    context_info = pyqtSignal(str)
    skill_info   = pyqtSignal(str)
    skill_draft  = pyqtSignal(str)
    done         = pyqtSignal(str, int, str)
    error        = pyqtSignal(str)
    file_modified= pyqtSignal(str, str)

    def __init__(self, agent, prompt, attachments=None):
        super().__init__(); self.agent=agent; self.prompt=prompt; self.attachments=attachments or []
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True
        self.requestInterruption()
        try:
            if getattr(self.agent, "_confirm_event", None):
                self.agent._confirm_result = False
                self.agent._confirm_event.set()
        except Exception:
            pass

    @staticmethod
    def _thinking_status(text: str) -> str:
        t=text.strip().lower()
        if any(k in t for k in ['read','file','search','grep','scan','look']): return "Reading files..."
        if any(k in t for k in ['plan','approach','step','think']): return "Planning..."
        if any(k in t for k in ['fix','bug','error','issue']): return "Analyzing issue..."
        if any(k in t for k in ['write','edit','create','modify','change']): return "Preparing changes..."
        if any(k in t for k in ['run','execute','shell','bash','test']): return "Running commands..."
        return "Thinking..."

    @staticmethod
    def _tool_status(name: str, inp: dict) -> str:
        target = inp.get("file_path","") or inp.get("path","") or inp.get("command","") or inp.get("pattern","")
        target = str(target)[:40]
        tool_map = {
            "Read": f"Reading: {target}" if target else "Reading file...",
            "Write": f"Writing: {target}" if target else "Writing file...",
            "Edit": f"Editing: {target}" if target else "Editing code...",
            "Bash": f"Running: {target}" if target else "Running command...",
            "Grep": f"Searching: {target}" if target else "Searching code...",
            "Glob": f"Finding: {target}" if target else "Finding files...",
            "WebFetch": "Fetching web content...",
            "WebSearch": "Searching web...",
            "Task": "Running subtask...",
            "AskUserQuestion": "Asking for clarification...",
        }
        return tool_map.get(name, f"Tool: {name}")

    def run(self):
        async def _r():
            try:
                async for e in self.agent.run(self.prompt, self.attachments):
                    if self._stop_requested or self.isInterruptionRequested():
                        break
                    t=e.get('type','')
                    if t=='text_delta': self.text_delta.emit(e.get('text',''))
                    elif t=='tool_call':
                        n=e.get('name',''); a=e.get('arguments',{})
                        self.tool_call.emit(n,e.get('preview',''))
                        if n=='edit_file' and isinstance(a,dict):
                            self.diff_preview.emit(a.get('path',''),a.get('old_string',''),a.get('new_string',''))
                    elif t=='thinking':
                        # kscc thinking → status
                        th=e.get('text','')
                        st=self._thinking_status(th)
                        if st: self.kscc_status.emit(st)
                    elif t=='kscc_tool':
                        n=e.get('name',''); inp=e.get('input',{})
                        st=self._tool_status(n,inp)
                        if st: self.kscc_status.emit(st)
                    elif t=='tool_result':
                        self.tool_result.emit(e.get('result',''), e.get('error',False))
                        if e.get('name','') in ('write_file','edit_file') and not e.get('error'):
                            pass  # file_modified handled by main
                    elif t=='context': self.context_info.emit(json.dumps(e.get('summary',{})))
                    elif t in ('skill_match', 'skill_miss', 'skill_ambiguous', 'skill_status'):
                        self.skill_info.emit(json.dumps(e, ensure_ascii=False))
                    elif t=='skill_save_draft':
                        self.skill_draft.emit(json.dumps(e.get('draft', {}), ensure_ascii=False))
                    elif t=='confirm':
                        # 代码审阅
                        tc=e.get('tool_call',{}); a=e.get('args',{})
                        self.confirm_request.emit(a.get('path',''), a.get('old_string',''), a.get('new_string',''))
                        # 等待 approve/reject (blocking)
                        self.agent._confirm_event.clear()
                        self.agent._confirm_event.wait()
                    elif t=='done':
                        if not (self._stop_requested or self.isInterruptionRequested()):
                            self.done.emit(e.get('text',''),e.get('turns',0),json.dumps(e.get('context',{})))
                    elif t=='error': self.error.emit(e.get('content',''))
            except Exception as ex: self.error.emit(f"{type(ex).__name__}: {ex}")
        try: asyncio.run(_r())
        except Exception as ex: self.error.emit(f"Worker: {ex}")

# ── SettingsPage (inline, non-modal) ───────────────────────
class SettingsPage(QWidget):
    saved = pyqtSignal()
    cancelled = pyqtSignal()

    def __init__(self, cfg, parent=None):
        super().__init__(parent); self.cfg=cfg; self.setMinimumWidth(720)
        self.setObjectName("SettingsPage")
        self._model_row_prev = -1
        self._monaco_installing = False
        self._monaco_status_tip = ""
        self._light = str(getattr(cfg, "theme", "dark")).lower() == "light"
        self._txt = "#0f172a" if self._light else C_TEXT
        self._dim = "#64748b" if self._light else C_DIM
        self._panel = "#f0f0f0" if self._light else C_PANEL
        self._panel_hi = "#e7e8e8" if self._light else C_PANEL_HI
        root=QHBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        nav_wrap = QFrame()
        nav_wrap.setObjectName("settingsNavWrap")
        nav_wrap.setFixedWidth(230)
        nav_l = QVBoxLayout(nav_wrap); nav_l.setContentsMargins(16,14,10,12); nav_l.setSpacing(10)
        t = QLabel("设置")
        t.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        t.setStyleSheet(f"font-size:22px;font-weight:800;color:{self._txt};background:transparent;letter-spacing:0.04em")
        nav_l.addWidget(t)
        self.nav = QListWidget()
        self.nav.setStyleSheet(
            f"QListWidget{{background:transparent;border:none;color:{self._dim};font-size:15px;font-weight:700}}"
            f"QListWidget::item{{padding:10px 12px;border-radius:10px;text-align:left;}}"
            f"QListWidget::item:selected{{background:{self._panel_hi};color:{self._txt}}}"
        )
        for item in ("外观", "模型与API", "IDE", "Agent"):
            self.nav.addItem(QListWidgetItem(item))
        self.nav.setCurrentRow(0)
        nav_l.addWidget(self.nav, 1)
        nav_l.addStretch(1)
        root.addWidget(nav_wrap)

        right = QFrame()
        right.setObjectName("settingsRightWrap")
        right_l = QVBoxLayout(right); right_l.setContentsMargins(28,24,28,12); right_l.setSpacing(12)
        self.pages = QStackedWidget()
        right_l.addWidget(self.pages, 1)
        foot = QHBoxLayout(); foot.setSpacing(8)
        self.save_btn = QPushButton("保存")
        self.save_btn.clicked.connect(self._sv)
        foot.addStretch(1); foot.addWidget(self.save_btn)
        right_l.addLayout(foot)
        root.addWidget(right, 1)

        self._build_appearance_page()
        self._build_model_api_page()
        self._build_ide_page()
        self._build_agent_page()
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self._apply_page_theme()
        self.reload_from_config()

    def _apply_page_theme(self):
        self._txt = "#4f5153" if self._light else C_TEXT
        self._dim = "#7b7e82" if self._light else C_DIM
        self._panel = "#f5f5f5" if self._light else "#202020"
        self._panel_hi = "#e7e8e8" if self._light else C_PANEL_HI
        c_bg = "#ffffff" if self._light else "#181818"
        c_fg = "#4f5153" if self._light else "#ffffff"
        c_dim = "#7b7e82" if self._light else "#858585"
        c_card = "#ffffff" if self._light else "#181818"
        c_border = "#d7dbe2" if self._light else "#2f3238"
        c_border2 = "#d7dbe2" if self._light else "#3a3d41"
        c_hover = "#e7e8e8" if self._light else "#313131"
        c_select_bg = "#cce8ff" if self._light else "#264f78"
        c_select_fg = "#1e1e1e" if self._light else "#ffffff"
        c_popup_bg = "#ffffff" if self._light else "#2d2d2d"
        self.setStyleSheet(
            f"QWidget#SettingsPage{{background:{c_bg};}}"
            f"QFrame#settingsNavWrap{{background:{'#f5f5f5' if self._light else '#202020'};border:none;}}"
            f"QFrame#settingsRightWrap{{background:{'#ffffff' if self._light else '#181818'};border:none;}}"
            f"QLineEdit,QSpinBox{{background:transparent;color:{c_fg};border:none;border-radius:0;min-height:30px;padding:0 2px;font-size:13px;"
            f"selection-background-color:{c_select_bg};selection-color:{c_select_fg};}}"
            f"QLineEdit:focus,QSpinBox:focus{{border:none;outline:none;}}"
            f"QLineEdit:disabled,QSpinBox:disabled{{color:{'#9ca3af' if self._light else '#6b7280'};background:transparent;}}"
            f"QCheckBox{{color:{c_fg};font-size:13px;spacing:8px;}}"
            f"QCheckBox::indicator{{width:16px;height:16px;border-radius:8px;border:1px solid {c_border};background:{c_card};}}"
            f"QCheckBox::indicator:checked{{background:#007acc;border:1px solid #007acc;}}"
            f"QCheckBox:disabled{{color:{'#9ca3af' if self._light else '#6b7280'};}}"
            f"QPushButton:disabled{{color:{'#9ca3af' if self._light else '#6b7280'};}}"
            f"QListWidget{{background:transparent;border:none;outline:none;color:{c_fg};}}"
            f"QListWidget::item:hover{{background:{c_hover};}}"
            f"QListWidget::item:selected{{background:{c_select_bg};color:{c_select_fg};}}"
            f"QToolTip{{background:{'#f3f3f4' if self._light else '#252526'};color:{c_fg};border:1px solid {c_border2};padding:4px 6px;}}"
        )
        if hasattr(self, "nav"):
            self.nav.setStyleSheet(
                "QListWidget{background:transparent;border:none;outline:none;font-size:15px;}"
                f"QListWidget::item{{color:{c_fg};padding:10px 12px;border-radius:10px;margin:3px;font-weight:700;text-align:left;}}"
                f"QListWidget::item:hover{{background:{c_hover};color:{c_fg};}}"
                f"QListWidget::item:selected{{background:{c_hover};color:{c_fg};}}"
            )
        if hasattr(self, "oa_list"):
            self.oa_list.setStyleSheet(
                f"QListWidget{{background:{'#f6f7f8' if self._light else '#202224'};border:none;border-radius:10px;color:{c_fg};font-size:12px;}}"
                f"QListWidget::item{{padding:8px 12px;border-radius:8px;margin:2px 4px;}}"
                f"QListWidget::item:hover{{background:{c_hover};color:{c_fg};}}"
                f"QListWidget::item:selected{{background:{c_select_bg};color:{c_select_fg};}}"
            )
        for btn_name in ("add_btn", "del_btn", "set_active_btn", "monaco_check_btn", "monaco_install_btn"):
            if hasattr(self, btn_name):
                getattr(self, btn_name).setStyleSheet(
                    _with_tooltip_style(
                        f"QPushButton{{background:{'#ffffff' if self._light else '#242628'};color:{c_fg};border:1px solid {c_border2};border-radius:12px;min-height:36px;font-size:13px;font-weight:500;padding:0 18px;}}"
                        f"QPushButton:hover{{background:{c_hover};border:1px solid {c_border2};}}"
                        f"QPushButton:pressed{{background:{'#dde3ea' if self._light else '#313131'};}}",
                        self._light,
                    )
                )
        if hasattr(self, "save_btn"):
            self.save_btn.setStyleSheet(
                _with_tooltip_style(
                    "QPushButton{background:#1683d8;color:#ffffff;border:none;border-radius:12px;min-width:108px;min-height:42px;padding:0 24px;font-size:15px;font-weight:700;}"
                    "QPushButton:hover{background:#1d8dea;}"
                    "QPushButton:pressed{background:#0f73bf;}",
                    self._light,
                )
            )
        if hasattr(self, "accent_btn"):
            self._refresh_accent_button()
        if hasattr(self, "monaco_status"):
            self._refresh_monaco_dependency_ui()

    def _style_combo(self, combo: QComboBox):
        c_fg = "#4f5153" if self._light else "#ffffff"
        c_hover = "#e7e8e8" if self._light else "#313131"
        c_select_bg = "#cce8ff" if self._light else "#264f78"
        c_select_fg = "#1e1e1e" if self._light else "#ffffff"
        c_popup_bg = "#ffffff" if self._light else "#2d2d2d"
        combo.setMaxVisibleItems(12)
        combo.setEditable(False)
        combo.setStyleSheet(
            f"QComboBox{{background:transparent;color:{c_fg};border:none;border-radius:0;min-height:30px;padding:0 2px;font-size:13px;selection-background-color:{c_select_bg};selection-color:{c_select_fg};}}"
            "QComboBox:focus{border:none;outline:none;}"
            "QComboBox::drop-down{border:none;width:18px;background:transparent;}"
            f"QComboBox QAbstractItemView{{background:{c_popup_bg};color:{c_fg};border:none;selection-background-color:{c_select_bg};selection-color:{c_select_fg};outline:none;}}"
            f"QComboBox QAbstractItemView::item{{background:{c_popup_bg};color:{c_fg};min-height:24px;padding:2px 8px;}}"
            f"QComboBox QAbstractItemView::item:hover{{background:{c_hover};color:{c_fg};}}"
        )

    def _make_size_combo(self, values: list[int]) -> NoWheelComboBox:
        combo = NoWheelComboBox()
        combo.addItems([str(v) for v in values])
        self._style_combo(combo)
        return combo

    def _make_group(self, title: str, subtitle: str = "") -> tuple[QFrame, QVBoxLayout]:
        box = QFrame()
        box.setStyleSheet("QFrame{background:transparent;border:none}")
        lay = QVBoxLayout(box); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)
        ttl = QLabel(title)
        ttl.setStyleSheet(f"font-size:28px;font-weight:600;color:{self._txt};background:transparent")
        lay.addWidget(ttl)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setStyleSheet(f"color:{self._dim};font-size:13px;background:transparent")
            lay.addWidget(sub)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{'#d7dbe2' if self._light else '#2f3238'};border:none;")
        lay.addSpacing(10)
        lay.addWidget(sep)
        lay.addSpacing(12)
        return box, lay

    def _add_setting_card(self, parent: QVBoxLayout, title: str, desc: str, control: QWidget):
        card = QFrame()
        card.setStyleSheet("QFrame{background:transparent;border:none}")
        cl = QVBoxLayout(card); cl.setContentsMargins(0,0,0,0); cl.setSpacing(6)
        t = QLabel(title)
        t.setStyleSheet(f"font-size:13px;font-weight:500;color:{self._txt};background:transparent")
        d = QLabel(desc)
        d.setWordWrap(True)
        d.setStyleSheet(f"font-size:11px;color:{self._dim};background:transparent")
        cl.addWidget(t)
        cl.addWidget(d)
        cl.addWidget(control)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{'#d7dbe2' if self._light else '#2f3238'};border:none;")
        cl.addSpacing(4)
        cl.addWidget(sep)
        parent.addWidget(card)

    def _pick_accent_color(self):
        current = QColor(self.accent_value.text().strip() or "#5ee9ff")
        dlg = QColorDialog(current, self)
        dlg.setWindowTitle("选择强调色")
        dlg.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, False)
        dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        dlg.setPalette(QApplication.instance().palette())
        if self._light:
            dlg.setStyleSheet(
                "QColorDialog{background:#f7f7f8;color:#4f5153;}"
                "QWidget{color:#4f5153;}"
                "QLabel{color:#4f5153;}"
                "QLineEdit{background:#ffffff;color:#4f5153;border:1px solid #d7dbe2;border-radius:6px;padding:4px 6px;}"
                "QSpinBox{background:#ffffff;color:#4f5153;border:1px solid #d7dbe2;border-radius:6px;padding:4px 6px;}"
                "QPushButton{background:#ffffff;color:#4f5153;border:1px solid #d7dbe2;border-radius:8px;min-height:28px;padding:0 12px;}"
                "QPushButton:hover{background:#eef2f7;}"
            )
        else:
            dlg.setStyleSheet(
                "QColorDialog{background:#1f1f1f;color:#eef4f8;}"
                "QWidget{color:#eef4f8;}"
                "QLabel{color:#eef4f8;}"
                "QLineEdit{background:#2a2a2a;color:#eef4f8;border:1px solid #3a3d41;border-radius:6px;padding:4px 6px;}"
                "QSpinBox{background:#2a2a2a;color:#eef4f8;border:1px solid #3a3d41;border-radius:6px;padding:4px 6px;}"
                "QPushButton{background:#2a2a2a;color:#eef4f8;border:1px solid #3a3d41;border-radius:8px;min-height:28px;padding:0 12px;}"
                "QPushButton:hover{background:#313131;}"
            )
        if dlg.exec():
            color = dlg.currentColor()
            self.accent_value.setText(color.name())
            self._refresh_accent_button()

    def _refresh_accent_button(self):
        if not hasattr(self, "accent_btn"):
            return
        col = self.accent_value.text().strip() or "#5ee9ff"
        border = "#d7dbe2" if self._light else "#3a3d41"
        self.accent_btn.setStyleSheet(
            f"QPushButton{{background:{col};border:1px solid {border};border-radius:10px;min-width:34px;min-height:34px;max-width:34px;max-height:34px;}}"
            f"QPushButton:hover{{border:1px solid {'#9ca3af' if self._light else '#94a3b8'};}}"
        )
        if hasattr(self, "accent_value"):
            self.accent_value.setStyleSheet(f"color:{self._txt};font-size:13px;background:transparent")

    def _build_appearance_page(self):
        page = QWidget(); v = QVBoxLayout(page); v.setContentsMargins(0,0,0,0); v.setSpacing(10)
        box, form = self._make_group("外观", "主题、字体与强调色")
        self.theme_combo = NoWheelComboBox(); self.theme_combo.addItems(["dark", "light"])
        self.ui_font_combo = NoWheelFontComboBox()
        self.ui_font_size = self._make_size_combo(list(range(9, 21)))
        self.code_font_combo = NoWheelFontComboBox()
        self.code_font_size = self._make_size_combo(list(range(10, 25)))
        self.accent_btn = QPushButton()
        self.accent_btn.clicked.connect(self._pick_accent_color)
        self.accent_value = QLabel("#5ee9ff")
        accent_row = QWidget()
        accent_l = QHBoxLayout(accent_row)
        accent_l.setContentsMargins(0,0,0,0)
        accent_l.setSpacing(8)
        accent_l.addWidget(self.accent_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        accent_l.addWidget(self.accent_value, 0, Qt.AlignmentFlag.AlignVCenter)
        accent_l.addStretch(1)
        for combo in (self.theme_combo, self.ui_font_combo, self.code_font_combo):
            self._style_combo(combo)
        self._add_setting_card(form, "主题", "切换整体主题（当前可选 dark / light）", self.theme_combo)
        self._add_setting_card(form, "UI 字体", "主界面文字字体", self.ui_font_combo)
        self._add_setting_card(form, "UI 字号", "主界面文字大小", self.ui_font_size)
        self._add_setting_card(form, "代码字体", "编辑器与代码块字体", self.code_font_combo)
        self._add_setting_card(form, "代码字号", "编辑器代码字体大小", self.code_font_size)
        self._add_setting_card(form, "强调色", "品牌高亮色（Hex）", accent_row)
        v.addWidget(box); v.addStretch(1)
        self.pages.addWidget(page)

    def _build_model_api_page(self):
        page = QWidget(); v = QVBoxLayout(page); v.setContentsMargins(0,0,0,0); v.setSpacing(10)
        box, form = self._make_group("模型与 API", "管理可用模型、启用状态与连接参数")
        content = QWidget()
        content_l = QHBoxLayout(content)
        content_l.setContentsMargins(0, 4, 0, 0)
        content_l.setSpacing(16)

        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(12)
        left_title = QLabel("已启用模型列表")
        left_title.setStyleSheet(f"font-size:13px;font-weight:600;color:{self._txt};background:transparent")
        left_l.addWidget(left_title)
        self.oa_list = QListWidget()
        self.oa_list.setStyleSheet(
            f"QListWidget{{background:{'#f6f7f8' if self._light else '#202224'};border:none;border-radius:10px}}"
            f"QListWidget::item{{padding:8px 12px}}"
            f"QListWidget::item:selected{{background:{self._panel_hi};color:{self._txt}}}"
        )
        self.oa_list.currentRowChanged.connect(self._on_model_row_changed)
        left_l.addWidget(self.oa_list, 1)

        btn_row = QWidget()
        btn_row_l = QHBoxLayout(btn_row)
        btn_row_l.setContentsMargins(0, 0, 0, 0)
        btn_row_l.setSpacing(10)
        self.add_btn = QPushButton("新增")
        self.del_btn = QPushButton("删除")
        self.set_active_btn = QPushButton("设为当前")
        self.add_btn.clicked.connect(self._add_model)
        self.del_btn.clicked.connect(self._remove_model)
        self.set_active_btn.clicked.connect(self._set_active_model)
        self.set_active_btn.hide()
        btn_row_l.addWidget(self.add_btn, 1)
        btn_row_l.addWidget(self.del_btn, 1)
        left_l.addWidget(btn_row)

        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(12)

        self.m_enabled = QCheckBox("启用模型")
        right_l.addWidget(self.m_enabled, 0, Qt.AlignmentFlag.AlignTop)
        self.m_model = QLineEdit()
        self.m_key = QLineEdit(); self.m_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.m_url = QLineEdit(); self.m_url.setPlaceholderText("该模型专属 API Base URL（可留空）")
        self.m_ctx_limit = NoWheelSpinBox(); self.m_ctx_limit.setRange(4096, 2_000_000); self.m_ctx_limit.setSingleStep(16384)
        self.m_out_limit = NoWheelSpinBox(); self.m_out_limit.setRange(512, 400_000); self.m_out_limit.setSingleStep(8192)

        def add_field(parent_layout: QVBoxLayout, title: str, control: QWidget, placeholder: str = ""):
            wrap = QWidget()
            wrap_l = QVBoxLayout(wrap)
            wrap_l.setContentsMargins(0, 0, 0, 0)
            wrap_l.setSpacing(6)
            title_lb = QLabel(title)
            title_lb.setStyleSheet(f"font-size:13px;font-weight:600;color:{self._txt};background:transparent")
            wrap_l.addWidget(title_lb)
            if placeholder:
                control.setPlaceholderText(placeholder)
            wrap_l.addWidget(control)
            parent_layout.addWidget(wrap)

        add_field(right_l, "模型名称", self.m_model)
        add_field(right_l, "API Key", self.m_key, "该模型专属 API Key")
        add_field(right_l, "Base URL", self.m_url)
        add_field(right_l, "Context Limit", self.m_ctx_limit)
        add_field(right_l, "Max Output", self.m_out_limit)
        right_l.addStretch(1)

        content_l.addWidget(left, 5)
        content_l.addWidget(right, 3)
        form.addWidget(content)
        v.addWidget(box); v.addStretch(1)
        self.pages.addWidget(page)

    def _build_ide_page(self):
        page = QWidget(); v = QVBoxLayout(page); v.setContentsMargins(0,0,0,0); v.setSpacing(10)
        box, form = self._make_group("IDE", "代码编辑器相关设置")
        self.ide_wrap = QCheckBox("自动换行")
        self.ide_minimap = QCheckBox("显示 minimap")
        self.ide_font_size = self._make_size_combo(list(range(10, 25)))
        self.monaco_status = QLabel()
        self.monaco_status.setWordWrap(True)
        self.monaco_check_btn = QPushButton("检查依赖")
        self.monaco_install_btn = QPushButton("安装 Monaco")
        self.monaco_check_btn.clicked.connect(self._refresh_monaco_dependency_ui)
        self.monaco_install_btn.clicked.connect(self._install_monaco_dependency)
        monaco_row = QWidget()
        monaco_row_l = QHBoxLayout(monaco_row)
        monaco_row_l.setContentsMargins(0, 0, 0, 0)
        monaco_row_l.setSpacing(10)
        monaco_row_l.addWidget(self.monaco_status, 1, Qt.AlignmentFlag.AlignVCenter)
        monaco_row_l.addWidget(self.monaco_check_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        monaco_row_l.addWidget(self.monaco_install_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._add_setting_card(form, "自动换行", "编辑器长行自动折行显示", self.ide_wrap)
        self._add_setting_card(form, "显示 minimap", "在编辑器右侧显示代码缩略图", self.ide_minimap)
        self._add_setting_card(form, "编辑器字号", "Monaco 编辑器字体大小", self.ide_font_size)
        self._add_setting_card(form, "Monaco 依赖", "按需检查并安装 Monaco 编辑器依赖；未安装时用户可在这里手动安装。", monaco_row)

        data_row = QWidget()
        data_row_l = QHBoxLayout(data_row)
        data_row_l.setContentsMargins(0, 0, 0, 0)
        data_row_l.setSpacing(10)
        self.export_data_btn = QPushButton("导出数据")
        self.import_data_btn = QPushButton("导入数据")
        self.export_data_btn.clicked.connect(self._export_local_data)
        self.import_data_btn.clicked.connect(self._import_local_data)
        data_row_l.addWidget(self.export_data_btn)
        data_row_l.addWidget(self.import_data_btn)
        data_row_l.addStretch(1)
        self._add_setting_card(form, "本地数据迁移", "导出/导入 skills 与 memory（zip）。用于备份、换机器迁移。", data_row)

        v.addWidget(box); v.addStretch(1)
        self.pages.addWidget(page)

    def _build_agent_page(self):
        page = QWidget(); v = QVBoxLayout(page); v.setContentsMargins(0,0,0,0); v.setSpacing(10)
        box, form = self._make_group("Agent", "Agent 行为与本地能力增强设置")
        self.skills_enabled = QCheckBox("启用 Skill 匹配")
        self.skill_debug_log = QCheckBox("记录 Skill 调试日志")
        self.memory_injection_enabled = QCheckBox("启用本地记忆注入")
        self._add_setting_card(form, "Skill 匹配", "关闭后将跳过本地 skill 召回，仅走普通对话。", self.skills_enabled)
        self._add_setting_card(form, "Skill 调试日志", "写入 logs/skill_debug.log，记录命中/未命中原因。", self.skill_debug_log)
        self._add_setting_card(form, "本地记忆注入", "将 memory/ 中规则、事实、归档摘要注入系统提示词。", self.memory_injection_enabled)
        v.addWidget(box); v.addStretch(1)
        self.pages.addWidget(page)

    def _export_local_data(self):
        try:
            path, _ = QFileDialog.getSaveFileName(self, "导出本地数据", str(Path(__file__).parent / "kscc-data.zip"), "Zip (*.zip)")
            if not path:
                return
            out = data_portability.export_zip(path)
            QToolTip.showText(self.mapToGlobal(self.rect().center()), f"已导出: {Path(out).name}", self)
        except Exception as e:
            QToolTip.showText(self.mapToGlobal(self.rect().center()), f"导出失败: {e}", self)

    def _import_local_data(self):
        try:
            path, _ = QFileDialog.getOpenFileName(self, "导入本地数据", str(Path(__file__).parent), "Zip (*.zip)")
            if not path:
                return
            counts = data_portability.import_zip(path)
            msg = f"导入完成: +skills {counts.get('skills_added',0)} / upd {counts.get('skills_updated',0)}"
            QToolTip.showText(self.mapToGlobal(self.rect().center()), msg, self)
        except Exception as e:
            QToolTip.showText(self.mapToGlobal(self.rect().center()), f"导入失败: {e}", self)

    def _read_monaco_dependency_info(self) -> tuple[bool, str]:
        if not MONACO_PACKAGE_JSON.exists():
            return False, ""
        try:
            payload = json.loads(MONACO_PACKAGE_JSON.read_text("utf-8", errors="replace"))
        except Exception:
            return True, ""
        version = str(payload.get("version") or "").strip()
        return True, version

    def _refresh_monaco_dependency_ui(self):
        if not hasattr(self, "monaco_status"):
            return
        installed, version = self._read_monaco_dependency_info()
        if self._monaco_installing:
            status_text = "正在安装 Monaco 依赖..."
            status_color = "#2563eb" if self._light else "#7dd3fc"
        elif installed:
            version_text = f" · v{version}" if version else ""
            status_text = f"已安装{version_text}"
            status_color = "#0f766e" if self._light else "#5eead4"
        else:
            status_text = "未安装，使用 Monaco IDE 前需先安装 npm 依赖"
            status_color = "#b45309" if self._light else "#fbbf24"
        self.monaco_status.setText(status_text)
        self.monaco_status.setStyleSheet(f"color:{status_color};font-size:12px;background:transparent;")
        self.monaco_status.setToolTip(self._monaco_status_tip or status_text)
        install_text = "安装中..." if self._monaco_installing else ("重装 Monaco" if installed else "安装 Monaco")
        self.monaco_install_btn.setText(install_text)
        self.monaco_install_btn.setEnabled(not self._monaco_installing)
        self.monaco_check_btn.setEnabled(not self._monaco_installing)
        self.monaco_install_btn.setToolTip("执行 npm install，安装或重装 Monaco 相关依赖")
        self.monaco_check_btn.setToolTip("重新检查 node_modules 中的 Monaco 安装状态")

    def _finish_monaco_install(self, ok: bool, message: str):
        self._monaco_installing = False
        self._monaco_status_tip = message.strip() or ("Monaco 依赖安装完成" if ok else "Monaco 依赖安装失败")
        self._refresh_monaco_dependency_ui()

    def _install_monaco_dependency(self):
        if self._monaco_installing:
            return
        self._monaco_installing = True
        self._monaco_status_tip = "正在执行 npm install ..."
        self._refresh_monaco_dependency_ui()

        def worker():
            npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
            try:
                if not PROJECT_PACKAGE_JSON.exists():
                    raise FileNotFoundError("未找到 package.json，无法安装 Monaco 依赖。")
                result = subprocess.run(
                    [npm_cmd, "install"],
                    cwd=str(Path(__file__).parent),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=900,
                    check=False,
                )
                output = (result.stdout or "").strip()
                err = (result.stderr or "").strip()
                detail = (output + "\n" + err).strip()
                if result.returncode == 0:
                    msg = detail or "npm install 已完成。"
                    QTimer.singleShot(0, lambda m=msg: self._finish_monaco_install(True, m))
                else:
                    msg = detail or f"npm install 失败，退出码 {result.returncode}"
                    QTimer.singleShot(0, lambda m=msg: self._finish_monaco_install(False, m))
            except FileNotFoundError as exc:
                QTimer.singleShot(0, lambda m=f"{exc}\n请先安装 Node.js / npm 并确保 npm 在 PATH 中。": self._finish_monaco_install(False, m))
            except Exception as exc:
                QTimer.singleShot(0, lambda m=str(exc): self._finish_monaco_install(False, m))

        threading.Thread(target=worker, daemon=True).start()

    def reload_from_config(self):
        cfg = self.cfg
        self._light = str(getattr(cfg, "theme", "dark")).lower() == "light"
        self._apply_page_theme()
        self.theme_combo.setCurrentText(getattr(cfg, "theme", "dark"))
        self.ui_font_combo.setCurrentText(getattr(cfg, "ui_font_family", "Segoe UI"))
        self.ui_font_size.setCurrentText(str(int(getattr(cfg, "ui_font_size", 12))))
        self.code_font_combo.setCurrentText(getattr(cfg, "code_font_family", "JetBrains Mono"))
        self.code_font_size.setCurrentText(str(int(getattr(cfg, "code_font_size", 13))))
        self.accent_value.setText(getattr(cfg, "accent_color", "#5ee9ff"))
        self.ide_wrap.setChecked(bool(getattr(cfg, "ide_word_wrap", True)))
        self.ide_minimap.setChecked(bool(getattr(cfg, "ide_minimap", False)))
        self.skills_enabled.setChecked(bool(getattr(cfg, "skills_enabled", True)))
        self.skill_debug_log.setChecked(bool(getattr(cfg, "skill_debug_log", False)))
        self.memory_injection_enabled.setChecked(bool(getattr(cfg, "memory_injection_enabled", True)))
        self.ide_font_size.setCurrentText(str(int(getattr(cfg, "ide_font_size", 13))))
        for combo in (self.theme_combo, self.ui_font_combo, self.ui_font_size, self.code_font_combo, self.code_font_size, self.ide_font_size):
            self._style_combo(combo)
        self._refresh_monaco_dependency_ui()
        self._refresh_models_list()

    def _model_entries(self) -> list[tuple[str, object]]:
        entries: list[tuple[str, object]] = [("kscc", name) for name in KSCC_MODELS]
        entries.extend(("openai", i) for i in range(len(self.cfg.openai_models)))
        return entries

    def _entry_for_index(self, idx: int) -> Optional[tuple[str, object]]:
        entries = self._model_entries()
        if 0 <= idx < len(entries):
            return entries[idx]
        return None

    def _refresh_models_list(self):
        self.oa_list.clear()
        for kind, ref in self._model_entries():
            if kind == "kscc":
                ctx, out = get_kscc_model_limits(self.cfg, str(ref))
                self.oa_list.addItem(f"Kscc · {ref}  ({ctx//1000}k / {out//1000}k)")
            else:
                m = self.cfg.openai_models[int(ref)]
                suffix = "（启用）" if m.enabled else "（禁用）"
                self.oa_list.addItem(f"{m.name}  {suffix}")
        entries = self._model_entries()
        if entries:
            idx = 0
            if self.cfg.backend == "kscc" and self.cfg.kscc_model in KSCC_MODELS:
                idx = KSCC_MODELS.index(self.cfg.kscc_model)
            elif self.cfg.openai_active:
                for pos, (kind, ref) in enumerate(entries):
                    if kind == "openai" and self.cfg.openai_models[int(ref)].name == self.cfg.openai_active:
                        idx = pos
                        break
            self.oa_list.setCurrentRow(idx)
            self._model_row_prev = idx
        else:
            self._model_row_prev = -1
            self._load_model_fields(-1)

    def _load_model_fields(self, idx: int):
        entry = self._entry_for_index(idx)
        if entry is None:
            self.m_enabled.setChecked(True)
            self.m_model.setText("")
            self.m_key.setText("")
            self.m_url.setText("")
            self.m_ctx_limit.setValue(0)
            self.m_out_limit.setValue(0)
            return
        kind, ref = entry
        is_kscc = kind == "kscc"
        if is_kscc:
            name = str(ref)
            ctx, out = get_kscc_model_limits(self.cfg, name)
            self.m_enabled.setChecked(True)
            self.m_model.setText(name)
            self.m_key.setText("")
            self.m_url.setText("")
            self.m_ctx_limit.setValue(ctx)
            self.m_out_limit.setValue(out)
        else:
            m = self.cfg.openai_models[int(ref)]
            ctx, out = get_effective_model_limits(m.model, "openai", m.context_limit, m.max_output_tokens)
            self.m_enabled.setChecked(bool(m.enabled))
            # UI label is "模型名称"; keep it aligned with list display name.
            self.m_model.setText(m.name or m.model)
            self.m_key.setText(m.api_key)
            self.m_url.setText(m.base_url)
            self.m_ctx_limit.setValue(ctx)
            self.m_out_limit.setValue(out)
        self.m_enabled.setEnabled(not is_kscc)
        self.m_model.setEnabled(not is_kscc)
        self.m_key.setEnabled(not is_kscc)
        self.m_url.setEnabled(not is_kscc)
        self.del_btn.setEnabled(not is_kscc)

    def _on_model_row_changed(self, idx: int):
        self._sync_current_model_fields(self._model_row_prev)
        self._model_row_prev = idx
        self._load_model_fields(idx)

    def _sync_current_model_fields(self, idx: Optional[int] = None):
        if idx is None:
            idx = self.oa_list.currentRow()
        entry = self._entry_for_index(idx)
        if entry is None:
            return
        kind, ref = entry
        if kind == "kscc":
            name = str(ref)
            default_ctx, default_out = get_effective_model_limits(name, "kscc")
            self.cfg.kscc_model_limits[name] = {
                "context_limit": 0 if self.m_ctx_limit.value() == default_ctx else self.m_ctx_limit.value(),
                "max_output_tokens": 0 if self.m_out_limit.value() == default_out else self.m_out_limit.value(),
            }
            return
        m = self.cfg.openai_models[int(ref)]
        m.enabled = self.m_enabled.isChecked()
        new_name = self.m_model.text().strip() or m.name or m.model
        old_name = m.name
        # In current UI, "模型名称" is both display name and model id.
        m.name = new_name
        m.model = new_name
        if self.cfg.openai_active == old_name:
            self.cfg.openai_active = new_name
        m.api_key = self.m_key.text().strip()
        m.base_url = (self.m_url.text().strip() or "https://api.openai.com/v1")
        default_ctx, default_out = get_effective_model_limits(m.model, "openai")
        m.context_limit = 0 if self.m_ctx_limit.value() == default_ctx else self.m_ctx_limit.value()
        m.max_output_tokens = 0 if self.m_out_limit.value() == default_out else self.m_out_limit.value()

    def _add_model(self):
        base = f"model-{len(self.cfg.openai_models)+1}"
        self.cfg.openai_models.append(OpenAIModel(name=base, model=base, api_key="", base_url="https://api.openai.com/v1", enabled=True))
        self._refresh_models_list()
        self.oa_list.setCurrentRow(len(self._model_entries())-1)

    def _remove_model(self):
        idx = self.oa_list.currentRow()
        entry = self._entry_for_index(idx)
        if entry and entry[0] == "openai":
            rm = self.cfg.openai_models.pop(int(entry[1]))
            if self.cfg.openai_active == rm.name:
                enabled = [m for m in self.cfg.openai_models if m.enabled]
                self.cfg.openai_active = enabled[0].name if enabled else (self.cfg.openai_models[0].name if self.cfg.openai_models else "")
        self._refresh_models_list()

    def _set_active_model(self):
        self._sync_current_model_fields()
        entry = self._entry_for_index(self.oa_list.currentRow())
        if not entry:
            return
        if entry[0] == "kscc":
            self.cfg.backend = "kscc"
            self.cfg.kscc_model = str(entry[1])
        else:
            self.cfg.backend = "openai"
            self.cfg.openai_active = self.cfg.openai_models[int(entry[1])].name
        self._refresh_models_list()

    def _sv(self):
        self._sync_current_model_fields()
        self.cfg.theme = self.theme_combo.currentText()
        self.cfg.ui_font_family = self.ui_font_combo.currentText()
        self.cfg.ui_font_size = int(self.ui_font_size.currentText() or 12)
        self.cfg.code_font_family = self.code_font_combo.currentText()
        self.cfg.code_font_size = int(self.code_font_size.currentText() or 13)
        self.cfg.accent_color = self.accent_value.text().strip() or "#5ee9ff"
        self.cfg.ide_word_wrap = self.ide_wrap.isChecked()
        self.cfg.ide_minimap = self.ide_minimap.isChecked()
        self.cfg.skills_enabled = self.skills_enabled.isChecked()
        self.cfg.skill_debug_log = self.skill_debug_log.isChecked()
        self.cfg.memory_injection_enabled = self.memory_injection_enabled.isChecked()
        self.cfg.ide_font_size = int(self.ide_font_size.currentText() or 13)
        if self.cfg.openai_active and not any(m.name == self.cfg.openai_active and m.enabled for m in self.cfg.openai_models):
            enabled = [m for m in self.cfg.openai_models if m.enabled]
            self.cfg.openai_active = enabled[0].name if enabled else ""
        save_config(self.cfg)
        self.saved.emit()

# ── MainWindow ──────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("KsccUI"); self.resize(1400,880); self.setMinimumSize(900,560)
        self.config=load_config(); self.store=SessionStore()
        # 产品要求：每次启动默认进入 Solo
        self.config.mode = "solo"
        self._agent:Optional[Agent]=None; self._worker:Optional[AgentWorker]=None
        self._running=False; self._cur_session:Optional[Session]=None
        self._session_group_collapsed: dict[str, bool] = {}
        self._pending_user_message_meta: Optional[dict] = None
        self._pending_skill_draft: Optional[dict] = None
        self._last_agent_prompt: str = ""
        ip=Path(__file__).parent/"icon.png"
        if ip.exists(): self.setWindowIcon(QIcon(str(ip)))
        app0 = QApplication.instance()
        if app0 is not None:
            app0.setProperty(
                "theme_mode",
                "light" if str(getattr(self.config, "theme", "dark")).lower() == "light" else "dark",
            )
        self._build_ui(); self._apply()
        self._on_mode_toggle(self.config.mode=="solo")

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
        # 全局换肤会触发布局/重绘，QToolBar 内纯 widget 的 setVisible 可能被冲掉，下一帧再同步
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
        """顶栏「文件树」开关：仅 IDE 模式显示。用 QWidgetAction 控制，避免 QToolBar 重布局把按钮又显示出来。"""
        ide = not self.mode_btn.isChecked()
        if hasattr(self, "_file_tree_toggle_action"):
            self._file_tree_toggle_action.setVisible(ide)
        elif hasattr(self, "_file_tree_toggle"):
            self._file_tree_toggle.setVisible(ide)

    def _sync_shell_chrome(self):
        """顶栏、侧栏底色、New session 等依赖主题的局部样式（stylesheet 全局样式之外的补充）。"""
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
                    f"QPushButton{{background:transparent;border:none;color:{dim};border-radius:8px;font-size:11px;padding:0 14px}}"
                    f"QPushButton:hover{{background:{hover_bg};color:{hi_text}}}"
                    f"QPushButton:checked{{background:{accent_sel};color:{accent}}}"
                    f"QPushButton:checked:hover{{background:{accent_sel};color:{accent}}}",
                    light,
                )
            )
            self.mode_btn.setToolTip("切换 Solo / IDE 模式")
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
                self.settings_btn.setIcon(_settings_icon(light, 18))
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
        c=QWidget(); c.setObjectName("centralGlass"); c.setStyleSheet("background: transparent;")
        self.setCentralWidget(c); r=QVBoxLayout(c); r.setContentsMargins(0,0,0,0); r.setSpacing(0)

        # Toolbar (dense copy + icon actions only; no separators)
        tb=QToolBar(); tb.setMovable(False)
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
        self.mode_btn=QPushButton("Solo" if self.config.mode=="solo" else "IDE")
        self.mode_btn.setCheckable(True); self.mode_btn.setChecked(self.config.mode=="solo")
        self.mode_btn.setFixedHeight(34)
        self.mode_btn.toggled.connect(self._on_mode_toggle); tb.addWidget(self.mode_btn)
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
        self.model_lbl=QLabel(f"Kscc/{self.config.kscc_model}" if self.config.backend=="kscc" else f"OpenAI/{p.model}")
        tb.addWidget(self.model_lbl)
        self._toolbar_spacer=QWidget(); self._toolbar_spacer.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred); tb.addWidget(self._toolbar_spacer)
        light = str(getattr(self.config, "theme", "dark")).lower() == "light"
        hover_bg = "rgba(0,0,0,0.06)" if light else "rgba(255,255,255,0.08)"
        self.ws_btn=QPushButton(); self.ws_btn.setFixedSize(34,34)
        self.ws_btn.setIcon(quark_icon("folder", 18))
        self.ws_btn.setIconSize(QSize(18,18))
        self.ws_btn.setStyleSheet(
            _with_tooltip_style(
                f"QPushButton{{background:transparent;border:none;border-radius:8px}}QPushButton:hover{{background:{hover_bg}}}",
                light,
            )
        )
        self.ws_btn.setToolTip("Change workspace")
        self.ws_btn.clicked.connect(self._chg_ws); tb.addWidget(self.ws_btn)
        self.ws_lbl=QLabel(self._short(self.config.workspace,30))
        tb.addWidget(self.ws_lbl)
        self.settings_btn=QPushButton("设置")
        self.settings_btn.setIcon(_settings_icon(light, 18))
        self.settings_btn.setIconSize(QSize(18,18))
        self.settings_btn.setMinimumHeight(34)
        self.settings_btn.clicked.connect(self._settings); tb.addWidget(self.settings_btn)
        self.skills_btn=QPushButton("Skills")
        self.skills_btn.setIcon(quark_icon("bullet_list", 18))
        self.skills_btn.setIconSize(QSize(18,18))
        self.skills_btn.setMinimumHeight(34)
        self.skills_btn.setToolTip("管理本地 Skills")
        self.skills_btn.clicked.connect(self._open_skills_manager); tb.addWidget(self.skills_btn)
        r.addWidget(tb)

        # Splitter：IDE 下为 [文件树 | 编辑器 | 会话侧栏+聊天]；Solo 下隐藏文件树，仅 [会话侧栏+聊天]
        self.splitter=QSplitter(Qt.Orientation.Horizontal); self.splitter.setHandleWidth(2)

        self.file_sidebar=QWidget()
        self.file_sidebar.setMinimumWidth(180)
        self.file_sidebar.setMaximumWidth(320)
        fs_l=QVBoxLayout(self.file_sidebar); fs_l.setContentsMargins(0,0,0,0); fs_l.setSpacing(0)
        self.file_tree=FileTree(self.config.workspace)
        self.file_tree.file_selected.connect(self._on_file)
        fs_l.addWidget(self.file_tree,1)
        self.splitter.addWidget(self.file_sidebar)

        self.editor_tabs = EditorTabHost()
        self.editor = self.editor_tabs.editor
        self.editor.ready.connect(self._apply_ide_editor_settings)
        self.editor.ask_selection.connect(self._on_sel)
        self.editor.ask_file.connect(lambda f: None)
        self.editor_tabs.save_requested.connect(self._on_editor_save_requested)
        QTimer.singleShot(2000, lambda: self.editor._js("window._enableCompletions()"))
        self.splitter.addWidget(self.editor_tabs)

        # 会话列表：挂在会话/聊天区域左侧，随聊天列一起缩放
        self.session_panel=QWidget()
        self.session_panel.setMinimumWidth(160)
        self.session_panel.setMaximumWidth(300)
        sl=QVBoxLayout(self.session_panel); sl.setContentsMargins(0,0,0,0); sl.setSpacing(2)
        self._new_session_btn = QPushButton("New session")
        self._new_session_btn.clicked.connect(self._new_session); sl.addWidget(self._new_session_btn)
        self.sess_scroll=QScrollArea(); self.sess_scroll.setWidgetResizable(True)
        self.sess_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sess_container=QWidget(); self.sess_container.setStyleSheet("background:transparent")
        self.sess_layout=QVBoxLayout(self.sess_container)
        self.sess_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.sess_layout.setSpacing(2); self.sess_layout.setContentsMargins(4,2,4,2)
        self.sess_scroll.setWidget(self.sess_container)
        sl.addWidget(self.sess_scroll,1)

        self.chat=ChatPanel(); self.chat.send_message.connect(self._on_send)
        self.chat.add_skill_requested.connect(self._open_skill_save_dialog)
        self.chat.stop_btn.clicked.connect(self._on_stop)
        self._init_chat_selectors()

        self.session_chat_splitter=QSplitter(Qt.Orientation.Horizontal)
        self.session_chat_splitter.setHandleWidth(2)
        self.session_chat_splitter.addWidget(self.session_panel)
        self.session_chat_splitter.addWidget(self.chat)
        # 默认 true：拖到最窄会把一侧收成 0，分割条贴边后很难再拖开
        self.session_chat_splitter.setCollapsible(0, False)
        self.session_chat_splitter.setCollapsible(1, False)
        self.session_chat_splitter.setStretchFactor(0, 0)
        self.session_chat_splitter.setStretchFactor(1, 1)
        # 会话列表相对窄，优先把宽度给聊天区（具体占比随外层列宽变化）
        self.session_chat_splitter.setSizes([200, 720])

        self.splitter.addWidget(self.session_chat_splitter)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 1)
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.splitter)
        self.settings_page = SettingsPage(self.config, self)
        self.settings_page.saved.connect(self._on_settings_saved)
        self.settings_page.cancelled.connect(self._show_main_page)
        self.content_stack.addWidget(self.settings_page)
        self.content_stack.setCurrentIndex(0)
        self._sync_settings_toolbar_button()
        r.addWidget(self.content_stack,1)
        self._load_layout_panel_settings()
        self._sync_file_tree_toolbar_visibility()
        init_w = max(self.width(), 1280)
        if not self.mode_btn.isChecked():
            self.splitter.setSizes(self._compute_outer_split_sizes(init_w))
        QTimer.singleShot(0, self._layout_after_first_show)
        QShortcut(QKeySequence("Ctrl+B"), self, self._shortcut_toggle_file_tree)
        QShortcut(QKeySequence("Ctrl+Shift+L"), self, self._shortcut_toggle_session)

        # Status bar
        self.sbar=QStatusBar(); self.slbl=QLabel("Ready"); self.tlbl=QLabel("")
        self.sbar.addWidget(self.slbl,1); self.sbar.addPermanentWidget(self.tlbl)
        self.setStatusBar(self.sbar); r.addWidget(self.sbar)

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

    def _compute_outer_split_sizes(self, total: int) -> list[int]:
        """IDE / Solo 下列宽；尊重文件树显隐与 Solo 模式。"""
        total = max(int(total), 680)
        if self.mode_btn.isChecked():
            return [0, 0, total]
        if not self.file_sidebar.isVisible():
            chat_col = max(520, int(round(total * 0.38)))
            ed = total - chat_col
            if ed < 260:
                chat_col = total - 260
                ed = 260
            chat_col = max(300, chat_col)
            ed = total - chat_col
            return [0, max(200, ed), chat_col]
        fw = int(self._layout_read("file_tree_width", 200, int))
        fw = max(180, min(320, fw))
        chat_col = max(520, int(round(total * 0.38)))
        ed = total - fw - chat_col
        if ed < 260:
            chat_col = total - fw - 260
            ed = 260
        chat_col = max(300, chat_col)
        ed = total - fw - chat_col
        return [fw, max(200, ed), chat_col]

    def _ide_split_sizes(self, total: int) -> list[int]:
        """兼容旧调用：等价于 _compute_outer_split_sizes。"""
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
        if self.mode_btn.isChecked():
            return
        total = max(self.splitter.width(), 680)
        self.splitter.setSizes(self._compute_outer_split_sizes(total))
        self._redistribute_session_inner()

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
        w=max(self.splitter.width(), 600)
        self._sync_file_tree_toolbar_visibility()
        if checked:
            self.mode_btn.setText("Solo")
            self.file_sidebar.hide()
            self.editor_tabs.hide()
            self.splitter.setSizes([0, 0, w])
        else:
            self.mode_btn.setText("IDE")
            self.editor_tabs.show()
            if self._file_tree_toggle.isChecked():
                self.file_sidebar.show()
            else:
                self.file_sidebar.hide()
            self.splitter.setSizes(self._compute_outer_split_sizes(w))
            QTimer.singleShot(0, self._redistribute_session_inner)
        self.config.mode="solo" if checked else "ide"
        QTimer.singleShot(0, self._sync_file_tree_toolbar_visibility)

    # ── File tree ──────────────────────────────────────────
    def _on_file(self, path):
        self.editor_tabs.open_file(path)
        self.slbl.setText(f"Opened: {Path(path).name}")
        # 更新会话区当前文件提示
        self.chat.set_kscc_status(f"Current file: {Path(path).name}")
        QTimer.singleShot(8000, lambda: self.chat.set_kscc_status(""))
        try:
            content = Path(path).read_text("utf-8", errors="replace")[:500]
            self.chat.add_message("tool", f"File · {Path(path).name}\n```\n{content}\n```")
        except Exception: pass

    def _init_chat_selectors(self):
        # 先设置模型列表，再切换 backend（避免触发空列表）
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
        p=QFileDialog.getExistingDirectory(self,"Select Workspace",base)
        if p:
            ap = os.path.abspath(p)
            # Apply to current session (not a global setting UI)
            if self._cur_session is not None:
                self._cur_session.workspace = ap
                self.store.save(self._cur_session)
                self._refresh_sessions()
            # Keep config.workspace as default for new sessions only
            self.config.workspace = ap
            save_config(self.config)
            self.ws_lbl.setText(self._short(ap,30))
            self.file_tree.set_workspace(ap)

    @staticmethod
    def _short(t,n): return t if len(t)<=n else "..."+t[-(n-3):]

    # ── Selection ──────────────────────────────────────────
    def _on_sel(self, fp, txt):
        p=txt[:300]
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
        title_col = "#000000" if light else "rgba(255,255,255,0.9)"
        meta_col = "#333333" if light else C_DIM
        ws_head = "#000000" if light else C_TEAL
        while self.sess_layout.count():
            w=self.sess_layout.takeAt(0)
            if w.widget(): w.widget().deleteLater()

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
            ws_name = Path(ws).name if ws not in ("", "(No workspace)") else "No workspace"
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
                sid=s["id"]; title=s.get("title","Untitled"); cnt=s.get("message_count",0)
                selected = bool(self._cur_session and self._cur_session.id == sid)
                raw_updated = str(s.get("updated", "") or "")
                short_updated = raw_updated[:16]
                if raw_updated:
                    try:
                        short_updated = datetime.fromisoformat(raw_updated.replace("Z", "+00:00")).strftime("%m-%d %H:%M")
                    except Exception:
                        m = _re.match(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}:\d{2})", raw_updated)
                        if m:
                            short_updated = f"{m.group(2)}-{m.group(3)} {m.group(4)}"
                card=QFrame()
                card_margin_x = 6
                card_pad = 2
                row_left = 5
                row_right = 8
                row_gap = 3
                bar_width = 3
                card.setStyleSheet(
                    f"QFrame{{background:{card_sel if selected else card_bg};border:none;border-radius:10px;margin:3px {card_margin_x}px;padding:{card_pad}px}}"
                    f"QFrame:hover{{background:{card_sel if selected else card_hover}}}"
                    f"QLabel{{background:transparent}}"
                )
                card.setCursor(Qt.CursorShape.PointingHandCursor)
                card.setMinimumWidth(0)
                card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                card.mousePressEvent=lambda e,si=sid: self._on_sess_click(e,si)
                card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                card.setProperty("sid",sid)
                card.customContextMenuRequested.connect(lambda pos: self._sess_menu(pos))
                row=QHBoxLayout(card); row.setContentsMargins(row_left,8,row_right,8); row.setSpacing(row_gap)
                bar = QFrame()
                if selected:
                    is_running = bool(
                        self._running
                        and self._cur_session
                        and self._cur_session.id == sid
                    )
                    bar = SessionActivityBar(indicator, running=is_running)
                else:
                    bar = QFrame()
                    bar.setFixedWidth(bar_width)
                    bar.setMinimumHeight(30)
                    bar.setStyleSheet("QFrame{background:transparent;border:none;border-radius:2px;}")
                row.addWidget(bar)
                body = QWidget()
                body.setMinimumWidth(0)
                body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                hl=QVBoxLayout(body); hl.setContentsMargins(0,0,0,0); hl.setSpacing(2)
                title_font = QFont("Segoe UI", 12, QFont.Weight.DemiBold if selected else QFont.Weight.Medium)
                tlab=ElidedLabel(str(title or "Untitled")); tlab.setStyleSheet(f"color:{title_col};font-size:12px;font-weight:{700 if selected else 500};background:transparent")
                tlab.setFont(title_font)
                tlab.set_full_text(str(title or "Untitled"))
                tlab.setToolTip(str(title or "Untitled"))
                hl.addWidget(tlab)
                tl=QLabel(f"msgs: {cnt}  {short_updated}")
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
        target_ws = workspace_key
        if target_ws in ("", "(No workspace)"):
            target_ws = self.config.workspace
        if target_ws and os.path.isdir(target_ws):
            self.config.workspace = os.path.abspath(target_ws)
            self.ws_lbl.setText(self._short(self.config.workspace, 30))
            self.file_tree.set_workspace(self.config.workspace)
        mode = self.config.mode
        session = self.store.create(title="", workspace=target_ws or self.config.workspace, mode=mode)
        self._load_session(session.id)

    def _message_display_text(self, msg: dict) -> str:
        if msg.get("display_text") is not None:
            return str(msg.get("display_text") or "")
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    return str(part.get("text", ""))
        return str(content or "")

    def _on_sess_click(self, event, sid):
        if event.button()!=Qt.MouseButton.LeftButton: return
        self._load_session(sid)

    def _stop_worker_for_navigation(self):
        """切换/新建会话前停止当前任务，避免旧信号继续写入已销毁控件。"""
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(2000)
            if self._worker.isRunning():
                self._worker.terminate()
                self._worker.wait(2000)
        self._running = False
        self._ctx_bind_sid = None
        self._pending_user_message_meta = None
        self.chat.end_stream()
        self._dispose_agent_runtime()

    def _load_session(self, sid):
        self._stop_worker_for_navigation()
        session=self.store.load(sid)
        if not session: return
        self._cur_session=session
        self._agent=None
        self._worker=None; self._running=False
        self._pending_skill_draft = None
        self.chat.show_save_skill_prompt(False)
        self._refresh_sessions()
        self.editor_tabs.clear_all_tabs()
        while self.chat.msg_layout.count():
            item = self.chat.msg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.chat.msg_layout.addStretch()
        for m in session.messages:
            r=m.get("role",""); c=m.get("content","")
            if r=="user":
                self.chat.add_message("user", self._message_display_text(m), attachments=m.get("attachments") or [])
            elif r=="assistant":
                if c: self.chat.add_message("assistant",str(c), model_label=str(m.get("model_label", "") or ""))
                for tc in m.get("tool_calls",[]):
                    fn=tc.get("function",{}); n=fn.get("name","")
                    if n=="edit_file":
                        try: a=json.loads(fn.get("arguments","{}"))
                        except: a={}
                        if a.get("path"):
                            self.chat.add_diff(a["path"], a.get("old_string",""), a.get("new_string",""))
            elif r=="tool": pass
        # Restore per-session workspace + model selection
        if session.workspace and os.path.isdir(session.workspace):
            self.config.workspace = os.path.abspath(session.workspace)
            self.ws_lbl.setText(self._short(self.config.workspace,30))
            self.file_tree.set_workspace(self.config.workspace)
        if session.backend:
            self.config.backend = str(session.backend)
        if session.backend == "kscc" and session.model:
            self.config.kscc_model = str(session.model)
        if session.backend == "openai" and session.model:
            self.config.openai_active = str(session.model)
        self._init_chat_selectors()
        self.slbl.setText(f"Session: {session.title[:36]}" if session.title.strip() else "Session")
        self._ctx_bind_sid = None
        self._pending_user_message_meta = None
        snap = getattr(session, "context_info", None)
        self._ctx_apply_snapshot(snap if isinstance(snap, dict) else None)

    def _sess_menu(self, pos):
        snd=self.sender()
        if not snd: return
        sid=snd.property("sid")
        if not sid: return
        m=QMenu(self); da=m.addAction("Delete")
        if m.exec(snd.mapToGlobal(pos))==da:
            if self._cur_session and self._cur_session.id==sid: self._new_session()
            self.store.delete(sid); self._refresh_sessions()

    def _new_session(self):
        self._stop_worker_for_navigation()
        self._cur_session=None; self._agent=None; self._worker=None; self._running=False
        self._pending_skill_draft = None
        self.chat.show_save_skill_prompt(False)
        self.editor_tabs.clear_all_tabs()
        while self.chat.msg_layout.count():
            item = self.chat.msg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.chat.msg_layout.addStretch()
        self._refresh_sessions(); self.slbl.setText("New session")
        self._ctx_bind_sid = None
        self._pending_user_message_meta = None
        self._ctx_clear()

    # ── Send / Agent ───────────────────────────────────────
    def _on_send(self, text, attachments=None):
        if self._running: return
        self._start_agent(text, attachments or [])

    def _start_agent(self, prompt, attachments_meta=None):
        self._running=True; mode=self.config.mode
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
                    "[Backend switch context anchor]\n"
                    "You are continuing the same user session after switching model backend.\n"
                    "Use this anchor to preserve continuity, and prioritize the latest user request if conflicts exist.\n"
                    f"{anchor}"
                )

        if backend == "Kscc":
            self.config.backend = "kscc"; self.config.kscc_model = model
            self.editor._js("window._disableCompletions()")
        else:
            self.config.backend = "openai"; self.config.openai_active = model
            self.editor._js("window._enableCompletions()")

        self.model_lbl.setText(f"{backend}/{model}")
        self._refresh_sessions()

        if self._cur_session is None:
            self._cur_session=self.store.create(title="",workspace=self.config.workspace,mode=mode)
        # Persist per-session selection
        self._cur_session.workspace = os.path.abspath(self.config.workspace)
        self._cur_session.backend = "kscc" if backend == "Kscc" else "openai"
        self._cur_session.model = model
        self.store.save(self._cur_session)
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
                f"Model switched: {prev_backend}/{prev_model or '-'} -> {next_backend_key}/{model or '-'}",
            )

        self.chat.send_btn.setEnabled(False); self.chat.start_stream(model_label=f"{backend}/{model}"); self.slbl.setText("Running...")
        self._dispose_agent_runtime()
        try:
            rmsgs=self._cur_session.messages if self._cur_session.messages else None
            agent=Agent(config=self.config,mode=mode,resume_messages=rmsgs)
        except Exception as e:
            self.chat.add_error(f"Failed: {e}"); self.chat.send_btn.setEnabled(True)
            self._running=False; return
        self._agent=agent
        self._ctx_bind_sid = self._cur_session.id
        w=AgentWorker(agent,agent_prompt,[a.get("path","") for a in attachments_meta if a.get("path")]); self._worker=w
        w.text_delta.connect(self.chat.append_stream)
        w.tool_call.connect(self.chat.add_tool)
        w.tool_result.connect(self.chat.add_result)
        w.diff_preview.connect(self.chat.add_diff)
        w.kscc_status.connect(self.chat.set_kscc_status)
        w.confirm_request.connect(self._on_confirm)
        w.context_info.connect(self._on_ctx)
        w.skill_info.connect(self._on_skill_info)
        w.skill_draft.connect(self._on_skill_draft)
        w.done.connect(self._on_done)
        w.error.connect(self._on_err)
        w.file_modified.connect(self._on_fmod)
        w.finished.connect(self._on_fin)
        w.start()
        if switched_runtime:
            self.slbl.setText(
                f"Backend switched: {prev_backend}/{prev_model or '-'} -> {next_backend_key}/{model or '-'} (context continuity: best-effort)"
            )

    def _build_backend_switch_anchor(self) -> str:
        """
        Build a compact anchor from recent session turns to stabilize
        continuity when switching between kscc/openai backends.
        """
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
        return "Recent turns:\n" + "\n".join(rows[-6:])

    def _on_ctx(self, sj):
        try:
            s = json.loads(sj)
        except (json.JSONDecodeError, TypeError):
            return
        if self._cur_session is None or getattr(self, "_ctx_bind_sid", None) != self._cur_session.id:
            return
        self._ctx_set_from_json(sj)
        self._cur_session.context_info = s

    def _on_confirm(self, path, old, new):
        def accept(): self._agent.approve()
        def reject(): self._agent.reject()
        self.chat.add_review_card(path, old, new, accept, reject)

    def _on_done(self, text, turns, cj):
        self._ctx_bind_sid = None
        self.chat.end_stream(); self.chat.set_kscc_status("")
        self.slbl.setText(f"Done - {turns} turns")
        try:
            c=json.loads(cj)
            self.tlbl.setText(f"in:{_fmt_k(c.get('total_input'))} out:{_fmt_k(c.get('total_output'))}")
        except: pass
        self._save()
        if bool(getattr(self.config, "memory_injection_enabled", True)):
            try:
                memory_store.append_archive(
                    session_id=self._cur_session.id if self._cur_session else "",
                    title=self._cur_session.title if self._cur_session else "",
                    user_prompt=self._last_agent_prompt,
                    summary=text,
                    turns=turns,
                    workspace=self.config.workspace,
                )
            except Exception:
                pass

    def _on_stop(self):
        if self._worker and self._worker.isRunning():
            self._worker.stop(); self._worker.wait(2000)
            if self._worker.isRunning():
                self._worker.terminate(); self._worker.wait(2000)
        self._ctx_bind_sid = None
        self._pending_user_message_meta = None
        self._running=False; self.chat.end_stream()
        self._dispose_agent_runtime()
        self._refresh_sessions()
        self.slbl.setText("Stopped")

    def _on_err(self, t):
        self._ctx_bind_sid = None
        self._pending_user_message_meta = None
        self.chat.add_error(t); self.chat.end_stream(); self.slbl.setText("Error"); self._running=False
        self._dispose_agent_runtime()
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
        self.chat.show_save_skill_prompt(True, "Save Skill")
        self.slbl.setText("Skill suggestion ready")

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

    def _on_fin(self):
        self._ctx_bind_sid = None
        self.chat.send_btn.setEnabled(True); self._running=False; self._worker=None
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
        ws=self.config.workspace
        fp=os.path.join(ws,path) if not os.path.isabs(path) else path
        if os.path.exists(fp): self.editor_tabs.open_file(fp)

    def _save(self):
        if not self._cur_session or not self._agent or not self._agent.messages: return
        self._cur_session.messages=list(self._agent.messages)
        # Persist model label for assistant bubbles so history can display which model answered.
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
        self._cur_session.mode=self.config.mode
        self._cur_session.workspace=os.path.abspath(self.config.workspace)
        # keep per-session backend/model updated (in case user switched before saving)
        try:
            bk = self.chat.current_backend_label()
            md = self.chat.current_model_name()
            self._cur_session.backend = "kscc" if bk == "Kscc" else "openai"
            self._cur_session.model = md
        except Exception:
            pass
        if not self._cur_session.title or self._cur_session.title in ("New Session","New Chat"):
            self._cur_session.title=self.store.auto_title(self._agent.messages)
        self.store.save(self._cur_session); self._refresh_sessions()

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

    def _sync_settings_toolbar_button(self):
        if not hasattr(self, "settings_btn") or not hasattr(self, "content_stack"):
            return
        in_settings = self.content_stack.currentIndex() == 1
        light = str(getattr(self.config, "theme", "dark")).lower() == "light"
        if in_settings:
            self.settings_btn.setText("返回")
            self.settings_btn.setIcon(quark_icon("arrow_left", 18))
            self.settings_btn.setToolTip("返回")
        else:
            self.settings_btn.setText("设置")
            self.settings_btn.setIcon(_settings_icon(light, 18))
            self.settings_btn.setToolTip("设置")
        self._sync_shell_chrome()

    def _on_settings_saved(self):
        p=get_active_provider(self.config)
        self.model_lbl.setText(f"OpenAI/{p.model}" if self.config.backend=="openai" else f"Kscc/{self.config.kscc_model}")
        self.file_tree.set_workspace(self.config.workspace)
        self._init_chat_selectors()
        self._apply()
        self._show_main_page()

    def _settings(self):
        if hasattr(self, "content_stack") and self.content_stack.currentIndex() == 1:
            self._show_main_page()
        else:
            self._show_settings_page()

    def _open_skills_manager(self):
        dlg = SkillsManagerDialog(self)
        dlg.exec()
        self.slbl.setText("Skills updated")

# ── Entry ───────────────────────────────────────────────────
def main():
    if sys.platform=="win32":
        try: import ctypes; ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("kscc.ui")
        except: pass
    app=QApplication(sys.argv)
    app.setOrganizationName("Kscc")
    app.setApplicationName("KsccUI")
    icon_path = Path(__file__).parent / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    fp=Path(__file__).parent/"fonts"
    if fp.exists():
        for f in fp.glob("*.ttf"): QFontDatabase.addApplicationFont(str(f))
    w=MainWindow(); w.show(); sys.exit(app.exec())

if __name__=="__main__": main()
