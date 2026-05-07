"""
Task Executor - 任务执行器

使用 GA 原生 agent.run() 循环执行任务：
1. Plan 阶段：独立 agent.run() 生成计划（供 UI 展示）
2. Execute 阶段：单次 agent.run() 注入计划上下文，由 agent 自主执行
   - 工具失败 → 错误反馈给 LLM → LLM 自行决定重试/换方案
   - 不需要外部反思、重试、降级机制
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import AsyncGenerator, Callable, Optional

from task_state import TaskPhase, TaskState
from task_logger import TaskLogger, create_task_logger


class TaskExecutor:
    """任务执行器 — 基于 GA 原生循环"""

    def __init__(self, agent, config=None, log_dir: str = "logs/tasks"):
        self.agent = agent
        self.config = config or agent.config
        self._hooks: dict[str, Callable] = {}
        self._current_task: Optional[TaskState] = None
        self._logger: Optional[TaskLogger] = None
        self._log_dir = log_dir

    def on(self, event: str, callback: Callable):
        """注册事件钩子"""
        self._hooks[event] = callback

    async def execute_task(self, goal: str, attachments: list[str] = None) -> AsyncGenerator[dict, None]:
        """
        执行任务，yield 事件流。

        事件类型:
          {"type": "task_start", "task_id": "...", "goal": "..."}
          {"type": "plan_generated", "plan": "...", "steps": [...]}
          {"type": "task_progress", "progress": {...}}
          {"type": "task_complete", "task_id": "...", "result": "..."}
          {"type": "task_failed", "task_id": "...", "error": "..."}
          {"type": "text_delta", "text": "..."}
          {"type": "tool_call", "name": "...", "arguments": {...}, "preview": "..."}
          {"type": "tool_result", "name": "...", "result": "...", "error": bool}
        """
        task_id = str(uuid.uuid4())[:8]
        self._current_task = TaskState(task_id=task_id, goal=goal)

        # 初始化日志记录器
        self._logger = create_task_logger(task_id, goal, self._log_dir)

        yield {"type": "task_start", "task_id": task_id, "goal": goal}

        try:
            # Phase 1: Planning（独立 agent.run()，生成计划供 UI 展示）
            async for event in self._plan_phase(goal, attachments):
                yield event

            if self._current_task.is_failed():
                self._logger.log_task_failure(self._current_task, "Planning failed")
                yield {"type": "task_failed", "task_id": task_id, "error": "Planning failed"}
                return

            # Phase 2: Execution（单次 agent.run()，GA 原生循环）
            self._current_task.set_phase(TaskPhase.EXECUTING)
            async for event in self._execution_phase():
                yield event

            # Phase 3: Final Result
            result = self._current_task.metadata.get("execution_result", "Task completed.")
            self._current_task.set_phase(TaskPhase.COMPLETED)
            self._logger.log_task_complete(self._current_task)
            yield {"type": "task_complete", "task_id": task_id, "result": result}

        except Exception as e:
            self._current_task.add_error({"type": "executor_error", "error": str(e)})
            self._logger.log_task_failure(self._current_task, str(e))
            yield {"type": "task_failed", "task_id": task_id, "error": str(e)}

    async def resume_task(self, task_state: TaskState) -> AsyncGenerator[dict, None]:
        """
        从已有任务状态恢复执行（跳过规划，重新执行）。
        将之前的计划作为上下文注入，agent 自主判断哪些已完成。
        """
        self._current_task = task_state
        task_id = task_state.task_id

        # 初始化日志
        self._logger = create_task_logger(task_id + "_resume", task_state.goal, self._log_dir)

        yield {
            "type": "task_resume",
            "task_id": task_id,
            "goal": task_state.goal,
            "resume_info": {"plan": task_state.plan or ""},
        }

        try:
            self._current_task.set_phase(TaskPhase.EXECUTING)

            # 直接进入执行阶段（跳过规划），plan 作为上下文注入
            async for event in self._execution_phase():
                yield event

            result = self._current_task.metadata.get("execution_result", "Task completed.")
            self._current_task.set_phase(TaskPhase.COMPLETED)
            self._logger.log_task_complete(self._current_task)
            yield {"type": "task_complete", "task_id": task_id, "result": result}

        except Exception as e:
            self._current_task.add_error({"type": "executor_error", "error": str(e)})
            self._logger.log_task_failure(self._current_task, str(e))
            yield {"type": "task_failed", "task_id": task_id, "error": str(e)}

    # ── Plan Phase ────────────────────────────────────────────

    async def _plan_phase(self, goal: str, attachments: list[str] = None) -> AsyncGenerator[dict, None]:
        """计划阶段：生成任务计划（供 UI 展示步骤列表）"""
        self._current_task.set_phase(TaskPhase.PLANNING)

        planning_prompt = self._build_planning_prompt(goal)

        # 记录 agent.messages 当前长度，用于后续标记内部消息
        msg_count_before = len(self.agent.messages)

        full_text = ""
        async for event in self.agent.run(planning_prompt, attachments):
            if event["type"] == "text_delta":
                full_text += event["text"]
                yield event
            elif event["type"] == "tool_call":
                yield event
            elif event["type"] == "tool_result":
                yield event
            elif event["type"] == "error":
                # 标记计划阶段的内部消息（即使失败也要标记）
                for msg in self.agent.messages[msg_count_before:]:
                    msg["_internal"] = True
                self._current_task.add_error({"type": "planning_error", "error": event.get("content", "")})
                return

        # 标记计划阶段添加的内部消息（user prompt + assistant response）
        for msg in self.agent.messages[msg_count_before:]:
            msg["_internal"] = True

        # 解析计划
        plan_data = self._parse_plan(full_text)
        if not plan_data:
            self._current_task.add_error({"type": "planning_error", "error": "Failed to parse plan"})
            return

        self._current_task.plan = plan_data.get("plan", full_text)

        # 创建步骤（仅用于 UI 展示，不影响执行逻辑）
        from task_state import Step
        parsed_steps = list(plan_data.get("steps", []) or [])
        if not parsed_steps:
            parsed_steps = ["执行任务并在完成后给出结果总结"]
        for i, step_desc in enumerate(parsed_steps, 1):
            step = Step(
                id=f"step_{i}",
                description=step_desc,
                dependencies=[f"step_{i-1}"] if i > 1 else [],
            )
            self._current_task.add_step(step)

        yield {
            "type": "plan_generated",
            "plan": self._current_task.plan,
            "steps": [s.to_dict() for s in self._current_task.steps],
        }
        yield {
            "type": "task_progress",
            "progress": {"tool_count": 0, "phase": "planning"},
        }

    # ── Execution Phase ───────────────────────────────────────

    async def _execution_phase(self) -> AsyncGenerator[dict, None]:
        """
        执行阶段：单次 agent.run()，GA 原生循环。
        - 工具失败 → 错误文本反馈给 LLM → LLM 自行重试/换方案
        - 不需要外部反思、重试、降级
        - 任务完成由 LLM 自然结束（无工具调用）决定
        """
        execution_prompt = self._build_execution_prompt()
        tool_count = 0
        full_text = ""

        # 记录 agent.messages 当前长度，用于后续标记内部消息
        msg_count_before = len(self.agent.messages)

        async for event in self.agent.run(execution_prompt):
            t = event.get("type", "")

            if t == "text_delta":
                full_text += event["text"]
                yield event

            elif t == "tool_call":
                tool_count += 1
                yield event
                # 记录工具调用
                if self._logger:
                    self._logger.log_tool_call("execution", event.get("name", ""), event.get("arguments", {}))
                # 第一个工具调用时立即报告进度（标记第一步为 RUNNING）
                if tool_count == 1:
                    yield {
                        "type": "task_progress",
                        "progress": {"tool_count": tool_count, "phase": "executing"},
                    }

            elif t == "tool_result":
                yield event
                # 记录工具结果
                if self._logger:
                    self._logger.log_tool_result(
                        "execution",
                        event.get("name", ""),
                        event.get("result", ""),
                        event.get("error", False),
                    )
                # 进度更新（每 3 个工具调用报告一次）
                if tool_count % 3 == 0:
                    yield {
                        "type": "task_progress",
                        "progress": {"tool_count": tool_count, "phase": "executing"},
                    }

            elif t == "error":
                # agent.run() 的 error 事件（API 错误、max_turns 等）
                error_content = event.get("content", "Unknown error")
                self._current_task.add_error({"type": "execution_error", "error": error_content})
                self._current_task.metadata["execution_result"] = full_text or "Execution interrupted."
                # 标记执行阶段的内部消息
                for msg in self.agent.messages[msg_count_before:]:
                    msg["_internal"] = True
                if "max turns" in error_content.lower():
                    yield {"type": "task_progress", "progress": {"tool_count": tool_count, "phase": "max_turns"}}
                    return
                self._current_task.metadata["execution_result"] = full_text or error_content
                return

            elif t == "done":
                # agent 自然结束（LLM 没有工具调用了）
                self._current_task.metadata["execution_result"] = event.get("text", full_text)
                # 标记执行阶段的内部消息
                for msg in self.agent.messages[msg_count_before:]:
                    msg["_internal"] = True
                yield {"type": "task_progress", "progress": {"tool_count": tool_count, "phase": "completed"}}
                return

            # 忽略其他事件（thinking, context, skill 等），不向上 yield

    # ── Prompt Builders ───────────────────────────────────────

    def _build_planning_prompt(self, goal: str) -> str:
        """构建计划提示"""
        return f"""请为以下任务制定执行计划：

任务目标：{goal}

请按以下格式输出计划：

1. 首先分析任务需求
2. 制定详细的执行步骤
3. 每个步骤应该是一个独立的、可执行的操作

输出格式：
```json
{{
    "plan": "任务计划概述",
    "steps": [
        "步骤1: 具体描述",
        "步骤2: 具体描述",
        ...
    ]
}}
```

注意：
- 步骤应该具体、可执行
- 步骤之间应该有逻辑顺序
- 每个步骤应该有明确的完成标准"""

    def _build_execution_prompt(self) -> str:
        """构建执行提示 — 注入计划上下文"""
        plan = self._current_task.plan or "（无计划，请根据任务目标直接执行）"
        goal = self._current_task.goal

        return f"""请执行以下任务：

任务目标：{goal}

执行计划：
{plan}

请按照计划逐步执行。如果某一步遇到错误，请自行判断是否重试或换一种方法继续。
完成所有步骤后，输出执行总结。"""

    # ── Plan Parsing ──────────────────────────────────────────

    def _parse_plan(self, text: str) -> Optional[dict]:
        """解析计划文本"""
        try:
            json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))
            return json.loads(text)
        except Exception:
            lines = text.strip().split('\n')
            steps = []
            for line in lines:
                line = line.strip()
                if line and (line.startswith('-') or line.startswith('*') or (line and line[0].isdigit())):
                    step_desc = line.lstrip('-*0123456789. ')
                    if step_desc:
                        steps.append(step_desc)
            if steps:
                return {
                    "plan": text[:200] + "..." if len(text) > 200 else text,
                    "steps": steps,
                }
            return None

    # ── State Access ──────────────────────────────────────────

    def get_current_task(self) -> Optional[TaskState]:
        """获取当前任务状态"""
        return self._current_task

    def save_task_state(self, filepath: str):
        """保存任务状态到文件"""
        if self._current_task:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(self._current_task.to_json())

    def load_task_state(self, filepath: str) -> Optional[TaskState]:
        """从文件加载任务状态"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._current_task = TaskState.from_dict(data)
            return self._current_task
        except Exception:
            return None
