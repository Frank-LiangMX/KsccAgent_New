"""
Chat Widgets - 聊天气泡、输入框、附件等 UI 组件
从 app.py 提取：BubbleTextEdit, ImagePreviewDialog, PreviewImageLabel,
SessionActivityBar, ElidedLabel, ComposerAttachmentChip, FileAttachmentPill,
ChatBubble, ChatInputEdit, NoWheelComboBox, NoWheelFontComboBox, NoWheelSpinBox,
WorkspaceGroupHeader, ContextRingWidget
"""

import html as _html
import math
import os
import re
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton, QPlainTextEdit,
    QScrollArea, QFrame, QDialog, QSizePolicy, QComboBox, QFontComboBox,
    QSpinBox, QToolButton, QApplication, QToolTip, QMenu,
)
from PyQt6.QtCore import Qt, QTimer, QSize, QPoint, QPointF, QRect, QRectF, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter,
    QPalette, QPen, QPixmap, QTextCursor, QTextOption, QIcon, QCursor, QKeySequence,
)

from ui_common import (
    _is_light_theme, _tooltip_css, _with_tooltip_style, _fmt_k,
    _make_plus_icon, _settings_icon, _ensure_attachments_dir,
    _is_image_path, _attachment_meta, _CODE_FONT_STACK, HAS_WEBENGINE,
)
from config import load_config
from theme import (
    md_chat_to_html, md_code_to_html, split_markdown_blocks, quark_icon,
    polish_menu,
    C_ACCENT, C_ACCENT_LIGHT, C_BORDER, C_DIM, C_PANEL, C_PANEL_HI,
    C_RED, C_TEAL, C_TEAL_LIGHT, C_TEXT, C_YELLOW,
)


# ── BubbleTextEdit ──────────────────────────────────────────
class BubbleTextEdit(QTextEdit):
    link_clicked = pyqtSignal(str)

    def __init__(self, scroll_area: Optional[QScrollArea] = None, parent=None):
        super().__init__(parent)
        self._scroll_area = scroll_area
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        if hasattr(self, "setCursorWidth"):
            try:
                self.setCursorWidth(0)
            except Exception:
                pass
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.viewport().setMouseTracking(True)
        self._selecting = False
        self._select_anchor = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            pos = event.position().toPoint()
            if self.anchorAt(pos):
                super().mousePressEvent(event)
                return
            self._selecting = True
            self._select_anchor = self.cursorForPosition(pos)
            cursor = self.textCursor()
            cursor.setPosition(self._select_anchor.position())
            self.setTextCursor(cursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            if self.anchorAt(pos):
                super().mouseDoubleClickEvent(event)
                return
            cursor = self.cursorForPosition(pos)
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
            self.setTextCursor(cursor)
            self._selecting = False
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._selecting and self._select_anchor is not None:
            cursor = self.textCursor()
            end_cursor = self.cursorForPosition(pos)
            cursor.setPosition(self._select_anchor.position())
            cursor.setPosition(end_cursor.position(), QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(cursor)
            event.accept()
            return
        if self.anchorAt(pos):
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._selecting:
            self._selecting = False
            event.accept()
            return
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

    def contextMenuEvent(self, event):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        menu = self.createStandardContextMenu()
        light = _is_light_theme()
        polish_menu(menu, "light" if light else "dark", font_size=max(10, self.font().pointSize()))
        menu.exec(event.globalPos())

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            cursor = self.textCursor()
            if cursor.hasSelection():
                QApplication.clipboard().setText(cursor.selectedText().replace("\u2029", "\n"))
                event.accept()
                return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        if self._scroll_area is not None:
            QApplication.sendEvent(self._scroll_area.viewport(), event)
            return
        super().wheelEvent(event)


# ── ImagePreviewDialog ──────────────────────────────────────
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


# ── PreviewImageLabel ───────────────────────────────────────
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


# ── SessionActivityBar ──────────────────────────────────────
class SessionActivityBar(QWidget):
    """Left indicator bar for session cards with running shimmer."""

    def __init__(self, color: str, running: bool = False, state: str = "idle", parent=None):
        super().__init__(parent)
        self.setFixedWidth(5)
        self.setMinimumHeight(30)
        self._color = QColor(color)
        self._running = False
        self._phase = 0.0
        self._direction = 1.0
        self._state = str(state or "idle")
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._on_tick)
        self.set_running(running)

    def _on_tick(self):
        step = 0.03 * self._direction
        nxt = self._phase + step
        if nxt >= 1.0:
            self._phase = 1.0
            self._direction = -1.0
        elif nxt <= 0.0:
            self._phase = 0.0
            self._direction = 1.0
        else:
            self._phase = nxt
        self.update()

    def set_running(self, running: bool):
        self._running = bool(running)
        if self._running:
            self._state = "running"
        if self._running:
            if not self._timer.isActive():
                self._timer.start()
        else:
            if self._timer.isActive():
                self._timer.stop()
            self._phase = 0.0
            self._direction = 1.0
        self.update()

    def set_state(self, state: str):
        st = str(state or "idle")
        if st not in ("idle", "running", "success", "error"):
            st = "idle"
        self._state = st
        self.set_running(st == "running")

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 0, -1, 0)
        base = QColor(self._color)
        if not self._running:
            if self._state == "success":
                ok = QColor("#22c55e")
                ok.setAlpha(240)
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(ok); p.drawRoundedRect(QRectF(rect), 2.0, 2.0)
                return
            if self._state == "error":
                err = QColor("#ef4444")
                err.setAlpha(240)
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(err); p.drawRoundedRect(QRectF(rect), 2.0, 2.0)
                return
            base.setAlpha(220)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(base); p.drawRoundedRect(QRectF(rect), 2.0, 2.0)
            return
        dim = QColor(self._color)
        dim.setAlpha(90)
        bright = QColor(self._color)
        bright.setAlpha(240)
        sheen = QColor("#ffffff")
        sheen.setAlpha(175)
        center = self._phase
        a = max(0.0, center - 0.22)
        c = min(1.0, center + 0.22)
        g = QLinearGradient(0, 0, 0, rect.height())
        g.setColorAt(0.0, dim)
        g.setColorAt(a, dim)
        g.setColorAt(center, bright)
        if c > center:
            g.setColorAt(c, dim)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(g); p.drawRoundedRect(QRectF(rect), 2.0, 2.0)

        # Multi-layer glow: outer → mid → core, layered for a natural
        # bloom / self-illuminating effect without visual noise.
        for spread, alpha, span, radius in [
            (4, 40, 0.48, 6.0),   # outer halo – widest, softest
            (2, 80, 0.32, 4.0),   # mid bloom
            (1, 155, 0.20, 3.0),  # core glow – tight, brightest
        ]:
            c_g = QColor(self._color)
            c_g.setAlpha(alpha)
            zero = QColor(self._color.red(), self._color.green(), self._color.blue(), 0)
            gg = QLinearGradient(0, 0, 0, rect.height())
            gg.setColorAt(0.0, zero)
            gg.setColorAt(max(0.0, center - span), zero)
            gg.setColorAt(center, c_g)
            ge = min(1.0, center + span)
            if ge > center:
                gg.setColorAt(ge, zero)
            p.setBrush(gg)
            p.drawRoundedRect(QRectF(rect.adjusted(-spread, 0, spread, 0)), radius, radius)

        # A narrow white-blue sheen gives the brightest area a subtle glossy highlight.
        sheen_g = QLinearGradient(rect.left(), 0, rect.right(), 0)
        sheen_g.setColorAt(0.0, QColor(255, 255, 255, 0))
        sheen_g.setColorAt(0.48, QColor(255, 255, 255, 18))
        sheen_g.setColorAt(0.5, sheen)
        sheen_g.setColorAt(0.52, QColor(255, 255, 255, 18))
        sheen_g.setColorAt(1.0, QColor(255, 255, 255, 0))
        clip = QRectF(rect.adjusted(0, int(rect.height() * a), 0, -int(rect.height() * (1.0 - c))))
        p.save()
        p.setClipRect(clip)
        p.setBrush(sheen_g); p.drawRoundedRect(QRectF(rect), 2.0, 2.0)
        p.restore()


class CodeBlockWidget(QFrame):
    def __init__(self, code_text: str, lang: str = "", scroll_area: Optional[QScrollArea] = None, parent=None):
        super().__init__(parent)
        self._scroll_area = scroll_area
        self._code_text = str(code_text or "").rstrip("\n")
        self._lang = str(lang or "").strip().lower() or "code"
        self._content_width = 0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setFrameShape(QFrame.Shape.NoFrame)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        top = QWidget(self)
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(12, 10, 12, 0)
        top_layout.setSpacing(8)

        self.lang_label = QLabel(self._lang, top)
        top_layout.addWidget(self.lang_label)
        top_layout.addStretch(1)

        self.copy_btn = QPushButton("复制", top)
        self.copy_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.clicked.connect(self._copy_code)
        top_layout.addWidget(self.copy_btn)
        layout.addWidget(top)

        self.code_view = BubbleTextEdit(self._scroll_area, self)
        self.code_view.setFont(QFont("Consolas", 10))
        self.code_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.code_view.document().setDocumentMargin(0)
        self.code_view.setViewportMargins(0, 0, 0, 0)
        self.code_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.code_view.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.code_view.document().documentLayout().documentSizeChanged.connect(self._sync_height)
        layout.addWidget(self.code_view)

        self.apply_theme()
        self.code_view.setHtml(md_code_to_html(self._code_text, self._lang, light=_is_light_theme()))

    def apply_theme(self):
        light = _is_light_theme()
        frame_bg = "#f7f8fa" if light else "#202226"
        frame_border = "#d9dee6" if light else "#2f3440"
        lang_fg = "#7b8694" if light else "#8f96a3"
        btn_fg = "#6b7280" if light else "#a0a7b4"
        btn_hover = "#111827" if light else "#ffffff"
        self.setStyleSheet(
            f"QFrame{{background:{frame_bg};border:1px solid {frame_border};border-radius:8px;}}"
            f"QLabel{{background:transparent;border:none;color:{lang_fg};font-size:10px;}}"
            f"QPushButton{{background:transparent;border:none;color:{btn_fg};font-size:10px;padding:0;}}"
            f"QPushButton:hover{{color:{btn_hover};}}"
            f"QTextEdit{{background:transparent;border:none;}}"
        )

    def set_content_width(self, width: int):
        inner_width = max(120, int(width) - 24)
        self._content_width = inner_width
        doc = self.code_view.document()
        doc.setTextWidth(float(inner_width))
        self._sync_height()

    def _sync_height(self, *_args):
        doc = self.code_view.document()
        if self._content_width > 0:
            doc.setTextWidth(float(self._content_width))
        layout_size = doc.documentLayout().documentSize()
        doc_size = doc.size()
        base_height = max(float(layout_size.height()), float(doc_size.height()))
        height = math.ceil(base_height) + 14
        target = max(30, height)
        self.code_view.setFixedHeight(target)
        self.updateGeometry()

    def _copy_code(self):
        try:
            QApplication.clipboard().setText(self._code_text)
            QToolTip.showText(QCursor.pos(), "已复制", self)
        except Exception:
            pass


# ── ElidedLabel ─────────────────────────────────────────────
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


# ── ComposerAttachmentChip ──────────────────────────────────
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


# ── FileAttachmentPill ──────────────────────────────────────
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
    def __init__(self, role, text="", parent=None, scroll_area: Optional[QScrollArea] = None, attachments: Optional[list[dict]] = None, model_label: str = "", render_markdown: bool = True):
        super().__init__(parent)
        self.role = role
        self._scroll_area = scroll_area
        self._raw_text = ""
        self._attachments = list(attachments or [])
        self._render_markdown = bool(render_markdown)
        self._segment_widgets: list[QWidget] = []
        self._render_timer: Optional[QTimer] = None  # throttled streaming render
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
        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 6, 14, 6)
        inner = QFrame(self)
        inner.setMaximumWidth(900)
        self.inner = inner
        if role == "user":
            inner.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        else:
            inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        il = QVBoxLayout(inner)
        il.setContentsMargins(10, 8, 10, 8)
        il.setSpacing(4)
        names = {"user": "You", "assistant": "Kscc UI", "tool": "Tool", "error": "Error"}
        self.header = QLabel(names.get(role, role), inner)
        self.header.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.model_tag = QLabel(str(model_label or "").strip(), inner)
        self.model_tag.setVisible(bool(str(model_label or "").strip()))
        self.model_tag.setFont(QFont("Segoe UI", 9))
        self.body = BubbleTextEdit(scroll_area, inner)
        self.body.link_clicked.connect(self._on_link_clicked)
        self.segment_wrap = QWidget(inner)
        self.segment_layout = QVBoxLayout(self.segment_wrap)
        self.segment_layout.setContentsMargins(0, 0, 0, 0)
        self.segment_layout.setSpacing(8)
        self.segment_wrap.hide()
        self.attachments_wrap = QWidget(inner)
        self.attachments_layout = QHBoxLayout(self.attachments_wrap)
        self.attachments_layout.setContentsMargins(0, 0, 0, 0)
        self.attachments_layout.setSpacing(8)
        self.attachments_wrap.hide()
        bf = QFont()
        bf.setPointSize(12)
        bf.setFamilies(["Segoe UI", "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", "sans-serif"])
        self.body.setFont(bf)
        if role == "user":
            self.body.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            self.body.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        else:
            self.body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.body.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.body.document().setDocumentMargin(2)
        self.body.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.body.document().documentLayout().documentSizeChanged.connect(self._fit)
        if role == "user":
            self.header.setStyleSheet(f"color:{hdr_user};background:transparent;font-size:12px;font-weight:700;letter-spacing:0.04em")
            inner.setStyleSheet(f"QFrame{{background:{panel_user};border:none;border-radius:14px}}")
            self.body.setStyleSheet(f"QTextEdit{{background:transparent;color:{text_user};border:none}}")
        elif role == "assistant":
            self.header.setStyleSheet(f"color:{hdr_assist};background:transparent;font-size:12px;font-weight:700;letter-spacing:0.04em")
            tag_col = "#667085" if light else "#8b95a5"
            self.model_tag.setStyleSheet(f"color:{tag_col};background:transparent;font-size:11px;font-weight:400;")
            inner.setStyleSheet(f"QFrame{{background:{panel_assist};border:none;border-radius:14px}}")
            self.body.setStyleSheet(f"QTextEdit{{background:transparent;color:{text_assist};border:none}}")
        elif role == "tool":
            self.header.setStyleSheet(f"color:{C_YELLOW};background:transparent;font-size:10px;letter-spacing:0.06em")
            _tool_panel = "rgba(0,0,0,0.04)" if light else "rgba(255,255,255,0.04)"
            _tool_dim = "#333333" if light else C_DIM
            inner.setStyleSheet(f"QFrame{{background:{_tool_panel};border:none;border-radius:12px}}")
            self.body.setStyleSheet(f"QTextEdit{{background:transparent;color:{_tool_dim};border:none;font-size:11px}}")
        elif role == "error":
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
        il.addWidget(self.segment_wrap)
        if role == "user":
            outer.addStretch()
            outer.addWidget(inner)
        else:
            outer.addWidget(inner)
            outer.addStretch()
        self._render_attachments()
        self.set_text(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "body"):
            QTimer.singleShot(0, self._fit)

    def _scroll_viewport_width(self) -> int:
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
        cap = self._avail_inner_content_width()
        if self.segment_wrap.isVisible():
            self._fit_segment_widgets(cap)
            return
        if not self.body.isVisible():
            return
        doc = self.body.document()
        side_pad = 8
        if self.role == "user":
            fm = QFontMetrics(self.body.font())
            plain = self.body.toPlainText()
            lines = plain.split("\n") if plain else [""]
            mw = max((fm.horizontalAdvance(L) for L in lines), default=40)
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
            layout_size = doc.documentLayout().documentSize()
            doc_size = doc.size()
            base_h = max(float(layout_size.height()), float(doc_size.height()))
            h = math.ceil(base_h) + side_pad * 2 + 8
            self.body.setFixedHeight(int(max(h, 36)))

    def set_text(self, t):
        self._raw_text = str(t or "")
        # Allow markdown rendering for assistant/tool bubbles (tool output often contains code fences).
        if self.role in ("assistant", "tool") and self._render_markdown:
            self._render_assistant_content()
        else:
            self.segment_wrap.hide()
            self.body.setVisible(bool(str(t or "").strip()))
            self.body.setPlainText(self._raw_text)
        QTimer.singleShot(0, self._fit)

    def enable_markdown_render(self):
        """Enable markdown render for deferred bulk restores."""
        if self._render_markdown:
            return
        self._render_markdown = True
        if self.role in ("assistant", "tool"):
            try:
                self._render_assistant_content()
            except Exception:
                # Fallback to plain text on unexpected render failure.
                self.segment_wrap.hide()
                self.body.setVisible(bool(self._raw_text.strip()))
                self.body.setPlainText(self._raw_text)
            QTimer.singleShot(0, self._fit)

    def append_text(self, t):
        if self.role == "assistant":
            self._raw_text += str(t or "")
            if self._render_markdown:
                # Throttle: batch deltas, render at most ~16fps during streaming
                if self._render_timer is None:
                    self._render_timer = QTimer(self)
                    self._render_timer.setSingleShot(True)
                    self._render_timer.timeout.connect(self._render_assistant_content)
                if not self._render_timer.isActive():
                    self._render_timer.start(60)
                # Don't call _fit here; _render_assistant_content handles fit+scroll
            else:
                if not self.body.isVisible():
                    self.body.setVisible(True)
                self.segment_wrap.hide()
                self.body.setPlainText(self._raw_text)
                QTimer.singleShot(0, self._fit)
            self.body.moveCursor(QTextCursor.MoveOperation.End)
        else:
            self.body.setReadOnly(False)
            try:
                c = self.body.textCursor()
                c.movePosition(QTextCursor.MoveOperation.End)
                c.insertText(t)
            finally:
                self.body.setReadOnly(True)

    def finalize_stream(self):
        """Force final render after streaming ends (flush pending throttle)."""
        if self._render_timer and self._render_timer.isActive():
            self._render_timer.stop()
            self._render_assistant_content()

    def _on_link_clicked(self, href: str):
        href = str(href or "")
        if not href.startswith("copycode:"):
            return
        try:
            idx = int(href.split(":", 1)[1])
        except Exception:
            return
        blocks = re.findall(r"```(?:\w*)\n(.*?)```", self._raw_text, flags=re.DOTALL)
        if idx < 0 or idx >= len(blocks):
            return
        code = blocks[idx]
        try:
            cb = QApplication.clipboard()
            cb.setText(code)
            QToolTip.showText(QCursor.pos(), "Copied", self)
        except Exception:
            pass

    def _clear_segment_widgets(self):
        self._segment_widgets = []
        while self.segment_layout.count():
            item = self.segment_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _build_text_segment(self, text: str) -> BubbleTextEdit:
        edit = BubbleTextEdit(self._scroll_area, self.segment_wrap)
        edit.link_clicked.connect(self._on_link_clicked)
        edit.setFont(self.body.font())
        edit.document().setDocumentMargin(4)
        edit.setViewportMargins(0, 0, 0, 0)
        edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        edit.setStyleSheet(self.body.styleSheet())
        edit.setHtml(md_chat_to_html(text, light=_is_light_theme()))
        return edit

    def _fit_text_segment(self, edit: BubbleTextEdit, width: int):
        doc = edit.document()
        text_width = max(100, width - 16)
        doc.setTextWidth(float(text_width))
        layout_size = doc.documentLayout().documentSize()
        doc_size = doc.size()
        base_h = max(float(layout_size.height()), float(doc_size.height()))
        height = math.ceil(base_h) + 18
        edit.setFixedHeight(int(max(height, 22)))

    def _fit_segment_widgets(self, width: int):
        for widget in self._segment_widgets:
            if isinstance(widget, CodeBlockWidget):
                widget.set_content_width(width)
            elif isinstance(widget, BubbleTextEdit):
                self._fit_text_segment(widget, width)
        self.segment_wrap.updateGeometry()

    def _render_assistant_content(self):
        text = self._raw_text
        has_code = bool(re.search(r"```(?:\w*)\n.*?```", text, flags=re.DOTALL))
        self._clear_segment_widgets()
        if not has_code:
            self.segment_wrap.hide()
            self.body.setVisible(bool(text.strip()))
            self.body.setHtml(md_chat_to_html(text, light=_is_light_theme()))
        else:
            self.body.hide()
            self.segment_wrap.setVisible(True)
            for kind, content, lang in split_markdown_blocks(text):
                if kind == "text":
                    if not content.strip():
                        continue
                    widget = self._build_text_segment(content)
                else:
                    widget = CodeBlockWidget(content, lang, self._scroll_area, self.segment_wrap)
                self._segment_widgets.append(widget)
                self.segment_layout.addWidget(widget)
            if not self._segment_widgets and text.strip():
                fallback = self._build_text_segment(text)
                self._segment_widgets.append(fallback)
                self.segment_layout.addWidget(fallback)
        # Fit layout, then scroll to bottom once layout is settled
        def _fit_then_scroll():
            self._fit()
            if self._scroll_area:
                # Only autoscroll if the user is already near the bottom.
                # This prevents history re-renders from yanking the viewport back down.
                sb = self._scroll_area.verticalScrollBar()
                try:
                    at_bottom = (sb.maximum() - sb.value()) <= 24
                except Exception:
                    at_bottom = False
                if at_bottom:
                    sb.setValue(sb.maximum())
        QTimer.singleShot(0, _fit_then_scroll)

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


# ── ChatInputEdit ───────────────────────────────────────────
class ChatInputEdit(QTextEdit):
    """多行输入 + Ctrl+Enter 发送。"""

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


# ── NoWheel* widgets ────────────────────────────────────────
class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelFontComboBox(QFontComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaxVisibleItems(10)
        try:
            self.view().setMinimumHeight(0)
            self.view().setMaximumHeight(320)
        except Exception:
            pass

    def wheelEvent(self, event):
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()


# ── WorkspaceGroupHeader ────────────────────────────────────
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


# ── ContextRingWidget ───────────────────────────────────────
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
        self._compression: Optional[str] = None  # None | "compressing" | "done"
        self._refresh_tip()

    def clear(self):
        self._ratio = None
        self._current = "—"
        self._limit = "—"
        self._compression = None
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

    def set_compression_status(self, status: Optional[str]):
        """设置压缩状态: None | 'compressing' | 'done'"""
        self._compression = status
        self._refresh_tip()
        self.update()
        # 压缩完成后 3 秒自动清除状态
        if status == "done":
            QTimer.singleShot(3000, self._clear_compression)

    def _clear_compression(self):
        if self._compression == "done":
            self._compression = None
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
        tip = f"上下文\n已用: {self._current}\n上限: {self._limit}\n占用: {pct}%"
        if self._compression == "compressing":
            tip += "\n状态: ⏳ 正在压缩上下文..."
        elif self._compression == "done":
            tip += "\n状态: ✓ 压缩完成"
        self.setToolTip(tip)

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

        # 压缩状态视觉指示
        if self._compression == "compressing":
            # 正在压缩：橙色虚线弧覆盖
            pen_c = QPen(QColor("#f59e0b"))
            pen_c.setWidthF(max(1.0, self._d * 0.08))
            pen_c.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen_c.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen_c)
            p.drawArc(inner, 90 * 16, -int(360 * 16))
        elif self._compression == "done":
            # 压缩完成：绿色短弧标记
            pen_c = QPen(QColor("#22c55e"))
            pen_c.setWidthF(max(1.4, self._d * 0.13))
            pen_c.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen_c)
            p.drawArc(inner, 90 * 16, -int(60 * 16))
