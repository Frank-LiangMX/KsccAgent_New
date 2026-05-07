"""
LLM Client - OpenAI / Anthropic / Kscc 后端实现
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import AsyncGenerator, Optional

import httpx

from config import ProviderConfig


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
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        url = f"{self.base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=httpx.Timeout(300)) as client:
            async with client.stream("POST", url, json=body, headers=self.headers) as resp:
                if resp.status_code != 200:
                    text = await resp.aread()
                    yield {"type": "error", "content": f"API Error {resp.status_code}: {text.decode()[:500]}"}
                    return
                tool_calls: dict[int, dict] = {}
                finish_reason = None
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
                    usage = chunk.get("usage")
                    if usage:
                        yield {"type": "usage", "input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)}
                    choice = chunk.get("choices", [{}])[0]
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta", {})
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
                if finish_reason == "tool_calls":
                    for tc in tool_calls.values():
                        fn = tc.get("function", {})
                        if not fn.get("name", "").strip():
                            continue
                        args = fn.get("arguments", "")
                        if not str(args).strip():
                            continue
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
            "stream": True,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
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
                tool_blocks: dict[int, dict] = {}
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


class KsccBackend:
    def __init__(self, provider: ProviderConfig, workspace: str = ""):
        self.bin = self._find_bin(provider.base_url)
        self.model = provider.model or ""
        self.workspace = workspace or str(Path.cwd())
        self._proc = None

    @staticmethod
    def _find_bin(hint: str) -> str:
        """找到 kscc 可执行文件路径"""
        import shutil
        for name in (hint, "kscc", "kscc.cmd", "kscc.ps1"):
            found = shutil.which(name)
            if found:
                return found
        npm_dir = Path(os.environ.get("APPDATA", "")) / "npm"
        for name in ("kscc.cmd", "kscc.ps1", "kscc"):
            p = npm_dir / name
            if p.exists():
                return str(p)
        return hint

    async def _ensure_process(self):
        if self._proc and self._proc.returncode is None:
            return
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
                    pass
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


def create_backend(provider: ProviderConfig, workspace: str = ""):
    if provider.name == "kscc":
        return KsccBackend(provider, workspace=workspace)
    if provider.name == "anthropic":
        return AnthropicBackend(provider)
    return OpenAIBackend(provider)
