"""
UI Common - 共享的 UI 工具函数和常量
从 app.py 提取，供多个 UI 模块共用。
"""

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QSize, QPointF
from PyQt6.QtGui import QColor, QIcon, QPen, QPixmap, QPainter

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except ImportError:
    HAS_WEBENGINE = False

# ── Constants ───────────────────────────────────────────────
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


# ── Helper Functions ────────────────────────────────────────

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
    from theme import quark_icon
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


def _suppress_tk_root_window() -> None:
    """Hide transient tkinter root windows created by third-party dependencies."""
    try:
        import tkinter as tk  # type: ignore
    except Exception:
        return
    if getattr(tk, "_kscc_root_suppressed", False):
        return
    orig_tk = getattr(tk, "Tk", None)
    if orig_tk is None:
        return
    orig_top = getattr(tk, "Toplevel", None)

    class _HiddenTk(orig_tk):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            try:
                self.withdraw()
            except Exception:
                pass

    if orig_top is not None:
        class _HiddenTop(orig_top):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                try:
                    self.withdraw()
                except Exception:
                    pass

        tk.Toplevel = _HiddenTop

    tk.Tk = _HiddenTk
    try:
        tk.NoDefaultRoot()
    except Exception:
        pass
    try:
        root = getattr(tk, "_default_root", None)
        if root is not None:
            root.withdraw()
    except Exception:
        pass
    tk._kscc_root_suppressed = True
