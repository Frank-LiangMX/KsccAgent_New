"""
Kscc Agent - CLI 入口

使用方式:
  python main.py "帮我创建一个 Python Flask 应用"
  python main.py --mode solo "重构 src/ 目录下的代码"
  python main.py --provider openai --model gpt-4o "写一个快速排序"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from agent import Agent
from config import Config, ProviderConfig, load_config, save_config

# ANSI 颜色
GRAY = "\033[90m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"


async def main_async():
    parser = argparse.ArgumentParser(description="Kscc Agent - AI Coding Assistant")
    parser.add_argument("prompt", nargs="*", help="Task description")
    parser.add_argument("--mode", choices=["solo", "ide"], help="Agent mode (default: ide)")
    parser.add_argument("--provider", choices=["openai", "anthropic", "deepseek", "kscc"], help="LLM provider")
    parser.add_argument("--model", help="Model name (e.g., gpt-4o, claude-sonnet-4-5)")
    parser.add_argument("--api-key", help="API key (or set env var)")
    parser.add_argument("--base-url", help="API base URL")
    parser.add_argument("--workspace", "-w", help="Working directory")
    parser.add_argument("--max-turns", type=int, default=50, help="Max agent loop turns")
    parser.add_argument("--init", action="store_true", help="Initialize config.json interactively")
    parser.add_argument("--file", "-f", action="append", help="Attach files to prompt")
    parser.add_argument("--config", action="store_true", help="Show current config")
    args = parser.parse_args()

    # 交互式初始化
    if args.init:
        interactive_init()
        return

    # 显示配置
    if args.config:
        show_config()
        return

    # 加载配置
    cfg = load_config()

    # 命令行覆盖
    if args.provider:
        cfg.provider = args.provider
        if cfg.provider not in cfg.providers:
            cfg.providers[cfg.provider] = ProviderConfig(name=cfg.provider)
    if args.model and cfg.provider in cfg.providers:
        cfg.providers[cfg.provider].model = args.model
    if args.api_key and cfg.provider in cfg.providers:
        cfg.providers[cfg.provider].api_key = args.api_key
    if args.base_url and cfg.provider in cfg.providers:
        cfg.providers[cfg.provider].base_url = args.base_url
    if args.workspace:
        cfg.workspace = os.path.abspath(args.workspace)
    cfg.max_turns = args.max_turns

    mode = args.mode or cfg.mode
    provider = cfg.active_provider()

    # 验证 API key
    if not provider.api_key:
        env_name = f"{provider.name.upper()}_API_KEY"
        print(f"{RED}Error: No API key for '{provider.name}'.{RESET}")
        print(f"  Set environment variable {env_name} or use --api-key")
        print(f"  Or run: python main.py --init")
        sys.exit(1)

    if not provider.model:
        print(f"{RED}Error: No model specified for '{provider.name}'.{RESET}")
        print(f"  Use --model or set in config.json")
        sys.exit(1)

    # 获取 prompt
    prompt = " ".join(args.prompt) if args.prompt else None
    if not prompt:
        if not sys.stdin.isatty():
            prompt = sys.stdin.read().strip()
        else:
            prompt = input(f"{BOLD}Enter your prompt:{RESET}\n> ")
    if not prompt:
        print(f"{RED}No prompt provided.{RESET}")
        sys.exit(1)

    # 创建工作目录
    Path(cfg.workspace).mkdir(parents=True, exist_ok=True)

    # 显示运行信息
    print(f"{GRAY}╔{'═' * 58}╗{RESET}")
    print(f"{GRAY}║{RESET} {BOLD}Kscc Agent{RESET}")
    print(f"{GRAY}║{RESET} Provider : {CYAN}{provider.name}{RESET}  Model: {CYAN}{provider.model}{RESET}")
    print(f"{GRAY}║{RESET} Mode     : {YELLOW}{mode}{RESET}  Workspace: {GRAY}{cfg.workspace}{RESET}")
    print(f"{GRAY}║{RESET} Max turns: {cfg.max_turns}")
    print(f"{GRAY}╚{'═' * 58}╝{RESET}")
    print()

    # 创建 Agent
    agent = Agent(config=cfg, mode=mode)

    # IDE 模式的确认回调
    if mode == "ide":
        agent.on("confirm", _terminal_confirm)

    # 运行
    attachments = [os.path.abspath(f) for f in (args.file or [])]
    full_text = ""
    turns = 0

    print(f"{GRAY}── Agent starting ──{RESET}\n")

    try:
        async for event in agent.run(prompt, attachments):
            t = event["type"]
            if t == "text_delta":
                sys.stdout.write(event["text"])
                sys.stdout.flush()
                full_text += event["text"]
            elif t == "tool_call":
                print(f"\n{GRAY}  ⚙ {event['name']}: {event.get('preview', '')}{RESET}")
            elif t == "tool_result":
                marker = f"{RED}✗{RESET}" if event.get("error") else f"{GREEN}✓{RESET}"
                result = event["result"]
                if len(result) > 300:
                    result = result[:300] + "..."
                print(f"{GRAY}  {marker} {result}{RESET}")
            elif t == "confirm":
                # IDE 模式确认已在回调中处理
                pass
            elif t == "done":
                turns = event.get("turns", 0)
            elif t == "error":
                print(f"\n{RED}Error: {event['content']}{RESET}")
                break
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Interrupted.{RESET}")
    except Exception as e:
        print(f"\n{RED}Exception: {e}{RESET}")

    print(f"\n{GRAY}── Done ({turns} turns) ──{RESET}")


def _terminal_confirm(tool_call):
    """IDE 模式终端确认。"""
    preview = tool_call.get("preview", "")
    name = tool_call.get("name", "unknown")
    print(f"\n{YELLOW}┌─ Review ──────────────────────────────┐{RESET}")
    print(f"{YELLOW}│{RESET} {BOLD}{name}{RESET}")
    for line in preview.split("\n"):
        print(f"{YELLOW}│{RESET} {GRAY}{line}{RESET}")
    print(f"{YELLOW}└────────────────────────────────────────┘{RESET}")
    while True:
        choice = input(f"{BOLD}[A]pply / [R]eject / [E]dit?{RESET} ").strip().lower()
        if choice in ("a", "y", "yes", "apply", ""):
            return True
        if choice in ("r", "n", "no", "reject"):
            return False
        if choice in ("e", "edit"):
            # 简单编辑：让用户修改参数后重新执行
            print(f"{GRAY}Edit mode not yet implemented. Applying as-is.{RESET}")
            return True
        print(f"{RED}Please enter A/R/E{RESET}")


def interactive_init():
    """交互式初始化配置。"""
    print(f"\n{BOLD}Kscc Agent - Initial Setup{RESET}\n")
    cfg = load_config()

    providers = list(Config().providers.keys())
    print("Available providers:")
    for i, p in enumerate(providers, 1):
        print(f"  {i}. {p}")
    default_idx = providers.index(cfg.provider) if cfg.provider in providers else 0
    choice = input(f"\nChoose provider [{default_idx + 1}]: ").strip()
    if choice:
        try:
            cfg.provider = providers[int(choice) - 1]
        except (ValueError, IndexError):
            pass
    print(f"Provider: {cfg.provider}")

    prov = cfg.providers.get(cfg.provider)
    if not prov:
        prov = ProviderConfig(name=cfg.provider)
        cfg.providers[cfg.provider] = prov

    api_key = input(f"API key [{prov.api_key[:8]}...]: ").strip() if prov.api_key else input(f"API key: ").strip()
    if api_key:
        prov.api_key = api_key

    model = input(f"Model [{prov.model or 'auto'}]: ").strip()
    if model:
        prov.model = model

    base_url = input(f"Base URL [{prov.base_url or 'auto'}]: ").strip()
    if base_url:
        prov.base_url = base_url

    mode = input(f"Default mode (solo/ide) [{cfg.mode}]: ").strip()
    if mode in ("solo", "ide"):
        cfg.mode = mode

    workspace = os.path.abspath(input(f"Workspace [{cfg.workspace}]: ").strip() or cfg.workspace)
    cfg.workspace = workspace

    save_config(cfg)
    print(f"\n{GREEN}Configuration saved to config.json{RESET}\n")


def show_config():
    cfg = load_config()
    prov = cfg.active_provider()
    print(f"\n{BOLD}Current Configuration{RESET}")
    print(f"  Provider  : {cfg.provider}")
    print(f"  API Key   : {prov.api_key[:12]}..." if prov.api_key else "  API Key   : (not set)")
    print(f"  Model     : {prov.model}")
    print(f"  Base URL  : {prov.base_url}")
    print(f"  Mode      : {cfg.mode}")
    print(f"  Workspace : {cfg.workspace}")
    print(f"  Max Turns : {cfg.max_turns}")
    print()


def main():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
