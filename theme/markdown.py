"""Chat bubble markdown → HTML using theme colors."""

import html as _html_mod
import re

from .palette import C_TEXT

# ── Pygments syntax highlighting ────────────────────────────
try:
    from pygments import highlight as _highlight
    from pygments.lexers import get_lexer_by_name as _get_lexer, guess_lexer as _guess_lexer
    from pygments.formatters.html import HtmlFormatter as _HtmlFormatter
    from pygments.token import Token as _Token
    _HAS_PYGMENTS = True
except ImportError:
    _HAS_PYGMENTS = False

# Token type → color for dark theme
_DARK_TOKEN_COLORS = {
    _Token.Keyword:        "#c586c0",
    _Token.Keyword.Type:   "#4ec9b0",
    _Token.Keyword.Constant: "#569cd6",
    _Token.Name.Builtin:   "#dcdcaa",
    _Token.Name.Function:  "#dcdcaa",
    _Token.Name.Class:     "#4ec9b0",
    _Token.Name.Decorator: "#dcdcaa",
    _Token.Name.Exception: "#4ec9b0",
    _Token.Name.Variable:  "#9cdcfe",
    _Token.Name.Constant:  "#4fc1ff",
    _Token.Name.Tag:       "#569cd6",
    _Token.Name.Attribute: "#9cdcfe",
    _Token.Name.Label:     "#c586c0",
    _Token.Literal.String: "#ce9178",
    _Token.Literal.String.Doc: "#ce9178",
    _Token.Literal.Number: "#b5cea8",
    _Token.Literal.String.Escape: "#d7ba7d",
    _Token.Literal.String.Regex: "#d16969",
    _Token.Comment:        "#6a9955",
    _Token.Comment.Preproc: "#c586c0",
    _Token.Operator:       "#d4d4d4",
    _Token.Punctuation:    "#d4d4d4",
    _Token.Generic.Heading: "#569cd6",
    _Token.Generic.Subheading: "#569cd6",
    _Token.Generic.Strong: "#d4d4d4",
    _Token.Generic.Emph:   "#d4d4d4",
}

# Token type → color for light theme
_LIGHT_TOKEN_COLORS = {
    _Token.Keyword:        "#af00db",
    _Token.Keyword.Type:   "#267f99",
    _Token.Keyword.Constant: "#0000ff",
    _Token.Name.Builtin:   "#795e26",
    _Token.Name.Function:  "#795e26",
    _Token.Name.Class:     "#267f99",
    _Token.Name.Decorator: "#795e26",
    _Token.Name.Exception: "#267f99",
    _Token.Name.Variable:  "#001080",
    _Token.Name.Constant:  "#0070c1",
    _Token.Name.Tag:       "#0000ff",
    _Token.Name.Attribute: "#001080",
    _Token.Label:          "#af00db",
    _Token.Literal.String: "#a31515",
    _Token.Literal.String.Doc: "#a31515",
    _Token.Literal.Number: "#098658",
    _Token.Literal.String.Escape: "#ee0000",
    _Token.Literal.String.Regex: "#811f35",
    _Token.Comment:        "#008000",
    _Token.Comment.Preproc: "#af00db",
    _Token.Operator:       "#000000",
    _Token.Punctuation:    "#000000",
    _Token.Generic.Heading: "#0000ff",
    _Token.Generic.Subheading: "#0000ff",
    _Token.Generic.Strong: "#000000",
    _Token.Generic.Emph:   "#000000",
}


def _resolve_token_color(token_type, dark_colors, light_colors, light: bool) -> str:
    """Walk up the token type hierarchy to find a matching color."""
    colors = light_colors if light else dark_colors
    tt = token_type
    while tt is not _Token:
        if tt in colors:
            return colors[tt]
        tt = tt.parent
    return ""


def _pygments_highlight(code: str, lang: str, light: bool) -> str:
    """Highlight code with Pygments, returning HTML with inline styles."""
    if not _HAS_PYGMENTS:
        # Fallback: plain escaped text
        return _html_mod.escape(code)

    try:
        lexer = _get_lexer(lang, stripall=True) if lang else _guess_lexer(code)
    except Exception:
        try:
            lexer = _guess_lexer(code)
        except Exception:
            return _html_mod.escape(code)

    tokens = lexer.get_tokens(code)
    parts = []
    for ttype, value in tokens:
        color = _resolve_token_color(ttype, _DARK_TOKEN_COLORS, _LIGHT_TOKEN_COLORS, light)
        escaped = _html_mod.escape(value)
        if color:
            parts.append(f'<span style="color:{color}">{escaped}</span>')
        else:
            parts.append(escaped)
    return "".join(parts)


def split_markdown_blocks(text: str) -> list[tuple[str, str, str]]:
    """Split markdown into ordered text/code segments."""
    src = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    parts: list[tuple[str, str, str]] = []
    last = 0
    for match in re.finditer(r"```(\w*)\n(.*?)```", src, flags=re.DOTALL):
        start, end = match.span()
        if start > last:
            parts.append(("text", src[last:start], ""))
        parts.append(("code", match.group(2) or "", (match.group(1) or "").strip()))
        last = end
    if last < len(src):
        parts.append(("text", src[last:], ""))
    return parts


def md_code_to_html(code: str, lang: str = "", light: bool = False) -> str:
    """Render highlighted code content without an outer frame."""
    code_fg = "#24292f" if light else "#e6edf3"
    highlighted = _pygments_highlight(str(code or ""), str(lang or "").strip(), light)
    return (
        '<div style="font-size:12px;line-height:1.55;margin:0;padding:0">'
        f'<pre style="margin:0;padding:0;background:transparent;'
        f'color:{code_fg};font-size:12px;line-height:1.55;'
        f'font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;'
        f'border:none;white-space:pre-wrap;word-wrap:break-word">'
        f'<code>{highlighted}</code></pre>'
        f'</div>'
    )


def md_chat_to_html(text: str, light: bool = False) -> str:
    """Convert basic markdown to HTML for QTextEdit display."""
    if light:
        pre_bg = "#f3f4f6"
        code_bg = "rgba(15,23,42,0.05)"
        code_fg = "#24292f"
        inline_code_fg = "#cf222e"
        inline_code_bd = "rgba(15,23,42,0.08)"
        header_bg = "#f3f4f6"
        header_fg = "#6b7280"
        header_border = "#dde3ea"
        card_border = "#d8dee6"
        copy_bg = "#e8edf3"
    else:
        pre_bg = "#17191d"
        code_bg = "rgba(255,255,255,0.06)"
        code_fg = "#e6edf3"
        inline_code_fg = "#f8654b"
        inline_code_bd = "rgba(255,255,255,0.10)"
        header_bg = "#17191d"
        header_fg = "#8f96a3"
        header_border = "#2f3440"
        card_border = "#2f3440"
        copy_bg = "#232831"

    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    code_idx = 0
    code_blocks: dict[str, str] = {}

    def _codeblock(m: re.Match) -> str:
        nonlocal code_idx
        lang = (m.group(1) or "").strip()
        raw_body = (m.group(2) or "").rstrip("\n")
        # Unescape HTML entities for Pygments lexing
        body = _html_mod.unescape(raw_body)
        idx = code_idx
        code_idx += 1

        # Syntax-highlighted code body
        highlighted = _pygments_highlight(body, lang, light)

        # Language badge (top-left)
        lang_display = lang.lower() if lang else "code"
        lang_badge = (
            f'<span style="font-size:10px;font-weight:600;color:{header_fg};'
            f'font-family:ui-monospace,SFMono-Regular,Consolas,monospace">'
            f'{_html_mod.escape(lang_display)}</span>'
        )

        # Copy button (top-right) – use table cell background for hover-like look
        copy_btn = (
            f'<a href="copycode:{idx}" '
            f'style="text-decoration:none;color:{header_fg};'
            f'font-size:10px;font-weight:500;'
            f'padding:0;'
            f'font-family:ui-monospace,SFMono-Regular,Consolas,monospace">'
            f'Copy</a>'
        )

        # Single table for the whole code block (Qt renders tables reliably)
        # Row 1: header bar with language badge + copy button
        # Row 2: code area spanning full width
        html = (
            f'<table cellspacing="0" cellpadding="0" '
            f'style="margin:10px 0;border:1px solid {card_border};'
            f'background:{pre_bg};width:100%">'
            f'<tr>'
            f'<td style="padding:10px 12px 10px 12px;background:{pre_bg}">'
            f'<table cellspacing="0" cellpadding="0" style="width:100%;margin:0 0 6px 0;border:none;background:transparent">'
            f'<tr>'
            f'<td style="padding:0;border:none;background:transparent">{lang_badge}</td>'
            f'<td style="padding:0;border:none;background:transparent;text-align:right">{copy_btn}</td>'
            f'</tr>'
            f'</table>'
            f'<pre style="margin:0;padding:0;background:transparent;'
            f'color:{code_fg};font-size:12px;line-height:1.55;'
            f'font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;'
            f'border:none;white-space:pre-wrap;word-wrap:break-word">'
            f'<code>{highlighted}</code></pre>'
            f'</td></tr></table>'
        )
        key = f"@@CODEBLOCK_{idx}@@"
        code_blocks[key] = html
        return key

    t = re.sub(r"```(\w*)\n(.*?)```", _codeblock, t, flags=re.DOTALL)
    t = re.sub(
        r"`([^`]+)`",
        rf'<code style="background:{code_bg};padding:1px 6px;'
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
            f'<table style="border-collapse:collapse;margin:8px 0;font-size:12px;line-height:1.5;table-layout:auto;max-width:100%">'
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
    return (
        '<div style="font-size:12px;line-height:1.5;'
        'white-space:normal;word-wrap:break-word;overflow-wrap:anywhere;'
        'max-width:100%">'
        f'{t}</div>'
    )
