"""
Settings Page - 设置页面
从 app.py 提取：SettingsPage
"""

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QCheckBox, QListWidget, QListWidgetItem,
    QComboBox, QFontComboBox, QSpinBox, QFrame, QStackedWidget,
    QFileDialog, QColorDialog, QToolTip, QScrollArea,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPalette

from ui_common import (
    _is_light_theme, _with_tooltip_style, _tooltip_css,
    MONACO_PACKAGE_JSON, PROJECT_PACKAGE_JSON,
)
from config import (
    load_config, Config, save_config, get_active_provider,
    get_effective_model_limits, get_kscc_model_limits,
    KSCC_MODELS, OpenAIModel,
)
import data_portability
from chat_widgets import NoWheelComboBox, NoWheelFontComboBox, NoWheelSpinBox
from theme import C_DIM, C_PANEL, C_PANEL_HI, C_TEXT


class SettingsPage(QWidget):
    saved = pyqtSignal()
    cancelled = pyqtSignal()

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setMinimumWidth(720)
        self.setObjectName("SettingsPage")
        self._model_row_prev = -1
        self._monaco_installing = False
        self._monaco_status_tip = ""
        self._light = str(getattr(cfg, "theme", "dark")).lower() == "light"
        self._txt = "#0f172a" if self._light else C_TEXT
        self._dim = "#64748b" if self._light else C_DIM
        self._panel = "#f0f0f0" if self._light else C_PANEL
        self._panel_hi = "#e7e8e8" if self._light else C_PANEL_HI
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        nav_wrap = QFrame()
        nav_wrap.setObjectName("settingsNavWrap")
        nav_wrap.setFixedWidth(230)
        nav_l = QVBoxLayout(nav_wrap)
        nav_l.setContentsMargins(16, 14, 10, 12)
        nav_l.setSpacing(10)
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
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(28, 24, 28, 12)
        right_l.setSpacing(12)
        self.pages = QStackedWidget()
        right_l.addWidget(self.pages, 1)
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.save_btn = QPushButton("保存")
        self.save_btn.clicked.connect(self._sv)
        foot.addStretch(1)
        foot.addWidget(self.save_btn)
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
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
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

    def _add_setting_card(self, parent: QVBoxLayout, title: str, desc: str, control: QWidget, help_text: str = ""):
        card = QFrame()
        card.setStyleSheet("QFrame{background:transparent;border:none}")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        if help_text:
            title_row = QWidget()
            title_row.setStyleSheet("background:transparent")
            tr = QHBoxLayout(title_row)
            tr.setContentsMargins(0, 0, 0, 0)
            tr.setSpacing(6)
            t = QLabel(title)
            t.setStyleSheet(f"font-size:13px;font-weight:500;color:{self._txt};background:transparent")
            tr.addWidget(t)
            help_btn = QPushButton("?")
            help_btn.setFixedSize(20, 20)
            help_btn.setCursor(Qt.CursorShape.WhatsThisCursor)
            border = "#d7dbe2" if self._light else "#3a3d41"
            dim = "#7b7e82" if self._light else "#858585"
            hover = "#e7e8e8" if self._light else "#313131"
            help_btn.setStyleSheet(
                f"QPushButton{{background:transparent;color:{dim};border:1px solid {border};border-radius:10px;font-size:12px;font-weight:700;}}"
                f"QPushButton:hover{{background:{hover};color:{self._txt};}}"
            )
            _help_text = help_text
            help_btn.clicked.connect(lambda checked=False, ht=_help_text, btn=help_btn: QToolTip.showText(btn.mapToGlobal(btn.rect().bottomLeft()), ht, self))
            tr.addWidget(help_btn)
            tr.addStretch(1)
            cl.addWidget(title_row)
        else:
            t = QLabel(title)
            t.setStyleSheet(f"font-size:13px;font-weight:500;color:{self._txt};background:transparent")
            cl.addWidget(t)
        d = QLabel(desc)
        d.setWordWrap(True)
        d.setStyleSheet(f"font-size:11px;color:{self._dim};background:transparent")
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
        from PyQt6.QtWidgets import QApplication
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        box, form = self._make_group("外观", "主题、字体与强调色")
        self.theme_combo = NoWheelComboBox()
        self.theme_combo.addItems(["dark", "light"])
        self.ui_font_combo = NoWheelFontComboBox()
        self.ui_font_size = self._make_size_combo(list(range(9, 21)))
        self.code_font_combo = NoWheelFontComboBox()
        self.code_font_size = self._make_size_combo(list(range(10, 25)))
        self.accent_btn = QPushButton()
        self.accent_btn.clicked.connect(self._pick_accent_color)
        self.accent_value = QLabel("#5ee9ff")
        accent_row = QWidget()
        accent_l = QHBoxLayout(accent_row)
        accent_l.setContentsMargins(0, 0, 0, 0)
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
        v.addWidget(box)
        v.addStretch(1)
        self._add_scroll_page(page)

    def _add_scroll_page(self, page: QWidget):
        """Wrap a page widget in a QScrollArea and add to stacked widget."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.pages.addWidget(scroll)

    def _build_model_api_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
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
        self.m_key = QLineEdit()
        self.m_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.m_url = QLineEdit()
        self.m_url.setPlaceholderText("该模型专属 API Base URL（可留空）")
        self.m_ctx_limit = NoWheelSpinBox()
        self.m_ctx_limit.setRange(4096, 2_000_000)
        self.m_ctx_limit.setSingleStep(16384)
        self.m_out_limit = NoWheelSpinBox()
        self.m_out_limit.setRange(512, 400_000)
        self.m_out_limit.setSingleStep(8192)

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
        v.addWidget(box)
        v.addStretch(1)
        self._add_scroll_page(page)

    def _build_ide_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
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

        v.addWidget(box)
        v.addStretch(1)
        self._add_scroll_page(page)

    def _build_agent_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        box, form = self._make_group("Agent", "Agent 行为与本地能力增强设置")
        self.skills_enabled = QCheckBox("启用 Skill 匹配")
        self.skill_debug_log = QCheckBox("记录 Skill 调试日志")
        self.memory_injection_enabled = QCheckBox("启用本地记忆注入")
        self._add_setting_card(form, "Skill 匹配", "关闭后将跳过本地 skill 召回，仅走普通对话。", self.skills_enabled)
        self._add_setting_card(form, "Skill 调试日志", "写入 logs/skill_debug.log，记录命中/未命中原因。", self.skill_debug_log)
        self._add_setting_card(form, "本地记忆注入", "将 memory/ 中规则、事实、归档摘要注入系统提示词。包括 P3-1 Insight Index、P3-2 任务类型选择性注入、P3-5 命中可视化。", self.memory_injection_enabled)
        # P2-2: Auto-save threshold
        self.auto_save_threshold = QSpinBox()
        self.auto_save_threshold.setRange(0, 100)
        self.auto_save_threshold.setSuffix(" 分")
        self._add_setting_card(form, "Skill 自动保存阈值", "任务完成后评分超过此阈值自动保存为 Skill，0 表示禁用自动保存。", self.auto_save_threshold)
        v.addWidget(box)

        # P5-5: Feature flags
        ff_box, ff_form = self._make_group("实验特性", "开启或关闭实验性功能，关闭后对应能力完全禁用")
        self.feature_task_mode = QCheckBox("任务状态机模式")
        self.feature_insight_index = QCheckBox("Insight 记忆层")
        self.feature_memory_compress = QCheckBox("记忆自动压缩")
        self.feature_risk_templates = QCheckBox("风控模板检测")
        self.feature_evidence_capture = QCheckBox("网页/设备证据采集")
        self.feature_adb_tools = QCheckBox("ADB 设备工具")
        self._add_setting_card(ff_form, "任务状态机", "启用 Plan→Execute→Reflect 任务循环，关闭后退化为普通对话模式。", self.feature_task_mode,
            help_text="关闭后：Agent 不再走 Plan → Execute → Reflect 的任务循环，退化成普通的一问一答对话模式。你在 UI 上点 Task Mode 切换时会直接被忽略。")
        self._add_setting_card(ff_form, "Insight Index", "自动从对话中提炼洞察并跨任务检索注入（P3-1）。", self.feature_insight_index,
            help_text="关闭后：Agent 不再自动从对话中提炼洞察（比如你常用的路径、偏好），也不会把这些洞察注入到后续对话中。相当于 Agent 不会越用越懂你。")
        self._add_setting_card(ff_form, "记忆压缩", "14 天前的归档自动压缩，控制 token 成本（P3-3）。", self.feature_memory_compress,
            help_text="关闭后：14 天前的归档记忆不会被自动压缩。时间长了 token 消耗会越来越高，但你不会丢任何历史信息。")
        self._add_setting_card(ff_form, "风控模板", "支付/删除/部署等敏感场景关键词匹配与 UI 警告（P4-5）。", self.feature_risk_templates,
            help_text="关闭后：Agent 遇到支付、删除、部署等敏感关键词时，不再弹出安全警告。适合你完全信任 Agent 自己判断的场景。")
        self._add_setting_card(ff_form, "证据采集", "web_fetch 和 ADB 操作自动保存证据到 evidence/ 目录（P4-3/P4-4）。", self.feature_evidence_capture,
            help_text="关闭后：web_fetch 和 ADB 操作不再自动保存快照到 evidence/ 目录。省磁盘，但事后没法追溯 Agent 到底抓了什么网页内容。")
        self._add_setting_card(ff_form, "ADB 工具", "启用 Android 设备调试工具，需要系统已安装 ADB。", self.feature_adb_tools,
            help_text="打开后：Agent 的工具列表里会多出 adb_command，可以控制 Android 设备（截屏、安装 APK、模拟点击等）。需要电脑上已安装 ADB。默认关是因为大多数人用不到，而且开了之后 Agent 有权限操作你的手机。")
        v.addWidget(ff_box)
        v.addStretch(1)
        self._add_scroll_page(page)

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
            msg = f"导入完成: +skills {counts.get('skills_added', 0)} / upd {counts.get('skills_updated', 0)}"
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
        self.auto_save_threshold.setValue(int(getattr(cfg, "auto_save_skill_threshold", 75.0)))
        # P5-5: Feature flags
        self.feature_task_mode.setChecked(bool(getattr(cfg, "feature_task_mode", True)))
        self.feature_insight_index.setChecked(bool(getattr(cfg, "feature_insight_index", True)))
        self.feature_memory_compress.setChecked(bool(getattr(cfg, "feature_memory_compress", True)))
        self.feature_risk_templates.setChecked(bool(getattr(cfg, "feature_risk_templates", True)))
        self.feature_evidence_capture.setChecked(bool(getattr(cfg, "feature_evidence_capture", True)))
        self.feature_adb_tools.setChecked(bool(getattr(cfg, "feature_adb_tools", False)))
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
                self.oa_list.addItem(f"Kscc · {ref}  ({ctx // 1000}k / {out // 1000}k)")
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
        base = f"model-{len(self.cfg.openai_models) + 1}"
        self.cfg.openai_models.append(OpenAIModel(name=base, model=base, api_key="", base_url="https://api.openai.com/v1", enabled=True))
        self._refresh_models_list()
        self.oa_list.setCurrentRow(len(self._model_entries()) - 1)

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
        from PyQt6.QtWidgets import QApplication
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
        self.cfg.auto_save_skill_threshold = float(self.auto_save_threshold.value())
        # P5-5: Feature flags
        self.cfg.feature_task_mode = self.feature_task_mode.isChecked()
        self.cfg.feature_insight_index = self.feature_insight_index.isChecked()
        self.cfg.feature_memory_compress = self.feature_memory_compress.isChecked()
        self.cfg.feature_risk_templates = self.feature_risk_templates.isChecked()
        self.cfg.feature_evidence_capture = self.feature_evidence_capture.isChecked()
        self.cfg.feature_adb_tools = self.feature_adb_tools.isChecked()
        self.cfg.ide_font_size = int(self.ide_font_size.currentText() or 13)
        if self.cfg.openai_active and not any(m.name == self.cfg.openai_active and m.enabled for m in self.cfg.openai_models):
            enabled = [m for m in self.cfg.openai_models if m.enabled]
            self.cfg.openai_active = enabled[0].name if enabled else ""
        save_config(self.cfg)
        self.saved.emit()
