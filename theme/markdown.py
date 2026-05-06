"""Chat bubble markdown → HTML using theme colors."""

import re

from .palette import C_TEXT


def md_chat_to_html(text: str, light: bool = False) -> str:
    """Convert basic markdown to HTML for QTextEdit display."""
    if light:
        pre_bg = "rgba(0,0,0,0.06)"
        code_bg = "rgba(15,23,42,0.06)"
        code_fg = "#000000"
        inline_code_fg = "#f8654b"
        inline_code_bd = "rgba(15,23,42,0.10)"
        btn_bg = "rgba(0,0,0,0.08)"
        btn_bg_h = "rgba(0,0,0,0.14)"
    else:
        pre_bg = "rgba(255,255,255,0.06)"
        code_bg = "rgba(255,255,255,0.07)"
        code_fg = C_TEXT
        inline_code_fg = "#f8654b"
        inline_code_bd = "rgba(255,255,255,0.12)"
        btn_bg = "rgba(255,255,255,0.10)"
        btn_bg_h = "rgba(255,255,255,0.16)"
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    code_idx = 0
    code_blocks: dict[str, str] = {}

    def _codeblock(m: re.Match) -> str:
        nonlocal code_idx
        lang = (m.group(1) or "").strip()
        body = (m.group(2) or "").rstrip("\n")
        idx = code_idx
        code_idx += 1
        lang_badge = (
            f'<span style="font-size:11px;color:{code_fg};opacity:0.75">{lang}</span>'
            if lang
            else f'<span style="font-size:11px;color:{code_fg};opacity:0.55">code</span>'
        )
        copy_btn = (
            f'<a href="copycode:{idx}" '
            f'style="text-decoration:none;color:{code_fg};background:{btn_bg};'
            f'padding:2px 8px;border-radius:8px;font-size:11px;">Copy</a>'
        )
        html = (
            f'<div style="margin:8px 0;">'
            f'<div style="margin:0 0 6px 0;">{lang_badge}&nbsp;&nbsp;{copy_btn}</div>'
            f'<pre style="background:{pre_bg};padding:12px;border-radius:12px;margin:0;border:none"><code>{body}</code></pre>'
            f'</div>'
        )
        key = f"@@CODEBLOCK_{idx}@@"
        code_blocks[key] = html
        return key

    t = re.sub(r"```(\w*)\n(.*?)```", _codeblock, t, flags=re.DOTALL)
    t = re.sub(
        r"`([^`]+)`",
        rf'<code style="background:{code_bg};padding:1px 6px;border-radius:7px;'
        rf'font-size:11px;font-weight:600;color:{inline_code_fg};border:1px solid {inline_code_bd}">\1</code>',
        t,
    )
    # --- markdown table ---
    def _table_to_html(match: re.Match) -> str:
        lines = match.group(0).strip().split("\n")
        # parse header
        header_cells = [c.strip() for c in lines[0].strip("|").split("|")]
        header_html = "".join(f'<th style="padding:6px 12px;border:1px solid {border_color};background:{header_bg};font-weight:bold">{c}</th>' for c in header_cells)
        # skip separator (line[1]) and parse body rows
        body_html = ""
        for row_line in lines[2:]:
            cells = [c.strip() for c in row_line.strip("|").split("|")]
            body_html += "<tr>" + "".join(f'<td style="padding:6px 12px;border:1px solid {border_color}">{c}</td>' for c in cells) + "</tr>"
        return (
            f'<table style="border-collapse:collapse;margin:8px 0;font-size:12px;line-height:1.5">'
            f'<thead><tr>{header_html}</tr></thead>'
            f'<tbody>{body_html}</tbody></table>'
        )

    if light:
        border_color = "rgba(0,0,0,0.15)"
        header_bg = "rgba(0,0,0,0.05)"
    else:
        border_color = "rgba(255,255,255,0.12)"
        header_bg = "rgba(255,255,255,0.06)"

    t = re.sub(
        r"(?:^|\n)(\|.+\|\n\|[-| :]+\|\n(?:\|.+\|\n?)+)",
        lambda m: _table_to_html(m),
        t,
        flags=re.MULTILINE,
    )
    # --- bold / italic ---
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"\*(.+?)\*", r"<i>\1</i>", t)
    t = re.sub(r"^### (.+)$", r'<h4 style="margin:4px 0">\1</h4>', t, flags=re.MULTILINE)
    t = re.sub(r"^## (.+)$", r'<h3 style="margin:4px 0">\1</h3>', t, flags=re.MULTILINE)
    t = re.sub(r"^# (.+)$", r'<h2 style="margin:4px 0">\1</h2>', t, flags=re.MULTILINE)
    t = re.sub(r"^- (.+)$", r"<li>\1</li>", t, flags=re.MULTILINE)
    t = re.sub(r"(<li>.*?</li>\n?)+", r"<ul>\g<0></ul>", t, flags=re.DOTALL)
    t = re.sub(r"\n{2,}", "\n", t)
    t = t.replace("\n", "<br>")
    t = re.sub(r"(?:<br>\s*){2,}", "<br>", t)
    for key, html in code_blocks.items():
        t = t.replace(key, html)
    return f'<div style="font-size:12px;line-height:1.5">{t}</div>'
