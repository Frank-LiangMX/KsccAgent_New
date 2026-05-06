"""
Editor - Monaco 编辑器与标签页管理
从 app.py 提取：MonacoPage, MonacoEditor, EditorTabHost
"""

import json
import os
import threading
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTabBar, QToolButton,
    QApplication,
)
from PyQt6.QtCore import Qt, QUrl, QTimer, QSize, pyqtSignal
from PyQt6.QtGui import QFont, QPixmap

from ui_common import (
    HAS_WEBENGINE, _is_light_theme, _settings_icon,
)
from theme import C_DIM, C_TEXT

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
except ImportError:
    pass


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
                            self.js_message.emit(data)
                            return
                        if isinstance(data, list):
                            for m in data:
                                if isinstance(m, dict) and 't' in m:
                                    self.js_message.emit(m)
                            return
                    except (json.JSONDecodeError, ValueError):
                        pass
            super().javaScriptConsoleMessage(level, message, line, src)


# ── MonacoEditor ────────────────────────────────────────────
class MonacoEditor(QWidget):
    content_changed = pyqtSignal(str)
    save_requested = pyqtSignal(str, str)
    ask_selection = pyqtSignal(str, str)
    ask_file = pyqtSignal(str)
    file_opened = pyqtSignal(str, str)
    ready = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path = ""
        self._monaco_ready = False
        self._pending_set_file: tuple[str, str] | None = None
        self.setStyleSheet("background: transparent;")
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        if not HAS_WEBENGINE:
            l.addWidget(QLabel("PyQt6-WebEngine required"))
            self.webview = None
            return
        self.page = MonacoPage(self)
        self.page.js_message.connect(self._on_msg)
        self.webview = QWebEngineView()
        self.webview.setPage(self.page)
        s = self.page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        hp = Path(__file__).parent / "monaco.html"
        if hp.exists():
            self.webview.load(QUrl.fromLocalFile(str(hp)))
        l.addWidget(self.webview)

    def _on_msg(self, m):
        t, d = m.get('t', ''), m.get('d', {})
        if t == 'content_changed':
            self.content_changed.emit(str(d))
        elif t == 'save_requested':
            content = str(d)
            self.content_changed.emit(content)
            self.save_requested.emit(self._current_path, content)
        elif t == 'ask_selection':
            self.ask_selection.emit(str(d.get('file', '')), str(d.get('text', '')))
        elif t == 'ask_file':
            self.ask_file.emit(str(d.get('file', '')))
        elif t == 'file_opened':
            self._current_path = str(d.get('path', ''))
            self.file_opened.emit(self._current_path, str(d.get('language', '')))
        elif t == 'ready':
            self._monaco_ready = True
            if self._pending_set_file is not None:
                pth, ctn = self._pending_set_file
                self._pending_set_file = None
                self._js(f"window.setFile({json.dumps(pth)},{json.dumps(ctn)})")
            self.ready.emit()
        elif t == 'completion_request':
            self._on_completion(d)
        elif t == 'error':
            print(f"[Monaco] {d}")

    def set_file(self, path, content=""):
        self._current_path = str(path or "")
        if not content:
            try:
                content = Path(path).read_text("utf-8", errors="replace")
            except Exception:
                content = ""
        pth = str(path or "")
        ctn = str(content or "")
        if not self._monaco_ready:
            # Defer until Monaco emits ready; prevents "window.setFile is not a function".
            self._pending_set_file = (pth, ctn)
            return
        self._js(f"window.setFile({json.dumps(pth)},{json.dumps(ctn)})")

    def _js(self, code, cb=None):
        if self.webview:
            if cb:
                self.webview.page().runJavaScript(code, cb)
            else:
                self.webview.page().runJavaScript(code)

    def _on_completion(self, d):
        from config import load_config
        cfg = load_config()
        if cfg.backend == "kscc":
            return
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
                self._js(f"window._resolveCompletion({req_id}, '')")
                return
            prov = get_active_provider(cfg)
            if not prov.api_key:
                self._js(f"window._resolveCompletion({req_id}, '')")
                return
            prompt = f"Complete this {language} code. Return ONLY completion, no explanation:\n\n```{language}\n{context}\n```"
            r = httpx.post(f"{prov.base_url}/chat/completions", json={
                "model": prov.model, "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 128, "temperature": 0.1, "stream": False,
            }, headers={"Authorization": f"Bearer {prov.api_key}"}, timeout=6)
            if r.status_code == 200:
                text = r.json()["choices"][0]["message"]["content"].strip()
                text = text.replace("```", "").strip()
                self._js(f"window._resolveCompletion({req_id}, {json.dumps(text)})")
            else:
                self._js(f"window._resolveCompletion({req_id}, '')")
        except Exception:
            self._js(f"window._resolveCompletion({req_id}, '')")


# ── EditorTabHost ───────────────────────────────────────────
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
