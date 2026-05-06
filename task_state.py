"""
Task State - 任务状态管理

定义统一的任务状态结构，支持：
- 任务计划（plan）
- 执行步骤（steps）
- 执行结果（results）
- 反思修正（reflection）
- 错误追踪（errors）
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class StepStatus(Enum):
    """步骤执行状态"""
    PENDING = "pending"          # 等待执行
    RUNNING = "running"          # 正在执行
    SUCCESS = "success"          # 执行成功
    FAILED = "failed"            # 执行失败
    SKIPPED = "skipped"          # 跳过执行
    RETRYING = "retrying"        # 重试中


class TaskPhase(Enum):
    """任务执行阶段"""
    PLANNING = "planning"        # 计划阶段
    EXECUTING = "executing"      # 执行阶段
    REFLECTING = "reflecting"    # 反思阶段
    COMPLETED = "completed"      # 任务完成
    FAILED = "failed"            # 任务失败


@dataclass
class StepResult:
    """步骤执行结果"""
    success: bool
    output: str = ""
    error: Optional[str] = None
    tool_calls: list[dict] = field(default_factory=list)
    duration_ms: int = 0
    tokens_used: int = 0

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "output": self.output[:1000] if self.output else "",
            "error": self.error,
            "tool_calls": self.tool_calls,
            "duration_ms": self.duration_ms,
            "tokens_used": self.tokens_used,
        }


@dataclass
class Step:
    """执行步骤"""
    id: str
    description: str
    status: StepStatus = StepStatus.PENDING
    result: Optional[StepResult] = None
    retry_count: int = 0
    max_retries: int = 3
    dependencies: list[str] = field(default_factory=list)  # 依赖的步骤 ID
    metadata: dict = field(default_factory=dict)

    def mark_running(self):
        self.status = StepStatus.RUNNING

    def mark_success(self, result: StepResult):
        self.status = StepStatus.SUCCESS
        self.result = result

    def mark_failed(self, error: str, result: Optional[StepResult] = None):
        self.status = StepStatus.FAILED
        if result:
            self.result = result
        elif not self.result:
            self.result = StepResult(success=False, error=error)

    def can_retry(self) -> bool:
        return self.retry_count < self.max_retries

    def mark_retrying(self):
        self.status = StepStatus.RETRYING
        self.retry_count += 1

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
            "result": self.result.to_dict() if self.result else None,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "dependencies": self.dependencies,
            "metadata": self.metadata,
        }


@dataclass
class Reflection:
    """反思记录"""
    step_id: str
    observation: str
    issue: Optional[str] = None
    suggestion: Optional[str] = None
    should_retry: bool = False
    should_replan: bool = False
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "observation": self.observation,
            "issue": self.issue,
            "suggestion": self.suggestion,
            "should_retry": self.should_retry,
            "should_replan": self.should_replan,
            "metadata": self.metadata,
        }


@dataclass
class TaskState:
    """任务状态"""
    task_id: str
    goal: str
    phase: TaskPhase = TaskPhase.PLANNING
    plan: Optional[str] = None
    steps: list[Step] = field(default_factory=list)
    reflections: list[Reflection] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def add_step(self, step: Step):
        """添加执行步骤"""
        self.steps.append(step)
        self.updated_at = time.time()

    def get_step(self, step_id: str) -> Optional[Step]:
        """获取指定步骤"""
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def get_current_step(self) -> Optional[Step]:
        """获取当前正在执行的步骤"""
        for step in self.steps:
            if step.status in (StepStatus.RUNNING, StepStatus.RETRYING):
                return step
        return None

    def get_pending_steps(self) -> list[Step]:
        """获取所有待执行的步骤"""
        return [s for s in self.steps if s.status == StepStatus.PENDING]

    def get_failed_steps(self) -> list[Step]:
        """获取所有失败的步骤"""
        return [s for s in self.steps if s.status == StepStatus.FAILED]

    def get_completed_steps(self) -> list[Step]:
        """获取所有已完成的步骤"""
        return [s for s in self.steps if s.status == StepStatus.SUCCESS]

    def add_reflection(self, reflection: Reflection):
        """添加反思记录"""
        self.reflections.append(reflection)
        self.updated_at = time.time()

    def add_error(self, error: dict):
        """添加错误记录"""
        error["timestamp"] = time.time()
        self.errors.append(error)
        self.updated_at = time.time()

    def set_phase(self, phase: TaskPhase):
        """设置任务阶段"""
        self.phase = phase
        self.updated_at = time.time()

    def is_complete(self) -> bool:
        """检查任务是否完成"""
        return self.phase == TaskPhase.COMPLETED

    def is_failed(self) -> bool:
        """检查任务是否失败"""
        return self.phase == TaskPhase.FAILED

    def has_pending_steps(self) -> bool:
        """检查是否有待执行的步骤"""
        return any(s.status == StepStatus.PENDING for s in self.steps)

    def progress(self) -> dict:
        """获取任务进度"""
        total = len(self.steps)
        completed = len(self.get_completed_steps())
        failed = len(self.get_failed_steps())
        pending = len(self.get_pending_steps())
        running = len([s for s in self.steps if s.status in (StepStatus.RUNNING, StepStatus.RETRYING)])

        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "pending": pending,
            "running": running,
            "progress_percent": (completed / total * 100) if total > 0 else 0,
        }

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "phase": self.phase.value,
            "plan": self.plan,
            "steps": [s.to_dict() for s in self.steps],
            "reflections": [r.to_dict() for r in self.reflections],
            "errors": self.errors,
            "metadata": self.metadata,
            "progress": self.progress(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_json(self, indent: int = 2) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> TaskState:
        """从字典创建 TaskState"""
        state = cls(
            task_id=data["task_id"],
            goal=data["goal"],
            phase=TaskPhase(data.get("phase", "planning")),
            plan=data.get("plan"),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )

        for step_data in data.get("steps", []):
            step = Step(
                id=step_data["id"],
                description=step_data["description"],
                status=StepStatus(step_data.get("status", "pending")),
                retry_count=step_data.get("retry_count", 0),
                max_retries=step_data.get("max_retries", 3),
                dependencies=step_data.get("dependencies", []),
                metadata=step_data.get("metadata", {}),
            )
            if step_data.get("result"):
                result_data = step_data["result"]
                step.result = StepResult(
                    success=result_data["success"],
                    output=result_data.get("output", ""),
                    error=result_data.get("error"),
                    tool_calls=result_data.get("tool_calls", []),
                    duration_ms=result_data.get("duration_ms", 0),
                    tokens_used=result_data.get("tokens_used", 0),
                )
            state.steps.append(step)

        for ref_data in data.get("reflections", []):
            state.reflections.append(Reflection(
                step_id=ref_data["step_id"],
                observation=ref_data["observation"],
                issue=ref_data.get("issue"),
                suggestion=ref_data.get("suggestion"),
                should_retry=ref_data.get("should_retry", False),
                should_replan=ref_data.get("should_replan", False),
                metadata=ref_data.get("metadata", {}),
            ))

        state.errors = data.get("errors", [])
        return state


class TaskStateBuilder:
    """TaskState 构建器"""

    def __init__(self, task_id: str, goal: str):
        self._state = TaskState(task_id=task_id, goal=goal)

    def with_plan(self, plan: str) -> TaskStateBuilder:
        """设置任务计划"""
        self._state.plan = plan
        return self

    def with_step(self, step_id: str, description: str, dependencies: list[str] = None) -> TaskStateBuilder:
        """添加执行步骤"""
        step = Step(
            id=step_id,
            description=description,
            dependencies=dependencies or [],
        )
        self._state.add_step(step)
        return self

    def with_metadata(self, key: str, value: Any) -> TaskStateBuilder:
        """添加元数据"""
        self._state.metadata[key] = value
        return self

    def build(self) -> TaskState:
        """构建 TaskState"""
        return self._state
