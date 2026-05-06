"""
File Tree - 文件树视图组件
从 app.py 提取：FileTreeDelegate, FileTree
"""

import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QTreeView, QStyledItemDelegate, QStyle, QSizePolicy,
)
from PyQt6.QtCore import Qt, QSize, QRect, pyqtSignal
from PyQt6.QtGui import (
    QColor, QPalette, QPainter, QStandardItemModel, QStandardItem, QIcon,
)

from ui_common import _is_light_theme
from theme import (
    quark_icon, C_ACCENT, C_ACCENT_LIGHT, C_FILE_TREE_SEL,
    C_FILE_TREE_SEL_LIGHT, C_PANEL_HI, C_TEXT,
)


class FileTreeDelegate(QStyledItemDelegate):
    """Hover/选中背景横跨整行（含左侧缩进与分支区），避免只在文字/图标格上色。"""

    def __init__(self, tree: QTreeView):
        super().__init__(tree)
        self._tree = tree

    def paint(self, painter, option, index):
        vp = self._tree.viewport()
        r = option.rect
        y, h = r.top(), max(1, r.height())
        full = QRect(r.left(), y, max(0, vp.width() - r.left()), h)

        st = option.state
        enabled = bool(st & QStyle.StateFlag.State_Enabled)
        selected = bool(st & QStyle.StateFlag.State_Selected)
        hover = bool(st & QStyle.StateFlag.State_MouseOver)

        if _is_light_theme():
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
        self.setHeaderHidden(True)
        self.setMinimumWidth(160)
        self.setAnimated(True)
        self.setIndentation(14)
        self.setMouseTracking(True)
        self.setAllColumnsShowFocus(True)
        self._model = QStandardItemModel()
        self.setModel(self._model)
        self._fi = quark_icon("file", 15)
        self._di = quark_icon("folder", 15, C_ACCENT_LIGHT if _is_light_theme() else C_ACCENT)
        self.set_workspace(ws)
        self.doubleClicked.connect(self._clk)

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
        self._ws = os.path.abspath(ws)
        self._model.clear()
        r = QStandardItem(self._di, os.path.basename(self._ws) or self._ws)
        r.setData(self._ws, Qt.ItemDataRole.UserRole)
        r.setEditable(False)
        self._model.appendRow(r)
        self._pop(r, self._ws)
        self.expandAll()

    def _pop(self, p, path, d=0):
        if d > 3:
            return
        try:
            es = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        for e in es:
            if e.name.startswith('.') and e.name not in ('.env', '.gitignore'):
                continue
            if e.is_dir():
                it = QStandardItem(self._di, e.name)
                it.setData(e.path, Qt.ItemDataRole.UserRole)
                it.setEditable(False)
                p.appendRow(it)
                self._pop(it, e.path, d + 1)
            else:
                it = QStandardItem(self._fi, e.name)
                it.setData(e.path, Qt.ItemDataRole.UserRole)
                it.setEditable(False)
                p.appendRow(it)

    def _clk(self, idx):
        p = idx.data(Qt.ItemDataRole.UserRole)
        if p and os.path.isfile(p):
            self.file_selected.emit(p)
