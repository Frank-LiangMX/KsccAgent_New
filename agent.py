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
from pathlib import Path
from typing import AsyncGenerator, Callable, Optional

from config import Config, ProviderConfig, load_config, get_active_provider
from context import ContextTracker, count_messages_tokens, count_tokens, get_max_output
import memory_store
import insight_index
from skill_manager import Skill, SkillManager, SkillMatchResult, skill_debug_log
from skill_scorer import score_skill_draft, is_worth_saving, get_score_label
from tool_executor import ToolExecutor, TOOL_REGISTRY, RiskLevel, classify_shell_risk, is_dangerous_shell, match_risk_template
from llm_client import OpenAIBackend, AnthropicBackend, KsccBackend, create_backend

# ══════════════════════════════════════════════════════════════
#  System Prompt
# ══════════════════════════════════════════════════════════════

_MODE_DESCRIPTIONS = {
    "solo": "你以自主模式工作。直接调用工具执行任务、验证结果并持续迭代，直到任务完成。",
    "ide": "你在 IDE 审阅模式下工作。进行文件修改前先说明计划并调用工具，由用户逐步批准或拒绝。只读类操作可直接执行。",
}

SYSTEM_PROMPT = """你是 Kscc，一名 AI 编程助手，帮助用户完成软件工程任务。

## 你的能力
- 读取、写入和编辑工作区文件
- 执行 Shell 命令
- 使用正则搜索代码
- 使用 glob 查找文件
- 列出目录内容

## 行为准则
- 回复简洁直接，不要冗余前言。
- 修改文件时优先使用精确编辑，不要无必要整文件重写。
- 编辑前先读取上下文，遵循现有代码风格。
- 不要臆造路径和 URL，先用搜索工具定位。
- 执行命令时优先非交互命令。
- 完成任务后做基本自检（读回改动或运行测试）。
- 默认使用简体中文输出；仅当用户明确要求英文时才使用英文。

## 模式
{mode_description}

## 工作目录
当前工作目录：{workspace}
尽量使用相对路径。"""


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
    {
        "type": "function",
        "function": {
            "name": "adb_command",
            "description": "Executes an ADB (Android Debug Bridge) command on a connected device. Use for Android device automation: install apps, push/pull files, run shell commands on device, capture screenshots, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "ADB subcommand (e.g., 'devices', 'shell ls /sdcard', 'install app.apk', 'pull /sdcard/file.txt .')"},
                    "device": {"type": "string", "description": "Optional device serial number when multiple devices are connected"},
                },
                "required": ["command"],
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
    {
        "name": "adb_command",
        "description": "Executes an ADB (Android Debug Bridge) command on a connected device. Use for Android device automation: install apps, push/pull files, run shell commands on device, capture screenshots, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "ADB subcommand (e.g., 'devices', 'shell ls /sdcard', 'install app.apk', 'pull /sdcard/file.txt .')"},
                "device": {"type": "string", "description": "Optional device serial number when multiple devices are connected"},
            },
            "required": ["command"],
        },
    },
]

TOOL_NAMES = [t["function"]["name"] for t in TOOLS_OPENAI]


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
        self.backend = create_backend(provider, workspace=self.config.workspace)
        self.provider_name = provider.name
        self.tools = ToolExecutor(self.config.workspace, config=self.config)
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
        self._collected_tool_calls = []  # 收集工具调用用于评分
        self._collected_tool_results = []  # 收集工具结果用于评分
        self._recorded_execution_plan = []  # 录制工具调用链用于 Skill 回放
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

        # P3-2: Classify task type for selective memory injection
        task_types = insight_index.classify_task_type(effective_prompt) if getattr(self.config, 'feature_insight_index', True) else []

        # P4-5: 敏感任务风控模板检测
        risk_template = match_risk_template(prompt) if getattr(self.config, 'feature_risk_templates', True) else None
        if risk_template:
            yield {
                "type": "risk_template",
                "template_id": risk_template["template_id"],
                "name": risk_template["name"],
                "risk_level": risk_template["risk_level"],
                "require_reason": risk_template.get("require_reason", False),
                "require_confirmation": risk_template.get("require_confirmation", True),
                "block_auto_execute": risk_template.get("block_auto_execute", False),
            }

        # 构建消息：如果已有会话 messages，则直接追加；否则初始化 system + 首条 user。
        if self.messages:
            # 防御：历史可能没有 system，补一个
            if not any(m.get("role") == "system" for m in self.messages):
                mode_desc = _MODE_DESCRIPTIONS.get(self.mode, _MODE_DESCRIPTIONS["ide"])
                system = SYSTEM_PROMPT.format(mode_description=mode_desc, workspace=self.config.workspace)
                if bool(getattr(self.config, "memory_injection_enabled", True)):
                    mem = memory_store.build_injection_text(task_types=task_types, query=effective_prompt)
                    if mem.strip():
                        system += "\n\n## Local memory (auto)\n" + mem
                self.messages.insert(0, {"role": "system", "content": system})
            self.messages.append({"role": "user", "content": self._build_user_content(effective_prompt, attachments)})
        else:
            mode_desc = _MODE_DESCRIPTIONS.get(self.mode, _MODE_DESCRIPTIONS["ide"])
            system = SYSTEM_PROMPT.format(mode_description=mode_desc, workspace=self.config.workspace)
            if bool(getattr(self.config, "memory_injection_enabled", True)):
                mem = memory_store.build_injection_text(task_types=task_types, query=effective_prompt)
                if mem.strip():
                    system += "\n\n## Local memory (auto)\n" + mem
            self.messages = [{"role": "system", "content": system}]
            self.messages.append({"role": "user", "content": self._build_user_content(effective_prompt, attachments)})

        # P3-5: Yield memory hit metadata for UI visualization
        if bool(getattr(self.config, "memory_injection_enabled", True)):
            try:
                hits = memory_store.get_injection_hits(task_types=task_types, query=effective_prompt)
                if any(hits.get(k, 0) > 0 for k in ("rules_count", "facts_count", "insights_count", "archives_count")):
                    yield {"type": "memory_hits", "hits": hits}
            except Exception:
                pass

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
                        # 评分
                        score_result = score_skill_draft(
                            draft=draft,
                            tool_calls=self._collected_tool_calls,
                            tool_results=self._collected_tool_results,
                            assistant_text=full_text,
                            conversation_ended_normally=True,
                        )
                        draft["score"] = {
                            "total": score_result.total,
                            "completeness": score_result.completeness,
                            "reusability": score_result.reusability,
                            "success_signal": score_result.success_signal,
                            "label": get_score_label(score_result.total),
                            "worth_saving": is_worth_saving(score_result),
                            "reasons": score_result.reasons,
                        }
                        # 自动入库策略
                        threshold = float(getattr(self.config, "auto_save_skill_threshold", 75.0))
                        if score_result.total >= threshold:
                            # 自动保存
                            saved = self.skill_manager.upsert_skill(
                                name=draft.get("name", ""),
                                intent_pattern=draft.get("intent_pattern", []),
                                steps=draft.get("steps", []),
                                execution_plan=draft.get("execution_plan", []),
                            )
                            draft["auto_saved"] = True
                            draft["saved_id"] = saved.id
                            yield {"type": "skill_auto_saved", "skill_id": saved.id, "score": score_result.total}
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

                # IDE 模式：基于风险分级的审批策略
                meta = TOOL_REGISTRY.get(name)
                need_confirm = False
                if self.mode == "ide" and meta:
                    # SAFE 级（只读）→ 直接放行
                    # LOW 级（文件写/编辑）→ 需要确认
                    # MEDIUM 级（安全 Shell、网页）→ 放行
                    # HIGH 级（未知 Shell）→ 需要确认
                    # CRITICAL 级（危险 Shell）→ execute() 会自动拦截
                    if meta.risk in (RiskLevel.LOW, RiskLevel.HIGH):
                        need_confirm = True
                    elif name == "run_shell":
                        shell_risk = classify_shell_risk(args.get("command", ""))
                        if shell_risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                            need_confirm = True

                if need_confirm:
                    preview = self.tools.preview(name, args)
                    yield {"type": "confirm", "tool_call": tc, "preview": preview, "args": args}
                    approved = await self._wait_approval()
                    if not approved:
                        tool_results.append({"tool_call_id": tc["id"], "role": "tool", "content": "User rejected this action."})
                        yield {"type": "tool_result", "name": name, "result": "Rejected by user", "error": False}
                        continue

                # 执行工具
                risk_val = meta.risk.value if meta else "unknown"
                if name == "run_shell":
                    shell_risk = classify_shell_risk(args.get("command", ""))
                    risk_val = shell_risk.value
                yield {"type": "tool_call", "name": name, "arguments": args, "preview": self.tools.preview(name, args), "risk": risk_val}
                result = self.tools.execute(name, args)
                is_error = result.startswith("ToolError:") or result.startswith("Error:")
                yield {"type": "tool_result", "name": name, "result": result, "error": is_error}
                tool_results.append({"tool_call_id": tc["id"], "role": "tool", "content": result})
                # 收集用于评分
                self._collected_tool_calls.append({"name": name, "arguments": args})
                self._collected_tool_results.append({"name": name, "result": result[:1000], "error": is_error})
                # 录制执行计划用于 Skill 回放（只存关键摘要，不存完整输出）
                self._recorded_execution_plan.append({
                    "step": len(self._recorded_execution_plan) + 1,
                    "tool": name,
                    "args": args,
                    "success": not is_error,
                })

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
        extra = ""
        if mr.is_ambiguous() and len(mr.candidates) > 1:
            names = [f"{sk.name} ({sk.id})" for _, sk in mr.candidates[1:4]]
            if names:
                extra = "\n[其他高相似技能（按需选择，不相关可忽略）: " + "; ".join(names) + "]\n"
        # 如果有录制的执行计划，注入回放指令
        if skill.execution_plan:
            plan_lines = []
            for step in skill.execution_plan:
                s = step.get("step", "?")
                tool = step.get("tool", "")
                args = step.get("args", {})
                if tool == "run_shell":
                    cmd = args.get("command", "")
                    plan_lines.append(f"  {s}. run_shell(command={cmd!r})")
                elif tool == "write_file":
                    path = args.get("path", "")
                    plan_lines.append(f"  {s}. write_file(path={path!r})")
                elif tool == "edit_file":
                    path = args.get("path", "")
                    plan_lines.append(f"  {s}. edit_file(path={path!r})")
                else:
                    plan_lines.append(f"  {s}. {tool}({json.dumps(args, ensure_ascii=False)[:120]})")
            plan_text = "\n".join(plan_lines)
            return (
                f"{prompt}\n\n"
                "[已匹配本地技能 — 回放模式]\n"
                f"- 技能: {skill.name}\n"
                f"- ID: {skill.id}\n"
                "- 指令: 以下是该技能上次成功执行的完整工具调用序列。请严格按照此序列执行，"
                "参数可按当前上下文微调，但工具名和调用顺序不要改变。"
                "如果某步失败，再自行规划替代方案。\n"
                "[回放序列]\n"
                f"{plan_text}\n"
                f"{extra}"
            )
        # 无执行计划，走原有的文字提示模式
        steps = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(skill.steps[:8])) or "1. 按已验证的最佳流程执行。"
        return (
            f"{prompt}\n\n"
            "[已匹配本地技能]\n"
            f"- 技能: {skill.name}\n"
            f"- ID: {skill.id}\n"
            "- 指引: 当前任务适配时优先复用该流程；若约束不同请明确调整。\n"
            "[建议步骤]\n"
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
        # 如果有录制的执行计划，基于实际步骤生成描述
        if self._recorded_execution_plan:
            for rec in self._recorded_execution_plan[:8]:
                tool = rec.get("tool", "")
                args = rec.get("args", {})
                if tool == "run_shell":
                    cmd = args.get("command", "")[:80]
                    steps.append(f"执行命令: `{cmd}`")
                elif tool == "write_file":
                    path = args.get("path", "")
                    steps.append(f"写入文件: `{path}`")
                elif tool == "edit_file":
                    path = args.get("path", "")
                    steps.append(f"编辑文件: `{path}`")
                elif tool == "read_file":
                    path = args.get("path", "")
                    steps.append(f"读取文件: `{path}`")
                else:
                    steps.append(f"调用工具 `{tool}`")
        else:
            for n in tools_used[:6]:
                steps.append(f"在此类任务中，适合时可调用工具 `{n}`。")
        summ = (last_assistant_text or "").strip()
        if summ:
            first = summ.split("\n", 1)[0][:240]
            steps.append(f"摘要提示: {first}")
        if not steps:
            steps.append("以最近一次助手有效回答作为流程基线。")
        draft = {
            "name": title,
            "intent_pattern": self._guess_keywords_from_prompt(prompt),
            "steps": steps[:10],
            "source_prompt": prompt[:4000],
        }
        # 附带录制的执行计划
        if self._recorded_execution_plan:
            draft["execution_plan"] = self._recorded_execution_plan
        return draft

    def _trim_conversation(self):
        """分层裁剪：保留近期高保真 + 历史摘要锚点。"""
        if len(self.messages) <= 8:
            return
        system = self.messages[0]
        body = self.messages[1:]

        body_wo_anchor = []
        for m in body:
            if m.get("role") == "system" and str(m.get("content", "")).startswith("[Conversation Memory Anchor]"):
                continue
            body_wo_anchor.append(m)
        body = body_wo_anchor
        if len(body) <= 8:
            self.messages = [system] + body
            self.ctx.record_prompt(self.messages)
            return 0

        keep = max(6, min(14, len(body) // 2))
        head = body[:-keep]
        tail = body[-keep:]

        def _msg_text(msg: dict) -> str:
            c = msg.get("content", "")
            if isinstance(c, str):
                return c.strip()
            if isinstance(c, list):
                parts = []
                for p in c:
                    if isinstance(p, dict) and p.get("type") == "text":
                        parts.append(str(p.get("text", "")).strip())
                return " ".join([x for x in parts if x]).strip()
            return str(c or "").strip()

        rows = []
        for m in head[-24:]:
            role = str(m.get("role", ""))
            if role not in ("user", "assistant", "tool"):
                continue
            txt = _msg_text(m)
            if not txt:
                continue
            txt = txt.replace("\n", " ")
            if len(txt) > 180:
                txt = txt[:177] + "..."
            rows.append(f"- {role}: {txt}")
        anchor = ""
        if rows:
            anchor = (
                "[会话记忆锚点]\n"
                "以下为较早轮次的压缩摘要，用于保持连续性；若与最近轮次冲突，以最近轮次为准。\n"
                "摘要:\n" + "\n".join(rows[-12:])
            )

        new_msgs = [system]
        if anchor:
            new_msgs.append({"role": "system", "content": anchor})
        new_msgs.extend(tail)
        old_count = len(self.messages) - len(new_msgs)
        self.messages = new_msgs
        # 重新估算 token
        self.ctx.record_prompt(self.messages)
        return old_count

    def _trim_messages(self):
        """已废弃，由 _trim_conversation 替代。"""
        self._trim_conversation()
