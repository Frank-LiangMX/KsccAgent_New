"""
Kscc Agent - AI 编码 Agent 核心

特性:
- OpenAI / Anthropic 双后端
- Solo 模式 (自主执行) / IDE 模式 (每步确认)
- 流式输出 + Tool Calling
- 文件读写、代码搜索、Shell 执行
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import AsyncGenerator, Callable, Optional, Union

import httpx

from config import Config, ProviderConfig, load_config, get_active_provider
from context import ContextTracker, count_messages_tokens, count_tokens, get_max_output
import memory_store
from skill_manager import Skill, SkillManager, SkillMatchResult, skill_debug_log

# ══════════════════════════════════════════════════════════════
#  System Prompt
# ══════════════════════════════════════════════════════════════

_MODE_DESCRIPTIONS = {
    "solo": "You work autonomously. Execute tools directly, verify your work, and report results. Keep iterating until the task is complete.",
    "ide": "You propose changes for user review. When you want to modify a file, explain what you plan to do and call the tool. The user will approve or reject each action. Use read_file and search tools freely without confirmation.",
}

SYSTEM_PROMPT = """You are Kscc, an AI coding assistant that helps users with software engineering tasks.

## Your Capabilities
- Read, write, and edit files in the workspace
- Execute shell commands
- Search code with regex patterns
- Find files by glob patterns
- List directory contents

## Guidelines
- Be concise and direct. Answer the user's question without unnecessary preamble.
- When making file changes, use edit_file for targeted edits rather than rewriting entire files.
- Read files before editing them to understand their content and conventions.
- Follow existing code conventions (naming, imports, patterns) when making changes.
- NEVER guess URLs or file paths - use search tools to find them first.
- When executing shell commands, prefer non-interactive commands.
- After completing a task, verify your work by reading the changed file or running tests.

## Mode
{mode_description}

## Workspace
The current working directory is: {workspace}
Use relative paths from this directory when possible."""


# ══════════════════════════════════════════════════════════════
#  Tool Definitions
# ══════════════════════════════════════════════════════════════

TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Reads a file from the local filesystem. Use offset and limit to read specific sections of large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative path to the file"},
                    "offset": {"type": "integer", "description": "Line number to start reading from (1-indexed)"},
                    "limit": {"type": "integer", "description": "Maximum number of lines to read"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Writes content to a file, creating it if it doesn't exist. Overwrites existing files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative path to the file"},
                    "content": {"type": "string", "description": "Content to write to the file"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Performs exact string replacement in a file. The old_string must match exactly once in the file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative path to the file"},
                    "old_string": {"type": "string", "description": "The exact text to replace"},
                    "new_string": {"type": "string", "description": "The replacement text"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob_files",
            "description": "Finds files matching a glob pattern (e.g., 'src/**/*.py', '*.json').",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern to match"},
                    "path": {"type": "string", "description": "Directory to search in (default: workspace root)"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_content",
            "description": "Searches file contents using a regular expression pattern. Returns matching file paths and line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "Directory to search in (default: workspace root)"},
                    "include": {"type": "string", "description": "File pattern to filter (e.g., '*.py', '*.{ts,tsx}')"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Executes a shell command in the workspace directory. Returns stdout and stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute"},
                    "workdir": {"type": "string", "description": "Working directory for the command"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "Lists files and subdirectories in a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the directory (default: workspace root)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetches content from a URL and returns as markdown/text. Use for reading documentation, API docs, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                },
                "required": ["url"],
            },
        },
    },
]

TOOLS_ANTHROPIC = [
    {
        "name": "read_file",
        "description": "Reads a file from the local filesystem. Use offset and limit to read specific sections of large files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative path to the file"},
                "offset": {"type": "integer", "description": "Line number to start reading from (1-indexed)"},
                "limit": {"type": "integer", "description": "Maximum number of lines to read"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Writes content to a file, creating it if it doesn't exist. Overwrites existing files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative path to the file"},
                "content": {"type": "string", "description": "Content to write to the file"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Performs exact string replacement in a file. The old_string must match exactly once in the file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative path to the file"},
                "old_string": {"type": "string", "description": "The exact text to replace"},
                "new_string": {"type": "string", "description": "The replacement text"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "glob_files",
        "description": "Finds files matching a glob pattern (e.g., 'src/**/*.py', '*.json').",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern to match"},
                "path": {"type": "string", "description": "Directory to search in (default: workspace root)"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "search_content",
        "description": "Searches file contents using a regular expression pattern. Returns matching file paths and line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "Directory to search in (default: workspace root)"},
                "include": {"type": "string", "description": "File pattern to filter (e.g., '*.py', '*.{ts,tsx}')"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "run_shell",
        "description": "Executes a shell command in the workspace directory. Returns stdout and stderr.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "workdir": {"type": "string", "description": "Working directory for the command"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "list_directory",
        "description": "Lists files and subdirectories in a directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the directory (default: workspace root)"},
            },
            "required": [],
        },
    },
    {
        "name": "web_fetch",
        "description": "Fetches content from a URL and returns as markdown/text. Use for reading documentation, API docs, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
            },
            "required": ["url"],
        },
    },
]

TOOL_NAMES = [t["function"]["name"] for t in TOOLS_OPENAI]


# ══════════════════════════════════════════════════════════════
#  Tool Executor
# ══════════════════════════════════════════════════════════════

class ToolExecutor:
    def __init__(self, workspace: str):
        self.workspace = Path(workspace).resolve()

    def _safe_path(self, path: str) -> Path:
        """Resolve path safely against workspace, prevent traversal."""
        p = Path(path)
        if not p.is_absolute():
            p = self.workspace / p
        p = p.resolve()
        # 允许读取 workspace 外的文件（如系统配置），但给出警告
        return p

    def execute(self, name: str, args: dict) -> str:
        try:
            if name == "read_file":
                return self._read_file(args)
            elif name == "write_file":
                return self._write_file(args)
            elif name == "edit_file":
                return self._edit_file(args)
            elif name == "glob_files":
                return self._glob_files(args)
            elif name == "search_content":
                return self._search_content(args)
            elif name == "run_shell":
                return self._run_shell(args)
            elif name == "list_directory":
                return self._list_directory(args)
            elif name == "web_fetch":
                return self._web_fetch(args)
            else:
                return f"Unknown tool: {name}"
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
            return f"Shell: {self._shorten(args.get('command', ''), 80)}"
        elif name == "list_directory":
            return f"List: {args.get('path') or 'workspace root'}"
        return f"{name}: {json.dumps(args, ensure_ascii=False)[:100]}"

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
            return text[:10000] + ("..." if len(text) > 10000 else "")
        except Exception as e:
            return f"WebFetch Error: {e}"

    @staticmethod
    def _html_to_text(html_text: str) -> str:
        import re
        text = re.sub(r'<script[^>]*>.*?</script>', '', html_text, flags=re.DOTALL|re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL|re.IGNORECASE)
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


# ══════════════════════════════════════════════════════════════
#  LLM 后端
# ══════════════════════════════════════════════════════════════

class OpenAIBackend:
    def __init__(self, provider: ProviderConfig):
        self.base_url = provider.base_url.rstrip("/")
        self.model = provider.model
        self.headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
            **(provider.extra_headers or {}),
        }

    async def chat(self, messages: list[dict], tools: list[dict], max_tokens: int = 16384) -> AsyncGenerator[dict, None]:
        """流式调用 OpenAI-compatible API，yield 统一事件。"""
        body = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": max_tokens,
        }
        url = f"{self.base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=httpx.Timeout(300)) as client:
            async with client.stream("POST", url, json=body, headers=self.headers) as resp:
                if resp.status_code != 200:
                    text = await resp.aread()
                    yield {"type": "error", "content": f"API Error {resp.status_code}: {text.decode()[:500]}"}
                    return
                tool_calls: dict[int, dict] = {}  # index -> accumulated tool call
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    # 捕获 usage 信息
                    usage = chunk.get("usage")
                    if usage:
                        yield {"type": "usage", "input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)}
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    if "reasoning_content" in delta and delta["reasoning_content"]:
                        yield {"type": "thinking", "text": delta["reasoning_content"]}
                    if "content" in delta and delta["content"]:
                        yield {"type": "text_delta", "text": delta["content"]}
                    for tc in delta.get("tool_calls", []):
                        idx = tc.get("index", 0)
                        if idx not in tool_calls:
                            tool_calls[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                        tc_obj = tool_calls[idx]
                        if "id" in tc:
                            tc_obj["id"] = tc["id"]
                        if "function" in tc:
                            if "name" in tc["function"]:
                                tc_obj["function"]["name"] += tc["function"]["name"]
                            if "arguments" in tc["function"]:
                                tc_obj["function"]["arguments"] += tc["function"]["arguments"]
                        yield {"type": "tool_delta", "index": idx, "name": tc_obj["function"]["name"], "arguments": tc_obj["function"]["arguments"]}
                for tc in tool_calls.values():
                    yield {"type": "tool_call_complete", "tool_call": tc}


class AnthropicBackend:
    def __init__(self, provider: ProviderConfig):
        self.base_url = provider.base_url.rstrip("/")
        self.model = provider.model
        self.headers = {
            "x-api-key": provider.api_key,
            "anthropic-version": provider.extra_headers.get("anthropic-version", "2023-06-01"),
            "Content-Type": "application/json",
        }

    async def chat(self, messages: list[dict], tools: list[dict], max_tokens: int = 16384) -> AsyncGenerator[dict, None]:
        """流式调用 Anthropic API，yield 统一事件。"""
        system = ""
        anthropic_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system += msg["content"] + "\n"
            elif msg["role"] == "tool":
                converted = self._convert_message(msg)
                if anthropic_messages and anthropic_messages[-1]["role"] == "user":
                    anthropic_messages[-1]["content"].extend(converted["content"])
                else:
                    anthropic_messages.append(converted)
            else:
                anthropic_messages.append(self._convert_message(msg))

        body = {
            "model": self.model,
            "messages": anthropic_messages,
            "tools": tools,
            "stream": True,
            "max_tokens": max_tokens,
        }
        if system.strip():
            body["system"] = system.strip()

        url = f"{self.base_url}/messages"
        async with httpx.AsyncClient(timeout=httpx.Timeout(300)) as client:
            async with client.stream("POST", url, json=body, headers=self.headers) as resp:
                if resp.status_code != 200:
                    text = await resp.aread()
                    yield {"type": "error", "content": f"API Error {resp.status_code}: {text.decode()[:500]}"}
                    return
                current_event = None
                tool_blocks: dict[int, dict] = {}  # index -> accumulated tool use
                async for line in resp.aiter_lines():
                    if line.startswith("event: "):
                        current_event = line[7:]
                    elif line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        for e in self._process_sse(current_event, data, tool_blocks):
                            yield e

    def _process_sse(self, event: str, data: dict, tool_blocks: dict) -> list[dict]:
        events = []
        t = data.get("type", "")
        if t == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                events.append({"type": "text_delta", "text": delta.get("text", "")})
            elif delta.get("type") == "input_json_delta":
                idx = data.get("index", 0)
                if idx not in tool_blocks:
                    tool_blocks[idx] = {"id": "", "name": "", "input_json": ""}
                tool_blocks[idx]["input_json"] += delta.get("partial_json", "")
                events.append({"type": "tool_delta", "index": idx, "name": tool_blocks[idx]["name"], "arguments": tool_blocks[idx]["input_json"]})
        elif t == "content_block_start":
            cb = data.get("content_block", {})
            if cb.get("type") == "tool_use":
                idx = data.get("index", 0)
                tool_blocks[idx] = {"id": cb.get("id", ""), "name": cb.get("name", ""), "input_json": json.dumps(cb.get("input", {}))}
        elif t == "message_delta":
            usage = data.get("usage", {})
            if usage:
                return [{"type": "usage", "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)}]
        elif t == "message_stop":
            # 所有 tool_blocks 已完成
            for idx, tb in tool_blocks.items():
                try:
                    args = tb["input_json"]
                except Exception:
                    args = "{}"
                events.append({
                    "type": "tool_call_complete",
                    "tool_call": {
                        "id": tb["id"],
                        "type": "function",
                        "function": {"name": tb["name"], "arguments": args if isinstance(args, str) else json.dumps(args)},
                    },
                })
        return [e for e in events if e]

    def _convert_message(self, msg: dict) -> dict:
        role = msg["role"]
        if role == "user":
            return {"role": "user", "content": [{"type": "text", "text": msg.get("content", "")}]}
        elif role == "tool":
            return {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": msg.get("tool_call_id", ""), "content": msg.get("content", "")}
            ]}
        elif role == "assistant":
            if msg.get("tool_calls"):
                content = []
                for tc in msg["tool_calls"]:
                    content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "input": json.loads(tc["function"]["arguments"]),
                    })
                return {"role": "assistant", "content": content}
            else:
                return {"role": "assistant", "content": [{"type": "text", "text": msg.get("content", "")}]}
        return msg


def create_backend(provider: ProviderConfig):
    if provider.name == "kscc":
        return KsccBackend(provider)
    if provider.name == "anthropic":
        return AnthropicBackend(provider)
    return OpenAIBackend(provider)


# ══════════════════════════════════════════════════════════════
#  Kscc Backend（公司内部 kscc CLI，stream-json 模式）
# ══════════════════════════════════════════════════════════════

class KsccBackend:
    def __init__(self, provider: ProviderConfig):
        self.bin = self._find_bin(provider.base_url)
        self.model = provider.model or ""
        self.workspace = str(Path.cwd())
        self._proc = None

    @staticmethod
    def _find_bin(hint: str) -> str:
        """找到 kscc 可执行文件路径"""
        import shutil
        for name in (hint, "kscc", "kscc.cmd", "kscc.ps1"):
            found = shutil.which(name)
            if found:
                return found
        # 查找 npm 全局安装路径
        npm_dir = Path(os.environ.get("APPDATA", "")) / "npm"
        for name in ("kscc.cmd", "kscc.ps1", "kscc"):
            p = npm_dir / name
            if p.exists():
                return str(p)
        return hint

    async def _ensure_process(self):
        if self._proc and self._proc.returncode is None:
            return
        import asyncio
        cmd = [
            self.bin, "--print", "--verbose",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--dangerously-skip-permissions",
        ]
        if self.model and self.model != "default":
            cmd += ["--model", self.model]
        env = os.environ.copy()
        api_key = os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        if api_key:
            env["ANTHROPIC_API_KEY"] = api_key
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.workspace,
            env=env,
            creationflags=creationflags,
        )
        self._proc.stdout._limit = 50 * 1024 * 1024

    async def _send(self, data: dict):
        line = json.dumps(data, ensure_ascii=False) + "\n"
        self._proc.stdin.write(line.encode("utf-8"))
        await self._proc.stdin.drain()

    async def _read_line(self) -> Optional[dict]:
        if not self._proc or self._proc.returncode is not None:
            return None
        try:
            line = await self._proc.stdout.readline()
        except Exception:
            return None
        if not line:
            return None
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _msg_text(content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                else:
                    parts.append(str(block))
            return "\n".join([p for p in parts if p])
        return str(content or "")

    def _build_transcript(self, messages: list[dict], latest_user_text: str) -> str:
        """
        kscc CLI stream-json --print currently doesn't accept OpenAI-style message arrays.
        We inject a compact transcript to preserve local context without relying on process persistence.
        """
        transcript = []
        for m in messages[-18:]:
            role = m.get("role", "")
            if role == "system":
                continue
            txt = self._msg_text(m.get("content", ""))
            if not txt.strip():
                continue
            if role == "user":
                transcript.append(f"[User]\n{txt.strip()}")
            elif role == "assistant":
                transcript.append(f"[Assistant]\n{txt.strip()}")
            elif role == "tool":
                transcript.append(f"[ToolResult]\n{txt.strip()}")
        if not transcript:
            return latest_user_text
        merged = "\n\n".join(transcript)
        if len(merged) > 18000:
            merged = merged[-18000:]
        return (
            "Conversation transcript:\n"
            f"{merged}\n\n"
            "Continue the conversation and answer the latest user message."
        )

    async def chat(self, messages: list[dict], tools: list[dict], max_tokens: int = 16384) -> AsyncGenerator[dict, None]:
        await self._ensure_process()

        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_text = str(m.get("content", ""))
                break
        if not user_text:
            yield {"type": "error", "content": "No user message found"}
            return

        kscc_input = self._build_transcript(messages, user_text)
        await self._send({"type": "user", "message": {"role": "user", "content": kscc_input}})

        acc_text = ""
        tool_calls = {}
        try:
            while True:
                msg = await self._read_line()
                if msg is None:
                    yield {"type": "error", "content": "kscc process ended unexpectedly"}
                    return

                t = msg.get("type", "")
                if t == "assistant":
                    blocks = msg.get("message", {}).get("content", [])
                    for block in blocks:
                        if not isinstance(block, dict):
                            continue
                        bt = block.get("type", "")
                        if bt == "text":
                            text = block.get("text", "")
                            acc_text += text
                            yield {"type": "text_delta", "text": text}
                        elif bt == "thinking":
                            yield {"type": "thinking", "text": block.get("thinking", "")}
                        elif bt == "tool_use":
                            name = block.get("name", "")
                            inp = block.get("input", {})
                            yield {"type": "kscc_tool", "name": name, "input": inp}
                            # kscc 的 Edit/Write 工具也产生 diff 预览
                            if name in ("Edit", "Write"):
                                path = inp.get("file_path", "")
                                old_s = inp.get("old_string", "")
                                new_s = inp.get("new_string", "") or inp.get("content", "")
                                if path and (old_s or new_s):
                                    yield {
                                        "type": "tool_call",
                                        "name": "edit_file",
                                        "arguments": {"path": path, "old_string": old_s, "new_string": new_s},
                                        "preview": f"Edit: {path}",
                                    }
                elif t == "user":
                    pass  # kscc 内部 tool_result，跳过
                elif t == "result":
                    subtype = msg.get("subtype", "")
                    if subtype == "success":
                        usage = msg.get("usage", {})
                        yield {
                            "type": "usage",
                            "input_tokens": usage.get("input_tokens", 0),
                            "output_tokens": usage.get("output_tokens", 0),
                        }
                        yield {"type": "result_end"}
                        return
                    else:
                        yield {"type": "error", "content": str(msg.get("errors", "kscc error"))}
                        yield {"type": "result_end"}
                        return
                elif t == "system":
                    # init, 跳过
                    pass
        except Exception as e:
            yield {"type": "error", "content": f"kscc stream error: {e}"}

    def close(self):
        """Best-effort shutdown to avoid unclosed proactor transport warnings on Windows."""
        proc = self._proc
        self._proc = None
        if not proc:
            return
        try:
            if proc.stdin and not proc.stdin.is_closing():
                proc.stdin.close()
        except Exception:
            pass
        try:
            if proc.returncode is None:
                proc.terminate()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
#  Agent
# ══════════════════════════════════════════════════════════════

class Agent:
    def __init__(self, config: Config = None, mode: str = None, resume_messages: list[dict] = None):
        self.config = config or load_config()
        self.mode = mode or self.config.mode
        provider = get_active_provider(self.config)
        if provider.name != "kscc" and not provider.api_key:
            raise ValueError(f"API key not set for provider '{provider.name}'. Set env var or config.json.")
        self.backend = create_backend(provider)
        self.provider_name = provider.name
        self.tools = ToolExecutor(self.config.workspace)
        self.ctx = ContextTracker(model=provider.model, context_limit=provider.context_limit or self.config.context_limit)
        # 输出上限：用户设置 > 模型默认表 > 16384
        self.max_output = provider.max_output_tokens or self.config.max_output_tokens or get_max_output(provider.model)
        # 预加载的历史消息（用于从 UI session 恢复）。注意：messages 一旦初始化完成，
        # 后续多次 run() 应该在同一个 messages 上追加，而不是每次重置。
        self.messages: list[dict] = list(resume_messages) if resume_messages else []
        self._resume_messages = None
        self._confirm_result: Optional[bool] = None
        self._confirm_event = None
        self._hooks: dict[str, Callable] = {}
        self.skill_manager = SkillManager()
        self._active_skill: Optional[Skill] = None
        self._skill_match_result: Optional[SkillMatchResult] = None
        self._run_user_prompt: str = ""

    def on(self, event: str, callback: Callable):
        """Register hook: 'confirm' -> callback(tool_call) -> bool"""
        self._hooks[event] = callback

    async def run(self, prompt: str, attachments: list[str] = None) -> AsyncGenerator[dict, None]:
        """
        运行 Agent，yield 事件流。

        事件类型:
          {"type": "text_delta", "text": "..."}
          {"type": "thinking", "text": "..."}
          {"type": "tool_call", "name": "...", "arguments": {...}, "preview": "..."}
          {"type": "tool_result", "name": "...", "result": "...", "error": bool}
          {"type": "confirm", "tool_call": {...}, "preview": "..."}   # IDE 模式
          {"type": "done", "text": "...", "turns": int}
          {"type": "error", "content": "..."}
        """
        import asyncio
        self._confirm_event = asyncio.Event()
        self._run_user_prompt = prompt or ""
        self._skill_match_result = None
        dbg = bool(getattr(self.config, "skill_debug_log", False))
        skills_on = bool(getattr(self.config, "skills_enabled", True))

        if not skills_on:
            self._active_skill = None
            self._skill_match_result = None
            skill_debug_log("skills_enabled=false, skip matching", dbg)
            yield {"type": "skill_status", "status": "disabled"}
            effective_prompt = prompt
        else:
            mr = self.skill_manager.match_detailed(prompt)
            self._skill_match_result = mr
            self._active_skill = mr.best
            skill_debug_log(
                f"match prompt={prompt[:200]!r} best={(mr.best.id if mr.best else None)} "
                f"miss={mr.miss_reason!r} top={[ (round(s,2), sk.id) for s, sk in mr.candidates[:3] ]}",
                dbg,
            )
            if mr.best:
                yield {
                    "type": "skill_match",
                    "skill_id": mr.best.id,
                    "skill_name": mr.best.name,
                    "intent_pattern": mr.best.intent_pattern,
                    "score": mr.candidates[0][0] if mr.candidates else 0,
                }
                if mr.is_ambiguous():
                    alts = [{"id": sk.id, "name": sk.name, "score": round(s, 2)} for s, sk in mr.candidates[1:4]]
                    yield {"type": "skill_ambiguous", "candidates": alts}
            else:
                yield {
                    "type": "skill_miss",
                    "reason": mr.miss_reason,
                    "hint": mr.hint,
                }
            effective_prompt = self._augment_prompt_with_skill(prompt, mr)

        # 构建消息：如果已有会话 messages，则直接追加；否则初始化 system + 首条 user。
        if self.messages:
            # 防御：历史可能没有 system，补一个
            if not any(m.get("role") == "system" for m in self.messages):
                mode_desc = _MODE_DESCRIPTIONS.get(self.mode, _MODE_DESCRIPTIONS["ide"])
                system = SYSTEM_PROMPT.format(mode_description=mode_desc, workspace=self.config.workspace)
                if bool(getattr(self.config, "memory_injection_enabled", True)):
                    mem = memory_store.build_injection_text()
                    if mem.strip():
                        system += "\n\n## Local memory (auto)\n" + mem
                self.messages.insert(0, {"role": "system", "content": system})
            self.messages.append({"role": "user", "content": self._build_user_content(effective_prompt, attachments)})
        else:
            mode_desc = _MODE_DESCRIPTIONS.get(self.mode, _MODE_DESCRIPTIONS["ide"])
            system = SYSTEM_PROMPT.format(mode_description=mode_desc, workspace=self.config.workspace)
            if bool(getattr(self.config, "memory_injection_enabled", True)):
                mem = memory_store.build_injection_text()
                if mem.strip():
                    system += "\n\n## Local memory (auto)\n" + mem
            self.messages = [{"role": "system", "content": system}]
            self.messages.append({"role": "user", "content": self._build_user_content(effective_prompt, attachments)})

        # 记录初始 token
        self.ctx.record_prompt(self.messages)
        yield {"type": "context", "summary": self.ctx.summary()}

        turns = 0

        while turns < self.config.max_turns:
            turns += 1

            # 上下文检查：接近上限时裁剪
            if self.ctx.need_trim():
                yield {"type": "context", "summary": self.ctx.summary(), "warning": "context near limit"}
                self._trim_conversation()
                yield {"type": "context", "summary": self.ctx.summary(), "note": "trimmed older messages"}

            # 根据 provider 选择工具格式
            if self.provider_name == "anthropic":
                tools_schema = TOOLS_ANTHROPIC
            else:
                tools_schema = TOOLS_OPENAI

            # 调用 LLM
            full_text = ""
            reasoning_text = ""
            tool_calls_complete = []
            pending_tool_calls: dict[int, dict] = {}

            try:
                async for event in self.backend.chat(self.messages, tools_schema, self.max_output):
                    if event["type"] == "error":
                        yield event
                        return
                    elif event["type"] == "text_delta":
                        full_text += event["text"]
                        yield {"type": "text_delta", "text": event["text"]}
                    elif event["type"] == "thinking":
                        reasoning_text += event["text"]
                        yield {"type": "thinking", "text": event["text"]}
                    elif event["type"] == "kscc_tool":
                        yield {"type": "kscc_tool", "name": event.get("name", ""), "input": event.get("input", {})}
                    elif event["type"] == "tool_delta":
                        idx = event["index"]
                        pending_tool_calls[idx] = {
                            "name": event.get("name", ""),
                            "arguments": event.get("arguments", ""),
                        }
                    elif event["type"] == "tool_call_complete":
                        tool_calls_complete.append(event["tool_call"])
                    elif event["type"] == "result_end":
                        pass  # kscc 内部结束信号
                    elif event["type"] == "tool_call":
                        # kscc backend 转发工具调用（仅展示，不执行）
                        yield event
                    elif event["type"] == "usage":
                        self.ctx.record_usage(
                            input_tokens=event.get("input_tokens", 0),
                            output_tokens=event.get("output_tokens", 0),
                        )
                        yield {"type": "context", "summary": self.ctx.summary()}
            except Exception as e:
                yield {"type": "error", "content": f"LLM call failed: {e}"}
                return

            if not tool_calls_complete:
                # 无工具调用，结束
                if full_text or reasoning_text:
                    msg = {"role": "assistant", "content": full_text or None}
                    if reasoning_text:
                        msg["reasoning_content"] = reasoning_text
                    self.messages.append(msg)
                    # 估算最后 token
                    if not self.ctx.output_tokens:
                        self.ctx.record_usage(output_tokens=count_tokens(full_text, self.ctx.model))
                if self._active_skill:
                    self.skill_manager.mark_used(self._active_skill.id)
                if bool(getattr(self.config, "skills_enabled", True)):
                    draft = self._build_skill_save_draft(full_text)
                    if draft:
                        yield {"type": "skill_save_draft", "draft": draft}
                yield {"type": "done", "text": full_text, "turns": turns, "context": self.ctx.summary()}
                return

            # 处理工具调用
            msg = {
                "role": "assistant",
                "content": full_text or None,
                "tool_calls": tool_calls_complete,
            }
            if reasoning_text:
                msg["reasoning_content"] = reasoning_text
            self.messages.append(msg)

            tool_results = []
            for tc in tool_calls_complete:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    yield {"type": "tool_result", "name": name, "result": f"Invalid JSON arguments: {tc['function']['arguments'][:200]}", "error": True}
                    continue

                # IDE 模式：生成预览并确认（read_file/search 等只读操作不需确认）
                if self.mode == "ide" and name in ("edit_file", "write_file"):
                    preview = self.tools.preview(name, args)
                    yield {"type": "confirm", "tool_call": tc, "preview": preview, "args": args}
                    approved = await self._wait_approval()
                    if not approved:
                        tool_results.append({"tool_call_id": tc["id"], "role": "tool", "content": "User rejected this action."})
                        yield {"type": "tool_result", "name": name, "result": "Rejected by user", "error": False}
                        continue

                # 执行工具
                yield {"type": "tool_call", "name": name, "arguments": args, "preview": self.tools.preview(name, args)}
                result = self.tools.execute(name, args)
                is_error = result.startswith("ToolError:") or result.startswith("Error:")
                yield {"type": "tool_result", "name": name, "result": result, "error": is_error}
                tool_results.append({"tool_call_id": tc["id"], "role": "tool", "content": result})

            # 添加工具结果消息（OpenAI 格式：逐个 tool 角色消息）
            for tr in tool_results:
                self.messages.append({"role": "tool", "tool_call_id": tr["tool_call_id"], "content": tr["content"]})

            # 上下文管理：如果消息太多，修剪旧消息
            if len(self.messages) > 20:
                self._trim_conversation()

        yield {"type": "error", "content": f"Reached max turns ({self.config.max_turns})"}

    async def _wait_approval(self) -> bool:
        """等待用户确认（IDE 模式）。"""
        import asyncio
        if self._hooks.get("confirm"):
            # 使用同步 hook
            result = self._hooks["confirm"](None)
            return bool(result)
        # 使用事件机制（异步）
        self._confirm_event.clear()
        await self._confirm_event.wait()
        result = self._confirm_result
        self._confirm_result = None
        return bool(result)

    def approve(self):
        self._confirm_result = True
        if self._confirm_event:
            self._confirm_event.set()

    def reject(self):
        self._confirm_result = False
        if self._confirm_event:
            self._confirm_event.set()

    def _build_user_content(self, prompt: str, attachments: list[str] = None):
        import base64
        if not attachments:
            return prompt
        image_parts = []; text_parts = []
        for fpath in attachments:
            mime, _ = mimetypes.guess_type(fpath)
            if mime and mime.startswith("image/") and self.provider_name != "kscc":
                try:
                    data = Path(fpath).read_bytes()
                    b64 = base64.b64encode(data).decode()
                    image_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"}
                    })
                except Exception:
                    pass
            elif mime and mime.startswith("image/"):
                text_parts.append(f"[Attached image: {Path(fpath).name}]")
            else:
                try:
                    content = Path(fpath).read_text("utf-8", errors="replace")
                    text_parts.append(f"\n--- {fpath} ---\n{content}\n")
                except Exception as e:
                    text_parts.append(f"\n[Failed to read {fpath}: {e}]\n")
        if image_parts:
            parts = [{"type": "text", "text": prompt}]
            if text_parts:
                parts.append({"type": "text", "text": "\n".join(text_parts)})
            parts.extend(image_parts)
            return parts
        content = prompt
        if text_parts:
            content += "\n\n[Attached files]\n" + "\n".join(text_parts)
        return content

    def _augment_prompt_with_skill(self, prompt: str, mr: SkillMatchResult) -> str:
        skill = mr.best
        if not skill:
            return prompt
        steps = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(skill.steps[:8])) or "1. Follow the best-known sequence."
        extra = ""
        if mr.is_ambiguous() and len(mr.candidates) > 1:
            names = [f"{sk.name} ({sk.id})" for _, sk in mr.candidates[1:4]]
            if names:
                extra = "\n[Other close-matching skills — pick the best fit or ignore if irrelevant: " + "; ".join(names) + "]\n"
        return (
            f"{prompt}\n\n"
            "[Matched Local Skill]\n"
            f"- Skill: {skill.name}\n"
            f"- ID: {skill.id}\n"
            "- Guidance: Reuse this plan when it fits the current task. If constraints differ, adapt explicitly.\n"
            "[Recommended Steps]\n"
            f"{steps}\n"
            f"{extra}"
        )

    def _collect_tool_names_from_messages(self) -> list[str]:
        seen: list[str] = []
        for m in self.messages:
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                n = fn.get("name")
                if n and n not in seen:
                    seen.append(n)
        return seen

    def _guess_keywords_from_prompt(self, text: str, max_n: int = 8) -> list[str]:
        t = (text or "").strip().lower()
        if not t:
            return []
        toks = re.findall(r"[a-zA-Z0-9_./-]+|[\u4e00-\u9fff]{2,}", t)
        stop = {
            "the", "and", "for", "with", "this", "that", "from", "into", "your", "you", "are", "was", "has",
            "please", "just", "only", "dont", "don't",
            "请", "帮我", "一下", "如何", "什么", "可以", "不要", "使用", "输出", "方案", "计划",
            "给我", "只给", "不要改", "不要修改", "代码", "文件", "内容", "建议", "一下子", "这个",
            "一下吧", "看看", "麻烦", "帮忙", "并且", "然后", "以及", "或者", "一个", "一些",
            "只输出", "分步计划", "重构建议", "不要实际修改文件",
            "edit_file", "write_file",
        }
        noise_patterns = [
            r"^[0-9]+$",
            r"^[a-z]$",
            r"^https?://",
        ]
        out = []
        for w in toks:
            w = w.strip("`'\".,:;!?()[]{}<>|")
            if not w:
                continue
            if w in stop or len(w) < 2:
                continue
            if any(re.match(p, w) for p in noise_patterns):
                continue
            if w.endswith(".py") and len(w) > 3:
                stem = w[:-3]
                if stem and stem not in out and stem not in stop:
                    out.append(stem)
            if w not in out:
                out.append(w)
            if len(out) >= max_n:
                break
        return out

    def _build_skill_save_draft(self, last_assistant_text: str) -> Optional[dict]:
        prompt = self._run_user_prompt or ""
        if not prompt.strip():
            return None
        tools_used = self._collect_tool_names_from_messages()
        title = prompt.replace("\n", " ").strip()[:48] or "Skill"
        steps: list[str] = []
        for n in tools_used[:6]:
            steps.append(f"Use tool `{n}` when appropriate for this task type.")
        summ = (last_assistant_text or "").strip()
        if summ:
            first = summ.split("\n", 1)[0][:240]
            steps.append(f"Summary hint: {first}")
        if not steps:
            steps.append("Follow the assistant's last answer as the workflow baseline.")
        return {
            "name": title,
            "intent_pattern": self._guess_keywords_from_prompt(prompt),
            "steps": steps[:10],
            "source_prompt": prompt[:4000],
        }

    def _trim_conversation(self):
        """裁剪消息历史，保留 system + 最近两轮完整对话。"""
        if len(self.messages) <= 8:
            return
        # 保留：system (idx 0) + 最近 6 条消息（约 2 轮完整工具调用循环）
        keep = max(6, min(14, len(self.messages) // 2))
        old_count = len(self.messages) - keep - 1
        self.messages = [self.messages[0]] + self.messages[-keep:]
        # 重新估算 token
        self.ctx.record_prompt(self.messages)
        return old_count

    def _trim_messages(self):
        """已废弃，由 _trim_conversation 替代。"""
        self._trim_conversation()
