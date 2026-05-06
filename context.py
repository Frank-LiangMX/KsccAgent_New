"""
Quark Agent - Token 统计 & Context Window 管理

全部本地计算，不依赖外部服务。
"""

from __future__ import annotations

import json
import re
from typing import Optional

# ── tiktoken ──────────────────────────────────────────────────
# 支持 OpenAI 模型精确计数，其他模型用近似估算

_ENCODER_CACHE = {}

# 模型上下文窗口上限（tokens）
MODEL_CONTEXT_LIMITS: dict[str, int] = {
    # ── OpenAI ──
    "gpt-5": 1000000,
    "gpt-4.1": 1000000,
    "gpt-4.1-mini": 1048576,
    "gpt-4.1-nano": 1048576,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "gpt-4-32k": 32768,
    "gpt-3.5-turbo": 16385,
    "gpt-3.5-turbo-16k": 16385,
    "o1": 200000,
    "o1-mini": 128000,
    "o1-pro": 200000,
    "o3": 200000,
    "o3-mini": 200000,
    "o4-mini": 200000,
    # ── Anthropic ──
    "claude-sonnet-4-5": 200000,
    "claude-sonnet-4": 200000,
    "claude-opus-4-5": 200000,
    "claude-opus-4": 200000,
    "claude-3.7-sonnet": 200000,
    "claude-3.5-sonnet": 200000,
    "claude-3.5-haiku": 200000,
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    # ── DeepSeek ──
    "deepseek-chat": 131072,
    "deepseek-coder": 131072,
    "deepseek-reasoner": 65536,
    "deepseek-r1": 131072,
    "deepseek-v3": 128000,
    "deepseek-v4": 1048576,
    "deepseek-v4-pro": 1048576,
    "deepseek-v4.1": 1048576,
    # ── Google ──
    "gemini-2.5-pro": 1000000,
    "gemini-2.5-flash": 1000000,
    "gemini-2.0-pro": 1048576,
    "gemini-2.0-flash": 1048576,
    "gemini-1.5-pro": 2097152,
    "gemini-1.5-flash": 1048576,
    "gemini-2.0-flash-lite": 1048576,
    # ── Mistral ──
    "mistral-large": 128000,
    "mistral-medium": 32768,
    "mistral-small": 32768,
    "codestral": 32768,
    "codestral-mamba": 262144,
    "pixtral": 131072,
    "ministral": 131072,
    # ── Grok (xAI) ──
    "grok-3": 131072,
    "grok-3-mini": 131072,
    "grok-2": 131072,
    "grok-beta": 131072,
    # ── Meta Llama ──
    "llama-4": 131072,
    "llama-4-maverick": 1000000,
    "llama-3.3": 131072,
    "llama-3.2": 131072,
    "llama-3.1": 131072,
    "llama-3": 8192,
    # ── Qwen (阿里) ──
    "qwen-max": 131072,
    "qwen-plus": 131072,
    "qwen-turbo": 131072,
    "qwen3-coder": 131072,
    "qwen-coder-plus": 131072,
    "qwq": 131072,
    # ── 默认 ──
    "mimo-v2-pro": 50000,
    "mimo-v2.5": 50000,
    "mimo-v2.5-pro": 50000,
    "_default": 65536,
}

# 模型默认最大输出 tokens
MODEL_MAX_OUTPUT: dict[str, int] = {
    "gpt-5": 32768,
    "gpt-4.1": 32768,
    "gpt-4o": 16384,
    "gpt-4o-mini": 16384,
    "gpt-4-turbo": 4096,
    "o1": 100000,
    "o1-mini": 65536,
    "o3": 100000,
    "o3-mini": 100000,
    "o4-mini": 100000,
    "claude-sonnet-4-5": 32768,
    "claude-sonnet-4": 16000,
    "claude-opus-4-5": 32768,
    "claude-opus-4": 32000,
    "claude-3.5-sonnet": 8192,
    "claude-3.5-haiku": 8192,
    "deepseek-chat": 8192,
    "deepseek-v3": 8192,
    "deepseek-r1": 8192,
    "deepseek-v4": 384000,
    "deepseek-v4-pro": 384000,
    "gemini-2.5-pro": 65536,
    "gemini-2.5-flash": 65536,
    "gemini-2.0-pro": 65536,
    "gemini-2.0-flash": 8192,
    "deepseek-v4.1": 384000,
    "llama-4-maverick": 16384,
    "mistral-large": 8192,
    "mimo-v2-pro": 128000,
    "mimo-v2.5": 128000,
    "mimo-v2.5-pro": 128000,
    "_default": 16384,
}


def get_max_output(model: str) -> int:
    model_lower = model.lower()
    for key, val in MODEL_MAX_OUTPUT.items():
        if key in model_lower:
            return val
    return MODEL_MAX_OUTPUT["_default"]


def get_context_limit(model: str) -> int:
    """获取模型上下文窗口上限。"""
    model_lower = model.lower()
    for key, limit in MODEL_CONTEXT_LIMITS.items():
        if key in model_lower:
            return limit
    return MODEL_CONTEXT_LIMITS.get(model_lower, MODEL_CONTEXT_LIMITS["_default"])


def _get_encoder(model: str):
    """获取 tiktoken 编码器（带缓存）。"""
    if model in _ENCODER_CACHE:
        return _ENCODER_CACHE[model]
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None
    _ENCODER_CACHE[model] = enc
    return enc


def count_tokens(text: str, model: str = "") -> int:
    """统计文本 token 数。优先用 tiktoken，回退到近似估算。"""
    enc = _get_encoder(model) if model else None
    if enc:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    # 近似估算：英文 ~0.75 tokens/word，中文 ~1.5 tokens/char
    english_words = len(re.findall(r"[a-zA-Z0-9]+", text))
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - english_words - chinese_chars
    return int(english_words * 0.75 + chinese_chars * 1.5 + other * 0.5)


def count_messages_tokens(messages: list[dict], model: str = "") -> int:
    """统计消息列表总 token 数（含 role/formatting 开销）。"""
    total = 0
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content, model)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += count_tokens(str(block), model)
                else:
                    total += count_tokens(str(block), model)
        # tool_calls 也算
        for tc in msg.get("tool_calls", []):
            total += count_tokens(json.dumps(tc, ensure_ascii=False), model)
        # tool_call_id
        if msg.get("tool_call_id"):
            total += count_tokens(str(msg["tool_call_id"]), model)
        # role + 格式化开销 ~4 tokens/msg
        total += 4
    return total


class ContextTracker:
    """追踪 Agent 会话的 token 使用情况。"""

    def __init__(self, model: str = "", context_limit: int = 0):
        self.model = model
        # 表里有的用表，没有的用用户设置，再没有用默认
        from_table = get_context_limit(model)
        if from_table != MODEL_CONTEXT_LIMITS["_default"]:
            self.limit = from_table  # 精确匹配
        elif context_limit:
            self.limit = context_limit  # 用户自定义
        else:
            self.limit = from_table  # 兜底 65536
        self.input_tokens: list[int] = []   # 每轮 input tokens
        self.output_tokens: list[int] = []  # 每轮 output tokens
        self.prompt_tokens = 0              # system + user 首次
        self.total_input = 0
        self.total_output = 0

    def record_usage(self, input_tokens: int = 0, output_tokens: int = 0):
        """记录一轮的 token 使用。"""
        if input_tokens:
            self.input_tokens.append(input_tokens)
            self.total_input += input_tokens
        if output_tokens:
            self.output_tokens.append(output_tokens)
            self.total_output += output_tokens

    def record_prompt(self, messages: list[dict]):
        """记录初始消息的 token 数。"""
        self.prompt_tokens = count_messages_tokens(messages, self.model)

    @property
    def current_usage(self) -> int:
        """估算当前消息历史占用的 token 数。"""
        if self.total_input:
            # 最新一轮的 input 包含所有历史 + 开销，最准确
            return self.input_tokens[-1] if self.input_tokens else self.prompt_tokens
        return self.prompt_tokens

    @property
    def usage_ratio(self) -> float:
        """当前使用 / 上限 的比率。"""
        return self.current_usage / self.limit if self.limit else 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.current_usage)

    def need_trim(self, threshold: float = 0.85) -> bool:
        """是否需要裁剪消息历史。"""
        return self.usage_ratio > threshold

    def summary(self) -> dict:
        return {
            "model": self.model,
            "limit": self.limit,
            "prompt_tokens": self.prompt_tokens,
            "current_usage": self.current_usage,
            "remaining": self.remaining,
            "usage_ratio": round(self.usage_ratio, 3),
            "total_input": self.total_input,
            "total_output": self.total_output,
            "turns": len(self.output_tokens),
        }
