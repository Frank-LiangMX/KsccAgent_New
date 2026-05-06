"""Chat bubble markdown → HTML using theme colors."""

import re

from .palette import C_TEXT


def md_chat_to_html(text: str, light: bool = False) -> str:
    """Convert basic markdown to HTML for QTextEdit display."""
    if light:
        pre_bg = "rgba(0,0,0,0.06)"
        code_bg = "rgba(0,0,0,0.08)"
        code_fg = "#000000"
    else:
        pre_bg = "rgba(255,255,255,0.06)"
        code_bg = "rgba(255,255,255,0.08)"
        code_fg = C_TEXT
    t = text
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = re.sub(
        r"```(\w*)\n(.*?)```",
        rf'<pre style="background:{pre_bg};padding:12px;border-radius:12px;margin:8px 0;border:none"><code>\2</code></pre>',
        t,
        flags=re.DOTALL,
    )
    t = re.sub(
        r"`([^`]+)`",
        rf'<code style="background:{code_bg};padding:2px 8px;border-radius:6px;font-size:11px;color:{code_fg}">\1</code>',
        t,
    )
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"\*(.+?)\*", r"<i>\1</i>", t)
    t = re.sub(r"^### (.+)$", r'<h4 style="margin:4px 0">\1</h4>', t, flags=re.MULTILINE)
    t = re.sub(r"^## (.+)$", r'<h3 style="margin:4px 0">\1</h3>', t, flags=re.MULTILINE)
    t = re.sub(r"^# (.+)$", r'<h2 style="margin:4px 0">\1</h2>', t, flags=re.MULTILINE)
    t = re.sub(r"^- (.+)$", r"<li>\1</li>", t, flags=re.MULTILINE)
    t = re.sub(r"(<li>.*?</li>\n?)+", r"<ul>\g<0></ul>", t, flags=re.DOTALL)
    t = t.replace("\n", "<br>")
    return f'<div style="font-size:12px;line-height:1.5">{t}</div>'
