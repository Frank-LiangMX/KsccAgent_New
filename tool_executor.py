"""
Tool Executor - 执行 Agent 工具调用（v2: 注册表 + 风险分级 + 安全策略）
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional


# ══════════════════════════════════════════════════════════════
#  Tool Registry 元数据
# ══════════════════════════════════════════════════════════════

class RiskLevel(Enum):
    """工具风险等级"""
    SAFE = "safe"           # 只读操作，无需确认
    LOW = "low"             # 低风险写操作（文件写入/编辑）
    MEDIUM = "medium"       # 中风险（Shell 安全命令、网页请求）
    HIGH = "high"           # 高风险（任意 Shell、删除操作）
    CRITICAL = "critical"   # 极高风险（支付、系统级操作）


class ToolCategory(Enum):
    """工具类别"""
    FILE = "file"           # 文件读写
    SHELL = "shell"         # Shell 命令
    WEB = "web"             # 网页请求
    DEVICE = "device"       # 设备操作（ADB 等）
    API = "api"             # 外部 API


@dataclass
class ToolMeta:
    """工具注册元数据"""
    name: str
    category: ToolCategory
    risk: RiskLevel
    read_only: bool = True
    needs_workspace: bool = True      # 是否需要工作区路径限制
    description: str = ""
    dangerous_patterns: list[str] = field(default_factory=list)  # 危险命令模式（Shell 用）


# ══════════════════════════════════════════════════════════════
#  工具注册表
# ══════════════════════════════════════════════════════════════

TOOL_REGISTRY: dict[str, ToolMeta] = {}


def register_tool(meta: ToolMeta):
    """注册一个工具到全局注册表"""
    TOOL_REGISTRY[meta.name] = meta
    return meta


# 内置工具注册
register_tool(ToolMeta(
    name="read_file", category=ToolCategory.FILE, risk=RiskLevel.SAFE,
    read_only=True, description="读取文件内容",
))
register_tool(ToolMeta(
    name="write_file", category=ToolCategory.FILE, risk=RiskLevel.LOW,
    read_only=False, description="写入文件",
))
register_tool(ToolMeta(
    name="edit_file", category=ToolCategory.FILE, risk=RiskLevel.LOW,
    read_only=False, description="精确编辑文件",
))
register_tool(ToolMeta(
    name="glob_files", category=ToolCategory.FILE, risk=RiskLevel.SAFE,
    read_only=True, description="按模式查找文件",
))
register_tool(ToolMeta(
    name="search_content", category=ToolCategory.FILE, risk=RiskLevel.SAFE,
    read_only=True, description="正则搜索文件内容",
))
register_tool(ToolMeta(
    name="run_shell", category=ToolCategory.SHELL, risk=RiskLevel.HIGH,
    read_only=False, needs_workspace=False,
    description="执行 Shell 命令",
    dangerous_patterns=[
        r"rm\s+-rf\s+/",       # rm -rf /
        r"mkfs\.",             # 格式化磁盘
        r"dd\s+if=",           # dd 写磁盘
        r"chmod\s+-R\s+777",   # 递归全开权限
        r":(){ :\|:& };:",     # fork bomb
        r"shutdown",           # 关机
        r"reboot",             # 重启
        r"format\s+[a-zA-Z]:", # Windows 格式化
        r"del\s+/[sfq]\s+[a-zA-Z]:\\",  # Windows 递归删除
        r"reg\s+delete",       # 注册表删除
    ],
))
register_tool(ToolMeta(
    name="list_directory", category=ToolCategory.FILE, risk=RiskLevel.SAFE,
    read_only=True, description="列出目录内容",
))
register_tool(ToolMeta(
    name="web_fetch", category=ToolCategory.WEB, risk=RiskLevel.MEDIUM,
    read_only=True, needs_workspace=False,
    description="获取网页内容",
))

# P4-4: 设备自动化（ADB）工具注册
register_tool(ToolMeta(
    name="adb_command", category=ToolCategory.DEVICE, risk=RiskLevel.HIGH,
    read_only=False, needs_workspace=False,
    description="执行 ADB 设备命令",
    dangerous_patterns=[
        r"adb\s+shell\s+rm\s+-rf\s+/",       # 设备上删除根目录
        r"adb\s+shell\s+reboot\s+recovery",    # 重启到恢复模式
        r"adb\s+shell\s+reboot\s+bootloader",  # 重启到 bootloader
        r"adb\s+shell\s+format",               # 格式化
        r"adb\s+shell\s+dd\s+",                # dd 写入
        r"adb\s+install\s+.*\.apk",            # APK 安装（需要确认）
        r"adb\s+push\s+.*\s+/system/",         # 推送到系统分区
        r"adb\s+shell\s+pm\s+uninstall",       # 卸载应用
    ],
))

# Browser automation tools (CDP Bridge)
register_tool(ToolMeta(
    name="web_scan", category=ToolCategory.WEB, risk=RiskLevel.MEDIUM,
    read_only=True, needs_workspace=False,
    description="获取浏览器页面内容和标签页列表",
))
register_tool(ToolMeta(
    name="web_execute_js", category=ToolCategory.WEB, risk=RiskLevel.HIGH,
    read_only=False, needs_workspace=False,
    description="在浏览器中执行 JavaScript 代码",
))
register_tool(ToolMeta(
    name="web_open", category=ToolCategory.WEB, risk=RiskLevel.MEDIUM,
    read_only=False, needs_workspace=False,
    description="在浏览器中打开新标签页",
))
register_tool(ToolMeta(
    name="web_launch", category=ToolCategory.WEB, risk=RiskLevel.MEDIUM,
    read_only=False, needs_workspace=False,
    description="启动 Chrome 浏览器并连接扩展",
))
register_tool(ToolMeta(
    name="skill_search", category=ToolCategory.API, risk=RiskLevel.SAFE,
    read_only=True, needs_workspace=False,
    description="搜索外部 Skill 库（105K+ 技能卡）",
))

# P4-5: 敏感任务风控模板
RISK_TEMPLATES: dict[str, dict] = {
    "payment": {
        "name": "支付/下单",
        "description": "涉及资金操作的任务",
        "keywords": ["支付", "付款", "下单", "购买", "转账", "pay", "purchase", "checkout", "order"],
        "risk_level": "critical",
        "require_reason": True,       # 必须说明操作理由
        "require_confirmation": True,  # 必须二次确认
        "block_auto_execute": True,    # 禁止自动执行
    },
    "file_deletion": {
        "name": "批量文件删除",
        "description": "删除多个文件或目录的操作",
        "keywords": ["删除所有", "批量删除", "清空", "rm -rf", "delete all", "clear all"],
        "risk_level": "high",
        "require_reason": True,
        "require_confirmation": True,
        "block_auto_execute": False,
    },
    "system_config": {
        "name": "系统配置修改",
        "description": "修改系统级配置文件",
        "keywords": ["注册表", "registry", "/etc/", "hosts", "crontab", "systemd", "环境变量"],
        "risk_level": "high",
        "require_reason": True,
        "require_confirmation": True,
        "block_auto_execute": False,
    },
    "deploy": {
        "name": "部署/发布",
        "description": "将代码部署到生产环境",
        "keywords": ["部署", "发布", "deploy", "publish", "release", "push to production"],
        "risk_level": "high",
        "require_reason": True,
        "require_confirmation": True,
        "block_auto_execute": False,
    },
}


def match_risk_template(user_prompt: str) -> Optional[dict]:
    """
    P4-5: 检测用户输入是否匹配敏感任务风控模板。
    返回匹配的模板信息，或 None。
    """
    prompt_lower = user_prompt.lower()
    matches = []
    for tid, template in RISK_TEMPLATES.items():
        for kw in template["keywords"]:
            if kw.lower() in prompt_lower:
                matches.append({"template_id": tid, **template})
                break
    if not matches:
        return None
    # 返回风险最高的模板
    risk_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "safe": 4}
    matches.sort(key=lambda m: risk_order.get(m.get("risk_level", "safe"), 99))
    return matches[0]


# ══════════════════════════════════════════════════════════════
#  Shell 命令安全过滤
# ══════════════════════════════════════════════════════════════

# 安全命令白名单（前缀匹配，可直接执行无需确认）
SAFE_SHELL_PREFIXES = [
    "git ", "dir", "ls", "cat", "head", "tail", "echo", "pwd",
    "whoami", "date", "type", "where", "which", "node ", "npm ",
    "python ", "pip ", "cargo ", "go ", "java ", "javac ",
    "dotnet ", "mvn ", "gradle", "make", "cmake", "gcc", "g++",
    "rustc", "tsc", "npx", "pnpm", "yarn", "bun",
    "curl -s", "curl --silent", "wget -q",
    "find ", "grep ", "rg ", "sort", "uniq", "wc", "diff",
    "mkdir", "touch", "cp ", "mv ",
    "code ", "notepad",
]

# 危险命令黑名单（正则匹配，直接拒绝）
DANGEROUS_SHELL_PATTERNS = TOOL_REGISTRY["run_shell"].dangerous_patterns


def classify_shell_risk(command: str) -> RiskLevel:
    """
    对 Shell 命令进行风险分级。
    - 白名单前缀 → MEDIUM（安全命令，IDE 模式可选确认）
    - 匹配危险模式 → CRITICAL（直接拒绝）
    - 其他 → HIGH（需要确认）
    """
    cmd_lower = command.strip().lower()

    # 危险模式检测
    for pat in DANGEROUS_SHELL_PATTERNS:
        if re.search(pat, cmd_lower, re.IGNORECASE):
            return RiskLevel.CRITICAL

    # 白名单前缀检测
    for prefix in SAFE_SHELL_PREFIXES:
        if cmd_lower.startswith(prefix.lower()):
            return RiskLevel.MEDIUM

    return RiskLevel.HIGH


def is_dangerous_shell(command: str) -> tuple[bool, str]:
    """检查 Shell 命令是否为危险命令。返回 (是否危险, 原因)。"""
    cmd_lower = command.strip().lower()
    for pat in DANGEROUS_SHELL_PATTERNS:
        if re.search(pat, cmd_lower, re.IGNORECASE):
            return True, f"命令匹配危险模式: {pat}"
    return False, ""


# ══════════════════════════════════════════════════════════════
#  ToolExecutor
# ══════════════════════════════════════════════════════════════

class ToolExecutor:
    def __init__(self, workspace: str, config=None):
        self.workspace = Path(workspace).resolve()
        self._config = config

    def _safe_path(self, path: str) -> Path:
        """Resolve path safely against workspace, enforce boundary."""
        p = Path(path)
        if not p.is_absolute():
            p = self.workspace / p
        p = p.resolve()
        # 安全检查：确保路径在工作区内（允许工作区自身的父目录不存在的情况）
        try:
            p.relative_to(self.workspace)
        except ValueError:
            raise PermissionError(
                f"路径越界: {p} 不在工作区 {self.workspace} 内。"
                f"禁止访问工作区外的文件。"
            )
        return p

    def get_meta(self, name: str) -> Optional[ToolMeta]:
        """获取工具元数据"""
        return TOOL_REGISTRY.get(name)

    def execute(self, name: str, args: dict) -> str:
        try:
            meta = TOOL_REGISTRY.get(name)
            if meta is None:
                return f"Unknown tool: {name}"

            # 安全检查：Shell/ADB 危险命令拦截
            if name == "run_shell":
                dangerous, reason = is_dangerous_shell(args.get("command", ""))
                if dangerous:
                    return f"ToolError: 命令被安全策略拦截。{reason}"
            elif name == "adb_command":
                dangerous, reason = self._is_dangerous_adb(args.get("command", ""))
                if dangerous:
                    return f"ToolError: ADB 命令被安全策略拦截。{reason}"

            dispatch = {
                "read_file": self._read_file,
                "write_file": self._write_file,
                "edit_file": self._edit_file,
                "glob_files": self._glob_files,
                "search_content": self._search_content,
                "run_shell": self._run_shell,
                "list_directory": self._list_directory,
                "web_fetch": self._web_fetch,
            }
            # ADB gated behind feature flag
            if name == "adb_command":
                if not (getattr(self._config, 'feature_adb_tools', False) if self._config else False):
                    return "ToolError: ADB 工具已禁用。请在设置 > Agent > 实验特性中开启。"
                dispatch["adb_command"] = self._adb_command
            # Browser tools gated behind feature flag
            if name in ("web_scan", "web_execute_js", "web_open", "web_launch"):
                if not (getattr(self._config, 'feature_browser_tools', False) if self._config else False):
                    return "ToolError: 浏览器工具已禁用。请在设置 > 扩展中开启。"
                dispatch["web_scan"] = self._web_scan
                dispatch["web_execute_js"] = self._web_execute_js
                dispatch["web_open"] = self._web_open
                dispatch["web_launch"] = self._web_launch
            # Skill search tool (no feature gate)
            if name == "skill_search":
                dispatch["skill_search"] = self._skill_search
            handler = dispatch.get(name)
            if handler:
                return handler(args)
            return f"Tool handler not implemented: {name}"
        except PermissionError as e:
            return f"ToolError: 安全拦截 - {e}"
        except Exception as e:
            return f"ToolError: {type(e).__name__}: {e}"

    def preview(self, name: str, args: dict) -> str:
        """Generate a human-readable preview for IDE mode."""
        if name == "read_file":
            return f"Read: {self._shorten(args.get('path', ''), 60)}"
        elif name == "write_file":
            return f"Write: {self._shorten(args.get('path', ''), 60)} ({len(args.get('content', ''))} chars)"
        elif name == "edit_file":
            p = self._shorten(args.get('path', ''), 50)
            old = self._shorten(args.get('old_string', ''), 40)
            return f"Edit: {p}\n  - {old}"
        elif name == "glob_files":
            return f"Glob: {args.get('pattern', '')}"
        elif name == "search_content":
            return f"Search: /{self._shorten(args.get('pattern', ''), 40)}/"
        elif name == "run_shell":
            cmd = args.get('command', '')
            risk = classify_shell_risk(cmd)
            tag = {"safe": "[safe]", "medium": "[ok]", "high": "[!]", "critical": "[BLOCK]"}.get(risk.value, "")
            return f"Shell {tag}: {self._shorten(cmd, 80)}"
        elif name == "list_directory":
            return f"List: {args.get('path') or 'workspace root'}"
        elif name == "web_fetch":
            return f"Fetch: {args.get('url', '')}"
        elif name == "web_scan":
            tabs_only = args.get('tabs_only', False)
            return f"Web Scan: {'标签页列表' if tabs_only else '获取页面内容'}"
        elif name == "web_execute_js":
            script = args.get('script', '')
            return f"Execute JS: {self._shorten(script, 80)}"
        elif name == "web_open":
            return f"Open: {args.get('url', 'about:blank')}"
        elif name == "web_launch":
            url = args.get('url')
            return f"Launch Chrome{' → ' + url if url else ''}"
        elif name == "skill_search":
            return f"Skill Search: {args.get('query', '')}"
        return f"{name}: {json.dumps(args, ensure_ascii=False)[:100]}"

    # ── 工具实现 ──

    def _read_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])
        offset = args.get("offset", 0)
        limit = args.get("limit") or 2000
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        if offset > 0:
            lines = lines[offset - 1:]
        if limit:
            lines = lines[:limit]
        result = "".join(lines)
        header = f"[{path}] {len(lines)}/{total} lines"
        if offset:
            header += f" (offset={offset})"
        return f"{header}\n\n{result}"

    def _write_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])
        content = args["content"]
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Wrote {len(content)} bytes to {path}"

    def _edit_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])
        old_str = args["old_string"]
        new_str = args["new_string"]
        content = path.read_text("utf-8")
        count = content.count(old_str)
        if count == 0:
            return f"Error: old_string not found in {path}"
        if count > 1:
            return f"Error: old_string found {count} times in {path}, must be unique. Provide more context."
        new_content = content.replace(old_str, new_str, 1)
        path.write_text(new_content, "utf-8")
        return f"Edited {path} (1 replacement)"

    def _glob_files(self, args: dict) -> str:
        pattern = args["pattern"]
        base = self._safe_path(args.get("path") or str(self.workspace))
        matches = sorted(base.glob(pattern))
        if not matches:
            return f"No files matched pattern: {pattern}"
        lines = [str(m.relative_to(base) if m.is_relative_to(base) else m) for m in matches]
        return f"Matched {len(matches)} files:\n" + "\n".join(lines[:200])

    def _search_content(self, args: dict) -> str:
        pattern = args["pattern"]
        base = self._safe_path(args.get("path") or str(self.workspace))
        include = args.get("include") or "*"
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"Invalid regex pattern: {e}"
        results = []
        for f in base.rglob(include):
            if not f.is_file():
                continue
            if any(p.startswith('.') for p in f.relative_to(base).parts):
                continue
            try:
                content = f.read_text("utf-8", errors="replace")
                for i, line in enumerate(content.splitlines(), 1):
                    if regex.search(line):
                        results.append(f"{f.relative_to(base)}:{i}: {line.strip()[:200]}")
            except Exception:
                continue
        if not results:
            return f"No matches for pattern: {pattern}"
        return f"Found {len(results)} matches:\n" + "\n".join(results[:100])

    def _run_shell(self, args: dict) -> str:
        command = args["command"]
        workdir = args.get("workdir") or str(self.workspace)
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                cwd=workdir, timeout=120, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            out = result.stdout.strip()
            err = result.stderr.strip()
            parts = []
            if out:
                parts.append(out)
            if err:
                parts.append(f"[stderr]\n{err}")
            if result.returncode != 0:
                parts.append(f"[exit code: {result.returncode}]")
            return "\n".join(parts) if parts else f"[exit code: {result.returncode}]"
        except subprocess.TimeoutExpired:
            return "Error: command timed out (120s)"
        except Exception as e:
            return f"Error executing command: {e}"

    def _list_directory(self, args: dict) -> str:
        path = self._safe_path(args.get("path") or str(self.workspace))
        if not path.exists():
            return f"Path not found: {path}"
        if not path.is_dir():
            return f"Not a directory: {path}"
        entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        lines = []
        for e in entries[:200]:
            marker = "/" if e.is_dir() else ""
            lines.append(f"  {e.name}{marker}")
        heading = f"{path}" + ("/" if lines else " (empty)")
        return heading + "\n" + "\n".join(lines) if lines else heading

    def _web_fetch(self, args: dict) -> str:
        url = args["url"]
        import httpx
        try:
            r = httpx.get(url, timeout=15, follow_redirects=True, headers={
                "User-Agent": "KsccAgent/1.0"
            })
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            if "text/html" in ct:
                text = self._html_to_text(r.text)
            else:
                text = r.text
            # P4-3: 证据采集 — 保存抓取结果到 evidence 目录
            if getattr(self._config, 'feature_evidence_capture', True) if self._config else True:
                self._save_web_evidence(url, text, ct, r.status_code)
            return text[:10000] + ("..." if len(text) > 10000 else "")
        except Exception as e:
            return f"WebFetch Error: {e}"

    def _save_web_evidence(self, url: str, content: str, content_type: str, status_code: int):
        """P4-3: 将网页抓取结果保存为本地证据文件。"""
        from datetime import datetime, timezone
        try:
            evidence_dir = self.workspace / "evidence" / "web"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            # 用 URL 的域名作为文件名一部分
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.replace(":", "_")[:40]
            fname = f"{ts}_{domain}.json"
            evidence = {
                "url": url,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status_code": status_code,
                "content_type": content_type,
                "content_length": len(content),
                "content_preview": content[:2000],
            }
            (evidence_dir / fname).write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2), "utf-8"
            )
        except Exception:
            pass  # 证据保存失败不影响主流程

    # ── Browser CDP tools ──

    def _web_scan(self, args: dict) -> str:
        """Get browser page content or tab list via CDP Bridge."""
        from browser_driver import get_browser_driver
        driver = get_browser_driver()
        if not driver.is_running:
            return "ToolError: 浏览器驱动未启动。请在设置 > 扩展中启用浏览器工具。"
        if not driver.has_connection:
            return "ToolError: 没有已连接的浏览器扩展。请确认 CDP Bridge 扩展已安装并启用。"
        if not driver.has_tabs:
            return "ToolError: 扩展已连接，但没有可用的浏览器标签页。请在 Chrome 中打开一个网页（非 chrome:// 页面），然后重试。"

        tabs_only = args.get('tabs_only', False)
        switch_tab_id = args.get('switch_tab_id', '')
        text_only = args.get('text_only', False)

        # Switch tab if requested
        if switch_tab_id:
            if not driver.set_default_session(switch_tab_id):
                return f"ToolError: 无法切换到标签页 {switch_tab_id}"

        # Tab list
        tabs = driver.connected_tabs
        if tabs_only:
            return json.dumps(tabs, ensure_ascii=False, indent=2)

        # Get page content via JS
        if text_only:
            js_code = "document.body.innerText"
        else:
            js_code = (
                "(() => {"
                "  function simplify(el, depth) {"
                "    if (!el || depth > 15) return '';"
                "    if (el.nodeType === 3) return el.textContent.trim();"
                "    if (el.nodeType !== 1) return '';"
                "    const tag = el.tagName.toLowerCase();"
                "    if (['script','style','noscript','svg','path'].includes(tag)) return '';"
                "    const style = getComputedStyle(el);"
                "    if (style.display === 'none' || style.visibility === 'hidden') return '';"
                "    const rect = el.getBoundingClientRect();"
                "    if (rect.width === 0 && rect.height === 0) return '';"
                "    const attrs = [];"
                "    if (el.id) attrs.push('id=' + el.id);"
                "    if (el.className && typeof el.className === 'string') attrs.push('class=' + el.className.trim().substring(0, 60));"
                "    if (tag === 'a' && el.href) attrs.push('href=' + el.href);"
                "    if (tag === 'img' && el.src) attrs.push('src=' + el.src);"
                "    if (tag === 'input') { attrs.push('type=' + (el.type||'text')); if(el.value) attrs.push('value=' + el.value.substring(0,50)); }"
                "    const attrStr = attrs.length ? ' ' + attrs.join(' ') : '';"
                "    const children = Array.from(el.childNodes).map(c => simplify(c, depth+1)).filter(Boolean).join('');"
                "    if (!children && !el.textContent.trim()) return '';"
                "    const blockTags = ['div','p','h1','h2','h3','h4','h5','h6','li','tr','section','article','nav','header','footer','main','form','table','ul','ol','select','textarea','button','label','fieldset','details','summary'];"
                "    if (blockTags.includes(tag)) return '<' + tag + attrStr + '>\\n' + children + '\\n</' + tag + '>\\n';"
                "    return '<' + tag + attrStr + '>' + children + '</' + tag + '>';"
                "  }"
                "  return simplify(document.body, 0);"
                "})()"
            )
        try:
            result = driver.execute_js(js_code, timeout=15)
            content = result.get('data', '')
            if isinstance(content, str) and len(content) > 15000:
                content = content[:15000] + "\n... [truncated]"
            tab_info = ""
            if driver.default_session_id and driver.default_session_id in driver.sessions:
                tab_info = f"[Tab: {driver.sessions[driver.default_session_id].title}]\n"
            return tab_info + str(content)
        except Exception as e:
            return f"ToolError: web_scan 失败: {e}"

    def _web_execute_js(self, args: dict) -> str:
        """Execute JavaScript in the browser via CDP Bridge."""
        from browser_driver import get_browser_driver
        driver = get_browser_driver()
        if not driver.is_running:
            return "ToolError: 浏览器驱动未启动。请在设置 > 扩展中启用浏览器工具。"
        if not driver.has_connection:
            return "ToolError: 没有已连接的浏览器扩展。请确认 CDP Bridge 扩展已安装并启用。"
        if not driver.has_tabs:
            return "ToolError: 扩展已连接，但没有可用的浏览器标签页。请在 Chrome 中打开一个网页（非 chrome:// 页面），然后重试。"

        script = args.get('script', '')
        switch_tab_id = args.get('switch_tab_id', '')
        no_monitor = args.get('no_monitor', False)
        save_to_file = args.get('save_to_file', '')

        if switch_tab_id:
            if not driver.set_default_session(switch_tab_id):
                return f"ToolError: 无法切换到标签页 {switch_tab_id}"

        try:
            result = driver.execute_js(script, timeout=15)
            data = result.get('data')
            # Save to file if requested
            if save_to_file and data:
                save_path = self._safe_path(save_to_file)
                save_path.parent.mkdir(parents=True, exist_ok=True)
                content = json.dumps(data, ensure_ascii=False, indent=2) if not isinstance(data, str) else data
                save_path.write_text(content, 'utf-8')
                return f"Result saved to {save_path}\n{str(data)[:5000]}"
            result_str = json.dumps(data, ensure_ascii=False, indent=2) if not isinstance(data, str) else str(data)
            if len(result_str) > 10000:
                result_str = result_str[:10000] + "\n... [truncated]"
            # Include new tabs info if any
            new_tabs = result.get('newTabs', [])
            if new_tabs:
                result_str += f"\n\nNew tabs opened: {json.dumps(new_tabs, ensure_ascii=False)}"
            return result_str
        except TimeoutError as e:
            return f"ToolError: JS 执行超时: {e}"
        except RuntimeError as e:
            return f"ToolError: JS 执行失败: {e}"

    def _web_launch(self, args: dict) -> str:
        """Launch Chrome browser and wait for extension connection."""
        from browser_driver import get_browser_driver
        driver = get_browser_driver()
        if not driver.is_running:
            return "ToolError: 浏览器驱动未启动。请在设置 > 扩展中启用浏览器工具。"

        url = args.get('url')
        if url and not url.startswith(('http://', 'https://', 'file://', 'about:')):
            url = 'https://' + url
        timeout = args.get('timeout', 30)

        try:
            result = driver.launch_browser(url=url, timeout=timeout)
            status = "Chrome 已启动" if not result.get('already_running') else "Chrome 已在运行"
            tabs = result.get('tabs', [])
            if url and tabs:
                return f"{status}，扩展已连接。已打开: {url}"
            elif tabs:
                return f"{status}，扩展已连接。当前有 {len(tabs)} 个标签页。"
            else:
                return f"{status}，扩展已连接。"
        except TimeoutError as e:
            return f"ToolError: {e}"
        except RuntimeError as e:
            return f"ToolError: {e}"

    def _web_open(self, args: dict) -> str:
        """Open a new tab in the user's browser via CDP Bridge."""
        from browser_driver import get_browser_driver
        driver = get_browser_driver()
        if not driver.is_running:
            return "ToolError: 浏览器驱动未启动。请在设置 > 扩展中启用浏览器工具。"
        if not driver.has_connection:
            return "ToolError: 没有已连接的浏览器扩展。请确认 CDP Bridge 扩展已安装并启用。"

        url = args.get('url', 'about:blank')
        if not url.startswith(('http://', 'https://', 'file://', 'about:')):
            url = 'https://' + url
        try:
            tab = driver.open_tab(url, timeout=15)
            return json.dumps(tab, ensure_ascii=False)
        except Exception as e:
            return f"ToolError: 打开标签页失败: {e}"

    # ── P4-4: ADB 设备自动化 ──

    def _is_dangerous_adb(self, command: str) -> tuple[bool, str]:
        """检查 ADB 命令是否为危险命令。"""
        meta = TOOL_REGISTRY.get("adb_command")
        if meta:
            for pat in meta.dangerous_patterns:
                if re.search(pat, command, re.IGNORECASE):
                    return True, f"ADB 命令匹配危险模式: {pat}"
        return False, ""

    def _adb_command(self, args: dict) -> str:
        """
        P4-4: 执行 ADB 设备命令（隔离沙箱）。
        ADB 命令通过 subprocess 执行，与普通 Shell 隔离。
        """
        command = args["command"]
        device = args.get("device", "")  # 可选：指定设备序列号
        # 构建完整 ADB 命令
        adb_cmd = "adb"
        if device:
            adb_cmd = f"adb -s {device}"
        full_cmd = f"{adb_cmd} {command}"
        try:
            result = subprocess.run(
                full_cmd, shell=True, capture_output=True, text=True,
                timeout=30, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            out = result.stdout.strip()
            err = result.stderr.strip()
            parts = []
            if out:
                parts.append(out)
            if err:
                parts.append(f"[stderr]\n{err}")
            if result.returncode != 0:
                parts.append(f"[exit code: {result.returncode}]")
            # P4-4: 保存设备操作日志
            if getattr(self._config, 'feature_evidence_capture', True) if self._config else True:
                self._save_device_evidence(full_cmd, result.returncode, out, err)
            return "\n".join(parts) if parts else f"[exit code: {result.returncode}]"
        except FileNotFoundError:
            return "ToolError: adb 未安装或不在 PATH 中。请先安装 Android SDK Platform Tools。"
        except subprocess.TimeoutExpired:
            return "Error: ADB 命令超时 (30s)"
        except Exception as e:
            return f"Error executing ADB command: {e}"

    def _save_device_evidence(self, command: str, exit_code: int, stdout: str, stderr: str):
        """P4-4: 保存设备操作日志到 evidence 目录。"""
        from datetime import datetime, timezone
        try:
            evidence_dir = self.workspace / "evidence" / "device"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            evidence = {
                "command": command,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "exit_code": exit_code,
                "stdout_preview": stdout[:2000],
                "stderr_preview": stderr[:1000],
            }
            (evidence_dir / f"{ts}_adb.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2), "utf-8"
            )
        except Exception:
            pass

    def _skill_search(self, args: dict) -> str:
        """搜索外部 Skill 库（105K+ 技能卡）。"""
        try:
            from skill_search import search, format_results_text, SkillSearchError
        except ImportError:
            return "ToolError: skill_search 模块未安装。"
        query = args.get("query", "")
        if not query:
            return "ToolError: query 参数不能为空。"
        category = args.get("category")
        top_k = args.get("top_k", 5)
        try:
            results = search(query, category=category, top_k=top_k)
            return format_results_text(results, query)
        except SkillSearchError as e:
            return f"ToolError: Skill 搜索失败 - {e}"
        except Exception as e:
            return f"ToolError: Skill 搜索异常 - {e}"

    @staticmethod
    def _html_to_text(html_text: str) -> str:
        text = re.sub(r'<script[^>]*>.*?</script>', '', html_text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'&[a-zA-Z]+;', ' ', text)
        text = re.sub(r'&#\d+;', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:8000]

    @staticmethod
    def _shorten(text: str, max_len: int) -> str:
        if len(text) <= max_len:
            return text
        return text[:max_len - 3] + "..."
