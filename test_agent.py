"""快速验证脚本 - 测试 Agent + Context 追踪"""
import asyncio, sys, json
sys.path.insert(0, "E:/QuarkSpace/Agent")

from agent import Agent
from config import load_config

async def test_agent():
    cfg = load_config()
    cfg.max_turns = 5
    cfg.workspace = "E:/QuarkSpace/Agent"
    
    print(f"Testing: {cfg.provider}/{cfg.active_provider().model}\n")
    
    agent = Agent(config=cfg, mode="solo")
    prompt = "Say hello and list the files in the current directory. Then create a file called hello.py with a simple print('hello world') in it."
    
    try:
        async for event in agent.run(prompt):
            t = event["type"]
            if t == "text_delta":
                sys.stdout.write(event["text"])
                sys.stdout.flush()
            elif t == "tool_call":
                print(f"\n  [tool] {event['name']}: {event.get('preview','')}")
            elif t == "tool_result":
                r = event['result']
                print(f"  [result] {r[:200]}")
            elif t == "context":
                s = event["summary"]
                w = event.get("warning", "") or event.get("note", "")
                print(f"\n  [ctx] {s['current_usage']}/{s['limit']} tokens ({s['usage_ratio']:.0%}) {w}")
            elif t == "done":
                s = event.get("context", {})
                print(f"\n--- Done ({event.get('turns')} turns, {s.get('total_input', '?')}/{s.get('total_output', '?')} in/out tokens) ---")
            elif t == "error":
                print(f"\n[error] {event['content']}")
    except Exception as e:
        print(f"\nException: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test_agent())
