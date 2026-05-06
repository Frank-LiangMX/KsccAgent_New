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
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton,
    QScrollArea, QFrame, QDialog, QSizePolicy, QComboBox, QFontComboBox,
    QSpinBox, QToolButton, QApplication, QToolTip, QMenu,
)
from PyQt6.QtCore import Qt, QTimer, QSize, QPoint, QPointF, QRect, QRectF, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter,
    QPalette, QPen, QPixmap, QTextCursor, QTextOption, QIcon,
)

from ui_common import (
    _is_light_theme, _tooltip_css, _with_tooltip_style, _fmt_k,
    _make_plus_icon, _settings_icon, _ensure_attachments_dir,
    _is_image_path, _attachment_meta, _CODE_FONT_STACK, HAS_WEBENGINE,
)
from config import load_config
from theme import (
    md_chat_to_html, quark_icon,
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
        glow = QColor(self._color)
        glow.setAlpha(130)
        sheen = QColor("#ffffff")
        sheen.setAlpha(175)
        center = self._phase
        a = max(0.0, center - 0.22)
        c = min(1.0, center + 0.22)
        g = QLinearGradient(0, 0, 0, rect.height())
        g.setColorAt(0.0, dim)
        g.setColorAt(a, dim)
        g.setColorAt(center, bright)
        g.setColorAt(c, dim)
        g.setColorAt(1.0, dim)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(g); p.drawRoundedRect(QRectF(rect), 2.0, 2.0)

        # Add a soft moving glow around the bright segment so the running
        # state feels more lively without becoming visually noisy.
        glow_g = QLinearGradient(0, 0, 0, rect.height())
        glow_g.setColorAt(0.0, QColor(self._color.red(), self._color.green(), self._color.blue(), 0))
        glow_g.setColorAt(max(0.0, center - 0.30), QColor(self._color.red(), self._color.green(), self._color.blue(), 0))
        glow_g.setColorAt(center, glow)
        glow_g.setColorAt(min(1.0, center + 0.30), QColor(self._color.red(), self._color.green(), self._color.blue(), 0))
        glow_g.setColorAt(1.0, QColor(self._color.red(), self._color.green(), self._color.blue(), 0))
        p.setBrush(glow_g); p.drawRoundedRect(QRectF(rect.adjusted(-1, 0, 1, 0)), 3.0, 3.0)

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
        self._raw_text = ""
        self._attachments = list(attachments or [])
        self._render_markdown = bool(render_markdown)
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
        il.setContentsMargins(12, 8, 12, 8)
        il.setSpacing(4)
        names = {"user": "You", "assistant": "Kscc UI", "tool": "Tool", "error": "Error"}
        self.header = QLabel(names.get(role, role), inner)
        self.header.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.model_tag = QLabel(str(model_label or "").strip(), inner)
        self.model_tag.setVisible(bool(str(model_label or "").strip()))
        self.model_tag.setFont(QFont("Segoe UI", 9))
        self.body = BubbleTextEdit(scroll_area, inner)
        self.body.link_clicked.connect(self._on_link_clicked)
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
        self.body.document().setDocumentMargin(4)
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
        if self.role == "assistant" and self._render_markdown:
            self.body.setHtml(md_chat_to_html(self._raw_text, light=_is_light_theme()))
        else:
            self.body.setPlainText(self._raw_text)
        QTimer.singleShot(0, self._fit)

    def append_text(self, t):
        if self.role == "assistant":
            if not self.body.isVisible():
                self.body.setVisible(True)
            self._raw_text += str(t or "")
            if self._render_markdown:
                self.body.setHtml(md_chat_to_html(self._raw_text, light=_is_light_theme()))
            else:
                self.body.setPlainText(self._raw_text)
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
