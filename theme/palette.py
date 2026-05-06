"""Color tokens for Qt UI, chat markdown, and Monaco (keep in sync with theme/monaco_shell.css)."""

# ── Base / glass surfaces ───────────────────────────────────
C_BG = "#030712"
C_BG_MID = "#061018"
C_BG_GRAD_END = "#0c1828"

C_PANEL = "rgba(255,255,255,0.055)"
C_PANEL_HI = "rgba(255,255,255,0.085)"
C_BORDER = "rgba(255,255,255,0.09)"
C_BORDER_AC = "rgba(94,233,255,0.28)"

C_TEXT = "#eef4f8"
C_DIM = "#94a3b8"
C_ACCENT = "#5ee9ff"
C_GREEN = "#6ee7b7"
C_RED = "#fca5a5"
C_TEAL = "#99f6e4"
C_YELLOW = "#fcd34d"
C_ACCENT_SEL = "rgba(94,233,255,0.14)"
# 文件树整行委托绘制时略亮于 C_ACCENT_SEL，避免条带发灰发暗
C_FILE_TREE_SEL = "rgba(94,233,255,0.28)"

# Alias used in legacy f-strings
C_SURFACE = C_PANEL

# Light theme: neutral surfaces + deep accent (avoid bright cyan on white)
C_TEXT_LIGHT = "#000000"
C_DIM_LIGHT = "#333333"
C_ACCENT_LIGHT = "#0c4a6e"
C_ACCENT_SEL_LIGHT = "rgba(12, 74, 110, 0.18)"
C_TEAL_LIGHT = "#0f766e"
C_FILE_TREE_SEL_LIGHT = "rgba(12, 74, 110, 0.22)"
C_PANEL_HI_LIGHT = "rgba(0, 0, 0, 0.06)"

# Icons default stroke (not in CSS)
ICON_FG_DEFAULT = "#9fb0c8"
