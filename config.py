"""
Kscc Agent - Configuration management
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

CONFIG_FILE = Path(__file__).parent / "config.json"

load_dotenv(Path(__file__).parent / ".env")
load_dotenv()

KSCC_MODELS = ["mimo-v2-pro", "mimo-v2.5", "mimo-v2.5-pro"]
KSCC_DEFAULT_CONTEXT_LIMIT = 50_000
KSCC_DEFAULT_MAX_OUTPUT = 128_000


@dataclass
class OpenAIModel:
    name: str = ""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    enabled: bool = True
    context_limit: int = 0
    max_output_tokens: int = 0


@dataclass
class Config:
    backend: str = "kscc"
    kscc_model: str = "mimo-v2.5-pro"
    openai_models: list[OpenAIModel] = field(default_factory=list)
    kscc_model_limits: dict[str, dict] = field(default_factory=dict)
    openai_active: str = ""
    mode: str = "solo"
    workspace: str = ""
    max_turns: int = 50
    context_limit: int = 65536
    max_output_tokens: int = 16384
    theme: str = "dark"
    ui_font_family: str = "Segoe UI"
    ui_font_size: int = 12
    code_font_family: str = "JetBrains Mono"
    code_font_size: int = 13
    accent_color: str = "#5ee9ff"
    ide_word_wrap: bool = True
    ide_minimap: bool = False
    ide_font_size: int = 13
    # Agent / Skill / memory (local-only)
    skills_enabled: bool = True
    skill_debug_log: bool = False
    memory_injection_enabled: bool = True
    auto_save_skill_threshold: float = 75.0  # 自动保存 skill 的分数阈值
    # P5-5: Feature flags
    feature_task_mode: bool = True          # 任务状态机模式
    feature_insight_index: bool = True      # Insight 记忆层
    feature_memory_compress: bool = True    # 记忆自动压缩
    feature_risk_templates: bool = True     # 风控模板检测
    feature_evidence_capture: bool = True   # 网页/设备证据采集
    feature_adb_tools: bool = False         # ADB 设备工具（默认关闭，需手动开启）


@dataclass
class ProviderConfig:
    name: str = ""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    context_limit: int = 0
    max_output_tokens: int = 0
    extra_headers: dict = field(default_factory=dict)


def get_model_defaults(model: str, provider: str = "") -> tuple[int, int]:
    model = str(model or "").strip()
    provider = str(provider or "").strip().lower()
    if provider == "kscc" or model in KSCC_MODELS:
        return KSCC_DEFAULT_CONTEXT_LIMIT, KSCC_DEFAULT_MAX_OUTPUT
    from context import get_context_limit, get_max_output
    return int(get_context_limit(model)), int(get_max_output(model))


def get_effective_model_limits(model: str, provider: str = "", context_limit: int = 0, max_output_tokens: int = 0) -> tuple[int, int]:
    default_context, default_output = get_model_defaults(model, provider)
    return int(context_limit or default_context), int(max_output_tokens or default_output)


def ensure_builtin_kscc_limits(cfg: Config):
    if not isinstance(cfg.kscc_model_limits, dict):
        cfg.kscc_model_limits = {}
    for model in KSCC_MODELS:
        entry = cfg.kscc_model_limits.get(model)
        if not isinstance(entry, dict):
            entry = {}
        entry.setdefault("context_limit", 0)
        entry.setdefault("max_output_tokens", 0)
        cfg.kscc_model_limits[model] = entry


def get_kscc_model_limits(cfg: Config, model: Optional[str] = None) -> tuple[int, int]:
    ensure_builtin_kscc_limits(cfg)
    name = str(model or cfg.kscc_model or KSCC_MODELS[-1])
    entry = cfg.kscc_model_limits.get(name, {})
    return get_effective_model_limits(
        name,
        "kscc",
        int(entry.get("context_limit", 0) or 0),
        int(entry.get("max_output_tokens", 0) or 0),
    )


def _ensure_builtin_openai_models(cfg: Config):
    defaults = [
        OpenAIModel(name="gpt-5", model="gpt-5", api_key="", base_url="https://api.openai.com/v1", enabled=False),
        OpenAIModel(name="gpt-4.1", model="gpt-4.1", api_key="", base_url="https://api.openai.com/v1", enabled=False),
        OpenAIModel(name="claude-opus-4", model="claude-opus-4", api_key="", base_url="", enabled=False),
        OpenAIModel(name="claude-sonnet-4", model="claude-sonnet-4", api_key="", base_url="", enabled=False),
        OpenAIModel(name="gemini-2.5-pro", model="gemini-2.5-pro", api_key="", base_url="", enabled=False),
        OpenAIModel(name="gemini-2.5-flash", model="gemini-2.5-flash", api_key="", base_url="", enabled=False),
        OpenAIModel(name="deepseek-v3", model="deepseek-v3", api_key="", base_url="https://api.deepseek.com", enabled=False),
        OpenAIModel(name="deepseek-v4", model="deepseek-v4", api_key="", base_url="https://api.deepseek.com", enabled=False),
        OpenAIModel(name="deepseek-v4-pro", model="deepseek-v4-pro", api_key="", base_url="https://api.deepseek.com", enabled=False),
        OpenAIModel(name="deepseek-v4.1", model="deepseek-v4.1", api_key="", base_url="https://api.deepseek.com", enabled=False),
        OpenAIModel(name="llama-4-maverick", model="llama-4-maverick", api_key="", base_url="", enabled=False),
        OpenAIModel(name="mistral-large", model="mistral-large", api_key="", base_url="", enabled=False),
    ]
    existing = {m.name for m in cfg.openai_models}
    for model in defaults:
        if model.name not in existing:
            cfg.openai_models.append(model)


def load_config() -> Config:
    cfg = Config()
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text("utf-8"))
            for key in (
                "backend",
                "kscc_model",
                "openai_active",
                "mode",
                "workspace",
                "max_turns",
                "context_limit",
                "max_output_tokens",
                "theme",
                "ui_font_family",
                "ui_font_size",
                "code_font_family",
                "code_font_size",
                "accent_color",
                "ide_word_wrap",
                "ide_minimap",
                "ide_font_size",
                "skills_enabled",
                "skill_debug_log",
                "memory_injection_enabled",
                "auto_save_skill_threshold",
                "feature_task_mode",
                "feature_insight_index",
                "feature_memory_compress",
                "feature_risk_templates",
                "feature_evidence_capture",
                "feature_adb_tools",
            ):
                if key in data:
                    setattr(cfg, key, data[key])
            if isinstance(data.get("kscc_model_limits"), dict):
                cfg.kscc_model_limits = data.get("kscc_model_limits") or {}
            for item in data.get("openai_models", []):
                cfg.openai_models.append(OpenAIModel(**item))
            if not cfg.openai_active and cfg.openai_models:
                enabled = [m for m in cfg.openai_models if m.enabled]
                cfg.openai_active = enabled[0].name if enabled else cfg.openai_models[0].name
        except Exception:
            pass
    ensure_builtin_kscc_limits(cfg)
    _ensure_builtin_openai_models(cfg)
    if not cfg.openai_active and cfg.openai_models:
        enabled = [m for m in cfg.openai_models if m.enabled]
        cfg.openai_active = enabled[0].name if enabled else cfg.openai_models[0].name
    return cfg


def save_config(cfg: Config):
    ensure_builtin_kscc_limits(cfg)
    data = {
        "backend": cfg.backend,
        "kscc_model": cfg.kscc_model,
        "openai_models": [
            {
                "name": m.name,
                "api_key": m.api_key,
                "base_url": m.base_url,
                "model": m.model,
                "enabled": m.enabled,
                "context_limit": m.context_limit,
                "max_output_tokens": m.max_output_tokens,
            }
            for m in cfg.openai_models
        ],
        "kscc_model_limits": cfg.kscc_model_limits,
        "openai_active": cfg.openai_active,
        "mode": cfg.mode,
        "workspace": cfg.workspace,
        "max_turns": cfg.max_turns,
        "context_limit": cfg.context_limit,
        "max_output_tokens": cfg.max_output_tokens,
        "theme": cfg.theme,
        "ui_font_family": cfg.ui_font_family,
        "ui_font_size": cfg.ui_font_size,
        "code_font_family": cfg.code_font_family,
        "code_font_size": cfg.code_font_size,
        "accent_color": cfg.accent_color,
        "ide_word_wrap": cfg.ide_word_wrap,
        "ide_minimap": cfg.ide_minimap,
        "ide_font_size": cfg.ide_font_size,
        "skills_enabled": cfg.skills_enabled,
        "skill_debug_log": cfg.skill_debug_log,
        "memory_injection_enabled": cfg.memory_injection_enabled,
        "auto_save_skill_threshold": cfg.auto_save_skill_threshold,
        "feature_task_mode": cfg.feature_task_mode,
        "feature_insight_index": cfg.feature_insight_index,
        "feature_memory_compress": cfg.feature_memory_compress,
        "feature_risk_templates": cfg.feature_risk_templates,
        "feature_evidence_capture": cfg.feature_evidence_capture,
        "feature_adb_tools": cfg.feature_adb_tools,
    }
    CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def get_active_provider(cfg: Config):
    if cfg.backend == "kscc":
        ctx, out = get_kscc_model_limits(cfg, cfg.kscc_model)
        return ProviderConfig(
            name="kscc",
            api_key="",
            base_url="kscc",
            model=cfg.kscc_model,
            context_limit=ctx,
            max_output_tokens=out,
        )
    for model in cfg.openai_models:
        if not model.enabled:
            continue
        if model.name == cfg.openai_active:
            ctx, out = get_effective_model_limits(model.model, "openai", model.context_limit, model.max_output_tokens)
            return ProviderConfig(
                name="openai",
                api_key=model.api_key,
                base_url=model.base_url,
                model=model.model,
                context_limit=ctx,
                max_output_tokens=out,
            )
    enabled = [m for m in cfg.openai_models if m.enabled]
    if enabled:
        model = enabled[0]
        ctx, out = get_effective_model_limits(model.model, "openai", model.context_limit, model.max_output_tokens)
        return ProviderConfig(
            name="openai",
            api_key=model.api_key,
            base_url=model.base_url,
            model=model.model,
            context_limit=ctx,
            max_output_tokens=out,
        )
    ctx, out = get_effective_model_limits("gpt-4o", "openai")
    return ProviderConfig(
        name="openai",
        api_key="",
        base_url="https://api.openai.com/v1",
        model="gpt-4o",
        context_limit=ctx,
        max_output_tokens=out,
    )
