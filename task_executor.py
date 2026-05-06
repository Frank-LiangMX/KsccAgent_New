"""
Task Executor - 任务执行状态机

实现 Plan -> Execute -> Reflect -> Next 的执行循环
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import AsyncGenerator, Callable, Optional

from task_state import (
    Reflection,
    Step,
    StepResult,
    StepStatus,
    TaskPhase,
    TaskState,
    TaskStateBuilder,
)
from task_logger import TaskLogger, create_task_logger


class TaskExecutor:
    """任务执行器"""

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
        执行任务，yield 事件流

        事件类型:
          {"type": "task_start", "task_id": "...", "goal": "..."}
          {"type": "plan_generated", "plan": "..."}
          {"type": "step_start", "step_id": "...", "description": "..."}
          {"type": "step_complete", "step_id": "...", "success": bool, "output": "..."}
          {"type": "step_failed", "step_id": "...", "error": "..."}
          {"type": "reflection", "step_id": "...", "observation": "...", "suggestion": "..."}
          {"type": "task_progress", "progress": {...}}
          {"type": "task_complete", "task_id": "...", "result": "..."}
          {"type": "task_failed", "task_id": "...", "error": "..."}
          {"type": "text_delta", "text": "..."}
          {"type": "tool_call", "name": "...", "arguments": {...}}
          {"type": "tool_result", "name": "...", "result": "...", "error": bool}
        """
        task_id = str(uuid.uuid4())[:8]
        self._current_task = TaskState(task_id=task_id, goal=goal)

        # 初始化日志记录器
        self._logger = create_task_logger(task_id, goal, self._log_dir)

        yield {"type": "task_start", "task_id": task_id, "goal": goal}

        try:
            # Phase 1: Planning
            async for event in self._plan_phase(goal, attachments):
                yield event

            if self._current_task.is_failed():
                self._logger.log_task_failure(self._current_task, "Planning failed")
                yield {"type": "task_failed", "task_id": task_id, "error": "Planning failed"}
                return

            # Phase 2: Execution Loop
            async for event in self._execution_loop():
                yield event

            # Phase 3: Final Result
            if self._current_task.is_complete():
                result = self._build_final_result()
                self._logger.log_task_complete(self._current_task)
                yield {"type": "task_complete", "task_id": task_id, "result": result}
            else:
                self._logger.log_task_failure(self._current_task, "Task did not complete successfully")
                yield {"type": "task_failed", "task_id": task_id, "error": "Task did not complete successfully"}

        except Exception as e:
            self._current_task.add_error({"type": "executor_error", "error": str(e)})
            self._logger.log_task_failure(self._current_task, str(e))
            yield {"type": "task_failed", "task_id": task_id, "error": str(e)}

    async def _plan_phase(self, goal: str, attachments: list[str] = None) -> AsyncGenerator[dict, None]:
        """计划阶段：生成任务计划和步骤"""
        self._current_task.set_phase(TaskPhase.PLANNING)

        # 构建计划提示
        planning_prompt = self._build_planning_prompt(goal)

        # 调用 LLM 生成计划
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
                self._current_task.add_error({"type": "planning_error", "error": event.get("content", "")})
                return

        # 解析计划
        plan_data = self._parse_plan(full_text)
        if not plan_data:
            self._current_task.add_error({"type": "planning_error", "error": "Failed to parse plan"})
            return

        # 设置计划
        self._current_task.plan = plan_data.get("plan", full_text)

        # 创建步骤
        for i, step_desc in enumerate(plan_data.get("steps", []), 1):
            step = Step(
                id=f"step_{i}",
                description=step_desc,
                dependencies=[f"step_{i-1}"] if i > 1 else [],
            )
            self._current_task.add_step(step)

        yield {"type": "plan_generated", "plan": self._current_task.plan, "steps": [s.to_dict() for s in self._current_task.steps]}

    async def _execution_loop(self) -> AsyncGenerator[dict, None]:
        """执行循环：Plan -> Execute -> Reflect -> Next"""
        self._current_task.set_phase(TaskPhase.EXECUTING)

        max_iterations = self.config.max_turns * 2  # 防止无限循环
        iteration = 0

        while iteration < max_iterations:
            iteration += 1

            # 获取下一个待执行步骤
            step = self._get_next_step()
            if not step:
                # 没有待执行的步骤，任务完成
                if self._current_task.get_failed_steps():
                    # 有失败的步骤，任务失败
                    self._current_task.set_phase(TaskPhase.FAILED)
                else:
                    self._current_task.set_phase(TaskPhase.COMPLETED)
                return

            # 执行步骤
            async for event in self._execute_step(step):
                yield event

            # 反思阶段
            async for event in self._reflect_on_step(step):
                yield event

            # 检查是否需要重新规划
            if self._should_replan():
                async for event in self._replan():
                    yield event

            # 更新进度
            yield {"type": "task_progress", "progress": self._current_task.progress()}

    def _get_next_step(self) -> Optional[Step]:
        """获取下一个待执行的步骤"""
        pending = self._current_task.get_pending_steps()
        if not pending:
            return None

        # 检查依赖是否满足
        for step in pending:
            deps_met = all(
                self._current_task.get_step(dep_id) and
                self._current_task.get_step(dep_id).status == StepStatus.SUCCESS
                for dep_id in step.dependencies
            )
            if deps_met:
                return step

        # 如果所有待执行步骤的依赖都未满足，返回第一个
        return pending[0] if pending else None

    async def _execute_step(self, step: Step) -> AsyncGenerator[dict, None]:
        """执行单个步骤"""
        step.mark_running()
        start_time = time.time()

        # 记录步骤开始
        if self._logger:
            self._logger.log_step_start(step)

        yield {"type": "step_start", "step_id": step.id, "description": step.description}

        # 构建执行提示
        execution_prompt = self._build_execution_prompt(step)

        # 调用 LLM 执行
        full_text = ""
        tool_calls = []

        try:
            async for event in self.agent.run(execution_prompt):
                if event["type"] == "text_delta":
                    full_text += event["text"]
                    yield event
                elif event["type"] == "tool_call":
                    tool_calls.append(event)
                    # 记录工具调用
                    if self._logger:
                        self._logger.log_tool_call(step.id, event.get("name", ""), event.get("arguments", {}))
                    yield event
                elif event["type"] == "tool_result":
                    # 记录工具结果
                    if self._logger:
                        self._logger.log_tool_result(step.id, event.get("name", ""), event.get("result", ""), event.get("error", False))
                    yield event
                elif event["type"] == "error":
                    duration = int((time.time() - start_time) * 1000)
                    result = StepResult(
                        success=False,
                        output=full_text,
                        error=event.get("content", ""),
                        tool_calls=tool_calls,
                        duration_ms=duration,
                    )
                    step.mark_failed(event.get("content", ""), result)

                    # 记录步骤失败
                    if self._logger:
                        self._logger.log_step_execution(step, {"prompt": execution_prompt}, {"output": full_text, "error": event.get("content", ""), "tool_calls": tool_calls})
                        self._logger.log_step_failure(step, event.get("content", ""))

                    yield {"type": "step_failed", "step_id": step.id, "error": event.get("content", "")}
                    return

            # 步骤执行成功
            duration = int((time.time() - start_time) * 1000)
            result = StepResult(
                success=True,
                output=full_text,
                tool_calls=tool_calls,
                duration_ms=duration,
            )
            step.mark_success(result)

            # 记录步骤成功
            if self._logger:
                self._logger.log_step_execution(step, {"prompt": execution_prompt}, {"output": full_text, "tool_calls": tool_calls})
                self._logger.log_step_complete(step)

            yield {"type": "step_complete", "step_id": step.id, "success": True, "output": full_text[:500]}

        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            result = StepResult(
                success=False,
                output=full_text,
                error=str(e),
                tool_calls=tool_calls,
                duration_ms=duration,
            )
            step.mark_failed(str(e), result)

            # 记录步骤异常
            if self._logger:
                self._logger.log_step_execution(step, {"prompt": execution_prompt}, {"output": full_text, "error": str(e), "tool_calls": tool_calls})
                self._logger.log_step_failure(step, str(e))

            yield {"type": "step_failed", "step_id": step.id, "error": str(e)}

    async def _reflect_on_step(self, step: Step) -> AsyncGenerator[dict, None]:
        """反思步骤执行结果"""
        self._current_task.set_phase(TaskPhase.REFLECTING)

        # 构建反思提示
        reflection_prompt = self._build_reflection_prompt(step)

        # 调用 LLM 反思
        full_text = ""
        async for event in self.agent.run(reflection_prompt):
            if event["type"] == "text_delta":
                full_text += event["text"]
                yield event
            elif event["type"] == "error":
                # 反思失败不影响主流程
                pass

        # 解析反思结果
        reflection_data = self._parse_reflection(full_text, step.id)
        if reflection_data:
            self._current_task.add_reflection(reflection_data)

            # 记录反思结果
            if self._logger:
                self._logger.log_reflection(
                    step.id,
                    reflection_data.observation,
                    reflection_data.suggestion,
                    reflection_data.should_retry,
                    reflection_data.should_replan,
                )

            yield {
                "type": "reflection",
                "step_id": step.id,
                "observation": reflection_data.observation,
                "suggestion": reflection_data.suggestion,
                "should_retry": reflection_data.should_retry,
                "should_replan": reflection_data.should_replan,
            }

            # 处理失败恢复策略
            if step.status == StepStatus.FAILED:
                async for event in self._handle_failure_recovery(step, reflection_data):
                    yield event

        self._current_task.set_phase(TaskPhase.EXECUTING)

    async def _handle_failure_recovery(self, step: Step, reflection: Reflection) -> AsyncGenerator[dict, None]:
        """处理失败恢复策略"""
        # 分析失败类型
        failure_type = self._classify_failure(step, reflection)

        yield {
            "type": "failure_analysis",
            "step_id": step.id,
            "failure_type": failure_type,
            "observation": reflection.observation,
        }

        # 根据失败类型选择恢复策略
        if failure_type == "transient":
            # 临时性错误：自动重试
            async for event in self._retry_strategy(step, reflection):
                yield event
        elif failure_type == "approach":
            # 方法错误：改写子任务
            async for event in self._rewrite_strategy(step, reflection):
                yield event
        elif failure_type == "dependency":
            # 依赖问题：降级处理
            async for event in self._degrade_strategy(step, reflection):
                yield event
        else:
            # 未知类型：尝试重试，如果失败则跳过
            if step.can_retry():
                async for event in self._retry_strategy(step, reflection):
                    yield event
            else:
                yield {
                    "type": "step_skipped",
                    "step_id": step.id,
                    "reason": f"Unrecoverable failure: {failure_type}",
                }

    def _classify_failure(self, step: Step, reflection: Reflection) -> str:
        """分类失败类型"""
        error = step.result.error if step.result else ""
        observation = reflection.observation.lower()

        # 临时性错误：网络、超时、资源限制
        transient_patterns = [
            "timeout", "network", "connection", "rate limit", "temporary",
            "retry", "again", "later", "busy", "overloaded",
        ]
        if any(pattern in error.lower() or pattern in observation for pattern in transient_patterns):
            return "transient"

        # 方法错误：逻辑错误、不正确的假设
        approach_patterns = [
            "wrong approach", "incorrect", "invalid", "assumption", "logic",
            "method", "strategy", "改用", "换个方法", "重新考虑",
        ]
        if any(pattern in error.lower() or pattern in observation for pattern in approach_patterns):
            return "approach"

        # 依赖问题：缺少依赖、权限、资源
        dependency_patterns = [
            "permission", "access", "denied", "not found", "missing",
            "dependency", "required", "权限", "依赖", "缺少",
        ]
        if any(pattern in error.lower() or pattern in observation for pattern in dependency_patterns):
            return "dependency"

        return "unknown"

    async def _retry_strategy(self, step: Step, reflection: Reflection) -> AsyncGenerator[dict, None]:
        """重试策略"""
        if not step.can_retry():
            yield {
                "type": "retry_exhausted",
                "step_id": step.id,
                "retry_count": step.retry_count,
                "max_retries": step.max_retries,
            }
            return

        step.mark_retrying()
        yield {
            "type": "step_retry",
            "step_id": step.id,
            "retry_count": step.retry_count,
            "strategy": "retry",
            "reason": reflection.suggestion or "Transient error, retrying",
        }

        # 重新执行步骤
        async for event in self._execute_step(step):
            yield event

    async def _rewrite_strategy(self, step: Step, reflection: Reflection) -> AsyncGenerator[dict, None]:
        """改写子任务策略"""
        yield {
            "type": "step_rewrite",
            "step_id": step.id,
            "original_description": step.description,
            "reason": reflection.suggestion or "Approach error, rewriting task",
        }

        # 构建改写提示
        rewrite_prompt = self._build_rewrite_prompt(step, reflection)

        # 调用 LLM 改写
        full_text = ""
        async for event in self.agent.run(rewrite_prompt):
            if event["type"] == "text_delta":
                full_text += event["text"]
                yield event

        # 解析改写结果
        new_description = self._parse_rewritten_step(full_text)
        if new_description:
            # 更新步骤描述
            step.description = new_description
            step.status = StepStatus.PENDING
            step.retry_count = 0  # 重置重试计数

            yield {
                "type": "step_rewritten",
                "step_id": step.id,
                "new_description": new_description,
            }

            # 重新执行步骤
            async for event in self._execute_step(step):
                yield event

    async def _degrade_strategy(self, step: Step, reflection: Reflection) -> AsyncGenerator[dict, None]:
        """降级策略"""
        yield {
            "type": "step_degrade",
            "step_id": step.id,
            "reason": reflection.suggestion or "Dependency issue, degrading task",
        }

        # 构建降级提示
        degrade_prompt = self._build_degrade_prompt(step, reflection)

        # 调用 LLM 降级
        full_text = ""
        async for event in self.agent.run(degrade_prompt):
            if event["type"] == "text_delta":
                full_text += event["text"]
                yield event

        # 解析降级结果
        degraded_step = self._parse_degraded_step(full_text)
        if degraded_step:
            # 更新步骤为降级版本
            step.description = degraded_step.get("description", step.description)
            step.status = StepStatus.PENDING
            step.metadata["degraded"] = True
            step.metadata["original_description"] = step.description
            step.metadata["degrade_reason"] = reflection.suggestion

            yield {
                "type": "step_degraded",
                "step_id": step.id,
                "new_description": step.description,
                "degraded": True,
            }

            # 重新执行降级后的步骤
            async for event in self._execute_step(step):
                yield event
        else:
            # 降级失败，跳过步骤
            yield {
                "type": "step_skipped",
                "step_id": step.id,
                "reason": "Failed to degrade step",
            }

    def _build_rewrite_prompt(self, step: Step, reflection: Reflection) -> str:
        """构建改写提示"""
        return f"""请重新描述以下任务步骤，采用不同的方法：

原始步骤：{step.description}
失败原因：{step.result.error if step.result else '未知'}
反思建议：{reflection.suggestion}

请提供一个新的步骤描述，采用不同的方法或策略。要求：
1. 保持相同的最终目标
2. 使用不同的方法或路径
3. 更具体、更可执行

输出格式：
```json
{{
    "description": "新的步骤描述"
}}
```"""

    def _build_degrade_prompt(self, step: Step, reflection: Reflection) -> str:
        """构建降级提示"""
        return f"""请为以下任务步骤提供降级方案：

原始步骤：{step.description}
失败原因：{step.result.error if step.result else '未知'}
反思建议：{reflection.suggestion}

请提供一个简化的、降级版本的步骤。要求：
1. 降低复杂度或要求
2. 跳过非关键部分
3. 提供基本功能或近似结果

输出格式：
```json
{{
    "description": "降级后的步骤描述",
    "compromise": "降级说明"
}}
```"""

    def _parse_rewritten_step(self, text: str) -> Optional[str]:
        """解析改写后的步骤"""
        try:
            import re
            json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(1))
                return data.get("description")
            return None
        except:
            return None

    def _parse_degraded_step(self, text: str) -> Optional[dict]:
        """解析降级后的步骤"""
        try:
            import re
            json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))
            return None
        except:
            return None

    def _should_replan(self) -> bool:
        """检查是否需要重新规划"""
        failed_steps = self._current_task.get_failed_steps()
        if not failed_steps:
            return False

        # 检查最近的反思记录
        if self._current_task.reflections:
            latest_reflection = self._current_task.reflections[-1]
            return latest_reflection.should_replan

        return False

    async def _replan(self) -> AsyncGenerator[dict, None]:
        """重新规划"""
        yield {"type": "replan_start", "reason": "Failed steps detected"}

        # 构建重新规划提示
        replan_prompt = self._build_replan_prompt()

        # 调用 LLM 重新规划
        full_text = ""
        async for event in self.agent.run(replan_prompt):
            if event["type"] == "text_delta":
                full_text += event["text"]
                yield event

        # 解析新计划
        new_plan = self._parse_plan(full_text)
        if new_plan:
            # 更新计划
            self._current_task.plan = new_plan.get("plan", self._current_task.plan)

            # 添加新步骤（替换失败的步骤）
            failed_steps = self._current_task.get_failed_steps()
            for failed_step in failed_steps:
                # 移除失败的步骤
                self._current_task.steps = [s for s in self._current_task.steps if s.id != failed_step.id]

            # 添加新步骤
            existing_count = len(self._current_task.steps)
            for i, step_desc in enumerate(new_plan.get("steps", []), existing_count + 1):
                step = Step(
                    id=f"step_{i}",
                    description=step_desc,
                    dependencies=[f"step_{i-1}"] if i > 1 else [],
                )
                self._current_task.add_step(step)

            yield {"type": "replan_complete", "new_steps": [s.to_dict() for s in self._current_task.steps]}

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

    def _build_execution_prompt(self, step: Step) -> str:
        """构建执行提示"""
        context = self._build_context()
        return f"""请执行以下任务步骤：

当前步骤：{step.description}
步骤ID：{step.id}

任务上下文：
{context}

请直接执行这个步骤，完成具体的工作。如果需要使用工具，请使用工具完成任务。"""

    def _build_reflection_prompt(self, step: Step) -> str:
        """构建反思提示"""
        result_summary = ""
        if step.result:
            result_summary = f"""
执行结果：
- 成功：{step.result.success}
- 输出：{step.result.output[:500] if step.result.output else '无'}
- 错误：{step.result.error if step.result.error else '无'}
- 耗时：{step.result.duration_ms}ms"""

        return f"""请反思以下步骤的执行情况：

步骤：{step.description}
{result_summary}

请分析：
1. 执行是否成功？
2. 如果失败，原因是什么？
3. 是否需要重试？
4. 是否需要调整后续计划？

输出格式：
```json
{{
    "observation": "执行观察",
    "issue": "问题描述（如果有的话）",
    "suggestion": "改进建议",
    "should_retry": true/false,
    "should_replan": true/false
}}
```"""

    def _build_replan_prompt(self) -> str:
        """构建重新规划提示"""
        failed_steps = self._current_task.get_failed_steps()
        failed_info = "\n".join([f"- {s.id}: {s.description} (错误: {s.result.error if s.result else '未知'})" for s in failed_steps])

        return f"""任务执行遇到问题，需要重新规划：

原始任务：{self._current_task.goal}
当前计划：{self._current_task.plan}

失败的步骤：
{failed_info}

请根据失败原因，重新制定执行计划。输出格式与之前相同。"""

    def _build_context(self) -> str:
        """构建上下文信息"""
        context_parts = []

        # 添加已完成步骤的结果
        completed = self._current_task.get_completed_steps()
        if completed:
            context_parts.append("已完成的步骤：")
            for step in completed[-3:]:  # 只显示最近3个
                output = step.result.output[:200] if step.result and step.result.output else "无输出"
                context_parts.append(f"- {step.id}: {step.description} -> {output}")

        # 添加反思建议
        if self._current_task.reflections:
            latest = self._current_task.reflections[-1]
            if latest.suggestion:
                context_parts.append(f"\n最新建议：{latest.suggestion}")

        return "\n".join(context_parts) if context_parts else "无"

    def _parse_plan(self, text: str) -> Optional[dict]:
        """解析计划文本"""
        try:
            # 尝试从文本中提取 JSON
            import re
            json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
                return json.loads(json_str)

            # 尝试直接解析整个文本
            return json.loads(text)
        except:
            # 解析失败，使用默认格式
            lines = text.strip().split('\n')
            steps = []
            for line in lines:
                line = line.strip()
                if line and (line.startswith('-') or line.startswith('*') or line[0].isdigit()):
                    # 清理步骤描述
                    step_desc = line.lstrip('-*0123456789. ')
                    if step_desc:
                        steps.append(step_desc)

            if steps:
                return {
                    "plan": text[:200] + "..." if len(text) > 200 else text,
                    "steps": steps,
                }
            return None

    def _parse_reflection(self, text: str, step_id: str) -> Optional[Reflection]:
        """解析反思文本"""
        try:
            # 尝试从文本中提取 JSON
            import re
            json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
                data = json.loads(json_str)
                return Reflection(
                    step_id=step_id,
                    observation=data.get("observation", ""),
                    issue=data.get("issue"),
                    suggestion=data.get("suggestion"),
                    should_retry=data.get("should_retry", False),
                    should_replan=data.get("should_replan", False),
                )

            # 尝试直接解析
            data = json.loads(text)
            return Reflection(
                step_id=step_id,
                observation=data.get("observation", ""),
                issue=data.get("issue"),
                suggestion=data.get("suggestion"),
                should_retry=data.get("should_retry", False),
                should_replan=data.get("should_replan", False),
            )
        except:
            # 解析失败，创建默认反思
            return Reflection(
                step_id=step_id,
                observation=text[:500] if text else "No reflection",
                suggestion="Continue with next step",
            )

    def _build_final_result(self) -> str:
        """构建最终结果"""
        completed = self._current_task.get_completed_steps()
        if not completed:
            return "No steps completed"

        # 收集所有步骤的输出
        outputs = []
        for step in completed:
            if step.result and step.result.output:
                outputs.append(f"## {step.description}\n{step.result.output[:1000]}")

        return "\n\n".join(outputs)

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
