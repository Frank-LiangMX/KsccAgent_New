"""
Task Logger - 任务运行日志

提供详细的步骤级日志记录，支持：
- 每步输入输出追踪
- 决策理由记录
- 性能指标收集
- 日志持久化
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from task_state import Step, StepResult, StepStatus, TaskPhase, TaskState


class TaskLogger:
    """任务日志记录器"""

    def __init__(self, log_dir: str = "logs/tasks"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._current_log_file: Optional[Path] = None
        self._log_entries: list[dict] = []
        self._start_time: float = 0

    def start_task(self, task_id: str, goal: str) -> Path:
        """开始记录任务日志"""
        self._start_time = time.time()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._current_log_file = self.log_dir / f"task_{task_id}_{timestamp}.json"

        # 初始化日志条目
        self._log_entries = []
        self._add_entry("task_start", {
            "task_id": task_id,
            "goal": goal,
            "timestamp": timestamp,
        })

        return self._current_log_file

    def log_step_start(self, step: Step):
        """记录步骤开始"""
        self._add_entry("step_start", {
            "step_id": step.id,
            "description": step.description,
            "dependencies": step.dependencies,
        })

    def log_step_execution(self, step: Step, input_data: dict, output_data: dict):
        """记录步骤执行详情"""
        self._add_entry("step_execution", {
            "step_id": step.id,
            "input": input_data,
            "output": output_data,
            "duration_ms": step.result.duration_ms if step.result else 0,
        })

    def log_step_complete(self, step: Step):
        """记录步骤完成"""
        entry_data = {
            "step_id": step.id,
            "success": step.status.value == "success",
            "output_preview": step.result.output[:500] if step.result and step.result.output else "",
            "error": step.result.error if step.result else None,
            "tool_calls_count": len(step.result.tool_calls) if step.result else 0,
            "duration_ms": step.result.duration_ms if step.result else 0,
            "tokens_used": step.result.tokens_used if step.result else 0,
        }
        self._add_entry("step_complete", entry_data)

    def log_step_failure(self, step: Step, error: str, recovery_action: Optional[str] = None):
        """记录步骤失败"""
        self._add_entry("step_failure", {
            "step_id": step.id,
            "error": error,
            "recovery_action": recovery_action,
            "retry_count": step.retry_count,
            "max_retries": step.max_retries,
        })

    def log_reflection(self, step_id: str, observation: str, suggestion: Optional[str] = None,
                      should_retry: bool = False, should_replan: bool = False):
        """记录反思结果"""
        self._add_entry("reflection", {
            "step_id": step_id,
            "observation": observation,
            "suggestion": suggestion,
            "should_retry": should_retry,
            "should_replan": should_replan,
        })

    def log_tool_call(self, step_id: str, tool_name: str, arguments: dict):
        """记录工具调用"""
        self._add_entry("tool_call", {
            "step_id": step_id,
            "tool_name": tool_name,
            "arguments": arguments,
        })

    def log_tool_result(self, step_id: str, tool_name: str, result: str, error: bool = False):
        """记录工具结果"""
        self._add_entry("tool_result", {
            "step_id": step_id,
            "tool_name": tool_name,
            "result": result[:1000] if result else "",
            "error": error,
        })

    def log_decision(self, decision_point: str, options: list[str], chosen: str, reason: str):
        """记录决策点"""
        self._add_entry("decision", {
            "decision_point": decision_point,
            "options": options,
            "chosen": chosen,
            "reason": reason,
        })

    def log_recovery_action(self, step_id: str, action_type: str, details: dict):
        """记录恢复动作"""
        self._add_entry("recovery_action", {
            "step_id": step_id,
            "action_type": action_type,
            "details": details,
        })

    def log_context_snapshot(self, context_summary: dict):
        """记录上下文快照"""
        self._add_entry("context_snapshot", context_summary)

    def log_performance_metrics(self, metrics: dict):
        """记录性能指标"""
        self._add_entry("performance_metrics", metrics)

    def log_task_complete(self, task_state: TaskState):
        """记录任务完成"""
        duration = time.time() - self._start_time
        self._add_entry("task_complete", {
            "task_id": task_state.task_id,
            "phase": task_state.phase.value,
            "progress": task_state.progress(),
            "total_duration_ms": int(duration * 1000),
            "total_steps": len(task_state.steps),
            "completed_steps": len(task_state.get_completed_steps()),
            "failed_steps": len(task_state.get_failed_steps()),
            "reflections_count": len(task_state.reflections),
            "errors_count": len(task_state.errors),
        })

        # 保存日志到文件
        self._save_log()

    def log_task_failure(self, task_state: TaskState, error: str):
        """记录任务失败"""
        duration = time.time() - self._start_time
        self._add_entry("task_failure", {
            "task_id": task_state.task_id,
            "error": error,
            "progress": task_state.progress(),
            "total_duration_ms": int(duration * 1000),
        })

        # 保存日志到文件
        self._save_log()

    def log_step_start_by_id(self, step_id: str):
        """按 ID 记录步骤开始"""
        self._add_entry("step_start", {"step_id": step_id})

    def log_step_execution_by_id(self, step_id: str, input_data: dict, output_data: dict, duration_ms: int = 0):
        """按 ID 记录步骤执行详情"""
        self._add_entry("step_execution", {
            "step_id": step_id,
            "input": input_data,
            "output": output_data,
            "duration_ms": duration_ms,
        })

    def log_step_complete_by_id(self, step_id: str, duration_ms: int = 0):
        """按 ID 记录步骤完成"""
        self._add_entry("step_complete", {
            "step_id": step_id,
            "success": True,
            "duration_ms": duration_ms,
        })

    def log_step_failure_by_id(self, step_id: str, error: str):
        """按 ID 记录步骤失败"""
        self._add_entry("step_failure", {
            "step_id": step_id,
            "error": error,
        })

    def _add_entry(self, event_type: str, data: dict):
        """添加日志条目"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "data": data,
        }
        self._log_entries.append(entry)

    def _save_log(self):
        """保存日志到文件"""
        if not self._current_log_file:
            return

        log_data = {
            "metadata": {
                "created_at": datetime.now().isoformat(),
                "total_entries": len(self._log_entries),
            },
            "entries": self._log_entries,
        }

        with open(self._current_log_file, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)

    def get_log_summary(self) -> dict:
        """获取日志摘要"""
        if not self._log_entries:
            return {}

        # 统计各类事件
        event_counts = {}
        for entry in self._log_entries:
            event_type = entry["event_type"]
            event_counts[event_type] = event_counts.get(event_type, 0) + 1

        # 计算总时长
        total_duration = 0
        if self._start_time:
            total_duration = int((time.time() - self._start_time) * 1000)

        return {
            "total_entries": len(self._log_entries),
            "event_counts": event_counts,
            "total_duration_ms": total_duration,
            "log_file": str(self._current_log_file) if self._current_log_file else None,
        }

    def get_step_timeline(self) -> list[dict]:
        """获取步骤时间线"""
        timeline = []
        current_step = None
        step_start_time = None

        for entry in self._log_entries:
            event_type = entry["event_type"]
            data = entry["data"]

            if event_type == "step_start":
                current_step = data["step_id"]
                step_start_time = datetime.fromisoformat(entry["timestamp"]).timestamp()
                timeline.append({
                    "step_id": current_step,
                    "description": data.get("description", ""),
                    "start_time": entry["timestamp"],
                    "events": [],
                })
            elif event_type in ("step_complete", "step_failure") and current_step:
                # 更新当前步骤的结束时间
                for step_info in timeline:
                    if step_info["step_id"] == current_step:
                        step_info["end_time"] = entry["timestamp"]
                        step_info["duration_ms"] = data.get("duration_ms", 0)
                        step_info["success"] = event_type == "step_complete"
                        break
                current_step = None

            # 添加事件到当前步骤
            if current_step and timeline:
                timeline[-1]["events"].append({
                    "type": event_type,
                    "timestamp": entry["timestamp"],
                    "data": data,
                })

        return timeline

    def export_markdown_report(self) -> str:
        """导出 Markdown 格式的报告"""
        if not self._log_entries:
            return "# Task Log\n\nNo entries recorded."

        lines = ["# Task Execution Log\n"]

        # 添加摘要
        summary = self.get_log_summary()
        lines.append("## Summary\n")
        lines.append(f"- **Total Entries**: {summary.get('total_entries', 0)}")
        lines.append(f"- **Total Duration**: {summary.get('total_duration_ms', 0)}ms")
        lines.append("")

        # 添加事件统计
        event_counts = summary.get('event_counts', {})
        if event_counts:
            lines.append("## Event Statistics\n")
            lines.append("| Event Type | Count |")
            lines.append("|------------|-------|")
            for event_type, count in sorted(event_counts.items()):
                lines.append(f"| {event_type} | {count} |")
            lines.append("")

        # 添加时间线
        timeline = self.get_step_timeline()
        if timeline:
            lines.append("## Step Timeline\n")
            for step_info in timeline:
                status = "✓" if step_info.get("success") else "✗"
                duration = step_info.get("duration_ms", 0)
                lines.append(f"### Step {step_info['step_id']} {status}\n")
                lines.append(f"- **Description**: {step_info.get('description', 'N/A')}")
                lines.append(f"- **Duration**: {duration}ms")
                lines.append(f"- **Start**: {step_info.get('start_time', 'N/A')}")
                if "end_time" in step_info:
                    lines.append(f"- **End**: {step_info['end_time']}")
                lines.append("")

                # 添加事件详情
                events = step_info.get("events", [])
                if events:
                    lines.append("**Events:**\n")
                    for event in events:
                        lines.append(f"- `{event['type']}` at {event['timestamp']}")
                    lines.append("")

        return "\n".join(lines)


class StepLogger:
    """步骤级详细日志记录器"""

    def __init__(self, step_id: str, task_logger: TaskLogger):
        self.step_id = step_id
        self.task_logger = task_logger
        self._start_time: float = 0
        self._input_data: dict = {}
        self._output_data: dict = {}
        self._tool_calls: list[dict] = []
        self._tool_results: list[dict] = []
        self._decisions: list[dict] = []

    def start(self):
        """开始记录步骤"""
        self._start_time = time.time()
        self.task_logger.log_step_start_by_id(self.step_id)

    def log_input(self, input_data: dict):
        """记录输入数据"""
        self._input_data = input_data

    def log_output(self, output_data: dict):
        """记录输出数据"""
        self._output_data = output_data

    def log_tool_call(self, tool_name: str, arguments: dict):
        """记录工具调用"""
        self._tool_calls.append({
            "tool": tool_name,
            "arguments": arguments,
            "timestamp": datetime.now().isoformat(),
        })

    def log_tool_result(self, tool_name: str, result: str, error: bool = False):
        """记录工具结果"""
        self._tool_results.append({
            "tool": tool_name,
            "result": result[:1000] if result else "",
            "error": error,
            "timestamp": datetime.now().isoformat(),
        })

    def log_decision(self, decision_point: str, options: list[str], chosen: str, reason: str):
        """记录决策"""
        self._decisions.append({
            "decision_point": decision_point,
            "options": options,
            "chosen": chosen,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
        })

    def complete(self, success: bool, output: str = "", error: str = ""):
        """完成步骤记录"""
        duration_ms = int((time.time() - self._start_time) * 1000)

        # 记录执行详情
        self.task_logger.log_step_execution_by_id(
            self.step_id,
            input_data=self._input_data,
            output_data={
                "output": output[:2000] if output else "",
                "error": error,
                "tool_calls": self._tool_calls,
                "tool_results": self._tool_results,
                "decisions": self._decisions,
            },
            duration_ms=duration_ms,
        )

        # 记录完成状态
        if success:
            self.task_logger.log_step_complete_by_id(self.step_id, duration_ms)
        else:
            self.task_logger.log_step_failure_by_id(self.step_id, error)


def load_task_state_from_log(log_path: str | Path) -> Optional[TaskState]:
    """从日志文件重建 TaskState"""
    log_path = Path(log_path)
    if not log_path.exists():
        return None

    with open(log_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    entries = data.get("entries", [])
    if not entries:
        return None

    # 提取 task_start
    task_id = ""
    goal = ""
    for e in entries:
        if e["event_type"] == "task_start":
            task_id = e["data"].get("task_id", "")
            goal = e["data"].get("goal", "")
            break

    if not task_id:
        return None

    state = TaskState(task_id=task_id, goal=goal)

    # 从事件流重建步骤
    step_map: dict[str, Step] = {}
    for e in entries:
        etype = e["event_type"]
        edata = e["data"]
        sid = edata.get("step_id", "")

        if etype == "step_start" and sid:
            step = Step(
                id=sid,
                description=edata.get("description", ""),
                dependencies=edata.get("dependencies", []),
            )
            step_map[sid] = step
            state.steps.append(step)

        elif etype == "step_complete" and sid and sid in step_map:
            step_map[sid].status = StepStatus.SUCCESS
            step_map[sid].result = StepResult(
                success=True,
                output=edata.get("output_preview", ""),
                duration_ms=edata.get("duration_ms", 0),
                tokens_used=edata.get("tokens_used", 0),
            )

        elif etype == "step_failure" and sid and sid in step_map:
            step_map[sid].status = StepStatus.FAILED
            step_map[sid].retry_count = edata.get("retry_count", 0)
            step_map[sid].result = StepResult(
                success=False,
                error=edata.get("error", ""),
            )

    # 确定最终阶段
    if any(s.status == StepStatus.FAILED for s in state.steps):
        state.phase = TaskPhase.FAILED
    elif all(s.status == StepStatus.SUCCESS for s in state.steps):
        state.phase = TaskPhase.COMPLETED
    else:
        # 有些步骤没完成（可能任务中途退出）
        state.phase = TaskPhase.FAILED

    return state


def get_latest_task_log(log_dir: str = "logs/tasks") -> Optional[Path]:
    """获取最新的任务日志文件路径"""
    log_path = Path(log_dir)
    if not log_path.exists():
        return None
    logs = sorted(log_path.glob("task_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def get_recent_task_logs(log_dir: str = "logs/tasks", limit: int = 10) -> list[Path]:
    """获取最近的任务日志文件列表"""
    log_path = Path(log_dir)
    if not log_path.exists():
        return []
    return sorted(log_path.glob("task_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def create_task_logger(task_id: str, goal: str, log_dir: str = "logs/tasks") -> TaskLogger:
    """创建任务日志记录器的工厂函数"""
    logger = TaskLogger(log_dir)
    logger.start_task(task_id, goal)
    return logger
