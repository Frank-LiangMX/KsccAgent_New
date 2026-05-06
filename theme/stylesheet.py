"""Application-wide Qt stylesheet built from palette tokens."""

from .palette import (
    C_ACCENT,
    C_ACCENT_LIGHT,
    C_ACCENT_SEL,
    C_DIM,
    C_BORDER,
    C_BG,
    C_BG_GRAD_END,
    C_BG_MID,
    C_PANEL,
    C_PANEL_HI,
    C_TEXT,
)


def build_stylesheet(mode: str = "dark") -> str:
    light = str(mode).lower() == "light"
    if light:
        bg = "#f5f5f5"
        bg_mid = "#f5f5f5"
        bg_end = "#f5f5f5"
        panel = "#f0f0f0"
        panel_hi = "#e8e8e8"
        text = "#000000"
        dim = "#333333"
        border = "rgba(0,0,0,0.12)"
        accent_sel = "rgba(12, 74, 110, 0.18)"
        accent_tab = C_ACCENT_LIGHT
        sb_handle = "rgba(0,0,0,0.12)"
        sb_grab = "rgba(0,0,0,0.22)"
        sb_hover = "rgba(0,0,0,0.32)"
        btn_bg = "rgba(12, 74, 110, 0.14)"
        btn_fg = "#000000"
        btn_hover = "rgba(12, 74, 110, 0.22)"
        btn_press = "rgba(12, 74, 110, 0.10)"
        btn_dis_bg = "rgba(0,0,0,0.06)"
        line_bg = "rgba(0,0,0,0.06)"
        tab_muted = "rgba(0,0,0,0.45)"
        tab_hover = "rgba(0,0,0,0.75)"
        tip_bg = "#ffffff"
        tip_fg = "#111827"
        tip_border = "#d1d5db"
    else:
        bg = C_BG
        bg_mid = C_BG_MID
        bg_end = C_BG_GRAD_END
        panel = C_PANEL
        panel_hi = C_PANEL_HI
        text = C_TEXT
        dim = C_DIM
        border = C_BORDER
        accent_sel = C_ACCENT_SEL
        accent_tab = C_ACCENT
        sb_handle = "rgba(255,255,255,0.06)"
        sb_grab = "rgba(255,255,255,0.12)"
        sb_hover = "rgba(255,255,255,0.2)"
        btn_bg = "rgba(94,233,255,0.18)"
        btn_fg = "#ecfeff"
        btn_hover = "rgba(94,233,255,0.28)"
        btn_press = "rgba(94,233,255,0.14)"
        btn_dis_bg = "rgba(255,255,255,0.05)"
        line_bg = "rgba(255,255,255,0.06)"
        tab_muted = "rgba(255,255,255,0.42)"
        tab_hover = "rgba(255,255,255,0.75)"
        tip_bg = "#111827"
        tip_fg = "#eef4f8"
        tip_border = "#334155"
    return f"""
QMainWindow {{
  background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {bg}, stop:0.5 {bg_mid}, stop:1 {bg_end});
  color: {text};
  font-size: 12px;
}}
QWidget {{ color: {text}; font-size: 12px; }}
#centralGlass {{ background: transparent; }}
QToolBar {{
  background: {panel};
  border: none;
  padding: 6px 18px;
  spacing: 14px;
}}
QToolBar QLabel {{ background: transparent; color: {dim}; }}
QStatusBar {{
  background: {panel};
  color: {dim};
  padding: 4px 14px;
  font-size: 11px;
  border: none;
}}
QStatusBar QLabel {{ color: {dim}; background: transparent; }}
QToolTip {{
  background-color: {tip_bg};
  color: {tip_fg};
  border: 1px solid {tip_border};
  padding: 4px 6px;
  font-size: 11px;
}}
QSplitter::handle {{ background: {sb_handle}; }}
QSplitter::handle:horizontal {{ width: 2px; }}
QTreeView {{
  background: transparent;
  border: none;
  outline: none;
  color: {dim};
  font-size: 12px;
  /* 选中/高亮覆盖整行（含缩进、展开符），避免只给文字格上圆角 */
  show-decoration-selected: 1;
}}
/* 禁止行内圆角：否则在 Windows 上缩进区与文字区会各画一块圆角底，像两个按钮 */
QTreeView::item {{ padding: 3px 8px; border: none; border-radius: 0px; }}
QTreeView::item:hover {{ background: {panel_hi}; color: {text}; border-radius: 0px; }}
QTreeView::item:selected {{ background: {accent_sel}; color: {text}; border-radius: 0px; }}
QTreeView::item:selected:active {{ background: {accent_sel}; color: {text}; }}
QTreeView::branch {{ background: transparent; border: none; }}
/* 文件树显式设置 hover/选中，避免回退到系统黑色高亮 */
QTreeView#fileTree::item:hover {{
  background: {panel_hi};
  color: {text};
}}
QTreeView#fileTree::item:selected,
QTreeView#fileTree::item:selected:active {{
  background: {accent_sel};
  color: {text};
}}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {sb_grab}; min-height: 32px; border-radius: 3px; }}
QScrollBar::handle:vertical:hover {{ background: {sb_hover}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 6px; }}
QScrollBar::handle:horizontal {{ background: {sb_grab}; min-width: 32px; border-radius: 3px; }}
QPushButton {{
  background: {btn_bg};
  color: {btn_fg};
  border: none;
  border-radius: 10px;
  padding: 6px 16px;
  font-size: 12px;
  font-weight: 600;
}}
QPushButton:hover {{ background: {btn_hover}; }}
QPushButton:pressed {{ background: {btn_press}; }}
QPushButton:disabled {{ background: {btn_dis_bg}; color: {dim}; }}
QLineEdit, QSpinBox {{
  background: {line_bg};
  color: {text};
  border: none;
  border-radius: 8px;
  padding: 6px 10px;
}}
QTextEdit, QPlainTextEdit {{
  background: {line_bg};
  color: {text};
  border: none;
  border-radius: 12px;
  padding: 10px;
  font-size: 13px;
}}
QTextEdit#composerInput {{
  padding: 0px 4px;
  background: transparent;
  border-radius: 0px;
}}
QComboBox {{
  background: {line_bg};
  color: {text};
  border: none;
  border-radius: 10px;
  padding: 5px 12px;
  font-size: 12px;
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
  background: {"rgba(255,255,255,0.98)" if light else "rgba(12,18,28,0.97)"};
  color: {text};
  selection-background-color: {accent_sel};
  border: none;
  border-radius: 8px;
}}
QMenu {{
  background: {"rgba(255,255,255,0.98)" if light else "rgba(12,18,28,0.97)"};
  color: {text};
  border: none;
  border-radius: 10px;
  padding: 6px;
}}
QMenu::item {{ padding: 8px 28px; border-radius: 6px; }}
QMenu::item:selected {{ background: {accent_sel}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 10px; }}
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar::tab {{
  background: transparent;
  color: {tab_muted};
  padding: 10px 20px;
  border: none;
  font-size: 11px;
}}
QTabBar::tab:selected {{ color: {accent_tab}; background: transparent; }}
QTabBar::tab:hover {{ color: {tab_hover}; }}
"""


STYLESHEET = build_stylesheet("dark")
